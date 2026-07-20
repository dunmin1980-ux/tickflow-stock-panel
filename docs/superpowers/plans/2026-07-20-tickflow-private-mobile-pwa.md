# TickFlow Private Mobile PWA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有同源 Web 前端交付为通过 Tailscale 私网安装和使用的手机 PWA，并在断网时只读展示明确标时的安全缓存。

**Architecture:** Vite 生成 manifest 和只缓存静态壳的 Service Worker；允许离线查看的 API 快照由应用层写入 IndexedDB，写请求在离线状态下统一拒绝。云机使用独立 Tailscale HTTPS 8443 入口，保留现有 443 到 8787 的代理。

**Tech Stack:** React 18、Vite 5、vite-plugin-pwa、Workbox、IndexedDB/idb-keyval、TanStack Query、Vitest、Playwright、FastAPI cookie auth、Tailscale Serve 1.98.8。

## Global Constraints

- 手机首页为自选股，不增加营销页。
- 只缓存静态资源、自选股快照、个股分析报告、复盘素材、概念/行业最近结果、数据状态和回测摘要。
- 不缓存登录、Cookie、认证响应、密钥设置、Webhook、实时流、未脱敏错误或回测大明细。
- 离线只读，所有新增、编辑、删除、生成、同步和计算提交入口必须被拒绝。
- 不使用 Tailscale Funnel，不开放公网 3018/3019。
- 保留 `https://vm-0-9-ubuntu.tail21c236.ts.net/ -> 127.0.0.1:8787`。
- PWA 使用 `https://vm-0-9-ubuntu.tail21c236.ts.net:8443 -> 127.0.0.1:3019`。
- HTTPS Cookie 必须是 `Secure`、`HttpOnly`、`SameSite=Lax`。
- `GOLD_WORKSPACE_ENABLED=false`，不修改独立 Gold Shadow、n8n、Telegram 或 OpenClaw。

---

## File Map

- Create: `frontend/src/lib/connectivity.ts`, `offlinePolicy.ts`, `offlineDb.ts`
- Create: `frontend/src/components/ConnectionBanner.tsx`, `MobileNav.tsx`
- Create: `frontend/src/components/__tests__/ConnectionBanner.test.tsx`
- Create: `frontend/src/lib/__tests__/offlinePolicy.test.ts`, `offlineDb.test.ts`, `apiOffline.test.ts`
- Create: `frontend/e2e/mobile-pwa.spec.ts`, `frontend/playwright.config.ts`
- Create: `frontend/public/pwa-192.png`, `pwa-512.png`, `apple-touch-icon.png`
- Create: `backend/tests/test_auth_secure_cookie.py`
- Create: `scripts/verify_pwa_private_access.sh`, `docs/pwa-private-runbook.md`
- Modify: `frontend/package.json`, `pnpm-lock.yaml`, `vite.config.ts`, `src/lib/api.ts`, `src/main.tsx`, `src/router.tsx`, `src/components/Layout.tsx`, `src/index.css`
- Modify: `frontend/src/pages/{Watchlist,StockAnalysis,Review,ConceptAnalysis,IndustryAnalysis,Data}.tsx`
- Modify: `backend/app/config.py`, `backend/app/api/auth.py`, `backend/app/main.py`, `docker-compose.yml`

### Task 1: PWA 构建与测试基座

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/pnpm-lock.yaml`
- Modify: `frontend/vite.config.ts`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/public/pwa-192.png`
- Create: `frontend/public/pwa-512.png`
- Create: `frontend/public/apple-touch-icon.png`

**Interfaces:**
- Consumes: 现有 Vite 配置和 `frontend/public/favicon.svg`。
- Produces: `pnpm test`、`pnpm test:e2e`、manifest、Service Worker 和 PWA PNG。

- [ ] **Step 1: 安装依赖并增加脚本**

```bash
pnpm --dir frontend add idb-keyval
pnpm --dir frontend add -D vite-plugin-pwa vitest jsdom fake-indexeddb @testing-library/react @testing-library/jest-dom @playwright/test
```

在 `package.json` 增加：

```json
"test": "vitest run",
"test:watch": "vitest",
"test:e2e": "playwright test"
```

- [ ] **Step 2: 建立测试环境**

`frontend/src/test/setup.ts`：

