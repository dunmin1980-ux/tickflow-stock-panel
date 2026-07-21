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

  it('merges the partial shared DTO over complete safe defaults without caching secrets', async () => {
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
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const preferences = await api.preferences()

    expect(preferences.indices_nav_pinned).toBe(false)
    expect(preferences.nav_order).toEqual(['watchlist', 'review'])
    expect(preferences.daily_data_provider).toBe('tickflow')
    expect(preferences.minute_sync_days).toBeGreaterThan(0)
    expect(preferences.pipeline_schedule).toEqual(expect.objectContaining({ hour: expect.any(Number) }))
    expect('api_key' in preferences).toBe(false)

    const cached = await getSnapshot('/api/workspace/resources/preferences')
    expect(JSON.stringify(cached?.data)).not.toContain(SECRET_CANARY)
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
})
