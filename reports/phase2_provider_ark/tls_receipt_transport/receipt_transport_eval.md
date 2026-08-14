# TickFlow Phase 2B Ark TLS Probe Receipt Transport 本地闭环报告

## 1. 最终状态

```text
PHASE2B_ARK_TLS_PROBE_RECEIPT_CHANNEL_READY
```

本轮只修复并验证 `Probe container -> receipt bundle -> host mount -> host validation -> durable archive` 的本地证据链。未访问 Ark 公网，未读取 Secret，未构造 Authorization，未执行 Provider 或 AI 调用。

## 2. 历史 Probe 冻结

- Probe ID：`5dd641ca9e4b4ccba1035fff4d695409`
- 状态：`PHASE2B_ARK_TLS_PROBE_UNKNOWN / PROCESS_ERROR / CONSUMED / PRESERVED`
- Retry：`0`
- 冻结文件：`7`
- SHA-256 逐文件复核：`UNCHANGED`
- 额外文件、目录、普通或断链 symlink：全部拒绝

本轮未重跑、未删除、未改写历史 receipt、ledger、preflight、postflight、closeout 或 Mock 证据。

## 3. 根因结论

历史容器退出码为 `0`，说明子进程已完成 receipt 写入、flush、文件 fsync、同目录 atomic rename 和父目录 fsync。文件在 Host 也确实可见。

真正失败点是 Host 读取器对 `tempfile.mkdtemp()` 生成的 `/var/folders/...` 路径执行了全链路 symlink 拒绝。macOS 上 `/var -> /private/var` 是可信系统别名，因此 Host 在打开已落盘 receipt 前就误报 `historical_evidence_invalid`，随后又将异常降级成空 `PROCESS_ERROR` receipt。

```text
container_receipt_path=/output/child-receipt.json
host_receipt_path=<canonical-system-temp>/output/child-receipt.json
mount_destination=/output
same_storage_object=YES
write_before_exit=YES
fsync_before_exit=YES
host_read_before_cleanup=YES
```

## 4. 最小修复

1. Host 对受信系统临时目录先执行 `resolve(strict=True)`，再严格拒绝受控目录内的 symlink。
2. `/output` 改为一次性空目录 bind mount，Host mode `0700`，容器视图为 `65532:65532 / 0700`。
3. Probe ID 移到独立只读文件 `/run/tickflow/probe-id`，避免污染输出白名单。
4. Child 持久化 `child-receipt.json` 后，再持久化带 receipt SHA、Probe ID、UID/GID 和目录 mode 的 `receipt.ready`。
5. Host 独立校验目录 `0700`、文件 `0600`、Host UID/GID、容器 publisher/directory `65532:65532`、SHA、Probe ID 和 schema。
6. Host 必须先持久化 child bundle 和 pre-cleanup evidence，再记录 `cleanup_started`。
7. 如已 dispatch 但有效 receipt 尚未完成归档，保留容器、网络和临时目录供恢复，禁止用 error-only receipt 降级替代。
8. `docker inspect` 权限或 daemon 异常不得记为零残留。

## 5. Mount 与持久化合同

| 项目 | 结果 |
|---|---|
| Mount type | 专用空目录 bind mount |
| Destination | `/output` |
| 容器用户 | `65532:65532` |
| Host 目录 / 文件 owner | `501:20` |
| Container 目录 / publisher owner | `65532:65532` |
| 目录 mode | `0700` |
| Receipt / marker mode | `0600` |
| Receipt writable | `YES` |
| 其他可写 bind mount | `0` |
| Atomic rename | `PASSED` |
| Receipt fsync | `PASSED` |
| Parent fsync | `PASSED` |
| `exitCode=0` 推导持久 receipt bundle | `YES` |

Scenario B 使用真实只读 `/output` bind mount 让 `65532:65532` 写入失败，不再用 Python monkeypatch 伪造权限错误。

## 6. 本地 Harness

所有容器均使用 `--network none`，未创建自定义网络。

| ID | 场景 | 结果 |
|---|---|---|
| A | Happy path | `PASSED / exit 0` |
| B | 真实只读 bind mount | `RECEIPT_OPEN_FAILED / exit 70` |
| C | Wrong destination | `RECEIPT_OPEN_FAILED / fail-closed` |
| D | Rename failure | `RECEIPT_RENAME_FAILED / exit 70` |
| E | Receipt 已写但 marker 缺失 | `RECEIPT_OPEN_FAILED / fail-closed` |
| F | Malformed receipt | `RECEIPT_VALIDATION_FAILED / fail-closed` |
| G | Cleanup before archive | `ARCHIVE_REQUIRED_BEFORE_CLEANUP / fail-closed` |
| H | Archive before cleanup | `PASSED` |
| I | 独立 Happy path x3 | `PASSED / 3 个唯一 Probe ID` |

三次 Happy path 全部满足：

```text
container_exit=0
receipt_created=YES
receipt_fsynced=YES
host_receipt_visible=YES
host_validation=PASSED
archive_before_cleanup=PASSED
residue=0
```

## 7. 验证与审计

- Receipt/TLS Probe 相关专项：`75 passed`
- `compileall`：`PASSED`
- Ruff F821：`PASSED`
- `git diff --check`：`PASSED`
- 独立复审：`NO ACTIONABLE FINDINGS`
- 历史 7 文件 SHA 与集合：`UNCHANGED`
- 容器 / 网络 / 临时目录残留：`0 / 0 / 0`
- 新增 Provider attempts：`0`
- 新增 AI calls：`0`
- Ark 公网成功次数：`0`
- Secret content read：`NO`

曾补充执行后端全量测试，结果为 `2627 passed / 14 failed`。14 项失败均由开工前已存在且本轮禁止触碰的未跟踪 `93cf...` 历史文件和 `8e42...` orphan scope 触发旧 Ark 精确 baseline 门禁，与 receipt transport 修复无关。本任务未修改共享 `backend/app/**`，因此该全量结果不是本轮验收门禁，也没有删除这些历史文件来制造全绿。

## 8. 边界和下一步

本轮没有授权且没有执行新的真实 TLS Probe。Receipt Channel READY 不等于 Ark TLS 或 Provider READY。

```text
next_action=REQUEST_NEW_TLS_PROBE_APPROVAL
```

下一步必须重新生成 Probe Candidate、Probe Approval identity 和独立单次授权，不得自动执行公网 TLS Probe。
