import { act, cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Layout } from '../Layout'
import { api, type PaperTradingDashboard } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { useWorkspaceStatus } from '@/lib/useWorkspaceEvents'

vi.mock('@/lib/useWorkspaceEvents', () => ({
  WorkspaceEvents: () => null,
  useWorkspaceStatus: vi.fn(),
}))

function dashboardFixture(): PaperTradingDashboard {
  return {
    status: 'VISUAL_WORKBENCH_READY', symbol: '000403.SZ', name: '派林生物', timezone: 'Asia/Shanghai',
    requested_date: '2026-09-11', last_completed_trade_date: '2026-09-08',
    safety: { simulation_only: 'SIMULATION ONLY', real_trading: 'DISABLED', can_publish: false, trading_advice: false },
    input_readiness: {
      status: 'READY_VALIDATED_DAILY', requested_date: '2026-09-11', effective_trade_date: '2026-09-10',
      source_fixture: 'VALIDATED_DAILY_INPUT', is_current_date: false, message: 'VALIDATED_DAILY_INPUT_AVAILABLE',
    },
    claims: {
      status: 'VALID', errors: [], normalized_sha256: 'a'.repeat(64), claim_count: 0,
      facts_pointer_binding_count: 0, free_text_field_count: 0, unsourced_claim_count: 0,
      trading_claim_count: 0, raw_qfq_mismatch_count: 0, sensitive_hit_count: 0, can_publish: false,
    },
    account: {
      initial_cash_cny: '100000', cash_cny: '100000', market_value_cny: '0', total_equity_cny: '100000',
      cumulative_return_percent: '0', realized_pnl_cny: '0', unrealized_pnl_cny: '0',
      current_drawdown_cny: '0', max_drawdown_cny: '0', pending_action: null,
    },
    positions: [], decisions: [], trades: [], equity_history: [],
    latest_daily: {
      report_date: '2026-09-08', research_signal: 'MIXED_OBSERVATION', paper_action: 'HOLD',
      pending_action: null, source_fixture: 'VALIDATED_DAILY_INPUT', risk_notes: [], next_observation_conditions: [],
    },
    chenquant_daily_markdown: null,
    research: {
      status: 'READY', trade_date: '2026-09-09', previous_trade_date: '2026-09-08',
      price_basis: 'qfq', source_provider: 'stocksdk_tencent', affects_paper_action: false,
      chan_status: 'READY', matched_count: 0, risk_count: 0, strategies: [],
      paper_rule: { signal: 'MIXED_OBSERVATION', positive_conditions: [] },
    },
  }
}

let client: QueryClient
let fetchSpy: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-10T16:01:00Z'))
  fetchSpy = vi.fn(() => { throw new Error('Unexpected network request in header test') })
  vi.stubGlobal('fetch', fetchSpy)
  vi.mocked(useWorkspaceStatus).mockReturnValue({
    mode: 'cloud', offlineReadonly: false, lastSuccessfulSync: '2026-09-10T16:00:00Z', dataAsOf: '2026-08-25',
  })
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } } })
  client.setQueryData(QK.version, { version: 'test' })
  client.setQueryData(QK.pipelineJobs, { active_id: null, jobs: [] })
  client.setQueryData(['alerts-total'], { total: 0, alerts: [] })
  client.setQueryData(QK.preferences, {})
  // Existing layout polling is outside the date display's network boundary.
  vi.spyOn(api, 'pipelineJobs').mockResolvedValue({ active_id: null, jobs: [] })
  vi.spyOn(api, 'alertsList').mockResolvedValue({ total: 0, alerts: [] })
})

