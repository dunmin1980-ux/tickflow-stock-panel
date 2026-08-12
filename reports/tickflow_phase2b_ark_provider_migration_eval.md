# TickFlow Phase 2B Ark Provider Migration Evaluation

## 1. Final status

```text
PHASE2B_ARK_STRUCTURED_OUTPUT_BLOCKED
```

Implementation stopped at the mandatory structured-output capability gate. No
Ark runtime, adapter, proxy policy, mock provider, approval candidate, or live
request was created.

## 2. Baseline

| Item | Result |
|---|---|
| Branch | `codex/tickflow-phase2-ai-review` |
| OpenAI evidence closeout commit | `4f6350b295d6accd30e1e3d7d37f355d866aebc3` |
| Local / fork remote after closeout | `MATCHED` |
| Worktree after closeout | `CLEAN` |
| OpenAI Provider | `FROZEN` |
| OpenAI Request 1 | `d59766101b63450e8148541d589a90bf / PRESERVED` |
| OpenAI Request 2 | `3d3e6adbe5b74c98ad18a2efe0fd1d0c / PRESERVED` |
| OpenAI Request 3 | `9728fc1f156c4568a98cea9c05ec9fa7 / PRESERVED` |

The Request 3 evidence was scanned for common credential patterns before it
was committed. The scan found zero matches.

## 3. Ark secret boundary

The macOS Keychain service `tickflow-phase2-canary-volcengine-ark` exists.
Only the entry's presence was checked. Secret content, length, prefix, suffix,
and hash were not read or recorded.

```text
secret_present=true
secret_content_read=false
```

## 4. Exact model capability gate

The proposed immutable identity is:

```text
provider_id=volcengine_ark
exact_model_id=deepseek-v4-flash-ga-260731
endpoint_alias=ark_responses_cn_beijing_v1
endpoint=https://ark.cn-beijing.volces.com:443/api/v3/responses
```

Volcengine's official model-list page embeds the complete model table in its
public HTML. That table proves the exact Model ID is
`deepseek-v4-flash-ga-260731` and identifies Responses API as a supported API.
The exact model identity gate therefore passes.

The same model-specific capability row lists deep thinking, text generation,
and tool calling, but does not list structured output or `json_schema`. This is
material because the same official table explicitly labels structured-output
support for models that provide it, including a control row which recommends
`json_schema`. The source table header explicitly identifies this column as
`模型能力`, so the absence is not inferred from an unrelated API matrix.

The official Responses API parameter reference exposes
`text.format.type=json_schema`, `text.format.schema`, and
`text.format.strict`, while warning that JSON modes are beta. This establishes
an API-level request shape, not model-level support. It does not override the
target model's capability row.

The official 36-page model-list PDF was also downloaded and visually checked.
PDF page 8 (printed page 5/33) shows the target model with only deep thinking,
text generation, and tool calling in its capability column, while models above
it visibly include structured output when supported. PDF page 26 (printed page
23/33) separately lists Responses API for the target model. The PDF SHA-256 is
`bffa2ad960654d483a98aa33a533da67af4c0ab13c0f7e43ff309ad40914a35b`.
The PDF also contains a dedicated `结构化输出能力 (beta)` section on pages
27-30 (printed pages 24/33-27/33). The target model is absent from the complete
structured-output model list. This is direct model-level evidence, rather than
an inference based only on a missing label in the general capability table.

Official references:

- [Volcengine Ark Responses quick start](https://www.volcengine.com/docs/82379/1795150)
- [Volcengine Ark model list](https://docs.volcengine.com/docs/82379/1330310?lang=zh)
- [Volcengine Ark Responses API](https://docs.volcengine.com/docs/82379/1569618?lang=zh)

The P0 gate results are therefore:

```text
exact_model_id == deepseek-v4-flash-ga-260731: PASSED
Responses API support: PASSED
Responses json_schema support for exact model: NOT_CONFIRMED
```

The approved specification prohibits guessing the model ID and prohibits
fallback to `json_object`, free text, Markdown, prompt-only JSON,
ChatCompletions, `latest`, or another model. Implementation therefore stopped
before code changes.

## 5. External behavior

```text
real_ark_provider_attempts=0
real_ark_ai_calls=0
new_openai_provider_attempts=0
tickflow_requests=0
approval_install=NO
public_provider_probe=NO
cloud_redeploy=NO
integrated_gold=DISABLED
```

No Ark authorization header was constructed. No DNS, TLS, curl, or Provider
runtime probe was executed by the project.

## 6. Required evidence to unblock

Before implementation can resume, obtain non-secret official evidence that the
exact model supports Responses API structured output with the current strict
Typed Claims `json_schema` contract.

Acceptable evidence is an official Volcengine model-list/capability document,
downloaded official PDF, or a redacted console capability export. It must not
contain an Ark Key, account identifier, billing data, Authorization header, or
other credentials.

```text
next_action=OBTAIN_OFFICIAL_EXACT_MODEL_STRUCTURED_OUTPUT_CONFIRMATION
```
