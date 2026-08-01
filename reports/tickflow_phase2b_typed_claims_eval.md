# TickFlow Phase 2B-1 类型化 Claims 输出合同验收报告

## 1. 验收结论

```text
PHASE2B_TYPED_CLAIMS_CONTRACT_READY
ISOLATION_DESIGN_READY
ISOLATION_RUNTIME_NOT_YET_VERIFIED
```

本轮已建立 `Facts JSON -> Claims JSON -> Claims Validator ->
Deterministic Renderer -> Markdown` 闭环。三只固定标的的 Claims、
Markdown 和仓库内 Obsidian 旁路预览均通过离线验证。当前
`can_publish=false`，不得自动进入正式 Obsidian 库或金融日报。

## 2. 合同概览

| 项目 | 结果 |
|---|---|
| Claims Schema | READY，Pydantic strict/extra-forbid |
| 允许 Claim Types | 8 |
| 禁止 Claim Types | 14 |
| Calculation Registry | 6 个版本化确定性函数 |
| 三票 Claims | VALID，每票 42，合计 126 |
| Facts Pointer 绑定 | PASSED，合计 141 |
| 无来源 Claim | 0 |
| raw/qfq 混用 | 0 |
| 自由文本字段 | 0 |
| 交易 Claim | 0 |
| 敏感形态命中 | 0 |
| Renderer | DETERMINISTIC |
| 重复渲染 | IDEMPOTENT |
| typed inbox | 3 |
| typed rejected | 0 |
| 历史 rejected | 3，PRESERVED |
| reviewed | 0 |

允许类型：

```text
NUMERIC_OBSERVATION
BOOLEAN_STATE
ENUM_STATE
SAME_BASIS_COMPARISON
ORDERED_RELATION
SCOPE_NOTICE
VENDOR_PENDING_NOTICE
DATA_QUALITY_STATE
```

禁止类型：

```text
RECOMMENDATION
TRADE_ACTION
POSITION_SIZING
BUY_SELL_SIGNAL
TARGET_PRICE
STOP_LOSS
FORECAST
PREDICTION
CATALYST
NEWS_INTERPRETATION
FINANCIAL_INTERPRETATION
INDUSTRY_RANKING
SUPPORT_LEVEL
RESISTANCE_LEVEL
```

## 3. 三票产物

| 标的 | Claims | Claims SHA-256 | Markdown SHA-256 | 路由 |
|---|---|---|---|---|
| 派林生物 `000403.SZ` | VALID | `66f7ef471b293a4d5453ca53fd91668f217176915e6e88f2ce88f4f037559183` | `63482087ca33cb7a0ea4767c9b4f41e9176b7abd0f5ac5516bb14b11fc9514e8` | inbox |
| 中金黄金 `600489.SH` | VALID | `fb09664d48733f188ff2cc7d0670e235d89de7ce717c3c21cfb233d726753bee` | `b62b729897a607c5eb51156e7f21310c1ad74444f6b97cdd0e1f6412c6af937f` | inbox |
| 东方财富 `300059.SZ` | VALID | `a0a993a42fe81a2122920d46738fc3d019be2a0ec8066852cc85b9ce7829f3c3` | `cc625649c54fc2cb8ffd00fba00926ee9bd8756c0b8589c2e14d5d3811dd7951` | inbox |

日线变化数字明确命名为“开盘至收盘变化率”，由同一 raw 口径
`/daily/open` 和 `/daily/close` 计算。Facts 中没有经合同验证的
前收盘指针，因此未伪造“日涨跌幅”。关键价位继续保持
`NOT_IMPLEMENTED`，未生成支撑位或阻力位 Claim。

## 4. Validator 与 Renderer

Claims Validator 已覆盖 Schema、Facts 文件/SHA、symbol/name/date、
42 项完整 predicate 集合、确定性 claim_id、类型/predicate、
JSON Pointer、operand label 与 pointer 绑定、值回放、计算注册表及输入类型、
raw/qfq、unit、NaN/Infinity、自由文本、禁止类型、scope、
vendor pending、模板、顺序、normalized hash 和敏感形态。任一
Claim 失败即整份 `CLAIMS_INVALID`，不自动修改值或口径。

Calculation Registry 只允许：

```text
compare_numbers_v1
ordered_relation_v1
difference_v1
percentage_difference_v1
threshold_compare_v1
normalize_scope_v1
```

Renderer 是无文件、无网络、无子进程的纯函数模块。它只接收
`CLAIMS_VALID` 对象，使用固定模板生成七个章节和固定
frontmatter，拒绝链接、HTML/注释、额外标题和零宽字符。两次
连续渲染后全部 fixture、Markdown、index、schema 和 typed preview 字节
不变，聚合 SHA-256 均为
`6aae616c7e0ff540dc544374b80813ab1ee2811ea90b38935fa76c1eb8820f43`。

## 5. Obsidian 旁路

确定性文件只写入仓库内：

```text
reports/phase2_obsidian_preview/inbox/typed_*.md
```

`typed_` 前缀避免与三份历史自由文本样本冲突。无效 Claims 路由
为 `rejected`，仅允许落盘候选内容 SHA、错误数量与错误摘要 SHA，
不保存无效候选正文，且永不进入 `reviewed`。旧版自由文本交付验证仍为
`PHASE2_AI_OUTPUT_BLOCKED`、`errors=[]`，说明 typed inbox 没有绕过或
改写旧路由。本轮未接受任何真实 Vault 路径。

`phase2_claims` 与 `phase2_obsidian_preview/inbox` 采用同一发布事务：
先生成完整 staging 与两份旧目录快照，再通过事务标记串行交换；
第二个目录发布失败时两个目录一并回滚。旧 `typed_*` 被精确替换，
inbox 内非 typed 人工文件保留。验证器在事务标记存在或
Claims/preview 任一父目录为 symlink 时会在读取前失败关闭。

