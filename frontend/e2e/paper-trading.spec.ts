import { expect, test } from '@playwright/test'
import { installMockApi, paperTradingDashboard } from './mock-api'

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
  await expect(run).toBeEnabled()
  await expect(page.getByText('点击运行后将校验并准备今日单票日线输入')).toBeVisible()
  await run.evaluate((button: HTMLButtonElement) => {
    button.click()
    button.click()
  })

  await expect(page.getByText('本次已写入日结')).toBeVisible()
  expect(runRequests).toBe(1)
  await expect(page.getByText('MIXED OBSERVATION').first()).toBeVisible()
  await expect(page.getByText('HOLD').first()).toBeVisible()
  await expect(page.getByText('ChenQuant Paper Trading Daily')).toBeVisible()
  await expect(page.getByLabel('权益曲线')).toBeVisible()

  await page.reload()
  await expect(page.getByText('MIXED OBSERVATION').first()).toBeVisible()
  await expect(page.getByRole('button', { name: '运行今日模拟盘' })).toBeDisabled()
  await expect(page.getByText('该数据日已完成，重复运行将进行幂等校验')).toBeVisible()

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
    await expect(page.getByText('Visual v1 不使用此 Key')).toBeVisible()
    await expect(page.getByText('Provider 已延后 · Visual v1 不调用')).toBeVisible()
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
