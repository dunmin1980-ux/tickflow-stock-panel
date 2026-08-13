# TickFlow Phase 2B Ark Candidate Build Provenance 收口报告

## 1. 最终状态

```text
PHASE2B_ARK_TIMEOUT_CONTRACT_READY_FOR_REAPPROVAL
```

本轮走 `FRESH_OFFLINE_REBUILD`：固定旧镜像缺少完整构建来源标签，未被推断或
复用。新 Proxy / Relay 由只读 staging 快照严格离线重建，随后生成不可变 Build
Provenance、Approval Candidate 和 Approval Scope。本轮没有安装新 Approval，
没有读取 Secret 内容，也没有执行真实 Ark/OpenAI Provider 请求或 AI 调用。

## 2. 构建来源绑定

| 绑定 | 结果 | SHA-256 / image ID |
|---|---|---|
| Pinned base image | PASSED | `sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5` |
| Proxy Dockerfile | PASSED | `e2aa832c0d6f0bc30b5564fcf2c9620544e6d369158031527e985ca3dfa4ae7b` |
| Proxy source | PASSED | `fe479235628c22af20cc31965940f612cf759a6a280c2feb792a3fd2400580e4` |
| Relay Dockerfile | PASSED | `464d77529b4a40dfbb33fd017a89cbd4c38858a92e75835a87ed80b3636c9e58` |
| Relay source | PASSED | `5715299a2103b7515e0c618880c2008def0499e12e2ef24da5bb6cf15bd8fbe3` |
| Proxy image | PASSED | `sha256:a8a01a003f7afc385b4e24fd2960bcdfd3eaac40c394f39aa6249fd7b2f0f79b` |
| Relay image | PASSED | `sha256:95a06dc938887f04358e42816a032f3e961cc45bb85d593946510d15fa6b46fb` |

构建使用 `--network=none --pull=false --no-cache`。构建上下文先复制到仓库外的
一次性 staging，拒绝符号链接和非普通文件，冻结为只读后再计算标签并构建；构建
结束还会复核 staging 与源工作区哈希，关闭 source hash TOCTOU 窗口。两个镜像的
RootFS 均验证以本地 pinned base 的完整 layer 序列为前缀。

旧镜像状态：

```text
Proxy sha256:7c74df9f92df2209abaf0f58b8d00075382025a89b00f2061ed9e961da698ae8: MISSING
Relay sha256:5447846b77d5918d5280ecbcbcfde7fa06ae427af3c82b7cab52edc747ea259c: MISSING
```

## 3. 不可变工件

| 工件 | 标识 |
|---|---|
| Build Provenance 文件 SHA-256 | `390fe01c366d8ca78effa44e450dd0d459a240ee9b4f165dd6a1a5804e67bf39` |
| Build Provenance 内嵌 self-hash | `6820449c2bec2e6d2121f0528da93a7891d758f178d3364d7f73aa5605c4b093` |
| Exact Ark Mock E2E 文件 SHA-256 | `fb67c11b1a2ee041f6a6b18cfddf997aea8bf4daff97e7e6c881637ba450de73` |
| New Candidate SHA-256 | `75263d87a2ae6c368ce6c2ce4d65dfbc20b29f4c5bf38e827b2da07290dda2a1` |
| New Approval Scope ID | `8e42a251cfa4e5b4cbdc95b3aceca9d59152ce574e65f5ba2300a0009df8a019` |
| Approval Scope 文件 SHA-256 | `3c2b6c386e1633deb30c0609da62a3c850a5a5b5bb2a2e609542b96d72b23067` |
| Artifact Generation 文件 SHA-256 | `01239091c691b72e96d2f40ba81c6f1fe896f941529fa0bac2b930dd4c61f544` |
| Artifact Generation self-hash | `5d3256616846413b7014d3b549a22ea601f02b0f22806d94ee7d58993057f85f` |

新 Scope 结果：

```text
historical_attempts=0
attempt_availability=AVAILABLE
ledger_preflight_status=READY
approval_installed=false
```

