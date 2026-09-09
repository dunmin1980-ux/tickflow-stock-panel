# 日线多策略研究交付记录

日期：2026-09-09。基于工作区 `tickflow-phase2-ai-review`、分支
`codex/tickflow-visual-workbench-v1`、基线提交
`de43f8b2157a2653a457773567117983fbb66d8b`。

## 最值得补齐的交付缺口

原始目标是多策略日线研究，已交付首页却主要呈现 MACD + RSI6 的单一模拟动作。
研究条件是否出现、差在哪里、与前日有何变化，并没有完整交付给用户。
模拟账本可用不等于多策略研究目标已完成；HOLD 也不能代替研究结论。

本次直接在现有今日工作台增加只读研究面板，没有另建架构、重写模拟引擎或恢复 Provider。

## 本次可用能力

访问 `http://127.0.0.1:3018/paper-trading`。

| 规则 | 实现来源 | 今日命中条件数 |
|---|---|---:|
| MACD 金叉放量 | 现有 `macd_golden` 默认参数 | 1/3 |
| 均线多头 | 现有 `bullish_alignment` 默认参数 | 1/4 |
| 布林突破 | 现有 `boll_breakout` 默认参数 | 0/2 |
| 量价齐升 | 现有 `volume_price_surge` 默认参数 | 1/4 |
| 缩量回踩 | 现有 `pullback_to_support` 默认参数 | 3/4 |
| 布林下轨回升 | 明确定义的新观察规则 | 1/3 |

新观察规则仅定义为：前日收盘不高于布林下轨、今日收盘高于下轨且高于前日收盘。
它不等于缠论，也不宣称完整的价格行为识别。

每条规则显示实际数值、条件阈值、满足情况和较前日的变化；数据不足和校验失败
分别显示，不伪装成未命中。风险标识来自现有策略 exit 条件，不是账户风险估计。
HOLD 区分为空仓观望和持仓不变，并单独展示当前模拟规则的 MACD + RSI6 依据。

2026-09-09 六条规则均未完整命中。例如缩量回踩满足价格接近 MA20、缩量、
位于 MA60 上方，但 20 日动量为 -3.7546%，因此为 3/4，而不是强行判定通过。
量比为 0.6721，收盘为 10.51，MA20 为 10.6405，均按面板的前复权口径。

离线读取已保存的 12 个日线输入：8 月 27、28 日的缩量回踩规则命中，
同日原研究信号为 RISK_OBSERVATION。这里只说明不同规则存在不同观察结果，
不是新的交易建议、历史买卖记录或策略收益证明。

## 数据与边界

- 固定 `000403.SZ` 派林生物；不扩三票。
- 读取现有已验证输入，重新检查文件哈希；本轮外部行情请求为 0。
- 价格使用 qfq；相对量能使用同源 raw 成交量。绝对量额单位仍待供应商确认。
- 前日比较采用同一快照的前一交易日，不混用不同复权快照。
- 不补空成交额，不移动日期，不修改原始行情。
- 研究读取失败时只阻断研究区，已保存账户仍可读取。
- 不改变 Paper Action、交易规则、账本、PnL 或 ChenQuant Daily 内容。
- AI/Provider 继续 DEFERRED；无 Secret 读取、AI 调用、实盘、云部署。
- 缠论：NOT_IMPLEMENTED。策略参数编辑、研究日报整合与多规则模拟动作尚未交付。
- 顶栏原有的全局行情库日期与研究快照日期不是同一来源；本面板明确显示其实际
  研究日期 2026-09-09，不把全局库的旧日期当作本次日线输入日期。

## 验证

| 验证项 | 结果 |
|---|---|
| 新研究专项 | 12 passed |
| 研究、Visual facade、输入准备、模拟引擎回归 | 83 passed |
| 前端研究面板、工作台、账户测试 | 14 passed |
| TypeScript + production build | PASSED |
| compileall、Ruff F821、新文件 F401/E9 | PASSED |
| git diff --check | PASSED |
| 桌面 1440x1000、手机 390x844 浏览器验收 | PASSED |
| 页面条件值与 API 一致、筛选、刷新持久性、无横向溢出 | PASSED |
| 账户及研究结果在只读页面刷新前后完全一致 | PASSED |
| 独立复审 | 无剩余 P0/P1 |

复审发现并修复了 MA20 严格边界的浮点精度差异；解释谓词与 builtin 数组
运算使用相同精度，新增上下边界各两种量能组合测试。真实数据验证发现的历史
nullable amount 推断问题通过只选择所需字段解决，没有清洗或改写源数据。

UI 验收禁止所有非本地请求及 POST；拦截了历史页面的 8 次外部字体加载请求，
未发现其它外部或写入请求。字体使用本地回退，不把拦截数写成 0。
本轮未再次执行今日 Daily Runner。

源目录清单哈希（相对路径与逐文件 SHA-256 顺序汇总），前后完全相同：

```text
inputs: 97 files
bbabcbaeba1e5b01d86f15015951001beb49bac3e72cde1dbe109cea5f7b3fd1

reference_account: 145 files
5e06c1707b849a02241ee2480ba4a077b256a9ece06bb8cd38e6f201d73d02f2
```

## 使用与后续顺序

本地服务已通过现有桌面 App 重启，health 模式仍为 `visual_provider_deferred`。
日常仍使用桌面图标和原有日线运行入口，完成后直接查看研究面板，无需新 Key。
重新打开页面即可加载新构建；未自动提交、推送或创建新的 Release。

后续应继续补研究价值：先明确一种可验证的缠论定义并标注已确认/未确认结构，
再把多策略结果写入研究日报。不要为了产生 BUY/SELL 而修改真实数据或模拟规则，
也不要再把 Provider 审批基础设施放回本阶段的开发重点。

## 交付文件

- `backend/app/services/phase2_research_panel.py`
- `frontend/src/components/paper-trading/ResearchPanel.tsx`
- `frontend/scripts/verify-research-panel.mjs`
- `TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md`
- [桌面研究区](multi_strategy_research/research-desktop.png)
- [手机研究区](multi_strategy_research/research-mobile.png)
- [UI 验收记录](multi_strategy_research/ui-verification.json)
