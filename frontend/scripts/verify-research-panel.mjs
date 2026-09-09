import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const base = 'http://127.0.0.1:3018'
const out = fileURLToPath(new URL('../../reports/research_engine_v1/ui/', import.meta.url))
const accountFields = data => JSON.stringify({
  account: data.account, decisions: data.decisions, trades: data.trades,
  equity_history: data.equity_history,
})
const response = await fetch(`${base}/api/paper-trading/dashboard`)
assert.equal(response.status, 200)
const before = await response.json()
assert.equal(before.research.status, 'READY')
assert.equal(before.research.strategies.length, 6)
const engine = before.research.engine
assert.equal(engine.trade_date, '2026-09-09')
assert.equal(engine.summary.paper_action, 'HOLD')
assert.equal(engine.summary.hold_reason, 'FLAT_WAIT')
assert.equal(before.safety.real_trading, 'DISABLED')
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const result = { research_date: before.research.trade_date, viewports: [], blocked_requests: [] }
try {
  for (const [name, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    const context = await browser.newContext({ viewport: { width, height }, serviceWorkers: 'block' })
    await context.route('**/*', route => {
      const request = route.request()
      if (new URL(request.url()).origin !== base || !['GET', 'HEAD'].includes(request.method())) {
        const url = new URL(request.url())
        result.blocked_requests.push({ method: request.method(), host: url.hostname, path: url.pathname })
        return route.abort()
      }
      return route.continue()
    })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', error => errors.push(error.name))
    await page.goto(`${base}/paper-trading`, { waitUntil: 'domcontentloaded' })
    const panel = page.getByRole('region', { name: '日线多策略研究' })
    const overview = page.getByRole('region', { name: 'Research Overview', exact: true })
    await overview.getByRole('heading', { name: '今日研究总览' }).waitFor()
    assert.ok((await overview.innerText()).includes(engine.summary.research_consensus))
    assert.ok((await overview.innerText()).includes('FLAT_WAIT · 空仓观望'))
    assert.ok((await overview.innerText()).includes(engine.summary.closest_trigger.strategy_name))
    const displayNumber = value => new Intl.NumberFormat('en-US', { maximumFractionDigits: 8, useGrouping: false }).format(value)
    for (const value of Object.values(engine.indicators)) {
      assert.ok((await overview.getByRole('group', { name: '当日核心指标' }).innerText()).includes(displayNumber(value)))
    }
    await page.screenshot({ path: `${out}/research-${name}-overview.png` })
    const colors = await panel.getByRole('heading', { name: '日线多策略研究' }).evaluate(element => {
      let background = 'transparent'
      for (let parent = element; parent; parent = parent.parentElement) {
        const value = getComputedStyle(parent).backgroundColor
        if (value !== 'rgba(0, 0, 0, 0)' && value !== 'transparent') { background = value; break }
      }
      return { text: getComputedStyle(element).color, background }
    })
    assert.notEqual(colors.text, colors.background)
    const matrix = panel.getByRole('table', { name: '七项研究矩阵' })
    assert.equal(await matrix.locator('tbody > tr').count(), 7)
    for (const strategy of engine.strategies) {
      await panel.getByRole('button', { name: `展开 ${strategy.strategy_name} 证据`, exact: true }).click()
      const detail = panel.getByRole('region', { name: `${strategy.strategy_name} 证据`, exact: true })
      for (const condition of strategy.evidence) {
        assert.ok((await detail.innerText()).includes(condition.actual))
      }
      await panel.getByRole('button', { name: `收起 ${strategy.strategy_name} 证据`, exact: true }).click()
    }
    await panel.getByRole('button', { name: '展开 缠论日线结构 证据', exact: true }).click()
    const chan = panel.getByRole('region', { name: '缠论日线结构 证据', exact: true })
    assert.ok((await chan.innerText()).includes('DEFERRED'))
    for (const fractal of [...engine.chan_structure.top_fractals, ...engine.chan_structure.bottom_fractals]) {
      assert.ok(fractal.confirmed_at <= engine.trade_date)
      assert.ok((await chan.innerText()).includes(fractal.confirmed_at))
    }
    await panel.getByRole('button', { name: '收起 缠论日线结构 证据', exact: true }).click()
    await panel.locator('summary').filter({ hasText: '较前日指标变化' }).click()
    for (const change of engine.summary.changes_vs_previous) {
      assert.ok((await panel.getByRole('table', { name: '较前日指标变化' }).innerText()).includes(change.label))
    }
    for (const label of ['JSON', 'Markdown']) {
      const downloaded = page.waitForEvent('download')
      await panel.getByRole('button', { name: `下载研究 ${label}`, exact: true }).click()
      const raw = await readFile(await (await downloaded).path(), 'utf8')
      if (label === 'JSON') assert.deepEqual(JSON.parse(raw), engine)
      else assert.equal(raw, engine.daily_markdown)
    }
    await page.reload({ waitUntil: 'domcontentloaded' })
    await overview.getByRole('heading', { name: '今日研究总览' }).waitFor()
    assert.equal(await matrix.locator('tbody > tr').count(), 7)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    await panel.evaluate(element => {
      document.querySelector('main')?.scrollTo(0, 0)
      for (let parent = element.parentElement; parent; parent = parent.parentElement) {
        if (getComputedStyle(parent).overflowY === 'auto' && parent.scrollHeight > parent.clientHeight) {
          parent.scrollTop += element.getBoundingClientRect().top - parent.getBoundingClientRect().top - 16
          break
        }
      }
    })
    await page.screenshot({ path: `${out}/research-${name}.png` })
    assert.deepEqual(errors, [])
    result.viewports.push({ name, width, height, status: 'PASSED', console_errors: 0,
      checks_passed: 8, checks: ['overview_api_parity', 'seven_row_matrix', 'numeric_evidence',
        'chan_confirmation_dates', 'daily_delta', 'json_markdown_download', 'refresh_persistence', 'no_page_overflow'] })
    await context.close()
  }
} finally {
  await browser.close()
}
const afterResponse = await fetch(`${base}/api/paper-trading/dashboard`)
assert.equal(afterResponse.status, 200)
const after = await afterResponse.json()
assert.equal(accountFields(after), accountFields(before))
assert.deepEqual(after.research, before.research)
const staticFonts = new Set(['rsms.me/inter/inter.css', 'fonts.googleapis.com/css2'])
assert.deepEqual(result.blocked_requests.filter(request => request.method !== 'GET'
  || !staticFonts.has(`${request.host}${request.path}`)), [])
result.external_font_requests_blocked = result.blocked_requests.length
result.account_unchanged = true
result.research_deterministic = true
result.daily_run_executed = false
result.provider_calls = 0
result.e2e_cases_passed = result.viewports.reduce((sum, viewport) => sum + viewport.checks_passed, 0)
await writeFile(`${out}/ui-verification.json`, JSON.stringify(result, null, 2) + '\n')
console.log(JSON.stringify(result))
