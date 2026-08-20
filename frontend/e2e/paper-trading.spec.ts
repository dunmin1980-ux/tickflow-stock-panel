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
          last_completed_trade_date: '2026-07-31',
          input_readiness: {
            ...paperTradingDashboard.input_readiness,
            status: 'MISSING',
            effective_trade_date: null,
            source_fixture: null,
            message: 'VALIDATED_DAILY_INPUT_REQUIRED',
          },
          decisions: [{
            trade_date: '2026-07-31', decision_at: '2026-07-31T21:20:00+08:00',
            research_signal: 'MIXED_OBSERVATION', paper_action: 'HOLD', pending_execution: false,
            action_id: 'b'.repeat(64), simulation_only: 'SIMULATION ONLY', can_publish: false, trading_advice: false,
          }],
          equity_history: [{
            trade_date: '2026-07-31', cash_cny: '100000.00', market_value_cny: '0.00',
            realized_pnl_cny: '0.00', unrealized_pnl_cny: '0.00', total_equity_cny: '100000.00',
            peak_equity_cny: '100000.00', current_drawdown_cny: '0.00', max_drawdown_cny: '0.00',
            simulation_only: 'SIMULATION ONLY', can_publish: false, trading_advice: false,
          }],
          latest_daily: {
            report_date: '2026-07-31', research_signal: 'MIXED_OBSERVATION', paper_action: 'HOLD',
            pending_action: null, source_fixture: 'DETERMINISTIC_REFERENCE_FIXTURE',
            risk_notes: ['SIMULATION_ONLY'], next_observation_conditions: ['LOAD_NEXT_VALIDATED_DAILY_INPUT'],
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
  await expect(page.getByText('当前日期缺少已验证的离线输入')).toBeVisible()

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  expect(unexpectedRequests).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('paper-trading-workbench.png'), fullPage: true })
})
