# TickFlow 项目时间线与决策日志

## 口径

- 时间线只采用 Git、release marker、正式报告与 verification JSON。
- `PROJECT_START` 指本次可连续追溯的验证/交付主线起点，不代表上游开源仓库的最初创建日期。
- Commit 选取能代表里程碑的 Head，不等于该阶段只有一个提交。
- `Outcome` 同时保留成功与负面结果，不以最终结论改写历史。

## 里程碑

| Date | Milestone | Problem | Decision | Reason | Outcome | Commit | Evidence |
|---|---|---|---|---|---|---|---|
| 2026-07-27 | `PROJECT_START` / Phase 1.2 | 数据日期、分钟合同与供应商口径需要跨日稳定性证据 | 建立三票五个实际交易日观察，不自动补数据 | 先证明输入，再构建研究层 | 观察工作流启动，Day 1 复用既有正式证据 | `f6df50c` | `reports/phase1_observation/` |
| 2026-07-31 | Phase 1 complete | 单日通过不能证明连续稳定 | 完成 5/5 日观察并冻结审计 | 为后续 Facts 提供可信源 | `PHASE1_OBSERVATION_PASSED` | `3af4185` | `reports/tickflow_phase1_observation_final.md` |
| 2026-07-31 | Phase 2 baseline | 需要从已合并基线独立开发 | 从 Phase 1 合并 Head 建立 Phase 2 | 隔离历史治理与新业务工作 | Phase 2 干净基线 | `690f828` | `reports/phase2_merge_and_baseline.md` |
| 2026-07-31 | Deterministic Facts | 指标数字缺少统一来源和口径 | 建立 Facts schema、source hash、calculation registry | 防止 AI 或人工猜数 | `PHASE2A_FACTS_READY` | `7ba58c3` | `reports/tickflow_phase2a_facts_eval.md` |
| 2026-08-01 | Structured Claims | 自由文本无法可靠进入下游 | 建 Strict Typed Claims、Validator、Renderer | 把解释限制为机器合同 | Claims contract ready | `afa6ca6` / `ab68f68` | `reports/tickflow_phase2b_typed_claims_eval.md` |
| 2026-08-01 | Provider isolation | 真实 Provider 需要 Secret、出站与失败关闭边界 | 设计 isolated relay、allowlist 和 Mock E2E | 让真实调用不污染主进程 | Relay 离线验证通过 | `c761b68` / `cba4fc4` | `reports/tickflow_phase2b_provider_relay_eval.md` |
| 2026-08-02 | `OPENAI_PROVIDER_EXPLORATION` | 需要一次严格、不可重试的真实 Canary | 建 OpenAI Proxy、one-shot、store=false、immutable approval | 控制数据泄漏与重复调用 | 形成安全执行框架，业务结果仍未证明 | `165dc46` 至 `38b90dd` | `reports/tickflow_phase2b_canary_runtime_contract_eval.md` |
| 2026-08-08 | Canary observability | 首次 attempt 结果无法证明网络阶段 | 增加 stage receipts、exit 分类与清理前归档 | 精确区分连接、TLS、写入与响应 | 可观测性增强；历史 unknown 被保留 | `928af48` 至 `5c2b476` | `reports/tickflow_phase2b_consumed_unknown_diagnosis.md` |
| 2026-08-10 | Approval-scoped ledger | 全局历史 attempt 会阻塞新批准工件 | ledger namespace 绑定 approval scope | 不篡改历史，同时保证唯一 attempt | 新 scope 可独立证明 attempts=0 | `57ff781` / `f55a0e6` | `reports/tickflow_phase2b_canary_ledger_namespace_eval.md` |
| 2026-08-12 | `ARK_PROVIDER_MIGRATION` | OpenAI Canary 未形成有效 Claims；Ark 旧模型能力不满足 | 迁移 Ark，并用官方能力门排除旧模型 | 验证另一 Provider 的 strict json_schema | 旧模型 preserved/forbidden，新 exact model 进入候选 | `42f9f0e` / `d6a6b54` | `reports/tickflow_phase2b_ark_provider_migration_eval.md` |
| 2026-08-13 | Timeout contract | Ark 请求在响应头前超时 | 仅调整 timeout 为 180/195/210，retry=0 | 做单变量实验，不改 reasoning/Prompt | Mock 通过，真实路径仍需新 Scope | `7127ab4` | `reports/tickflow_phase2b_ark_timeout_contract_eval.md` |
| 2026-08-14 | `TLS_PROXY_ENGINEERING` | DNS/TCP/TLS/request 阶段需可证 | 加 build provenance、receipt transport、TLS probe v2 | 缩小真实路径根因范围 | 形成大量离线证据，但真实 Proxy 根因仍不唯一 | `d4eb447` 至 `23352e5` | `reports/tickflow_phase2b_ark_proxy_tls_differential_eval.md` |
| 2026-08-15 | Isolated Ark scope | 历史 consumed Scope 不能复用 | 创建新 Candidate/Scope，保持历史字节不变 | 遵守 one-shot 与审批边界 | 实际 Canary 仍在连接阶段阻断 | `0bed04b` / `ad458da` | `reports/phase2_provider_ark/live_canary/d57b276fe9014d35bdae5a92950ca17b-closeout.md` |
| 2026-08-16 | `MINIMAL_ARK_PATH` / `OPTION_A_EXHAUSTED` | 完整 Proxy 路径工程成本过高 | 尝试最小 Ark 路径和 read-timeout 单变量实验 | 判断能否低成本恢复 Provider | 未证明可稳定产出 Claims；Option A exhausted | `292cdd3` 至 `2894dd2` | `reports/tickflow_phase2b_option_c_paper_trading_eval.md` |
| 2026-08-16 | `ENGINEERING_STOP_DECISION` | Provider 成本与业务价值失衡 | 将 Provider 冻结并移出 Critical Path | 优先形成可日用业务闭环 | `BUSINESS_VALUE_REPRIORITIZATION` | `bc76c80` / `7e7d94a` | Option C design/eval |
| 2026-08-16 | `TRANSITION_TO_OPTION_C` | 需要不依赖真实 Provider 的研究到动作链 | 复用 Facts/Claims，新增确定性 Decision、T+1 账本与 ChenQuant Daily | 保留安全资产并恢复交付速度 | Option C ready，真实 Fixture 为 HOLD | `68656dd` 至 `3d2fbac` | `reports/tickflow_phase2b_option_c_paper_trading_eval.md` |
| 2026-08-20 | Option C hardening | 初版需补齐 fixture identity、claims source、HOLD 与执行价边界 | 只修 P0/P1，补幂等、状态恢复与 Replay | 防止未来数据、重复交易和账本损坏 | 单票验证通过，无 P0/P1 | `05ae11a` / `1c16efb` | `reports/tickflow_phase2b_single_symbol_paper_trading_validation.md` |
| 2026-08-20 | `PAPER_TRADING_MVP_RELEASE` | CLI 闭环需正式运行合同和 release marker | 固化 Daily Runner、状态目录、Release SHA | 进入可重复离线日用 | `TICKFLOW_PAPER_TRADING_MVP_RELEASED` | `a14e068` / `fbf4c98` | `TICKFLOW_PAPER_TRADING_MVP_RELEASED.md` |
| 2026-08-21 | `VISUAL_WORKBENCH_RELEASE` | CLI 不符合日常浏览器使用预期 | 加薄 API、Dashboard、一键 Run、持久化视图 | 降低每日操作成本 | 本地 Visual v1 发布 | `443858e` / `a34aa05` | `reports/tickflow_visual_workbench_v1_eval.md` |
| 2026-08-25 | `VISUAL_PRODUCT_CLEANUP` | 历史 Provider/API Key/SaaS UI 混淆当前产品 | 收敛导航，隐藏遗留入口，明确能力状态 | 让用户看到真实已发布能力 | 产品壳清理发布 | `ec6c6d1` | `TICKFLOW_VISUAL_PRODUCT_CLEANUP_RELEASED.md` |
| 2026-08-25 | `WORKBENCH_ACCOUNT_UX_SPLIT` | 一个页面同时回答今日任务和账户状态 | 拆成 `/paper-trading` 与 `/paper-account` | 提高日用扫描与职责清晰度 | 两页共享同一状态，仅 Today 可运行 | `f1587ab` | `TICKFLOW_WORKBENCH_ACCOUNT_UX_SPLIT_RELEASED.md` |
| 2026-08-25 | `BEGIN_NORMAL_DAILY_USE` | 工程验证需要转成真实使用反馈 | 冻结扩功能，按 Runbook 单票日用 | 用真实摩擦决定后续投资 | 当前状态：进入日用，仍 Simulation Only | `f1587ab` | `TICKFLOW_VISUAL_WORKBENCH_V1_RUNBOOK.md` |

