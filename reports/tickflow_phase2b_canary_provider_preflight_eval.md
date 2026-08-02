# TickFlow Phase 2B-3B Provider Canary 最终离线门禁报告

## 1. 结论

```text
CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL
```

用户已明确批准包含 `store=false` 的完整 Proxy 工件哈希集合。批准对象已以模式 `0600`、原子 rename 和目录 `fsync` 方式写入本机非敏感批准文件；安装前后都未读取 Keychain Secret 内容。

最终离线预门禁返回 `CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL`。这只证明固定工件、本地边界和保护证据已就绪，不是 TLS 实时握手或 Provider 可用性证据。本轮 Provider 请求、AI 调用和真实公网连接仍为 `0`。

## 2. 隐私合同

| 项目 | 结果 |
|---|---|
| Provider | `openai` |
| Exact Model | `gpt-5.6-terra` |
| Endpoint | `POST https://api.openai.com:443/v1/responses` |
| Symbol | `000403.SZ` |
| Trade date | `2026-07-31` |
| `store` | `false`，不可配置 |
| Streaming | `false` |
| Tools / Web / Files | `disabled` |
| Retry | `0` |
| Maximum attempts | `1` |
| Strict JSON Schema | `READY` |
| Can publish | `false` |

预门禁对缺失 `store` 和 `store=true` 均返回 `responses_schema_not_ready`，并在占位 Secret 注入和任何运行时创建之前停止。

## 3. 已批准的精确工件

| 工件 | SHA-256 / 不可变标识 |
|---|---|
| Base image digest | `sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5` |
| Dockerfile | `31202e8be3e091282b8351086a4d3250b2002ca12632bc2769a9e2133a4c07dd` |
| Proxy source | `47efc9d087e3d028e97a5b2c738fa3c5bdfbce98e4cbbe74b55dca6543e0b02d` |
| Responses contract | `22f6dba5e5292a09237c26bab04b89315fe6f1dd3f2a1d6fcf46272fdf381303` |
| Proxy policy | `3d4c996e80f52bc37d33eea12cf728a9a9c24f0f6aeef5a114833ae245dd5489` |
| Launcher source | `43eaf09a31cdcf94c0d4d6e60eef8c41a6531f6dc48a90dd554cedfb5fa49e9c` |
| Image ID | `sha256:084d13d2524e5992e0034956aad5d18dbb30909b028dea42a9d3acbb1ff9bfe0` |
| Approval candidate file | `c7614947da89cae8727ed3c59d1e965dd73bc95450953d3e59c1a89dd0b9f96d` |

运行身份仍固定为 `65532:65532`，入口仍固定为 `/usr/bin/python3 /proxy/proxy.py`。

## 4. 构建与复审

| 项目 | 结果 |
|---|---|
| Fresh offline build | `PASSED` |
| Build network | `none` |
| Pull | `disabled` |
| No cache | `true` |
| Base RootFS prefix | `VERIFIED` |
| Input hashes | `STABLE` |
| Image identity / contents | `VERIFIED` |
| Image history | `CLEAN` |
| Cleanup | `COMPLETE` |
| Independent review | `PASSED` |
| Focused suites | `312 passed` |
| Backend full suite | `2129 passed, 13 warnings` |
| compileall / Ruff / contract check | `PASSED` |

独立复审未发现 `store=false` 传播、重试/第二调用、Secret 边界或 false-READY 路径问题。复审指出单票报告必须在真实 Git 差异中存在；该文件已纳入最终提交，并由提交后的真实预门禁验证。

## 5. Secret、证据和外部行为

| 项目 | 结果 |
|---|---:|
| Keychain Secret content read | `NO` |
| Secret length / prefix / suffix / hash | `NOT_COMPUTED` |
| Provider attempts | 0 |
| AI calls | 0 |
| Provider HTTP | `NOT_RUN` |
| TickFlow requests | 0 |
| Provider runtime public connections | 0 |
| 九组历史证据 | `UNCHANGED` |
| Facts / Projection / Relay | `VALID` |
| Container / network / process residue | 0 |
| Default artifact approval | `APPROVED / MODE_0600` |
| Superseded approval | `PRESERVED / MODE_0600` |
| Obsidian real vault write | `NO` |
| Paper Trading | `NOT_STARTED` |
| Cloud redeploy | `NO` |
| Integrated Gold | `DISABLED / external_send_count=0` |
| Three-symbol batch | `NOT_APPROVED` |

## 6. 当前停止点

```text
status=CANARY_READY_FOR_FINAL_EXECUTION_APPROVAL
provider_store=false
provider_attempt_count=0
ai_call_count=0
provider_http=NOT_RUN
next_action=REQUEST_FINAL_SINGLE_CALL_APPROVAL
```

本次工件哈希批准不自动扩大为真实调用批准。下一步必须单独获得“唯一一次 `000403.SZ` Provider 调用”的最终执行授权；在此之前不得读取真实 Secret、启动 Canary 运行时或发送外部请求。
