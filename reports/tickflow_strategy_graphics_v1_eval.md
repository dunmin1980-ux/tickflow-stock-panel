# TickFlow Strategy Graphics V1 评测报告

日期：2026-09-10，Asia/Shanghai。

状态：`TICKFLOW_STRATEGY_GRAPHICS_V1_RELEASED`。

基线：`59ea865b6fb6b651989eb3521b9c72e266d03aa5`。
分支：`codex/tickflow-visual-workbench-v1`。发布身份以包含本报告及发布标记的 Git 提交为准。

## 交付结果

| 能力 | 结果 |
|---|---|
| 今日工作台 `/paper-trading` | 研究总览、六策略卡、条件进度、三张图；保留原日用按钮 |
| 个股研究 `/stock-research` | 同一研究数据的完整只读页，无模拟盘执行按钮 |
| 六策略 | 名称、原状态、满足项、支持/限制数量、较前日变化、最近触发标识 |
| 条件展开 | 原始观测、原阈值、满足/未满足/缺失、差值、前日条件与当日条件区分 |
| 价格主图 | 最近 30 个交易日 QFQ 收盘、MA5/10/20/60、BOLL 三轨、观察日、策略标记 |
| MACD | 原计算的 DIF、DEA、柱值 |
| RSI6 | 原计算的 RSI6、50 中性线 |
| HOLD 含义 | 空仓观望与持仓不变分开；无已发布记录仍为 NOT_RUN，不捏造 HOLD |
| 缠论 | 复用原日线分型、候选笔、确认结构；中枢仍 DEFERRED |

两个页面共用组件和 Dashboard API。侧栏及手机导航进入新 `/stock-research`；旧
`/stock-analysis` 兼容路由保留，没有重接历史 Provider。

## 当前真实快照

读取的是本轮开工前已存在的 `2026-09-10` 输入，未重新运行 Daily、未拉取行情。

- Research Signal：`RISK_OBSERVATION`。
- 多策略 Research Consensus：`MIXED`。
- 已有 Paper Action：`HOLD`；含义：`FLAT_WAIT / 空仓观望`。
- 最近触发规则：`均线多头`，`2/4`。未满足的是中期均线关系及 20 日动量。
- QFQ 收盘：10.33；MACD DIF 约 -0.00487119，DEA 约 0.03663709，柱值约 -0.08301657；RSI6 约 40.48173733。
- 图形窗口：2026-07-31 至 2026-09-10，共 30 点；29 条回溯策略观察标记。

“最近触发”是现有排序给出的相对最近规则，不等于状态已达到 NEAR_TRIGGER。
Signal、多策略 Consensus 和 Paper Action 是不同层次，没有互相替换。
本轮没有把示例“缩量回踩 3/4”硬编码为今天的结果。

另用本地 `2026-09-09` 的真实历史 Dashboard 响应完成浏览器案例：缩量回踩 3/4，
未满足 20 日动量。该案例只在验收浏览器中提供已读取的历史响应，不改今日页面或任何输入。

## 数值与边界

新增服务只在已验证研究面板旁追加 `research.graphics`，不改 `engine` 输出：

1. 重新检查原始/QFQ 文件 SHA-256、日期顺序、唯一性、目标日期及有限数值。
2. 使用已有 `compute_indicators`，先对完整历史预热，再截取最后 30 点；缺失预热值保持 null。
3. 图表只用 QFQ 价格；原始成交量只提供给已有相对量能策略，不引入 raw 价格。
4. 历史标记对每个日期的快照前缀调用原 `evaluate_strategies` 和 `normalize_strategy`；不放宽阈值。
5. 标记是**同一当前 QFQ 快照的回溯观察**，不是当时已经发布的信号、成交记录或回测。
6. 原矩阵策略保留原精度，图表指标沿用既有 Polars 计算；不为显示一致而改写策略值。
7. 图表源校验失败仅阻断图表；已验证研究和账户仍可读。过期/缺失/日期不一致有明确状态。

Canonical 状态继续使用 `TRIGGERED / NEAR_TRIGGER / NOT_TRIGGERED / NOT_AVAILABLE`。
持仓不变仍使用既有 `POSITION_HOLD`，与需求中 `HOLD_POSITION` 的中文含义一致，不改账本协议。
进度不是胜率或概率；未知证据不会填零，也不生成新的交易动作。

## 验证证据

