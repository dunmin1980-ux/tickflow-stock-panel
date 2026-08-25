import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PaperAccount } from '../PaperAccount'
import { api, type PaperTradingDashboard } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    paperTradingDashboard: vi.fn(),
  },
}))

vi.mock('@/components/paper-trading/PaperEquityChart', () => ({
  PaperEquityChart: ({ points }: { points: unknown[] }) => (
    <div aria-label="权益曲线">chart:{points.length}</div>
  ),
}))

const ACCOUNT_DASHBOARD: PaperTradingDashboard = {
  status: 'VISUAL_WORKBENCH_READY',
  symbol: '000403.SZ',
  name: '派林生物',
  timezone: 'Asia/Shanghai',
  requested_date: '2026-08-25',
  last_completed_trade_date: '2026-08-25',
  safety: {
    simulation_only: 'SIMULATION ONLY',
    real_trading: 'DISABLED',
    can_publish: false,
    trading_advice: false,
  },
  input_readiness: {
    status: 'ALREADY_PUBLISHED',
    requested_date: '2026-08-25',
    effective_trade_date: '2026-08-25',
    source_fixture: 'VALIDATED_DAILY_INPUT',
    is_current_date: true,
    message: 'VALIDATED_DAILY_ALREADY_PUBLISHED',
  },
  claims: {
    status: 'VALID',
    errors: [],
    normalized_sha256: 'a'.repeat(64),
    claim_count: 21,
    facts_pointer_binding_count: 23,
    free_text_field_count: 0,
    unsourced_claim_count: 0,
    trading_claim_count: 0,
    raw_qfq_mismatch_count: 0,
    sensitive_hit_count: 0,
    can_publish: false,
    source_fixture: 'VALIDATED_DAILY_INPUT',
    trade_date: '2026-08-25',
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
    lot_id: 'b'.repeat(64),
    symbol: '000403.SZ',
    acquired_trade_date: '2026-07-30',
    sellable_from_trade_date: '2026-07-31',
    original_quantity: 100,
    remaining_quantity: 100,
    remaining_cost_cny: '21500.00',
  }],
  decisions: [
    {
      trade_date: '2026-07-31',
      decision_at: '2026-07-31T21:20:00+08:00',
      research_signal: 'MIXED_OBSERVATION',
      paper_action: 'HOLD',
      pending_execution: false,
      claims_sha256: 'c'.repeat(64),
      action_id: 'd'.repeat(64),
    },
    {
      trade_date: '2026-08-25',
      decision_at: '2026-08-25T16:37:59+08:00',
      research_signal: 'MIXED_OBSERVATION',
      paper_action: 'HOLD',
      pending_execution: false,
      claims_sha256: 'e'.repeat(64),
      action_id: 'f'.repeat(64),
    },
  ],
  trades: [{
    execution_id: '1'.repeat(64),
    trade_date: '2026-07-30',
    execution_timestamp: '2026-07-30T09:30:00+08:00',
    side: 'BUY',
    quantity: 100,
    execution_price: '215.00',
    fees_cny: '5.00',
    realized_pnl_cny: '0.00',
    symbol: '000403.SZ',
    status: 'EXECUTED',
  }],
  equity_history: [
    {
      trade_date: '2026-07-31',
      cash_cny: '80000.00',
      market_value_cny: '21500.00',
      realized_pnl_cny: '1500.00',
      unrealized_pnl_cny: '0.00',
      total_equity_cny: '101500.00',
      peak_equity_cny: '101500.00',
      current_drawdown_cny: '0.00',
      max_drawdown_cny: '0.00',
    },
    {
      trade_date: '2026-08-25',
      cash_cny: '80000.00',
      market_value_cny: '22000.00',
      realized_pnl_cny: '1500.00',
      unrealized_pnl_cny: '500.00',
      total_equity_cny: '102000.00',
      peak_equity_cny: '102300.00',
      current_drawdown_cny: '300.00',
      max_drawdown_cny: '800.00',
    },
  ],
  latest_daily: {
    report_date: '2026-08-25',
    research_signal: 'MIXED_OBSERVATION',
    paper_action: 'HOLD',
    pending_action: null,
    source_fixture: 'VALIDATED_DAILY_INPUT',
    risk_notes: ['SIMULATION_ONLY'],
    next_observation_conditions: ['LOAD_NEXT_VALIDATED_DAILY_INPUT'],
  },
  chenquant_daily_markdown: '# ChenQuant Paper Trading Daily\n\nSIMULATION ONLY.',
}

