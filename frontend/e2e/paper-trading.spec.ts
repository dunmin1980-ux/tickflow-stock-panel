import { expect, test } from '@playwright/test'
import { installMockApi, paperTradingDashboard } from './mock-api'

test('Today Workbench and Paper Account expose distinct product responsibilities', async ({ page }) => {
  const unexpectedRequests = await installMockApi(page)

  await page.goto('/paper-trading')
  await expect(page.getByRole('heading', { name: '今日工作台' })).toBeVisible()
  for (const region of [
    '今日市场状态',
    '今日 Research Signal',
    '今日 Paper Action',
    '今日持仓',
    '今日 PnL',
    '今日待办',
    'ChenQuant Daily 摘要',
    '最近 Signal Timeline',
  ]) {
    await expect(page.getByRole('region', { name: region })).toBeVisible()
  }
  await expect(page.getByRole('button', { name: '运行今日模拟盘' })).toBeVisible()
  await expect(page.getByRole('region', { name: '历史交易' })).toHaveCount(0)
  await expect(page.getByRole('region', { name: '历史决策' })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1)

  await page.goto('/paper-account')
  await expect(page.getByRole('heading', { name: '模拟账户' })).toBeVisible()
  for (const region of ['账户总览', '当前持仓', '历史交易', '历史决策', '账户 PnL']) {
    await expect(page.getByRole('region', { name: region })).toBeVisible()
  }
  await expect(page.getByLabel('权益曲线')).toBeVisible()
  await expect(page.getByRole('button', { name: '运行今日模拟盘' })).toHaveCount(0)
  await expect(page.getByRole('region', { name: '今日待办' })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '返回今日工作台' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1)
  expect(unexpectedRequests).toEqual([])
})

test('one-click Paper Trading run persists across refresh and rejects double click', async ({ page }, testInfo) => {
  let state: Record<string, unknown> = paperTradingDashboard
  let runRequests = 0
  const unexpectedRequests = await installMockApi(page, {
    handleRoute: async (route, url) => {
      if (url.pathname === '/api/paper-trading/dashboard') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(state),
        })
        return true
      }
      if (url.pathname === '/api/paper-trading/run') {
        runRequests += 1
        await new Promise(resolve => setTimeout(resolve, 120))
        state = {
          ...paperTradingDashboard,
          last_completed_trade_date: '2026-08-20',
          input_readiness: {
            ...paperTradingDashboard.input_readiness,
            status: 'ALREADY_PUBLISHED',
            effective_trade_date: '2026-08-20',
            source_fixture: 'VALIDATED_DAILY_INPUT',
            message: 'VALIDATED_DAILY_ALREADY_PUBLISHED',
          },
          decisions: [{
            trade_date: '2026-08-20', decision_at: '2026-08-20T16:20:00+08:00',
            research_signal: 'MIXED_OBSERVATION', paper_action: 'HOLD', pending_execution: false,
            action_id: 'b'.repeat(64), simulation_only: 'SIMULATION ONLY', can_publish: false, trading_advice: false,
          }],
          equity_history: [{
            trade_date: '2026-08-20', cash_cny: '100000.00', market_value_cny: '0.00',
            realized_pnl_cny: '0.00', unrealized_pnl_cny: '0.00', total_equity_cny: '100000.00',
            peak_equity_cny: '100000.00', current_drawdown_cny: '0.00', max_drawdown_cny: '0.00',
            simulation_only: 'SIMULATION ONLY', can_publish: false, trading_advice: false,
          }],
          latest_daily: {
            report_date: '2026-08-20', research_signal: 'MIXED_OBSERVATION', paper_action: 'HOLD',
            pending_action: null, source_fixture: 'VALIDATED_DAILY_INPUT',
            risk_notes: ['SIMULATION_ONLY', 'VALIDATED_DAILY_INPUT'], next_observation_conditions: ['LOAD_NEXT_VALIDATED_DAILY_INPUT'],
          },
          chenquant_daily_markdown: '# ChenQuant Paper Trading Daily\n\nSIMULATION ONLY.',
          run_status: 'DAY_PUBLISHED',
        }
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(state),
        })
        return true
      }
      return false
    },
  })

  await page.goto('/paper-trading')
  const run = page.getByRole('button', { name: '运行今日模拟盘' })
  const tasks = page.getByRole('region', { name: '今日待办' })
  await expect(run).toBeEnabled()
  await expect(tasks.getByText('今日待运行')).toBeVisible()
  await expect(tasks.getByText('点击运行今日模拟盘')).toBeVisible()
  await run.evaluate((button: HTMLButtonElement) => {
    button.click()
    button.click()
  })

  await expect(page.getByRole('status').filter({ hasText: '今日模拟盘已完成' })).toHaveText('今日模拟盘已完成')
  expect(runRequests).toBe(1)
  await expect(page.getByText('MIXED OBSERVATION').first()).toBeVisible()
  await expect(page.getByText('HOLD').first()).toBeVisible()
  const dailySummary = page.getByRole('region', { name: 'ChenQuant Daily 摘要' })
  await expect(dailySummary).toBeVisible()
  await expect(dailySummary).toContainText('Claims')
  await expect(page.getByLabel('权益曲线')).toHaveCount(0)

  await page.reload()
  await expect(page.getByText('MIXED OBSERVATION').first()).toBeVisible()
  await expect(page.getByRole('button', { name: '运行今日模拟盘' })).toBeDisabled()
  await expect(page.getByRole('region', { name: '今日待办' })).toContainText('今日决策已完成')
  await expect(page.getByRole('region', { name: '今日待办' })).toContainText('再次运行不会产生重复交易')

  await page.goto('/paper-account')
  await expect(page.getByLabel('权益曲线')).toBeVisible()
  await expect(page.getByRole('region', { name: '历史决策' })).toContainText('MIXED OBSERVATION')
  await expect(page.getByRole('button', { name: '运行今日模拟盘' })).toHaveCount(0)

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  expect(unexpectedRequests).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('paper-trading-workbench.png'), fullPage: true })
})

