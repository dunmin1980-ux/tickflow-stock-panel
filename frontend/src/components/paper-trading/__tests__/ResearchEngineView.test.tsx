import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ResearchPanel } from '../ResearchPanel'
import { PaperTrading } from '@/pages/PaperTrading'
import { api, type PaperResearchPanel, type PaperTradingDashboard } from '@/lib/api'
import type { Condition, ResearchEngine, StrategyResearch } from '@/lib/research-engine'

vi.mock('@/lib/api', () => ({
  api: { paperTradingDashboard: vi.fn(), paperTradingRun: vi.fn() },
}))

const condition: Condition = {
  label: '量比', required: '量比 > 1.5', actual: '量比=1.38', passed: false,
  observed: 1.38, threshold: 1.5, operator: 'gt', unit: 'RATIO', margin: -0.12,
  gap: 0.12, temporal_scope: 'CURRENT_DAY',
}
const priceCondition: Condition = {
  label: '收盘与上轨', required: '收盘 > BOLL 上轨', actual: '收盘=18.6 / 上轨=18.5',
  passed: true, observed: 0.1, threshold: 0, operator: 'gt', unit: 'QFQ_PRICE',
  margin: 0.1, gap: 0, temporal_scope: 'CURRENT_DAY',
}

function engineFixture(): ResearchEngine {
  const rules = [
    ['macd_golden', 'MACD 金叉放量', 'NEUTRAL'],
    ['bullish_alignment', '均线多头排列', 'BULLISH'],
    ['boll_breakout', 'BOLL 突破', 'SETUP'],
    ['volume_price_surge', '量价齐升', 'NEUTRAL'],
    ['pullback_to_support', '回踩支撑', 'BEARISH'],
    ['boll_lower_reclaim', 'BOLL 下轨收复', 'SETUP'],
  ] as const
  const strategies: StrategyResearch[] = rules.map(([strategy_id, strategy_name, research_bias]) => ({
    strategy_id, strategy_name, research_bias, trade_date: '2026-09-09', timeframe: '1d',
    price_basis: 'QFQ', status: 'NOT_TRIGGERED', conditions_total: 2, conditions_met: 1,
    condition_ratio: 0.5, evidence: [priceCondition, condition], failed_conditions: [condition],
    distance_to_trigger: [condition], risk_triggered: research_bias === 'BEARISH',
    delta_vs_previous: {
      previous_conditions_met: 0, conditions_met_delta: 1, change: 'IMPROVING',
      conditions: [{ label: '量比', previous: 1.2, current: 1.38, value_delta: 0.18,
        margin_delta: 0.18, change: 'IMPROVING', unit: 'RATIO' }],
    },
    calculation_source: `builtin.${strategy_id}`, parameters: { volume_ratio_min: 1.5 },
  }))
  strategies[2].status = 'NEAR_TRIGGER'
  const chan = {
    mode: 'CHAN_DAILY_STRUCTURE_V1' as const, trade_date: '2026-09-09',
    current_structure: 'RANGE_STRUCTURE' as const, previous_structure: 'DOWN_STRUCTURE' as const,
    change: 'WEAKENING' as const, structure_high: 19.75, structure_low: 17.25,
    latest_close: 18.6, price_location: 'INSIDE_CONFIRMED_RANGE' as const,
    top_fractals: [{ type: 'TOP_FRACTAL' as const, date: '2026-09-04', confirmed_at: '2026-09-07', price: 19.75 }],
    bottom_fractals: [{ type: 'BOTTOM_FRACTAL' as const, date: '2026-09-08', confirmed_at: '2026-09-09', price: 17.25 }],
    stroke_candidates: [{ type: 'STROKE_CANDIDATE' as const, direction: 'DOWN_STROKE' as const,
      start_date: '2026-09-04', end_date: '2026-09-08', start_price: 19.75, end_price: 17.25,
      confirmed_at: '2026-09-09', source_bar_separation: 2 }],
    central_zone: 'DEFERRED' as const,
  }
  const confidence = { category: 'LOW' as const, basis: '方向证据不一致，存在策略冲突。', is_probability: false as const }
  const closest = {
    strategy_id: 'boll_breakout', strategy_name: 'BOLL 突破', conditions_met: 1,
    conditions_total: 2, condition_ratio: 0.5, distance_to_trigger: [condition],
  }
  return {
    schema_version: 1, symbol: '000403.SZ', trade_date: '2026-09-09', timeframe: '1d', price_basis: 'QFQ',
    indicators: { macd_dif: 0.0105, macd_dea: 0.047, macd_hist: -0.073, rsi6: 50.0891 },
    strategies, chan_structure: chan,
    aggregate: {
      bullish_count: 1, bearish_count: 1, neutral_count: 2, setup_count: 2,
      triggered_strategies: [], near_trigger_strategies: ['boll_breakout'],
      supporting_factors: ['均线方向偏强'], limiting_factors: ['量比未达阈值'],
      conflicts: ['均线方向与回踩风险存在分歧'], closest_trigger: closest,
      research_consensus: 'MIXED', research_confidence: confidence, rule_trace: ['方向锚点分歧 -> MIXED'],
      delta_vs_previous: { trade_date: '2026-09-08', previous_consensus: 'BEARISH_WITH_CONFLICTS',
        current_consensus: 'MIXED', changed: true },
    },
    summary: {
      symbol: '000403.SZ', trade_date: '2026-09-09', research_consensus: 'MIXED',
      research_confidence: confidence, paper_action: 'HOLD', position_state: 'FLAT',
      hold_reason: 'FLAT_WAIT', hold_explanation: ['MACD 与 RSI6 未同时满足；当前空仓。'],
      supporting_factors: ['均线方向偏强'], limiting_factors: ['量比未达阈值'],
      strategy_conflicts: ['均线方向与回踩风险存在分歧'], closest_trigger: closest, chan_structure: chan,
      changes_vs_previous: [{ id: 'volume', label: '量比变化', previous: 1.2, current: 1.38,
        delta: 0.18, unit: 'RATIO', direction: 'IMPROVING' }],
      next_observation_conditions: ['观察量比能否超过 1.5'], claims_count: 21,
      data_quality: { source_provider: 'stocksdk_tencent_fallback', price_basis: 'QFQ',
        source_hashes: { qfq: 'a'.repeat(64) }, vendor_pending: ['绝对成交量单位待确认'],
        claims_status: 'VALID', financial_data: 'NOT_CONNECTED', material_news: 'NOT_CONNECTED' },
    },
    daily_markdown: '# Research Daily\n\n2026-09-09 / MIXED\n\n仅供研究，不构成投资建议。',
    safety: { research_only: true, simulation_only: true, can_publish: false,
      trading_advice: false, real_trading: 'DISABLED' },
  }
}

