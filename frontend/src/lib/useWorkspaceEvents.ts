import { useEffect, useSyncExternalStore } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'

import { QK } from './queryKeys'
import { connectivityStore } from './connectivity'
import { etagStore, type WorkspaceResourceName } from './etagStore'
import {
  WORKSPACE_CONFLICT_EVENT,
  workspaceApi,
  workspaceStatusStore,
  type WorkspaceBootstrap,
} from './workspace'

const RECONNECT_DELAYS = [1_000, 2_000, 5_000, 10_000, 30_000] as const
const POLL_INTERVAL = 30_000
export const WORKSPACE_RESOURCE_EVENT = 'tickflow:workspace-resource-changed'

const RESOURCE_QUERY_KEYS: Record<WorkspaceResourceName, readonly (readonly unknown[])[]> = {
  watchlist: [QK.watchlist, ['watchlist-enriched']],
  preferences: [QK.preferences],
  stock_reports: [['workspace', 'stock_reports']],
  market_recaps: [QK.reviewReports],
  backtest_summaries: [['workspace', 'backtest_summaries']],
}

function invalidateResource(queryClient: QueryClient, resource: WorkspaceResourceName): void {
  for (const queryKey of RESOURCE_QUERY_KEYS[resource]) {
    void queryClient.invalidateQueries({ queryKey })
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(WORKSPACE_RESOURCE_EVENT, { detail: { resource } }))
  }
}

function hydrateBootstrap(queryClient: QueryClient, bootstrap: WorkspaceBootstrap): void {
  queryClient.setQueryData(QK.watchlist, bootstrap.resources.watchlist.data)
  queryClient.setQueryData(QK.reviewReports, bootstrap.resources.market_recaps.data)
  queryClient.setQueryData(['workspace', 'stock_reports'], bootstrap.resources.stock_reports.data)
  queryClient.setQueryData(['workspace', 'backtest_summaries'], bootstrap.resources.backtest_summaries.data)
  // The workspace preference DTO is intentionally partial; keep the full safe runtime query intact.
  void queryClient.invalidateQueries({ queryKey: QK.preferences })
}

async function bootstrap(queryClient: QueryClient): Promise<void> {
  const payload = await workspaceApi.bootstrap()
  hydrateBootstrap(queryClient, payload)
}

async function reconcileRevisions(queryClient: QueryClient): Promise<void> {
  const before = new Map<WorkspaceResourceName, string | undefined>()
  for (const resource of Object.keys(RESOURCE_QUERY_KEYS) as WorkspaceResourceName[]) {
    before.set(resource, etagStore.get(resource))
  }
  const payload = await workspaceApi.revisions()
  for (const [resource, revision] of Object.entries(payload.resources)) {
    const name = resource as WorkspaceResourceName
    if (revision && before.get(name) !== revision) invalidateResource(queryClient, name)
  }
}

export function useWorkspaceEvents(): void {
  const queryClient = useQueryClient()

  useEffect(() => {
    let disposed = false
    let source: EventSource | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let pollTimer: ReturnType<typeof setInterval> | null = null
    let failureCount = 0
    let opened = false

    const stopPolling = () => {
      if (pollTimer) clearInterval(pollTimer)
      pollTimer = null
    }

    const startPolling = () => {
      if (pollTimer || disposed || opened) return
      pollTimer = setInterval(() => {
        void reconcileRevisions(queryClient).catch(() => workspaceStatusStore.markOffline())
      }, POLL_INTERVAL)
    }

    const refreshBootstrap = () => {
      void bootstrap(queryClient).catch(() => {
        workspaceStatusStore.markOffline()
        startPolling()
      })
    }

    const disconnectAndRetry = (failedSource: EventSource) => {
      if (disposed || source !== failedSource) return
      failedSource.close()
      source = null
      opened = false
      connectivityStore.markOffline()
      workspaceStatusStore.markOffline()
      startPolling()
      const delay = RECONNECT_DELAYS[Math.min(failureCount, RECONNECT_DELAYS.length - 1)]
      failureCount += 1
      if (reconnectTimer) clearTimeout(reconnectTimer)
      reconnectTimer = setTimeout(connect, delay)
    }

    function connect() {
      if (disposed) return
      reconnectTimer = null
      source?.close()
      opened = false
      startPolling()
      const currentSource = new EventSource('/api/workspace/events')
      source = currentSource

      currentSource.onopen = () => {
        if (disposed) return
        void reconcileRevisions(queryClient).then(() => {
          if (disposed || source !== currentSource) return
          opened = true
          failureCount = 0
          const syncedAt = new Date().toISOString()
          connectivityStore.markOnline(syncedAt)
          workspaceStatusStore.markOnline(syncedAt)
          stopPolling()
        }).catch(() => disconnectAndRetry(currentSource))
      }

      currentSource.addEventListener('resource_changed', event => {
        try {
          const payload = JSON.parse((event as MessageEvent).data) as {
            resource?: WorkspaceResourceName
            revision?: string
          }
          if (!payload.resource || !RESOURCE_QUERY_KEYS[payload.resource]) return
          invalidateResource(queryClient, payload.resource)
        } catch {
          void reconcileRevisions(queryClient)
        }
      })

      currentSource.addEventListener('resync_required', () => {
        void reconcileRevisions(queryClient).catch(() => disconnectAndRetry(currentSource))
      })

      currentSource.onerror = () => disconnectAndRetry(currentSource)
    }

    const onOnline = () => {
      refreshBootstrap()
      if (!source && !reconnectTimer) connect()
    }
    const onVisibility = () => {
      if (document.visibilityState === 'visible') refreshBootstrap()
    }
    const onConflict = (event: Event) => {
      const resource = (event as CustomEvent<{ resource?: WorkspaceResourceName }>).detail?.resource
      if (resource && RESOURCE_QUERY_KEYS[resource]) invalidateResource(queryClient, resource)
    }

    refreshBootstrap()
    connect()
    window.addEventListener('online', onOnline)
    window.addEventListener(WORKSPACE_CONFLICT_EVENT, onConflict)
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      disposed = true
      source?.close()
      if (reconnectTimer) clearTimeout(reconnectTimer)
      stopPolling()
      window.removeEventListener('online', onOnline)
      window.removeEventListener(WORKSPACE_CONFLICT_EVENT, onConflict)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [queryClient])
}

export function WorkspaceEvents(): null {
  useWorkspaceEvents()
  return null
}

export function useWorkspaceStatus() {
  return useSyncExternalStore(
    workspaceStatusStore.subscribe,
    workspaceStatusStore.getSnapshot,
    workspaceStatusStore.getSnapshot,
  )
}
