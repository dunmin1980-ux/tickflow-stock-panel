# TickFlow Research Engine v1 交付评测

日期：2026-09-09，Asia/Shanghai。正式样本：000403.SZ 派林生物。

## 交付结论

研究能力验收通过：六策略、确定性缠论日线结构、综合研究解释、昨日变化、触发缺口和研究日报已进入今日工作台。
研究结果不参与既有 Paper Action 生成。模拟盘保持 HOLD 不等于研究结果没有变化。
最终提交身份见根目录 `TICKFLOW_RESEARCH_ENGINE_V1_RELEASED.md` 及包含该文件的 Git 提交。

## 恢复与范围

- RESEARCH_RESUME_AUDIT=PASSED。
- 开工分支：`codex/tickflow-visual-workbench-v1`。
- 开工 Head：`de43f8b2157a2653a457773567117983fbb66d8b`，当时 local/fork matched。
- 开工工作区并非干净：已有六策略研究代码、测试、截图与报告未提交。本轮在其上继续实现，没有 reset、clean、覆盖或另建第二套研究引擎。
- 沿用 `phase2_research_panel.py` 六策略计算入口，新增统一合同、有限缠论模块、研究聚合/日报，以及现有 Dashboard 中的只读展示。
- 未修改 Paper Trading、动作规则、T+1、下一日开盘执行、Decimal/PnL、State Store、日用数据输入基础设施或任何 Provider 实现。
- 已有六策略阶段的 `reports/multi_strategy_research/` 保留为历史阶段证据；其中尚未实现缠论的说明不代表本版状态。

## 9 月 9 日重新计算结果

全部从该日已验证冻结输入重新计算，不抄历史报告数值。

| 项目 | 结果 |
|---|---|
| DIF | 0.010503294119764917 |
| DEA | 0.04701416256582537 |
| MACD Hist | -0.07302173689212091 |
| RSI6 | 50.089134272944904 |
| Research Consensus | BEARISH_WITH_CONFLICTS，偏弱但存在分歧 |
| Research Confidence | MODERATE，仅描述证据一致性，不是胜率 |
| Paper Action / 含义 | HOLD / FLAT_WAIT，空仓观望 |
| 最接近触发 | 缩量回踩，3/4 |
| 剩余条件 | 20 日动量 > 0%；当前 -3.754580020904541% |
| 数值缺口 | 3.754580020904541 个百分点；严格大于仍须越过边界 |
| 支持 / 限制 / 分歧 | 6 / 13 / 3 项 |
| 已确认结构 | RANGE_STRUCTURE，整理结构，较前日不变 |
| 最近确认结构高 / 低 | 11.21 / 10.60，QFQ |
| 当前 QFQ 收盘 | 10.51，低于最近确认低点，等待后续分型确认 |
| Claims | VALID / 21 |

六策略分别满足 1/3、1/4、0/2、1/4、3/4、1/3。名称依次为 MACD 金叉放量、均线多头、布林突破、量价齐升、缩量回踩、布林下轨回升。
MACD 柱值比前日改善约 0.01597，但仍为负；RSI6 下降约 9.10549；相对量能从 1.05016 倍降至 0.67208 倍；均线满足数不变，布林区间位置下降。
这些变化并不自动形成模拟订单。原动作仍读取当日正式发布的模拟记录。

## 合同与解释口径

- 技术价格统一 QFQ；回归测试只修改 raw 价格后研究状态不变。相对量能使用同来源 raw volume 比率，不推断绝对量额单位。
- 六策略统一输出状态、偏向、满足数、数值证据、阈值、未满足条件、昨日变化与缺口。现有前五项复用内置计算和默认参数，第六项明确为前日触及下轨、今日回到下轨上方且收盘高于前日。
- NEAR_TRIGGER：恰有一个未满足的当日条件，且满足比例至少一半。历史条件不能由今日价格倒补。
- Closest Trigger 先比较满足比例，再比较缺项数量，最后以策略 ID 稳定排序。不同单位的缺口不相加，也不换算成预测目标价。
- 综合结论采用 MACD/MA 方向、结构确认、形态与显式分歧的决策树；支持项数量不是多数投票，不输出交易动作。页面提供规则追踪。
- `CHAN_DAILY_STRUCTURE_V1` 使用版本化三根日线严格高低点分型，必须等右侧日线完成后才确认；仅提供候选笔，不含包含合并、完整笔/线段/中枢。详见 `docs/research-chan-daily-structure-v1.md`。
- 已确认结构与当前价格位置分开表示，不能把整理结构误读为今天收盘仍在确认区间内。
- HOLD 根据正式当日发布状态区分空仓与持仓。缺少发布日为 NOT_RUN；损坏的发布证据失败关闭，不伪造 HOLD。
- 研究日报为 JSON 和确定性 Markdown，固定 RESEARCH ONLY、SIMULATION ONLY、NOT INVESTMENT ADVICE、can_publish=false、trading_advice=false。

## 十二日离线回看

| 日期 | 综合研究 | 已触发 | 接近触发 | 确认结构 | Paper Action |
|---|---|---|---|---|---|
| 2026-08-25 | MIXED | 无 | 均线、量价 | 整理 | HOLD |
| 2026-08-26 | MIXED | 无 | 均线 | 整理 | HOLD |
| 2026-08-27 | MIXED | 缩量回踩 | 均线 | 整理 | HOLD |
| 2026-08-28 | MIXED | 缩量回踩 | 均线 | 整理 | HOLD |
| 2026-08-31 | MIXED | 无 | 无 | 整理 | HOLD |
| 2026-09-01 | BEARISH_WITH_CONFLICTS | 无 | 无 | 整理 | HOLD |
| 2026-09-02 | BEARISH_WITH_CONFLICTS | 无 | 无 | 整理 | HOLD |
| 2026-09-03 | BEARISH_WITH_CONFLICTS | 无 | 无 | 整理 | HOLD |
| 2026-09-04 | BEARISH_WITH_CONFLICTS | 无 | 无 | 整理 | HOLD |
| 2026-09-07 | BEARISH_WITH_CONFLICTS | 无 | 无 | 整理 | HOLD |
| 2026-09-08 | BEARISH_WITH_CONFLICTS | 无 | 无 | 整理 | HOLD |
| 2026-09-09 | BEARISH_WITH_CONFLICTS | 无 | 缩量回踩 | 整理 | HOLD |

