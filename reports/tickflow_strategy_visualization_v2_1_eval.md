# TickFlow Strategy Visualization V2.1 验收报告

状态：`TICKFLOW_STRATEGY_VISUALIZATION_V2_1_RELEASED`

日期：2026-09-11，Asia/Shanghai。
基线：`d7a5cc3c92322e90c4ccbe9ad3ee1bd41e5e3c20`。
分支：`codex/tickflow-visual-workbench-v1`。
Release Head 为首次加入本报告与 release marker 的提交；最终 SHA 由提交后 Git 核验记录，不在文件内循环自引用。

## 1. 交付范围

仅完成既定五项：Volume 副图、语义策略标记、证据图层落位、卡片与四图联动、顶部日期语义。最终复审后冻结实现，本收口阶段未修改运行时代码。

| 验收项 | 结果 | 口径 |
|---|---|---|
| Volume Chart | PASSED | RAW 相对量能、现有 VOL5/VOL10、同一观察日 |
| Semantic Markers | PASSED | 六策略稳定类型和 canonical status，Chan 独立 |
| Price Overlay | PASSED | MA/BOLL/回踩/Chan，保留实际价格锚点 |
| MACD Overlay | PASSED | MACD 策略在对应日 DIF 区域 |
| Volume Overlay | PASSED | 量价、回踩、MACD 的量能证据 |
| Strategy Card Linkage | PASSED | 筛选、相关线强调、日期居中、条件详情 |
| Observation Date Sync | PASSED | 卡片、四图、指标值、详情与纸面记录同步 |
| Layer Controls | PASSED | K、MA5/10/20/60、BOLL、策略；只影响显示 |
| Top Date Semantics | PASSED | 系统日、缓存行情日、研究日、模拟盘日分别标注 |
| Desktop | PASSED | Chrome 1440x1000，两个页面 |
| Mobile | PASSED | Chrome 390x844 触控视口，两个页面 |

入口：`http://127.0.0.1:3018/stock-research`、`http://127.0.0.1:3018/paper-trading`。
已有桌面 TickFlow.app 启动本地服务；`/health` 与上述两页均返回 200，运行模式 `visual_provider_deferred`。本次未修改启动器或云端。

## 2. 数据与研究边界

- 后端只在 `phase2_strategy_graphics.py` 扩充已有只读绘图投影，输出 volume、volume_ma5、volume_ma10 与来源元数据，并对空源数据失败关闭。
- QFQ OHLC/MA/BOLL 与 RAW volume 明确区分。Volume 使用研究既有 RAW 序列；VOL5/VOL10 复用 `compute_indicators`，先对完整历史预热再取最近 30 日，不在前端建立第二套指标计算。
- VOL5/VOL10 包含当日，不等于策略中“不含当日的前五根均量”比较基准；两者不互相替换。
- 成交量权威单位未确认，显示“相对量能”，不擅自标记股、手。轴上紧凑数字仅为排版，源值和详情精度未改。
- MACD 金叉放量、均线多头、布林突破、量价齐升、缩量回踩、布林下轨回升保留原策略阈值与状态。
- Chan 保留真实结构类型、价格与确认日期，固定 `NOT_AVAILABLE / N/A`，标注“结构识别｜未定义策略触发合同”。
- 同一 QFQ 快照回溯观察、真实已发布研究、真实模拟动作继续分开。缺失纸面记录或未保存的 HOLD 原因显示不可用，不推断、不回填。
- Research Engine、Paper Trading Engine、指标核心、动作规则和历史研究证据均未修改。未扩股票池，未接完整缠论或策略动作。

## 3. 可视化合同

默认 K + MA5 + MA20，活跃策略与 Chan 优先显示；未触发条件可通过卡片或全状态筛选查看。类型缩写 MC/MA/BU/VP/PB/BL/CH 配合不同形状与全名图例；实心、空心、弱化与灰色表达不同状态，条件进度不是概率。

点击卡片会聚焦该策略、显露相关 MA/BOLL 并打开条件。显式再次点击同一卡片会在手动缩放后重新居中。四图共享观察日期和缩放区间；手机可点标记或直接选择日期，不依赖悬停。

四图使用相同 plot grid，不因价格、量能、MACD、RSI 标签宽度不同而发生日期横向错位。空指标不伪造零值；隐藏蜡烛和全部价格指标时，观察日仍可辨认。

旧顶部“数据 2026-08-25”的来源已核对：`workspace.data_as_of -> get_enriched_latest() -> _enriched_cache_date`，现名称为“股票日线指标缓存日期”。最新行情、研究、模拟盘标签限定为 `000403.SZ 工作台缓存`，不是全市场实时数据。仅订阅页面既有缓存，不新增请求。

## 4. 测试与最终验收

