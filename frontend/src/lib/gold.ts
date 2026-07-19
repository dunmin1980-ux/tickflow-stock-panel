import type {
  GoldGateStatus,
  GoldCandidateSignal,
  GoldHealth,
  GoldObservationGate,
  GoldShadowSnapshot,
  GoldState,
  GoldStatus,
} from './api'

const GOLD_SYMBOL = '600489.SH'
const GOLD_SOURCE = 'tickflow'
const GOLD_STATES = new Set<GoldState>(['恐慌', '死机', '贪婪'])
const GOLD_SIGNALS = new Set<GoldCandidateSignal>(['恐慌极端', '惯性衰竭', '均值回归', '贪婪态'])
const GOLD_GATE_STATUSES = new Set<GoldGateStatus>(['collecting', 'failed', 'review_eligible'])
type UnknownRecord = Record<string, unknown>

const isRecord = (value: unknown): value is UnknownRecord =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value)

const isNonNegativeInteger = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0

const isStringOrNull = (value: unknown): value is string | null =>
  typeof value === 'string' || value === null

function normalizeSignals(value: unknown): GoldCandidateSignal[] | null {
  if (!Array.isArray(value) || !value.every((signal): signal is GoldCandidateSignal =>
    typeof signal === 'string' && GOLD_SIGNALS.has(signal as GoldCandidateSignal))) {
    return null
  }
  return [...value]
}

function normalizeHealth(value: unknown): GoldHealth | null {
  if (!isRecord(value)
    || !isStringOrNull(value.latest_success_at)
    || !isStringOrNull(value.latest_market_date)
    || !isNonNegativeInteger(value.consecutive_failures)) {
    return null
  }
  let lastError: GoldHealth['last_error'] = null
  if (value.last_error !== null) {
    const rawLastError = value.last_error
    if (!isRecord(rawLastError)
      || typeof rawLastError.code !== 'string'
      || typeof rawLastError.message !== 'string') {
      return null
    }
    lastError = { code: rawLastError.code, message: rawLastError.message }
  }
  return {
    latest_success_at: value.latest_success_at,
    latest_market_date: value.latest_market_date,
    consecutive_failures: value.consecutive_failures,
    last_error: lastError,
  }
}

function normalizeSnapshot(value: unknown): GoldShadowSnapshot | null {
  const quoteTs = isRecord(value) ? value.quote_ts : undefined
  const price = isRecord(value) ? value.price : undefined
  const previousClose = isRecord(value) ? value.previous_close : undefined
  const legacyReference60 = isRecord(value) ? value.legacy_reference_60 : undefined
  const nativeEma60 = isRecord(value) ? value.native_ema60 : undefined
  const p = isRecord(value) ? value.P : undefined
  const v = isRecord(value) ? value.V : undefined
  const a = isRecord(value) ? value.A : undefined
  if (!isRecord(value)
    || value.schema_version !== 1
    || typeof value.observed_at !== 'string'
    || typeof value.market_date !== 'string'
    || value.symbol !== GOLD_SYMBOL
    || value.quote_source !== GOLD_SOURCE
    || !GOLD_STATES.has(value.state as GoldState)
    || !isFiniteNumber(quoteTs)
    || !isFiniteNumber(price)
    || !isFiniteNumber(previousClose)
    || !isFiniteNumber(legacyReference60)
    || !isFiniteNumber(nativeEma60)
    || !isFiniteNumber(p)
    || !isFiniteNumber(v)
    || !isFiniteNumber(a)) {
    return null
  }
  if (!Number.isInteger(quoteTs) || quoteTs <= 0
    || price <= 0
    || previousClose <= 0
    || legacyReference60 <= 0
    || nativeEma60 <= 0) {
    return null
  }
  const candidateSignals = normalizeSignals(value.candidate_signals)
  const newCandidateSignals = normalizeSignals(value.new_candidate_signals)
  if (candidateSignals === null || newCandidateSignals === null) return null

  return {
    schema_version: 1,
    observed_at: value.observed_at,
    market_date: value.market_date,
    symbol: GOLD_SYMBOL,
    quote_source: GOLD_SOURCE,
    quote_ts: quoteTs,
    price,
    previous_close: previousClose,
    legacy_reference_60: legacyReference60,
    native_ema60: nativeEma60,
    P: p,
    V: v,
    A: a,
    state: value.state as GoldState,
    candidate_signals: candidateSignals,
    new_candidate_signals: newCandidateSignals,
  }
}

export function normalizeGoldStatus(value: unknown): GoldStatus | null {
  if (!isRecord(value) || typeof value.enabled !== 'boolean') return null
  if (!value.enabled) {
    return {
      enabled: false,
      symbol: null,
      latest: null,
      latest_market_date: null,
      health: {
        latest_success_at: null,
        latest_market_date: null,
        consecutive_failures: 0,
        last_error: null,
      },
      external_send_count: 0,
    }
  }
  if (value.symbol !== GOLD_SYMBOL
    || !isStringOrNull(value.latest_market_date)
    || !isNonNegativeInteger(value.external_send_count)) {
    return null
  }
  const health = normalizeHealth(value.health)
  const latest = value.latest === null ? null : normalizeSnapshot(value.latest)
  if (health === null || latest === null && value.latest !== null) return null
  return {
    enabled: true,
    symbol: GOLD_SYMBOL,
    latest,
    latest_market_date: value.latest_market_date,
    health,
    external_send_count: value.external_send_count,
  }
}

export function normalizeGoldGate(value: unknown): GoldObservationGate | null {
  if (!isRecord(value)
    || typeof value.status !== 'string'
    || !GOLD_GATE_STATUSES.has(value.status as GoldGateStatus)
    || !isNonNegativeInteger(value.required_complete_trading_days)
    || !isNonNegativeInteger(value.complete_trading_days)
    || !(value.external_send_count === null || isNonNegativeInteger(value.external_send_count))
    || !Array.isArray(value.reasons)
    || !value.reasons.every((reason) => typeof reason === 'string')) {
    return null
  }
  return {
    status: value.status as GoldGateStatus,
    required_complete_trading_days: value.required_complete_trading_days,
    complete_trading_days: value.complete_trading_days,
    external_send_count: value.external_send_count,
    reasons: [...value.reasons],
  }
}
