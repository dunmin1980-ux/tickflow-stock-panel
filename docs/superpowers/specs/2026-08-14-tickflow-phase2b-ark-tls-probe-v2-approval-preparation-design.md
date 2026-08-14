# TickFlow Phase 2B Ark TLS Probe v2 Approval Preparation Design

## Status and objective

The receipt transport channel is already verified at commit
`238817ca6851084008d0d61fbc69b134cc870c90`. This phase prepares a new,
independent Ark TLS Probe candidate and approval scope. It is offline-only and
must stop at `TLS_PROBE_READY_FOR_FINAL_EXECUTION_APPROVAL`.

The historical probe `5dd641ca9e4b4ccba1035fff4d695409` remains permanently
`PHASE2B_ARK_TLS_PROBE_UNKNOWN / PROCESS_ERROR / CONSUMED / PRESERVED` with
`retry_count=0`. No historical DNS, TCP, TLS, certificate, or hostname result is
backfilled.

## Isolation and output layout

The v2 preparation logic is a dedicated offline builder. It cannot call the
existing live probe entrypoint and cannot read Keychain data. It writes only to:

`reports/phase2_provider_ark/tls_probe_v2/`

The directory contains:

- `approval_candidate.json`;
- `approval_scope.json`;
- `build_provenance.json`;
- `offline_preflight.json`;
- `verification.json`;
- `approval_preparation_eval.md`.

The scope type is `ark_tls_connectivity_probe_v2`. Its identity is derived from
the canonical candidate SHA-256 and the fixed target identity. It is independent
from Ark Provider, AI Canary, and OpenAI scopes. It starts with
`historical_attempts=0`, `attempt_availability=AVAILABLE`, and no attempt ledger.

## Immutable source and contract bindings

Source-controlled contract files live under:

`docker/phase2-ark-tls-connectivity-probe/contracts/`

They define:

- the child receipt JSON schema;
- the ready marker JSON schema;
- the path-validation contract, including the single trusted macOS
  `/var -> /private/var` canonical alias;
- the receipt transport contract;
- the TLS contract and stage/error taxonomy.

The candidate binds the SHA-256 and repository path for every contract and
source. At minimum it binds:

- child Probe source;
- host one-shot orchestrator;
- local receipt harness;
- receipt fixture;
- child receipt schema;
- ready marker schema;
- path-validation contract;
- receipt transport contract;
- TLS contract;
- Ark Proxy Dockerfile and source;
- immutable container image ID and base-image digest;
- build provenance;
- frozen seven-file historical Probe baseline;
- receipt transport verification;
- local harness evidence.

No candidate is valid when an expected binding is missing, duplicated, points
outside the repository, is a symlink, or differs from the current file bytes.

## Container and build provenance

The future Probe continues to use the approved immutable Ark egress Proxy image
as a restricted execution base. The Probe source remains a read-only single-file
mount and does not become part of that image. Build provenance therefore binds
both identities:

1. the existing image build identity, labels, Dockerfile, image-contained Proxy
   source, base image digest, architecture, and OS;
2. the exact source-controlled Probe file mounted at execution time.

The builder uses read-only local Docker image inspection. It may run one
`--network none` container only to hash `/etc/ssl/certs/ca-certificates.crt` and
confirm that it is a regular non-empty file. It never creates a public-capable
network and never resolves Ark.

## CA and TLS contract

The candidate fixes:

- `CA_source=/etc/ssl/certs/ca-certificates.crt`;
- `target_host=ark.cn-beijing.volces.com`;
- `target_port=443`;
- `sni_hostname=ark.cn-beijing.volces.com`;
- `hostname_verification_target=ark.cn-beijing.volces.com`;
- `tls_minimum=TLSv1_2`;
- `tls_maximum=MAXIMUM_SUPPORTED`;
- `verify_mode=CERT_REQUIRED`;
- `check_hostname=true`;
- `retry_count=0`;
- `maximum_probe_attempts=1`.

