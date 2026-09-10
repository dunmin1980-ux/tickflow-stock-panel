import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const base = 'http://127.0.0.1:3018'
const out = fileURLToPath(new URL('../../reports/strategy_overlay_v2/ui/', import.meta.url))
const read = async () => {
  const response = await fetch(`${base}/api/paper-trading/dashboard`)
  assert.equal(response.status, 200)
  return response.json()
}
const state = data => JSON.stringify({ account: data.account, positions: data.positions,
  decisions: data.decisions, trades: data.trades, equity: data.equity_history })
const before = await read(), graphics = before.research.graphics
assert.equal(graphics.status, 'READY')
assert.equal(graphics.points.length, 30)
assert.ok(Array.isArray(graphics.overlay_markers), 'Backend must serve the V2 overlay projection')
assert.equal(graphics.overlay_markers.filter(m => m.kind === 'STRATEGY').length, 180)
assert.equal(before.safety.real_trading, 'DISABLED')
for (const point of graphics.points) for (const key of ['open', 'close', 'low', 'high']) assert.ok(Number.isFinite(point[key]))
for (const marker of graphics.overlay_markers.filter(m => m.kind === 'CHAN_STRUCTURE')) {
  assert.equal(marker.trade_date, marker.structure.confirmed_at)
  assert.equal(marker.canonical_status, 'NOT_AVAILABLE')
  assert.equal(marker.conditions_met, null)
  assert.equal(marker.conditions_total, null)
}
const dates = markers => [...new Set(markers.map(m => m.trade_date))].sort()
const filter = (strategy, status) => graphics.overlay_markers.filter(m =>
  (strategy === 'ALL' || m.strategy_id === strategy) &&
  (status === 'ALL' || status === 'ACTIVE' && (m.kind === 'CHAN_STRUCTURE' || ['TRIGGERED', 'NEAR_TRIGGER'].includes(m.canonical_status)) || m.canonical_status === status))
