# Chan Daily Structure v1

## Scope and API

`app.services.phase2_chan_structure.analyze_chan(rows: list[dict], as_of: date) -> dict`

This is a bounded engineering definition, **not full Chan theory**. It is a pure,
standard-library calculation on caller-supplied QFQ daily OHLC. There is no AI,
network, Provider, secret access, file I/O, trading signal, or paper-engine call.
Strategy schemas, aggregation, UI, actual strokes, segments, inclusion merging,
divergence, and central zones are outside this module. `central_zone` is `DEFERRED`.

## Input and Time Boundary

- `rows` must be a list of dictionaries containing `trade_date`, `open`, `high`,
  `low`, and `close`. The caller guarantees QFQ basis and one instrument; this
  function cannot infer adjustment provenance from OHLC or convert raw prices.
  Extra fields are ignored, including volume and amount.
- Dates must be canonical `YYYY-MM-DD` strings representing valid dates, strictly
  increasing and unique. No sorting, deduplication, or calendar inference occurs.
- OHLC must be built-in `int` or `float`, positive and finite under Python's
  finite-number check; unrepresentably large integers are rejected. Booleans,
  numeric strings, Decimal, null, NaN, and infinities are rejected. Require
  `low <= open <= high` and `low <= close <= high`; a flat OHLC bar is valid.
- `as_of` must be a Python `date`, not a datetime or string. Invalid input raises
  `ValueError` with a `CHAN_*` category; it never becomes an empty success result.
- Validate the entire supplied input first, including future rows. Then filter
  to `trade_date <= as_of` **before any structure calculation**. Appending or
  changing valid future bars cannot change any result field. Malformed future
  input still fails boundary validation; prefix invariance assumes valid input.
- Output `trade_date` is the last available date in that filtered prefix, not
  necessarily `as_of`, and is null for an empty prefix. The previous trading day
  means the previous supplied bar, not the preceding calendar day. The previous
  structure is independently recalculated from the filtered prefix minus its
  last bar. A non-trading-day query does not change the daily comparison.

## Fractals and Candidates

For each original consecutive three-bar window:

- TOP: the center's high **and** low are each strictly above both neighbors.
- BOTTOM: the center's high **and** low are each strictly below both neighbors.
- Equality or inclusion does not qualify. Bars are neither removed nor merged.
- `date` is the center bar's date; `confirmed_at` is the right bar's date. TOP
  price is the center high; BOTTOM price is the center low. The first and current
  last bars cannot be confirmed centers. All confirmation dates are `<= as_of`.
- Public records explicitly carry `type: "TOP_FRACTAL"` or
  `type: "BOTTOM_FRACTAL"`. Every returned date is an ISO string, never a Python
  date/datetime object.

Both raw fractal lists are complete, chronological, and untruncated, including
consecutive same-type confirmations. All calculations use the full prefix.

Candidate pivots are built separately in confirmation order. Retain the first
pivot. For a same-type event, replace the last candidate pivot only with a
strictly higher TOP or strictly lower BOTTOM; equal prices retain the earliest.
Append an opposite-type event only when its source center index is at least two
greater than the last retained pivot's index and its endpoint price moves
strictly in the required direction. Otherwise skip it for candidate purposes,
leaving the last pivot and raw history intact. Later events are compared with
that last retained pivot.

Each adjacent retained pair produces `STROKE_CANDIDATE`: BOTTOM to TOP is
`UP_STROKE`, TOP to BOTTOM is `DOWN_STROKE`. Separation is an original source-bar
index difference, not a calendar interval or reduced-pivot count. Candidate
`confirmed_at` is the end pivot's confirmation date. All candidates are returned.
A later extreme may revise the latest candidate endpoint on a later query;
historical `as_of` queries remain reproducible. Candidates are not full Chan strokes.

## Structure and Change

`structure_high` and `structure_low` always preserve the latest **raw confirmed**
TOP and BOTTOM prices, respectively, or null when that type is absent. Never
sort, swap, round, clip, or substitute prices to manufacture a valid range.

Classification priority:

1. If both latest levels exist and `structure_high <= structure_low`, report
   `TRANSITION`. This explicit inconsistent/zero-width range guard takes priority
   even when fewer than two fractals of either type exist.
2. Otherwise, fewer than two raw TOPs or two raw BOTTOMs: `INSUFFICIENT_DATA`.
3. Latest two TOP prices increasing and latest two BOTTOM prices increasing:
   `UP_STRUCTURE`.
4. Both decreasing: `DOWN_STRUCTURE`.
5. TOP prices nonincreasing and BOTTOM prices nondecreasing: `RANGE_STRUCTURE`.
6. Any other combination: `TRANSITION`.

The `change` field is the current-versus-previous-trading-day comparison, with
this deterministic first-match priority:

1. `REVERSING`: direct `UP_STRUCTURE` to `DOWN_STRUCTURE` or the reverse.
2. `STRENGTHENING`: entering `UP_STRUCTURE` from a nondirectional state, or
   leaving `DOWN_STRUCTURE` for a nondirectional state.
