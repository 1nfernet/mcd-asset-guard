# workbuddy.md — WorkBuddy 开发上下文

本文件记录「麦麦资产管家」使用 WorkBuddy 开发的过程上下文，用于核验 WorkBuddy 联动活动奖励条件。

---

## 1. 项目基本信息

- **项目名称**：mcd-asset-guard（麦麦资产管家）
- **一句话定位**：个人资产不蒸发 + 团队消费不虚花——积分过期前救回来，周边开抢时提醒你，多人拼单时算到最省、分账分到分
- **技术栈**：Python 3.10+（CLI），运行时依赖仅 `httpx`
- **接入的 MCP**：麦当劳 MCP Server（`https://mcp.mcd.cn`，Streamable HTTP）
- **开发日期**：2026-10-14

## 2. WorkBuddy 在本项目中的实际作用

### 2.1 通过 WorkBuddy 连接器实测真实 MCP 接口（关键环节）

项目开发中，WorkBuddy 内置的**麦当劳 MCP 连接器**被用于直接调用真实工具、观察真实响应结构，而不是靠猜测字段：

| 实测工具 | 得到的结论 |
|---|---|
| `now-time-info` | 所有工具统一业务信封 `{success, code, message, datetime, traceId, data}`；`Content-Type: application/json` |
| `query-my-account` | 积分是**字符串小数**（`availablePoint="842.9"`）；临期只有月度粒度（`currentMouthExpirePoint`，官方拼写为 `Mouth`）；无具体到期日 |
| `mall-points-products` | 商品字段 `spuId/point/price/upTime/downTime/status(1-5)`，`catRuleIds` 支持类目筛选 |
| `available-coupons` / `query-my-coupons` | 返回**预渲染 Markdown**（非 JSON），含单日时间窗与"到店专用"标签 |
| `query-promotions` | 满减规则**仅企业团餐（beType=6）**可用；官方说明"由大模型自行计算搭配方案，工具不做最优推荐" |
| `query-party-city/store/store-date/store-session` | 五级链路全程走通；场次含 `leftNum`（剩余名额）、`partyMin/partyMax`（成团人数）、`price`（单位：分） |

这批实测结论直接固化为 `tests/fixtures/` 中的离线演示数据与 37 个单元测试的断言基础。

### 2.2 架构设计

WorkBuddy 完成了整体架构设计与数据流梳理，并输出三张数据流图（分层架构 / 模块 A 积分与派对链路 / 模块 B 拼单优化链路），确定核心架构铁律：

> **取数交给 MCP、算术交给代码、表达交给 Agent**——主流程零大模型调用，排序、组合优化、到期判断、分账全部本地确定性代码实现。

### 2.3 代码生成与验证

WorkBuddy 生成了完整可运行代码并在本机完成验证：

- `src/mcp_client.py`：Streamable HTTP 握手、`Mcp-Session-Id` 持久化复用、SSE/JSON 双解析、令牌桶限速 + 429 退避、写操作白名单闸门
- `src/services/personal/{points,mall,party}.py`：积分月末推算与 7/3/1 分级、有界背包 DP 兑换规划、派对五级链路与 watch 快照差异告警
- `src/main.py`：统一 CLI 入口；`src/models.py`、`src/config.py`、`src/state.py`、`src/notify.py`、`src/report/render.py` 等共享层
- `tests/`：37 个单元测试（含单位换算、SSE 解析、信封判定、DP 策略、链路与快照 diff），本机全部通过
- `scripts/check_secrets.py`：提交前凭据自检

## 3. 开发过程中的关键决策

| 决策 | 结论 |
|---|---|
| 定价归属 | 本地定价引擎负责高速枚举，仅 Top-N 候选回传 `calculate-price` 做权威复核（既守"算术交给代码"，又保最终数字与服务端一致） |
| 兑换规划目标 | 最大化临期积分消耗：最大化 `min(消耗, 临期桶)`，再最小化对非临期积分的透支 |
| 到期分级 | 因接口只有月度粒度，改用**月末推算法**计算 `days_left`，并提供 `--as-of` 模拟日期以便复现演示 |
| 离线演示 | 增加 `--offline` + fixtures，未检测到 Token 时自动降级，保证评审零配置可复现 |
| 派对监控 | 采用内置 `--watch` 常驻轮询 + 快照 diff，只对新增/重新开放的空位告警 |
| 隐私合规 | fixture 中的真实 `accountId` 末段置零脱敏，符合参赛声明的信息安全要求 |

## 4. 交付物

- 可运行源代码（v1.0：个人资产守护三件套 + 共享 MCP 连接层）
- 37 个单元测试（全部通过）
- `README.md` / `MCP_INTEGRATION.md` / `CONTEST_DECLARATION.md`
- 离线演示 fixtures（真实接口响应）与 README 对比图
