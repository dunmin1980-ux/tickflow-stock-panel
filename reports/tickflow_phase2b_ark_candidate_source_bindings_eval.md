# TickFlow Phase 2B Ark Candidate Source Bindings 评估报告

## 1. 最终状态

```text
PHASE2B_ARK_CANDIDATE_BUILD_EVIDENCE_BLOCKED
```

本轮没有安装 Approval，没有执行真实 Ark/OpenAI Provider 请求，没有读取
Secret 内容，也没有保留证据不足的新 Candidate 或 Approval Scope。

## 2. 已核验的源码绑定

以下值均按仓库文件原始字节计算：

| 绑定 | 值 |
|---|---|
| Base image digest | `sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5` |
| Proxy Dockerfile | `82bb2101351b8022ea6a7ccdf0d51ebe562017e36bc7050986b0b3361dfdae2b` |
| Proxy source | `fe479235628c22af20cc31965940f612cf759a6a280c2feb792a3fd2400580e4` |
| Relay Dockerfile | `a977f96c15793619c7e52caf99ba09f939f754effcfd13e33f6b7f0b45a5443c` |
| Relay source | `5715299a2103b7515e0c618880c2008def0499e12e2ef24da5bb6cf15bd8fbe3` |

Proxy 和 Relay Dockerfile 使用同一 digest-pinned base image。四个源文件自镜像
构建提交 `7127ab4` 至当前 Head 未发生变化。

## 3. 阻断原因

已提交的 `reports/phase2_provider_ark/mock_e2e.json` 固定了 Proxy/Relay image ID
并记录三次 Mock E2E 通过，但它没有保存镜像 labels、Dockerfile SHA 或完整构建
provenance。Mock 生成脚本只读取 image ID；Candidate 构建脚本原本会在 Docker
可用时另外 inspect labels，但该检查结果没有写入不可变证据。

当前 Docker Engine 不可用，无法重新 inspect 以下固定镜像：

```text
Proxy: sha256:7c74df9f92df2209abaf0f58b8d00075382025a89b00f2061ed9e961da698ae8
Relay: sha256:5447846b77d5918d5280ecbcbcfde7fa06ae427af3c82b7cab52edc747ea259c
```

因此现有证据只能分别证明“源文件哈希”和“Mock 使用的镜像 ID”，不能证明这两个
镜像 ID 确实由上述五项 source bindings 构建。根据 fail-closed 规则，不得将二者
拼接成可重新审批的 Candidate。

## 4. 旧 Candidate 保全

旧的不完整 Candidate：

```text
c9a305de7f0064a47ff6d3549ee00190120c67428cde1fd9b7f31aace682fbad
INVALID_FOR_INSTALL / SUPERSEDED_AND_PRESERVED
```

原字节副本保存在：

```text
reports/phase2_provider_ark/superseded/
c9a305de7f0064a47ff6d3549ee00190120c67428cde1fd9b7f31aace682fbad.json
```

当前仓库中的旧 Candidate/Scope 基线未被新工件覆盖。本报告不构成安装授权。

## 5. 历史证据与外部行为

历史 Ark Request 保持：

```text
2f17745f58534063bdd7eda1eb0d16f1
FAILED_AFTER_DISPATCH / CONSUMED / PRESERVED
WAITING_FOR_PROVIDER_RESPONSE_HEADERS
```

固定证据：

```text
Historical evidence SHA-256: 35f957462c493331fe89c39f43420b3699a52346df4b8d3f6ff5beeebab1466e
Historical ledger SHA-256:   5eeba9e7068c9225a1963883f7b814b87fe6198c53202502595a177f671bc8ed
History baseline SHA-256:    704a4e4a3341e0c04943468ed02b6ea7c4757bec0794b5946eb5097405698853
Old Approval SHA-256:        0fee5a1c509c77ae9b03107dfddf8caefa453673106cced13db4a4d6ac523ffc
```

```text
approval_installed=NO
secret_content_read=NO
real_ark_provider_attempts=0
real_ark_ai_calls=0
provider_http=NOT_RUN
container_residue=NOT_VERIFIED_DOCKER_UNAVAILABLE
network_residue=NOT_VERIFIED_DOCKER_UNAVAILABLE
temporary_residue=0
```

## 6. 验证与复审

在发现构建证据缺口前，拟议的 source binding 实现通过了：

```text
Ark focused: 123 passed
Backend full: 2522 passed, 13 warnings
compileall: PASSED
Ruff F821: PASSED
```

这些结果仅证明拟议代码的行为，不足以补齐镜像 build provenance。拟议的新
Candidate、Scope、builder/verifier 修改和测试已 fail-closed 回滚，不作为 READY
证据。独立复审结论为 `ACTIONABLE_FINDINGS`，其中 build provenance 缺口是本次
阻断依据。

## 7. 解除阻断所需证据

1. 恢复 Docker Engine，但不启动任何 Canary 或 Provider 路径。
2. 对固定 Proxy/Relay image ID 执行只读 inspect。
3. 核验并保存 base image digest、Proxy/Relay source labels、runtime label 和 image ID。
4. 生成包含 image ID、labels、五项 source bindings 及其原始文件哈希的不可变 build evidence。
5. 独立复审确认镜像与源码绑定后，再重新生成 Candidate 和 Approval Scope。

下一动作：

```text
DIAGNOSE_BLOCKER
```
