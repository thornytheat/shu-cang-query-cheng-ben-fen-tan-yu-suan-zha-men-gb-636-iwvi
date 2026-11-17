# 接口 costsplit

| 方法 | 路径 | 用途 | 失败码 |
|---|---|---|---|
| GET | `/api/contract` | 列契约、单价表、规则表（按 priority 排序） | — |
| POST | `/api/billing/preview` | 单条字段试算，不落库 | 400 `E_BAD_REQUEST`、`Q_BAD_UNIT`、`Q_SENTINEL` |
| POST | `/api/import` | 导入样例并跑完整条流水线 | 400 路径穿越/非法后缀、404 样例缺失、422 空态/缺表头 |
| GET | `/api/quarantine` | 原因码分布 + 隔离证据行（可 `?code=` 过滤） | 404 `Q_NO_BATCH` |
| GET | `/api/allocations` | 部门份额、共享份额、未分摊、金额账 | 404 `Q_NO_BATCH` |
| GET | `/api/gates` | 部门预算结论 + 排序决策 | 404 `Q_NO_BATCH` |
| POST | `/api/gates` | 带预算重算闸门，升版本 | 404、409 `Q_STALE_VERSION`、400 预算格式 |
| GET | `/api/batches` | 批次清单（含校验位校验结果） | — |
| GET | `/api/export?kind=` | `bill` / `quarantine` / `shares` / `gates` / `manifest` / `receipt` | 404、400 `E_BAD_REQUEST` |
| GET | `/api/export/index` | 导出项清单与摘要 | 404 |
| POST | `/api/appeals` | 提交申诉（一条或多条），改判后重算并升版本 | 400 请求形状、404 `Q_NO_BATCH`、409 `Q_STALE_VERSION`、422 `Q_APPEAL_NONE` `Q_APPEAL_BUCKET` `Q_APPEAL_IMBALANCE` |
| POST | `/api/appeals/rollback` | 按版本快照回滚，写成一个新版本 | 400 缺 `to_version`、404 `Q_ROLLBACK_TARGET`、409 `Q_ROLLBACK_SAME` `Q_STALE_VERSION` |
| GET | `/api/appeals` | 申诉记录、版本时间线、版本差异与四账（可带 `left` / `right`） | 404 `Q_NO_BATCH` |
| PUT | `/api/ledger/budget` | 设定部门月度预算（`budget_cents` 或 `budget_yuan`，`month` 缺省本月） | 400 `E_BAD_REQUEST`、`Q_LEDGER_BAD_MONTH` |
| POST | `/api/ledger/reserve` | 按预估费用预占额度（可带 `reservation_id` 作幂等键） | 400、404 `Q_LEDGER_NO_BUDGET`、409 `Q_LEDGER_OVER_BUDGET` `Q_LEDGER_RESERVE_CONFLICT` |
| POST | `/api/ledger/settle` | 执行回执结算，全额记账（`receipt_id` 幂等，超额记欠额） | 400、404 `Q_LEDGER_NO_RESERVATION`、409 `Q_LEDGER_SETTLED` `Q_LEDGER_RECEIPT_CONFLICT` |
| POST | `/api/ledger/cancel` | 撤销未执行的预占（重复撤销幂等） | 404 `Q_LEDGER_NO_RESERVATION`、409 `Q_LEDGER_SETTLED` |
| GET | `/api/ledger/status?dept=&month=` | 本月已用 / 预占 / 可用 / 欠额（整数分） | 400 `E_BAD_REQUEST` |
| GET | `/api/ledger/reservations?dept=&month=&status=` | 预占清单（对账与中断恢复入口） | 400 `E_BAD_REQUEST` |
| GET | `/api/ledger/settlements?dept=&month=` | 实账清单（含 `late` 晚到标记） | 400 `Q_LEDGER_BAD_MONTH` |
| GET | `/healthz` | 存活探测 | — |

页面：`/` 账单接入、`/allocations` 分摊结果、`/gates` 闸门决策、`/appeals` 申诉回滚、`/quarantine` 异常查询、`/batches` 批次清单（可打印）、`/export` 导出。

失败响应统一形如：

```json
{"error": "E_BAD_REQUEST", "message": "来源只能是 sample_data 下的文件名，不能带路径", "detail": {}}
```
