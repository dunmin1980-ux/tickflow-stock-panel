export type ResearchConsensus =
  | 'STRONG_BULLISH_ALIGNMENT' | 'BULLISH_WITH_CONFLICTS' | 'MIXED'
  | 'BEARISH_WITH_CONFLICTS' | 'STRONG_BEARISH_ALIGNMENT' | 'SETUP_WATCH' | 'INSUFFICIENT_DATA'

export interface ResearchConfidence {
  category: 'HIGH' | 'MODERATE' | 'LOW'
  basis: string
  is_probability: false
}

export interface Condition {
  label: string
  required: string
  actual: string
  passed: boolean | null
  observed: number | null
  threshold: number | number[] | null
  operator: 'gt' | 'gte' | 'lt' | 'lte' | 'between'
  unit: 'QFQ_PRICE' | 'RATIO' | 'PERCENTAGE_POINTS'
  margin: number | null
  gap: number | null
  temporal_scope: 'CURRENT_DAY' | 'PREVIOUS_DAY'
}

export interface ConditionDelta {
  label: string
  previous: number | null
  current: number | null
  value_delta: number | null
  margin_delta: number | null
  change: 'IMPROVING' | 'WEAKENING' | 'UNCHANGED' | 'NOT_AVAILABLE'
  unit: string
}

export interface StrategyResearch {
  strategy_id: string
  strategy_name: string
  trade_date: string
  timeframe: '1d'
  price_basis: 'QFQ'
  status: 'TRIGGERED' | 'NEAR_TRIGGER' | 'NOT_TRIGGERED' | 'NOT_AVAILABLE'
  conditions_total: number
  conditions_met: number
  condition_ratio: number
  evidence: Condition[]
  failed_conditions: Condition[]
  distance_to_trigger: Condition[]
  delta_vs_previous: {
    previous_conditions_met: number
    conditions_met_delta: number
    change: 'IMPROVING' | 'WEAKENING' | 'MIXED' | 'UNCHANGED' | 'NOT_AVAILABLE'
    conditions: ConditionDelta[]
  }
  research_bias: 'BULLISH' | 'BEARISH' | 'NEUTRAL' | 'SETUP'
  risk_triggered: boolean | null
  calculation_source: string
  parameters: Record<string, number | boolean>
}

export interface Closest {
  strategy_id: string
  strategy_name: string
  conditions_met: number
  conditions_total: number
  condition_ratio: number
  distance_to_trigger: Condition[]
}

export interface MetricDelta {
  id: string
  label: string
  previous: number | null
  current: number | null
  delta: number | null
  unit: string
  direction: string
}

export type ChanStructure = 'UP_STRUCTURE' | 'DOWN_STRUCTURE' | 'RANGE_STRUCTURE' | 'TRANSITION' | 'INSUFFICIENT_DATA'

export interface Fractal {
  type: 'TOP_FRACTAL' | 'BOTTOM_FRACTAL'
  date: string
  confirmed_at: string
  price: number
}

export interface StrokeCandidate {
  type: 'STROKE_CANDIDATE'
  direction: 'UP_STROKE' | 'DOWN_STROKE'
  start_date: string
  end_date: string
  start_price: number
  end_price: number
  confirmed_at: string
  source_bar_separation: number
}

export interface Chan {
  mode: 'CHAN_DAILY_STRUCTURE_V1'
  trade_date: string | null
  current_structure: ChanStructure
  previous_structure: ChanStructure
  change: 'UNCHANGED' | 'STRENGTHENING' | 'WEAKENING' | 'REVERSING' | 'NEW_FRACTAL'
  structure_high: number | null
  structure_low: number | null
  latest_close: number | null
  price_location: 'ABOVE_CONFIRMED_RANGE' | 'BELOW_CONFIRMED_RANGE' | 'INSIDE_CONFIRMED_RANGE' | 'NOT_AVAILABLE'
  top_fractals: Fractal[]
  bottom_fractals: Fractal[]
  stroke_candidates: StrokeCandidate[]
  central_zone: 'DEFERRED'
  [key: string]: unknown
}

export interface ResearchEngine {
  schema_version: 1
  symbol: '000403.SZ'
  trade_date: string
  timeframe: '1d'
  price_basis: 'QFQ'
  strategies: StrategyResearch[]
  indicators: {
    macd_dif: number | null
    macd_dea: number | null
    macd_hist: number | null
    rsi6: number | null
  }
  indicator_source?: string
  chan_structure: Chan
  aggregate: {
    bullish_count: number
    bearish_count: number
    neutral_count: number
    setup_count: number
    triggered_strategies: string[]
    near_trigger_strategies: string[]
    supporting_factors: string[]
    limiting_factors: string[]
    conflicts: string[]
    closest_trigger: Closest | null
    research_consensus: ResearchConsensus
    research_confidence: ResearchConfidence
    rule_trace: string[]
    delta_vs_previous: {
      trade_date: string
      previous_consensus: string
      current_consensus: string
      changed: boolean
    }
  }
  summary: {
    trade_date: string
    symbol: '000403.SZ'
    research_consensus: ResearchConsensus
    research_confidence: ResearchConfidence
    paper_action: 'BUY' | 'HOLD' | 'SELL' | 'NOT_RUN'
    position_state: 'FLAT' | 'HOLDING' | 'UNKNOWN'
    hold_reason: 'FLAT_WAIT' | 'POSITION_HOLD' | 'N/A' | 'NOT_RUN'
    hold_explanation: string[]
    supporting_factors: string[]
    limiting_factors: string[]
    strategy_conflicts: string[]
    closest_trigger: Closest | null
    chan_structure: Chan
    changes_vs_previous: MetricDelta[]
    next_observation_conditions: string[]
    claims_count: number
    data_quality: {
      source_provider: string
      price_basis: 'QFQ'
      source_hashes: Record<string, string>
      vendor_pending: string[]
      claims_status: 'VALID'
      financial_data: 'NOT_CONNECTED'
      material_news: 'NOT_CONNECTED'
    }
  }
  daily_markdown: string
  safety: {
    research_only: true
    simulation_only: true
    can_publish: false
    trading_advice: false
    real_trading: 'DISABLED'
  }
}
