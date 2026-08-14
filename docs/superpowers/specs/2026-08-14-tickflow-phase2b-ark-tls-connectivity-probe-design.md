# TickFlow Phase 2B Ark TLS Connectivity Probe Design

## Status and objective

The historical Ark Canary request `93cf0ea84804444ebc9745fa34c4bb82`
remains `FAILED_AFTER_DISPATCH / CONSUMED / PRESERVED`. Its receipt proves a
TLS-layer failure but intentionally lacks enough detail to identify the exact
SSL subtype.

This change creates and runs one independent TLS connectivity probe. The probe
is not a Provider attempt or AI call, does not read a Secret, does not construct
Authorization, and does not send HTTP or application data. It validates only
DNS, one TCP connection, one verified TLS handshake, certificate and hostname
verification, and a TLS close to `ark.cn-beijing.volces.com:443`.

## Architecture

The host one-shot runner owns a probe-only ledger, creates an ephemeral Docker
bridge network and restricted container, waits for one child receipt, removes
the container and network, and atomically publishes a final receipt after
cleanup. The ledger identity is independent from every Canary Approval Scope.
Once network dispatch starts, that probe ID is permanently consumed and cannot
be retried.

The child is the already approved immutable Ark Proxy image
`sha256:a8a01a003f7afc385b4e24fd2960bcdfd3eaac40c394f39aa6249fd7b2f0f79b`
with its entrypoint replaced by one source-controlled, read-only probe file.
The container mounts only that file and a dedicated temporary output directory.
It receives no Secret, request, repository, home directory, Docker socket, or
Vault mount.

Container constraints are fixed:

- user `65532:65532`;
- read-only root filesystem;
- all capabilities dropped;
- `no-new-privileges` enabled;
- restart disabled;
- no host ports and no Docker control mount;
- one fixed hostname and port in source, with no CLI or environment override.

## TLS contract

The child uses Python `ssl` and `socket` with the same security contract as the
current Ark Proxy:

- CA file `/etc/ssl/certs/ca-certificates.crt`;
- minimum TLS `TLSv1_2`;
- maximum TLS `MAXIMUM_SUPPORTED`;
- `verify_mode=ssl.CERT_REQUIRED`;
- `check_hostname=True`;
- SNI and hostname target `ark.cn-beijing.volces.com`.

`socket.getaddrinfo()` is called once. The first returned stream address is used
for one TCP connect; alternate addresses are not tried. After a successful TLS
handshake, the child records bounded certificate metadata, sends no application
bytes, performs `SSLSocket.unwrap()` to send TLS close notification, and closes
the raw socket.

The production policy has a 10-second TCP timeout and 180-second TLS handshake
deadline, matching the current Ark Proxy connect and total timeout boundaries.
There is no retry and no environment override.

## Receipt and error model

The child writes one canonical JSON receipt using a same-directory temporary
file, `fsync`, atomic rename, and directory `fsync`. The host validates the
receipt, adds the host-only `cleanup_completed` event after residue checks, and
publishes the final receipt atomically under:

`reports/phase2_provider_ark/tls_connectivity_probe/<probe_id>/receipt.json`

Every required stage contains `occurred`, `wall_time`, `monotonic_ns`, and one
bounded non-sensitive category. The final receipt also records TLS version,
cipher name, certificate dates, and whether SAN contains the fixed hostname.
It never records certificate bytes, IP addresses, exception traces, headers,
Secrets, prompts, Facts, projections, or claims.

The fail-closed categories are:

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

Certificate errors may include integer `verify_code` and a sanitized,
bounded `verify_message`. No stack trace is persisted.

## One-shot ledger and historical isolation

The host creates a probe-only execution ledger before starting Docker. It stores
the generated probe ID, source hash, fixed image ID, target identity, attempt
count, retry count, dispatch timestamp, and terminal state. The ledger is never
stored beneath the Canary attempts namespace.

Before dispatch, the runner verifies the frozen historical Canary files and the
current local Approval hashes. The runner refuses if any historical byte differs
or if a probe execution ledger already exists. The implementation and live
probe never edit or delete historical ledgers, receipts, rejected evidence,
runtime evidence, Candidate, Approval, provenance, or artifact generation.

## Test and execution gates

Tests first exercise the classifier and receipt contract. A Docker internal-only
mock harness then runs trusted CA, unknown CA, hostname mismatch, connection
refused, missing DNS alias, and handshake timeout scenarios against the fixed
image. Test-only policies are passed by a separate mock entrypoint; the
production entrypoint accepts no policy input.

Before the only public probe, focused tests, compileall, Ruff F821,
`git diff --check`, secret-shape scanning, and an independent focused review
must pass with no actionable findings. The public probe runs once. Whether it
passes or fails, retry remains zero, all runtime residue is removed, and the
historical SHA-256 baseline is rechecked byte-for-byte.

## Terminal states

Success requires all DNS, TCP, TLS, certificate, hostname, close, and cleanup
events. It yields `PHASE2B_ARK_TLS_CONNECTIVITY_PROBE_PASSED` and only proves
`CURRENT_TLS_CONNECTIVITY_VERIFIED`; it does not rewrite the historical cause.

Failures map to the approved DNS, TCP, certificate, hostname, handshake, or
unknown terminal state. The next action after success is
`DESIGN_NEW_ARK_CANARY_SCOPE`; after failure it is
`DIAGNOSE_TLS_CONNECTIVITY_ROOT_CAUSE`.