```ts
import '@testing-library/jest-dom/vitest'
import 'fake-indexeddb/auto'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
afterEach(() => cleanup())
```

- [ ] **Step 3: 配置 PWA**

在 `vite.config.ts` 使用 `VitePWA`，固定以下配置。将 `defineConfig` 改为从 `vitest/config` 导入，使同一配置文件中的 `test` 字段有完整类型；`VitePWA` 从 `vite-plugin-pwa` 导入：

```ts
VitePWA({
  registerType: 'autoUpdate',
  includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
  manifest: {
    name: 'TickFlow 股票面板', short_name: 'TickFlow',
    description: 'A 股日线研究工作台', display: 'standalone',
    start_url: '/watchlist', scope: '/',
    theme_color: '#18181b', background_color: '#09090b',
    icons: [
      { src: '/pwa-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/pwa-512.png', sizes: '512x512', type: 'image/png' },
      { src: '/pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
  },
  workbox: {
    navigateFallback: '/index.html',
    globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
    runtimeCaching: [],
    cleanupOutdatedCaches: true,
  },
})
```

Vitest 固定使用 `jsdom` 和 `./src/test/setup.ts`。`runtimeCaching` 必须为空，Service Worker 不缓存 `/api`。

- [ ] **Step 4: 生成 Web 图标但不改桌面图标**

```bash
cd frontend
qlmanage -t -s 512 -o /tmp public/favicon.svg >/dev/null
sips -z 192 192 /tmp/favicon.svg.png --out public/pwa-192.png
sips -z 512 512 /tmp/favicon.svg.png --out public/pwa-512.png
sips -z 180 180 /tmp/favicon.svg.png --out public/apple-touch-icon.png
```

Expected: PNG 文件存在；`packaging/icon.ico`、`packaging/icon.icns` 哈希不变。

- [ ] **Step 5: 构建并提交**

```bash
pnpm --dir frontend build
test -f frontend/dist/manifest.webmanifest
test -f frontend/dist/sw.js
git add frontend/package.json frontend/pnpm-lock.yaml frontend/vite.config.ts frontend/src/test frontend/public
git commit -m "feat: add private mobile PWA build"
```

### Task 2: 安全离线快照策略

**Files:**
- Create: `frontend/src/lib/offlinePolicy.ts`
- Create: `frontend/src/lib/offlineDb.ts`
- Test: `frontend/src/lib/__tests__/offlinePolicy.test.ts`
- Test: `frontend/src/lib/__tests__/offlineDb.test.ts`

**Interfaces:**
- Produces: `isOfflineCacheAllowed(path)`, `isWriteMethod(method)`, `putSnapshot()`, `getSnapshot()`, `clearSnapshot()`。

- [ ] **Step 1: 写白名单失败测试**

```ts
import { describe, expect, it } from 'vitest'
import { isOfflineCacheAllowed, isWriteMethod } from '../offlinePolicy'

describe('offline policy', () => {
  it.each(['/api/watchlist', '/api/watchlist/enriched', '/api/kline/daily', '/api/stock-analysis/levels', '/api/stock-analysis/reports', '/api/market-recap/reports', '/api/data/status', '/api/overview/market', '/api/backtest/summaries'])('allows %s', p => expect(isOfflineCacheAllowed(p)).toBe(true))
  it.each(['/api/auth/status', '/api/auth/login', '/api/settings', '/api/settings/preferences/feishu-webhook', '/api/alerts/stream', '/api/watchlist/quotes', '/api/stock-analysis/analyze'])('rejects %s', p => expect(isOfflineCacheAllowed(p)).toBe(false))
  it('detects writes', () => expect(['POST', 'PUT', 'PATCH', 'DELETE'].every(isWriteMethod)).toBe(true))
})
```

- [ ] **Step 2: 运行 RED**

```bash
pnpm --dir frontend test -- src/lib/__tests__/offlinePolicy.test.ts
```

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现白名单**

```ts
const EXACT = new Set(['/api/watchlist', '/api/data/status', '/api/overview/market', '/api/stock-analysis/reports', '/api/market-recap/reports', '/api/backtest/summaries'])
const PREFIXES = ['/api/watchlist/enriched', '/api/kline/daily', '/api/stock-analysis/levels', '/api/analysis/']
export const normalizedApiPath = (s: string) => new URL(s, window.location.origin).pathname
export const isOfflineCacheAllowed = (s: string) => EXACT.has(normalizedApiPath(s)) || PREFIXES.some(p => normalizedApiPath(s).startsWith(p))
export const isWriteMethod = (m = 'GET') => new Set(['POST', 'PUT', 'PATCH', 'DELETE']).has(m.toUpperCase())
```

