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

export const normalizedApiPath = (source: string) =>
  new URL(source, window.location.origin).pathname

export function isOfflineCacheAllowed(source: string): boolean {
  const path = normalizedApiPath(source)
  return (
    EXACT_PATHS.has(path) ||
    PATH_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`))
  )
}

export const isWriteMethod = (method = 'GET') => WRITE_METHODS.has(method.toUpperCase())
