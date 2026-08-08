# Phase 2B Single-Symbol Canary Manual Review

```yaml
request_id: d59766101b63450e8148541d589a90bf
symbol: 000403.SZ
name: 派林生物
trade_date: 2026-07-31
provider: openai
exact_model: gpt-5.6-terra
machine_status: ATTEMPT_CONSUMED_UNKNOWN
manual_review: PENDING
review_eligibility: BLOCKED_NO_CANDIDATE
candidate_available: false
rendered_markdown_available: false
can_publish: false
three_symbol_batch: NOT_APPROVED
```

## Review Gate

- [ ] Do not mark `manual_review=PASSED`: no Provider response or Candidate was recovered.
- [ ] Confirm the durable ledger remains `ATTEMPT_CONSUMED_UNKNOWN`.
- [ ] Confirm `provider_attempt_count=1` and `retry_count=0`.
- [ ] Confirm the current authorization is treated as permanently consumed.
- [ ] Confirm no second Provider request was issued.

## Candidate Review

The following checks cannot be completed because no Candidate or rendered
Markdown was produced:

- [ ] Symbol and name
- [ ] Trade date
- [ ] Claim count
- [ ] Facts SHA and Projection SHA bindings
- [ ] Facts Pointer bindings
- [ ] Raw/QFQ basis and units
- [ ] Claim contents
- [ ] Deterministic rendered Markdown
- [ ] No trading advice or prediction
- [ ] No financial or news fabrication

## Current Disposition

```text
route=REJECTED
can_publish=false
next_action=DIAGNOSE_BLOCKER
```

This checklist is an audit record only. It does not authorize another request.
