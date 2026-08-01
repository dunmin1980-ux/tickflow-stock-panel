# TickFlow Phase 2B-3B Provider Canary 安全预门禁报告

## 1. 结论

```text
PHASE2B_PROXY_ARTIFACT_APPROVAL_REQUIRED
```

独立 OpenAI Proxy 已完成纯离线实现、无网络构建、不可变镜像校验和独立代码复审。当前只缺用户对本报告所列精确工件哈希的明确批准；本轮未安装本地批准文件，也未执行 Provider Canary。

本轮没有读取 Keychain Secret 内容，没有记录 Secret 长度、前后缀或哈希，没有发起 Provider、AI、TickFlow 或其他公网请求。

## 2. 固定合同

| 项目 | 固定值 / 结果 |
|---|---|
| Provider | `openai` |
| Exact Model | `gpt-5.6-terra` |
| Endpoint | `POST https://api.openai.com:443/v1/responses` |
| Symbol | `000403.SZ` |
| TLS verification | `true`，最低 `TLSv1.2` |
| Redirect | `disabled` |
| Streaming | `false` |
| Tools / Web / Files | `disabled` |
| Retry | `0` |
| Maximum attempts | `1` |
| Timeout | `60 seconds` |
| Maximum response | `1,048,576 bytes` |
| Response contract | strict JSON Schema |
| Can publish | `false` |

Proxy 只接受严格 `application/json`，可选参数仅允许单个 UTF-8 charset；其他媒体类型、额外参数、重定向、超限响应、非 JSON 响应和 schema 不合格响应均按 fail-closed 处理。

## 3. 精确工件哈希

以下值来自 `approval_candidate.json`，必须作为一个整体批准，不允许只批准镜像标签或部分文件：

| 工件 | SHA-256 / 不可变标识 |
|---|---|
| Base image digest | `sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5` |
| Dockerfile | `31202e8be3e091282b8351086a4d3250b2002ca12632bc2769a9e2133a4c07dd` |
| Proxy source | `b17ce80c480576c36ee81764ce51154720e6834de9c11a6186b99e80ff8021f0` |
| Responses contract | `6fab15c902e310f299a5a2545c3d84428ab6bf69052a37ca7dee367baa67ac06` |
| Proxy policy | `46c8b4aef0f775b31b5da0843fd56791e0ad87c25067f6ba603f291ac93b4093` |
| Launcher source | `43eaf09a31cdcf94c0d4d6e60eef8c41a6531f6dc48a90dd554cedfb5fa49e9c` |
| Image ID | `sha256:083cd22cd8fac6e6de1051ef78e62033711783a50a5b4a1c0d49c5b8908754d4` |
| Approval candidate file | `437c51a7afda22f54fb9fa4160e696dabbf5b2e5334efd6abe317a9179d6105a` |

运行身份固定为 `65532:65532`，入口固定为 `/usr/bin/python3 /proxy/proxy.py`。后续运行门禁必须使用不可变 Image ID；可变标签不能作为执行身份。

## 4. 离线构建与镜像校验

| 项目 | 结果 |
|---|---|
| Fresh build | `PASSED` |
| Build network | `none` |
| Pull | `disabled` |
| No cache | `true` |
| IID file | `VERIFIED` |
| Base RootFS prefix | `VERIFIED` |
| Input hashes before / after build | `STABLE` |
| Image identity | `VERIFIED` |
| Image history | `CLEAN` |
| Image contents | `VERIFIED` |
| Cleanup | `COMPLETE` |
| Independent review | `PASSED / NO ACTIONABLE FINDINGS` |

独立复审覆盖精确 Endpoint、TLS context、重定向和重试关闭、响应解析、schema 校验、Secret 生命周期、镜像与源码绑定、Docker 命令限制、外部批准信任边界、异常脱敏和 false-READY 路径。复审提出的可变标签执行、构建来源证明、遗留 `--build` 路径和 Content-Type 参数过宽问题均已修复并重新验证。

## 5. Secret 与运行边界

| 项目 | 结果 |
|---|---|
| Provider 配置 | `VALID` |
| Keychain Secret | `PRESENT`，仅检查存在性 |
| Secret 内容读取 | `NO` |
| Secret 哈希记录 | `NO` |
| 临时只读单文件注入预演 | `PASSED` |
| Secret 日志命中 | `0` |
| Secret 产物命中 | `0` |
| Secret 临时残留 | `0` |
| Proxy container residue | `0` |
| Proxy network residue | `0` |
| Proxy process residue | `0` |
| 本地工件批准文件 | `NOT_INSTALLED` |

存在性检查只允许使用不输出 Secret 的 Keychain 查询方式。真实执行仍被精确工件批准门禁阻断。

## 6. 历史证据与外部行为

| 项目 | 结果 |
|---|---:|
| Facts / Projection / Relay | `VALID` |
| 九组受保护历史证据 | `UNCHANGED` |
| Provider attempts | 0 |
| AI calls | 0 |
| Provider HTTP | `NOT_RUN` |
| TickFlow API requests | 0 |
| Real public network successes | 0 |
| Obsidian real vault writes | 0 |
| Paper Trading | `NOT_STARTED` |
| Cloud redeploy | `NO` |
| Integrated Gold | `DISABLED / external_send_count=0` |
| Three-symbol real batch | `NOT_APPROVED` |

## 7. 验证结果

| 校验 | 结果 |
|---|---|
| Proxy / Runner / Artifact / Preflight 专项 | `309 passed` |
| 后端全量 | `2126 passed, 13 warnings` |
| `compileall app scripts proxy` | `PASSED` |
| Ruff F821 | `PASSED` |
| 聚焦 Ruff | `PASSED` |
| Proxy contract deterministic check | `PASSED` |
| Immutable artifact verify | `PASSED` |
| 高置信敏感形态扫描 | `CLEAN` |
| 独立安全代码复审 | `NO ACTIONABLE FINDINGS` |

## 8. 当前停止点

```text
status=PHASE2B_PROXY_ARTIFACT_APPROVAL_REQUIRED
provider_attempt_count=0
ai_call_count=0
provider_http=NOT_RUN
tickflow_api_request_count=0
real_public_network_success_count=0
next_action=APPROVE_EXACT_PROXY_ARTIFACT_HASHES
```

在用户明确批准第 3 节完整哈希集合之前，不得安装 `proxy-artifact-approval.json`，不得进入最终 READY，也不得执行单票真实 Provider Canary。