- [ ] **Step 4: 写 IndexedDB 失败测试**

```ts
import { beforeEach, describe, expect, it } from 'vitest'
import { clear } from 'idb-keyval'
import { getSnapshot, putSnapshot } from '../offlineDb'
beforeEach(async () => clear())
it('round trips a versioned snapshot', async () => {
  await putSnapshot('/api/watchlist', { symbols: ['000403.SZ'] }, 'r1')
  const hit = await getSnapshot<{ symbols: string[] }>('/api/watchlist')
  expect(hit?.data.symbols).toEqual(['000403.SZ'])
  expect(hit?.revision).toBe('r1')
  expect(hit?.schemaVersion).toBe(1)
})
```

- [ ] **Step 5: 实现 IndexedDB 存储**

```ts
import { del, get, set } from 'idb-keyval'
import { normalizedApiPath } from './offlinePolicy'
const SCHEMA_VERSION = 1
export interface OfflineSnapshot<T> { schemaVersion: number; fetchedAt: string; revision: string | null; data: T }
const key = (p: string) => `tickflow-offline:v1:${normalizedApiPath(p)}`
export async function putSnapshot<T>(p: string, data: T, revision: string | null) { await set(key(p), { schemaVersion: SCHEMA_VERSION, fetchedAt: new Date().toISOString(), revision, data }) }
export async function getSnapshot<T>(p: string): Promise<OfflineSnapshot<T> | null> {
  const value = await get<OfflineSnapshot<T>>(key(p))
  if (!value) return null
  if (value.schemaVersion !== SCHEMA_VERSION || !value.fetchedAt) { await del(key(p)); return null }
  return value
}
export async function clearSnapshot(p: string) { await del(key(p)) }
```

- [ ] **Step 6: 运行 GREEN 并提交**

```bash
pnpm --dir frontend test -- src/lib/__tests__/offlinePolicy.test.ts src/lib/__tests__/offlineDb.test.ts
git add frontend/src/lib/offlinePolicy.ts frontend/src/lib/offlineDb.ts frontend/src/lib/__tests__
git commit -m "feat: add allowlisted offline snapshots"
```

### Task 3: API 缓存回退与离线禁写

**Files:**
- Create: `frontend/src/lib/connectivity.ts`
- Modify: `frontend/src/lib/api.ts:8-37`
- Modify: `frontend/src/main.tsx:30-43`
- Test: `frontend/src/lib/__tests__/apiOffline.test.ts`

**Interfaces:**
- Produces: `connectivityStore`, `OfflineWriteError`, 导出的 `request<T>()`。

- [ ] **Step 1: 写失败测试**

```ts
import { expect, it, vi } from 'vitest'
import { connectivityStore } from '../connectivity'
import { putSnapshot } from '../offlineDb'
import { OfflineWriteError, request } from '../api'

it('falls back only for an allowlisted GET network error', async () => {
  await putSnapshot('/api/watchlist', { symbols: ['600489.SH'] }, 'r1')
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
  expect(await request('/api/watchlist')).toEqual({ symbols: ['600489.SH'] })
  expect(connectivityStore.getSnapshot().mode).toBe('offline-readonly')
})
it('blocks offline writes before fetch', async () => {
  const fetchSpy = vi.fn(); vi.stubGlobal('fetch', fetchSpy); connectivityStore.markOffline()
  await expect(request('/api/watchlist', { method: 'POST', body: '{}' })).rejects.toBeInstanceOf(OfflineWriteError)
  expect(fetchSpy).not.toHaveBeenCalled()
})
```

- [ ] **Step 2: 运行 RED**

```bash
pnpm --dir frontend test -- src/lib/__tests__/apiOffline.test.ts
```

- [ ] **Step 3: 实现连接状态**