function panelFixture(engine?: ResearchEngine): PaperResearchPanel {
  return {
    status: 'READY', trade_date: '2026-09-09', previous_trade_date: '2026-09-08', price_basis: 'qfq',
    source_provider: 'stocksdk_tencent_fallback', affects_paper_action: false, chan_status: engine ? 'READY' : 'NOT_IMPLEMENTED',
    matched_count: 0, risk_count: 0, engine,
    strategies: [{ id: 'legacy', name: '旧缓存规则', description: '旧缓存说明', status: 'NOT_MATCHED',
      previous_status: 'NOT_MATCHED', change: 'UNCHANGED', conditions: [], matched_conditions: 0,
      risk_triggered: false, calculation_source: 'builtin.legacy' }],
    paper_rule: { signal: 'MIXED_OBSERVATION', positive_conditions: [] },
  }
}

function renderPanel(engine = engineFixture(), requestedDate = '2026-09-09') {
  return render(<ResearchPanel research={panelFixture(engine)} requestedDate={requestedDate} />)
}

function renderWorkbench(engine = engineFixture(), requestedDate = '2026-09-09') {
  const dashboard: PaperTradingDashboard = {
    status: 'VISUAL_WORKBENCH_READY', symbol: '000403.SZ', name: '派林生物', timezone: 'Asia/Shanghai',
    requested_date: requestedDate, last_completed_trade_date: '2026-09-09',
    safety: { simulation_only: 'SIMULATION ONLY', real_trading: 'DISABLED', can_publish: false, trading_advice: false },
    input_readiness: { status: 'MISSING', requested_date: requestedDate, effective_trade_date: null,
      source_fixture: null, is_current_date: false, message: 'VALIDATED_DAILY_INPUT_REQUIRED' },
    claims: { status: 'VALID', errors: [], normalized_sha256: 'a'.repeat(64), claim_count: 21,
      facts_pointer_binding_count: 21, free_text_field_count: 0, unsourced_claim_count: 0,
      trading_claim_count: 0, raw_qfq_mismatch_count: 0, sensitive_hit_count: 0,
      can_publish: false, source_fixture: 'VALIDATED_DAILY_INPUT', trade_date: '2026-09-09' },
    account: { initial_cash_cny: '100000', cash_cny: '100000', market_value_cny: '0',
      total_equity_cny: '100000', cumulative_return_percent: '0', realized_pnl_cny: '0',
      unrealized_pnl_cny: '0', current_drawdown_cny: '0', max_drawdown_cny: '0', pending_action: null },
    positions: [], decisions: [], trades: [], equity_history: [], latest_daily: null,
    chenquant_daily_markdown: null, research: panelFixture(engine),
  }
  vi.mocked(api.paperTradingDashboard).mockResolvedValue(dashboard)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><QueryClientProvider client={client}><PaperTrading /></QueryClientProvider></MemoryRouter>)
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllMocks() })

