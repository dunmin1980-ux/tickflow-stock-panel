# TickFlow Phase 2B Production Proxy Path TLS-only Probe Design

## Status and Goal

The historical Ark TLS Probe passed, while the historical Ark Canary failed after dispatch with only the coarse `TLS_FAILED` category. The historical root cause remains `NOT_PROVABLE` and is outside this design's scope.

This design prepares one separately approved `PRODUCTION_PROXY_PATH_TLS_ONLY_PROBE`. It will test the current production Proxy TLS path through DNS, TCP, TLS handshake, certificate verification, hostname verification, and a clean TLS close. It must never read an Ark Secret, construct Authorization, write HTTP bytes, call `/api/v3/responses`, consume a Provider attempt, or count as an AI call.

The offline preparation target is:

```text
PROXY_PATH_TLS_PROBE_READY_FOR_FINAL_EXECUTION_APPROVAL
```

No real Ark network operation is permitted during preparation.

## Immutable Identity

The Probe Candidate binds one fixed Git Head and all execution-relevant bytes. If the Head changes after Candidate generation, the Candidate and Scope are invalid and must be regenerated.

The immutable identity includes:

- the rebuilt Proxy image ID and fixed base-image digest;
- Proxy Dockerfile, Proxy source, TLS classification source, runtime contract, readiness contract, and CA bundle identity;
- the production host launcher, container Probe runner, offline builder, Mock harness, and all network contracts;
- the current Phase 2 Canary Orchestrator source SHA-256;
- frozen historical Probe, differential diagnosis, local parity, and backend failure-baseline evidence;
- target host, target port, SNI, hostname verification target, and TLS policy;
- retry count zero and one maximum Probe attempt.

The image is rebuilt with Docker `--network=none --pull=false --no-cache` from a read-only copied context. The local base image must already exist and match the fixed digest. Build provenance records the exact command contract, image labels, context hashes, CA bytes, and zero external activity.

## Execution Components

### Container Probe Runner

`docker/phase2-ark-proxy-tls-probe/probe.py` imports `/proxy/proxy.py` from the rebuilt production image and calls its `create_tls_context()` and `connect_verified_tls()` functions. It performs a separate DNS resolution for stage evidence, then opens the verified TLS connection, records negotiated TLS metadata, closes it, and atomically publishes a bounded receipt.

The runner never calls `read_auth_file()` or `perform_provider_request()`. It has no Secret mount and no request, projection, Prompt, Facts, Claims, or output-generation inputs.

### Host Launcher

`backend/scripts/run_phase2_ark_proxy_tls_probe.py` owns the one-shot lifecycle. A future live run requires an exact local approval file matching the Candidate and Scope. Before any network dispatch it acquires an exclusive lock, verifies the approval, confirms the Scope has no historical attempts, creates a durable ledger, builds the two-network topology, and verifies the Proxy container's attachments.

The launcher creates:

1. an internal Relay-side bridge;
2. a separate non-internal egress bridge;
3. one Proxy container initially attached to the internal bridge and then attached to egress.

Only the Proxy container has egress attachment. The runner fixes the destination to `ark.cn-beijing.volces.com:443`. A network or attachment mismatch fails before the container starts. The ledger consumes the Probe Scope only when `NETWORK_DISPATCH_STARTED` is durably recorded. Retry is always zero.

### Offline Builder

`backend/scripts/build_phase2_ark_proxy_tls_probe_offline.py` has three explicit modes:

- `--build-image`: rebuild and inspect the immutable Proxy image and publish build provenance;
- `--prepare`: validate Mock evidence and frozen baselines, then publish Candidate, Scope, and offline preflight artifacts;
- `--verify`: recompute all identities and fail on any mismatch.

Artifact publication uses staging, file fsync, atomic rename, and directory fsync. The Scope is separate from Provider, AI Canary, generic TLS Probe, and OpenAI scopes.

## Network Contracts

Source-controlled contracts define:

- the internal Relay network as a Docker internal bridge;
- the production egress network as a distinct non-internal bridge;
- exact Proxy attachment order and the requirement for exactly two named networks;
- Proxy-only egress ownership, fixed target host and port, and no host networking;
- Docker embedded DNS with no custom `--dns`, proxy environment, or host override;
- the TLS-only operation set and forbidden HTTP, Secret, Provider, and AI behavior.

The local Mock harness preserves the two-network attachment topology but makes the test egress bridge internal. The contract explicitly records this safety substitution. The Mock Provider is attached only to test egress and is addressed by the production hostname as a Docker network alias. This validates production attachment semantics while proving zero public routing during tests.

## Production-topology Mock Harness

The harness uses the rebuilt Proxy image, the production Probe runner, an internal Relay network, and an internal test-egress network. It inspects the Proxy container before start and fails if either attachment is missing or unexpected.

It covers:

1. trusted CA and hostname, repeated three times;
2. unknown CA;
3. hostname mismatch;
4. connection reset;
5. handshake timeout;
6. TLS protocol incompatibility;
7. connection refused with successful DNS;
8. valid Relay/internal plus egress attachment;
9. missing egress attachment;
10. wrong egress attachment.

All successful TLS cases must use `CERT_REQUIRED`, `check_hostname=true`, TLS 1.2 minimum, the production SNI, and the production hostname verification target. Every case removes its containers and networks in a `finally` path.

## Failure and Receipt Contracts

The receipt preserves the precise categories already added to the Proxy:

```text
TLS_CERT_VERIFY_FAILED
TLS_HOSTNAME_VERIFY_FAILED
TLS_PROTOCOL_FAILED
TLS_HANDSHAKE_TIMEOUT
TLS_CONNECTION_RESET
TLS_EOF
TLS_OTHER_SSL_ERROR
PROVIDER_CONNECT_FAILED
```

Sanitized `exception_class`, `verify_code`, `verify_message`, and `errno` are allowed. Secrets, Authorization, business request data, complete stack traces, and environment dumps are forbidden. Historical `d57b276fe9014d35bdae5a92950ca17b` evidence is never rewritten or reclassified.

## Verification and Stop Condition

Preparation succeeds only when:

- the new image and Build Provenance verify;
- the source-controlled network contracts and double-network topology verify;
- all production-topology Mock cases and three Happy Paths pass;
- Candidate and Scope recompute byte-for-byte;
- Scope historical attempts are zero and availability is `AVAILABLE`;
- focused TLS, Candidate, Scope, provenance, compatibility, compileall, Ruff F821, and diff checks pass;
- the exact known backend failure set remains unchanged;
- all historical evidence hashes remain unchanged;
- an independent review returns `NO ACTIONABLE FINDINGS`;
- Provider attempts, AI calls, real Probe attempts, Secret reads, HTTP requests, and public-network successes remain zero.

At that point the next action is `REQUEST_FINAL_PROXY_PATH_TLS_PROBE_APPROVAL`. This design does not install approval or execute the real Probe.
