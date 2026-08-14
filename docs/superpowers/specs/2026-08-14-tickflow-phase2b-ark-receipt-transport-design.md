# TickFlow Phase 2B Ark Receipt Transport Design

## Status and objective

The historical TLS Probe `5dd641ca9e4b4ccba1035fff4d695409`
remains `PHASE2B_ARK_TLS_PROBE_UNKNOWN / PROCESS_ERROR / CONSUMED /
PRESERVED`. This change repairs only the local evidence path:

`Probe container -> receipt bundle -> bind mount -> host validation -> durable archive`

No Ark DNS, TCP, TLS, HTTP, Provider, AI, Secret, or Authorization operation is
part of this work. A future public TLS Probe requires a new Candidate, Approval
identity, and explicit single-call authorization.

## Proven root cause

The historical container exited with code 0, which the child can do only after
`_atomic_write_json()` writes, flushes, fsyncs, renames, and fsyncs the parent
directory. The host then called `_read_json_regular()` on a path returned by
`tempfile.mkdtemp()` under `/var/folders/...`. On macOS, `/var` is a symlink to
`/private/var`; `_assert_no_symlink_components()` rejected that trusted system
alias as `historical_evidence_invalid` before opening the file. The exception
was swallowed and replaced with an empty `PROCESS_ERROR` receipt.

A local `--network none` experiment using the same image, user, mount, child
publication function, and host reader proved:

- the child wrote `/output/child-receipt.json` and exited 0;
- the host-visible file existed, was mode `0600`, and matched the archived hash;
- direct host reading worked;
- the current host reader failed only because `/tmp` and `/var` are macOS
  system symlinks;
- the existing Mock harness did not reproduce the fault because it used
  `Path.read_bytes()` rather than the production host reader.

## Fixed path and mount contract

The container paths are fixed:

- receipt: `/output/child-receipt.json`;
- readiness marker: `/output/receipt.ready`;
- probe ID: `/run/tickflow/probe-id`, mounted as one read-only file;
- source: one empty, one-shot host directory mounted read/write at `/output`.

The host creates the temporary root, immediately canonicalizes it with
`resolve(strict=True)`, and performs all later path validation and reads through
the canonical `/private/...` path. It still rejects any symlink created inside
the controlled temporary root. The mount remains a directory bind mount, so
the temporary file and final file share one filesystem and atomic rename is
valid.

The host output directory is mode `0700`. Docker Desktop presents that bind
mount inside the container as owned by the configured process user
`65532:65532`; a local no-network check proved it remains writable. The output
directory is empty before container start and contains only the receipt and
marker after publication. No other writable bind mount is allowed.

## Child publication contract

The child validates the receipt, then publishes a two-file bundle:

1. create a temporary receipt in `/output`;
2. write and flush all bytes;
3. fsync the receipt;
4. atomically rename to `child-receipt.json`;
5. fsync `/output`;
6. generate a marker containing the receipt SHA-256, probe ID, publisher
   UID/GID, directory UID/GID, directory mode, and `publication_complete=true`;
7. publish `receipt.ready` with the same write/fsync/rename/parent-fsync
   sequence;
8. exit 0 only after both durable publications succeed.

Publication failures are classified exactly as:

- `RECEIPT_OPEN_FAILED`;
- `RECEIPT_WRITE_FAILED`;
- `RECEIPT_FSYNC_FAILED`;
- `RECEIPT_RENAME_FAILED`;
- `RECEIPT_PARENT_FSYNC_FAILED`;
- `RECEIPT_VALIDATION_FAILED`.

The child emits only a bounded, non-sensitive transport category on stderr and
exits nonzero. It emits no traceback, path, Secret, request, or environment.

## Host lifecycle contract

The host sequence is fixed:

1. create and start the container;
2. wait and capture the actual container exit code;
3. inspect the canonical output directory;
4. require the exact whitelist `child-receipt.json` and `receipt.ready`;
5. reject symlinks and non-regular files;
6. validate the marker, receipt SHA-256, probe ID, UID/GID, and receipt schema;
7. durably archive the exact child receipt and transport evidence under a new
   probe directory;
8. record `receipt_archive_completed`;
9. record `cleanup_started` and only then remove container, network, and
   temporary directory;
10. derive the final receipt from the durable child archive and add the host
    cleanup event.

`receipt_archive_completed` must have a lower monotonic timestamp than
`cleanup_started`. Exit code 0 with a missing, malformed, unready, mismatched,
or unarchived receipt always fails closed.

## Local-only validation harness

The harness uses `--network none` and the immutable Proxy image. It mounts only
the production probe source, a test-only receipt fixture, and one dedicated
output directory. It runs these scenarios:

- happy path;
- permission denied;
- wrong destination;
- receipt temp write followed by rename failure;
- receipt written but process exits before readiness marker;
- malformed receipt;
- cleanup-before-archive fault injection;
- archive-before-cleanup success;
- three repeated independent happy-path runs.

No scenario resolves or contacts the Ark host. Every scenario records
`real_public_network_success_count=0`, Provider attempts 0, AI calls 0, Secret
reads 0, and zero container/network/temporary residue.

## Historical isolation

All seven pre-existing files under
`reports/phase2_provider_ark/tls_connectivity_probe/` are frozen by exact
SHA-256 before implementation. New evidence is written under
`reports/phase2_provider_ark/tls_receipt_transport/`. The historical Probe
receipt, ledger, preflight, postflight, closeout, baseline, and Mock evidence
are never edited, removed, or regenerated.

## Success state

`PHASE2B_ARK_TLS_PROBE_RECEIPT_CHANNEL_READY` requires:

- three consecutive local happy paths with container exit 0;
- durable receipt and readiness marker publication;
- host visibility and validation;
- archive before cleanup;
- every fault injection fail-closed with the expected category;
- exact historical hashes unchanged;
- zero public network, Provider, AI, Secret, container, network, and temporary
  residue;
- independent review with `NO ACTIONABLE FINDINGS`.