12 个日期使用各自冻结快照分别回算三次，策略、结构、综合结论、摘要、JSON/Markdown 字节一致。未进行选优、拟合或为展示触发而改阈值。
确认结构类别没有变化，但数值、条件、接近触发项目与综合结果确实变化；不能把全部 HOLD 解释成系统没有运行。

证据目录：`reports/research_engine_v1/YYYY-MM-DD/`。
索引：`reports/research_engine_v1/replay_verification.json`。

| SHA-256 对象 | 值 |
|---|---|
| Replay index | `1493de37f5dabd8f82e4ad707896f7331375be35c309a9c39e3dce825a8b92e9` |
| 9/9 Research JSON | `0d5ed9ffd53f99eeff328ead0d5ecbb07e784e843787e0bfa41bd1b6cb2d5cb6` |
| 9/9 Research Markdown | `1b92071c8cfd2788736ab4fc90d50ca0e609e317a92e46f8147c0bbf2990e162` |

## 验证

| 验证 | 实测结果 |
|---|---|
| 后端研究及 Option C/Claims/Renderer/Paper 回归 | 388 passed |
| 其中：新研究合同 / 六策略 / Chan | 20 / 12 / 165 passed |
| 前端全部单元测试 | 141 passed，27 files |
| 其中：新研究页面 / 旧研究面板 / 今日页面 | 27 / 3 / 9 passed |
| TypeScript + production build | PASSED |
| compileall / Ruff F821 / focused E9,F / diff whitespace | PASSED |
| 十二日 Replay x3 | PASSED |
| Chrome 桌面 1440x1000、手机 390x844 | 16 checks passed，0 page errors |
| 独立 focused review | NO_P0_P1_FINDINGS |

独立复审修复了两类 P1：发布日报缺少完整性/状态绑定校验，以及持仓 lot 数量字段读取错误。七种损坏案例被拒绝；真实已有定义的测试夹具 BUY/HOLD/SELL/HOLD 的只读数量为 0/100/100/0，读取前后字节不变。未借此更改账本实现。

E2E 检查总览/API 数值一致、七行矩阵、六策略全部数值证据、分型确认日期、昨日变化、JSON/Markdown 下载、刷新恢复、页面无横向溢出。浏览器下载 JSON 与 API 对象语义一致，Markdown 字节一致；规范离线 JSON 的序列化格式见导出器。
浏览器拦截了所有非本地和非只读请求，只出现 8 次已知历史外部字体请求，均被阻断；没有 Provider 或 Daily Run 请求。桌面截图人工检查通过，手机矩阵可在独立容器内横向查看完整证据，不撑宽页面。

截图与自动化结果：
- `reports/research_engine_v1/ui/research-desktop-overview.png`
- `reports/research_engine_v1/ui/research-desktop.png`
- `reports/research_engine_v1/ui/research-mobile-overview.png`
- `reports/research_engine_v1/ui/research-mobile.png`
- `reports/research_engine_v1/ui/ui-verification.json`

## 未改变的业务边界

七个受保护文件的开工/完成 SHA-256 均一致：Option C schema、continuous、daily、state、Paper Trading、research decision、visual daily input。
97 个原输入文件集合哈希仍为 `d73e4d11a0e5ad0c6772488111f79b9f993036de31e9951744d999ee30810b8f`；145 个账户文件集合哈希仍为 `2d604f1e644ba09755b4e891e9a36457d3eaa47b38a3a55f4d2a5d33fc9a055f`。
集合哈希按排序后的相对路径字节和各文件 SHA-256 原始摘要依次拼接计算。Replay 索引另含逐文件哈希。

新增行情请求=0；AI/Provider 调用=0；真实交易=0；未读取真实 Secret；新增产物凭据形态扫描无命中。
未进行云部署、正式 Obsidian 写入、三票扩展或多策略到 Action 接入。集成 Gold 未开启。
本机旧进程已通过现有桌面 App 重新启动，`/health` 为 `visual_provider_deferred`；未修改启动器。

## 已知限制与 P2

P2_DEFERRED=8：完整缠论定义、高级线段、中枢、背驰、多周期联立、策略回测优化、权重学习、AI 解释。
财务和重大消息尚未接入本研究日报；供应商四项 pending（intraday_batch 权限、30m 首桶 09:30、volume 单位、amount 单位）继续保留。
历史全局顶栏的“数据”日期属于另一条数据状态口径，可能显示 2026-08-25；本研究使用总览和矩阵中明确标注的 2026-09-09 快照。未为此修改范围外的数据基础设施。
构建保留既有大 chunk 提示，单元测试保留 React Router future 提示，均非本轮阻断项。

## 日用入口

双击现有桌面 TickFlow.app，进入 `http://127.0.0.1:3018/paper-trading`。
先核对研究日期，再看综合结论、HOLD 含义、最近触发条件和昨日变化；展开七行矩阵可核对细节，工具栏可下载研究日报。
Runbook：`TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md` 的 Research Engine v1 Daily Use。
下一动作：`BEGIN_RESEARCH_ENGINE_DAILY_USE`。不自动进入 Multi-strategy Action Integration。