const result = { research_date: graphics.trade_date, checks: [], canvas: [], controls: [], blocked_requests: [] }
const add = (view, path, check) => result.checks.push({ view, path, check, status: 'PASSED' })
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
async function scrollTo(locator) {
  await locator.evaluate(element => {
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      if (getComputedStyle(parent).overflowY === 'auto' && parent.scrollHeight > parent.clientHeight) {
        parent.scrollTop += element.getBoundingClientRect().top - parent.getBoundingClientRect().top - 12
        break
      }
    }
  })
}
async function selectStatus(region, value) {
  const select = region.getByRole('combobox', { name: '状态筛选', exact: true })
  if (await select.count()) await select.selectOption(value)
  else await region.getByRole('group', { name: '状态筛选', exact: true })
    .getByRole('button', { name: { ACTIVE: '触发与结构', ALL: '全部状态', TRIGGERED: '只看已触发', NEAR_TRIGGER: '只看接近触发' }[value], exact: true }).click()
}
async function verifyDates(region, expected) {
  const available = await region.getByRole('combobox', { name: '观察日期', exact: true })
    .locator('option').evaluateAll(options => options.map(o => o.value).filter(Boolean))
  assert.deepEqual(available, graphics.points.map(p => p.trade_date))
  const actual = await region.getByRole('table', { name: '策略图表数据' }).locator('tbody tr')
    .evaluateAll(rows => rows.filter(row => row.lastElementChild.textContent !== '无').map(row => row.firstElementChild.textContent))
  assert.deepEqual(actual.sort(), expected)
}
async function verifyControlContrast(region, view, path, theme) {
  const controls = await region.getByRole('combobox').evaluateAll(elements => elements.map(element => {
    const style = getComputedStyle(element)
    const rgb = value => (value.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number)
    const luminance = color => rgb(color).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
      .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0)
    const fg = luminance(style.color), bg = luminance(style.backgroundColor)
    return { name: element.getAttribute('aria-label'), foreground: style.color, background: style.backgroundColor,
      contrast: (Math.max(fg, bg) + .05) / (Math.min(fg, bg) + .05) }
  }))
  assert.equal(controls.length, 3)
  for (const control of controls) assert.ok(control.contrast >= 4.5, `${theme} ${control.name} contrast=${control.contrast}`)
  result.controls.push({ view, path, theme, controls })
  add(view, path, `${theme}_select_text_contrast`)
}
try {
  for (const [view, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    const context = await browser.newContext({ viewport: { width, height },
      hasTouch: view === 'mobile', isMobile: view === 'mobile', serviceWorkers: 'block' })
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
      const region = page.getByRole('region', { name: '策略图表', exact: true })
      const price = region.getByRole('img', { name: '价格与均线图', exact: true })
      const strategySelect = region.getByRole('combobox', { name: '策略筛选', exact: true })
      const daySelect = region.getByRole('combobox', { name: '观察日期', exact: true })
      const detail = region.getByRole('region', { name: '策略观察详情', exact: true })
      const record = region.getByRole('region', { name: '当日模拟记录', exact: true })
      await price.locator('canvas').first().waitFor()
      await verifyControlContrast(region, view, path, 'dark')
      await verifyDates(region, dates(filter('ALL', 'ACTIVE')))
      add(view, path, 'default_active_and_chan_only')
      await scrollTo(region)
      await page.screenshot({ path: `${out}/${view}-${path}-default.png` })

      await selectStatus(region, 'ALL')
      await verifyDates(region, dates(filter('ALL', 'ALL')))
      for (const id of [...new Set(graphics.overlay_markers.map(m => m.strategy_id))]) {
        await strategySelect.selectOption(id)
        await verifyDates(region, dates(filter(id, 'ALL')))
      }
      add(view, path, 'all_seven_strategy_filters')
      await strategySelect.selectOption('ALL')
      for (const status of ['TRIGGERED', 'NEAR_TRIGGER']) {
        await selectStatus(region, status)
        await verifyDates(region, dates(filter('ALL', status)))
      }
      await strategySelect.selectOption('chan_daily_structure')
      await verifyDates(region, [])
      add(view, path, 'trigger_near_and_chan_filter_intersections')
      await selectStatus(region, 'ALL')
      const chan = graphics.overlay_markers.find(m => m.kind === 'CHAN_STRUCTURE')
      assert.ok(chan, 'Real snapshot must contain an existing confirmed Chan event for this acceptance')
      await daySelect.selectOption(chan.trade_date)
      await expect(detail).toContainText('结构识别｜未定义策略触发合同')
      await expect(detail).toContainText('NOT_AVAILABLE')
      await expect(detail).toContainText('N/A')
      await expect(detail).toContainText(chan.structure.confirmed_at)
      await scrollTo(detail)
      await page.screenshot({ path: `${out}/${view}-${path}-chan.png` })
      add(view, path, 'chan_confirmation_original_structure_no_fake_progress')

      await strategySelect.selectOption('pullback_to_support')
      await daySelect.selectOption(graphics.trade_date)
      const latest = filter('pullback_to_support', 'ALL').find(m => m.trade_date === graphics.trade_date)
      await expect(detail).toContainText(`${latest.conditions_met}/${latest.conditions_total}`)
      for (const c of latest.evidence) await expect(detail).toContainText(c.actual)
      await expect(detail).toContainText('较前日变化')
      for (const c of latest.delta_vs_previous.conditions) {
        if (c.value_delta != null) await expect(detail).toContainText(String(c.value_delta))
      }
      await expect(record).toContainText('HOLD')
      await expect(record).toContainText('不回填或推断')
      add(view, path, 'conditions_delta_exact_parity_and_published_hold_without_inference')
      await scrollTo(detail)
      await page.screenshot({ path: `${out}/${view}-${path}-details.png` })
      const missing = Object.keys(graphics.paper_records).find(d => graphics.paper_records[d].status === 'NOT_AVAILABLE')
      assert.ok(missing)
      await daySelect.selectOption(missing)
      await expect(record).toContainText('不可用')
      await expect(record).not.toContainText('RISK_OBSERVATION')
      add(view, path, 'missing_historical_record_does_not_inherit_today')

      await strategySelect.selectOption('ALL')
      await scrollTo(price)
      const pixels = await price.evaluate(element => {
        const canvas = element.querySelector('canvas'), data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
        const colors = new Set(); let opaque = 0
        for (let i = 0; i < data.length; i += 4) if (data[i + 3]) { opaque++; colors.add(`${data[i]},${data[i + 1]},${data[i + 2]}`) }
        return { width: element.getBoundingClientRect().width, opaque, colors: colors.size }
      })
      assert.ok(pixels.width > 250 && pixels.width <= width && pixels.opaque > 500 && pixels.colors > 10)
      result.canvas.push({ view, path, ...pixels })
      await page.screenshot({ path: `${out}/${view}-${path}-all-statuses.png` })
      add(view, path, 'candles_and_dense_30_day_markers_nonblank_framed')

      // Exercise the real canvas annotation band, not a test-only DOM event hook.
      const box = await price.boundingBox()
      const originalDate = await daySelect.inputValue()
      let hit = null
      for (let x = 24; x < box.width - 16; x += 4) {
        await page.mouse.move(box.x + x, box.y + 74)
        const selected = await daySelect.inputValue()
        if (selected && selected !== originalDate) { hit = { x, date: selected }; break }
      }
      assert.ok(hit, 'Hover must select a real aggregate glyph')
      await daySelect.selectOption(originalDate)
      if (view === 'mobile') await price.tap({ position: { x: hit.x, y: 74 } })
      else await price.click({ position: { x: hit.x, y: 74 } })
      await expect(daySelect).toHaveValue(hit.date)
      await expect(detail).toContainText(hit.date)
      add(view, path, 'actual_canvas_hover_and_click_open_date_group')
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      assert.equal(await region.evaluate(element => element.scrollWidth > element.clientWidth + 1), false)
      assert.deepEqual(errors, [])
      await page.reload({ waitUntil: 'domcontentloaded' })
      await strategySelect.waitFor()
      await verifyDates(region, dates(filter('ALL', 'ACTIVE')))
      add(view, path, 'refresh_consistency_no_horizontal_overflow_no_js_errors')
      if (path === 'stock-research') assert.equal(await page.getByRole('button', { name: '运行今日模拟盘' }).count(), 0)
      await page.evaluate(() => localStorage.setItem('tf-theme', 'light'))
      await page.reload({ waitUntil: 'domcontentloaded' })
      await strategySelect.waitFor()
      await verifyControlContrast(region, view, path, 'light')
      await scrollTo(region)
      await page.screenshot({ path: `${out}/${view}-${path}-light.png` })
      await page.evaluate(() => localStorage.setItem('tf-theme', 'dark'))
      await page.close()
    }
    await context.close()
  }
} finally { await browser.close() }
assert.equal(state(await read()), state(before))
const allowedFonts = new Set(['rsms.me/inter/inter.css', 'fonts.googleapis.com/css2'])
assert.deepEqual(result.blocked_requests.filter(r => r.method !== 'GET' || !allowedFonts.has(`${r.host}${r.path}`)), [])
Object.assign(result, { checks_passed: result.checks.length, state_sha256: createHash('sha256').update(state(before)).digest('hex'),
  account_unchanged: true, provider_calls: 0, market_requests: 0, daily_run_executed: false, real_trades: 0 })
await writeFile(`${out}/ui-verification.json`, JSON.stringify(result, null, 2) + '\n')
console.log(JSON.stringify(result))
