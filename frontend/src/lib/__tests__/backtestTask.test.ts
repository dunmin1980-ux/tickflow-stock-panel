import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { connectivityStore } from '../connectivity'
import { etagStore } from '../etagStore'
import {
  clearBacktest,
  confirmBacktestSummary,
  startBacktest,
  useBacktestTask,
} from '../backtestTask'
import { workspaceStatusStore } from '../workspace'

const CURRENT_REVISION = 'b'.repeat(64)
const SAVED_REVISION = 'c'.repeat(64)

const pendingSummary = {
  id: 'bt-1',
  task: 'strategy_backtest',
  strategy_id: 'macd_golden',
  parameters_digest: 'a'.repeat(64),
  stats: { total_return: 0.1 },
  started_at: '2026-07-22T01:00:00Z',
  finished_at: '2026-07-22T01:01:00Z',
  data_as_of: '2026-07-22',
  engine: 'tickflow-strategy-backtest-v1',
  execution_target: 'local',
}

const donePayload = {
  run_id: 'run-1',
  config: {},
  stats: {},
  equity_curve: [],
  drawdown_curve: [],
  trades: [],
  per_symbol_stats: [],
  strategy_info: {},
  elapsed_ms: 1,
  error: null,
  summary_sync: {
    status: 'confirmation_required',
    current_revision: CURRENT_REVISION,
    pending_summary: pendingSummary,
  },
}

class FakeEventSource {
  static instances: FakeEventSource[] = []
  onopen: (() => void) | null = null
  listeners = new Map<string, (event: MessageEvent) => void>()

  constructor(public readonly url: string) {
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, listener)
  }

  close() {}

  emit(type: string, data: unknown) {
    this.listeners.get(type)?.(new MessageEvent(type, { data: JSON.stringify(data) }))
  }
}

function jsonResponse(body: unknown, status = 200, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

function savedSnapshot() {
  return {
    resource: 'backtest_summaries',
    revision: SAVED_REVISION,
    updated_at: '2026-07-22T01:02:00Z',
    data: { summaries: [pendingSummary] },
  }
}

function finishWithPendingSummary() {
  startBacktest({ strategy_id: 'macd_golden' })
  FakeEventSource.instances.at(-1)?.emit('done', donePayload)
}

describe('backtest summary confirmation', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', FakeEventSource)
    FakeEventSource.instances = []
    localStorage.clear()
    etagStore.clear()
    connectivityStore.markOnline('2026-07-22T01:00:00Z')
    workspaceStatusStore.markOnline('2026-07-22T01:00:00Z')
    clearBacktest()
  })

  afterEach(() => {
    clearBacktest()
    etagStore.clear()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    connectivityStore.markOnline()
    workspaceStatusStore.markOnline()
  })

  it('preserves SSE confirmation data and saves only the pending summary once', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      jsonResponse(savedSnapshot(), 200, { ETag: `"${SAVED_REVISION}"` }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useBacktestTask())

    act(() => finishWithPendingSummary())

    expect(result.current?.summaryConfirmation).toMatchObject({
      pendingSummary,
      currentRevision: CURRENT_REVISION,
      isSaving: false,
    })

    let saved = false
    await act(async () => {
      saved = await confirmBacktestSummary()
    })

    expect(saved).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/workspace/resources/backtest_summaries/commands',
    )
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect((init.headers as Headers).get('If-Match')).toBe(`"${CURRENT_REVISION}"`)
    expect(JSON.parse(init.body as string)).toEqual({
      operation: 'append',
      payload: pendingSummary,
    })
    expect(result.current?.summaryConfirmation).toBeNull()
    expect(result.current?.result?.summary_sync).toEqual({
      status: 'saved',
      revision: SAVED_REVISION,
    })
  })

  it('keeps the pending summary when confirmation is attempted offline', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useBacktestTask())
    act(() => finishWithPendingSummary())
    connectivityStore.markOffline()

    let saved = true
    await act(async () => {
      saved = await confirmBacktestSummary()
    })

    expect(saved).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(result.current?.summaryConfirmation).toMatchObject({
      pendingSummary,
      currentRevision: CURRENT_REVISION,
      isSaving: false,
    })
    expect(result.current?.summaryConfirmation?.error).toContain('离线')
  })

  it('keeps the pending summary and adopts the returned revision after another 412', async () => {
    const nextRevision = 'd'.repeat(64)
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(
      { code: 'WORKSPACE_REVISION_CONFLICT' },
      412,
      { ETag: `"${nextRevision}"` },
    ))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useBacktestTask())
    act(() => finishWithPendingSummary())

    let saved = true
    await act(async () => {
      saved = await confirmBacktestSummary()
    })

    expect(saved).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(result.current?.summaryConfirmation).toMatchObject({
      pendingSummary,
      currentRevision: nextRevision,
      isSaving: false,
    })
    expect(result.current?.summaryConfirmation?.error).toContain('变化')
  })
})
