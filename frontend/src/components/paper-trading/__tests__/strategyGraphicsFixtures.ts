import type { Condition, ResearchEngine, StrategyResearch } from '@/lib/research-engine'
import type { StrategyGraphicsReadyData } from '@/lib/strategy-graphics'

export function graphicsFixture(): StrategyGraphicsReadyData {
  return {
    schema_version: 1, status: 'READY', symbol: '000403.SZ', trade_date: '2026-09-09',
    timeframe: '1d', price_basis: 'QFQ', history_basis: 'SAME_SNAPSHOT_PREFIX', window_requested: 30,
    points: [
      { trade_date: '2026-09-08', open: 18, high: 18.5, low: 17.9, close: 18.25, ma5: 18.1, ma10: 18.2, ma20: 18.3,
        ma60: null, boll_upper: 19.5, boll_middle: 18.3, boll_lower: 17.1,
        macd_dif: 0, macd_dea: 0.03, macd_hist: -0.06, rsi6: null },
      { trade_date: '2026-09-09', open: 18.7, high: 18.8, low: 18.4, close: 18.6, ma5: 18.35, ma10: 18.26, ma20: 18.31,
        ma60: 17.8, boll_upper: 19.6, boll_middle: 18.31, boll_lower: 17.02,
        macd_dif: 0.0105, macd_dea: 0.047, macd_hist: -0.073, rsi6: 50.0891 },
    ],
    strategy_markers: [
      { trade_date: '2026-09-08', strategy_id: 'macd_golden', strategy_name: 'MACD 金叉放量',
        status: 'TRIGGERED', price: 18.25, conditions_met: 4, conditions_total: 4 },
      { trade_date: '2026-09-09', strategy_id: 'pullback_to_support', strategy_name: '回踩支撑',
        status: 'NEAR_TRIGGER', price: 18.6, conditions_met: 3, conditions_total: 4 },
    ],
  }
}

export function overlayFixture(): StrategyGraphicsReadyData {
  const graphics = graphicsFixture()
  return {
    ...graphics,
    overlay_markers: [
      ...engineFixture().strategies.map(strategy => ({
        marker_id: `${strategy.trade_date}:${strategy.strategy_id}`,
        trade_date: strategy.trade_date, strategy_id: strategy.strategy_id, strategy_name: strategy.strategy_name,
        kind: 'STRATEGY' as const, canonical_status: strategy.status, price: 18.6,
        conditions_met: strategy.conditions_met, conditions_total: strategy.conditions_total,
        summary: `${strategy.strategy_name}：满足 3/4 条件`, evidence: strategy.evidence,
        failed_conditions: strategy.failed_conditions, delta_vs_previous: strategy.delta_vs_previous, structure: null,
      })),
      { marker_id: 'chan:top', trade_date: '2026-09-09', strategy_id: 'chan_daily_structure',
        strategy_name: 'Chan Daily Structure', kind: 'CHAN_STRUCTURE', canonical_status: 'NOT_AVAILABLE',
        price: 18.6, conditions_met: null, conditions_total: null,
        summary: '结构识别｜未定义策略触发合同', evidence: [], failed_conditions: [], delta_vs_previous: null,
        structure: { type: 'TOP_FRACTAL', date: '2026-09-08', confirmed_at: '2026-09-09', price: 18.5 } },
      { marker_id: 'chan:stroke', trade_date: '2026-09-09', strategy_id: 'chan_daily_structure',
        strategy_name: 'Chan Daily Structure', kind: 'CHAN_STRUCTURE', canonical_status: 'NOT_AVAILABLE',
        price: 18.6, conditions_met: null, conditions_total: null,
        summary: '结构识别｜未定义策略触发合同', evidence: [], failed_conditions: [], delta_vs_previous: null,
        structure: { type: 'STROKE_CANDIDATE', direction: 'UP_STROKE', start_date: '2026-09-07',
          end_date: '2026-09-08', start_price: 17.8, end_price: 18.5, confirmed_at: '2026-09-09', source_bar_separation: 2 } },
    ],
    paper_records: {
      '2026-09-09': { status: 'PUBLISHED', trade_date: '2026-09-09', paper_action: 'HOLD',
        research_signal: 'WATCH', quantity: 0, hold_explanation: null,
        source: 'days/2026-09-09/chenquant_daily.json', source_sha256: 'fixture-sha256' },
    },
  }
}

export function overlayWindowFixture(): StrategyGraphicsReadyData {
  const graphics = overlayFixture()
  const dates = Array.from({ length: 30 }, (_, index) => new Date(Date.UTC(2026, 7, 11 + index)).toISOString().slice(0, 10))
  const rules = graphics.overlay_markers!.slice(0, 6)
  const chan = graphics.overlay_markers![6]
  return {
    ...graphics,
    points: dates.map(trade_date => ({ ...graphics.points[1], trade_date })),
    overlay_markers: [
      ...dates.flatMap(trade_date => rules.map(marker => ({ ...marker, trade_date, marker_id: `${trade_date}:${marker.strategy_id}` }))),
      ...dates.slice(-8).map((trade_date, index) => ({ ...chan, trade_date, marker_id: `chan:${trade_date}`,
        structure: { type: 'TOP_FRACTAL' as const, date: dates[21 + index], confirmed_at: trade_date, price: 18.8 } })),
    ],
  }
}