test('Visual v1 settings expose runtime boundaries without historical key forms', async ({ page, isMobile }) => {
  let settingsRequests = 0
  let capabilityRequests = 0
  let providerSurfaceRequests = 0
  page.on('request', request => {
    const pathname = new URL(request.url()).pathname
    if (pathname === '/api/settings') settingsRequests += 1
    if (pathname === '/api/capabilities') capabilityRequests += 1
    if (
      pathname === '/api/intraday/status'
      || pathname === '/api/settings/data-sources'
      || pathname === '/api/intraday/indices'
    ) providerSurfaceRequests += 1
  })
  const unexpectedRequests = await installMockApi(page)

  await page.goto('/settings?tab=account')
  const runtime = page.getByRole('region', { name: 'Visual v1 runtime status' })
  await expect(runtime.getByText('Paper Trading Engine')).toBeVisible()
  await expect(runtime.getByText('ACTIVE')).toBeVisible()
  await expect(runtime.getByText('AI Provider')).toBeVisible()
  await expect(runtime.getByText('DEFERRED', { exact: true })).toBeVisible()
  await expect(runtime.getByText('Real Trading')).toBeVisible()
  await expect(runtime.getByText('DISABLED')).toBeVisible()
  await expect(page.getByText('当前 Visual v1 不使用此配置')).toBeVisible()
  await expect(page.getByPlaceholder('粘贴 TickFlow API Key')).toHaveCount(0)
  if (!isMobile) {
    const primary = page.getByRole('navigation', { name: '主导航' })
    await expect(primary.getByRole('link', { name: /Provider|AI配置|实时行情/ })).toHaveCount(0)
  }
  await expect(page.getByText('配置 Key 解锁更多能力')).toHaveCount(0)
  await expect(page.getByText('接入策略生成模型')).toHaveCount(0)

  await page.goto('/settings?tab=ai')
  await expect(page.getByText('当前 Visual v1 不使用此配置')).toBeVisible()
  await expect(page.getByRole('button', { name: '保存配置' })).toHaveCount(0)
  await page.goto('/onboarding')
  await expect(page).toHaveURL(/\/settings\?tab=account$/)
  await expect(page.getByPlaceholder('粘贴 TickFlow API Key')).toHaveCount(0)
  expect(settingsRequests).toBe(0)
  expect(capabilityRequests).toBe(0)
  expect(providerSurfaceRequests).toBe(0)
  expect(unexpectedRequests).toEqual([])
})

test('Paper Trading explains backend unavailability without exposing a write path', async ({ page }) => {
  let runRequests = 0
  await installMockApi(page, {
    handleRoute: async (route, url) => {
      if (url.pathname === '/api/paper-trading/dashboard') {
        await route.abort('failed')
        return true
      }
      if (url.pathname === '/api/paper-trading/run') {
        runRequests += 1
        await route.abort('blockedbyclient')
        return true
      }
      return false
    },
  })

  await page.goto('/paper-trading')
  await expect(page.getByRole('alert')).toContainText(
    'TickFlow 后端未运行，当前为只读模式',
  )
  await expect(page.getByRole('button', { name: '运行今日模拟盘' })).toHaveCount(0)
  expect(runRequests).toBe(0)
})
