import { toast } from '@/components/Toast'
import { connectivityStore } from './connectivity'
import { etagStore, quoteRevision, type WorkspaceResourceName } from './etagStore'

export type { WorkspaceResourceName } from './etagStore'
export const WORKSPACE_CONFLICT_EVENT = 'tickflow:workspace-revision-conflict'

export interface WatchlistWorkspaceData {
  symbols: Array<{ symbol: string; added_at?: string; note?: string; name?: string | null }>
}

export interface ClientPreferencesWorkspaceData {
  preferences: Record<string, unknown>
}

export interface ReportMetadata {
  id: string
  symbol?: string
  title?: string
  created_at?: string
  data_as_of?: string
  verification_status?: string
  can_publish?: boolean
  trading_advice?: boolean
}

export interface ReportsWorkspaceData {
  reports: ReportMetadata[]
}

export interface BacktestSummary {
  id: string
  task: string
  strategy_id: string
  parameters_digest: string
  stats: Record<string, number>
  started_at: string
  finished_at: string
  data_as_of: string
  engine: string
  execution_target: string
}

export interface BacktestSummariesWorkspaceData {
  summaries: BacktestSummary[]
}

export interface WorkspaceResourceDataMap {
  watchlist: WatchlistWorkspaceData
  preferences: ClientPreferencesWorkspaceData
  stock_reports: ReportsWorkspaceData
  market_recaps: ReportsWorkspaceData
  backtest_summaries: BacktestSummariesWorkspaceData
}

export interface WorkspaceSnapshot<R extends WorkspaceResourceName = WorkspaceResourceName> {
  resource?: R
  revision: string
  updated_at: string
  data: WorkspaceResourceDataMap[R]
  offline_readonly?: boolean
}

export type WorkspaceResources = {
  [R in WorkspaceResourceName]: WorkspaceSnapshot<R>
}

export interface WorkspaceBootstrap {
  schema_version: number
  server_time: string
  data_as_of: string | null
  mode: string
  capabilities: {
    label: string
    capabilities: Record<string, { rpm?: number | null; batch?: number | null; subscribe?: number | null }>
  }
  resources: WorkspaceResources
  offline_readonly?: boolean
}

export interface WorkspaceRevisions {
  server_time: string
  resources: Partial<Record<WorkspaceResourceName, string>>
}

export interface WorkspaceStatus {
  mode: string
  offlineReadonly: boolean
  lastSuccessfulSync: string | null
  dataAsOf: string | null
}

let status: WorkspaceStatus = {
  mode: 'cloud',
  offlineReadonly: connectivityStore.getSnapshot().mode === 'offline-readonly',
  lastSuccessfulSync: null,
  dataAsOf: null,
}
const statusListeners = new Set<() => void>()

function updateStatus(patch: Partial<WorkspaceStatus>) {
  const next = { ...status, ...patch }
  if (
    next.mode === status.mode &&
    next.offlineReadonly === status.offlineReadonly &&
    next.lastSuccessfulSync === status.lastSuccessfulSync &&
    next.dataAsOf === status.dataAsOf
  ) return
  status = next
  statusListeners.forEach(listener => listener())
}

export const workspaceStatusStore = {
  subscribe(listener: () => void) {
    statusListeners.add(listener)
    return () => statusListeners.delete(listener)
  },
  getSnapshot: () => status,
  markOffline() {
    updateStatus({ offlineReadonly: true })
  },
}

export class WorkspaceApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code?: string,
    public readonly currentRevision?: string,
  ) {
    super(message)
    this.name = 'WorkspaceApiError'
  }
}

export class WorkspaceOfflineError extends Error {
  constructor() {
    super('离线只读模式不允许修改云端工作区')
    this.name = 'WorkspaceOfflineError'
  }
}

function isOffline(): boolean {
  return (
    connectivityStore.getSnapshot().mode === 'offline-readonly' ||
    (typeof navigator !== 'undefined' && navigator.onLine === false)
  )
}

async function responseError(response: Response): Promise<WorkspaceApiError> {
  let code: string | undefined
  let detail: string | undefined
  try {
    const payload = await response.json() as { code?: string; detail?: string; message?: string }
    code = payload.code
    detail = payload.detail ?? payload.message
  } catch {
    // A typed status is still more useful than replacing it with a parse error.
  }
  const messages: Record<number, string> = {
    409: '云端内容与当前操作冲突，请刷新后检查',
    412: '云端内容已变化，请检查后重试',
    428: '请先刷新云端内容后再操作',
  }
  const message = messages[response.status] ?? detail ?? `${response.status} ${response.statusText}`
  return new WorkspaceApiError(message, response.status, code)
}

