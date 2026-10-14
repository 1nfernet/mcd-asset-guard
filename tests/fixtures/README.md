# tests/fixtures — 真实接口响应夹具

全部来自 **2026-10-14 经麦当劳 MCP 连接器的实测响应**（只读工具），
用于 `--offline` 离线演示与单元测试。文件名规则：`工具名__参数slug.json`。

- 含真实门店编码与公开商品数据，**不含任何 Token、密钥或账号凭证**。
- 隐私处理：query-my-account 的 accountId 末段已置零脱敏（符合参赛声明的信息安全要求）。
- 券两个工具的响应是预渲染 Markdown（非 JSON），此处保存了截断图片 URL 的版本。
- 在线跑通新工具后，把响应信封按同样格式存进来即可扩充离线演示。

| 文件 | 来源工具 |
|---|---|
| now-time-info.json | now-time-info |
| query-my-account.json | query-my-account |
| mall-points-products__catRuleIds_2_8.json | mall-points-products（周边） |
| mall-points-products__catRuleIds_1_6_21.json | mall-points-products（主题派对） |
| query-party-city__spuId_6971.json | query-party-city |
| query-party-store__code_350200__*.json | query-party-store（厦门） |
| query-party-store-date__*.json | query-party-store-date |
| query-party-store-session__*.json | query-party-store-session（2026-10-17） |
| available-coupons.json / query-my-coupons.json | 预渲染 Markdown（Phase 2 素材） |