```ts
export type ConnectivityMode = 'online' | 'offline-readonly'
let state = { mode: (navigator.onLine === false ? 'offline-readonly' : 'online') as ConnectivityMode, lastSuccessfulSync: null as string | null, cachedAt: null as string | null }
const listeners = new Set<() => void>()
const update = (next: Partial<typeof state>) => { state = { ...state, ...next }; listeners.forEach(fn => fn()) }
export const connectivityStore = {
  subscribe(fn: () => void) { listeners.add(fn); return () => listeners.delete(fn) },
  getSnapshot: () => state,
  markOnline(at = new Date().toISOString()) { update({ mode: 'online', lastSuccessfulSync: at, cachedAt: null }) },
  markOffline(cachedAt: string | null = null) { update({ mode: 'offline-readonly', cachedAt }) },
}
```

- [ ] **Step 4: 改造 request**

`request<T>()` 必须：写请求在 `offline-readonly` 时抛 `OfflineWriteError`；fetch 设置 `credentials: 'same-origin'` 和 API `cache: 'no-store'`；成功的白名单 GET 写 IndexedDB；只有 fetch `TypeError` 可从白名单缓存回退；HTTP 4xx/5xx 不回退；401 仍由全局登录跳转处理。把当前 17-35 行的错误解析抽成以下函数：

```ts
async function apiErrorFromResponse(res: Response): Promise<Error> {
  let detail = ''
  try {
    const body = await res.json()
    const raw = body.detail ?? body.message ?? ''
    detail = Array.isArray(raw) ? raw.map((item: any) => item?.msg || String(item)).join('; ') : typeof raw === 'string' ? raw : JSON.stringify(raw)
  } catch { detail = '' }
  const message = detail || `${res.status} ${res.statusText}`
  if (res.status !== 401) toast(message, 'error')
  return new Error(message)
}
```

核心分支：

```ts
if (isWriteMethod(method) && connectivityStore.getSnapshot().mode === 'offline-readonly') throw new OfflineWriteError()
try {
  const res = await fetch(path, { ...init, headers, credentials: 'same-origin', cache: path.startsWith('/api/') ? 'no-store' : init?.cache })
  if (!res.ok) throw await apiErrorFromResponse(res)
  const data = await res.json() as T
  if (method === 'GET' && isOfflineCacheAllowed(path)) await putSnapshot(path, data, res.headers.get('etag'))
  connectivityStore.markOnline()
  return data
} catch (error) {
  if (method === 'GET' && isOfflineCacheAllowed(path) && error instanceof TypeError) {
    const cached = await getSnapshot<T>(path)
    if (cached) { connectivityStore.markOffline(cached.fetchedAt); return cached.data }
  }
  throw error
}
```

- [ ] **Step 5: 网络恢复刷新 Query**

在 `main.tsx` 注册 `online` 时 `markOnline()` + `queryClient.invalidateQueries()`，`offline` 时 `markOffline()`；queries 使用 `networkMode: 'always'`、`refetchOnReconnect: true`，`OfflineWriteError` 不重试。

- [ ] **Step 6: 运行 GREEN 并提交**

```bash
pnpm --dir frontend test -- src/lib/__tests__/apiOffline.test.ts
pnpm --dir frontend build
git add frontend/src/lib/connectivity.ts frontend/src/lib/api.ts frontend/src/main.tsx frontend/src/lib/__tests__/apiOffline.test.ts
git commit -m "feat: enforce offline read-only API behavior"
```

### Task 4: 手机壳与重点页面

**Files:**
- Create: `frontend/src/components/ConnectionBanner.tsx`
- Create: `frontend/src/components/MobileNav.tsx`
- Test: `frontend/src/components/__tests__/ConnectionBanner.test.tsx`
- Modify: `frontend/src/components/Layout.tsx`, `frontend/src/router.tsx`, `frontend/src/index.css`
- Modify: `frontend/src/pages/{Watchlist,StockAnalysis,Review,ConceptAnalysis,IndustryAnalysis,Data}.tsx`
- Create: `frontend/e2e/mobile-pwa.spec.ts`, `frontend/playwright.config.ts`

**Interfaces:**
- Produces: 5 项底部导航、缓存时间横幅、390x844/412x915 无横向溢出。

- [ ] **Step 1: 写组件与 E2E 失败测试**

```tsx
connectivityStore.markOffline('2026-07-20T20:00:00+08:00')
render(<ConnectionBanner />)
expect(screen.getByText(/只读缓存/)).toBeInTheDocument()
```