## 阶段意义

### 从 Phase 1 到 Facts

这不是普通的数据接入。五日观察把“最近一天有数据”升级为“跨交易日合同可复核”，Facts 再把数据转换为可引用、可计算、可哈希的研究输入。这两步是后续所有成功模块的共同基础。

### 从 Claims 到 Provider

Typed Claims 先于真实 Provider 是正确顺序：先定义什么输出可以被系统接受，再决定由谁生成。问题出现在 Provider 执行保障逐渐成为主项目，而不是 Claims 的一个可替换输入源。

### 从 Provider 到 Option C

Option C 没有降低账本标准，只降低了非确定性依赖。它保留 Typed Claims 和 Validator，用确定性 Fixture/Rule 代替真实模型生成，证明业务链本身成立。

### 从 Engine 到 Product

MVP Release 证明指定引擎合同、Fixture 和测试生命周期通过，Visual Workbench 证明已批准的浏览器路径可操作。产品清理与 UX 拆分则解决“页面表达的能力”和“实际发布的能力”不一致的问题；发布后的连续日用稳定性仍需真实使用记录补证。

## KEY_REVERSIBLE_DECISIONS

| Decision | Why reversible | Current state |
|---|---|---|
| Provider Adapter 保留但退出 Critical Path | Facts/Projection/Claims 合同仍兼容可选 Provider | `DEFERRED` |
| 单票范围 | 引擎 schema 可扩，但未批准三票日用 | `000403.SZ only` |
| 本地浏览器部署 | 薄 API/React 可迁移，但云端未发布 | `LOCAL ONLY` |
| Obsidian 自动写入关闭 | ChenQuant Markdown 已存在，可旁路加人工门禁 | `NO FORMAL WRITE` |
| 历史 Settings 页面隐藏 | 路由和代码保留，可在能力真正接通后重做 | `LEGACY HIDDEN` |

