import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import { StockResearch } from '../StockResearch'
import { api, type PaperTradingDashboard } from '@/lib/api'

vi.mock('@/lib/api', () => ({ api: { paperTradingDashboard: vi.fn(), paperTradingRun: vi.fn() } }))
vi.mock('@/components/paper-trading/ResearchEngineView', () => ({
  ResearchOverview: ({ engine, requestedDate }: { engine: { trade_date: string }; requestedDate: string }) =>
    <div>summary:{engine.trade_date}/{requestedDate}</div>,
}))
vi.mock('@/components/paper-trading/StrategyOverviewCards', () => ({
  StrategyOverviewCards: ({ engine }: { engine: { trade_date: string } }) => <div>six-cards:{engine.trade_date}</div>,
}))
vi.mock('@/components/paper-trading/StrategyGraphics', () => ({
  StrategyGraphics: ({ graphics }: { graphics?: { trade_date: string } }) => <div>graphics:{graphics?.trade_date ?? 'absent'}</div>,
}))
vi.mock('@/components/paper-trading/ResearchPanel', () => ({
  ResearchPanel: ({ requestedDate }: { requestedDate: string }) => <div>conditions-chan:{requestedDate}</div>,
}))

function renderPage() {
  return render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}>
      <StockResearch />
    </QueryClientProvider>
  </MemoryRouter>)
}

beforeEach(() => vi.clearAllMocks())

it('reads the same dashboard chain for summary, cards, graphics and Chan without a run', async () => {
  vi.mocked(api.paperTradingDashboard).mockResolvedValue({
    symbol: '000403.SZ', name: '派林生物', requested_date: '2026-09-10',
    research: { status: 'READY', trade_date: '2026-09-09',
      engine: { trade_date: '2026-09-09' }, graphics: { trade_date: '2026-09-09' } },
  } as PaperTradingDashboard)
  renderPage()
  expect(await screen.findByText('summary:2026-09-09/2026-09-10')).toBeVisible()
  expect(screen.getByText('six-cards:2026-09-09')).toBeVisible()
  expect(screen.getByText('graphics:2026-09-09')).toBeVisible()
  expect(screen.getByText('conditions-chan:2026-09-10')).toBeVisible()
  expect(screen.getByRole('link', { name: /今日工作台/ })).toHaveAttribute('href', '/paper-trading')
  expect(api.paperTradingDashboard).toHaveBeenCalledTimes(1)
  expect(api.paperTradingRun).not.toHaveBeenCalled()
})

it('shows offline error instead of inventing research', async () => {
  vi.mocked(api.paperTradingDashboard).mockRejectedValue(new TypeError('Failed to fetch'))
  renderPage()
  expect(await screen.findByRole('alert')).toHaveTextContent('TickFlow 后端未运行')
  expect(screen.queryByText(/six-cards/)).not.toBeInTheDocument()
  expect(api.paperTradingRun).not.toHaveBeenCalled()
})

it('keeps a blocked research result blocked and never prepares inputs', async () => {
  vi.mocked(api.paperTradingDashboard).mockResolvedValue({
    symbol: '000403.SZ', name: '派林生物', requested_date: '2026-09-10', research: { status: 'BLOCKED' },
  } as PaperTradingDashboard)
  renderPage()
  expect(await screen.findByText('conditions-chan:2026-09-10')).toBeVisible()
  expect(screen.getByText('graphics:absent')).toBeVisible()
  expect(screen.queryByText(/six-cards/)).not.toBeInTheDocument()
  expect(api.paperTradingRun).not.toHaveBeenCalled()
})