afterEach(() => {
  cleanup()
  client.clear()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function renderHeader(dashboard?: PaperTradingDashboard) {
  if (dashboard) client.setQueryData(QK.paperTrading, dashboard)
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <QueryClientProvider client={client}><Layout /></QueryClientProvider>
    </MemoryRouter>,
  )
}

function expectDate(label: string, value: string) {
  expect(screen.getByLabelText(`${label} ${value}`)).toHaveTextContent(`${label} ${value}`)
}

describe('top workbench date semantics', () => {
  it('names the old workspace date as the stock daily indicator cache, not generic data', () => {
    renderHeader()
    expectDate('股票日线指标缓存日期', '2026-08-25')
    expect(screen.queryByText(/^数据 \d{4}-/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/^数据日期 /)).not.toBeInTheDocument()
  })

  it.each([
    ['2026-09-10T15:59:59Z', '2026-09-10'],
    ['2026-09-10T16:00:00Z', '2026-09-11'],
  ])('uses Shanghai system date at %s independently of cached dates', (instant, expected) => {
    vi.setSystemTime(new Date(instant))
    renderHeader(dashboardFixture())
    expectDate('系统日期', expected)
    expect(screen.getByLabelText(`系统日期 ${expected}`)).toHaveAttribute('title', 'Asia/Shanghai')
  })

  it('updates the system date across Shanghai midnight without a cache change', async () => {
    vi.setSystemTime(new Date('2026-09-10T15:59:30Z'))
    renderHeader()
    expectDate('系统日期', '2026-09-10')
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expectDate('系统日期', '2026-09-11')
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('keeps validated market, ready research and completed paper dates distinct', () => {
    renderHeader(dashboardFixture())
    expect(screen.getByText('000403.SZ 工作台缓存')).toBeInTheDocument()
    expectDate('最新行情', '2026-09-10')
    expectDate('最新研究', '2026-09-09')
    expectDate('最新模拟盘', '2026-09-08')
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('uses ready research input for market freshness when today has no prepared input', () => {
    const dashboard = dashboardFixture()
    dashboard.input_readiness = { ...dashboard.input_readiness, status: 'PREPARABLE', effective_trade_date: null }
    renderHeader(dashboard)
    expectDate('最新行情', '2026-09-09')
    expectDate('最新研究', '2026-09-09')
  })

  it.each(['NOT_READY', 'BLOCKED'] as const)('does not treat %s request dates as available research or market data', status => {
    const dashboard = dashboardFixture()
    dashboard.research = { status, trade_date: '2026-09-11' }
    dashboard.input_readiness = { ...dashboard.input_readiness, status: 'BLOCKED', effective_trade_date: '2026-09-11' }
    dashboard.last_completed_trade_date = null
    dashboard.latest_daily = null
    renderHeader(dashboard)
    expectDate('最新行情', '不可用')
    expectDate('最新研究', '不可用')
    expectDate('最新模拟盘', '不可用')
  })

  it('shows unavailable for a cold dashboard cache without starting a fetch', async () => {
    renderHeader()
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expectDate('最新行情', '不可用')
    expectDate('最新研究', '不可用')
    expectDate('最新模拟盘', '不可用')
    expect(client.getQueryState(QK.paperTrading)?.fetchStatus ?? 'idle').toBe('idle')
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('reacts to shared cache population, updates and removal without a second request workflow', async () => {
    renderHeader()
    const dashboard = dashboardFixture()
    await act(async () => { client.setQueryData(QK.paperTrading, dashboard) })
    expectDate('最新研究', '2026-09-09')
    await act(async () => {
      client.setQueryData(QK.paperTrading, {
        ...dashboard, research: { ...dashboard.research, trade_date: '2026-09-10' },
        last_completed_trade_date: '2026-09-10',
      })
    })
    expectDate('最新研究', '2026-09-10')
    expectDate('最新模拟盘', '2026-09-10')
    await act(async () => { client.removeQueries({ queryKey: QK.paperTrading, exact: true }) })
    expectDate('最新行情', '不可用')
    expectDate('最新研究', '不可用')
    expectDate('最新模拟盘', '不可用')
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('does not present a deterministic reference fixture as latest market data', () => {
    const dashboard = dashboardFixture()
    dashboard.research = undefined
    dashboard.input_readiness = {
      ...dashboard.input_readiness, status: 'ALREADY_PUBLISHED', source_fixture: 'DETERMINISTIC_REFERENCE_FIXTURE',
    }
    renderHeader(dashboard)
    expectDate('最新行情', '不可用')
    expectDate('最新研究', '不可用')
    expectDate('最新模拟盘', '2026-09-08')
  })

  it('preserves explicitly cached dates offline instead of substituting system or sync date', () => {
    vi.mocked(useWorkspaceStatus).mockReturnValue({
      mode: 'cloud', offlineReadonly: true, lastSuccessfulSync: '2026-09-11T00:00:00Z', dataAsOf: null,
    })
    renderHeader(dashboardFixture())
    expect(screen.getByText('离线只读')).toBeInTheDocument()
    expect(screen.getByText('000403.SZ 工作台缓存')).toBeInTheDocument()
    expectDate('最新研究', '2026-09-09')
    expectDate('股票日线指标缓存日期', '不可用')
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})
