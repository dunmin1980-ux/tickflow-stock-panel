import { beforeEach, describe, expect, it } from 'vitest'
import { clear, set } from 'idb-keyval'

import { clearAllSnapshots, clearSnapshot, getSnapshot, putSnapshot } from '../offlineDb'

describe('offline snapshot storage', () => {
  beforeEach(async () => clear())

  it('round trips a versioned snapshot', async () => {
    await putSnapshot('/api/watchlist', { symbols: ['000403.SZ'] }, 'r1')

    const hit = await getSnapshot<{ symbols: string[] }>('/api/watchlist')

    expect(hit?.data.symbols).toEqual(['000403.SZ'])
    expect(hit?.revision).toBe('r1')
    expect(hit?.schemaVersion).toBe(1)
    expect(Number.isNaN(Date.parse(hit?.fetchedAt ?? ''))).toBe(false)
  })

  it('normalizes query order, isolates symbols, and clears a snapshot', async () => {
    await putSnapshot('/api/kline/daily?symbol=000403.SZ&adjust=qfq', { close: 20 }, null)
    expect(await getSnapshot('/api/kline/daily?adjust=qfq&symbol=000403.SZ')).not.toBeNull()
    expect(await getSnapshot('/api/kline/daily?symbol=600489.SH')).toBeNull()

    await clearSnapshot('/api/kline/daily?adjust=qfq&symbol=000403.SZ')
    expect(await getSnapshot('/api/kline/daily?symbol=000403.SZ&adjust=qfq')).toBeNull()
  })

  it('deletes snapshots with an unsupported schema', async () => {
    const path = '/api/watchlist'
    await set(`tickflow-offline:v1:${path}`, {
      schemaVersion: 0,
      fetchedAt: new Date().toISOString(),
      revision: null,
      data: {},
    })

    expect(await getSnapshot(path)).toBeNull()
    expect(await getSnapshot(path)).toBeNull()
  })

  it('rejects non-canonical timestamps and cross-origin keys', async () => {
    const path = '/api/watchlist'
    await set(`tickflow-offline:v1:${path}`, {
      schemaVersion: 1,
      fetchedAt: '2026-02-30',
      revision: null,
      data: {},
    })

    expect(await getSnapshot(path)).toBeNull()
    await expect(putSnapshot('https://evil.example/api/watchlist', {}, null)).rejects.toThrow(
      'same-origin',
    )
  })

  it('clears only the TickFlow snapshot namespace', async () => {
    await putSnapshot('/api/watchlist', { symbols: [] }, null)
    await set('unrelated-key', 'keep')

    await clearAllSnapshots()

    expect(await getSnapshot('/api/watchlist')).toBeNull()
    const { get } = await import('idb-keyval')
    expect(await get('unrelated-key')).toBe('keep')
  })
})
