import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { connectivityStore, offlineSessionAccess } from '../connectivity'
import { etagStore } from '../etagStore'
import { clearAllSnapshots, getSnapshot } from '../offlineDb'
import { workspaceApi, workspaceStatusStore } from '../workspace'

const REVISION = 'a'.repeat(64)
const BODY_CANARY = 'MARKDOWN_BODY_MUST_NOT_BE_CACHED'
const SECRET_CANARY = 'WORKSPACE_SECRET_MUST_NOT_BE_CACHED'

function jsonResponse(body: unknown, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

function bootstrapPayload() {
  return {
    schema_version: 1,
    server_time: '2026-07-21T09:00:00+08:00',
    data_as_of: '2026-07-20',
    mode: 'cloud',
    capabilities: { label: 'Free', capabilities: {} },
    resources: {
      watchlist: {
        revision: REVISION,
        updated_at: '2026-07-21T09:00:00+08:00',
        data: { symbols: [{ symbol: '000403.SZ', name: '派林生物', extra: SECRET_CANARY }] },
      },
      preferences: {
        revision: REVISION,
        updated_at: '2026-07-21T09:00:00+08:00',
        data: { preferences: { nav_order: ['watchlist'], api_key: SECRET_CANARY } },
      },
      stock_reports: {
        revision: REVISION,
        updated_at: '2026-07-21T09:00:00+08:00',
        data: { reports: [{ id: 'stock-1', symbol: '000403.SZ', title: '复盘', content: BODY_CANARY }] },
      },
      market_recaps: {
        revision: REVISION,
        updated_at: '2026-07-21T09:00:00+08:00',
        data: { reports: [{ id: 'recap-1', title: '市场复盘', markdown: BODY_CANARY }] },
      },
      backtest_summaries: {
        revision: REVISION,
        updated_at: '2026-07-21T09:00:00+08:00',
        data: {
          summaries: [{
            id: 'bt-1', task: 'strategy', strategy_id: 'macd', parameters_digest: 'digest',
            stats: { return_pct: 3.2 }, started_at: '2026-07-21T08:00:00+08:00',
            finished_at: '2026-07-21T08:01:00+08:00', data_as_of: '2026-07-20',
            engine: 'local', execution_target: 'desktop', debug: SECRET_CANARY,
          }],
        },
      },
    },
  }
}

describe('workspace offline metadata cache', () => {
  beforeEach(async () => {
    await clearAllSnapshots()
    etagStore.clear()
    offlineSessionAccess.revoke()
    connectivityStore.markOnline()
    workspaceStatusStore.markOnline()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('restores all five sanitized metadata resources after a PWA restart-like network failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse(bootstrapPayload())))

    await workspaceApi.bootstrap()

    const cached = await getSnapshot('/api/workspace/bootstrap')
    const serialized = JSON.stringify(cached?.data)
    expect(serialized).not.toContain(BODY_CANARY)
    expect(serialized).not.toContain(SECRET_CANARY)
    expect(offlineSessionAccess.isGranted()).toBe(true)
    for (const resource of [
      'watchlist', 'preferences', 'stock_reports', 'market_recaps', 'backtest_summaries',
    ]) {
      expect(await getSnapshot(`/api/workspace/resources/${resource}`)).not.toBeNull()
    }

    etagStore.clear()
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch')))

    const restored = await workspaceApi.bootstrap()

    expect(restored.offline_readonly).toBe(true)
    expect(restored.resources.watchlist.data.symbols[0].symbol).toBe('000403.SZ')
    expect(restored.resources.preferences.data.preferences.nav_order).toEqual(['watchlist'])
    expect(restored.resources.stock_reports.data.reports[0].id).toBe('stock-1')
    expect(restored.resources.market_recaps.data.reports[0].id).toBe('recap-1')
    expect(restored.resources.backtest_summaries.data.summaries[0].id).toBe('bt-1')
    expect(connectivityStore.getSnapshot().mode).toBe('offline-readonly')

    const restoredPreferences = await workspaceApi.get('preferences')
    expect(restoredPreferences.offline_readonly).toBe(true)
    expect(restoredPreferences.data.preferences.nav_order).toEqual(['watchlist'])
  })

  it('never persists a report body from a single resource response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse({
      resource: 'stock_reports',
      revision: REVISION,
      updated_at: '2026-07-21T09:00:00+08:00',
      data: {
        reports: [{ id: 'stock-1', symbol: '000403.SZ', title: '复盘', content: BODY_CANARY }],
      },
    }, { ETag: `"${REVISION}"` })))

    await workspaceApi.get('stock_reports')

    const cached = await getSnapshot('/api/workspace/resources/stock_reports')
    expect(JSON.stringify(cached?.data)).not.toContain(BODY_CANARY)
    expect(cached?.data).toMatchObject({ data: { reports: [{ id: 'stock-1' }] } })
  })

  it('does not restore offline access when the session is revoked during an in-flight read', async () => {
    let resolveFetch!: (response: Response) => void
    vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(new Promise<Response>(resolve => {
      resolveFetch = resolve
    })))

    const pending = workspaceApi.bootstrap()
    offlineSessionAccess.revoke()
    resolveFetch(jsonResponse(bootstrapPayload()))

    await pending

    expect(offlineSessionAccess.isGranted()).toBe(false)
    expect(await getSnapshot('/api/workspace/bootstrap')).toBeNull()
  })

  it('revokes access and clears every workspace metadata snapshot after a read returns 401', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse(bootstrapPayload())))
    await workspaceApi.bootstrap()
    expect(offlineSessionAccess.isGranted()).toBe(true)

    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(
      JSON.stringify({ detail: 'expired' }),
      { status: 401, headers: { 'Content-Type': 'application/json' } },
    )))
    await expect(workspaceApi.bootstrap()).rejects.toMatchObject({ status: 401 })

    expect(offlineSessionAccess.isGranted()).toBe(false)
    expect(await getSnapshot('/api/workspace/bootstrap')).toBeNull()
    for (const resource of [
      'watchlist', 'preferences', 'stock_reports', 'market_recaps', 'backtest_summaries',
    ]) {
      expect(await getSnapshot(`/api/workspace/resources/${resource}`)).toBeNull()
    }

    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch')))
    await expect(workspaceApi.bootstrap()).rejects.toThrow('Failed to fetch')
  })

  it('revokes access and clears workspace snapshots after a command returns 401', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse(bootstrapPayload())))
    await workspaceApi.bootstrap()
    workspaceStatusStore.markOnline()
    connectivityStore.markOnline()

    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(
      JSON.stringify({ detail: 'expired' }),
      { status: 401, headers: { 'Content-Type': 'application/json' } },
    )))

    await expect(
      workspaceApi.command('watchlist', 'clear', {}, REVISION),
    ).rejects.toMatchObject({ status: 401 })
    expect(offlineSessionAccess.isGranted()).toBe(false)
    expect(await getSnapshot('/api/workspace/bootstrap')).toBeNull()
    expect(await getSnapshot('/api/workspace/resources/watchlist')).toBeNull()
  })
})
