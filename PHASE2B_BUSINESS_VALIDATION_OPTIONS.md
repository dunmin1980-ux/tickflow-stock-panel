# TickFlow Phase 2B Business Validation Options

## 1. Decision Status

```text
PHASE2B_ENGINEERING_STOPPED_BUSINESS_DECISION_READY
```

This document closes the network and timeout-forensics work and compares only
the business paths that can move TickFlow toward a usable research workflow.
It does not authorize implementation or any Provider request.

## 2. Frozen Engineering Conclusion

```text
NETWORK_TLS_ENGINEERING=CLOSED
TIMEOUT_FORENSICS=CLOSED
LEGACY_PROXY_RELAY_PATH=FROZEN
```

- TLS connectivity is verified and closed. Probe
  `d73e91f766854ff7a64c0e725939eb64` is consumed, preserved, and non-reusable.
- Minimal Ark Request `459db009c89853f9240d4a155a93d275` is consumed,
  preserved, and non-reusable. Its terminal state is `TIMEOUT`.
- The request ran for `180.288192s`. The exact DNS, TCP, TLS, request-write,
  response-header, and response-body stages were not recorded and remain
  `NOT_PROVEN`.
- The runtime was `httpx 0.28.1 / httpcore 1.0.9`, with connect timeout `10s`,
  read timeout `180s`, write timeout `180s`, no end-to-end total timeout, and
  `trust_env=false`. The macOS system proxy was not used.
- Structured Output, Typed Claims, and deterministic rendering passed Mock
  validation. No real Provider output for `000403.SZ` has been observed.
- Historical Candidates, Scopes, IDs, ledgers, receipts, manifests, reports,
  provenance, rejected evidence, and Mock evidence remain preserved and must
  not be rewritten or reused.

The verification infrastructure has reached diminishing returns. Future work
must improve the probability of obtaining or evaluating a real research result,
not add network evidence or make historical diagnosis more detailed.

## 3. Evaluation Criteria

The options are ranked only by:

1. Probability of obtaining the first real AI research result for `000403.SZ`.
2. Engineering time.
3. Provider cost.
4. System complexity.
5. Maintainability.
6. Speed of progress toward Paper Trading and ChenQuant workflows.

Audit depth and infrastructure sophistication provide no business-value bonus.

## 4. Option Comparison

| Dimension | A. One bounded Ark retry path | B. Simpler supported Provider path | C. Pause real Provider integration |
|---|---|---|---|
| First real result probability | Medium to high for one more attempt | Medium, potentially high after Provider selection | Zero while paused |
| Engineering time | Low, two controlled rounds | Medium to high, three to four rounds | Lowest, no Provider work |
| Provider cost | One bounded Ark request | New account/model pricing and one bounded request | Zero real LLM cost |
| System complexity | Low; reuse Minimal Path | Medium; new Provider adapter and approval identity | Lowest |
| Maintainability | Good if the change remains one variable | Acceptable only with a direct supported path | Best for deterministic workflow code |
| Paper Trading / ChenQuant speed | Short delay for one final validation | Largest delay | Fastest |

## 5. Option A: Continue the Ark Minimal Path Once

### Scope

- Change exactly one runtime variable: increase the read timeout from `180s`
  to a separately approved business-validation value. Keep connect timeout,
  write timeout, model, endpoint, Prompt, Facts, schema, retry, Secret handling,
  Claims validation, and renderer unchanged.
- Create a new one-shot Candidate and Scope. Historical IDs and Scopes remain
  non-reusable.
- Execute at most one real request for `000403.SZ`, with `retry=0`,
  `can_publish=false`, and manual review still required.
- Do not add probes, receipts, proxy layers, stage instrumentation, or timeout
  forensics.

### Benefits

- Preserves all currently validated business logic and changes the least code.
- TLS connectivity is already verified, so this route has the shortest path to
  observing a real strict-schema response.
- Produces a direct decision point: either validate the first real research
  result or stop Ark Provider work.