```ts
for (const path of ['/watchlist', '/stock-analysis', '/review', '/concept-analysis', '/industry-analysis', '/data']) {
  test(`${path} has no horizontal overflow`, async ({ page }) => {
    await page.goto(path); await page.waitForLoadState('networkidle')
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1)
  })
}
```

- [ ] **Step 2: 实现 ConnectionBanner 和 MobileNav**

横幅使用 `useSyncExternalStore`，固定显示“只读缓存 · 缓存时间 {locale time}”。底部导航只含：`/watchlist`、`/stock-analysis`、`/review`、`/concept-analysis`、`/settings`，使用 lucide 图标和至少 44px 触控高度。

- [ ] **Step 3: 接入 Layout 与首页**

移动端隐藏 desktop sidebar，主内容增加 `pb-[calc(64px+env(safe-area-inset-bottom))]`；index route 改为 `<Navigate to="/watchlist" replace />`；`body` 设置 `overflow-x:hidden` 和 safe-area。

- [ ] **Step 4: 修正页面断点**

```text
StockAnalysis: grid-cols-[1fr_288px] -> grid-cols-1 xl:grid-cols-[minmax(0,1fr)_288px]
Data: px-8 py-6 -> px-3 py-4 sm:px-5 lg:px-8 lg:py-6
Concept/Industry: 手机统计条改两列，表格父容器 overflow-x-auto
Review: 手机单列，Markdown 容器 min-w-0 break-words
Watchlist: 手机首次默认 card，用户手动表格模式仅在自身容器滚动
```

- [ ] **Step 5: 配置三视口 Playwright**

`playwright.config.ts` 使用 iPhone 14、Pixel 7、1440x900 三个 project，`webServer` 为 `pnpm preview --host 127.0.0.1 --port 4173`，产物写 `reports/pwa_acceptance/test-results`。

- [ ] **Step 6: 验证并提交**

```bash
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test e2e/mobile-pwa.spec.ts
git add frontend/src frontend/e2e frontend/playwright.config.ts
git commit -m "feat: add mobile research shell"
```

### Task 5: HTTPS Cookie 与同源安全

**Files:**
- Modify: `backend/app/config.py`, `backend/app/api/auth.py`, `backend/app/main.py`, `docker-compose.yml`
- Test: `backend/tests/test_auth_secure_cookie.py`

**Interfaces:**
- Consumes: `AUTH_COOKIE_SECURE=true`。
- Produces: HTTPS Secure cookie；开发默认 false；无通配 CORS。

- [ ] **Step 1: 写失败测试**

```python
def test_login_cookie_is_secure_when_configured(monkeypatch, client):
    monkeypatch.setattr(auth_api.auth, "is_configured", lambda: True)
    monkeypatch.setattr(auth_api.auth, "verify_and_create_session", lambda _: "session-token")
    monkeypatch.setattr(auth_api.settings, "auth_cookie_secure", True)
    response = client.post("/api/auth/login", json={"password": "secret"})
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" in cookie
    assert "session-token" not in response.text
```

- [ ] **Step 2: 运行 RED**

```bash
uv run --project backend pytest backend/tests/test_auth_secure_cookie.py -q
```

- [ ] **Step 3: 实现配置**

`Settings` 增加 `auth_cookie_secure: bool = False`；login 的 `set_cookie` 和 logout 的 `delete_cookie` 均使用 `secure=settings.auth_cookie_secure`、`httponly=True`（login）、`samesite="lax"`、`path="/"`。

- [ ] **Step 4: 收紧同源部署**

删除 `main.py` 的通配 `CORSMiddleware`。在 SPA fallback 之前为 `/manifest.webmanifest` 和 `/sw.js` 注册 `FileResponse`，两者 `Cache-Control: no-cache`；哈希 assets 保持长缓存，index 保持 no-store。

- [ ] **Step 5: Compose 固化**

```yaml
environment:
  AUTH_COOKIE_SECURE: "true"
ports:
  - "127.0.0.1:${PORT:-3018}:3018"
```

- [ ] **Step 6: 验证并提交**

```bash
uv run --project backend pytest backend/tests/test_auth_secure_cookie.py -q
git add backend/app/config.py backend/app/api/auth.py backend/app/main.py backend/tests/test_auth_secure_cookie.py docker-compose.yml
git commit -m "fix: secure PWA authentication cookies"
```

