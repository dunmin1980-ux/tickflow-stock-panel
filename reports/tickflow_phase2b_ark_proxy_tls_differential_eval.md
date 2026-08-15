# TickFlow Phase 2B Ark TLS Probe 与 Proxy 路径差分诊断

## 最终状态

```text
PHASE2B_ARK_PROXY_TLS_PATH_DIAGNOSIS_INCOMPLETE
```

历史 Probe 与失败 Canary 的证据均已按 SHA-256 冻结并复核，13 个文件原字节保持不变。本轮未访问 Ark 公网、未读取 Secret、未构造 Authorization、未发送 HTTP、未增加 Provider attempt 或 AI call。

## 历史事实

- 成功 TLS Probe：`d73e91f766854ff7a64c0e725939eb64 / PASSED / PRESERVED / NON_REUSABLE`
- 失败 Canary：`d57b276fe9014d35bdae5a92950ca17b / FAILED_AFTER_DISPATCH / CONSUMED / PRESERVED / NON_REUSABLE`
- 失败最后证明阶段：`provider_connect_started`
- 旧 Proxy 分类：`TLS_FAILED`，没有足够异常元数据可做事后精确归因

## 差异结论

两条历史路径使用相同容器镜像、基础镜像、Python 3.11.2、OpenSSL 3.0.19、CA 文件字节、UID/GID、目标主机、SNI 与主机名校验目标。

实际调用链不同：

```text
Probe:
socket.getaddrinfo
-> socket.socket
-> connect
-> SSLContext.wrap_socket(server_hostname=TARGET_HOST, do_handshake_on_connect=False)
-> do_handshake

Proxy:
http.client.HTTPSConnection(host, port, context)
-> HTTPSConnection.connect
-> socket.create_connection
-> SSLContext.wrap_socket(server_hostname=host)
```

网络拓扑也不同：Probe 使用单个普通 bridge；Provider Proxy 先连接 internal Relay bridge，再连接独立 egress bridge。因此：

- `same_tls_implementation=NO`
- `same_network_topology=NO`
- `same_egress_policy=UNKNOWN`，两条路径都有普通 egress bridge，但历史容器、宿主机防火墙和 allowlist 状态不可恢复
- `same_dns_configuration=UNKNOWN`，历史容器已清理，不能从设计推断真实运行时 DNS 完全一致
- `ca_bundle_bytes_same=YES`
- `same_sni=YES`
- `same_hostname_verification=YES`

完整逐项证据见 `reports/phase2_provider_ark/proxy_tls_differential/path_difference_matrix.json`。

## 全内网 Parity Harness

Harness 只创建 Docker `--internal` 网络，挂载生产 Ark Proxy TLS 源码，不挂载凭据，不调用业务请求函数。八个用例全部符合预期：

| 用例 | 结果 |
|---|---|
| trusted Proxy | `PASSED` |
| trusted Probe | `PROBE_PASSED` |
| unknown CA | `TLS_CERT_VERIFY_FAILED` |
| hostname mismatch | `TLS_HOSTNAME_VERIFY_FAILED` |
| connection reset | `TLS_CONNECTION_RESET` |
| handshake timeout | `TLS_HANDSHAKE_TIMEOUT` |
| protocol incompatibility | `TLS_PROTOCOL_FAILED` |
| connection refused | `PROVIDER_CONNECT_FAILED` |

可信同目标场景中，Probe 与 Proxy 均协商：

```text
TLSv1.3
TLS_AES_256_GCM_SHA384
```

且 CA SHA、实际由 Mock 服务端观察到的 SNI、主机名校验目标一致，因此记录：

```text
LOCAL_PROXY_TLS_PATH_VALIDATED
```

这只能证明本地生产 TLS 连接实现可用，不能证明历史 Ark 公网 Proxy 路径当前可用，也不能把网络拓扑差异认定为历史失败的唯一根因。

## 分类补强

Ark Proxy 的连接边界已按 TDD 增加精确分类：证书、主机名、协议、握手超时、连接重置、EOF、其他 SSL 与连接失败。`exception_class`、`verify_code`、脱敏 `verify_message` 和 `errno` 写入 Ark receipt schema v3；历史 Ark v2 receipt 继续按原字节兼容，未被回填或重分类。Secret、请求体和完整 stack trace 不保留。

TLS 校验策略未放宽：`CERT_REQUIRED`、`check_hostname=true`、最低 TLS 1.2 均保持不变。

## 为什么根因仍不可证明

1. 旧失败收据只保存 `TLS_FAILED`，没有原始 SSL 异常类别。
2. 旧容器和网络已清理，无法只读恢复当时 DNS、route 或瞬时网络状态。
3. 本地同目标测试没有复现 Probe PASS / Proxy FAIL。
4. 调用链和网络拓扑确实不同，但现有证据不能证明其中任一项导致了旧失败。

因此根因必须记录为：

```text
NOT_PROVABLE
```

## 验证

- 精确分类专项：`20 passed`
- Ark 运行时兼容专项：`127 passed`
- Proxy-path TLS parity：`PASSED`
- Probe-vs-Proxy same-target：`PASSED`
- compileall：`PASSED`
- Ruff F821：`PASSED`
- `git diff --check`：`PASSED`
- 历史证据：`UNCHANGED`
- 容器残留：`0`
- 网络残留：`0`
- Provider attempts：`0`
- AI calls：`0`
- 公网成功：`0`
- 独立复审：`NO ACTIONABLE FINDINGS`

后端全量结果为 `2656 passed, 57 failed, 13 warnings`。其中 41 项是修改 Proxy/Orchestrator 源码后，旧不可变镜像和 Approval 的 source binding 按设计 fail-closed；本轮没有生成或安装新 Candidate，也没有创建新 live Scope。其余 16 项均已在 clean Head `ad458daa57425093940480f1a090537e885ba35d` 独立复现：3 项是已提交的顶层 closeout 文件不在历史 launcher 目录白名单内，13 项是 Gold comparison/store 的既有测试债务。因此本轮不宣称后端全量测试通过。

当前工作树源码身份：

```text
Proxy source SHA-256:
5bd5349d899d0d693054d332b66d81e9d64bc3332133903b91e2d05e71c479c7

Orchestrator source SHA-256:
c94ac29ca20bc2eb590bf465fc576505821b651ef9b7a7a6ac4b5be7181ebd9e
```

## 下一动作

```text
DESIGN_PROXY_PATH_TLS_PROBE
```

下一步应设计一次无 Secret、无 HTTP、复用真实双网络拓扑的 Proxy-path TLS-only Probe，并为修改后的 Proxy 源码重新走不可变镜像与审批身份流程。在获得新审批前，不得执行真实 Ark Canary。
