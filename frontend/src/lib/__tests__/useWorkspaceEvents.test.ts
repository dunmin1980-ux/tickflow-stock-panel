import { createElement, type PropsWithChildren } from 'react'
import { act, render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { WorkspaceEvents } from '../useWorkspaceEvents'
import { connectivityStore } from '../connectivity'
import { workspaceApi, workspaceStatusStore } from '../workspace'

class FakeEventSource {
  static instances: FakeEventSource[] = []

  readonly url: string
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  private listeners = new Map<string, Set<(event: MessageEvent) => void>>()

  constructor(url: string | URL) {
    this.url = String(url)
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    const handler = listener as (event: MessageEvent) => void
    const listeners = this.listeners.get(type) ?? new Set()
    listeners.add(handler)
    this.listeners.set(type, listeners)
  }

  close() {
    this.closed = true
  }

  emit(type: string, payload: unknown) {
    const event = new MessageEvent(type, { data: JSON.stringify(payload) })
    this.listeners.get(type)?.forEach(listener => listener(event))
  }
}

function renderBridge(queryClient: QueryClient) {
  function Wrapper({ children }: PropsWithChildren) {
    return createElement(QueryClientProvider, { client: queryClient }, children)
  }
  return render(createElement(WorkspaceEvents), { wrapper: Wrapper })
}

describe('workspace event refresh', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.spyOn(workspaceApi, 'bootstrap').mockResolvedValue({
      schema_version: 1,
      server_time: '2026-07-21T09:00:00+08:00',
      data_as_of: '2026-07-20',
      mode: 'cloud',
      capabilities: { label: 'Free', capabilities: {} },
      resources: {
        watchlist: { revision: 'a'.repeat(64), updated_at: '2026-07-21T09:00:00+08:00', data: { symbols: [] } },
        preferences: { revision: 'b'.repeat(64), updated_at: '2026-07-21T09:00:00+08:00', data: { preferences: {} } },
        stock_reports: { revision: 'c'.repeat(64), updated_at: '2026-07-21T09:00:00+08:00', data: { reports: [] } },
        market_recaps: { revision: 'd'.repeat(64), updated_at: '2026-07-21T09:00:00+08:00', data: { reports: [] } },
        backtest_summaries: { revision: 'e'.repeat(64), updated_at: '2026-07-21T09:00:00+08:00', data: { summaries: [] } },
      },
    })
    vi.spyOn(workspaceApi, 'revisions').mockResolvedValue({
      server_time: '2026-07-21T09:02:00+08:00',
      resources: { watchlist: 'f'.repeat(64) },
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('uses a same-origin stream and invalidates only the changed resource queries', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    renderBridge(queryClient)

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))
    expect(FakeEventSource.instances[0].url).toBe('/api/workspace/events')

    act(() => {
      FakeEventSource.instances[0].emit('resource_changed', {
        type: 'resource_changed',
        resource: 'watchlist',
        revision: 'f'.repeat(64),
      })
    })

    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ['watchlist'] })
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ['watchlist-enriched'] })
    })
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ['review-reports'] })
  })

  it('pulls revisions for a resync_required event', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    renderBridge(queryClient)
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))

    act(() => {
      FakeEventSource.instances[0].emit('resync_required', { type: 'resync_required' })
    })

    await waitFor(() => expect(workspaceApi.revisions).toHaveBeenCalledTimes(1))
  })

  it('reconnects with the first one-second backoff after a stream failure', async () => {
    vi.useFakeTimers()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    renderBridge(queryClient)
    await act(async () => { await Promise.resolve() })
    expect(FakeEventSource.instances).toHaveLength(1)

    act(() => FakeEventSource.instances[0].onerror?.())
    act(() => vi.advanceTimersByTime(999))
    expect(FakeEventSource.instances).toHaveLength(1)
    act(() => vi.advanceTimersByTime(1))
    expect(FakeEventSource.instances).toHaveLength(2)
  })

  it('reconciles the disconnect window before restoring online state and stopping polling', async () => {
    let resolveRevisions!: (value: Awaited<ReturnType<typeof workspaceApi.revisions>>) => void
    vi.mocked(workspaceApi.revisions).mockReturnValueOnce(new Promise(resolve => {
      resolveRevisions = resolve
    }))
    workspaceStatusStore.markOffline()
    connectivityStore.markOffline()

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    renderBridge(queryClient)
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1))

    act(() => FakeEventSource.instances[0].onopen?.())
    expect(workspaceApi.revisions).toHaveBeenCalledTimes(1)
    expect(workspaceStatusStore.getSnapshot().offlineReadonly).toBe(true)

    await act(async () => {
      resolveRevisions({
        server_time: '2026-07-21T09:02:00+08:00',
        resources: { watchlist: 'f'.repeat(64) },
      })
      await Promise.resolve()
    })

    expect(workspaceStatusStore.getSnapshot().offlineReadonly).toBe(false)
    expect(connectivityStore.getSnapshot().mode).toBe('online')
  })

  it('keeps polling and retries when the onopen revision reconciliation fails', async () => {
    vi.useFakeTimers()
    vi.mocked(workspaceApi.revisions).mockRejectedValueOnce(new TypeError('network down'))
    workspaceStatusStore.markOffline()
    connectivityStore.markOffline()

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    renderBridge(queryClient)
    await act(async () => { await Promise.resolve() })
    expect(FakeEventSource.instances).toHaveLength(1)

    await act(async () => {
      FakeEventSource.instances[0].onopen?.()
      await Promise.resolve()
    })

    expect(workspaceStatusStore.getSnapshot().offlineReadonly).toBe(true)
    expect(FakeEventSource.instances[0].closed).toBe(true)
    act(() => vi.advanceTimersByTime(1_000))
    expect(FakeEventSource.instances).toHaveLength(2)
  })
})
