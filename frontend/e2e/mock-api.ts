import type { Page, Route } from '@playwright/test'

export interface MockApiOptions {
  installEventSource?: boolean
  handleRoute?: (route: Route, url: URL) => Promise<boolean>
}

const extFields = {
  concept: [
    { name: 'symbol', dtype: 'string', label: '股票代码' },
    { name: 'name', dtype: 'string', label: '股票简称' },
    { name: 'concept', dtype: 'string', label: '概念' },
  ],
  industry: [
    { name: 'symbol', dtype: 'string', label: '股票代码' },
    { name: 'name', dtype: 'string', label: '股票简称' },
    { name: 'industry', dtype: 'string', label: '行业' },
  ],
}

const marketRows = [
  { symbol: '600000.SH', name: '浦发银行', close: 11.2, change_pct: 1.8, amount: 2_400_000_000, turnover_rate: 1.1, vol_ratio_5d: 1.2, float_market_cap: 320_000_000_000 },
  { symbol: '000001.SZ', name: '平安银行', close: 12.5, change_pct: -0.7, amount: 1_900_000_000, turnover_rate: 0.9, vol_ratio_5d: 0.8, float_market_cap: 250_000_000_000 },
  { symbol: '300750.SZ', name: '宁德时代', close: 228.4, change_pct: 2.4, amount: 4_200_000_000, turnover_rate: 1.6, vol_ratio_5d: 1.4, float_market_cap: 890_000_000_000 },
]

const preferences = {
  realtime_quotes_enabled: false,
  indices_nav_pinned: false,
  minute_sync_enabled: false,
  minute_sync_days: 5,
  minute_sync_segment_days: 1,
  daily_data_provider: 'tickflow',
  pipeline_pull_a_share: true,
  pipeline_pull_etf: false,
  pipeline_pull_index: true,
  pipeline_index_symbols: '',
  pipeline_schedule: { hour: 15, minute: 30 },
  instruments_schedule: { hour: 9, minute: 10 },
  enriched_batch_size: 100,
  index_daily_batch_size: 100,
  limit_ladder_monitor_enabled: false,
  depth_polling_interval: 10,
  depth_finalize_time: { hour: 15, minute: 5 },
  review_schedule: { enabled: false, hour: 16, minute: 0 },
  review_push_channels: [],
  sse_refresh_pages: {},
  strategy_monitor_enabled: false,
  strategy_monitor_ids: [],
  system_notify_enabled: false,
  sidebar_index_symbols: [],
  nav_order: [],
  nav_hidden: [],
  screener_auto_run: false,
  minute_intraday_refresh: false,
  minute_intraday_refresh_interval: 6,
  monitor_ext_fields: { concept: null, industry: null },
}

const dataStatus = {
  daily: null,
  enriched: null,
  index_daily: null,
  index_enriched: null,
  index_instruments: null,
  etf_daily: null,
  etf_enriched: null,
  etf_instruments: null,
  minute: null,
  adj_factor: null,
  instruments: null,
  financials: null,
  storage: {
    daily_files: 0,
    daily_size_mb: 0,
    enriched_files: 0,
    enriched_size_mb: 0,
    minute_files: 0,
    minute_size_mb: 0,
    adj_factor_files: 0,
    adj_factor_size_mb: 0,
    instruments_files: 0,
    instruments_size_mb: 0,
    total_size_mb: 0,
  },
  next_pipeline_run: null,
  next_instruments_run: null,
  last_pipeline_run: null,
  last_instruments_run: null,
  checked_at: '2026-07-20T20:00:00+08:00',
  indicators_ready: true,
}

