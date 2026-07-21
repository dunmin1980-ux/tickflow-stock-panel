import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../api'
import { connectivityStore, offlineSessionAccess } from '../connectivity'
import { etagStore } from '../etagStore'
import { clearAllSnapshots, getSnapshot } from '../offlineDb'
import { workspaceApi } from '../workspace'

const REVISION = 'a'.repeat(64)
const SECRET_CANARY = 'PREFERENCE_SECRET_MUST_NOT_PERSIST'

function jsonResponse(body: unknown, status = 200, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

describe('desktop workspace preference projection', () => {
  beforeEach(async () => {
    await clearAllSnapshots()
    etagStore.clear()
    offlineSessionAccess.revoke()
    connectivityStore.markOnline()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('merges the partial shared DTO over the real complete runtime response without caching secrets', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/client/status') {
        return jsonResponse({
          configured: true,
          authenticated: true,
          reachable: true,
          mode: 'hybrid',
          error_code: null,
        })
      }
      if (path === '/api/workspace/resources/preferences') {
        return jsonResponse({
          resource: 'preferences',
          revision: REVISION,
          updated_at: '2026-07-21T09:00:00+08:00',
          data: {
            preferences: {
              indices_nav_pinned: false,
              nav_order: ['watchlist', 'review'],
              daily_data_provider: 'tickflow',
              api_key: SECRET_CANARY,
            },
          },
        }, 200, { ETag: `"${REVISION}"` })
      }
      if (path === '/api/settings/preferences') {
        return jsonResponse({
          realtime_quotes_enabled: true,
          minute_sync_enabled: true,
          minute_sync_days: 17,
          strategy_monitor_enabled: true,
          review_schedule: { enabled: true, hour: 16, minute: 5 },
          minute_intraday_refresh: true,
          minute_intraday_refresh_interval: 9,
          system_notify_enabled: true,
          api_key: SECRET_CANARY,
        })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const preferences = await api.preferences()

    expect(preferences.indices_nav_pinned).toBe(false)
    expect(preferences.nav_order).toEqual(['watchlist', 'review'])
    expect(preferences.daily_data_provider).toBe('tickflow')
    expect(preferences.realtime_quotes_enabled).toBe(true)
    expect(preferences.minute_sync_enabled).toBe(true)
    expect(preferences.minute_sync_days).toBe(17)
    expect(preferences.strategy_monitor_enabled).toBe(true)
    expect(preferences.review_schedule).toEqual({ enabled: true, hour: 16, minute: 5 })
    expect(preferences.minute_intraday_refresh_interval).toBe(9)
    expect('api_key' in preferences).toBe(false)
    expect(fetchMock.mock.calls.map(call => String(call[0]))).toEqual([
      '/api/client/status',
      '/api/settings/preferences',
      '/api/workspace/resources/preferences',
    ])

    const cached = await getSnapshot('/api/workspace/resources/preferences')
    expect(JSON.stringify(cached?.data)).not.toContain(SECRET_CANARY)
    expect(await getSnapshot('/api/settings/preferences')).toBeNull()
  })

  it('falls back to the sanitized workspace preference snapshot when PWA client detection is offline', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse({
      resource: 'preferences',
      revision: REVISION,
      updated_at: '2026-07-21T09:00:00+08:00',
      data: { preferences: { nav_hidden: ['/monitor'], screener_auto_run: false } },
    }, 200, { ETag: `"${REVISION}"` })))
    await workspaceApi.get('preferences')

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const preferences = await api.preferences()

    expect(preferences.nav_hidden).toEqual(['/monitor'])
    expect(preferences.screener_auto_run).toBe(false)
    expect(preferences.minute_sync_days).toBeGreaterThan(0)
  })

  it('refreshes monitor, minute, review, and realtime runtime fields after a mutation refetch', async () => {
    let runtimeRead = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/client/status') {
        return jsonResponse({
          configured: true,
          authenticated: true,
          reachable: true,
          mode: 'hybrid',
          error_code: null,
        })
      }
      if (path === '/api/settings/preferences' && !init?.method) {
        runtimeRead += 1
        return jsonResponse(runtimeRead === 1 ? {
          realtime_quotes_enabled: false,
          minute_sync_enabled: false,
          minute_sync_days: 5,
          strategy_monitor_enabled: false,
          review_schedule: { enabled: false, hour: 15, minute: 10 },
        } : {
          realtime_quotes_enabled: true,
          minute_sync_enabled: true,
          minute_sync_days: 12,
          strategy_monitor_enabled: true,
          review_schedule: { enabled: true, hour: 16, minute: 20 },
        })
      }
      if (path === '/api/settings/preferences/minute-sync' && init?.method === 'PUT') {
        return jsonResponse({ minute_sync_enabled: true, minute_sync_days: 12 })
      }
      if (path === '/api/workspace/resources/preferences') {
        return jsonResponse({
          resource: 'preferences',
          revision: REVISION,
          updated_at: '2026-07-21T09:00:00+08:00',
          data: { preferences: { nav_order: ['watchlist'] } },
        }, 200, { ETag: `"${REVISION}"` })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const before = await api.preferences()
    expect(before.minute_sync_days).toBe(5)
    expect(before.strategy_monitor_enabled).toBe(false)

    await api.updateMinuteSync(true, 12)
    const after = await api.preferences()

    expect(after.realtime_quotes_enabled).toBe(true)
    expect(after.minute_sync_enabled).toBe(true)
    expect(after.minute_sync_days).toBe(12)
    expect(after.strategy_monitor_enabled).toBe(true)
    expect(after.review_schedule).toEqual({ enabled: true, hour: 16, minute: 20 })
    expect(runtimeRead).toBe(2)
  })
})
