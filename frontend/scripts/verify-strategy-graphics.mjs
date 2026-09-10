import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const base = 'http://127.0.0.1:3018'
const out = fileURLToPath(new URL('../../reports/strategy_graphics_v1/ui/', import.meta.url))
const state = data => JSON.stringify({ account: data.account, positions: data.positions,
  decisions: data.decisions, trades: data.trades, equity: data.equity_history })
const read = async date => {
  const response = await fetch(`${base}/api/paper-trading/dashboard${date ? `?target_date=${date}` : ''}`)
  assert.equal(response.status, 200)
  return response.json()
}
const before = await read()
assert.equal(before.research.status, 'READY')
const { engine, graphics } = before.research
assert.equal(graphics.status, 'READY')
assert.equal(graphics.points.length, 30)
assert.equal(graphics.trade_date, engine.trade_date)
assert.equal(before.safety.real_trading, 'DISABLED')
for (const key of ['macd_dif', 'macd_dea', 'macd_hist', 'rsi6']) {
  assert.equal(graphics.points.at(-1)[key], engine.indicators[key])
}
await mkdir(out, { recursive: true })
const result = { research_date: engine.trade_date, cases: [], blocked_requests: [], canvas: [] }
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const add = (view, path, check) => result.cases.push({ view, path, check, status: 'PASSED' })
async function scrollSection(locator) {
  await locator.evaluate(element => {
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      if (getComputedStyle(parent).overflowY === 'auto' && parent.scrollHeight > parent.clientHeight) {
        parent.scrollTop += element.getBoundingClientRect().top - parent.getBoundingClientRect().top - 12
        break
      }
    }
  })
}
try {
  for (const [view, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    const context = await browser.newContext({ viewport: { width, height }, serviceWorkers: 'block' })
    await context.route('**/*', route => {
      const request = route.request(), url = new URL(request.url())
      if (url.origin !== base || !['GET', 'HEAD'].includes(request.method())) {
        result.blocked_requests.push({ method: request.method(), host: url.hostname, path: url.pathname })
        return route.abort()
      }
      return route.continue()
    })
    for (const path of ['paper-trading', 'stock-research']) {
      const page = await context.newPage(), errors = []
      page.on('pageerror', error => errors.push(error.name))
      await page.goto(`${base}/${path}`, { waitUntil: 'domcontentloaded' })
      const overview = page.getByRole('region', { name: 'Research Overview', exact: true })
      await overview.waitFor()
      const overviewText = await overview.innerText()
      assert.ok(overviewText.includes(engine.summary.research_consensus))
      assert.ok(overviewText.includes(engine.summary.paper_action))
      assert.ok(overviewText.includes(engine.summary.hold_reason))
      add(view, path, 'summary_hold_api_parity')
      await page.screenshot({ path: `${out}/${view}-${path}-summary.png` })

      const cards = page.getByRole('region', { name: '六策略总览', exact: true })
      assert.equal(await cards.getByRole('article').count(), 6)
      for (const strategy of engine.strategies) {
        const card = cards.getByRole('article', { name: strategy.strategy_name, exact: true })
        assert.ok((await card.innerText()).includes(`${strategy.conditions_met}/${strategy.conditions_total}`))
        if (strategy.strategy_id === engine.summary.closest_trigger?.strategy_id)
          assert.ok((await card.innerText()).includes('最接近触发'))
        await card.getByRole('button').click()
        for (const condition of strategy.evidence) assert.ok((await card.innerText()).includes(condition.actual))
        await card.getByRole('button').click()
      }
      add(view, path, 'six_cards_closest_and_all_condition_values')
      await scrollSection(cards)
      await page.screenshot({ path: `${out}/${view}-${path}-cards.png` })

      const charts = page.getByRole('region', { name: '策略图表', exact: true })
      for (const label of ['价格与均线图', 'MACD 图', 'RSI6 图']) {
        const chart = charts.getByRole('img', { name: label, exact: true })
        await chart.locator('canvas').first().waitFor()
        await scrollSection(chart)
        await page.waitForFunction(label => {
          const canvas = document.querySelector(`[role="img"][aria-label="${label}"] canvas`)
          if (!canvas || !canvas.width || !canvas.height) return false
          const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
          return data.some((v, i) => i % 4 === 3 && v > 0)
        }, label)
        const pixels = await chart.evaluate(element => {
          const canvas = element.querySelector('canvas')
          const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
          const colors = new Set(); let opaque = 0
          for (let i = 0; i < data.length; i += 4) if (data[i+3] > 0) {
            opaque++; colors.add(`${data[i]},${data[i+1]},${data[i+2]}`)
          }
          const rect = element.getBoundingClientRect()
          return { width: rect.width, height: rect.height, pixel_width: canvas.width,
            opaque_pixels: opaque, unique_colors: colors.size }
        })
        assert.ok(pixels.opaque_pixels > 500 && pixels.unique_colors > 10)
        assert.ok(pixels.width > 250 && pixels.width <= width && pixels.height >= 180)
        result.canvas.push({ view, path, label, ...pixels })
        await chart.screenshot({ path: `${out}/${view}-${path}-${label === '价格与均线图' ? 'price' : label === 'MACD 图' ? 'macd' : 'rsi'}.png` })
        add(view, path, `${label}_nonblank_framed_canvas`)
      }
      const price = charts.getByRole('img', { name: '价格与均线图', exact: true })
      await scrollSection(price)
      const canvasHash = async () => createHash('sha256').update(await price.screenshot()).digest('hex')
      const legendBefore = await canvasHash()
      await price.click({ position: { x: 23, y: 10 } })
      assert.notEqual(await canvasHash(), legendBefore)
      await price.click({ position: { x: 23, y: 10 } })
      add(view, path, 'price_legend_interactive')
      const table = charts.getByRole('table', { name: '策略图表数据' })
      const lastRow = table.getByRole('row', { name: new RegExp(graphics.trade_date) })
      for (const [key, value] of Object.entries(graphics.points.at(-1))) {
        if (key === 'trade_date' || value === null) continue
        assert.ok((await lastRow.textContent()).includes(String(value)), `${key} table parity`)
      }
      add(view, path, 'accessible_chart_values_api_parity')
      await page.reload({ waitUntil: 'domcontentloaded' })
      await overview.waitFor()
      assert.ok((await overview.innerText()).includes(engine.summary.research_consensus))
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      assert.deepEqual(errors, [])
      add(view, path, 'refresh_persistence_no_overflow_no_js_error')
      if (path === 'stock-research') {
        const research = page.getByRole('region', { name: '日线多策略研究', exact: true })
        await research.getByRole('button', { name: '展开 缠论日线结构 证据', exact: true }).click()
        assert.ok((await research.getByRole('region', { name: '缠论日线结构 证据', exact: true }).innerText()).includes('DEFERRED'))
        assert.equal(await page.getByRole('button', { name: '运行今日模拟盘' }).count(), 0)
        add(view, path, 'chan_existing_readonly_no_daily_runner')
      }
      await page.close()
    }
    await context.close()
  }
  // A real historical local response verifies the approved 3/4 example, without changing today's snapshot.
  const historical = await read('2026-09-09')
  const historicalClosest = historical.research.engine.summary.closest_trigger
  assert.equal(historicalClosest.conditions_met, 3)
  assert.equal(historicalClosest.conditions_total, 4)
  const historicalContext = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' })
  await historicalContext.route('**/*', route => {
    const request = route.request(), url = new URL(request.url())
    if (url.origin !== base || !['GET', 'HEAD'].includes(request.method())) return route.abort()
    if (url.pathname === '/api/paper-trading/dashboard') return route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(historical),
    })
    return route.continue()
  })
  const historicalPage = await historicalContext.newPage()
  await historicalPage.goto(`${base}/stock-research`, { waitUntil: 'domcontentloaded' })
  const historicalCards = historicalPage.getByRole('region', { name: '六策略总览', exact: true })
  const historicalCard = historicalCards.getByRole('article', { name: historicalClosest.strategy_name, exact: true })
  await historicalCard.waitFor()
  await historicalCard.getByRole('button').click()
  assert.ok((await historicalCard.innerText()).includes('3/4'))
  for (const condition of historicalClosest.distance_to_trigger)
    assert.ok((await historicalCard.innerText()).includes(condition.actual))
  await scrollSection(historicalCard)
  await historicalPage.screenshot({ path: `${out}/historical-2026-09-09-three-of-four.png` })
  add('historical_local_response_2026-09-09', 'stock-research', 'three_of_four_actual_unmet_condition')
  await historicalContext.close()
} finally { await browser.close() }
assert.equal(state(await read()), state(before))
const allowedFonts = new Set(['rsms.me/inter/inter.css', 'fonts.googleapis.com/css2'])
assert.deepEqual(result.blocked_requests.filter(r => r.method !== 'GET' || !allowedFonts.has(`${r.host}${r.path}`)), [])
Object.assign(result, { checks_passed: result.cases.length, account_unchanged: true,
  provider_calls: 0, market_requests: 0, daily_run_executed: false, real_trades: 0 })
await writeFile(`${out}/ui-verification.json`, JSON.stringify(result, null, 2) + '\n')
console.log(JSON.stringify(result))
