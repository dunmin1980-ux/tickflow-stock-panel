import { devices, expect, test, type BrowserContext, type Page, type Route } from '@playwright/test'

import { installMockApi } from './mock-api'

type ClientId = 'desktop' | 'mobile'

interface CommandRecord {
  client: ClientId
  ifMatch: string | null
  operation: string
  status: number
  symbol: string | null
}

interface WorkspaceSnapshot {
  resource: string
  revision: string
  updated_at: string
  data: Record<string, unknown>
}

interface CommandGate {
  remaining: number
  ready: Promise<void>
  release: () => void
}

interface PendingEventStream {
  route: Route
  complete: () => void
}

const symbolNames: Record<string, string> = {
  '000403.SZ': '派林生物',
  '600489.SH': '中金黄金',
  '300059.SZ': '东方财富',
  '300750.SZ': '宁德时代',
  '600000.SH': '浦发银行',
}

function revision(value: number): string {
  return value.toString(16).padStart(64, '0')
}

function quoted(value: string): string {
  return `"${value}"`
}

async function fulfillJson(route: Route, body: unknown, status = 200, headers: Record<string, string> = {}) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    headers: { 'cache-control': 'private, no-store', ...headers },
    body: JSON.stringify(body),
  })
}

class WorkspaceMock {
  readonly workspaceId = 'workspace-e2e-shared'
  readonly logins = new Map<ClientId, string>()
  readonly commands: CommandRecord[] = []
  private readonly eventStreams = new Map<ClientId, PendingEventStream>()
  private readonly symbols: string[] = []
  private revisionNumber = 1
  private commandGate: CommandGate | null = null

  get currentRevision(): string {
    return revision(this.revisionNumber)
  }

  get commandCount(): number {
    return this.commands.length
  }

  get eventStreamCount(): number {
    return this.eventStreams.size
  }

  armConcurrentCommands(count = 2): void {
    let release = () => undefined
    const ready = new Promise<void>((resolve) => { release = resolve })
    this.commandGate = { remaining: count, ready, release }
  }

  private async awaitConcurrentCommands(): Promise<void> {
    const gate = this.commandGate
    if (!gate) return
    gate.remaining -= 1
    if (gate.remaining === 0) {
      this.commandGate = null
      gate.release()
    }
    await gate.ready
  }

  private authenticated(route: Route): boolean {
    return (route.request().headers()['cookie'] ?? '').includes(`tickflow_e2e_workspace=${this.workspaceId}`)
  }

  private async holdEventStream(client: ClientId, route: Route): Promise<boolean> {
    const existing = this.eventStreams.get(client)
    if (existing) {
      this.eventStreams.delete(client)
      try {
        await existing.route.abort('aborted')
      } catch {
        // A StrictMode cleanup may already have canceled the prior browser request.
      } finally {
        existing.complete()
      }
    }
    return new Promise<boolean>((resolve) => {
      this.eventStreams.set(client, { route, complete: () => resolve(true) })
    })
  }

  private watchlistSnapshot(): WorkspaceSnapshot {
    return {
      resource: 'watchlist',
      revision: this.currentRevision,
      updated_at: '2026-07-22T12:00:00+08:00',
      data: {
        symbols: this.symbols.map(symbol => ({ symbol, name: symbolNames[symbol] ?? symbol, note: '' })),
      },
    }
  }

  private resourceSnapshot(resource: string): WorkspaceSnapshot {
    if (resource === 'watchlist') return this.watchlistSnapshot()
    if (resource === 'preferences') {
      return {
        resource,
        revision: revision(100),
        updated_at: '2026-07-22T12:00:00+08:00',
        data: {
          preferences: {
            daily_data_provider: 'tickflow',
            nav_hidden: [],
            has_feishu_webhook: false,
            has_feishu_credential_data: false,
            has_wecom_webhook: false,
            has_wecom_bot: false,
            has_wecom_bot_credential_data: false,
          },
        },
      }
    }
    if (resource === 'backtest_summaries') {
      return {
        resource,
        revision: revision(101),
        updated_at: '2026-07-22T12:00:00+08:00',
        data: { summaries: [] },
      }
    }
    return {
      resource,
      revision: revision(resource === 'stock_reports' ? 102 : 103),
      updated_at: '2026-07-22T12:00:00+08:00',
      data: { reports: [] },
    }
  }

  private resources() {
    return {
      watchlist: this.resourceSnapshot('watchlist'),
      preferences: this.resourceSnapshot('preferences'),
      stock_reports: this.resourceSnapshot('stock_reports'),
      market_recaps: this.resourceSnapshot('market_recaps'),
      backtest_summaries: this.resourceSnapshot('backtest_summaries'),
    }
  }