The future approved Probe can perform only DNS, one TCP connect, one verified
TLS handshake, certificate verification, hostname verification, and a clean TLS
close. HTTP methods, paths, headers, bodies, Authorization, Secrets, Provider
requests, and AI calls are excluded by schema and source scans.

## Receipt transport contract

The fixed output contract is:

- child receipt `/output/child-receipt.json`;
- marker `/output/receipt.ready`;
- one dedicated empty writable directory bind at `/output`;
- container user `65532:65532`;
- output directory mode `0700`;
- receipt and marker mode `0600`.

The child publication sequence is temp write, flush, file fsync, same-directory
atomic rename, parent-directory fsync, marker publication with the same durable
sequence, then exit zero. Consequently:

`exitCode=0 => receipt durable => marker durable => Host visible => Host validation PASSED`

The Host resolves the trusted system temporary root first, permits only the
verified macOS `/var -> /private/var` alias, then rejects all symlinks, escapes,
unexpected parents, non-regular files, wrong owners/modes, and unexpected output
files inside the controlled directory. Archive completion must precede cleanup.

## Stage and failure taxonomy

The future stage receipt contains these ordered events:

1. `probe_started`;
2. `dns_started`;
3. `dns_completed`;
4. `tcp_connect_started`;
5. `tcp_connect_completed`;
6. `tls_handshake_started`;
7. `tls_handshake_completed`;
8. `certificate_verified`;
9. `hostname_verified`;
10. `probe_closed`;
11. `cleanup_completed`.

The exact failure categories are:

- `DNS_RESOLUTION_FAILED`;
- `TCP_CONNECT_FAILED`;
- `TCP_CONNECT_TIMEOUT`;
- `TLS_HANDSHAKE_TIMEOUT`;
- `TLS_CERT_VERIFY_FAILED`;
- `TLS_HOSTNAME_VERIFY_FAILED`;
- `TLS_PROTOCOL_FAILED`;
- `TLS_CONNECTION_RESET`;
- `TLS_EOF`;
- `TLS_OTHER_SSL_ERROR`;
- `PROCESS_ERROR`.

No generic `TLS_FAILED` fallback is valid.

## Offline verification and fail-closed baseline

The builder first validates the historical seven-file SHA baseline and the
receipt transport evidence. It runs the local A-I harness with every container
on `--network none`, including three independent happy paths and all fail-closed
fault injections. It requires zero container, network, and temporary residue.

The pre-change backend full-suite baseline is `2627 passed / 14 failed`. The 14
known failures are caused by preserved local
`93cf...` historical evidence, the `8e42...` orphan scope, and existing exact
Ark baseline guards. They are recorded as
`KNOWN_HISTORICAL_SCOPE_FAIL_CLOSED_COUNT=14`. New focused tests may increase
the passed count, but the final failure identities and count must remain exactly
the known 14. Any additional or missing failure, or any receipt-transport-related
failure, blocks preparation.

The final preflight also requires focused tests, compileall, Ruff F821,
`git diff --check`, sensitive/HTTP operation scans, historical byte equality,
source-binding verification, and an independent review with
`NO ACTIONABLE FINDINGS`.

## Git and completion contract

The preserved untracked historical evidence remains byte-identical and remains
present for the full-suite fail-closed check. Exact local Git exclude entries
may protect those known files from accidental staging; no broad ignore rule is
allowed. All v2 source, tests, contracts, and evidence are committed and pushed
to the fork branch. Completion requires local HEAD to equal the fetched fork
branch HEAD and normal `git status --porcelain` to be empty.

The only successful terminal state is:

`TLS_PROBE_READY_FOR_FINAL_EXECUTION_APPROVAL`

The next action is:

`REQUEST_FINAL_TLS_PROBE_SINGLE_ATTEMPT_APPROVAL`

This phase never installs an approval and never executes the real Ark TLS Probe.
