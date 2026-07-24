# TickFlow Real Data and AI Validation Runbook

Scope: private A-share daily research and de-traded stock-review Markdown only.

This runbook does not connect a brokerage, enable trading, enable Gold, modify Telegram/OpenClaw, or publish a financial daily automatically.

## Safety Rules

- Never paste a password, TickFlow key, AI key, cookie, or token into chat.
- Configure credentials only through the authenticated private UI or a private local terminal.
- Keep ports 3018 and 3019 loopback-only and use the existing Tailscale HTTPS `:8443` route.
- Keep `GOLD_WORKSPACE_ENABLED=false`.
- Do not write any key until authentication is initialized and file permissions are verified.
- Stop on stale or internally inconsistent market data; do not let AI fill missing facts.

## Validation Symbols

| Symbol | Name |
|---|---|
| `000403.SZ` | 派林生物 |
| `600489.SH` | 中金黄金 |
| `300059.SZ` | 东方财富 |

## Required Sequence

```text
Initialize authentication
-> privately configure TickFlow Pro
-> verify data freshness
-> verify raw daily bars
-> verify adjustment factors
-> verify forward-adjusted indicators
-> verify incremental history warmup
-> run stock analysis
-> privately configure AI
-> generate de-traded Markdown
-> manually verify facts
```

Do not reorder key configuration ahead of authentication.

## Step 1: Authentication

1. Connect to Tailscale and open the private 8443 URL.
2. Set the first access password in the UI. Do not send the password to an agent.
3. Verify the safe status endpoint reports `configured=true`.
4. On the server, verify `data/user_data/auth.json` is mode `0600`.
5. Log out and back in over HTTPS.

Expected gate:

```text
AUTH_READY
```

If any check fails:

```text
AUTH_USER_ACTION_REQUIRED
```

## Step 2: TickFlow Pro Configuration

1. In the authenticated Settings page, privately enter the existing TickFlow Pro key.
2. Confirm the UI reports a valid paid mode and the expected endpoint.
3. Do not copy the masked or unmasked key into reports.
4. Confirm `data/user_data/secrets.json` is mode `0600`.

No agent should request the real key.

## Step 3: No-Key Structural Readiness

Before using the key, run:

```bash
cd "/Users/macbookpro/Documents/TradingView 量化/tickflow-multiclient-app"
./scripts/verify_real_data_readiness.sh
```

The script:

- makes no external market-data or AI request;
- never reads secret values;
- checks the three instrument records;
- compares raw and enriched daily coverage;
- reports adjustment-factor and financial-file availability;
- checks sensitive file modes when those files exist.

`REAL_DATA_READINESS_BLOCKED` is expected before authentication, factors, financials, and AI are configured.

## Step 4: Data Freshness and Raw Daily Bars

For each symbol, record:

- expected latest mainland-China trading date from an independent trading calendar;
- latest raw-bar date;
- open, high, low, close;
- volume and amount, including their units;
- source endpoint and retrieval timestamp.

Fail the run if:

- the latest bar is not the expected trading date;
- OHLC relationships are invalid;
- volume/amount units are unknown;
- any of the three symbols is missing.

## Step 5: Adjustment Factors

For each symbol:

1. Retrieve the factor series separately from raw OHLCV.
2. Confirm factor direction and effective dates.
3. Check recent dividends, splits, rights issues, and other corporate actions manually.
4. Recompute a small date window independently.

If factor data is unavailable, do not label enriched bars as independently verified.

## Step 6: Forward-Adjusted Indicators

Verify:

- adjusted close against the independent factor calculation;
- moving-average ordering and exact lookback windows;
- MACD parameters and latest values;
- RSI parameters and latest values;
- volume units and any volume moving average;
- limit-up label against exchange/ST rules;
- latest close against the raw daily bar.

The historical warmup frame must already be adjusted exactly once. A raw historical frame may be adjusted once; an enriched historical frame must not be adjusted again.

Run the focused regression gate:

```bash
cd "/Users/macbookpro/Documents/TradingView 量化/tickflow-multiclient-app/backend"
PYTHONPATH=. .venv/bin/pytest tests/test_pipeline_incremental_history.py -q
```

## Step 7: Stock Analysis Before AI

For each of the three symbols, verify the deterministic input bundle first:

- symbol and company name;
- latest daily close and date;
- key levels and their derivation;
- indicator values and parameters;
- financial-data availability flag;
- material-news availability flag;
- corporate-action availability flag.

Missing financials or news must be represented as unavailable, not inferred.

## Step 8: AI Configuration

Only after deterministic inputs pass:

1. Privately configure the AI provider/key in the authenticated Settings page.
2. Confirm only boolean/masked status is visible from the safe settings response.
3. Run one stock at a time.
4. Do not enable automatic publishing or notification delivery.

## Step 9: Markdown Contract

Every generated stock-review file must start with:

```yaml
---
type: stock-analysis
source_system: tickflow-stock-panel
data_scope: watchlist-sample
can_publish: false
trading_advice: false
verification_status: pending
needs_verification:
  - latest_close
  - key_levels
  - technical_indicators
  - financial_data_availability
  - material_news
  - corporate_actions
timeframe: 1d
---
```

Required sections:

1. 一句话定调
2. 日线技术结构
3. 关键观察区间
4. 基本面与财务面
5. 消息面与人工核验
6. 明日观察清单
7. 风险触发条件
8. 需要人工二次确认的数据
9. 免责声明

Reject output containing explicit instructions to buy, sell, add/reduce a position, set position size, set a stop-loss, or name a target price.

## Step 10: Manual Fact Verification

Before moving a Markdown file into an Obsidian tracking library:

- verify latest close and date against a second source;
- verify support/resistance calculations;
- verify indicator parameters and values;
- verify volume/amount units;
- verify financial-report period and availability;
- verify material announcements and corporate actions;
- set `verification_status` only after human review;
- keep `can_publish: false`.

The sample may become a private research-material block, but it must not automatically enter the formal financial daily.

## Observation Window

Repeat the checks manually for 3-5 trading days. This runbook does not claim those future observation days have already passed.

Success state after that observation:

```text
REAL_DATA_CONTRACT_OBSERVED
AI_MARKDOWN_CONTRACT_OBSERVED
```

These states do not imply trading readiness or publication approval.
