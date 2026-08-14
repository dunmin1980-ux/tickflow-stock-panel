# TickFlow Phase 2B New Ark Canary Scope 离线审批准备报告

## 最终状态

`CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL`

本轮仅生成并核验新的 Ark AI Canary Candidate 和 Approval Scope。未执行真实 Ark Provider 请求、AI 调用或第二次 TLS Probe。

## 不可变身份

- Candidate source Head：`c3182157e0fefc674b18661028da0b46a5c593d5`
- New Ark Approval Candidate：`26c09cc183adcd7562e53f72368a27eceff8b56603cb0ce250fe1244466b41ac`
- New Ark Approval Scope：`6f696a3213c32f962c2b7f8571509b499d9d7e978e773edd09aac1543f7f265a`
- Provider：`volcengine_ark`
- Exact model：`doubao-seed-2-1-turbo-260628`
- Symbol：`000403.SZ`
- Retry：`0`
- Maximum Provider attempts：`1`

Candidate 与 Scope 均绑定同一 source Head。最终证据提交会晚于 Candidate source Head，这是内容证据提交造成的正常差异，不改变 Candidate 的不可变身份。

## TLS 证据

- TLS Connectivity：`VERIFIED`
- Probe ID：`d73e91f766854ff7a64c0e725939eb64`
- Probe result：`PASSED`
- Receipt SHA-256：`ef1310f780687e494965d399c1c02d7d7666abeb7a018310ab7b5a4d9abaa6e3`
- Historical TLS Scope：`abd999450d007f23cadccd86d22136608c3fbb04b1d92c0078153088781f919c`
- Historical TLS Scope 状态：`CONSUMED / PRESERVED / NON_REUSABLE`
- 第二次 TLS Probe：`NOT_RUN`

## Scope 门禁

- Historical attempts：`0`
- Attempt availability：`AVAILABLE`
- Attempt directory：`ABSENT`
- Complete artifact hashes：`PASSED`
- Complete source bindings：`PASSED`
- Build Provenance：`PASSED`
- Artifact Generation：`PASSED`
- Historical evidence：`UNCHANGED`

## 独立审查

首次审查发现离线预门禁在 `independent_review=PENDING` 时提前写入 READY。已通过 TDD 修复：当前 `offline_preflight.json` 固定为 `PENDING_INDEPENDENT_REVIEW`，最终 READY 仅由独立审查通过后的验证证据和本报告声明。

最终独立复核：`NO ACTIONABLE FINDINGS`。

## 验证结果

- Ark focused tests：`257 passed`
- Candidate verifier：`PASSED`
- compileall：`PASSED`
- Ruff F821：`PASSED`
- git diff --check：`PASSED`
- Backend full：`2679 passed / 13 failed / 13 warnings`

后端全量的 13 个失败仅位于 Gold 日期保留测试。相同测试在原始 Head `23352e5871c61f56bfde01726a040c7d548daac7` 上得到完全相同的 `13 failed / 43 passed`，原因是固定的 2026-07-16 样本被当前日期的 10 日保留逻辑清理，判定为既有时间敏感失败，不是 Ark 回归。本轮未修改 Gold。

## 安全边界

- Keychain Secret：`PRESENT`
- Secret content read：`NO`
- Real Ark Provider attempts：`0`
- Real AI calls：`0`
- Provider HTTP：`NOT_RUN`
- Container residue：`0`
- Network residue：`0`
- Temporary residue：`0`
- can_publish：`false`

本轮未构造 Authorization Header，未执行 DNS/TCP/TLS/curl 探测，未安装 Approval，未启动真实 Canary launcher，未进行三票批次、Paper Trading、正式 Obsidian 写入或云端部署。

## 下一动作

`REQUEST_FINAL_ARK_SINGLE_CALL_APPROVAL`

在收到新的明确单次调用授权前，不得执行真实 Ark Provider 请求。
