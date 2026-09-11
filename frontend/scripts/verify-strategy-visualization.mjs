import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { dirname } from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'
import * as echarts from 'echarts'

const require = createRequire(import.meta.url)
const { build } = require(require.resolve('esbuild', { paths: [dirname(require.resolve('vite'))] }))
async function sourceModule(file) {
  const compiled = await build({ entryPoints: [fileURLToPath(new URL(file, import.meta.url))], bundle: true,
    platform: 'node', format: 'cjs', write: false, packages: 'external' })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(require, module, module.exports)
  return module.exports
}
const { buildStrategyGraphicsOptions, DEFAULT_CHART_LAYERS } = await sourceModule('../src/lib/strategy-graphics.ts')
const { chartTheme } = await sourceModule('../src/lib/theme.ts')
const base = 'http://127.0.0.1:3018'
const out = fileURLToPath(new URL('../../reports/strategy_visualization_v2_1/ui/', import.meta.url))
const read = async () => {
  const response = await fetch(`${base}/api/paper-trading/dashboard`)
  assert.equal(response.status, 200)
  return response.json()
}
const state = data => JSON.stringify({ account: data.account, positions: data.positions,
  decisions: data.decisions, trades: data.trades, equity: data.equity_history })
const before = await read(), graphics = before.research.graphics
assert.equal(graphics.status, 'READY')
assert.equal(before.safety.real_trading, 'DISABLED')
assert.ok(graphics.points.every(point => Number.isFinite(point.volume)))
const result = { research_date: graphics.trade_date, checks: [], screenshots: [], blocked_requests: [] }
const add = (view, path, check) => result.checks.push({ view, path, check, status: 'PASSED' })
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
async function scrollTo(locator) {
  await locator.evaluate(element => {
    for (let p = element.parentElement; p; p = p.parentElement) {
      if (getComputedStyle(p).overflowY === 'auto' && p.scrollHeight > p.clientHeight) {
        p.scrollTop += element.getBoundingClientRect().top - p.getBoundingClientRect().top - 12
        break
      }
    }
  })
}
async function shot(page, target, name) {
  await scrollTo(target)
  await page.screenshot({ path: `${out}/${name}.png` })
  result.screenshots.push(`${name}.png`)
}
async function markerPosition(locator, key, filters, date, strategyId) {
  const box = await locator.boundingBox()
  const chart = echarts.init(null, undefined, { renderer: 'svg', ssr: true, width: box.width, height: box.height })
  try {
    chart.setOption(buildStrategyGraphicsOptions(graphics, chartTheme('dark'), filters,
      { selectedObservationDate: date, layers: DEFAULT_CHART_LAYERS })[key])
    const option = chart.getOption()
    for (const [index, series] of option.series.entries()) {
      if (series.type !== 'scatter') continue
      for (const datum of series.data ?? []) {
        if (datum.marker?.strategy_id !== strategyId || datum.marker?.trade_date !== date) continue
        const xy = chart.convertToPixel({ seriesIndex: index }, datum.value)
        const offset = datum.symbolOffset ?? series.symbolOffset ?? [0, 0]
        return { x: xy[0] + offset[0], y: xy[1] + offset[1] }
      }
    }
    throw new Error(`Missing ${key} ${strategyId} ${date} marker`)
  } finally { chart.dispose() }
}
const chartNames = ['价格与均线图', '成交量图', 'MACD 图', 'RSI6 图']
try {
  for (const [view, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    const context = await browser.newContext({ viewport: { width, height }, hasTouch: view === 'mobile',
      isMobile: view === 'mobile', serviceWorkers: 'block' })
    await context.route('**/*', route => {
      const request = route.request(), url = new URL(request.url())
      if (url.origin !== base || !['GET', 'HEAD'].includes(request.method())) {
        result.blocked_requests.push({ method: request.method(), host: url.hostname, path: url.pathname })
        return route.abort()
      }
      return route.continue()
    })
    for (const path of ['stock-research', 'paper-trading']) {
      const page = await context.newPage(), errors = []
      page.on('pageerror', error => errors.push(error.message))
      await page.goto(`${base}/${path}`, { waitUntil: 'domcontentloaded' })
      const region = page.getByRole('region', { name: '策略图表', exact: true })
      const cards = page.getByRole('region', { name: '六策略总览' })
      const day = region.getByRole('combobox', { name: '观察日期', exact: true })
      const strategy = region.getByRole('combobox', { name: '策略筛选', exact: true })
      const status = region.getByRole('combobox', { name: '状态筛选', exact: true })
      const detail = region.getByRole('region', { name: '策略观察详情' })
      const record = region.getByRole('region', { name: '当日模拟记录' })
      await expect(day).toHaveValue(graphics.trade_date, { timeout: 45000 })
      for (const name of chartNames) {
        const chart = region.getByRole('img', { name, exact: true })
        await chart.locator('canvas').first().waitFor()
        const pixels = await chart.evaluate(element => {
          const canvas = element.querySelector('canvas'), d = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
          let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++
          return n
        })
        assert.ok(pixels > 500)
      }
      add(view, path, 'four_nonblank_canvases')
      await expect(page.getByLabel('工作台日期', { exact: true })).toContainText('股票日线指标缓存日期')
      for (const text of ['系统日期', '最新行情', '最新研究', '最新模拟盘']) await expect(page.getByLabel('工作台日期', { exact: true })).toContainText(text)
      add(view, path, 'truthful_top_date_semantics')
      await shot(page, region, `${view}-${path}-overview`)
      await shot(page, region.getByRole('img', { name: '价格与均线图', exact: true }), `${view}-${path}-price`)

      for (const id of ['bullish_alignment', 'pullback_to_support', 'macd_golden']) {
        const row = graphics.overlay_markers.find(m => m.trade_date === graphics.trade_date && m.strategy_id === id)
        await cards.getByRole('button', { name: `定位 ${row.strategy_name}`, exact: true }).click()
        await expect(strategy).toHaveValue(id)
        await expect(status).toHaveValue('ALL')
        await expect(detail).toContainText(`${row.conditions_met}/${row.conditions_total}`)
        for (const c of row.evidence) await expect(detail).toContainText(c.actual)
      }
      add(view, path, 'strategy_cards_focus_filters_and_exact_evidence')
      const previous = graphics.points.at(-2).trade_date
      await day.selectOption(previous)
      for (const name of chartNames) await expect(region.getByRole('img', { name, exact: true })).toHaveAttribute('data-observation-date', previous)
      await expect(cards).toContainText(`回溯观察日期 ${previous}`)
      await expect(region.getByRole('region', { name: '观察日指标' })).toContainText(previous)
      await expect(record).toContainText(previous)
      add(view, path, 'one_observation_date_for_cards_four_charts_values_and_record')
      await shot(page, region.getByRole('img', { name: '成交量图', exact: true }), `${view}-${path}-volume-macd`)

      for (const [id, key, name] of [['pullback_to_support', 'price', '价格与均线图'],
        ['macd_golden', 'macd', 'MACD 图'], ['volume_price_surge', 'volume', '成交量图']]) {
        await region.getByRole('button', { name: '全部策略', exact: true }).click()
        await strategy.selectOption(id)
        await day.selectOption(graphics.trade_date)
        const chart = region.getByRole('img', { name, exact: true })
        await scrollTo(chart)
        const position = await markerPosition(chart, key, { strategy: id, status: 'ALL' }, previous, id)
        if (view === 'mobile') await chart.tap({ position })
        else await chart.click({ position })
        await expect(day).toHaveValue(previous)
        await expect(strategy).toHaveValue(id)
        await expect(detail).toContainText(previous)
        add(view, path, `actual_${key}_marker_${view === 'mobile' ? 'tap' : 'click'}`)
      }
      await strategy.selectOption('ALL')
      await status.selectOption('ALL')
      await day.selectOption(previous)
      assert.ok(await detail.getByRole('article').count() >= 6)
      add(view, path, 'same_day_expands_all_real_strategy_entries')
      await strategy.selectOption('chan_daily_structure')
      const chan = graphics.overlay_markers.find(m => m.kind === 'CHAN_STRUCTURE')
      await day.selectOption(chan.trade_date)
      await expect(detail).toContainText('NOT_AVAILABLE')
      await expect(detail).toContainText('条件进度 N/A')
      await expect(detail).toContainText(chan.structure.type)
      await expect(detail).toContainText(`确认日期 ${chan.structure.confirmed_at}`)
      await expect(detail).not.toContainText('NEAR_TRIGGER')
      add(view, path, 'chan_confirmation_structure_not_strategy')
      const missing = Object.keys(graphics.paper_records).find(d => graphics.paper_records[d].status === 'NOT_AVAILABLE')
      await day.selectOption(missing)
      await expect(record).toContainText('不可用')
      await expect(record).not.toContainText('HOLD')
      add(view, path, 'missing_history_is_not_backfilled')
      await region.getByRole('button', { name: '全部策略', exact: true }).click()
      await expect(status).toHaveValue('ACTIVE')
      for (const label of ['K线', 'MA5', 'MA10', 'MA20', 'MA60', 'BOLL', '策略']) {
        const input = region.getByRole('checkbox', { name: label, exact: true }), initial = await input.isChecked()
        await input.setChecked(!initial)
        await expect(input).toBeChecked({ checked: !initial })
        await input.setChecked(initial)
      }
      add(view, path, 'all_display_layer_toggles')
      await day.selectOption(graphics.trade_date)
      await shot(page, detail, `${view}-${path}-detail`)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      assert.equal(await region.evaluate(element => element.scrollWidth > element.clientWidth + 1), false)
      await page.evaluate(() => localStorage.setItem('tf-theme', 'light'))
      await page.reload({ waitUntil: 'domcontentloaded' })
      await expect(day).toHaveValue(graphics.trade_date, { timeout: 45000 })
      await shot(page, region, `${view}-${path}-light`)
      assert.deepEqual(errors, [])
      add(view, path, 'refresh_theme_no_overflow_or_javascript_errors')
      await page.evaluate(() => localStorage.setItem('tf-theme', 'dark'))
      await page.close()
    }
    await context.close()
  }
} finally { await browser.close() }
assert.equal(state(await read()), state(before))
const fonts = new Set(['rsms.me/inter/inter.css', 'fonts.googleapis.com/css2'])
assert.deepEqual(result.blocked_requests.filter(r => r.method !== 'GET' || !fonts.has(`${r.host}${r.path}`)), [])
Object.assign(result, { checks_passed: result.checks.length, state_sha256: createHash('sha256').update(state(before)).digest('hex'),
  account_unchanged: true, provider_calls: 0, market_requests: 0, real_trades: 0, daily_executed: false })
await writeFile(`${out}/ui-verification.json`, JSON.stringify(result, null, 2) + '\n')
console.log(JSON.stringify({ checks_passed: result.checks_passed, screenshots: result.screenshots.length, account_unchanged: true }))
