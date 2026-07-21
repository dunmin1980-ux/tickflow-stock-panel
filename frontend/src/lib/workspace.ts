import { toast } from '@/components/Toast'
import { connectivityStore, offlineSessionAccess } from './connectivity'
import { etagStore, parseRevision, quoteRevision, type WorkspaceResourceName } from './etagStore'
import { clearSnapshot, getSnapshot, putSnapshot, type OfflineSnapshot } from './offlineDb'

export type { WorkspaceResourceName } from './etagStore'
export const WORKSPACE_CONFLICT_EVENT = 'tickflow:workspace-revision-conflict'

export interface WatchlistWorkspaceData {
  symbols: Array<{ symbol: string; added_at?: string; note?: string; name?: string | null }>
}

export interface SharedClientPreferences {
  indices_nav_pinned?: boolean
  watchlist_columns?: unknown[]
  screener_result_columns?: unknown[]
  sidebar_index_symbols?: string[]
  nav_order?: string[]
  nav_hidden?: string[]
  screener_auto_run?: boolean
  daily_data_provider?: string
  adj_factor_provider?: string
  minute_data_provider?: string
  realtime_data_provider?: string
  financial_data_provider?: string
  has_feishu_webhook?: boolean
  has_feishu_credential_data?: boolean
  has_wecom_webhook?: boolean
  has_wecom_bot?: boolean
  has_wecom_bot_credential_data?: boolean
}

export interface ClientPreferencesWorkspaceData {
  preferences: SharedClientPreferences
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
  markOnline(at = new Date().toISOString()) {
    updateStatus({ offlineReadonly: false, lastSuccessfulSync: at })
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
    workspaceStatusStore.getSnapshot().offlineReadonly ||
    connectivityStore.getSnapshot().mode === 'offline-readonly' ||
    (typeof navigator !== 'undefined' && navigator.onLine === false)
  )
}

