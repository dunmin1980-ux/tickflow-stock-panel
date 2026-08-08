# TickFlow Phase 2B-3B4 Canary 可观测性补强评估

## 1. 结论

最终状态：`PHASE2B_CANARY_OBSERVABILITY_READY_FOR_REAPPROVAL`

本轮只增加子进程错误分类、Relay/Proxy 阶段 receipt、永久归档和失败关闭清理证明。未运行 Canary launcher，未调用 OpenAI 或 TickFlow，未安装新批准。历史 Request ID `d59766101b63450e8148541d589a90bf` 仍为已消费且不可重试。

## 2. 实现范围

- Relay 和 Proxy 严格区分 `TIMEOUT`、`NONZERO_EXIT`、`PROCESS_ERROR`、`SIGNALLED`、`START_FAILED` 和 `EXIT_UNKNOWN`。
- Docker 容器的正数信号退出码，如 `137` 和 `143`，保留容器 exit code 并记录对应 signal。
- 任何非空 child stderr 只保留存在、截断和敏感形态命中元数据；不落盘正文或摘要哈希。
- Proxy 持久化七个 Provider 阶段事件，Relay 持久化本地连接、等待和 Candidate 发布阶段。
- receipt 使用同目录临时文件、`fsync`、原子 `rename` 和目录 `fsync`。
- Host 在 ledger 终态更新与 cleanup 前原子归档 receipt 和 child metadata。
- dispatch 首次验证的 Proxy receipt 原始字节被固定；临时路径后续被替换时，归档仍使用原授权字节。
- Docker 删除或 inspect 失败不再被当作“零残留”；只接受精确的资源不存在证据。

## 3. 必答问题

1. **Relay nonzero 是否与 timeout 分离：** 是。非零退出为 `RELAY_NONZERO_EXIT`，只有真实 deadline 超时为 `RELAY_TIMEOUT`。
2. **Proxy nonzero 是否与 timeout 分离：** 是。非零退出为 `PROXY_NONZERO_EXIT`，超时为 `PROXY_TIMEOUT`。
3. **七个 Provider stage events 是否全部实现：** 是。覆盖 connect start/complete、TLS complete、request write start/complete、response headers/body complete。
4. **Relay receipt 是否 durable：** 是。原子写入并在 cleanup 前归档。
5. **Proxy receipt 是否 durable：** 是。原子写入、原字节固定并在 cleanup 前归档。
6. **child exit metadata 是否在 cleanup 前归档：** 是。生命周期测试证明 `archive -> ledger terminal -> cleanup`。
7. **bounded stderr 是否脱敏：** 是。非空 stderr 不落正文或摘要，仅保留非敏感状态元数据。
8. **error path 是否仍可归档证据：** 是。覆盖非零退出、真超时、receipt 缺失/非法、cleanup 失败及 archive 前后崩溃。
9. **monotonic timestamps 是否落盘：** 是。已发生事件持久化 `monotonic_ns`，并校验递增顺序。
10. **`ATTEMPT_CONSUMED_UNKNOWN` 是否只用于真正未知：** 是。已知的 child、Provider 阶段和 Candidate 错误使用精确分类，证据不足时才保留 unknown。
11. **旧 consumed attempt 是否完全未修改：** 是。历史 ledger、runtime evidence、rejection、诊断和两份报告哈希与冻结值一致，不回填旧 DNS/TCP/TLS/HTTP 事件。
12. **是否具备重新申请新 Canary 的诊断能力：** 是。工程与机器校验已就绪，但新批准仍未安装，必须先对新哈希集合进行人工再批准。

## 4. 验证证据

- 专项测试：`185 passed`
- 后端全量：`2255 passed, 13 warnings`
- `compileall`：`PASSED`
- Ruff F821：`PASSED`
- 独立聚焦审查：三轮；前两轮共发现 5 项可执行问题，全部补测与修复，最终轮为 `No actionable findings.`
- 离线构建：`--network=none --pull=false --no-cache`
- 不可变候选校验：`PASSED`
- 容器 / 网络 / 临时残留：`0 / 0 / 0`

## 5. 新候选工件

- Approval Candidate SHA-256：`b30c156bee1f5f2a1c8d44004fcbd25934e56a1caf6739ab58ffe7ead0e881fb`
- Runtime build evidence SHA-256：`6a91ef0717386cdd872e8fcb175ed8547fcd49cc6890606be27f08cc9bab6978`
- Proxy image：`sha256:421551285b5805abb6e01bcd54f6d7ff2bb0a131ca4a7b0e5e693086174df5a3`
- Relay image：`sha256:e0fd61824433164ef6b2c77a012365a273e71ce26e56283dd36a3ca0396de7b3`
- 旧批准：`e77596da06c1054a01a02066b2ef28f47db3db6f69d981723f7277faf61ade2b`，`SUPERSEDED_AND_PRESERVED`
- 新批准安装：`NO`

## 6. 边界状态

- New Provider attempts：`0`
- New AI calls：`0`
- Provider HTTP：`NOT_RUN_THIS_ROUND`
- New TickFlow requests：`0`
- Paper Trading：`NOT_STARTED`
- Obsidian real Vault：`NO`
- Cloud deploy：`NO`
- Integrated Gold：`DISABLED`
- Three-symbol batch：`NOT_APPROVED`
- `can_publish=false`

## 7. 下一动作

`REQUEST_OBSERVABILITY_HASH_REAPPROVAL`

必须先对本报告的新 Candidate、Proxy/Relay image 和 source hash 集合进行明确再批准。本轮不授权任何新的 Provider 请求。
