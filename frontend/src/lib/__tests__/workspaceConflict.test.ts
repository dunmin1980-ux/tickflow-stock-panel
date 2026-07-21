import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { connectivityStore } from '../connectivity'
import { etagStore } from '../etagStore'
import {
  WorkspaceApiError,
  WorkspaceOfflineError,
  workspaceApi,
  workspaceStatusStore,
} from '../workspace'

const REVISION = 'a'.repeat(64)
const NEXT_REVISION = 'b'.repeat(64)

function jsonResponse(body: unknown, status = 200, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

describe('workspace conditional writes', () => {
  beforeEach(() => {
    etagStore.clear()
    connectivityStore.markOnline('2026-07-21T09:00:00.000Z')
    workspaceStatusStore.markOnline('2026-07-21T09:00:00.000Z')
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    connectivityStore.markOnline()
  })

  it('does not retry a stale write or promote the server revision to writable state', async () => {
    etagStore.set('watchlist', REVISION)
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(
      { code: 'WORKSPACE_REVISION_CONFLICT', detail: 'Workspace revision is stale' },
      412,
      { ETag: `"${NEXT_REVISION}"` },
    ))
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      workspaceApi.command('watchlist', 'add', { symbol: '000403.SZ' }, REVISION),
    ).rejects.toMatchObject({
      status: 412,
      code: 'WORKSPACE_REVISION_CONFLICT',
      currentRevision: NEXT_REVISION,
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(etagStore.get('watchlist')).toBe(REVISION)
    expect(etagStore.isConflicted('watchlist')).toBe(true)

    await expect(
      workspaceApi.command('watchlist', 'add', { symbol: '300059.SZ' }, REVISION),
    ).rejects.toMatchObject({ status: 412, code: 'WORKSPACE_CONFLICT_PENDING' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('clears a conflict only after a successful resource refresh', async () => {
    etagStore.set('watchlist', REVISION)
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(
        { code: 'WORKSPACE_REVISION_CONFLICT' },
        412,
        { ETag: `"${NEXT_REVISION}"` },
      ))
      .mockResolvedValueOnce(jsonResponse({
        resource: 'watchlist',
        revision: NEXT_REVISION,
        updated_at: '2026-07-21T09:01:00+08:00',
        data: { symbols: [] },
      }, 200, { ETag: `"${NEXT_REVISION}"` }))
      .mockResolvedValueOnce(jsonResponse({
        resource: 'watchlist',
        revision: NEXT_REVISION,
        updated_at: '2026-07-21T09:02:00+08:00',
        data: { symbols: [{ symbol: '000403.SZ' }] },
      }, 200, { ETag: `"${NEXT_REVISION}"` }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      workspaceApi.command('watchlist', 'add', { symbol: '000403.SZ' }, REVISION),
    ).rejects.toMatchObject({ status: 412 })
    expect(etagStore.isConflicted('watchlist')).toBe(true)

    await workspaceApi.get('watchlist')
    expect(etagStore.isConflicted('watchlist')).toBe(false)

    await workspaceApi.command('watchlist', 'add', { symbol: '000403.SZ' }, NEXT_REVISION)
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it.each([
    [428, 'WORKSPACE_PRECONDITION_REQUIRED'],
    [409, 'WORKSPACE_COMMAND_CONFLICT'],
  ])('returns a typed error for HTTP %s', async (status, code) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse({ code }, status)))

    await expect(
      workspaceApi.command('watchlist', 'add', { symbol: '000403.SZ' }, REVISION),
    ).rejects.toBeInstanceOf(WorkspaceApiError)
  })

  it('blocks offline writes before fetch', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    connectivityStore.markOffline()

    await expect(
      workspaceApi.command('watchlist', 'add', { symbol: '000403.SZ' }, REVISION),
    ).rejects.toBeInstanceOf(WorkspaceOfflineError)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('blocks writes when the local gateway is online but the cloud workspace is offline', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    connectivityStore.markOnline()
    workspaceStatusStore.markOffline()

    await expect(
      workspaceApi.command('watchlist', 'add', { symbol: '000403.SZ' }, REVISION),
    ).rejects.toBeInstanceOf(WorkspaceOfflineError)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('sends one strong If-Match and updates the in-memory revision', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse({
      resource: 'watchlist',
      revision: NEXT_REVISION,
      updated_at: '2026-07-21T09:01:00+08:00',
      data: { symbols: [] },
    }, 200, { ETag: `"${NEXT_REVISION}"` }))
    vi.stubGlobal('fetch', fetchMock)

    await workspaceApi.command('watchlist', 'clear', {}, REVISION)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspace/resources/watchlist/commands',
      expect.objectContaining({
        method: 'POST',
        cache: 'no-store',
        credentials: 'same-origin',
        headers: expect.any(Headers),
      }),
    )
    const headers = fetchMock.mock.calls[0][1].headers as Headers
    expect(headers.get('If-Match')).toBe(`"${REVISION}"`)
    expect(etagStore.get('watchlist')).toBe(NEXT_REVISION)
  })

  it('hydrates revisions in memory without writing browser persistence', async () => {
    const storageWrite = vi.spyOn(Storage.prototype, 'setItem')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse({
      schema_version: 1,
      server_time: '2026-07-21T09:00:00+08:00',
      data_as_of: '2026-07-20',
      mode: 'cloud',
      capabilities: { label: 'Free', capabilities: {} },
      resources: {
        watchlist: { revision: REVISION, updated_at: '2026-07-21T09:00:00+08:00', data: { symbols: [] } },
        preferences: { revision: NEXT_REVISION, updated_at: '2026-07-21T09:00:00+08:00', data: { preferences: {} } },
        stock_reports: { revision: REVISION, updated_at: '2026-07-21T09:00:00+08:00', data: { reports: [] } },
        market_recaps: { revision: REVISION, updated_at: '2026-07-21T09:00:00+08:00', data: { reports: [] } },
        backtest_summaries: { revision: REVISION, updated_at: '2026-07-21T09:00:00+08:00', data: { summaries: [] } },
      },
    })))

    await workspaceApi.bootstrap()

    expect(etagStore.get('watchlist')).toBe(REVISION)
    expect(etagStore.get('preferences')).toBe(NEXT_REVISION)
    expect(storageWrite).not.toHaveBeenCalledWith(
      expect.any(String),
      expect.stringContaining(REVISION),
    )
  })
})
