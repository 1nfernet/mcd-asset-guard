# MCP_INTEGRATION.md

本文说明「麦麦资产管家」实际使用的麦当劳 MCP Server、Tool、调用链路与业务价值。

---

## 1. 接入的 MCP Server

| 项 | 值 |
|---|---|
| Server | 麦当劳 MCP Server（M-China） |
| 端点 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP（JSON-RPC 2.0） |
| 认证 | `Authorization: Bearer ${MCD_MCP_TOKEN}`，**Token 只从环境变量读取** |
| 协议版本 | `2025-06-18`（`MCP-Protocol-Version` 请求头） |
| 会话 | `Mcp-Session-Id` 持久化到 `state/mcp-session.json` 跨进程复用，404 自动重新握手 |
| 限速 | 官方 600 req/min；本地令牌桶自限 480 req/min（留 20% 余量）+ 429 退避重试 |
| 响应格式 | 同时支持 `application/json` 与 `text/event-stream`（SSE） |
| 写操作 | 默认锁定，需 `ENABLE_WRITE_ACTIONS=true` 显式放行 |

实现代码全部集中在 `src/mcp_client.py`（唯一触网模块）。

## 2. 使用的 Tool 清单

### v1.0 已接入（个人资产守护）

| Tool | 服务 | 用途 | 类型 |
|---|---|---|---|
| `now-time-info` | `points` / `mall` | 取服务端时间做基准，避免本地时钟偏差 | 读 |
| `query-my-account` | `points` | 取可用积分与月度临期积分桶 | 读 |
| `mall-points-products` | `mall` / `party` | 取可兑换商品（支持 `catRuleIds` 类目筛选），同时作为派对链路的 spuId 锚点 | 读 |
| `query-party-city` | `party` | 该 spu 可举办派对的城市列表（code / 经纬度） | 读 |
| `query-party-store` | `party` | 城市下支持该派对的门店列表（storeCode） | 读 |
| `query-party-store-date` | `party` | 门店可预约日期列表 | 读 |
| `query-party-store-session` | `party` | 指定日期场次：时段 / 成团人数 / **剩余名额 leftNum** / 单价 | 读 |

### v1.0 已做闸门保护、v1.1 启用（写操作）

| Tool | 用途 |
|---|---|
| `mall-create-order` | 积分商城兑换下单（如兑换推荐组合） |
| `party-order-create` | 派对场次预约 |
| `auto-bind-coupons` | 自动领券 |
| `create-order` | 点餐下单 |

### v1.1 计划接入（团队拼单）

| Tool | 用途 |
|---|---|
| `available-coupons` / `query-my-coupons` | 券池取数（响应为预渲染 Markdown，需确定性解析） |
| `query-store-coupons` | 门店券 |
| `query-promotions` | 满减/满折规则（仅 `beType=6` 企业团餐） |
| `calculate-price` | 对 Top-N 候选方案做**权威定价复核**（券按 item 传 `couponId`/`couponCode`） |
| `query-meals` / `query-meal-detail` | 菜单与商品详情（拼单需求收集） |
| `delivery-query-stores` | 团餐场景取 storeCode + beCode |

### 未接入及原因

| Tool | 未接入原因 |
|---|---|
| `list-nutrition-foods` | 与资产守护主线无关（可作拼单营养汇总的加分项，v1.1 考虑） |
| `query-lottery-info` / `draw-lottery` / `query-my-prizes` | 抽奖类属于另一条资产线，v1.0 聚焦积分与名额；`draw-lottery` 已列入写操作白名单保护 |
| `create-order` / `cancel-order` / `query-order` 等点餐链路 | 与"资产不蒸发"主线不同，v1.1 拼单会用到 `create-order` |
| `campaign-calendar` / `query-survey-coupon` / `query-meal-assistance` | 暂未发现能直接提升核心场景价值的用法 |
| `auto-bind-coupons` | 写操作，需显式开关；v1.0 只读不写 |

## 3. 调用链路

### 链路 A：积分看护（`personal points`）

```
now-time-info ─────────────► 服务端当日日期（基准，非本地时钟）
query-my-account ──────────► { availablePoint, currentMouthExpirePoint,
                               nextMouthExpirePoint, expiredPoint, ... }
        │
        ▼  本地纯计算（无网络调用）
月末推算到期日  → days_left = 月末 - 今天
7/3/1 天分级    → critical(≤1) / high(≤3) / warn(≤7)
跨运行去重      → state/notify-state.json 记录「桶 + 档位」
        │
        ▼
终端分级提醒（含建议命令）
```

### 链路 B：商城监控与兑换规划（`personal mall --plan`）

