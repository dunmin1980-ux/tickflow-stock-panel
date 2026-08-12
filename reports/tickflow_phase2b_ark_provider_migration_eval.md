# TickFlow Phase 2B Ark Provider Migration Evaluation

## 1. Final status

```text
PHASE2B_ARK_PROVIDER_READY_FOR_REAPPROVAL
```

The Ark provider migration is implemented and validated for one separately
approved `000403.SZ` Canary. No Ark Approval was installed and no real Ark
request was executed.

## 2. Immutable provider identity

| Item | Value |
|---|---|
| Provider | `volcengine_ark` |
| Exact model | `doubao-seed-2-1-turbo-260628` |
| Endpoint alias | `ark_responses_cn_beijing_v1` |
| Endpoint | `https://ark.cn-beijing.volces.com/api/v3/responses` |
| Keychain service | `tickflow-phase2-canary-volcengine-ark` |
| Output mode | `json_schema` with `strict=true` |
| Retry | `0` |
| Maximum attempts | `1` |
| Publish | `false` |

The former exact model `deepseek-v4-flash-ga-260731` remains:

```text
PHASE2B_ARK_STRUCTURED_OUTPUT_BLOCKED / PRESERVED / FORBIDDEN
```

There is no `json_object`, free-text, prompt-only JSON, ChatCompletions, model
alias, `latest`, OpenAI, or old DeepSeek fallback in the Ark execution path.

Official capability evidence:

- [Volcengine Ark model list](https://docs.volcengine.com/docs/82379/1330310?lang=zh)
- [Volcengine Ark Responses API](https://docs.volcengine.com/docs/82379/1569618?lang=zh)
- [Volcengine Ark Responses quick start](https://www.volcengine.com/docs/82379/1795150)

The checked-in capability snapshot records Responses API and structured-output
support for the exact new model. The request contract permits only
`text.format.type=json_schema`.

## 3. Runtime and Approval scope

The provider-neutral interface, Ark adapter, strict Responses envelope parser,
proxy policy, immutable Proxy image, one-shot launcher, independent Ark
Approval loader, global lock and approval-scoped ledger are implemented.

Important boundaries:

- Candidate and Approval are read once using parent directory descriptors,
  `O_NOFOLLOW`, `fstat`, owner and mode checks.
- The launcher uses the approved immutable byte snapshot and does not reopen the
  Candidate to derive a fresh ledger scope.
- OpenAI accepts only the base OpenAI Receipt schema.
- Ark accepts only the extended Receipt schema with the exact provider, model
  and endpoint alias.
- Dispatch, failure archival and historical ledger validation all enforce the
  matching provider Receipt schema.
- The global lock and Request ID uniqueness remain shared across providers.
- The Ark Approval scope reports zero historical attempts and `AVAILABLE`.

## 4. Immutable evidence

| Artifact | SHA-256 |
|---|---|
| Ark Approval Candidate | `0fee5a1c509c77ae9b03107dfddf8caefa453673106cced13db4a4d6ac523ffc` |
| Ark Approval Scope preflight | `ae4710b24fb032c37179584c955ccee5cf36eb3a41d61689c8d23ad66f39490a` |
| Ark Mock E2E report | `0252360307c125812c7dfde87dc652223967e8ffdac2261acaad1b8df67fd05d` |
| Facts | `adae2b97110da8c759fd9697cf2677faeaea5536b9bce4d66c38fcbf75572956` |
| Projection | `0ffe5b7fbd57d6b16af073db12d826c940c88b43547870f6b9513401eb49fb1f` |
| Runtime contract | `34aa9235d25ecc3f2b658551382f2a8ade031eb84f46cc2a1d79bcbc86bdcf68` |
| Ark Proxy image | `sha256:234a49c104d142afd826bb3fb073a0a72780a8fb61e22912eae07fee863721d7` |
| Relay image | `sha256:54b4cbf91f578a6d6d328cb2a249444c0cdbdc633cbf63f6cf6f3b1ff8af3627` |

The Scope evidence binds both the final Candidate SHA and Mock E2E SHA. The
Mock report binds the Proxy/Relay image IDs plus final orchestrator, runtime,
Facts and Projection hashes.

## 5. Mock E2E

Three independent internal-TLS Mock executions passed:

```text
requested_run_count=3
completed_run_count=3
distinct_approval_scope_count=3
mock_provider_attempt_count=3
retry_count=0
real_provider_attempt_count=0
real_ai_call_count=0
real_public_network_success_count=0
container_residue_count=0
network_residue_count=0
temporary_residue_count=0
```

Each Mock run proved the strict Ark request, provider-bound Receipt, Responses
envelope, Typed Claims schema, Claims validator and deterministic renderer.

## 6. Verification

| Gate | Result |
|---|---|
| Ark focused tests | `56 passed` |
| OpenAI/runtime compatibility | `327 passed` |
| Backend full | `2452 passed, 13 warnings` |
| compileall | `PASSED` |
| Ruff F821 | `PASSED` |
| Ark changed-file Ruff | `PASSED` |
| Ark changed-file format | `PASSED` |
| Independent review | `P0/P1/P2: NO FINDINGS` |
| Historical evidence manifest | `133/133 UNCHANGED` |
| Sensitive pattern scan | `0 matches` |

The 13 full-suite warnings are existing deprecation/sortedness warnings and do
not represent test failures.

## 7. Secret and external behavior

Only Keychain item existence was checked. Secret content, length, prefix,
suffix and hash were not read or recorded.

```text
keychain_secret=PRESENT
secret_content_read=NO
approval_install=NO
provider_http=NOT_RUN
real_ark_provider_attempts=0
real_ark_ai_calls=0
tickflow_requests=0
paper_trading=NOT_STARTED
obsidian_real_vault_write=NO
cloud_redeploy=NO
integrated_gold=DISABLED
container_residue=0
network_residue=0
temporary_residue=0
```

## 8. Residual risk and next action

The exact model's real Ark Responses behavior has not been exercised because
this phase explicitly prohibited a live Provider call. That is the remaining
expected risk and must be handled by a separate human approval.

```text
next_action=REQUEST_NEW_ARK_APPROVAL_CANDIDATE_REAPPROVAL
```

Do not install an Approval or run the launcher until the new Candidate and its
complete immutable hash set are explicitly approved.
