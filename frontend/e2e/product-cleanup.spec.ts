import { expect, test } from '@playwright/test'

import { installMockApi } from './mock-api'

const LEGACY_PRIMARY_LABELS = [
  '自选',
  '策略',
  '回测',
  '连板梯队',
  '概念分析',
  '行业分析',
  '财务分析',
] as const

const SAAS_COPY = /(?:Starter\+?|Pro\+?|Expert)(?:\s|套餐|批量|$)|升级套餐|需要\s*Expert|分时数据权限需/

test('desktop product shell exposes only the daily-use information architecture', async ({ page, isMobile }) => {
  test.skip(isMobile, 'desktop primary navigation contract')
  const unexpectedRequests = await installMockApi(page)

  await page.goto('/')

  await expect(page).toHaveURL(/\/paper-trading$/)
  const primary = page.getByRole('navigation', { name: '主导航' })
  await expect(primary.getByRole('link')).toHaveCount(6)
  for (const label of ['今日工作台', '模拟盘', '个股研究', '市场', '监控', '数据']) {
    await expect(primary.getByRole('link', { name: label, exact: true })).toBeVisible()
  }
  for (const label of LEGACY_PRIMARY_LABELS) {
    await expect(primary.getByRole('link', { name: label, exact: true })).toHaveCount(0)
  }

  await expect(page.getByText('本地模式', { exact: true })).toBeVisible()
  await expect(page.getByText('Backend Online', { exact: true })).toBeVisible()
  await expect(page.getByText('云端模式', { exact: true })).toHaveCount(0)
  await expect(page.getByText('SIMULATION ONLY', { exact: true }).first()).toBeVisible()
  expect(unexpectedRequests).toEqual([])
})

test('formal daily pages describe actual Visual v1 capabilities without provider or pricing UX', async ({ page, isMobile }) => {
  test.skip(isMobile, 'desktop route contract; mobile workbench is covered separately')
  let minuteRequests = 0
  const providerSurfaceRequests: string[] = []
  page.on('request', request => {
    const pathname = new URL(request.url()).pathname
    if (pathname.includes('/minute')) minuteRequests += 1
    if (pathname === '/api/intraday/status' || pathname === '/api/settings/data-sources') {
      providerSurfaceRequests.push(pathname)
    }
  })
  const unexpectedRequests = await installMockApi(page)

  await page.goto('/stock-analysis')
  await expect(page.getByRole('heading', { name: '个股研究' })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('AI Provider DEFERRED', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /AI 个股分析|生成.*分析/ })).toHaveCount(0)
  await page.getByRole('button', { name: /派林生物 个股日线复盘/ }).click()
  await expect(page.getByRole('dialog', { name: '历史研究报告' })).toBeVisible()
  await expect(page.getByText('派林生物确定性研究记录')).toBeVisible()
  await page.getByRole('button', { name: '关闭历史研究报告' }).click()

  await page.goto('/indices')
  await expect(page.getByText(SAAS_COPY)).toHaveCount(0)
  await expect(page.getByText('分钟 K', { exact: true })).toBeVisible()
  await expect(page.getByText('DISABLED', { exact: true })).toBeVisible()

  await page.goto('/data')
  for (const [label, status] of [
    ['股票列表', 'READY'],
    ['日 K', 'READY'],
    ['除权因子', 'READY'],
    ['Enriched', 'READY'],
    ['指数', 'READY'],
    ['分钟 K', 'DISABLED'],
    ['财务数据', 'NOT CONNECTED'],
  ] as const) {
    const item = page.getByRole('listitem').filter({ hasText: label })
    await expect(item.getByText(status, { exact: true })).toBeVisible()
  }
  await expect(page.getByText(SAAS_COPY)).toHaveCount(0)

  await page.goto('/monitor')
  await expect(page.getByRole('heading', { name: '监控中心' })).toBeVisible({ timeout: 15_000 })

  await page.goto('/financials')
  await expect(page.getByRole('heading', { name: 'Financial Data' })).toBeVisible()
  await expect(page.getByText('NOT CONNECTED', { exact: true })).toBeVisible()
  await expect(page.getByText(SAAS_COPY)).toHaveCount(0)

  await page.goto('/review')
  await expect(page.getByRole('heading', { name: '每日复盘' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'ChenQuant Daily' })).toBeVisible()
  await expect(page.getByText('AI Provider DEFERRED', { exact: false }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: /生成复盘|生成今日复盘/ })).toHaveCount(0)

  await page.goto('/settings')
  await expect(page.getByText('Paper Trading Engine')).toBeVisible()
  await expect(page.getByText('AI Provider')).toBeVisible()
  await expect(page.getByText('Real Trading')).toBeVisible()
  await expect(page.getByPlaceholder('粘贴 TickFlow API Key')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '保存配置' })).toHaveCount(0)
  for (const legacyTab of ['实时监控', '数据源', '扩展页面', '信号库', '菜单设置', '系统设置']) {
    await expect(page.getByRole('button', { name: legacyTab })).toHaveCount(0)
  }

  expect(minuteRequests).toBe(0)
  expect(providerSurfaceRequests).toEqual([])
  expect(unexpectedRequests).toEqual([])
})

test('mobile shell keeps the core workbench usable without horizontal overflow', async ({ page, isMobile }) => {
  test.skip(!isMobile, 'mobile product shell contract')
  const unexpectedRequests = await installMockApi(page)

  await page.goto('/paper-trading')
  await expect(page.getByRole('button', { name: '运行今日模拟盘' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: '手机主导航' })).toBeVisible()
  await expect(page.getByText('SIMULATION ONLY', { exact: true }).first()).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  expect(unexpectedRequests).toEqual([])
})

test('mobile market route is readable without horizontal overflow', async ({ page, isMobile }, testInfo) => {
  test.skip(!isMobile, 'mobile market layout contract')
  const unexpectedRequests = await installMockApi(page)

  await page.goto('/indices')
  await expect(page.getByRole('heading', { name: '市场' })).toBeVisible({ timeout: 15_000 })
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  expect(unexpectedRequests).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('mobile-market.png'), fullPage: true })
})

test('Visual data sync disables persisted minute sync before starting the daily pipeline', async ({ page, isMobile }) => {
  test.skip(isMobile, 'desktop data operation contract')
  const sequence: string[] = []
  const unexpectedRequests = await installMockApi(page, {
    handleRoute: async (route, url) => {
      if (url.pathname === '/api/settings/preferences/minute-sync') {
        const body = route.request().postDataJSON() as { minute_sync_enabled?: boolean }
        sequence.push(`minute=${String(body.minute_sync_enabled)}`)
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ minute_sync_enabled: false }) })
        return true
      }
      if (url.pathname === '/api/pipeline/run') {
        sequence.push('pipeline')
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'visual-daily-job', reused: false }) })
        return true
      }
      if (url.pathname === '/api/pipeline/jobs/visual-daily-job') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ id: 'visual-daily-job', status: 'succeeded', stage: 'done', pct: 100, stage_pct: 100, logs: [] }),
        })
        return true
      }
      return false
    },
  })

  await page.goto('/data')
  await page.getByRole('button', { name: '立即同步' }).click()
  await expect.poll(() => sequence).toEqual(['minute=false', 'pipeline'])
  expect(unexpectedRequests).toEqual([])
})
