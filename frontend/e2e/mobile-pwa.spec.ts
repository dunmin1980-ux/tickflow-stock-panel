import { expect, test } from '@playwright/test'
import { installMockApi } from './mock-api'

const routes = [
  { path: '/watchlist', heading: '自选股' },
  { path: '/stock-analysis', heading: '个股分析' },
  { path: '/review', heading: 'AI 复盘' },
  { path: '/concept-analysis', heading: '概念分析' },
  { path: '/industry-analysis', heading: '行业分析' },
  { path: '/data', heading: '数据' },
] as const

const mobileDestinations = [
  '/paper-trading',
  '/stock-analysis',
  '/review',
  '/concept-analysis',
  '/settings',
]

test('root redirects to the Paper Trading workbench and exposes the responsive shell', async ({ page }, testInfo) => {
  const unexpectedRequests = await installMockApi(page)
  await page.goto('/')
  await expect(page).toHaveURL(/\/paper-trading$/)
  await expect(page.getByRole('heading', { name: '模拟投研工作台', exact: true })).toBeVisible()

  const mobileNav = page.locator('nav[aria-label="手机主导航"]')
  await expect(mobileNav.locator('a')).toHaveCount(5)
  expect(await mobileNav.locator('a').evaluateAll((links) => links.map(link => link.getAttribute('href')))).toEqual(mobileDestinations)

  const isMobile = testInfo.project.name !== 'desktop-1440x900'
  if (isMobile) {
    await expect(mobileNav).toBeVisible()
    await expect(page.locator('aside').first()).toBeHidden()
    for (const link of await mobileNav.locator('a').all()) {
      const box = await link.boundingBox()
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
    }
    const mainPaddingBottom = await page.locator('main').evaluate(element => Number.parseFloat(getComputedStyle(element).paddingBottom))
    expect(mainPaddingBottom).toBeGreaterThanOrEqual(64)
  } else {
    await expect(mobileNav).toBeHidden()
    await expect(page.locator('aside').first()).toBeVisible()
  }

  await page.waitForLoadState('networkidle')
  expect(unexpectedRequests).toEqual([])
})

for (const { path, heading } of routes) {
  test(`${path} renders without horizontal overflow`, async ({ page }, testInfo) => {
    const unexpectedRequests = await installMockApi(page)
    await page.goto(path)
    await expect(page.getByRole('heading', { name: heading, exact: true }).first()).toBeVisible()
    await page.waitForLoadState('networkidle')

    if (path === '/watchlist') {
      const isMobile = testInfo.project.name !== 'desktop-1440x900'
      if (isMobile) {
        const switchToTable = page.locator('button[title="列表视图"]')
        await expect(switchToTable).toBeVisible()
        await switchToTable.click()
        const tableScroller = page.locator('table').first().locator('..')
        await expect(tableScroller).toBeVisible()
        expect(await tableScroller.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true)
        expect(await page.evaluate(() => localStorage.getItem('watchlist_view'))).toBe('"table"')
      } else {
        await expect(page.locator('button[title="卡片视图"]')).toBeVisible()
      }
    }

    if (path === '/review') {
      await page.getByText('2026-07-19', { exact: true }).click()
      await expect(page.getByText('THIS_IS_A_DETERMINISTIC_UNBROKEN_MARKDOWN_TOKEN_USED_TO_VERIFY_MOBILE_OVERFLOW_HANDLING_20260720')).toBeVisible()
    }

    const overflow = await page.evaluate(() => (
      document.documentElement.scrollWidth - document.documentElement.clientWidth
    ))
    expect(overflow).toBeLessThanOrEqual(1)
    expect(unexpectedRequests).toEqual([])

    await page.screenshot({
      path: testInfo.outputPath(`${path.slice(1) || 'root'}.png`),
      fullPage: true,
    })
  })
}