| 检查 | 实际证据 |
|---|---|
| Frontend full | 实施阶段 35 files / 256 passed；已包含卡片重新居中修复 |
| Final layout focused | 最后共享 grid 修改后 7/7 passed；不把此结果冒充重新跑过全量 258 项 |
| Backend focused | 92 passed，覆盖 graphics、overlay、research engine/panel 与 visual workbench |
| TypeScript / production build | 本轮重新执行 `corepack pnpm build`，tsc -b 与 Vite 均通过，exit 0 |
| compileall / Ruff F821 | 实施阶段通过，后端源码随后未再修改 |
| Real-browser E2E | 最终 production build 上 48 PASSED，20 screenshots |
| Independent focused re-review | NO_P0_P1_FINDINGS，7 项 layout 测试；8 configurations / 480 date columns 精确对齐 |
| git diff --check | 提交前通过 |

本轮遵守用户冻结指令，没有重启开发循环或重复历史 Provider 测试。最终 E2E 运行命令：

```bash
cd frontend
node scripts/verify-strategy-visualization.mjs
```

E2E 使用真实本机后端与生产静态构建。网络闸门只允许本机 GET/HEAD，拦截写入与外部字体等请求；不点击运行按钮。覆盖四张非空 canvas、卡片对应原始条件值、日期全局同步、价格/MACD/Volume 实际标记 click/tap、同日展开、Chan、缺失历史、七类图层开关、刷新、明暗主题和无横向溢出/JS 异常。

最终研究日期：2026-09-11。测试前后账户、持仓、决策、交易和净值历史一致：
`c22927981b83ab90246b4a02ec5e5e09e6e83eb6a9b8a27efc9813ff32488659`。

## 5. 验收期间外部日用更新

第一次收口 E2E 读取 9/10 基线后，页面收到了 9/11 新记录，日期断言如实失败，未降低断言或修改代码。后端脱敏访问记录显示一次 `POST /api/paper-trading/run` 返回 200；新日收据落盘于 `2026-09-11T13:38:45Z`。该 POST 不来自验收脚本，其浏览器仅允许 GET/HEAD；无法仅凭本地访问记录确定外部操作者身份。

保留 9/11 新记录，不删除、不回退、不重算。稳定后完整重跑只读 E2E，48 项通过。必须区分：

- 本轮 Agent / 最终 E2E：Daily execution=NO，新增 Provider/AI/行情请求=0，Real Trades=0。
- 整个本地实例：观察到外部 Daily 提交；不能把全实例这段时间的请求或所有可变状态都宣称为零/未变。
- 12 个保护源码文件与 3 组历史报告目录字节不变。
- 旧 inputs 105 文件哈希未变；新增 9/11 的 8 个文件单独保留。
- 旧账户历史核验通过；新日新增 11 文件并正常推进 current_state。只读哈希比较时以既有 9/10 continuous_state 字节代表原 current_state，排除新增日后恰好匹配此前完整 156 文件哈希。未写入、替换或恢复任何账户文件。

证据见 `strategy_visualization_v2_1/protected-integrity.json`，两个可变目录如实标记 `EXTERNAL_DAILY_UPDATE`，不是笼统写成 UNCHANGED。

## 6. 截图与证据索引

目录：`strategy_visualization_v2_1/ui/`。

| 页面/视口 | 总览 | 价格图 | 量能/MACD滚动视图 | 默认详情区 | 浅色模式 |
|---|---|---|---|---|---|
| Desktop Stock Research | desktop-stock-research-overview.png | desktop-stock-research-price.png | desktop-stock-research-volume-macd.png | desktop-stock-research-detail.png | desktop-stock-research-light.png |
| Mobile Stock Research | mobile-stock-research-overview.png | mobile-stock-research-price.png | mobile-stock-research-volume-macd.png | mobile-stock-research-detail.png | mobile-stock-research-light.png |
| Desktop Today | desktop-paper-trading-overview.png | desktop-paper-trading-price.png | desktop-paper-trading-volume-macd.png | desktop-paper-trading-detail.png | desktop-paper-trading-light.png |
| Mobile Today | mobile-paper-trading-overview.png | mobile-paper-trading-price.png | mobile-paper-trading-volume-macd.png | mobile-paper-trading-detail.png | mobile-paper-trading-light.png |

截图为真实视口截图，不要求所有副图同时进入首屏；默认筛选无匹配观察时详情显示为空是有效状态。点选后的日期、原始条件值和触控详情由 E2E 逐项断言。人工复看桌面/手机价格、量能/MACD、详情及明暗主题截图，未发现新增 P0/P1。

- 机器验收：`strategy_visualization_v2_1/ui/ui-verification.json`
- 源码/历史：`strategy_visualization_v2_1/protected-integrity.json`
- 独立复审：`strategy_visualization_v2_1/independent-review.md`
- 启动与日用：`../TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md`

## 7. Deferred 与发布边界

- P2 / DEFER：既有 ECharts 大包构建提示、React Router future flags、更多设备专项认证；本轮不拆包或重构。
- 手机验收是 Chromium 触控视口，不声称已经验收实体 iPhone 或 Safari。
- Provider=DEFERRED，Real Trading=DISABLED，Integrated Gold 不启用，云端不部署，正式 Obsidian 不写入。
- API 总版本仍沿用现有 0.1.86，不等于本次可视化 release marker；不扩大版本元数据修改。

发布仅提交并推送当前分支到 fork；不创建 PR、不合并、不部署。完成后进入 `BEGIN_STRATEGY_VISUALIZATION_DAILY_USE`，停止本轮实施。
