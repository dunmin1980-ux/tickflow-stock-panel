import { createElement, type PropsWithChildren } from 'react'
import { act, render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { WorkspaceEvents } from '../useWorkspaceEvents'
import { workspaceApi } from '../workspace'

class FakeEventSource {
  static instances: FakeEventSource[] = []

  readonly url: string
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
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

  close() {}

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
})