const overviewMarket = {
  as_of: '2026-07-20',
  quote_status: { enabled: false, running: false, is_trading_hours: false },
  indices: [],
  breadth: { total: 3, up: 2, down: 1, flat: 0, up_pct: 66.7, down_pct: 33.3, avg_pct: 1.17, median_pct: 1.8, strong_up: 1, strong_down: 0 },
  amount: { total: 8_500_000_000, avg: 2_833_333_333 },
  boards: [],
  limit: { limit_up: 1, broken: 0, failed: 0, limit_down: 0, max_boards: 1, tiers: [] },
  distribution: [],
  trend: { above_ma5: 2, above_ma20: 2, above_ma60: 1, above_ma5_pct: 66.7, above_ma20_pct: 66.7, above_ma60_pct: 33.3, new_high: 1, new_low: 0 },
  activity: { avg_turnover: 1.2, high_turnover: 0, high_vol_ratio: 1, vol_ratio: 1.1 },
  radar: [],
  emotion: { score: 62, label: '偏强' },
  top_gainers: marketRows,
  top_losers: [...marketRows].reverse(),
  turnover_leaders: marketRows,
  active_leaders: marketRows,
  concept_rank: { leading: [], lagging: [] },
  industry_rank: { leading: [], lagging: [] },
}

export const paperTradingDashboard = {
  status: 'VISUAL_WORKBENCH_READY',
  symbol: '000403.SZ',
  name: '派林生物',
  timezone: 'Asia/Shanghai',
  requested_date: '2026-08-20',
  last_completed_trade_date: null,
  safety: { simulation_only: 'SIMULATION ONLY', real_trading: 'DISABLED', can_publish: false, trading_advice: false },
  input_readiness: {
    status: 'PREPARABLE',
    requested_date: '2026-08-20',
    effective_trade_date: null,
    source_fixture: 'VALIDATED_DAILY_INPUT',
    is_current_date: true,
    message: 'VALIDATED_DAILY_INPUT_CAN_BE_PREPARED',
  },
  claims: {
    status: 'VALID', errors: [], normalized_sha256: 'a'.repeat(64), claim_count: 11,
    facts_pointer_binding_count: 14, free_text_field_count: 0, unsourced_claim_count: 0,
    trading_claim_count: 0, raw_qfq_mismatch_count: 0, sensitive_hit_count: 0, can_publish: false,
    source_fixture: 'VALIDATED_DAILY_INPUT', trade_date: '2026-08-20',
  },
  account: {
    initial_cash_cny: '100000.00', cash_cny: '100000.00', market_value_cny: '0.00',
    total_equity_cny: '100000.00', cumulative_return_percent: '0.0000', realized_pnl_cny: '0.00',
    unrealized_pnl_cny: '0.00', current_drawdown_cny: '0.00', max_drawdown_cny: '0.00', pending_action: null,
  },
  positions: [],
  decisions: [],
  trades: [],
  equity_history: [],
  latest_daily: null,
  chenquant_daily_markdown: null,
}

const reviewReport = {
  id: 'review-e2e',
  title: 'A 股盘后复盘 2026-07-19',
  as_of: '2026-07-19',
  data_as_of: '2026-07-19',
  content: '# 复盘\n\n长文本换行验证\n\n`THIS_IS_A_DETERMINISTIC_UNBROKEN_MARKDOWN_TOKEN_USED_TO_VERIFY_MOBILE_OVERFLOW_HANDLING_20260720`',
  summary: '上证 +0.8% | 创业板 +1.2%',
  emotion_score: 58,
  emotion_label: '冷静',
  created_at: '2026-07-19T16:00:00+08:00',
  verification_status: 'pending',
  can_publish: false,
  trading_advice: false,
}

const stockReport = {
  id: 'stock-report-e2e',
  symbol: '000403.SZ',
  name: '派林生物',
  focus: '',
  title: '派林生物 个股日线复盘',
  content: '# 派林生物确定性研究记录\n\n仅展示历史素材，不调用模型。',
  summary: '确定性历史素材',
  close: 21.35,
  created_at: '2026-07-20T16:20:00+08:00',
  data_as_of: '2026-07-20',
  verification_status: 'pending',
  can_publish: false,
  trading_advice: false,
}