## 6. AI Worker 隔离设计

最小投影仅包含三票中已批准的结构化值、Facts SHA、投影 SHA
和 predicate 白名单，不含 Home/仓库/Vault 路径、`source_evidence`、
`numeric_provenance`、密钥或无关 Facts。读取器只允许一个精确路径，
在 open 前拒绝其他路径和任何 symlink 组件，并使用 `O_NOFOLLOW`、
`fstat`、大小限制和 inode 复核。假 Worker 仅输出 JSON，没有模型、
Provider 或网络依赖。

当前只验证了应用协议，并未在外部容器或 OS sandbox 中运行
真实模型。Python 路径守卫不能证明 macOS 上的敌对进程无法读取
其他文件或环境，因此必须保持
`ISOLATION_RUNTIME_NOT_YET_VERIFIED`。

## 7. 不变性与安全证据

| 受保护集合 | 文件数 | 聚合 SHA-256 / SHA-256 | 结果 |
|---|---:|---|---|
| Phase 1 观察证据 | 36 | `8660ecc155caacbd1dc291e88f0104d3cb970a748eb82a81746eac0e365daaa5` | UNCHANGED |
| Phase 1 请求审计 | 7 | `dd60675f63dd5354bc5e2a255b57a36a6aedc0b8ff889dd1d1c42741ff4f4e4d` | UNCHANGED |
| Phase 2 Facts | 4 | `10c6664754b8e1391f55d9b1b8b98f1577b749f0dceafbb77288749ae5376ef7` | UNCHANGED |
| Provider audit | 1 | `7996c13681e248b2a9932cdd7428949f1317719589234a7087b8ab019e4274f9` | UNCHANGED |

三份历史 AI 正文与三份 rejected 副本的对应 SHA-256 仍为：

```text
000403: b3307f02406c0314186e41da69ff16f68f86aba720207a4736d53e791f3ca2be
300059: 8211f480f1c20cd0201a23a4c1bb2e79461ba98f85708abb47ef611726c77717
600489: 8de882f2a32ba48b8c049759bf5e7e9836e44c8b05b62d697b6fe378b6a88baa
```

历史 `ai_body_sha256` 三项也保持：

```text
000403: 028d7ab8d409dc2c9b58d993d63b3e62c5a82c47a1a80941f10075abe22f4675
600489: 5f2901b36c721dc99db06da28d26c2efe3ff14e2b11068eab375860b3da97890
300059: acd1c28a0774cfadd42fcd3196eacc5385c9f19ee43618193c95d21050abd98b
```

生产产物扫描结果：无密钥/Cookie/Session/Authorization/私钥形态，
无真实 Home/Vault/Docker socket 路径，无禁止 Claim 或中文交易指令，
无 staging/tmp/partial/bak/transaction 遗留。

## 8. 验证结果

| 验证 | 结果 |
|---|---|
| Claims/Renderer/Worker 专项 | `99 passed` |
| 后端全量 | `1605 passed, 13 warnings` |
| `compileall app scripts` | PASSED |
| Ruff F821 | PASSED |
| Claims 离线验证 | `CLAIMS_VALID`, `errors=[]` |
| 假 Worker harness | `22 passed` |
| 旧版 freeform 验证 | `PHASE2_AI_OUTPUT_BLOCKED`, `errors=[]` |

13 条 warning 均为已有 Polars、websockets、`datetime.utcnow()` 弃用提示和
Polars sortedness 警告，本轮未扩大范围处理。

## 9. 十七项回答

1. Claims Schema 已完成，并已输出确定性 JSON Schema。
2. 首批允许 8 种 Claim Types，如第 2 节所列。
3. 明确禁止 14 种 Claim Types，任一种均无法通过 Schema。
4. 每条 Claim 均绑定白名单 Facts Pointer，141 次绑定全部通过。
5. 每条 Claim 均绑定对应 Facts SHA-256。
6. Calculation Registry 是关闭、版本化且可回放的确定性注册表。
7. raw/qfq 严格隔离，跨口径比较以 `CLAIM_REJECTED_RAW_QFQ_MISMATCH` 拒绝。
8. 可发布 Claims Schema 禁止自由文本字段；调试或人工备注也不会进入 Renderer。
9. Renderer 完全确定，同一 Claims 字节级输出一致。
10. 三票 fixtures 全部 `CLAIMS_VALID`。
11. 三票 Markdown 可重复生成，连续两次渲染哈希一致。
12. 无来源 Claim 为 0。
13. 交易 Claim 为 0，`trading_advice=false`。
14. Obsidian 路由失败关闭：仅 VALID typed 产物进 inbox，无效进 rejected，不写 reviewed。
15. 宿主读取隔离设计已完成，假 Worker 和路径拒绝 harness 已验证。
16. OS 级隔离未真实验证，仍是 `ISOLATION_RUNTIME_NOT_YET_VERIFIED`。
17. 建议下一步只设计一次外部 sandbox 的 AI Claims 批次；在运行时隔离验收前，不恢复真实 AI 调用。

## 10. 冻结边界与下一步

```text
新增 TickFlow API 请求: 0
新增 AI 调用: 0
新增 Provider 尝试: 0
AI Key: NOT_CONFIGURED
Paper Trading: NOT_STARTED
Obsidian 正式写入: NO
云端重新部署: NO
Integrated Gold: DISABLED
external_send_count: 0
Telegram / OpenClaw: NOT_CONNECTED
真实 Key: NOT_EXPOSED
```

下一动作：

```text
DESIGN_ISOLATED_AI_CLAIMS_BATCH
```

该动作只是隔离运行设计与验收，不是批准真实 AI 批次。
