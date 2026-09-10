# TickFlow Strategy Overlay V2 交付验收

状态：`TICKFLOW_STRATEGY_OVERLAY_V2_RELEASED`

日期：2026-09-10，Asia/Shanghai。
分支：`codex/tickflow-visual-workbench-v1`。
基线：`177205f8e2edfe6289dad37e5096054fd5d06d32`。
发布身份：包含 `TICKFLOW_STRATEGY_OVERLAY_V2_RELEASED.md` 的 Git 提交。

## 交付范围

两个页面共用主图与只读数据：`/paper-trading`、`/stock-research`。
主图为前复权 K 线、原 MA/BOLL 和最近 30 个交易日的策略标记。同日多条
观察合并为一个带数量的标记，按日期对齐于主图；不挤占价格或图例空间。

六策略完整覆盖 MACD 金叉放量、均线多头、布林突破、量价齐升、缩量回踩、
布林下轨回升。策略状态、条件满足数、原条件及 delta 均来自原研究计算。
新增只读 `overlay_markers`，保留 V1 `strategy_markers` 兼容字段。

支持全部/单策略以及活跃、全部状态、仅 TRIGGERED、仅 NEAR_TRIGGER 筛选。
默认只突出触发、近触发与 Chan 结构。悬停、点击、触摸和日期选择均可查看
同日详情，手机详情位于图下。手机默认显示 10 日视窗，滑动窗口查看完整
30 日数据；中等宽度显示 16 日，桌面显示 30 日，避免相邻标记重叠。

## Chan 与 HOLD

Chan 保留真实分型/候选笔类型、发生日期和确认日期，标记只放在确认日期。
固定 `canonical_status=NOT_AVAILABLE`、进度 `N/A`，显示
“结构识别｜未定义策略触发合同”。没有伪造触发状态或条件比例。

历史模拟记录独立校验对应日期的 Daily、回执与账本身份，不把同一快照的
策略前缀回溯当成历史已发布决策。没有有效发布记录时显示不可用。
现有 MVP Daily 未保存 HOLD 原因文字，因此该项明确不可用；只展示真实
已发布动作、信号、数量与来源，不用今日解释回填过去。

## 实际快照

研究日期为 2026-09-10；价格窗口为 2026-07-31 至 2026-09-10。
30 根 QFQ K 线，180 条六策略观察，8 个已确认 Chan 事件。
窗口内 14 日有有效已发布模拟记录，16 日无记录，未做任何补录。
当前模拟动作仍为 HOLD；图层不会改变该结论。

## 验证结果

| 项目 | 结果 |
|---|---:|
| 后端 Overlay 专项 | 11 passed |
| 后端相关回归（含专项） | 71 passed |
| 前端 Overlay 专项 | 57 passed |
| 前端全量 | 211 passed / 32 files |
| TypeScript / production build | PASSED |
| compileall / Ruff F821 / 新 Python 文件 Ruff | PASSED |
| 桌面与手机浏览器验收 | 44 passed |
| 独立复审 | NO_P0_P1_FINDINGS |
| git diff --check / 新增内容凭据形态扫描 | PASSED / 0 命中 |

浏览器使用 Chromium，1440x1000 桌面与 390x844 手机触摸模拟，覆盖两条路由。
检查真实 canvas 点击/悬停/触摸、全部七类筛选、原值与 delta、Chan 口径、
缺失历史记录、亮暗主题、刷新恢复、无横向溢出。保存 20 张截图。
三个选择框的文字对比度：暗色约 16.97，亮色约 17.72，均高于 4.5。

TDD 先得到 9 项后端缺失字段失败和 35 项前端预期失败，再实现；追加复现并
修复了回执非对象、tooltip 过长、暗色控件对比度问题。一次浏览器初始 canvas
等待超时未复现，原因未确认；没有据此修改产品逻辑或加自动重试，最终完整
44 项运行成功。真实手机 Safari 硬件测试不在本轮已验证范围内。

## 不变性与边界

12 个受保护研究/指标/模拟引擎文件哈希不变；输入 105 文件、账户 156 文件、
历史研究报告 30 文件及 Graphics V1 证据 25 文件哈希不变。
浏览器验收前后账户、持仓、决策、交易和权益返回值完全一致。

`Provider Calls=0`，`AI Calls=0`，`Market Requests=0`，`Real Trades=0`。
没有执行 Daily，没有读取 Secret，没有新增股票、改策略阈值或交易规则。
仅重启本地工作台加载新版，未部署云端，Integrated Gold 未开启。
既有构建大文件提示和 React Router future-flag 警告保留，不做范围外重构。

## 交付索引

- [运行说明](../TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md)
- [只读不变性证据](strategy_overlay_v2/protected-integrity.json)
- [独立复审](strategy_overlay_v2/independent-review.md)
- [浏览器验收明细](strategy_overlay_v2/ui/ui-verification.json)
- [桌面主图](strategy_overlay_v2/ui/desktop-stock-research-default.png)
- [手机密集标记](strategy_overlay_v2/ui/mobile-paper-trading-all-statuses.png)
- [手机亮色](strategy_overlay_v2/ui/mobile-paper-trading-light.png)

完成即停。不进入策略动作接入、完整缠论或其他阶段。