async function workspaceRequest<T>(path: string, init?: RequestInit): Promise<{ data: T; response: Response }> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      credentials: 'same-origin',
      cache: 'no-store',
      headers: new Headers(init?.headers),
    })
  } catch (error) {
    if (error instanceof TypeError) {
      connectivityStore.markOffline()
      workspaceStatusStore.markOffline()
    }
    throw error
  }
  if (!response.ok) {
    const error = await responseError(response)
    if (error.status === 409 || error.status === 412 || error.status === 428) {
      toast(error.message, 'error')
    }
    throw error
  }
  const data = await response.json() as T
  const syncedAt = new Date().toISOString()
  connectivityStore.markOnline(syncedAt)
  updateStatus({ offlineReadonly: false, lastSuccessfulSync: syncedAt })
  return { data, response }
}

function rememberSnapshot<R extends WorkspaceResourceName>(resource: R, snapshot: WorkspaceSnapshot<R>, response?: Response) {
  etagStore.set(resource, response?.headers.get('ETag') ?? snapshot.revision)
  updateStatus({ offlineReadonly: snapshot.offline_readonly === true })
  return snapshot
}

function rememberBootstrap(bootstrap: WorkspaceBootstrap): WorkspaceBootstrap {
  for (const resource of Object.keys(bootstrap.resources) as WorkspaceResourceName[]) {
    etagStore.set(resource, bootstrap.resources[resource].revision)
  }
  updateStatus({
    mode: bootstrap.mode,
    offlineReadonly: bootstrap.offline_readonly === true ||
      Object.values(bootstrap.resources).some(resource => resource.offline_readonly === true),
    lastSuccessfulSync: new Date().toISOString(),
    dataAsOf: bootstrap.data_as_of,
  })
  return bootstrap
}

export const workspaceApi = {
  async bootstrap(): Promise<WorkspaceBootstrap> {
    const { data } = await workspaceRequest<WorkspaceBootstrap>('/api/workspace/bootstrap')
    return rememberBootstrap(data)
  },

  async get<R extends WorkspaceResourceName>(resource: R): Promise<WorkspaceSnapshot<R>> {
    const { data, response } = await workspaceRequest<WorkspaceSnapshot<R>>(
      `/api/workspace/resources/${resource}`,
    )
    return rememberSnapshot(resource, data, response)
  },

  async command<R extends WorkspaceResourceName>(
    resource: R,
    operation: string,
    payload: Record<string, unknown>,
    revision: string,
  ): Promise<WorkspaceSnapshot<R>> {
    if (isOffline()) {
      workspaceStatusStore.markOffline()
      throw new WorkspaceOfflineError()
    }
    const headers = new Headers({
      'Content-Type': 'application/json',
      'If-Match': quoteRevision(revision),
    })
    let response: Response
    try {
      response = await fetch(`/api/workspace/resources/${resource}/commands`, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers,
        body: JSON.stringify({ operation, payload }),
      })
    } catch (error) {
      if (error instanceof TypeError) {
        connectivityStore.markOffline()
        workspaceStatusStore.markOffline()
      }
      throw error
    }
    if (!response.ok) {
      let code: string | undefined
      let detail: string | undefined
      try {
        const body = await response.json() as { code?: string; detail?: string }
        code = body.code
        detail = body.detail
      } catch {
        // Preserve the HTTP contract even when an intermediary returns invalid JSON.
      }
      const currentRevision = etagStore.set(resource, response.headers.get('ETag'))
      const messages: Record<number, string> = {
        409: '云端内容与当前操作冲突，请刷新后检查',
        412: '云端内容已变化，请检查后重试',
        428: '请先刷新云端内容后再操作',
      }
      const error = new WorkspaceApiError(
        messages[response.status] ?? detail ?? `${response.status} ${response.statusText}`,
        response.status,
        code,
        currentRevision,
      )
      if (response.status === 409 || response.status === 412 || response.status === 428) {
        toast(error.message, 'error')
      }
      if (response.status === 412 && typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent(WORKSPACE_CONFLICT_EVENT, { detail: { resource } }))
      }
      throw error
    }
    const snapshot = await response.json() as WorkspaceSnapshot<R>
    const syncedAt = new Date().toISOString()
    connectivityStore.markOnline(syncedAt)
    updateStatus({ offlineReadonly: false, lastSuccessfulSync: syncedAt })
    return rememberSnapshot(resource, snapshot, response)
  },

  async revisions(): Promise<WorkspaceRevisions> {
    const { data } = await workspaceRequest<WorkspaceRevisions>('/api/workspace/revisions')
    etagStore.setMany(data.resources)
    return data
  },
}

export async function ensureWorkspaceRevision(resource: WorkspaceResourceName): Promise<string> {
  const known = etagStore.get(resource)
  if (known) return known
  return (await workspaceApi.get(resource)).revision
}

export async function workspaceCommand<R extends WorkspaceResourceName>(
  resource: R,
  operation: string,
  payload: Record<string, unknown>,
): Promise<WorkspaceSnapshot<R>> {
  if (isOffline()) {
    workspaceStatusStore.markOffline()
    throw new WorkspaceOfflineError()
  }
  const revision = await ensureWorkspaceRevision(resource)
  return workspaceApi.command(resource, operation, payload, revision)
}

export function workspaceDataAsOf(): string | null {
  return workspaceStatusStore.getSnapshot().dataAsOf
}