| 验证 | 最终结果 |
|---|---|
| TDD 后端首轮 RED | 8 failed：新字段和模块尚不存在 |
| TDD 图形组件 | 29 failed 起步，最终 32 项专项通过 |
| 页面接入 RED/GREEN | 新路由/模块缺失先失败，完成后通过 |
| 手机观察日标注 RED/GREEN | 1 failed / 9 passed，调整纯标签布局后 10 passed |
| 前端全量 | 31 files，177 passed |
| 后端限定回归 | 60 passed，含新增图形 10 项及原研究/只读工作台兼容 |
| TypeScript / production build | PASSED |
| compileall / Ruff F821 | PASSED |
| 新后端模块及测试 Ruff | PASSED |
| 浏览器验收 | 35 passed，桌面 1440x1000、手机 390x844，两个页面及历史 3/4 案例 |
| Canvas | 12 个画布均非空、尺寸有效、数值与 API 对应 |
| 交互与恢复 | 图例切换、全部条件展开、刷新持久性、无整页横向溢出、无 JS 异常 |
| 独立复审 | NO_P0_P1_FINDINGS；独立复跑后端 10、前端 35、TypeScript 通过 |

浏览器运行阻断所有外部请求和写请求；仅观察到被阻断的历史静态字体请求。
首次 E2E 启动早于本地后端完成启动而连接拒绝；健康检查通过后重新做只读验收。
没有因此执行行情、模型或 Daily 重试。最终证据是标注修正后重新执行的 35 项。

证据文件：

- [浏览器逐项结果](strategy_graphics_v1/ui/ui-verification.json)
- [源代码、账户及输入完整性](strategy_graphics_v1/protected-integrity.json)
- [当前只读数据合同摘要](strategy_graphics_v1/graphics-contract.json)
- [独立复审记录](strategy_graphics_v1/independent-review.md)

## 截图索引

| 页面 | 桌面 | 手机 |
|---|---|---|
| 今日摘要 | [截图](strategy_graphics_v1/ui/desktop-paper-trading-summary.png) | [截图](strategy_graphics_v1/ui/mobile-paper-trading-summary.png) |
| 六策略卡 | [截图](strategy_graphics_v1/ui/desktop-paper-trading-cards.png) | [截图](strategy_graphics_v1/ui/mobile-paper-trading-cards.png) |
| 个股研究 | [截图](strategy_graphics_v1/ui/desktop-stock-research-summary.png) | [截图](strategy_graphics_v1/ui/mobile-stock-research-summary.png) |
| 价格/MA/BOLL | [截图](strategy_graphics_v1/ui/desktop-stock-research-price.png) | [截图](strategy_graphics_v1/ui/mobile-stock-research-price.png) |
| MACD | [截图](strategy_graphics_v1/ui/desktop-stock-research-macd.png) | [截图](strategy_graphics_v1/ui/mobile-stock-research-macd.png) |
| RSI6 | [截图](strategy_graphics_v1/ui/desktop-stock-research-rsi.png) | [截图](strategy_graphics_v1/ui/mobile-stock-research-rsi.png) |

[历史 9 月 9 日缩量回踩 3/4 条件展开](strategy_graphics_v1/ui/historical-2026-09-09-three-of-four.png)。

## 未变与暂缓

受保护的 12 个研究/指标/账本代码文件、105 个输入文件、156 个账户文件和 30 个原研究报告文件
均保持原哈希。没有写入新的账户、交易、历史研究报告或第二套状态。

- Provider Calls=0；真实 AI Calls=0；新增行情请求=0；真实交易=0。
- Paper Trading Engine=UNCHANGED；Research Engine=THIN_READONLY_EXTENSION_ONLY。
- AI Provider=DEFERRED；Real Trading=DISABLED；can_publish=false。
- 无云部署、三票扩展、实时流、Provider 接线、正式 Obsidian 发布。
- 量额权威单位、分钟批量权限、30m 首桶供应商待确认项继续保留，本轮不使用这些能力。
- P2：原 ECharts 大包/React Router 预告警告不阻塞；独立复审建议未来把 raw 价格隔离测试扩至
  全部指标及标记等值，目前未发现实现缺陷，本轮不扩大测试工程。

## 日用入口

双击已有桌面 `TickFlow.app`，进入 [今日工作台](http://127.0.0.1:3018/paper-trading)。
查看日期和研究摘要，展开策略条件，再看图。完整图形页：[个股研究](http://127.0.0.1:3018/stock-research)。
仅需看图时不点击运行按钮。新交易日输入仍按既有日用流程准备，不由图表自动获取。

[Runbook](../TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md) 已更新。
下一动作：`BEGIN_GRAPHICS_DAILY_USE`。本轮完成即停。