## KEY_IRREVERSIBLE_DECISIONS

严格说，软件架构决策大多可逆；真正不可逆的是已发生的外部行为和历史证据：

| Decision / event | Irreversible property | Required preservation |
|---|---|---|
| One-shot dispatch 后消费 attempt | 无法安全证明请求未到 Provider | Scope/ledger 永久不可复用 |
| 历史 HTTP 401 / unknown / Ark blocked | 结果已经发生，不能改写为 success | rejected、receipt、closeout 原字节保留 |
| Paper Trading 已发布日状态 | 日账本形成历史链 | 禁止手工重写 current/day artifacts |
| Release SHA | 对应已验收字节集合 | 新改动必须生成新 Head/Release marker |

## ENGINEERING_STOP_DECISIONS

1. **停止 OpenAI Scope 重试**：unknown 或 401 后均不重复同一 request/scope。
2. **停止 Ark 模型能力猜测**：不支持 Structured Output 的 exact model 被明确排除。
3. **停止继续扩大 TLS/Proxy 工程**：差分诊断不能产生足够业务增量时，不再自动建新 Scope。
4. **停止让 Provider 阻塞产品**：Option A 标记 `EXHAUSTED`，Option C 成为 `ACTIVE`。
5. **停止为演示制造动作**：真实 Fixture 为 HOLD 即接受；BUY/SELL 只在测试 Fixture 中验证。

这些决定的共同原则是：失败证据必须保留，但历史投入不能自动成为继续投入的理由。
