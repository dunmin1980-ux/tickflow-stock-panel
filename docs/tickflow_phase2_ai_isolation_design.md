# TickFlow Phase 2 AI Isolation Design

## Status

```text
ISOLATION_DESIGN_READY
ISOLATION_RUNTIME_NOT_YET_VERIFIED
```

This phase validates a typed adapter contract with a deterministic fake worker.
It does not execute a model, configure an AI key, or prove OS-level process
isolation.

## Boundary

```text
validated Phase 2A Facts
-> host-built minimal projection
-> isolated worker input file
-> JSON-only typed Claims candidate
-> host-side schema and semantic validation
-> host-owned deterministic Markdown renderer
```

The worker never owns publishable Markdown. Only the host renderer can produce
preview content, and only a fully valid candidate may enter the typed inbox.

## Projection Contract

The projection includes only:

- fixed symbol, name, trade date, and timezone;
- the bound Facts SHA-256 and projection SHA-256;
- approved daily open/close and contract state;
- approved indicator values;
- approved 1m and 30m quality fields;
- approved adjustment, scope, freshness, and vendor-pending fields;
- the closed predicate allowlist.

It excludes:

- repository, Home, source-file, Vault, and cloud paths;
- `source_evidence`, `numeric_provenance`, key levels, and unrelated Facts;
- authentication data, API keys, cookies, sessions, and authorization headers;
- raw provider responses and arbitrary prose.

The reader accepts one exact path. It rejects alternate paths and every
symlink component before opening the file, then uses a no-follow descriptor,
`fstat`, a one-megabyte size ceiling, an inode recheck, strict JSON parsing,
and projection hash validation.

## Candidate Contract

The candidate must be one JSON object matching the closed Pydantic schema.
Host validation replays every pointer and calculation against the projection,
checks symbol/date/hash binding, enforces units and price basis, rejects
raw/qfq mixing, and accepts no freeform keys or forbidden trading claim type.
The fake worker is deterministic and reports zero AI calls, provider attempts,
TickFlow requests, or external sends.

## Required Runtime For A Real Batch

A future real model batch requires an external container or equivalent OS
sandbox with all of these controls:

- network disabled by default, with an explicit single-provider egress policy
  only when approved;
- one read-only projection file mount and one disposable output mount;
- no Home, repository, Obsidian Vault, SSH, application config, secret store,
  Docker socket, or host device mount;
- a short-lived provider secret injected only into the worker process;
- logs scrubbed before persistence and never containing prompts with secrets;
- strict process, memory, output-size, and wall-clock limits;
- deletion of projection, candidate, temporary directory, and secret material
  after host validation;
- host-side revalidation before deterministic rendering.

## Limitation

Python path guards and a fake worker verify the application protocol, not the
security boundary of a future model process. On the current macOS host they do
not prove that a hostile process cannot inspect another path, environment, or
process. Therefore runtime isolation remains explicitly unverified.