### Task 6: Tailscale 8443 私网部署

**Files:**
- Create: `scripts/verify_pwa_private_access.sh`
- Create: `docs/pwa-private-runbook.md`
- Create: `reports/pwa_acceptance/pre_deploy_network.txt`

**Interfaces:**
- Produces: 8443 tailnet-only PWA，不覆盖 443/8787。

- [ ] **Step 1: 记录现状**

```bash
mkdir -p reports/pwa_acceptance
ssh codex-vm 'tailscale serve status --json; ss -lntp | grep -E ":(3018|3019|8443|8787)[[:space:]]"' > reports/pwa_acceptance/pre_deploy_network.txt
```

- [ ] **Step 2: 更新旁路容器**

```bash
ssh codex-vm 'cd /home/ubuntu/tickflow-stock-panel-stage-a && GOLD_WORKSPACE_ENABLED=false PORT=3019 docker compose up -d --build && curl -fsS http://127.0.0.1:3019/health'
```

- [ ] **Step 3: 新增 8443**

```bash
ssh codex-vm 'tailscale serve --bg --https=8443 http://127.0.0.1:3019; tailscale serve status'
```

Expected: 同时存在 443 -> 8787 和 8443 -> 3019。若 443 消失，立即回滚。

- [ ] **Step 4: 写验证脚本**

```bash
#!/usr/bin/env bash
set -euo pipefail
status="$(ssh codex-vm 'tailscale serve status')"
grep -Fq 'proxy http://127.0.0.1:8787' <<<"$status"
grep -Fq 'proxy http://127.0.0.1:3019' <<<"$status"
ssh codex-vm 'ss -lnt | grep -Eq "127.0.0.1:3019[[:space:]]"'
ssh codex-vm '! ss -lnt | grep -Eq "(0.0.0.0|\[::\]):3019[[:space:]]"'
curl -fsS --connect-timeout 10 'https://vm-0-9-ubuntu.tail21c236.ts.net:8443/health' | grep -q '"status":"ok"'
echo PWA_PRIVATE_ACCESS_OK
```

- [ ] **Step 5: 写安装与回滚 runbook**

Runbook 必须包含 Tailscale 入网、8443 登录、iOS/Android 添加主屏幕、容器升级、验收，以及：

```bash
ssh codex-vm 'tailscale serve --https=8443 off'
ssh codex-vm 'tailscale serve --bg --https=443 http://127.0.0.1:8787'
```

- [ ] **Step 6: 验证并提交**

```bash
chmod +x scripts/verify_pwa_private_access.sh
./scripts/verify_pwa_private_access.sh
git add scripts/verify_pwa_private_access.sh docs/pwa-private-runbook.md reports/pwa_acceptance/pre_deploy_network.txt
git commit -m "docs: add private PWA deployment runbook"
```

### Task 7: Phase 1 验收

**Files:**
- Create: `reports/pwa_acceptance/phase1_results.md`
- Create: `reports/pwa_acceptance/screenshots/`

**Interfaces:**
- Produces: Phase 1 进入 Intel Mac 阶段的门禁证据。

- [ ] **Step 1: 自动化回归**

```bash
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
uv run --project backend pytest backend/tests/test_auth_secure_cookie.py -q
./scripts/verify_pwa_private_access.sh
```

- [ ] **Step 2: 安全扫描**

```bash
grep -R '/api/' frontend/dist/sw.js && exit 1 || true
grep -R -E 'TICKFLOW_API_KEY[=]|TUSHARE_TOKEN[=]|DEEPSEEK_API_KEY[=]|tf_session[=]' frontend/dist && exit 1 || true
```

Expected: 零命中。

- [ ] **Step 3: 真机流程**

iPhone Safari 和 Android Chrome 各完成：Tailscale 连接、8443 登录、安装主屏幕、自选股、三只股票个股分析、Markdown 查看、断网缓存、离线禁写、恢复网络刷新。页面不得横向溢出，离线不能显示“实时”或“最新”。

- [ ] **Step 4: 写报告并提交**

```bash
git add reports/pwa_acceptance
git commit -m "test: record private PWA acceptance"
```

报告记录设备、版本、时间、通过项、失败项、8443 URL、443 保留证据和回滚状态，不含密码、Cookie、Key 或复盘正文。
