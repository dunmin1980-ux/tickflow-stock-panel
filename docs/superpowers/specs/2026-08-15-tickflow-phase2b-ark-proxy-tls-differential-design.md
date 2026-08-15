# TickFlow Phase 2B Ark Proxy TLS Differential Design

## Objective

Explain, without public network access or credentials, why the historical TLS-only probe passed while the historical Ark Proxy stopped after `provider_connect_started`. Preserve all historical evidence and add enough sanitized classification for future runs to avoid the generic `TLS_FAILED` bucket.

## Immutable Boundaries

- Historical probe `d73e91f766854ff7a64c0e725939eb64` remains passed, consumed, preserved, and non-reusable.
- Historical Canary `d57b276fe9014d35bdae5a92950ca17b` remains failed after dispatch, consumed, and non-reusable.
- No Ark DNS, TCP, TLS, HTTP, Provider, or AI request is permitted.
- No Secret content or Authorization value is read or constructed.
- No live Approval Scope is created.
- Model, endpoint, Prompt, reasoning, schema, Facts, Projection, Claims, Renderer, TLS verification, and the 180/195/210 timeout contract are unchanged.

## Evidence Model

The diagnostic report records SHA-256 baselines for both historical evidence sets before any implementation work. Final verification recomputes every hash and fails if any byte changes.

The Probe/Proxy matrix distinguishes `SAME`, `DIFFERENT`, `NOT_APPLICABLE`, and `UNKNOWN`. A value is marked `SAME` only when source, image metadata, or offline runtime metadata proves it.

## Production Diagnostic Change

Ark Proxy keeps verified TLS and `http.client.HTTPSConnection`. Its TLS connection boundary gains a classifier that maps sanitized Python exceptions to:

- `TLS_CERT_VERIFY_FAILED`
- `TLS_HOSTNAME_VERIFY_FAILED`
- `TLS_PROTOCOL_FAILED`
- `TLS_HANDSHAKE_TIMEOUT`
- `TLS_CONNECTION_RESET`
- `TLS_EOF`
- `TLS_OTHER_SSL_ERROR`

New Ark receipts use schema version 3 and persist the precise category plus bounded `exception_class`, `verify_code`, `verify_message`, and `errno`. The orchestrator accepts the new exact field set only for Ark v3 while continuing to accept the historical Ark v2 field set byte-for-byte; OpenAI and Relay receipt contracts remain on v2. No historical receipt is rewritten or reclassified.

## Internal-only Parity Harness

A Docker harness creates only `--internal` bridge networks. It uses the approved Ark runtime image with the working-tree production Ark Proxy source mounted read-only against local endpoints for trusted CA, unknown CA, hostname mismatch, reset, handshake timeout, protocol mismatch, and refusal. A trusted case is also tested by the existing Probe implementation and the production Proxy TLS implementation against the same internal DNS alias and certificate.

The harness never mounts credentials, never constructs Authorization, and never sends an HTTP request. The internal TLS server records the SNI actually received. It records zero public network successes and fails closed unless every container, network, certificate, and staging file is removed.

## Success Semantics

- If the internal parity harness reproduces a deterministic Probe-pass/Proxy-fail difference, report the exact local root cause.
- If both implementations pass the same trusted internal target and the failure cannot be reconstructed, report `PHASE2B_ARK_PROXY_TLS_PATH_DIAGNOSIS_INCOMPLETE`; do not infer a historical root cause from the generic legacy receipt.