Candidate 显式逐文件绑定 Dockerfile、Proxy/Relay source、Build Provenance、
runtime/readiness contract、orchestrator、Ark adapter/launcher、Responses contract、
proxy policy、Facts、Projection、Typed Claims Schema 和 Mock E2E。目录聚合哈希未被
用作单文件绑定的替代品。

独立复审后又补强两层失败关闭边界：Candidate 的全部文件输入由同一 `O_NOFOLLOW`
只读快照提供，并在发布前重新核对；镜像构建、Mock、Candidate 生成和真实 launcher
共用同一跨进程锁。生成流程先撤销旧 READY manifest，逐文件原子发布并完整重验，
最后才原子提交 `artifact_generation.json`。因此并发更新或中途失败不能留下可被
Approval loader 接受的混合代际工件。

## 4. Mock 与防误放行

Exact Ark Mock E2E 在隔离的内部 TLS 网络运行三次，结果全部为：

```text
Candidate=VALID
Claims=VALID
Renderer=DETERMINISTIC
retry=0
real_provider_attempt_count=0
real_ai_call_count=0
real_public_network_success_count=0
```

Mock 三次运行使用三个不同的确定性 request ID 和 Docker 名称前缀，避免跨运行
容器/网络名称碰撞。Mock 证据不仅绑定哈希，还由严格语义校验器核对三次运行、镜像
ID、Facts/Projection、Claims、Renderer、零真实调用和零残留。Approval Scope
builder 在生成 `READY` 前必须通过当前完整 Candidate 校验，旧格式仅可用于历史
ledger 身份解析。

## 5. 历史证据冻结

历史 Ark Request：

```text
2f17745f58534063bdd7eda1eb0d16f1
FAILED_AFTER_DISPATCH / CONSUMED / PRESERVED
WAITING_FOR_PROVIDER_RESPONSE_HEADERS
```

| 历史工件 | SHA-256 / 状态 |
|---|---|
| Historical evidence | `35f957462c493331fe89c39f43420b3699a52346df4b8d3f6ff5beeebab1466e` |
| Historical ledger | `5eeba9e7068c9225a1963883f7b814b87fe6198c53202502595a177f671bc8ed` |
| History baseline | `704a4e4a3341e0c04943468ed02b6ea7c4757bec0794b5946eb5097405698853` |
| Old Approval | `0fee5a1c509c77ae9b03107dfddf8caefa453673106cced13db4a4d6ac523ffc` / `SUPERSEDED_AND_PRESERVED` |
| Old incomplete Candidate | `c9a305de7f0064a47ff6d3549ee00190120c67428cde1fd9b7f31aace682fbad` / `INVALID_FOR_INSTALL / SUPERSEDED_AND_PRESERVED` |
| Previous provenance Candidate | `1958c91ee1a6f2f0590de78a87af939311525017b8ab3972fc5650624dbb09e0` / `SUPERSEDED_AND_PRESERVED` |

上述历史证据原字节哈希未变化。旧 `c9a305...` Candidate 会被当前 verifier 拒绝，
且不能生成 `READY / AVAILABLE` 的新 Scope。

## 6. 验证结果

```text
Ark focused: 265 passed
Backend full: 2573 passed, 13 warnings
compileall: PASSED
Ruff F821: PASSED
git diff --check: PASSED
Exact Ark Mock E2E x3: PASSED
container_residue: 0
network_residue: 0
temporary_residue: 0
Independent review: multiple focused rounds, final NO ACTIONABLE FINDINGS
```

敏感扫描只命中 Proxy 源码中预期的运行时 `Authorization` header 构造语句；生成的
Provenance、Candidate、Scope、Mock 和本报告均未包含 Secret、Cookie、Session 或
真实 Authorization 值。

## 7. 审批边界

```text
approval_installed=NO
secret_content_read=NO
real_ark_provider_attempts=0
real_ark_ai_calls=0
provider_http=NOT_RUN
```

本报告只形成新的哈希重新审批对象，不构成 Approval 安装或真实 Provider 调用授权。

下一动作：

```text
REQUEST_ARK_TIMEOUT_HASH_REAPPROVAL
```
