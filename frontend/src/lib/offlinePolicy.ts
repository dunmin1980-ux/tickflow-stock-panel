const EXACT_PATHS = new Set([
  '/api/watchlist',
  '/api/data/status',
  '/api/overview/market',
  '/api/stock-analysis/reports',
  '/api/market-recap/reports',
  '/api/backtest/summaries',
])

const PATH_PREFIXES = [
  '/api/watchlist/enriched',
  '/api/kline/daily',
  '/api/stock-analysis/levels',
  '/api/analysis',
]

const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export function normalizedApiPath(source: string): string {
  const url = new URL(source, window.location.origin)
  if (url.origin !== window.location.origin) {
    throw new TypeError('Offline snapshots require a same-origin API path')
  }
  return url.pathname
}

export function isOfflineCacheAllowed(source: string): boolean {
  try {
    const path = normalizedApiPath(source)
    return (
      EXACT_PATHS.has(path) ||
      PATH_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`))
    )
  } catch {
    return false
  }
}

export const isWriteMethod = (method = 'GET') => WRITE_METHODS.has(method.toUpperCase())