const workspaceRevision = '1'.repeat(64)
const workspaceUpdatedAt = '2026-07-20T20:00:00+08:00'
const workspaceResources = {
  watchlist: {
    resource: 'watchlist',
    revision: workspaceRevision,
    updated_at: workspaceUpdatedAt,
    data: { symbols: [{ symbol: '600000.SH', note: '' }] },
  },
  preferences: {
    resource: 'preferences',
    revision: workspaceRevision,
    updated_at: workspaceUpdatedAt,
    data: {
      preferences: {
        indices_nav_pinned: false,
        sidebar_index_symbols: [],
        nav_order: [],
        nav_hidden: [],
        screener_auto_run: false,
        daily_data_provider: 'tickflow',
        adj_factor_provider: 'tickflow',
        minute_data_provider: 'tickflow',
        realtime_data_provider: 'tickflow',
        financial_data_provider: 'tickflow',
        has_feishu_webhook: false,
        has_feishu_credential_data: false,
        has_wecom_webhook: false,
        has_wecom_bot: false,
        has_wecom_bot_credential_data: false,
      },
    },
  },
  stock_reports: {
    resource: 'stock_reports',
    revision: workspaceRevision,
    updated_at: workspaceUpdatedAt,
    data: { reports: [stockReport] },
  },
  market_recaps: {
    resource: 'market_recaps',
    revision: workspaceRevision,
    updated_at: workspaceUpdatedAt,
    data: {
      reports: [{
        id: reviewReport.id,
        title: reviewReport.title,
        created_at: reviewReport.created_at,
        data_as_of: reviewReport.data_as_of,
        verification_status: reviewReport.verification_status,
        can_publish: reviewReport.can_publish,
        trading_advice: reviewReport.trading_advice,
      }],
    },
  },
  backtest_summaries: {
    resource: 'backtest_summaries',
    revision: workspaceRevision,
    updated_at: workspaceUpdatedAt,
    data: { summaries: [] },
  },
}

function payloadFor(url: URL): unknown | undefined {
  const { pathname } = url

  if (pathname === '/api/settings') {
    return {
      mode: 'none',
      onboarding_completed: true,
      tickflow_api_key_masked: '',
      has_tickflow_key: false,
      tier_label: 'None',
      current_endpoint: '',
      probe_log: [],
      missing_caps: [],
      extras_caps: [],
      ai_provider: '',
      ai_base_url: '',
      ai_api_key_masked: '',
      has_ai_key: false,
      ai_configured: false,
      ai_model: '',
      ai_user_agent: '',
    }
  }
  if (pathname === '/api/workspace/bootstrap') {
    return {
      schema_version: 1,
      server_time: workspaceUpdatedAt,
      data_as_of: '2026-07-20',
      mode: 'cloud',
      capabilities: { label: 'None', capabilities: {} },
      resources: workspaceResources,
    }
  }
  if (pathname === '/api/workspace/revisions') {
    return {
      server_time: workspaceUpdatedAt,
      resources: Object.fromEntries(
        Object.entries(workspaceResources).map(([name, snapshot]) => [name, snapshot.revision]),
      ),
    }
  }
  const workspaceResource = pathname.match(/^\/api\/workspace\/resources\/([^/]+)$/)?.[1]
  if (workspaceResource && workspaceResource in workspaceResources) {
    return workspaceResources[workspaceResource as keyof typeof workspaceResources]
  }
  if (pathname === '/api/capabilities') return { label: 'None', capabilities: {} }
  if (pathname === '/api/data/version') return { version: '0.1.86-e2e' }
  if (pathname === '/api/settings/preferences') return preferences
  if (pathname === '/api/settings/data-sources') return { builtin: [], plugins: [], custom: [], errors: [], config_dir: '' }
  if (pathname === '/api/settings/preferences/watchlist-columns') return { columns: null }
  if (pathname === '/api/settings/preferences/quote-interval') return { interval: 6, min_interval: 3, max_interval: 60 }
  if (pathname === '/api/intraday/status') return { enabled: false, running: false, interval_s: 6, symbol_count: 0, quote_age_ms: null, is_trading_hours: false, last_fetch_ms: null }
  if (pathname === '/api/intraday/indices') return { rows: [], count: 0 }
  if (pathname === '/api/index/list') return { results: [], count: 0 }
  if (pathname === '/api/index/daily') return { symbol: url.searchParams.get('symbol') ?? '', rows: [], source: 'mock' }
  if (pathname === '/api/analysis-menus') return { items: [] }
  if (pathname === '/api/gold/status') return { enabled: false }
  if (pathname === '/api/paper-trading/dashboard') return paperTradingDashboard
  if (pathname === '/api/pipeline/jobs') return { active_id: null, jobs: [] }
  if (pathname === '/api/alerts') return { alerts: [], total: 0 }
  if (pathname === '/api/monitor-rules') return { rules: [] }

  if (pathname === '/api/watchlist') return { symbols: [{ symbol: '600000.SH', note: '' }] }
  if (pathname === '/api/watchlist/enriched') return { rows: [marketRows[0]], as_of: '2026-07-20', elapsed_ms: 1 }
  if (pathname === '/api/kline/daily-batch') return { data: { '600000.SH': [] } }
  if (pathname === '/api/stock-analysis/reports') return { reports: [] }
  if (pathname === `/api/stock-analysis/reports/${stockReport.id}`) return { report: stockReport }

  if (pathname === '/api/overview/market') return overviewMarket
  if (pathname === '/api/market-recap/reports') {
    return { reports: [reviewReport] }
  }
  if (pathname === `/api/market-recap/reports/${reviewReport.id}`) return { report: reviewReport }

  if (pathname === '/api/ext-data') {
    const now = '2026-07-20T20:00:00+08:00'
    return {
      items: [
        { id: 'ext_gn_ths', label: '概念分类', mode: 'snapshot', fields: extFields.concept, created_at: now, updated_at: now, latest_sync_date: '2026-07-20' },
        { id: 'ext_hy_ths', label: '行业分类', mode: 'snapshot', fields: extFields.industry, created_at: now, updated_at: now, latest_sync_date: '2026-07-20' },
      ],
    }
  }
  if (pathname === '/api/ext-data/ext_gn_ths/rows') {
    return { id: 'ext_gn_ths', label: '概念分类', mode: 'snapshot', date: '2026-07-20', total: 3, limit: 12000, fields: extFields.concept, rows: marketRows.map((row, index) => ({ ...row, concept: index === 2 ? '新能源' : '金融科技' })) }
  }
  if (pathname === '/api/ext-data/ext_hy_ths/rows') {
    return { id: 'ext_hy_ths', label: '行业分类', mode: 'snapshot', date: '2026-07-20', total: 3, limit: 12000, fields: extFields.industry, rows: marketRows.map((row, index) => ({ ...row, industry: index === 2 ? '电力设备' : '银行' })) }
  }
  if (pathname === '/api/screener/market-snapshot') return { as_of: '2026-07-20', rows: marketRows }
  if (pathname === '/api/data/status') return dataStatus

  return undefined
}