export const failedCondition: Condition = {
  label: '量比', required: '量比 > 1.5', actual: '量比=1.38', passed: false,
  observed: 1.38, threshold: 1.5, operator: 'gt', unit: 'RATIO', margin: -0.12,
  gap: 0.12, temporal_scope: 'CURRENT_DAY',
}

export function engineFixture(): ResearchEngine {
  const rules = [
    ['macd_golden', 'MACD 金叉放量', 'TRIGGERED'],
    ['bullish_alignment', '均线多头排列', 'NOT_TRIGGERED'],
    ['boll_breakout', 'BOLL 突破', 'NEAR_TRIGGER'],
    ['volume_price_surge', '量价齐升', 'NOT_AVAILABLE'],
    ['pullback_to_support', '回踩支撑', 'NEAR_TRIGGER'],
    ['boll_lower_reclaim', 'BOLL 下轨收复', 'NOT_TRIGGERED'],
  ] as const
  const strategies: StrategyResearch[] = rules.map(([strategy_id, strategy_name, status]) => ({
    strategy_id, strategy_name, status, trade_date: '2026-09-09', timeframe: '1d', price_basis: 'QFQ',
    conditions_total: 4, conditions_met: 3, condition_ratio: 0.75,
    evidence: [
      ...['价格', '趋势', '动量'].map(label => ({ ...failedCondition, label, passed: true,
        observed: 2, actual: `${label}=2`, gap: 0, margin: 0.5 })),
      { ...failedCondition },
    ],
    failed_conditions: [{ ...failedCondition }], distance_to_trigger: [{ ...failedCondition }],
    delta_vs_previous: {
      previous_conditions_met: 2, conditions_met_delta: 1, change: 'IMPROVING',
      conditions: [{ label: '量比', previous: 1.2, current: 1.38, value_delta: 0.18,
        margin_delta: 0.18, change: 'IMPROVING', unit: 'RATIO' }],
    },
    research_bias: 'NEUTRAL', risk_triggered: false,
    calculation_source: `builtin.${strategy_id}`, parameters: { volume_ratio_min: 1.5 },
  }))
  const chan: ResearchEngine['chan_structure'] = {
    mode: 'CHAN_DAILY_STRUCTURE_V1', trade_date: '2026-09-09', current_structure: 'RANGE_STRUCTURE',
    previous_structure: 'RANGE_STRUCTURE', change: 'UNCHANGED', structure_high: 19.75,
    structure_low: 17.25, latest_close: 18.6, price_location: 'INSIDE_CONFIRMED_RANGE',
    top_fractals: [], bottom_fractals: [], stroke_candidates: [], central_zone: 'DEFERRED',
  }
  const closest = { strategy_id: 'pullback_to_support', strategy_name: '回踩支撑',
    conditions_met: 3, conditions_total: 4, condition_ratio: 0.75, distance_to_trigger: [{ ...failedCondition }] }
  const confidence = { category: 'LOW' as const, basis: '方向证据不一致', is_probability: false as const }
  return {
    schema_version: 1, symbol: '000403.SZ', trade_date: '2026-09-09', timeframe: '1d', price_basis: 'QFQ',
    strategies, indicators: { macd_dif: 99, macd_dea: 98, macd_hist: 97, rsi6: 96 }, chan_structure: chan,
    aggregate: {
      bullish_count: 0, bearish_count: 0, neutral_count: 6, setup_count: 0,
      triggered_strategies: ['macd_golden'], near_trigger_strategies: ['boll_breakout', 'pullback_to_support'],
      supporting_factors: [], limiting_factors: [], conflicts: [], closest_trigger: closest,
      research_consensus: 'MIXED', research_confidence: confidence, rule_trace: [],
      delta_vs_previous: { trade_date: '2026-09-08', previous_consensus: 'MIXED', current_consensus: 'MIXED', changed: false },
    },
    summary: {
      trade_date: '2026-09-09', symbol: '000403.SZ', research_consensus: 'MIXED', research_confidence: confidence,
      paper_action: 'HOLD', position_state: 'FLAT', hold_reason: 'FLAT_WAIT', hold_explanation: [],
      supporting_factors: [], limiting_factors: [], strategy_conflicts: [], closest_trigger: closest,
      chan_structure: chan, changes_vs_previous: [], next_observation_conditions: [], claims_count: 21,
      data_quality: { source_provider: 'fixture', price_basis: 'QFQ', source_hashes: {}, vendor_pending: [],
        claims_status: 'VALID', financial_data: 'NOT_CONNECTED', material_news: 'NOT_CONNECTED' },
    },
    daily_markdown: '# Research Daily',
    safety: { research_only: true, simulation_only: true, can_publish: false, trading_advice: false, real_trading: 'DISABLED' },
  }
}

export function deepFreeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(deepFreeze)
    Object.freeze(value)
  }
  return value
}
