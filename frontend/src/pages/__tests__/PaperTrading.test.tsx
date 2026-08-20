import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PaperTrading } from '../PaperTrading'
import { api, type PaperTradingDashboard } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    paperTradingDashboard: vi.fn(),
    paperTradingRun: vi.fn(),
  },
}))

vi.mock('@/components/paper-trading/PaperEquityChart', () => ({
  PaperEquityChart: ({ points }: { points: unknown[] }) => (
    <div aria-label="权益曲线">chart:{points.length}</div>
  ),
}))

const BASE_DASHBOARD: PaperTradingDashboard = {
  status: 'VISUAL_WORKBENCH_READY',
  symbol: '000403.SZ',
  name: '派林生物',
  timezone: 'Asia/Shanghai',
  requested_date: '2026-08-20',
  last_completed_trade_date: '2026-07-31',
  safety: {
    simulation_only: 'SIMULATION ONLY',
    real_trading: 'DISABLED',
    can_publish: false,
    trading_advice: false,
  },
  input_readiness: {
    status: 'MISSING',
    requested_date: '2026-08-20',
    effective_trade_date: null,
    source_fixture: null,
    is_current_date: false,
    message: 'VALIDATED_DAILY_INPUT_REQUIRED',
  },
  claims: {
    status: 'VALID',
    errors: [],
    normalized_sha256: 'a'.repeat(64),
    claim_count: 11,
    facts_pointer_binding_count: 14,
    free_text_field_count: 0,
    unsourced_claim_count: 0,
    trading_claim_count: 0,
    raw_qfq_mismatch_count: 0,
    sensitive_hit_count: 0,
    can_publish: false,
  },
  account: {
    initial_cash_cny: '100000.00',
    cash_cny: '80000.00',
    market_value_cny: '22000.00',
    total_equity_cny: '102000.00',
    cumulative_return_percent: '2.0000',
    realized_pnl_cny: '1500.00',
    unrealized_pnl_cny: '500.00',
    current_drawdown_cny: '300.00',
    max_drawdown_cny: '800.00',
    pending_action: null,
  },
  positions: [{
    lot_schema_version: 1,
    lot_id: 'b'.repeat(64),
    source_action_id: 'c'.repeat(64),
    symbol: '000403.SZ',
    acquired_trade_date: '2026-07-30',
    sellable_from_trade_date: '2026-07-31',
    original_quantity: 100,
    remaining_quantity: 100,
    remaining_cost_cny: '21500.00',
    simulation_only: 'SIMULATION ONLY',
    can_publish: false,
    trading_advice: false,
  }],
  decisions: [{
    decision_entry_schema_version: 1,
    trade_date: '2026-07-31',
    decision_at: '2026-07-31T21:20:00+08:00',
    input_identity: 'd'.repeat(64),
    facts_sha256: 'e'.repeat(64),
    projection_sha256: 'f'.repeat(64),
    claims_sha256: '1'.repeat(64),
    fixture_identity: '2'.repeat(64),
    signal_id: '3'.repeat(64),
    action_id: '4'.repeat(64),
    order_id: null,
    research_signal: 'MIXED_OBSERVATION',
    paper_action: 'HOLD',
    pending_execution: false,
    simulation_only: 'SIMULATION ONLY',
    can_publish: false,
    trading_advice: false,
  }],
  trades: [],
  equity_history: [{
    equity_point_schema_version: 1,
    trade_date: '2026-07-31',
    cash_cny: '80000.00',
    market_value_cny: '22000.00',
    realized_pnl_cny: '1500.00',
    unrealized_pnl_cny: '500.00',
    total_equity_cny: '102000.00',
    peak_equity_cny: '102300.00',
    current_drawdown_cny: '300.00',
    max_drawdown_cny: '800.00',
    simulation_only: 'SIMULATION ONLY',
    can_publish: false,
    trading_advice: false,
  }],
  latest_daily: {
    report_date: '2026-07-31',
    research_signal: 'MIXED_OBSERVATION',
    paper_action: 'HOLD',
    pending_action: null,
    source_fixture: 'DETERMINISTIC_REFERENCE_FIXTURE',
    risk_notes: ['SIMULATION_ONLY'],
    next_observation_conditions: ['LOAD_NEXT_VALIDATED_DAILY_INPUT'],
  },
  chenquant_daily_markdown: '# ChenQuant Paper Trading Daily\n\nSIMULATION ONLY.',
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <PaperTrading />
    </QueryClientProvider>,
  )
}