3. `WEAKENING`: entering `DOWN_STRUCTURE` from a nondirectional state, or leaving
   `UP_STRUCTURE` for a nondirectional state.
4. `NEW_FRACTAL`: any other newly confirmed raw fractal, including a continuation
   of the same directional structure or a candidate-rejected confirmation.
5. `UNCHANGED`: none of the above.

Strengthening/weakening describe **price direction**, not generic trend strength
and not an investment recommendation. Nondirectional includes `INSUFFICIENT_DATA`,
`RANGE_STRUCTURE`, and `TRANSITION`. Direct `REVERSING` is a reserved mapping:
one newly available bar can confirm at most one fractal type, so it cannot flip
both strict comparisons at once in v1. An actual turn can pass through
`TRANSITION`/`RANGE_STRUCTURE`; each adjacent-day comparison follows the table.

## Current Price vs Confirmed Structure

`latest_close` is the last QFQ close in the filtered prefix, or null if it is
empty. This is a separate current-price observation, not a new fractal or input
to the four-pivot structure rule. With both confirmed levels present and
`structure_high > structure_low`, `price_location` is:

- `ABOVE_CONFIRMED_RANGE` when `latest_close > structure_high`.
- `BELOW_CONFIRMED_RANGE` when `latest_close < structure_low`.
- `INSIDE_CONFIRMED_RANGE` otherwise, including exact equality with either level.

An absent close, absent level, or crossed/zero-width pair gives `NOT_AVAILABLE`.
Location only needs one confirmed TOP and BOTTOM, not two of each. It is computed
for any structure label when the pair is valid. `RANGE_STRUCTURE` describes the
confirmed pivot pattern, not a claim that the current price remains within its
levels. A below-range close can coexist with `RANGE_STRUCTURE` and `UNCHANGED`;
neither structure nor `change` is overridden by price location. Future bars cannot
affect the close, location, or confirmation history for a historical query.

## Exact Output Shape

The following is a type description, not literal JSON. `ISODate` is a canonical
date string and `Number` is a finite positive JSON number. No optional fields or
`latest_unconfirmed` object are emitted in v1; missing levels/dates are null.

```text
Structure = UP_STRUCTURE | DOWN_STRUCTURE | RANGE_STRUCTURE | TRANSITION | INSUFFICIENT_DATA
Change = UNCHANGED | STRENGTHENING | WEAKENING | REVERSING | NEW_FRACTAL
PriceLocation = ABOVE_CONFIRMED_RANGE | BELOW_CONFIRMED_RANGE | INSIDE_CONFIRMED_RANGE | NOT_AVAILABLE
TopFractal = {type: "TOP_FRACTAL", date: ISODate, confirmed_at: ISODate, price: Number}
BottomFractal = {type: "BOTTOM_FRACTAL", date: ISODate, confirmed_at: ISODate, price: Number}
StrokeCandidate = {
  type: "STROKE_CANDIDATE", direction: "UP_STROKE" | "DOWN_STROKE",
  start_date: ISODate, end_date: ISODate,
  start_price: Number, end_price: Number,
  confirmed_at: ISODate, source_bar_separation: integer >= 2
}
{
  mode: "CHAN_DAILY_STRUCTURE_V1",
  price_basis: "QFQ",
  timeframe: "1d",
  trade_date: ISODate | null,
  current_structure: Structure,
  previous_structure: Structure,
  change: Change,
  top_fractals: TopFractal[],
  bottom_fractals: BottomFractal[],
  stroke_candidates: StrokeCandidate[],
  structure_high: Number | null,
  structure_low: Number | null,
  latest_close: Number | null,
  price_location: PriceLocation,
  central_zone: "DEFERRED",
  deterministic_contract_version: 1
}
```

Input values are not mutated. Outputs contain only JSON-native values and are
safe for `json.dumps(result, allow_nan=False)`. The function has no mutable global
state or current-clock dependency. Repeating the same inputs produces the same
result and JSON serialization. Runtime and memory are linear in supplied history.

## Verification

Run offline from `backend`, without pytest cache or bytecode writes:

```sh
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_phase2_chan_structure.py
.venv/bin/ruff check --no-cache app/services/phase2_chan_structure.py tests/test_phase2_chan_structure.py
```

Observed TDD cycles: missing API **1 failed -> 1 passed**; validation/fractals
**90 failed, 14 passed -> 104 passed**; candidates/structures/change
**37 failed, 109 passed -> 146 passed**; public fractal tags
**4 failed, 142 passed -> 146 passed**; price-direction change mapping
**7 failed, 146 passed -> 153 passed**; separate current-price location
**13 failed, 152 passed -> 165 passed**. Tests exercise both extrema, equality,
inclusion, malformed dates/OHLC, confirmation timing, all valid future prefixes,
calendar gaps, full raw history, extremal reduction and ties, invalid candidates,
every structure, crossed levels, price-direction comparison priority, current-price
location (including boundary equality), JSON safety, and repetition.