```
mall-points-products(catRuleIds) ──► 商品列表 {spuName, point, price,
                                               upTime, downTime, status}
query-my-account ──────────────────► 可用积分 / 本月临期积分
        │
        ▼  本地纯计算
状态筛选（上架 2 / 预热 5）        → 差多少（所需积分 - 可用积分）
窗口判定（upTime/downTime）        → 即将下架 / 即将开抢
有界背包 DP（0.1 积分为整数单位）  → 兑换组合
        │
        ▼
兑换规划：组合 / 合计消耗 / 救回临期 / 兑换后余额
```

### 链路 C：派对空位（`personal party` / `--watch`）

```
mall-points-products(catRuleIds=1>6>21) ──► spuId 锚点
        │
query-party-city(spuId) ──────────────► 城市 {code, latitude, longitude}   [缓存 12h]
        │
query-party-store(code, lat, lng, spuId) ► 门店 {code=storeCode, ...}      [缓存 12h]
        │
query-party-store-date(spuId, storeCode) ► 可预约日期 [{date}]              [缓存 1h]
        │
query-party-store-session(spuId, storeCode, dateStr) ──► 场次列表          [高频轮询]
        { id, timeStart, timeEnd, partyMin, partyMax, price(分), leftNum }
        │
        ▼  本地纯计算
快照 diff（state/party-snapshot.json）
  · 首次出现且 leftNum > 0        → 新增可预约场次
  · leftNum 由 0 变为 > 0         → 空位重新开放
  · leftNum 增大                  → 空位增加
        │
        ▼
只对新变化告警（避免同一场次反复刷屏）
```

> 五级链路全部以 `spuId` 为锚点，逐级下钻；上三层为慢变数据做 TTL 缓存，只有最底层场次做高频轮询，从而在 600 req/min 的配额内安全运行。

## 4. 业务价值

| 维度 | 价值 |
|---|---|
| **对用户** | 把"看不见的资产流失"变成"看得见、可行动的建议"：还剩几天过期、能换成什么、还差多少分、哪个场次有空位 |
| **对平台** | 减少因过期/忘记使用而流失的会员权益，提升积分与券的实际核销率，把临期流量引导到商城兑换与派对预约 |
| **对 MCP 生态** | 补齐官方工具**明确不做**的那一环——`query-promotions` 的说明写着"由大模型自行根据规则解读优惠并计算搭配方案，工具不做最优推荐"，本项目用确定性代码把这段"口算"变成可复现、可测试的算法 |
| **工程示范** | 完整演示了 Streamable HTTP 的生产级接入：会话复用、SSE/JSON 双解析、429 退避、写操作闸门与凭据零落盘 |

## 5. 关键工程实现要点

| 问题 | 处理方式 |
|---|---|
| 业务信封判定 | 以 `code == 200` 为准（实测 `success` 字段可能缺失），失败抛 `McpBusinessError` 并带 `traceId` |
| 响应双格式 | 先看 `Content-Type`：`text/event-stream` 走 SSE 行扫描（忽略 `: ping` 注释，多行 `data:` 合并），否则按 JSON 解析 |
| 结果解包 | `structuredContent` 优先；否则拼 `content[].text` 再尝试 JSON 解析；解析失败返回 `RawText`（Markdown 场景） |
| 会话失效 | 收到 HTTP 404 判定会话过期 → 清本地 session → 单飞锁内重新 `initialize` → 重发原请求（最多一次） |
| 限速与退避 | 本地滑动窗口令牌桶；429 读取 `Retry-After`，与指数退避取较大值 + 抖动，最多重试 3 次 |
| 单位归一 | 积分 `Decimal`（0.1 粒度）；金额统一整数"分"（商城元字符串 ×100、场次分整数原样、`calculate-price` 返回分） |
| 字段名防腐 | 照抄真实字段名（含 `currentMouthExpirePoint` 的官方拼写 `Mouth`），解析失败不静默吞异常 |
| 离线可演示 | `FixtureTransport` 与 `HttpTransport` 同构，`--offline` 时零改动切换；fixtures 为真实响应，可复现截图 |

## 6. 遗留未知数（已在代码中标注）

1. **券的 `couponId` / `couponCode`**：`available-coupons` 与 `query-my-coupons` 返回的是预渲染 Markdown，看不到券 ID；而 `calculate-price` 需要按 item 传券 ID。需通过真实 MCP 的原始 JSON-RPC 响应进一步确认字段位置（v1.1 待办）。
2. **写操作入参细节**：`mall-create-order` / `party-order-create` 的实际入参尚未实测（v1.0 主动锁定，不作验证）。
3. **商品特制项**：`calculate-price` 的 `modification.selectedKey/unselectedKey` 规则较复杂，v1.0/v1.1 均先不支持特制商品。