describe('PaperTrading', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.paperTradingDashboard).mockResolvedValue(BASE_DASHBOARD)
  })

  it('renders persisted account, safety, research, ledger, chart, and Daily state', async () => {
    renderPage()

    expect(await screen.findByText('模拟投研工作台')).toBeInTheDocument()
    expect(screen.getByText('¥102,000.00')).toBeInTheDocument()
    expect(screen.getByText('¥80,000.00')).toBeInTheDocument()
    expect(screen.getByText('¥22,000.00')).toBeInTheDocument()
    expect(screen.getByText('+2.00%')).toBeInTheDocument()
    expect(screen.getByText('¥800.00')).toBeInTheDocument()
    expect(screen.getAllByText('SIMULATION ONLY').length).toBeGreaterThan(0)
    expect(screen.getByText('REAL TRADING DISABLED')).toBeInTheDocument()
    expect(screen.getAllByText('MIXED OBSERVATION').length).toBeGreaterThan(0)
    expect(screen.getAllByText('HOLD').length).toBeGreaterThan(0)
    expect(screen.getByText('VALID / 11 claims')).toBeInTheDocument()
    expect(screen.getByText('100 股')).toBeInTheDocument()
    expect(screen.getByLabelText('权益曲线')).toHaveTextContent('chart:1')
    expect(screen.getByText('ChenQuant Paper Trading Daily')).toBeInTheDocument()
  })

  it('blocks a run when current validated input is missing', async () => {
    renderPage()

    const button = await screen.findByRole('button', { name: '运行今日模拟盘' })
    expect(button).toBeDisabled()
    expect(screen.getByText('当前日期缺少已验证的离线输入')).toBeInTheDocument()
    expect(api.paperTradingRun).not.toHaveBeenCalled()
  })

  it('runs an available day once and updates the dashboard without a reload', async () => {
    let finish!: (value: PaperTradingDashboard & { run_status: string }) => void
    const pending = new Promise<PaperTradingDashboard & { run_status: string }>(resolve => {
      finish = resolve
    })
    const ready = {
      ...BASE_DASHBOARD,
      input_readiness: {
        status: 'READY_REFERENCE' as const,
        requested_date: '2026-08-20',
        effective_trade_date: '2026-07-31',
        source_fixture: 'DETERMINISTIC_REFERENCE_FIXTURE',
        is_current_date: false,
        message: 'FROZEN_REFERENCE_INITIALIZATION_AVAILABLE',
      },
      last_completed_trade_date: null,
      decisions: [],
      equity_history: [],
      latest_daily: null,
      chenquant_daily_markdown: null,
    }
    vi.mocked(api.paperTradingDashboard).mockResolvedValue(ready)
    vi.mocked(api.paperTradingRun).mockReturnValue(pending)
    renderPage()

    const button = await screen.findByRole('button', { name: '运行今日模拟盘' })
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.click(button)
    fireEvent.click(button)

    await waitFor(() => expect(api.paperTradingRun).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(button).toBeDisabled())

    finish({ ...BASE_DASHBOARD, run_status: 'DAY_PUBLISHED' })
    await screen.findByText('本次已写入日结')
    expect(screen.getAllByText('MIXED OBSERVATION').length).toBeGreaterThan(0)
  })

  it('shows a friendly run error without hiding the persisted state', async () => {
    const ready = {
      ...BASE_DASHBOARD,
      input_readiness: {
        status: 'READY_REFERENCE' as const,
        requested_date: '2026-08-20',
        effective_trade_date: '2026-07-31',
        source_fixture: 'DETERMINISTIC_REFERENCE_FIXTURE',
        is_current_date: false,
        message: 'FROZEN_REFERENCE_INITIALIZATION_AVAILABLE',
      },
    }
    vi.mocked(api.paperTradingDashboard).mockResolvedValue(ready)
    vi.mocked(api.paperTradingRun).mockRejectedValue(new Error('模拟盘引擎拒绝了本次输入'))
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '运行今日模拟盘' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('模拟盘引擎拒绝了本次输入')
    expect(screen.getByText('¥102,000.00')).toBeInTheDocument()
  })
})