  private async broadcastWatchlistChange(): Promise<void> {
    const payload = { resource: 'watchlist', revision: this.currentRevision }
    const streams = [...this.eventStreams.values()]
    this.eventStreams.clear()
    await Promise.all(streams.map(async ({ route, complete }) => {
      try {
        await route.fulfill({
          status: 200,
          headers: {
            'cache-control': 'private, no-store',
            'content-type': 'text/event-stream',
          },
          body: `retry: 10\nevent: resource_changed\ndata: ${JSON.stringify(payload)}\n\n`,
        })
      } finally {
        complete()
      }
    }))
  }

  async handle(client: ClientId, route: Route, url: URL): Promise<boolean> {
    const request = route.request()
    const { pathname } = url

    if (pathname === '/api/auth/status') {
      await fulfillJson(route, { configured: true, authenticated: this.authenticated(route) })
      return true
    }
    if (pathname === '/api/auth/login' && request.method() === 'POST') {
      this.logins.set(client, this.workspaceId)
      await fulfillJson(route, { ok: true }, 200, {
        'set-cookie': `tickflow_e2e_workspace=${this.workspaceId}; Path=/; HttpOnly; SameSite=Strict`,
      })
      return true
    }
    if (pathname === '/api/client/status') {
      await route.fulfill({ status: 404, body: '' })
      return true
    }
    if (pathname === '/api/intraday/stream') {
      await route.fulfill({ status: 204, body: '' })
      return true
    }

    if (pathname.startsWith('/api/workspace/') && !this.authenticated(route)) {
      await fulfillJson(route, { detail: 'authentication required' }, 401)
      return true
    }
    if (pathname === '/api/workspace/events' && request.method() === 'GET') {
      return this.holdEventStream(client, route)
    }
    if (pathname === '/api/workspace/bootstrap') {
      await fulfillJson(route, {
        schema_version: 1,
        server_time: '2026-07-22T12:00:00+08:00',
        data_as_of: '2026-07-21',
        mode: 'cloud',
        capabilities: { label: 'Pro', capabilities: {} },
        resources: this.resources(),
      })
      return true
    }
    if (pathname === '/api/workspace/revisions') {
      await fulfillJson(route, {
        server_time: '2026-07-22T12:00:00+08:00',
        resources: Object.fromEntries(
          Object.entries(this.resources()).map(([name, snapshot]) => [name, snapshot.revision]),
        ),
      })
      return true
    }

    const resourceMatch = pathname.match(/^\/api\/workspace\/resources\/([^/]+)$/)
    if (resourceMatch && request.method() === 'GET') {
      const snapshot = this.resourceSnapshot(resourceMatch[1])
      await fulfillJson(route, snapshot, 200, { etag: quoted(snapshot.revision) })
      return true
    }

    const commandMatch = pathname.match(/^\/api\/workspace\/resources\/([^/]+)\/commands$/)
    if (commandMatch && request.method() === 'POST') {
      const body = request.postDataJSON() as { operation?: string; payload?: { symbol?: string } }
      const ifMatch = request.headers()['if-match'] ?? null
      const operation = body.operation ?? ''
      const symbol = body.payload?.symbol ?? null
      await this.awaitConcurrentCommands()
      if (commandMatch[1] !== 'watchlist' || ifMatch !== quoted(this.currentRevision)) {
        this.commands.push({ client, ifMatch, operation, symbol, status: 412 })
        await fulfillJson(route, { code: 'WORKSPACE_REVISION_CONFLICT', detail: 'stale revision' }, 412, {
          etag: quoted(this.currentRevision),
        })
        return true
      }

      if (operation === 'add' && symbol && !this.symbols.includes(symbol)) this.symbols.push(symbol)
      this.revisionNumber += 1
      const snapshot = this.watchlistSnapshot()
      this.commands.push({ client, ifMatch, operation, symbol, status: 200 })
      await fulfillJson(route, snapshot, 200, { etag: quoted(snapshot.revision) })
      await this.broadcastWatchlistChange()
      return true
    }

    if (pathname === '/api/watchlist/enriched') {
      await fulfillJson(route, {
        rows: this.symbols.map((symbol, index) => ({
          symbol,
          name: symbolNames[symbol] ?? symbol,
          close: 10 + index,
          change_pct: 0.5 + index,
          amount: 100_000_000 + index,
        })),
        as_of: '2026-07-21',
        elapsed_ms: 1,
      })
      return true
    }
    if (pathname === '/api/kline/instruments/search') {
      const query = (url.searchParams.get('q') ?? '').toUpperCase()
      const results = Object.entries(symbolNames)
        .filter(([symbol, name]) => symbol.includes(query) || name.includes(query))
        .map(([symbol, name]) => ({ symbol, name, code: symbol.slice(0, 6), asset_type: 'stock' }))
      await fulfillJson(route, { results })
      return true
    }
    if (pathname === '/api/kline/daily-batch') {
      await fulfillJson(route, { data: Object.fromEntries(this.symbols.map(symbol => [symbol, []])) })
      return true
    }
    return false
  }
}

