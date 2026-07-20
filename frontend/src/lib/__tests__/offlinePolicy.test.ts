import { describe, expect, it } from 'vitest'

import { isOfflineCacheAllowed, isWriteMethod } from '../offlinePolicy'

describe('offline policy', () => {
  it.each([
    '/api/watchlist',
    '/api/watchlist/enriched',
    '/api/kline/daily',
    '/api/stock-analysis/levels',
    '/api/stock-analysis/reports',
    '/api/market-recap/reports',
    '/api/data/status',
    '/api/overview/market',
    '/api/backtest/summaries',
  ])('allows %s', (path) => expect(isOfflineCacheAllowed(path)).toBe(true))

  it.each([
    '/api/auth/status',
    '/api/auth/login',
    '/api/settings',
    '/api/settings/preferences/feishu-webhook',
    '/api/alerts/stream',
    '/api/watchlist/quotes',
    '/api/stock-analysis/analyze',
  ])('rejects %s', (path) => expect(isOfflineCacheAllowed(path)).toBe(false))

  it('ignores query strings when matching a read path', () => {
    expect(isOfflineCacheAllowed('/api/kline/daily?symbol=000403.SZ')).toBe(true)
  })

  it('does not allow lookalike paths', () => {
    expect(isOfflineCacheAllowed('/api/watchlist-malicious')).toBe(false)
  })

  it('rejects absolute and scheme-relative cross-origin URLs', () => {
    expect(isOfflineCacheAllowed('https://evil.example/api/watchlist')).toBe(false)
    expect(isOfflineCacheAllowed('//evil.example/api/watchlist')).toBe(false)
  })

  it('detects writes case-insensitively', () => {
    expect(['POST', 'put', 'Patch', 'DELETE'].every(isWriteMethod)).toBe(true)
    expect(isWriteMethod('GET')).toBe(false)
  })
})
