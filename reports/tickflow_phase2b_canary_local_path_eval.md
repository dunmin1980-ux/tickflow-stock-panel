# TickFlow Phase 2B-3B6 Canary Runtime / Approval Contract 收口报告

## 1. 最终状态

```text
PHASE2B_CANARY_LOCAL_PATH_READY_FOR_REAPPROVAL
```

该状态仅表示新的 schema-v2 Runtime Candidate 已通过离线构建、不可变验证、
Exact Mock E2E x3 和独立复审，可供用户重新审批哈希。本轮未安装审批，
未读取 Keychain Secret，未调用 OpenAI Provider，也未进行任何公网探测。

- 新 Approval Candidate SHA-256：`769f4d762594ac496bbaf14b90b8dd3cb7eae572ac9bf05f0484abfe3b86ffaf`
- 旧 Candidate `45c5a569eb542ff8b03c53cd0be995d769a2a9a998a8319c2df0618d410d5127`：`INVALID_FOR_RUNTIME / SUPERSEDED_AND_PRESERVED`
- 历史 Request `d59766101b63450e8148541d589a90bf`：`CONSUMED`
- 历史 Request `3d3e6adbe5b74c98ad18a2efe0fd1d0c`：`FAILED_AFTER_DISPATCH / CONSUMED`
- 新增真实 Provider attempts：`0`
- 新增真实 AI calls：`0`
- 真实公网连接成功数：`0`

## 2. 八项问题收口

| # | 合同问题 | 结果 | 机器级保证 |
|---:|---|---|---|
| 1 | Readiness hash runtime identity | PASSED | `readiness_contract_sha256` 进入 Candidate identity、完整 schema-v2 loader、镜像 label/content 和 immutable verification |
| 2 | Preflight 精确 hash allowlist | PASSED | 15 个精确路径哈希，包含 readiness contract；Git 变更合同 74/74 路径精确匹配 |
| 3 | Pre-dispatch receipt 归档 | DURABLE | `FAILED_BEFORE_DISPATCH` 在 attempt=0 时仍归档可验 receipt，cleanup 不丢证据 |
| 4 | Proxy exception terminal receipt | DURABLE | readiness 失败、请求处理异常和 lifecycle 异常均写入脱敏、持久 terminal receipt |
| 5 | Relay 75s / Host 90s timeout 分层 | PASSED | Relay timeout、Host orchestrator timeout、child nonzero exit 和 process error 分类独立 |
| 6 | Monotonic stage selection | PASSED | `last_proven_stage` 优先使用已持久 `monotonic_ns`，wall clock 仅作辅助 |
| 7 | Mock 外部批准 SHA | PASSED | CLI 必须提供精确 Candidate SHA，3 次均经真实 v2 Approval loader，不可绕过 binding |
| 8 | Mock/真实拓扑差异及 pre-fix replay | PASSED | 三项差异逐 token 机械校验；离线回放固定复现 `ECONNREFUSED -> RELAY_TIMEOUT` 旧误分类 |

Approval loader 还经过一次独立复审加固：现在必须解析完整
`RuntimeArtifactCandidate`、验证全部 cross-bindings 与 canonical bytes，缺失任一字段的
schema-v2 Candidate 都会在 loader 阶段 fail closed。

## 3. 新不可变工件

| 工件 | SHA-256 / Image ID |
|---|---|
| Approval Candidate | `769f4d762594ac496bbaf14b90b8dd3cb7eae572ac9bf05f0484abfe3b86ffaf` |
| Runtime Contract | `34aa9235d25ecc3f2b658551382f2a8ade031eb84f46cc2a1d79bcbc86bdcf68` |
| Readiness Contract | `092f45689e2bea4bd3e45dc3e8f473fe180cd67832ec8300ba66dfecfa71d59e` |
| Orchestrator source | `dc2d9562c8c5d92612308b07d07d40293478783a0c1dee3270a25f7163724810` |
| Proxy image | `sha256:97f410e482119c2aa6d4c9cabf58c5510c7dd9c92189ae6d3306777315db3dc8` |
| Relay image | `sha256:92a18952b6a6ab140879194cfa18d3e81080bab2f2f214630ae42758213c3c0f` |
| Facts | `adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956` |
| Projection | `0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f` |

构建参数固定为 `--network=none --pull=false --no-cache`；不可变验证通过，
Candidate 与当前 15 个精确源文件哈希一致。

## 4. Exact Mock E2E x3

机器证据：`reports/phase2_provider_canary/local_path_mock_e2e.json`

| 项目 | Run 1 | Run 2 | Run 3 |
|---|---:|---:|---:|
| Approval loader | APPROVED | APPROVED | APPROVED |
| Proxy ready | YES | YES | YES |
| Provider stages | COMPLETE | COMPLETE | COMPLETE |
| Proxy terminal | FORWARDED | FORWARDED | FORWARDED |
| Relay terminal | RELAY_COMPLETED | RELAY_COMPLETED | RELAY_COMPLETED |
| Candidate / Claims | VALID | VALID | VALID |
| Retry | 0 | 0 | 0 |
| Topology command diff | VERIFIED | VERIFIED | VERIFIED |
| Container / network / temporary residue | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |

3 次 Mock Provider attempt 全部发生在 internal-only Docker network，不计入真实 Provider attempt。
Renderer SHA-256 三次固定为：

```text
63482087ca33cb7a0ea4767c9b4f41e9176b7abd0f5ac5516bb14b11fc9514e8
```

Pre-fix replay：`reports/phase2_provider_canary/local_path_pre_fix_replay.json`，SHA-256：
`44ddc6b2c50897fa11d8e5d03cb20b1c44950f8cc73b0e9978046d8303d30b9f`。

## 5. 历史证据与安全边界

- 13 个历史 evidence / rejected / receipt / diagnosis / superseded Candidate 文件与冻结哈希完全一致：`UNCHANGED`
- 旧 `45c5...` Candidate 在 runtime loader 阶段被拒绝，文件继续保留
- 任务产物中 OpenAI Key 形态、Bearer value、Authorization value 和 private-key marker：`0 hits`
- Keychain Secret content：`NOT_READ / NOT_EXPOSED`
- 新 Approval install：`NO`
- Paper Trading / Obsidian Vault / 云端部署 / Integrated Gold：`NOT_STARTED / NO / NO / DISABLED`

## 6. 验证结果

- Phase 2 专项：`948 passed`
- Backend 全量：`2297 passed, 13 warnings`
- `compileall app scripts proxy relay`：`PASSED`
- Ruff F821：`PASSED`
- `git diff --check`：`PASSED`
- Runtime Candidate immutable `--verify`：`PASSED`
- Exact runtime hash allowlist：`15/15 PASSED`
- 独立复审：`NO_ACTIONABLE_FINDINGS`

13 条 warning 为已有依赖/API 弃用提示，与本次 Canary Runtime / Approval 收口无关。

## 7. 下一动作

```text
REQUEST_LOCAL_PATH_HASH_REAPPROVAL
```

用户需对本报告中的新 Candidate 和工件哈希集合进行独立重新审批。
本报告不构成审批安装或真实 Provider 调用授权。
