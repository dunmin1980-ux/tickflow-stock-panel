import { beforeEach, describe, expect, it } from 'vitest'
import { clear, set } from 'idb-keyval'

import { clearSnapshot, getSnapshot, putSnapshot } from '../offlineDb'

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
    await putSnapshot('/api/kline/daily?symbol=000403.SZ', { close: 20 }, null)
    expect(await getSnapshot('/api/kline/daily?symbol=000403.SZ')).not.toBeNull()
    expect(await getSnapshot('/api/kline/daily?symbol=600489.SH')).toBeNull()

    await clearSnapshot('/api/kline/daily?symbol=000403.SZ')
    expect(await getSnapshot('/api/kline/daily?symbol=000403.SZ')).toBeNull()
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
})
