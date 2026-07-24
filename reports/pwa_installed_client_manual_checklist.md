# TickFlow Installed PWA Manual Verification

Audit date: 2026-07-24 (Asia/Shanghai)

Automated server checks cannot prove the state of an already installed Mac,
iPhone, or Android PWA. The server-side PWA asset fix is deployed; complete this
checklist against each installed client.

## Automated Server Evidence

- Deployed revision: `494ee71481f1487c1046e1030381c1dfab03d707`.
- The Tailscale `:8443` route and loopback `:3019` returned hashes matching the
  deployed container for the main JavaScript/CSS bundles, `index.html`, the
  manifest, `sw.js`, `registerSW.js`, the Workbox runtime, and both PWA icons.
- `registerSW.js` and `workbox-9c191d2f.js` returned JavaScript content types.
- A nonexistent `workbox-deadbeef.js` returned `404`, not SPA HTML.
- The fresh automated verifier returned `CLOUD_P0_RUNTIME_OK`.

## Preconditions

- Device is connected to the approved Tailscale tailnet.
- URL is `https://vm-0-9-ubuntu.tail21c236.ts.net:8443/`.
- Ports 3018 and 3019 remain inaccessible directly from the public internet.
- No password, cookie, or API key is pasted into a report or chat.

## iPhone or iPad

- [ ] Completely close the installed TickFlow PWA.
- [ ] Open the Tailscale app and confirm the tailnet connection.
- [ ] Open the 8443 URL in Safari and reload once.
- [ ] Confirm the page loads without a JavaScript MIME-type error for `registerSW.js` or `workbox-*.js`.
- [ ] In Safari website data, confirm TickFlow has a Service Worker and cache storage.
- [ ] Remove and re-add the Home Screen installation only if the existing client remains pinned to an old shell.
- [ ] Launch from the Home Screen and confirm standalone display.
- [ ] Confirm Dashboard, Watchlist, and Stock Analysis navigation works.
- [ ] Disable network temporarily and confirm only previously cached read-only shell/snapshots remain available.
- [ ] Confirm offline state does not claim data is live or current.
- [ ] Restore network and confirm a reload returns current server content.

## Mac PWA

- [ ] Fully quit the installed web app.
- [ ] Open the 8443 URL in Safari or Chrome while connected to Tailscale.
- [ ] Confirm `/registerSW.js` is JavaScript, not HTML.
- [ ] Confirm the `workbox-*.js` requested by `/sw.js` returns JavaScript, not HTML.
- [ ] Confirm manifest icons load successfully.
- [ ] Open developer tools and verify the active Service Worker scope is `/`.
- [ ] Use “Update” or unregister/reload only if the old worker remains active.
- [ ] Reopen the installed app and confirm navigation and private HTTPS access.

## Evidence to Record

Record only:

- device model and OS version;
- browser/PWA version;
- verification time;
- Service Worker status;
- visible build revision when the product later exposes one;
- pass/fail results.

Do not record:

- passwords;
- cookies;
- API keys;
- full AI review content;
- private Tailscale authentication material.

## Current Boundary

```text
PWA_SERVER_UPDATED
PWA_INSTALLED_CLIENT_USER_CHECK_REQUIRED
```

This checklist does not claim that a real installed client has already refreshed its cached application shell.
