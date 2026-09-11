# Strategy Visualization V2.1 Focused Review

Status: `NO_P0_P1_FINDINGS`

The implementation-stage independent review and final focused re-review were
completed before the release-only instruction. The user confirmed their result.
This closeout preserves those findings; it does not claim a new independent
review or reopen implementation.

## Reviewed Boundaries

- RAW relative volume with existing full-history VOL5/VOL10; QFQ prices and
  existing strategy predicates remain separate and unchanged.
- Stable marker identity, canonical status and evidence-based chart placement.
- One observation date, historical evidence separation, Chan confirmation date
  and `NOT_AVAILABLE / N/A`, unavailable HOLD reasons remain unavailable.
- Mobile tap, same-day expansion, layer controls and default visual density.
- Explicit card re-selection centers the date after manual zoom while ordinary
  zoom remains synchronized and chart instances stay stable.
- Missing volume/indicator values are not converted to fake zero values.
- Header date provenance is cached workspace state, not an additional fetch.

## Evidence

- The earlier P1 card re-centering issue was reproduced with a failing test,
  fixed minimally, and re-reviewed successfully.
- Semantic glyph bounds: 48 SSR panel/width/theme/filter configurations reviewed.
- Final shared grid: left 64, right 16, `containLabel=false` on all four charts.
- Final `strategy-overlay-layout.test.ts`: 7/7 passed.
- Independent exact-pixel check: 8 configurations, 480 date columns, all aligned.
- Compact axis labels change presentation only; source values, tooltip values
  and underlying price/volume series remain unchanged.
- No remaining P0/P1 actionable findings. Bundle size and broader device
  certification remain P2 / DEFER, not implementation work in this release.

Final production build and real-browser acceptance are recorded separately in
the release evaluation and `ui/ui-verification.json`.
