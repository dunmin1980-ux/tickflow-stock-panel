import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
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
    source_fixture: 'DETERMINISTIC_REFERENCE_FIXTURE',
    trade_date: '2026-07-31',
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
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <PaperTrading />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('PaperTrading', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.paperTradingDashboard).mockResolvedValue(BASE_DASHBOARD)
  })

  it('answers what the user should do today without rendering account-lifetime tables', async () => {
    vi.mocked(api.paperTradingDashboard).mockResolvedValue({
      ...BASE_DASHBOARD,
      requested_date: '2026-08-25',
      last_completed_trade_date: '2026-08-25',
      input_readiness: {
        status: 'ALREADY_PUBLISHED',
        requested_date: '2026-08-25',
        effective_trade_date: '2026-08-25',
        source_fixture: 'VALIDATED_DAILY_INPUT',
        is_current_date: true,
        message: 'VALIDATED_DAILY_ALREADY_PUBLISHED',
      },
      claims: {
        ...BASE_DASHBOARD.claims,
        claim_count: 21,
        trade_date: '2026-08-25',
      },
      account: {
        ...BASE_DASHBOARD.account,
        cash_cny: '100000.00',
        market_value_cny: '0.00',
        total_equity_cny: '100000.00',
        cumulative_return_percent: '0.0000',
        realized_pnl_cny: '0.00',
        unrealized_pnl_cny: '0.00',
        max_drawdown_cny: '0.00',
      },
      positions: [],
      decisions: [
        BASE_DASHBOARD.decisions[0],
        {
          ...BASE_DASHBOARD.decisions[0],
          trade_date: '2026-08-25',
          decision_at: '2026-08-25T16:37:59+08:00',
          action_id: '5'.repeat(64),
        },
      ],
      equity_history: [
        BASE_DASHBOARD.equity_history[0],
        {
          ...BASE_DASHBOARD.equity_history[0],
          trade_date: '2026-08-25',
          cash_cny: '100000.00',
          market_value_cny: '0.00',
          total_equity_cny: '100000.00',
          realized_pnl_cny: '0.00',
          unrealized_pnl_cny: '0.00',
        },
      ],
      latest_daily: {
        ...BASE_DASHBOARD.latest_daily!,
        report_date: '2026-08-25',
        source_fixture: 'VALIDATED_DAILY_INPUT',
        risk_notes: ['SIMULATION_ONLY', 'REAL_TRADING_DISABLED'],
        next_observation_conditions: ['LOAD_NEXT_VALIDATED_DAILY_INPUT'],
      },
    })

    renderPage()

    expect(await screen.findByRole('heading', { name: '今日工作台' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '今日市场状态' })).toHaveTextContent('2026-08-25')
    expect(screen.getByRole('region', { name: '今日市场状态' })).toHaveTextContent('A 股交易日')
    expect(screen.getByRole('region', { name: '今日市场状态' })).toHaveTextContent('数据 READY')
    expect(screen.getByRole('region', { name: '今日市场状态' })).toHaveTextContent('Backend Online')
    expect(screen.getByRole('region', { name: '今日 Research Signal' })).toHaveTextContent('MIXED OBSERVATION')
    expect(screen.getByRole('region', { name: '今日 Research Signal' })).toHaveTextContent('VALID / 21')
    expect(screen.getByRole('region', { name: '今日 Paper Action' })).toHaveTextContent('HOLD')
    expect(screen.getByRole('region', { name: '今日 Paper Action' })).toHaveTextContent('无需交易')
    expect(screen.getByRole('region', { name: '今日持仓' })).toHaveTextContent('当前空仓')
    expect(screen.getByRole('region', { name: '今日 PnL' })).toHaveTextContent('今日权益变化')
    expect(screen.getByRole('region', { name: '今日待办' })).toHaveTextContent('今日决策已完成')
    expect(screen.getByRole('region', { name: '今日待办' })).toHaveTextContent('无需交易，继续观察')
    expect(screen.getByRole('region', { name: 'ChenQuant Daily 摘要' })).toHaveTextContent('LOAD NEXT VALIDATED DAILY INPUT')
    expect(screen.getByRole('link', { name: '查看完整日报' })).toHaveAttribute('href', '/review')
    expect(screen.getByRole('region', { name: '最近 Signal Timeline' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '历史交易' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '历史决策' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '账户总览' })).not.toBeInTheDocument()
  })

  it('keeps stale research in the timeline instead of presenting it as today', async () => {
    renderPage()

    expect(await screen.findByText('今日工作台')).toBeInTheDocument()
    expect(screen.getAllByText('SIMULATION ONLY').length).toBeGreaterThan(0)
    expect(screen.getByText('REAL TRADING DISABLED')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '今日 Research Signal' })).toHaveTextContent('--')
    expect(screen.getByRole('region', { name: '今日 Research Signal' })).toHaveTextContent('Claims 待生成')
    expect(screen.getByRole('region', { name: '今日 Paper Action' })).toHaveTextContent('等待今日确定性决策')
    expect(screen.getByRole('region', { name: 'ChenQuant Daily 摘要' })).toHaveTextContent('今日复盘尚未生成')
    expect(screen.getByRole('region', { name: '最近 Signal Timeline' })).toHaveTextContent('MIXED OBSERVATION')
    expect(screen.getByRole('region', { name: '最近 Signal Timeline' })).toHaveTextContent('HOLD')
  })

  it('does not label stale Claims as current when a current Daily exists', async () => {
    vi.mocked(api.paperTradingDashboard).mockResolvedValue({
      ...BASE_DASHBOARD,
      requested_date: '2026-08-20',
      latest_daily: {
        ...BASE_DASHBOARD.latest_daily!,
        report_date: '2026-08-20',
      },
      claims: {
        ...BASE_DASHBOARD.claims,
        trade_date: '2026-07-31',
      },
    })

    renderPage()

    expect(await screen.findByRole('region', { name: '今日 Research Signal' })).toHaveTextContent('Claims 待生成')
    expect(screen.getByRole('region', { name: 'ChenQuant Daily 摘要' })).toHaveTextContent('Claims 待生成')
    expect(screen.getByRole('region', { name: 'ChenQuant Daily 摘要' })).not.toHaveTextContent('VALID / 11')
  })

  it('blocks a run when current validated input is missing', async () => {
    renderPage()

    const button = await screen.findByRole('button', { name: '运行今日模拟盘' })
    expect(button).toBeDisabled()
    expect(screen.getAllByText('今日输入尚未准备').length).toBeGreaterThan(0)
    expect(screen.getByText('缺少已验证的日线研究输入')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '今日 PnL' })).toHaveTextContent('今日权益变化 --')
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
        status: 'PREPARABLE' as const,
        requested_date: '2026-08-20',
        effective_trade_date: null,
        source_fixture: 'VALIDATED_DAILY_INPUT',
        is_current_date: true,
        message: 'VALIDATED_DAILY_INPUT_CAN_BE_PREPARED',
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
    await screen.findByText('今日模拟盘已完成')
    expect(screen.getAllByText('MIXED OBSERVATION').length).toBeGreaterThan(0)
  })

  it('shows a friendly run error without hiding the persisted state', async () => {
    const ready = {
      ...BASE_DASHBOARD,
      input_readiness: {
        status: 'PREPARABLE' as const,
        requested_date: '2026-08-20',
        effective_trade_date: null,
        source_fixture: 'VALIDATED_DAILY_INPUT',
        is_current_date: true,
        message: 'VALIDATED_DAILY_INPUT_CAN_BE_PREPARED',
      },
    }
    vi.mocked(api.paperTradingDashboard).mockResolvedValue(ready)
    vi.mocked(api.paperTradingRun).mockRejectedValue(new Error('模拟盘引擎拒绝了本次输入'))
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '运行今日模拟盘' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('模拟盘引擎拒绝了本次输入')
    expect(screen.getByText('¥102,000.00')).toBeInTheDocument()
  })

  it('explains that the backend is not running when the dashboard is unreachable', async () => {
    vi.mocked(api.paperTradingDashboard).mockRejectedValue(new TypeError('Failed to fetch'))

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'TickFlow 后端未运行，当前为只读模式',
    )
  })

  it('keeps the one-click run disabled until the A-share close gate', async () => {
    vi.mocked(api.paperTradingDashboard).mockResolvedValue({
      ...BASE_DASHBOARD,
      input_readiness: {
        status: 'WAITING_FOR_CLOSE',
        requested_date: '2026-08-20',
        effective_trade_date: null,
        source_fixture: 'VALIDATED_DAILY_INPUT',
        is_current_date: true,
        message: 'WAIT_UNTIL_15_10',
      },
    })
    renderPage()

    const button = await screen.findByRole('button', { name: '运行今日模拟盘' })
    expect(button).toBeDisabled()
    expect(screen.getAllByText('收盘后可运行').length).toBeGreaterThan(0)
    expect(screen.getByText('15:10 后准备今日研究输入')).toBeInTheDocument()
    expect(api.paperTradingRun).not.toHaveBeenCalled()
  })

  it('does not send another request after the day is already published', async () => {
    vi.mocked(api.paperTradingDashboard).mockResolvedValue({
      ...BASE_DASHBOARD,
      input_readiness: {
        status: 'ALREADY_PUBLISHED',
        requested_date: '2026-08-20',
        effective_trade_date: '2026-08-20',
        source_fixture: 'VALIDATED_DAILY_INPUT',
        is_current_date: true,
        message: 'VALIDATED_DAILY_ALREADY_PUBLISHED',
      },
    })
    renderPage()

    const button = await screen.findByRole('button', { name: '运行今日模拟盘' })
    expect(button).toBeDisabled()
    expect(screen.getByText('今日决策已完成')).toBeInTheDocument()
    expect(screen.getByText('再次运行不会产生重复交易')).toBeInTheDocument()
    fireEvent.click(button)
    expect(api.paperTradingRun).not.toHaveBeenCalled()
  })
})