async function installClient(
  context: BrowserContext,
  client: ClientId,
  workspace: WorkspaceMock,
): Promise<{ page: Page; unexpected: string[] }> {
  const page = await context.newPage()
  const unexpected = await installMockApi(page, {
    installEventSource: false,
    handleRoute: (route, url) => workspace.handle(client, route, url),
  })
  return { page, unexpected }
}

async function login(page: Page): Promise<void> {
  await page.goto('/login')
  await expect(page.getByText('登录访问', { exact: true })).toBeVisible()
  await page.getByPlaceholder('访问密码').fill('e2e-local-fixture')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/paper-trading$/)
  await page.goto('/watchlist')
  await expect(page.getByRole('heading', { name: '自选股', exact: true })).toBeVisible()
}

async function addThroughUi(page: Page, symbol: string): Promise<void> {
  const search = page.getByPlaceholder('搜索…')
  await search.fill(symbol)
  const result = page.locator('div').filter({ hasText: symbol }).filter({
    has: page.getByTitle('加入自选'),
  }).last()
  await expect(result).toBeVisible()
  await result.getByTitle('加入自选').click()
}

test('two authenticated clients synchronize, conflict once, and reject offline writes before fetch', async ({ browser }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-1440x900', 'The test creates both target contexts itself.')

  const workspace = new WorkspaceMock()
  const desktopContext = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const mobileContext = await browser.newContext({ ...devices['iPhone 14'] })
  const desktop = await installClient(desktopContext, 'desktop', workspace)
  const mobile = await installClient(mobileContext, 'mobile', workspace)

  try {
    await Promise.all([login(desktop.page), login(mobile.page)])
    expect(Object.fromEntries(workspace.logins)).toEqual({
      desktop: workspace.workspaceId,
      mobile: workspace.workspaceId,
    })
    await expect.poll(() => workspace.eventStreamCount).toBe(2)

    await addThroughUi(desktop.page, '000403.SZ')
    await expect(mobile.page.getByText('000403.SZ', { exact: true }).first()).toBeVisible()
    expect(workspace.commands.at(-1)).toMatchObject({ client: 'desktop', symbol: '000403.SZ', status: 200 })
    await expect.poll(() => workspace.eventStreamCount).toBe(2)

    await addThroughUi(mobile.page, '600489.SH')
    await expect(desktop.page.getByText('600489.SH', { exact: true }).first()).toBeVisible()
    expect(workspace.commands.at(-1)).toMatchObject({ client: 'mobile', symbol: '600489.SH', status: 200 })
    await expect.poll(() => workspace.eventStreamCount).toBe(2)

    const staleRevision = workspace.currentRevision
    workspace.armConcurrentCommands()
    await Promise.all([
      addThroughUi(desktop.page, '300059.SZ'),
      addThroughUi(mobile.page, '300750.SZ'),
    ])
    await expect.poll(
      () => workspace.commands.filter(item => item.ifMatch === quoted(staleRevision)).length,
    ).toBe(2)
    const concurrent = workspace.commands.filter(item => item.ifMatch === quoted(staleRevision))
    expect(concurrent.map(item => item.status).sort()).toEqual([200, 412])
    const winningSymbol = workspace.commands.find(
      item => item.ifMatch === quoted(staleRevision) && item.status === 200,
    )?.symbol
    expect(winningSymbol).toBeTruthy()
    await expect(desktop.page.getByText(winningSymbol!, { exact: true }).first()).toBeVisible()
    await expect(mobile.page.getByText(winningSymbol!, { exact: true }).first()).toBeVisible()
    await mobile.page.waitForTimeout(250)
    expect(workspace.commands.filter(item => item.ifMatch === quoted(staleRevision))).toHaveLength(2)

    const beforeOfflineWrite = workspace.commandCount
    const offlineCommandRequests: string[] = []
    mobile.page.on('request', request => {
      if (new URL(request.url()).pathname.endsWith('/commands')) {
        offlineCommandRequests.push(request.url())
      }
    })
    const search = mobile.page.getByPlaceholder('搜索…')
    await search.fill('600000.SH')
    const offlineAdd = mobile.page.getByTitle(/加入自选|离线只读，暂不能修改自选股/).first()
    await expect(offlineAdd).toBeVisible()
    await mobile.page.evaluate(() => {
      Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => false })
      window.dispatchEvent(new Event('offline'))
    })
    await expect(mobile.page.getByRole('status').filter({
      hasText: 'TickFlow 后端未运行，当前为只读模式',
    })).toBeVisible()
    await expect(offlineAdd).toBeDisabled()
    await offlineAdd.dispatchEvent('click')
    await mobile.page.waitForTimeout(100)
    expect(workspace.commandCount).toBe(beforeOfflineWrite)
    expect(offlineCommandRequests).toEqual([])

    expect(desktop.unexpected).toEqual([])
    expect(mobile.unexpected).toEqual([])
  } finally {
    await desktopContext.close()
    await mobileContext.close()
  }
})
