# TickFlow Phase 2B Ark Provider Migration Evaluation

## 1. Final status

```text
PHASE2B_ARK_MODEL_ID_BLOCKED
```

Implementation stopped at the mandatory model capability gate. No Ark runtime,
adapter, proxy policy, mock provider, approval candidate, or live request was
created.

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

The repository's current tree, all local Git history, neighboring TickFlow
worktrees, and existing reports contain no official capability snapshot for
`deepseek-v4-flash-ga-260731`. The only local occurrence was the migration
instruction itself, which is a requirement rather than independent evidence.

Volcengine's official documentation confirms the Beijing Ark Responses
endpoint and shows model IDs are supplied to `POST /api/v3/responses`. Its
model-list page was updated on 2026-08-11, but the accessible static document
did not expose the model table. Public search found an official Volcengine
developer article announcing DeepSeek V4 Flash as a product, but did not expose
the exact API model ID. It also did not establish that this exact model supports
the required Responses `json_schema` contract.

Official references:

- [Volcengine Ark Responses quick start](https://www.volcengine.com/docs/82379/1795150)
- [Volcengine Ark model list](https://docs.volcengine.com/docs/82379/1330310?lang=zh)
- [Volcengine DeepSeek V4 announcement](https://developer.volcengine.com/articles/7645133105870667830)

Consequently, neither of the following P0 assertions is proven:

```text
exact_model_id == deepseek-v4-flash-ga-260731
Responses json_schema support == CONFIRMED_FOR_EXACT_MODEL
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

Before implementation can resume, preserve a non-secret official capability
snapshot in the repository that proves both:

1. the exact Ark API Model ID is `deepseek-v4-flash-ga-260731`; and
2. that exact model supports Responses API structured output with the current
   strict Typed Claims `json_schema` contract.

Acceptable evidence is an official Volcengine model-list/capability document,
downloaded official PDF, or a redacted console capability export. It must not
contain an Ark Key, account identifier, billing data, Authorization header, or
other credentials.

```text
next_action=PROVIDE_OFFICIAL_ARK_MODEL_CAPABILITY_SNAPSHOT
```