export async function installMockApi(page: Page, options: MockApiOptions = {}): Promise<string[]> {
  const unexpectedRequests: string[] = []

  await page.route(
    /https:\/\/(?:rsms\.me|fonts\.googleapis\.com|fonts\.gstatic\.com)\//,
    route => route.abort('blockedbyclient'),
  )

  if (options.installEventSource !== false) await page.addInitScript(() => {
    localStorage.removeItem('watchlist_view')

    class TestEventSource {
      static readonly CONNECTING = 0
      static readonly OPEN = 1
      static readonly CLOSED = 2
      readonly CONNECTING = 0
      readonly OPEN = 1
      readonly CLOSED = 2
      readonly url: string
      readonly withCredentials = false
      readyState = TestEventSource.OPEN
      onopen: ((event: Event) => void) | null = null
      onmessage: ((event: MessageEvent) => void) | null = null
      onerror: ((event: Event) => void) | null = null

      constructor(url: string | URL) {
        this.url = String(url)
        setTimeout(() => this.onopen?.(new Event('open')), 0)
      }

      addEventListener() {}
      removeEventListener() {}
      dispatchEvent() { return true }
      close() { this.readyState = TestEventSource.CLOSED }
    }

    Object.defineProperty(window, 'EventSource', { configurable: true, value: TestEventSource })
  })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (options.handleRoute && await options.handleRoute(route, url)) return
    if (url.pathname === '/api/client/status') {
      await route.fulfill({ status: 404, body: '' })
      return
    }
    const payload = payloadFor(url)
    if (payload === undefined) {
      unexpectedRequests.push(`${request.method()} ${url.pathname}${url.search}`)
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: `Unhandled E2E API request: ${url.pathname}` }),
      })
      return
    }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'cache-control': 'no-store' },
      body: JSON.stringify(payload),
    })
  })

  return unexpectedRequests
}
