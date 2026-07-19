import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  nextGoldMarketDate,
  normalizeGoldStatus,
  normalizeGoldGate,
} from '../src/lib/gold.ts'

const disabled = {
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
assert.equal(normalizeGoldStatus(disabled).enabled, false)
assert.equal(normalizeGoldStatus({ enabled: true, symbol: '000001.SZ' }), null)
const normalizedGate = normalizeGoldGate({
  status: 'review_eligible',
  required_complete_trading_days: 10,
  complete_trading_days: 10,
  external_send_count: 0,
  reasons: [],
  canonical_days: [{
    market_date: '2026-07-20',
    run_id: 'a'.repeat(64),
    automatic_passed: true,
    review_recorded: true,
    review_verified: true,
    note: 'must not cross the UI boundary',
  }],
  restart_review: { recorded: true, verified: true, note: 'must not be exposed' },
})
assert.equal(normalizedGate.status, 'review_eligible')
assert.deepEqual(normalizedGate.canonical_days, [{
  market_date: '2026-07-20',
  run_id: 'a'.repeat(64),
  automatic_passed: true,
  review_recorded: true,
  review_verified: true,
}])
assert.deepEqual(normalizedGate.restart_review, { recorded: true, verified: true })
assert.equal(normalizeGoldGate({ status: 'notifications_enabled' }), null)

const enabled = {
  enabled: true,
  symbol: '600489.SH',
  latest: {
    schema_version: 1,
    observed_at: '2026-07-20T15:00:00+08:00',
    market_date: '2026-07-20',
    symbol: '600489.SH',
    quote_source: 'tickflow',
    quote_ts: 1_784_531_600_000,
    price: 19.99,
    previous_close: 20.12,
    legacy_reference_60: 22.87,
    native_ema60: 22.74,
    P: -12.59,
    V: -0.13,
    A: -0.93,
    state: '恐慌',
    candidate_signals: ['恐慌极端'],
    new_candidate_signals: ['恐慌极端'],
  },
  latest_market_date: '2026-07-20',
  health: {
    latest_success_at: '2026-07-20T15:00:00+08:00',
    latest_market_date: '2026-07-20',
    consecutive_failures: 0,
    last_error: null,
  },
  external_send_count: 0,
}
assert.equal(normalizeGoldStatus(enabled)?.latest?.quote_source, 'tickflow')

const statusWithLatest = (latest) => ({ ...enabled, latest: { ...enabled.latest, ...latest } })

assert.deepEqual([
  normalizeGoldStatus(statusWithLatest({ quote_ts: 0 }))?.latest?.quote_ts,
  normalizeGoldStatus(statusWithLatest({ native_ema60: 0 }))?.latest?.native_ema60,
], [0, 0])

for (const field of [
  'quote_ts',
  'price',
  'previous_close',
  'legacy_reference_60',
  'native_ema60',
  'P',
  'V',
  'A',
]) {
  for (const value of [NaN, Infinity]) {
    assert.equal(normalizeGoldStatus(statusWithLatest({ [field]: value })), null, `${field}=${value}`)
  }
}

for (const latest of [
  { quote_source: 'other' },
  { state: 'unknown' },
  { candidate_signals: ['unknown'] },
]) {
  assert.equal(normalizeGoldStatus(statusWithLatest(latest)), null)
}

for (const external_send_count of [-1, 0.5]) {
  assert.equal(normalizeGoldStatus({ ...enabled, external_send_count }), null)
}

assert.equal(normalizeGoldGate({
  status: 'failed',
  required_complete_trading_days: 10,
  complete_trading_days: 0,
  external_send_count: null,
  reasons: ['external_send_state_corrupt'],
  canonical_days: [{
    market_date: '2026-07-20',
    run_id: 'b'.repeat(64),
    automatic_passed: null,
    review_recorded: null,
    review_verified: null,
  }],
  restart_review: { recorded: null, verified: null },
})?.status, 'failed')

assert.equal(normalizeGoldGate({
  status: 'collecting',
  required_complete_trading_days: 10,
  complete_trading_days: 0,
  external_send_count: 0,
  reasons: [],
}), null)

assert.equal(nextGoldMarketDate('2026-07-18', '2026-07-20', false), '2026-07-20')
assert.equal(nextGoldMarketDate('2026-07-18', '2026-07-20', true), '2026-07-18')
assert.equal(nextGoldMarketDate('2026-07-18', '', false), '2026-07-18')

const observationPanel = readFileSync(
  new URL('../src/components/gold/GoldObservationPanel.tsx', import.meta.url),
  'utf8',
)
assert.match(observationPanel, /external_send_count === null\s*\?\s*['"]无法确认['"]/)
assert.match(observationPanel, /gate\.canonical_days/)
assert.match(observationPanel, /gate\.restart_review/)
