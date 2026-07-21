import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../api'
import { connectivityStore } from '../connectivity'
import { etagStore } from '../etagStore'

const REVISION = 'a'.repeat(64)
const NEXT_REVISION = 'b'.repeat(64)

function snapshot(symbols: string[], revision = REVISION): Response {
  return new Response(JSON.stringify({
    resource: 'watchlist',
    revision,
    updated_at: '2026-07-21T09:00:00+08:00',
    data: { symbols: symbols.map(symbol => ({ symbol })) },
  }), {
    status: 200,
    headers: { 'Content-Type': 'application/json', ETag: `"${revision}"` },
  })
}

describe('workspace watchlist mutation counts', () => {
  beforeEach(() => {
    etagStore.clear()
    connectivityStore.markOnline()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('counts only unique newly added symbols when the request contains duplicates and existing items', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/workspace/resources/watchlist' && !init?.method) {
        return snapshot(['000403.SZ'])
      }
      if (path === '/api/workspace/resources/watchlist/commands' && init?.method === 'POST') {
        return snapshot(['000403.SZ', '600489.SH'], NEXT_REVISION)
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await api.watchlistBatchAdd(
      ['000403.SZ', '600489.SH', '600489.SH'],
      'sample',
    )

    expect(result.added).toBe(1)
    expect(result.symbols.map(item => item.symbol)).toEqual(['000403.SZ', '600489.SH'])
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('returns the number of items actually removed by clear', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/workspace/resources/watchlist' && !init?.method) {
        return snapshot(['000403.SZ', '600489.SH'])
      }
      if (path === '/api/workspace/resources/watchlist/commands' && init?.method === 'POST') {
        return snapshot([], NEXT_REVISION)
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await api.watchlistClear()

    expect(result.removed).toBe(2)
    expect(result.symbols).toEqual([])
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