### Costs And Risks

- The historical timeout stage is not provable, so a longer read timeout may
  not change the outcome.
- One additional Ark request incurs bounded Provider cost and waiting time.
- A successful response still requires Claims validation and human quality
  review before it can be treated as research material.

### Stop Condition

If the single attempt does not produce a valid Candidate and Claims document,
preserve its terminal evidence and move directly to Option C. Do not open a new
network investigation and do not authorize another Ark attempt.

## 6. Option B: Use A Simpler Supported Provider Path

### Scope

- Select a Provider and exact model only after its official documentation
  confirms strict JSON Schema output for the chosen endpoint.
- Use a direct execution path. Do not import or reproduce the legacy Ark
  Proxy/Relay proof chain.
- Retain only the necessary controls: exact model, strict schema, `retry=0`,
  one-shot identity, safe Secret handling, existing Facts, Typed Claims
  validation, deterministic rendering, `can_publish=false`, and manual review.

### Benefits

- May provide a more predictable structured-output contract and faster path to
  a valid real response than Ark.
- Keeps the deterministic Facts, Claims, and renderer investment reusable.

### Costs And Risks

- Provider selection, account readiness, pricing, adapter behavior, and exact
  model capability must all be validated before a request.
- Requires a new adapter, Mock contract, Candidate, Scope, and approval cycle.
- Risks recreating integration work before the current Ark path has received
  its one bounded business-oriented retry.

### Stop Condition

Reject any Provider that needs free-text parsing, `json_object` fallback,
ChatCompletions fallback, automatic retries, or a new proxy/relay subsystem.

## 7. Option C: Pause Real Provider Integration

### Scope

- Keep current Mock responses and deterministic fixtures as the AI boundary.
- Continue later with the research workflow and Paper Trading design using
  deterministic Facts and validated Claims fixtures.
- Treat real Provider integration as a separate future milestone with its own
  budget and success criteria.

### Benefits

- Immediately stops Provider engineering cost and preserves the simplest
  system.
- Maximizes progress toward Paper Trading and ChenQuant workflow capabilities.
- Keeps tests deterministic and maintenance burden low.

### Costs And Risks

- Cannot produce the first real AI research result while paused.
- Real latency, schema adherence, output quality, and factual behavior remain
  unvalidated.
- Mock success must never be represented as real Provider validation.

## 8. Recommendation

```text
Recommended option: A
Estimated next implementation rounds: 2
```

Option A provides the best business-value ratio because it has the shortest
bounded path to the primary goal: obtaining and evaluating the first real AI
research result for `000403.SZ`. The existing Minimal Path already preserves
the required Facts, strict schema, Claims validation, renderer, and Secret
safety controls. One controlled timeout change avoids another infrastructure
cycle.

The recommendation is deliberately limited to two rounds:

1. Offline round: approve one timeout value and generate a new immutable
   Candidate and one-shot Scope without adding infrastructure.
2. Execution round: after explicit approval, perform one real request and
   validate its output through the existing Claims and renderer path.

This is not approval to execute either round. If that one request fails to
produce valid Claims, the default next decision is Option C, not additional
Ark debugging. Option B remains a later alternative only when a specific
Provider and exact model have official strict-schema support and lower expected
integration cost.

## 9. Frozen Boundaries

This decision document does not authorize:

- Any real Provider request or AI call.
- A new Candidate, Scope, Probe, timeout, model, Prompt, or reasoning change.
- Paper Trading implementation, a three-symbol batch, production Obsidian
  output, cloud deployment, Telegram, OpenClaw, or Integrated Gold.
- Deletion, rewriting, reclassification, or reuse of historical evidence.

```text
Engineering stop: ENFORCED
TLS/network layer: CLOSED
Timeout forensics: CLOSED
Legacy Proxy/Relay: FROZEN
Historical evidence: UNCHANGED
Next action: USER_SELECTS_BUSINESS_VALIDATION_PATH
```