export function assertWorkspaceWritable(resource?: WorkspaceResourceName): void {
  if (isOffline()) {
    workspaceStatusStore.markOffline()
    throw new WorkspaceOfflineError()
  }
  if (resource && etagStore.isConflicted(resource)) {
    throw new WorkspaceApiError(
      '云端内容已变化，请先刷新后再次确认',
      412,
      'WORKSPACE_CONFLICT_PENDING',
    )
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringArray(value: unknown): string[] | undefined {
  return Array.isArray(value) && value.every(item => typeof item === 'string')
    ? [...value]
    : undefined
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function projectColumnValue(value: unknown): unknown | undefined {
  if (!isRecord(value) || typeof value.id !== 'string' || typeof value.label !== 'string') return undefined
  const source = isRecord(value.source) ? value.source : null
  if (!source || !['builtin', 'computed', 'ext'].includes(String(source.type))) return undefined
  const projectedSource: Record<string, string> = { type: String(source.type) }
  for (const field of ['key', 'configId', 'fieldName', 'fieldLabel']) {
    if (typeof source[field] === 'string') projectedSource[field] = source[field]
  }
  const projected: Record<string, unknown> = {
    id: value.id,
    label: value.label,
    source: projectedSource,
    visible: value.visible === true,
  }
  for (const field of ['pinned', 'standalone']) {
    if (typeof value[field] === 'boolean') projected[field] = value[field]
  }
  if (['left', 'center', 'right'].includes(String(value.align))) projected.align = value.align
  return projected
}

export function projectSharedPreferences(value: unknown): SharedClientPreferences {
  const source = isRecord(value) ? value : {}
  const projected: SharedClientPreferences = {}
  const booleanKeys: Array<keyof SharedClientPreferences> = [
    'indices_nav_pinned', 'screener_auto_run', 'has_feishu_webhook',
    'has_feishu_credential_data', 'has_wecom_webhook', 'has_wecom_bot',
    'has_wecom_bot_credential_data',
  ]
  for (const key of booleanKeys) {
    if (typeof source[key] === 'boolean') Object.assign(projected, { [key]: source[key] })
  }
  for (const key of ['sidebar_index_symbols', 'nav_order', 'nav_hidden'] as const) {
    const values = stringArray(source[key])
    if (values) projected[key] = values
  }
  for (const key of [
    'daily_data_provider', 'adj_factor_provider', 'minute_data_provider',
    'realtime_data_provider', 'financial_data_provider',
  ] as const) {
    if (typeof source[key] === 'string') projected[key] = source[key]
  }
  for (const key of ['watchlist_columns', 'screener_result_columns'] as const) {
    if (!Array.isArray(source[key])) continue
    projected[key] = source[key].map(projectColumnValue).filter(item => item !== undefined)
  }
  return projected
}

function projectWatchlistData(value: unknown): WatchlistWorkspaceData {
  const rows = isRecord(value) && Array.isArray(value.symbols) ? value.symbols : []
  return {
    symbols: rows.flatMap(row => {
      if (!isRecord(row) || typeof row.symbol !== 'string') return []
      return [{
        symbol: row.symbol,
        ...(typeof row.added_at === 'string' ? { added_at: row.added_at } : {}),
        ...(typeof row.note === 'string' ? { note: row.note } : {}),
        ...(typeof row.name === 'string' || row.name === null ? { name: row.name } : {}),
      }]
    }),
  }
}

function projectReportsData(value: unknown): ReportsWorkspaceData {
  const rows = isRecord(value) && Array.isArray(value.reports) ? value.reports : []
  const textFields = ['symbol', 'title', 'created_at', 'data_as_of', 'verification_status'] as const
  return {
    reports: rows.flatMap(row => {
      if (!isRecord(row) || typeof row.id !== 'string') return []
      const report: ReportMetadata = { id: row.id }
      for (const field of textFields) {
        if (typeof row[field] === 'string') report[field] = row[field] as never
      }
      if (typeof row.can_publish === 'boolean') report.can_publish = row.can_publish
      if (typeof row.trading_advice === 'boolean') report.trading_advice = row.trading_advice
      return [report]
    }),
  }
}

function projectBacktestsData(value: unknown): BacktestSummariesWorkspaceData {
  const rows = isRecord(value) && Array.isArray(value.summaries) ? value.summaries : []
  const required = [
    'id', 'task', 'strategy_id', 'parameters_digest', 'started_at', 'finished_at',
    'data_as_of', 'engine', 'execution_target',
  ] as const
  return {
    summaries: rows.flatMap(row => {
      if (!isRecord(row) || required.some(field => typeof row[field] !== 'string')) return []
      const rawStats = isRecord(row.stats) ? row.stats : {}
      const stats = Object.fromEntries(
        Object.entries(rawStats).flatMap(([key, item]) => {
          const number = finiteNumber(item)
          return number === undefined ? [] : [[key, number]]
        }),
      )
      return [{
        id: row.id as string,
        task: row.task as string,
        strategy_id: row.strategy_id as string,
        parameters_digest: row.parameters_digest as string,
        stats,
        started_at: row.started_at as string,
        finished_at: row.finished_at as string,
        data_as_of: row.data_as_of as string,
        engine: row.engine as string,
        execution_target: row.execution_target as string,
      }]
    }),
  }
}

function projectResourceData<R extends WorkspaceResourceName>(
  resource: R,
  value: unknown,
): WorkspaceResourceDataMap[R] {
  if (resource === 'watchlist') return projectWatchlistData(value) as WorkspaceResourceDataMap[R]
  if (resource === 'preferences') {
    const preferences = isRecord(value) ? value.preferences : undefined
    return { preferences: projectSharedPreferences(preferences) } as WorkspaceResourceDataMap[R]
  }
  if (resource === 'backtest_summaries') return projectBacktestsData(value) as WorkspaceResourceDataMap[R]
  return projectReportsData(value) as WorkspaceResourceDataMap[R]
}

function projectSnapshot<R extends WorkspaceResourceName>(
  resource: R,
  snapshot: WorkspaceSnapshot<R>,
): WorkspaceSnapshot<R> {
  return {
    resource,
    revision: snapshot.revision,
    updated_at: snapshot.updated_at,
    data: projectResourceData(resource, snapshot.data),
    ...(snapshot.offline_readonly === true ? { offline_readonly: true } : {}),
  }
}

function projectBootstrap(bootstrap: WorkspaceBootstrap): WorkspaceBootstrap {
  const resources = {} as WorkspaceResources
  for (const resource of Object.keys(bootstrap.resources) as WorkspaceResourceName[]) {
    resources[resource] = projectSnapshot(resource, bootstrap.resources[resource]) as never
  }
  const capabilities: WorkspaceBootstrap['capabilities']['capabilities'] = {}
  for (const [key, raw] of Object.entries(bootstrap.capabilities?.capabilities ?? {})) {
    if (!isRecord(raw)) continue
    capabilities[key] = {
      ...(finiteNumber(raw.rpm) !== undefined ? { rpm: finiteNumber(raw.rpm) } : {}),
      ...(finiteNumber(raw.batch) !== undefined ? { batch: finiteNumber(raw.batch) } : {}),
      ...(finiteNumber(raw.subscribe) !== undefined ? { subscribe: finiteNumber(raw.subscribe) } : {}),
    }
  }
  return {
    schema_version: bootstrap.schema_version,
    server_time: bootstrap.server_time,
    data_as_of: bootstrap.data_as_of,
    mode: bootstrap.mode,
    capabilities: { label: bootstrap.capabilities?.label ?? '', capabilities },
    resources,
    ...(bootstrap.offline_readonly === true ? { offline_readonly: true } : {}),
  }
}

async function cacheWorkspaceSnapshot<T>(
  path: string,
  data: T,
  revision: string | null,
  generation: number,
): Promise<void> {
  try {
    if (offlineSessionAccess.generation() !== generation) return
    await putSnapshot(path, data, revision)
    if (offlineSessionAccess.generation() !== generation) {
      await clearSnapshot(path)
      return
    }
    offlineSessionAccess.grant()
  } catch {
    // IndexedDB is best-effort; the online response remains authoritative.
  }
}

async function cacheWorkspaceBootstrap(
  path: string,
  bootstrap: WorkspaceBootstrap,
  generation: number,
): Promise<void> {
  await cacheWorkspaceSnapshot(path, bootstrap, null, generation)
  for (const resource of Object.keys(bootstrap.resources) as WorkspaceResourceName[]) {
    const snapshot = bootstrap.resources[resource]
    await cacheWorkspaceSnapshot(
      `/api/workspace/resources/${resource}`,
      snapshot,
      snapshot.revision,
      generation,
    )
  }
}

async function cachedWorkspaceValue<T>(path: string): Promise<OfflineSnapshot<T> | null> {
  if (!offlineSessionAccess.isGranted()) return null
  const cached = await getSnapshot<T>(path)
  if (!cached) return null
  connectivityStore.markOffline(cached.fetchedAt)
  workspaceStatusStore.markOffline()
  return cached
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

function rememberSnapshot<R extends WorkspaceResourceName>(
  resource: R,
  snapshot: WorkspaceSnapshot<R>,
  response?: Response,
  clearConflict = true,
) {
  etagStore.set(resource, response?.headers.get('ETag') ?? snapshot.revision)
  if (clearConflict && snapshot.offline_readonly !== true) etagStore.clearConflict(resource)
  updateStatus({ offlineReadonly: snapshot.offline_readonly === true })
  return snapshot
}

function rememberBootstrap(
  bootstrap: WorkspaceBootstrap,
  clearConflicts = true,
  lastSuccessfulSync = new Date().toISOString(),
): WorkspaceBootstrap {
  for (const resource of Object.keys(bootstrap.resources) as WorkspaceResourceName[]) {
    etagStore.set(resource, bootstrap.resources[resource].revision)
    if (clearConflicts && bootstrap.resources[resource].offline_readonly !== true) {
      etagStore.clearConflict(resource)
    }
  }
  updateStatus({
    mode: bootstrap.mode,
    offlineReadonly: bootstrap.offline_readonly === true ||
      Object.values(bootstrap.resources).some(resource => resource.offline_readonly === true),
    lastSuccessfulSync,
    dataAsOf: bootstrap.data_as_of,
  })
  return bootstrap
}

export const workspaceApi = {
  async bootstrap(): Promise<WorkspaceBootstrap> {
    const path = '/api/workspace/bootstrap'
    const offlineGeneration = offlineSessionAccess.generation()
    try {
      const { data } = await workspaceRequest<WorkspaceBootstrap>(path)
      const projected = projectBootstrap(data)
      await cacheWorkspaceBootstrap(path, projected, offlineGeneration)
      return rememberBootstrap(projected)
    } catch (error) {
      if (!(error instanceof TypeError)) throw error
      const cached = await cachedWorkspaceValue<WorkspaceBootstrap>(path)
      if (!cached) throw error
      const offline: WorkspaceBootstrap = {
        ...cached.data,
        offline_readonly: true,
        resources: Object.fromEntries(
          Object.entries(cached.data.resources).map(([resource, snapshot]) => [
            resource,
            { ...snapshot, offline_readonly: true },
          ]),
        ) as WorkspaceResources,
      }
      return rememberBootstrap(offline, false, cached.fetchedAt)
    }
  },

  async get<R extends WorkspaceResourceName>(resource: R): Promise<WorkspaceSnapshot<R>> {
    const path = `/api/workspace/resources/${resource}`
    const offlineGeneration = offlineSessionAccess.generation()
    try {
      const { data, response } = await workspaceRequest<WorkspaceSnapshot<R>>(path)
      const projected = projectSnapshot(resource, data)
      await cacheWorkspaceSnapshot(
        path,
        projected,
        response.headers.get('ETag') ?? projected.revision,
        offlineGeneration,
      )
      return rememberSnapshot(resource, projected, response)
    } catch (error) {
      if (!(error instanceof TypeError)) throw error
      const cached = await cachedWorkspaceValue<WorkspaceSnapshot<R>>(path)
      if (!cached) throw error
      return rememberSnapshot(resource, { ...cached.data, offline_readonly: true }, undefined, false)
    }
  },

  async command<R extends WorkspaceResourceName>(
    resource: R,
    operation: string,
    payload: Record<string, unknown>,
    revision: string,
  ): Promise<WorkspaceSnapshot<R>> {
    assertWorkspaceWritable(resource)
    const offlineGeneration = offlineSessionAccess.generation()
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
      const currentRevision = parseRevision(response.headers.get('ETag'))
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
        etagStore.markConflicted(resource)
        window.dispatchEvent(new CustomEvent(WORKSPACE_CONFLICT_EVENT, { detail: { resource } }))
      } else if (response.status === 412) {
        etagStore.markConflicted(resource)
      }
      throw error
    }
    const rawSnapshot = await response.json() as WorkspaceSnapshot<R>
    const snapshot = projectSnapshot(resource, rawSnapshot)
    const syncedAt = new Date().toISOString()
    connectivityStore.markOnline(syncedAt)
    updateStatus({ offlineReadonly: false, lastSuccessfulSync: syncedAt })
    await cacheWorkspaceSnapshot(
      `/api/workspace/resources/${resource}`,
      snapshot,
      response.headers.get('ETag') ?? snapshot.revision,
      offlineGeneration,
    )
    return rememberSnapshot(resource, snapshot, response, false)
  },

  async revisions(): Promise<WorkspaceRevisions> {
    const { data } = await workspaceRequest<WorkspaceRevisions>('/api/workspace/revisions')
    return data
  },
}

export async function ensureWorkspaceRevision(resource: WorkspaceResourceName): Promise<string> {
  assertWorkspaceWritable(resource)
  const known = etagStore.get(resource)
  if (known) return known
  return (await workspaceApi.get(resource)).revision
}

export async function workspaceCommand<R extends WorkspaceResourceName>(
  resource: R,
  operation: string,
  payload: Record<string, unknown>,
): Promise<WorkspaceSnapshot<R>> {
  assertWorkspaceWritable(resource)
  const revision = await ensureWorkspaceRevision(resource)
  return workspaceApi.command(resource, operation, payload, revision)
}

export function workspaceDataAsOf(): string | null {
  return workspaceStatusStore.getSnapshot().dataAsOf
}
