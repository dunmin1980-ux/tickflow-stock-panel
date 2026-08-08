# TickFlow Phase 2B-3B2 Single-Symbol Provider Canary Evaluation

## 1. Final Status

```text
ATTEMPT_CONSUMED_UNKNOWN
```

The approved one-shot launcher was executed exactly once for `000403.SZ`.
`NETWORK_DISPATCH_STARTED` was durably recorded, so the authorization is
permanently consumed. No Provider response, HTTP status, Candidate, Claims, or
rendered Markdown was recovered. The request must not be retried.

## 2. Approved Identity

| Field | Value |
|---|---|
| Current Head | `c3a71dee4fc543a980e1fc48c2b0bde9ea687169` |
| Runtime Approval Candidate | `e77596da06c1054a01a02066b2ef28f47db3db6f69d981723f7277faf61ade2b` |
| Runtime Contract | `34aa9235d25ecc3f2b658551382f2a8ade031eb84f46cc2a1d79bcbc86bdcf68` |
| Request ID | `d59766101b63450e8148541d589a90bf` |
| Symbol | `000403.SZ` |
| Name | `派林生物` |
| Trade date | `2026-07-31` |
| Provider | `openai` |
| Exact model | `gpt-5.6-terra` |
| Endpoint policy | `POST https://api.openai.com:443/v1/responses` |

## 3. Runtime Outcome

| Check | Result |
|---|---|
| Provider attempts | `1` |
| AI calls | `UNKNOWN`, maximum `1` |
| Retry | `0` |
| Provider HTTP | `UNKNOWN` |
| Provider response received | `NO` |
| TLS result | `NOT_PROVEN` |
| Egress policy | `ALLOWLIST_ONLY`; dispatch outcome unknown |
| Candidate | `REJECTED_NOT_PRODUCED` |
| Claims | `NOT_RUN` |
| Claim count | `0` |
| Facts Pointer bindings | `0` |
| Unsupported Claims | `0` observed; no Candidate |
| Free-text fields | `0` observed; no Candidate |
| Trading Claims | `0` observed; no Candidate |
| Raw/QFQ | `NOT_RUN` |
| Renderer | `BLOCKED` |
| Machine validation | `FAILED` |
| Manual review | `PENDING / BLOCKED_NO_CANDIDATE` |
| Canary route | `REJECTED` |
| Can publish | `false` |

The sanitized evidence cannot distinguish DNS, TLS, transport, Proxy, or Relay
failure after dispatch. The contract therefore requires
`ATTEMPT_CONSUMED_UNKNOWN` rather than an inferred cause.

## 4. Durable Ledger

```text
state=ATTEMPT_CONSUMED_UNKNOWN
provider_attempt_count=1
retry_count=0
dispatch_started_at=2026-08-08T08:23:32.214310Z
response_received_at=null
candidate_ready_at=null
host_validation_completed_at=null
```

The ledger is retained as audit evidence. It must not be deleted or altered to
enable another request.

## 5. Cleanup and Security

| Check | Result |
|---|---|
| Exclusive lock | `RELEASED` |
| Proxy/Relay container residue | `0` |
| Network residue | `0` |
| Secret temporary file residue | `0` |
| Request/response/staging residue | `0` |
| Secret hits in evidence | `0` |
| Historical evidence | `UNCHANGED` |
| TickFlow requests | `0` |
| Obsidian real Vault write | `NO` |
| Paper Trading | `NOT_STARTED` |
| Cloud redeploy | `NO` |
| Integrated Gold | `DISABLED / external_send_count=0` |
| Three-symbol batch | `NOT_APPROVED` |

No Secret, Authorization header, complete request, or Provider response was
written to the report or ledger.

## 6. Required Next Action

```text
DIAGNOSE_BLOCKER
```

Diagnosis must remain offline and read-only against the retained evidence. This
report does not authorize a second Provider request, another symbol, a model or
Prompt change, or a direct HTTP probe.
