import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { OfflineWriteError, request } from '../api'
import { connectivityStore, offlineSessionAccess } from '../connectivity'
import { clearSnapshot, getSnapshot, putSnapshot } from '../offlineDb'

describe('offline API behavior', () => {
  beforeEach(async () => {
    connectivityStore.markOnline('2026-07-20T12:00:00.000Z')
    offlineSessionAccess.revoke()
    await clearSnapshot('/api/watchlist')
  })

  afterEach(() => vi.unstubAllGlobals())

  it('falls back only for an allowlisted GET network error', async () => {
    await putSnapshot('/api/watchlist', { symbols: ['600489.SH'] }, 'r1')
    offlineSessionAccess.grant()
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(request('/api/watchlist')).resolves.toEqual({ symbols: ['600489.SH'] })
    expect(connectivityStore.getSnapshot().mode).toBe('offline-readonly')
    expect(connectivityStore.getSnapshot().cachedAt).not.toBeNull()
  })

  it('does not mask HTTP failures with cached data', async () => {
    await putSnapshot('/api/watchlist', { symbols: ['600489.SH'] }, 'r1')
    offlineSessionAccess.grant()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'server rejected request' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await expect(request('/api/watchlist')).rejects.toThrow('server rejected request')
    expect(connectivityStore.getSnapshot().mode).toBe('online')
  })

  it('revokes offline access and clears snapshots after a 401', async () => {
    await putSnapshot('/api/watchlist', { symbols: ['600489.SH'] }, 'r1')
    offlineSessionAccess.grant()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: '会话已过期' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await expect(request('/api/watchlist')).rejects.toThrow('会话已过期')
    expect(offlineSessionAccess.isGranted()).toBe(false)
    expect(await getSnapshot('/api/watchlist')).toBeNull()
  })

  it('does not cache or fall back for a forbidden route', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(request('/api/auth/status')).rejects.toThrow('Failed to fetch')
    expect(connectivityStore.getSnapshot().mode).toBe('online')
  })

  it('does not reveal a snapshot without an active browser session grant', async () => {
    await putSnapshot('/api/watchlist', { symbols: ['600489.SH'] }, 'r1')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(request('/api/watchlist')).rejects.toThrow('Failed to fetch')
  })

  it('blocks offline writes before fetch', async () => {
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    connectivityStore.markOffline()

    await expect(
      request('/api/watchlist', { method: 'POST', body: '{}' }),
    ).rejects.toBeInstanceOf(OfflineWriteError)
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('uses same-origin no-store requests and snapshots successful reads', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ symbols: ['000403.SZ'] }), {
        status: 200,
        headers: { ETag: 'revision-2', 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchSpy)

    await expect(request('/api/watchlist')).resolves.toEqual({ symbols: ['000403.SZ'] })
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/watchlist',
      expect.objectContaining({ credentials: 'same-origin', cache: 'no-store' }),
    )
    expect((await getSnapshot<{ symbols: string[] }>('/api/watchlist'))?.revision).toBe(
      'revision-2',
    )
    expect(offlineSessionAccess.isGranted()).toBe(true)
    expect(connectivityStore.getSnapshot().mode).toBe('online')
  })

  it('does not let an in-flight read restore access after revocation', async () => {
    let resolveFetch!: (response: Response) => void
    vi.stubGlobal(
      'fetch',
      vi.fn().mockReturnValue(
        new Promise<Response>((resolve) => {
          resolveFetch = resolve
        }),
      ),
    )

    const pending = request('/api/watchlist')
    offlineSessionAccess.revoke()
    resolveFetch(
      new Response(JSON.stringify({ symbols: ['000403.SZ'] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(pending).resolves.toEqual({ symbols: ['000403.SZ'] })
    expect(offlineSessionAccess.isGranted()).toBe(false)
    expect(await getSnapshot('/api/watchlist')).toBeNull()
  })

  it('does not fail a response when sessionStorage throws', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError')
    })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ symbols: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await expect(request('/api/watchlist')).resolves.toEqual({ symbols: [] })
  })
})