function renderPage(dashboard = ACCOUNT_DASHBOARD) {
  vi.mocked(api.paperTradingDashboard).mockResolvedValue(dashboard)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <PaperAccount />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('PaperAccount', () => {
  beforeEach(() => vi.clearAllMocks())

  it('answers how the account is doing across its lifetime', async () => {
    renderPage()

    expect(await screen.findByRole('heading', { name: '模拟账户' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '账户总览' })).toHaveTextContent('¥102,000.00')
    expect(screen.getByText('¥102,000.00')).toHaveClass('text-foreground')
    expect(screen.getByRole('region', { name: '账户总览' })).toHaveTextContent('¥80,000.00')
    expect(screen.getByRole('region', { name: '账户总览' })).toHaveTextContent('¥22,000.00')
    expect(screen.getByRole('region', { name: '账户总览' })).toHaveTextContent('+2.00%')
    expect(screen.getByRole('region', { name: '账户总览' })).toHaveTextContent('¥800.00')
    expect(screen.getByRole('region', { name: '当前持仓' })).toHaveTextContent('000403.SZ')
    expect(screen.getByRole('region', { name: '当前持仓' })).toHaveTextContent('派林生物')
    expect(screen.getByRole('region', { name: '当前持仓' })).toHaveTextContent('100 股')
    expect(screen.getByRole('region', { name: '当前持仓' })).toHaveTextContent('可卖 100 股')
    expect(screen.getByRole('region', { name: '当前持仓' })).toHaveTextContent('2026-07-30')
    expect(screen.getByRole('region', { name: '当前持仓' })).toHaveTextContent('2026-07-31')
    expect(screen.getByRole('region', { name: '历史交易' })).toHaveTextContent('EXECUTED')
    expect(screen.getByRole('region', { name: '历史交易' })).toHaveTextContent('¥5.00')
    expect(screen.getByRole('region', { name: '历史决策' })).toHaveTextContent('2026-07-31')
    expect(screen.getByRole('region', { name: '历史决策' })).toHaveTextContent('2026-08-25')
    expect(screen.getByRole('region', { name: '历史决策' })).toHaveTextContent('NO TRADE')
    expect(screen.getByRole('region', { name: '账户 PnL' })).toHaveTextContent('¥1,500.00')
    expect(screen.getByRole('region', { name: '账户 PnL' })).toHaveTextContent('¥500.00')
    expect(screen.getByLabelText('权益曲线')).toHaveTextContent('chart:2')
    expect(screen.getByRole('link', { name: '返回今日工作台' })).toHaveAttribute('href', '/paper-trading')
    expect(screen.queryByRole('button', { name: '运行今日模拟盘' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '今日待办' })).not.toBeInTheDocument()
  })

  it('shows an explicit empty account position state', async () => {
    renderPage({
      ...ACCOUNT_DASHBOARD,
      positions: [],
      account: {
        ...ACCOUNT_DASHBOARD.account,
        cash_cny: '100000.00',
        market_value_cny: '0.00',
        total_equity_cny: '100000.00',
        cumulative_return_percent: '0.0000',
        realized_pnl_cny: '0.00',
        unrealized_pnl_cny: '0.00',
        max_drawdown_cny: '0.00',
      },
      trades: [],
    })

    expect(await screen.findByRole('region', { name: '当前持仓' })).toHaveTextContent('当前空仓')
    expect(screen.getByRole('region', { name: '当前持仓' })).toHaveTextContent('可卖数量 0 股')
    expect(screen.getByRole('region', { name: '历史交易' })).toHaveTextContent('暂无模拟成交')
  })
})