describe('Research Engine v1', () => {
  it('adds shared strategy cards and graphic structure to the workbench without running daily', async () => {
    renderWorkbench()
    expect(await screen.findByRole('region', { name: '六策略总览' })).toBeVisible()
    expect(screen.getByRole('link', { name: /完整个股研究/ })).toHaveAttribute('href', '/stock-research')
    expect(api.paperTradingRun).not.toHaveBeenCalled()
  })

  it('places current mixed research below safety and before status grids, separately from flat HOLD', async () => {
    renderWorkbench()
    const overview = await screen.findByRole('region', { name: 'Research Overview' })
    expect(overview).toHaveTextContent('MIXED')
    expect(overview).toHaveTextContent('HOLD')
    expect(overview).toHaveTextContent('FLAT_WAIT · 空仓观望')
    expect(overview).toHaveTextContent('Research ≠ Paper Action')
    expect(overview).toHaveTextContent('证据一致性')
    expect(overview).toHaveTextContent('不是预测概率')
    expect(overview).toHaveTextContent('方向证据不一致，存在策略冲突。')
    expect(screen.getByText('REAL TRADING DISABLED').compareDocumentPosition(overview) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(overview.compareDocumentPosition(screen.getByRole('region', { name: '今日市场状态' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByRole('button', { name: '运行今日模拟盘' })).toBeDisabled()
    expect(api.paperTradingRun).not.toHaveBeenCalled()
  })

  it('renders exactly six strategies and a Chan row instead of legacy cards', () => {
    renderPanel()
    const matrix = screen.getByRole('table', { name: '七项研究矩阵' })
    expect(within(matrix).getAllByRole('row')).toHaveLength(8)
    expect(within(matrix).getAllByRole('columnheader').map(el => el.textContent)).toEqual([
      '研究项 / 状态', '条件满足', '研究偏向', '距触发条件', '较前日', '证据',
    ])
    expect(within(matrix).getByRole('row', { name: /BOLL 突破/ })).toHaveTextContent('1/2')
    expect(within(matrix).getByRole('row', { name: /BOLL 突破/ })).toHaveTextContent('SETUP')
    expect(screen.queryByText('旧缓存规则')).not.toBeInTheDocument()
  })

  it('keeps old cached responses on the existing fallback', () => {
    render(<ResearchPanel research={panelFixture()} requestedDate="2026-09-09" />)
    expect(screen.getByText('旧缓存规则')).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: '七项研究矩阵' })).not.toBeInTheDocument()
  })

  it('distinguishes held HOLD from empty-position waiting', async () => {
    const engine = engineFixture()
    Object.assign(engine.summary, { position_state: 'HOLDING', hold_reason: 'POSITION_HOLD',
      hold_explanation: ['当前模拟持仓不变，没有新增模拟订单。'] })
    renderWorkbench(engine)
    const overview = await screen.findByRole('region', { name: 'Research Overview' })
    expect(overview).toHaveTextContent('POSITION_HOLD · 持仓不变')
    expect(overview).not.toHaveTextContent('FLAT_WAIT')
    expect(overview).toHaveTextContent('当前模拟持仓不变，没有新增模拟订单。')
  })

  it('does not invent HOLD when a date has no persisted action', async () => {
    const engine = engineFixture()
    Object.assign(engine.summary, { paper_action: 'NOT_RUN', position_state: 'UNKNOWN',
      hold_reason: 'NOT_RUN', hold_explanation: ['该日尚无已发布模拟记录。'] })
    renderWorkbench(engine)
    const overview = await screen.findByRole('region', { name: 'Research Overview' })
    expect(overview).toHaveTextContent('NOT_RUN · 未形成动作')
    expect(overview).not.toHaveTextContent('空仓观望')
    expect(overview).not.toHaveTextContent('持仓不变')
  })

  it('shows unavailable evidence as unknown, never zero or a negative signal', () => {
    const engine = engineFixture()
    const missing = { ...condition, passed: null, observed: null, margin: null, gap: null, actual: '日线长度不足' }
    Object.assign(engine.strategies[0], { status: 'NOT_AVAILABLE', conditions_met: 0, conditions_total: 1,
      condition_ratio: 0, evidence: [missing], failed_conditions: [missing], distance_to_trigger: [missing],
      research_bias: 'NEUTRAL', risk_triggered: null,
      delta_vs_previous: { previous_conditions_met: 0, conditions_met_delta: 0, change: 'NOT_AVAILABLE', conditions: [] } })
    renderPanel(engine)
    const row = screen.getByRole('row', { name: /MACD 金叉放量/ })
    expect(row).toHaveTextContent('NOT_AVAILABLE')
    expect(row).toHaveTextContent('数据不足')
    expect(row).toHaveTextContent('差值不可用')
    expect(row).not.toHaveTextContent('未触发')
    fireEvent.click(within(row).getByRole('button', { name: /展开/ }))
    expect(screen.getByText('日线长度不足')).toBeVisible()
  })

  it('shows numeric near-trigger gaps with their original units', () => {
    renderPanel()
    const row = screen.getByRole('row', { name: /BOLL 突破/ })
    expect(row).toHaveTextContent('NEAR_TRIGGER')
    expect(row).toHaveTextContent('量比：差 0.12 倍')
    expect(row).toHaveTextContent('IMPROVING')
    expect(row).toHaveTextContent('+1')
  })

  it('does not present prior-day predicates or strict equality as a current-price target', () => {
    const engine = engineFixture()
    engine.strategies[0].distance_to_trigger = [{ ...condition, label: '前日交叉', temporal_scope: 'PREVIOUS_DAY' }]
    engine.strategies[2].distance_to_trigger = [{ ...condition, observed: 1.5, gap: 0, margin: 0 }]
    renderPanel(engine)
    expect(screen.getByRole('row', { name: /MACD 金叉放量/ })).toHaveTextContent('前日条件，不可由今日补足')
    expect(screen.getByRole('row', { name: /BOLL 突破/ })).toHaveTextContent('严格阈值仍未满足')
  })

  it('expands actual conditions, thresholds, per-condition deltas and calculation inputs', () => {
    renderPanel()
    const toggle = screen.getByRole('button', { name: '展开 BOLL 突破 证据' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    const evidence = screen.getByRole('region', { name: 'BOLL 突破 证据' })
    expect(evidence).toHaveTextContent('量比=1.38')
    expect(evidence).toHaveTextContent('量比 > 1.5')
    expect(evidence).toHaveTextContent('收盘=18.6 / 上轨=18.5')
    expect(evidence).toHaveTextContent('1.2 → 1.38')
    expect(evidence).toHaveTextContent('+0.18')
    expect(evidence).toHaveTextContent('builtin.boll_breakout')
    expect(evidence).toHaveTextContent('volume_ratio_min')
    fireEvent.click(toggle)
    expect(screen.queryByRole('region', { name: 'BOLL 突破 证据' })).not.toBeInTheDocument()
  })

  it('makes Chan confirmations and candidate strokes inspectable without claiming a central zone', () => {
    renderPanel()
    const row = screen.getByRole('row', { name: /缠论日线结构/ })
    expect(row).toHaveTextContent('RANGE_STRUCTURE')
    expect(row).toHaveTextContent('WEAKENING')
    fireEvent.click(within(row).getByRole('button', { name: /展开/ }))
    const evidence = screen.getByRole('region', { name: '缠论日线结构 证据' })
    expect(evidence).toHaveTextContent('19.75')
    expect(evidence).toHaveTextContent('17.25')
    expect(evidence).toHaveTextContent('分型日期 2026-09-04 · 确认日期 2026-09-07')
    expect(evidence).toHaveTextContent('2026-09-04 → 2026-09-08')
    expect(evidence).toHaveTextContent('确认日期 2026-09-09')
    expect(evidence).toHaveTextContent('中枢：DEFERRED · 未实现')
    expect(evidence).toHaveTextContent('候选笔，不等同于完整缠论笔')
  })

  it('keeps conflicts, supporting factors and limits visible alongside closest conditions', async () => {
    renderWorkbench()
    const overview = await screen.findByRole('region', { name: 'Research Overview' })
    expect(overview).toHaveTextContent('均线方向与回踩风险存在分歧')
    expect(overview).toHaveTextContent('均线方向偏强')
    expect(overview).toHaveTextContent('量比未达阈值')
    expect(overview).toHaveTextContent('BOLL 突破')
    expect(overview).toHaveTextContent('量比：差 0.12 倍')
  })

  it('keeps Chan and closest conditions before compact overview lists while retaining every explanation', async () => {
    const engine = engineFixture()
    const lists = [
      ['supporting_factors', '支持因素'], ['limiting_factors', '限制因素'],
      ['strategy_conflicts', '策略分歧'], ['hold_explanation', '动作说明'],
    ] as const
    for (const [key, label] of lists) engine.summary[key] = [1, 2, 3, 4].map(index => `${label}证据 ${index}`)
    renderWorkbench(engine)
    const overview = await screen.findByRole('region', { name: 'Research Overview' })
    const chan = within(overview).getByText(/确认结构：整理/)
    const closest = within(overview).getByRole('heading', { name: '最近触发条件' })
    for (const [, label] of lists) {
      const first = within(overview).getByText(`${label}证据 1`)
      expect(first).toBeVisible()
      expect(within(overview).getByText(`${label}证据 2`)).toBeVisible()
      expect(within(overview).getByText(`${label}证据 3`)).not.toBeVisible()
      expect(within(overview).getByText(`${label}证据 4`)).not.toBeVisible()
      expect(chan.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      expect(closest.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      const toggle = within(overview).getByText(`更多${label}（2）`)
      fireEvent.click(toggle)
      expect(within(overview).getByText(`${label}证据 3`)).toBeVisible()
      expect(within(overview).getByText(`${label}证据 4`)).toBeVisible()
      fireEvent.click(toggle)
      expect(within(overview).getByText(`${label}证据 3`)).not.toBeVisible()
    }
  })

  it('summarizes daily MACD RSI and volume directions plus the first two actual observations above the matrix', async () => {
    const engine = engineFixture()
    engine.summary.changes_vs_previous = [
      { id: 'macd_hist', label: 'MACD 柱值', previous: -0.09, current: -0.073,
        delta: 0.017, unit: 'QFQ_PRICE', direction: 'IMPROVING' },
      { id: 'rsi6', label: 'RSI6', previous: 50, current: 40.8945,
        delta: -9.1055, unit: 'POINTS', direction: 'FALLING' },
      { id: 'volume_ratio', label: '相对量能', previous: 1.8, current: 1.38,
        delta: -0.42, unit: 'RATIO', direction: 'CONTRACTING' },
    ]
    engine.summary.next_observation_conditions = ['观察 DIF 能否高于 DEA；当前仍低于。',
      '观察 RSI6 是否回升至 50；当前 40.8945。', '观察后续收盘是否确认新的日线分型。']
    renderWorkbench(engine)
    const overview = await screen.findByRole('region', { name: 'Research Overview' })
    expect(within(overview).getByText('较前日：MACD：改善 / RSI6：下降 / 相对量能：缩小')).toBeVisible()
    expect(within(overview).getByText('观察 DIF 能否高于 DEA；当前仍低于。')).toBeVisible()
    expect(within(overview).getByText('观察 RSI6 是否回升至 50；当前 40.8945。')).toBeVisible()
    expect(within(overview).queryByText('观察后续收盘是否确认新的日线分型。')).not.toBeInTheDocument()
    const panel = screen.getByRole('region', { name: '日线多策略研究' })
    expect(within(panel).getByText('观察后续收盘是否确认新的日线分型。')).toBeVisible()
    expect(overview.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('does not infer daily directions or observation conditions when those inputs are absent', async () => {
    const engine = engineFixture()
    engine.summary.changes_vs_previous = []
    engine.summary.next_observation_conditions = []
    renderWorkbench(engine)
    const overview = await screen.findByRole('region', { name: 'Research Overview' })
    expect(within(overview).getByText('较前日：MACD：无法比较 / RSI6：无法比较 / 相对量能：无法比较')).toBeVisible()
    expect(within(overview).getByText('暂无已记录观察条件')).toBeVisible()
  })

  it.each([
    ['RISING', '上升'], ['FALLING', '下降'], ['EXPANDING', '放大'], ['CONTRACTING', '缩小'],
  ])('translates the %s metric delta without changing its numeric evidence', (direction, label) => {
    const engine = engineFixture()
    engine.summary.changes_vs_previous = [{ id: 'metric', label: '指标变化', previous: 1.2,
      current: 1.38, delta: 0.18, unit: 'RATIO', direction }]
    renderPanel(engine)
    fireEvent.click(screen.getByText('较前日指标变化'))
    const row = within(screen.getByRole('table', { name: '较前日指标变化' })).getByRole('row', { name: /指标变化/ })
    expect(row).toHaveTextContent(label)
    expect(row).toHaveTextContent('+0.18')
    expect(row).not.toHaveTextContent(direction)
  })

  it.each([1, 2])('marks unmet strict between at boundary %s despite a zero numeric gap', value => {
    const engine = engineFixture()
    engine.strategies[2].distance_to_trigger = [{ ...condition, operator: 'between',
      threshold: [1, 2], observed: value, gap: 0, margin: 0, passed: false }]
    renderPanel(engine)
    expect(screen.getByRole('row', { name: /BOLL 突破/ })).toHaveTextContent('严格阈值仍未满足')
  })

  it('separates the confirmed range from a current close below its low on the reported 9/9 snapshot', async () => {
    const engine = engineFixture()
    engine.summary.research_consensus = engine.aggregate.research_consensus = 'BEARISH_WITH_CONFLICTS'
    engine.summary.research_confidence.category = 'MODERATE'
    Object.assign(engine.chan_structure, { structure_high: 11.21, structure_low: 10.6,
      latest_close: 10.51, price_location: 'BELOW_CONFIRMED_RANGE' })
    engine.summary.closest_trigger = { strategy_id: 'pullback_to_support', strategy_name: '回踩支撑',
      conditions_met: 3, conditions_total: 4, condition_ratio: 0.75,
      distance_to_trigger: [{ ...condition, label: '动量', gap: 3.75458, unit: 'PERCENTAGE_POINTS' }] }
    renderWorkbench(engine)
    const overview = await screen.findByRole('region', { name: 'Research Overview' })
    expect(overview).toHaveTextContent('BEARISH_WITH_CONFLICTS')
    expect(overview).toHaveTextContent('MODERATE')
    expect(overview).toHaveTextContent('研究日期 2026-09-09')
    expect(overview).toHaveTextContent('动量：差 3.75458 个百分点')
    expect(overview).toHaveTextContent('确认结构：整理')
    expect(overview).toHaveTextContent('收盘低于最近确认低点，待后续分型确认')
    expect(overview).toHaveTextContent('10.51')
    expect(overview).not.toHaveTextContent('收盘位于最近确认区间内')
    const row = screen.getByRole('row', { name: /缠论日线结构/ })
    expect(row).toHaveTextContent('BELOW_CONFIRMED_RANGE')
    expect(row).toHaveTextContent('10.6')
  })

  it('labels tomorrow viewing as a dated snapshot without presenting historical HOLD as today', async () => {
    renderWorkbench(engineFixture(), '2026-09-10')
    const overview = await screen.findByRole('region', { name: 'Research Overview' })
    expect(within(overview).getByRole('status')).toHaveTextContent('尚非 2026-09-10 当日结果')
    expect(overview).toHaveTextContent('历史模拟记录 · 2026-09-09')
    expect(within(overview).queryByText('今日研究总览')).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: '今日 Paper Action' })).toHaveTextContent('等待今日确定性决策')
    expect(screen.getByRole('region', { name: '日线多策略研究' })).toHaveTextContent('尚非 2026-09-10 当日结果')
  })

  it('exposes daily changes, next observations, source hashes and disconnected datasets', () => {
    renderPanel()
    fireEvent.click(screen.getByText('较前日指标变化'))
    expect(screen.getByRole('table', { name: '较前日指标变化' })).toHaveTextContent('量比变化')
    expect(screen.getByRole('table', { name: '较前日指标变化' })).toHaveTextContent('+0.18')
    expect(screen.getByText('观察量比能否超过 1.5')).toBeVisible()
    fireEvent.click(screen.getByText('数据质量与规则追踪'))
    expect(screen.getByText('a'.repeat(64))).toBeVisible()
    expect(screen.getByText('方向锚点分歧 -> MIXED')).toBeVisible()
    expect(screen.getByText(/财务数据：NOT_CONNECTED/)).toBeVisible()
    expect(screen.getByText(/重大新闻：NOT_CONNECTED/)).toBeVisible()
    expect(screen.getByText('绝对成交量单位待确认')).toBeVisible()
  })

  it('shows all four engine indicators as supplied, not reconstructed from strategy conditions', async () => {
    renderWorkbench()
    const overview = await screen.findByRole('region', { name: 'Research Overview' })
    const indicators = within(overview).getByRole('group', { name: '当日核心指标' })
    expect(indicators).toHaveTextContent('MACD DIF 0.0105')
    expect(indicators).toHaveTextContent('MACD DEA 0.047')
    expect(indicators).toHaveTextContent('MACD 柱值 -0.073')
    expect(indicators).toHaveTextContent('RSI6 50.0891')
  })

  it('shows the previous-to-current consensus using the backend comparison date', () => {
    renderPanel()
    fireEvent.click(screen.getByText('较前日指标变化'))
    const comparison = screen.getByRole('group', { name: '研究结论较前日变化' })
    expect(comparison).toBeVisible()
    expect(comparison).toHaveTextContent('2026-09-08 → 2026-09-09')
    expect(comparison).toHaveTextContent('BEARISH_WITH_CONFLICTS → MIXED')
    expect(comparison).toHaveTextContent('结论已变化')
  })

  it('does not fabricate indicators or closest triggers for insufficient data', async () => {
    const engine = engineFixture()
    engine.indicators = { macd_dif: null, macd_dea: null, macd_hist: null, rsi6: null }
    engine.summary.research_consensus = engine.aggregate.research_consensus = 'INSUFFICIENT_DATA'
    engine.summary.closest_trigger = null
    Object.assign(engine.summary.chan_structure, { latest_close: null, structure_high: null,
      structure_low: null, trade_date: null, current_structure: 'INSUFFICIENT_DATA', price_location: 'NOT_AVAILABLE' })
    renderWorkbench(engine)
    const overview = await screen.findByRole('region', { name: 'Research Overview' })
    const indicators = within(overview).getByRole('group', { name: '当日核心指标' })
    expect(indicators).toHaveTextContent('MACD DIF --')
    expect(indicators).toHaveTextContent('RSI6 --')
    expect(overview).toHaveTextContent('暂无可比较的未触发策略')
    expect(overview).toHaveTextContent('当前价格位置不可用')
    expect(overview).toHaveTextContent('INSUFFICIENT_DATA')
  })

  it('previews Markdown as text and exports JSON and Markdown locally without network or publishing', async () => {
    const engine = engineFixture()
    const blobs: Blob[] = []
    const downloads: { name: string; url: string }[] = []
    const create = vi.fn((blob: Blob) => { blobs.push(blob); return `blob:research-${blobs.length}` })
    const revoke = vi.fn()
    vi.stubGlobal('URL', class extends URL { static createObjectURL = create; static revokeObjectURL = revoke })
    const fetchSpy = vi.fn(() => { throw new Error('Exports must remain local') })
    vi.stubGlobal('fetch', fetchSpy)
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      downloads.push({ name: this.download, url: this.href })
    })
    renderPanel(engine)
    fireEvent.click(screen.getByText('研究日报 Markdown'))
    expect(screen.getByText(/# Research Daily/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '下载研究 JSON' }))
    fireEvent.click(screen.getByRole('button', { name: '下载研究 Markdown' }))
    const readBlob = (blob: Blob) => new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = reject
      reader.readAsText(blob)
    })
    const json = JSON.parse(await readBlob(blobs[0]))
    expect(json).toEqual(engine)
    expect(json.summary.paper_action).toBe('HOLD')
    expect(json.safety.can_publish).toBe(false)
    expect(await readBlob(blobs[1])).toBe(engine.daily_markdown)
    expect(blobs.map(blob => blob.type)).toEqual(['application/json;charset=utf-8', 'text/markdown;charset=utf-8'])
    expect(downloads).toEqual([
      { name: 'research-000403.SZ-2026-09-09.json', url: 'blob:research-1' },
      { name: 'research-000403.SZ-2026-09-09.md', url: 'blob:research-2' },
    ])
    expect(revoke).toHaveBeenCalledTimes(2)
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(api.paperTradingRun).not.toHaveBeenCalled()
  })
})
