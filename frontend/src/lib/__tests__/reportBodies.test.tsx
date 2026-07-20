import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '../api'
import {
  loadHistory,
  openHistoryReport,
  useDialogTask,
} from '../stockAnalysisStore'

const STOCK_BODY = 'UNIQUE_STOCK_REPORT_BODY_CANARY'
const MARKET_BODY = 'UNIQUE_MARKET_REPORT_BODY_CANARY'

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'private, no-store',
    },
  })
}

function DialogProbe() {
  const { task } = useDialogTask()
  return <div>{task?.content ?? ''}</div>
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('report body loading', () => {
  it('uses encoded exact-ID GET APIs with browser no-store caching', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ report: { id: 'sar / 1', content: STOCK_BODY } }))
      .mockResolvedValueOnce(jsonResponse({ report: { id: 'mkr / 1', content: MARKET_BODY } }))
    vi.stubGlobal('fetch', fetchMock)

    const stock = await api.stockAnalysisReportGet('sar / 1')
    const market = await api.reviewReportGet('mkr / 1')

    expect(stock.report.content).toBe(STOCK_BODY)
    expect(market.report.content).toBe(MARKET_BODY)
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/stock-analysis/reports/sar%20%2F%201',
      expect.objectContaining({ cache: 'no-store', credentials: 'same-origin' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/market-recap/reports/mkr%20%2F%201',
      expect.objectContaining({ cache: 'no-store', credentials: 'same-origin' }),
    )
  })

  it('fetches the stock report body when history is selected', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/stock-analysis/reports') {
        return jsonResponse({
          reports: [{
            id: 'sar_exact',
            symbol: '000403.SZ',
            title: '派林生物日线复盘',
            created_at: '2026-07-20T16:00:00+08:00',
          }],
        })
      }
      if (path === '/api/stock-analysis/reports/sar_exact') {
        return jsonResponse({
          report: {
            id: 'sar_exact',
            symbol: '000403.SZ',
            name: '派林生物',
            focus: '',
            content: STOCK_BODY,
            created_at: '2026-07-20T16:00:00+08:00',
          },
        })
      }
      throw new Error(`unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<DialogProbe />)

    await loadHistory()
    await openHistoryReport('sar_exact')

    expect(await screen.findByText(STOCK_BODY)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/stock-analysis/reports/sar_exact',
      expect.objectContaining({ cache: 'no-store' }),
    )
  })
})
