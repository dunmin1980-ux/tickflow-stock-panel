# TickFlow Authentication and Data-Permission Readiness

Audit date: 2026-07-24 (Asia/Shanghai)

Status:

```text
AUTH_USER_ACTION_REQUIRED
```

No password, cookie, API key, secret value, or complete container environment was read or written during this audit.

## 1. Current Cloud State

| Check | Result |
|---|---|
| Authentication | `configured=false`, `authenticated=false` |
| Container user | Docker `.Config.User` is empty, therefore the image runs as root |
| `AUTH_COOKIE_SECURE` | `true` |
| Deployment data directory | owner `0:0`, mode `0755` |
| `data/user_data` | owner `0:0`, mode `0755` |
| `preferences.json` | owner `0:0`, mode `0600` |
| `auth.json` | absent because authentication is not initialized |
| `secrets.json` | absent because real keys are not initialized |
| Network boundary | Tailscale-only HTTPS `:8443`; application port `3019` is loopback-only |

The root-owned data layout is consistent with the current root container, but it creates host-side maintenance friction and is not a least-privilege deployment.

## 2. Password and Session Storage

- Passwords are not stored in plaintext. `backend/app/services/auth.py:70-86` uses PBKDF2-HMAC-SHA256 with a random salt and constant-time comparison.
- `auth.json` is explicitly changed to mode `0600` by `backend/app/services/auth.py:61-67`.
- Browser login uses an HttpOnly, SameSite=Lax cookie; the cloud Compose configuration forces Secure cookies. See `backend/app/api/auth.py:170-194`.
- Residual P2 risk: valid random session tokens are stored verbatim in `auth.json` for up to 30 days and restored after restart. Theft of that file would permit session replay until expiry. See `backend/app/services/auth.py:32-37`, `152-164`, and `189-207`.

## 3. Secret Storage and API Projection

- TickFlow and AI keys are stored through `backend/app/secrets_store.py`; writes explicitly apply mode `0600`.
- `/api/settings` returns masked values and boolean configuration flags rather than complete keys.
- `/api/settings/preferences` uses a safe projection, and workspace sync uses client-safe preferences.
- Existing canary tests cover workspace/API secret projection.

No direct plaintext-secret API response was found.

## 4. Notification Credential Classification

P1 readiness concern:

- Feishu and WeCom webhook URLs/secrets are persisted in `preferences.json`.
- The module header describes preferences as non-sensitive, even though credential fields are present.
- Atomic writes currently produce mode `0600` on POSIX, and the cloud file is mode `0600`, but the code does not explicitly reassert that permission after every preferences write.
- Backups and workspace migration procedures must therefore continue treating `preferences.json` as sensitive material even though client APIs redact it.

Relevant implementation:

- `backend/app/services/preferences.py:1-5`
- `backend/app/services/preferences.py:111-132`
- `backend/app/services/preferences.py:997-1036`
- `backend/app/services/preferences.py:1039-1079`
- `backend/app/services/preferences.py:1093-1137`

This audit records the issue only. Changing the credential model is outside the P0 governance scope.

## 5. Container Identity

P2 readiness concern:

- The runtime Dockerfile does not set `USER`, so the application runs as root.
- `./data` is mounted at `/app/data`, producing root-owned host files.
- The host Codex directory is mounted read-only at `/root/.codex`.

Relevant implementation:

- `Dockerfile:161-173`
- `docker-compose.yml:20-37`

A non-root migration requires ownership planning and a separate rollback-tested change. It must not be combined with authentication initialization.

## 6. Safe Initialization Preconditions

Before adding TickFlow or AI keys:

1. Connect through the existing Tailscale HTTPS `:8443` endpoint.
2. Initialize the access password in the UI without sending it through chat or committing it to a file.
3. Confirm `/api/auth/status` changes to `configured=true`.
4. Confirm the created `auth.json` is mode `0600`.
5. Log out and back in once to verify Secure-cookie behavior over Tailscale HTTPS.
6. Configure TickFlow Pro and AI only through the authenticated UI or a private local terminal session.
7. Confirm any created `secrets.json` is mode `0600`.
8. Keep `preferences.json`, `auth.json`, and `secrets.json` out of Git and treat all three as sensitive backup content.

Authentication is not ready for unattended key initialization until the user privately completes steps 1-5.

## 7. Decision

```text
authentication_code_readiness=CONDITIONAL
cloud_authentication_initialized=false
real_keys_written_this_run=false
plaintext_secret_api_leak_found=false
user_action_required=true
```

The current state is acceptable for a private, uninitialized service under Tailscale, but not for loading real API credentials yet.
