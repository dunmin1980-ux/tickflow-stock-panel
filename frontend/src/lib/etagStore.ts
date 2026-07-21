export type WorkspaceResourceName =
  | 'watchlist'
  | 'preferences'
  | 'stock_reports'
  | 'market_recaps'
  | 'backtest_summaries'

const revisions = new Map<WorkspaceResourceName, string>()
const conflictedResources = new Set<WorkspaceResourceName>()

export function parseRevision(value: string | null | undefined): string | undefined {
  if (!value) return undefined
  const unquoted = value.trim().replace(/^"|"$/g, '')
  return /^[0-9a-f]{64}$/.test(unquoted) ? unquoted : undefined
}

export const etagStore = {
  get(resource: WorkspaceResourceName): string | undefined {
    return revisions.get(resource)
  },

  set(resource: WorkspaceResourceName, value: string | null | undefined): string | undefined {
    const revision = parseRevision(value)
    if (revision) revisions.set(resource, revision)
    return revision
  },

  setMany(values: Partial<Record<WorkspaceResourceName, string>>): void {
    for (const [resource, revision] of Object.entries(values)) {
      this.set(resource as WorkspaceResourceName, revision)
    }
  },

  clear(resource?: WorkspaceResourceName): void {
    if (resource) {
      revisions.delete(resource)
      conflictedResources.delete(resource)
    } else {
      revisions.clear()
      conflictedResources.clear()
    }
  },

  markConflicted(resource: WorkspaceResourceName): void {
    conflictedResources.add(resource)
  },

  isConflicted(resource: WorkspaceResourceName): boolean {
    return conflictedResources.has(resource)
  },

  clearConflict(resource: WorkspaceResourceName): void {
    conflictedResources.delete(resource)
  },
}

export function quoteRevision(revision: string): string {
  const normalized = parseRevision(revision)
  if (!normalized) throw new TypeError('Workspace revision must be a 64-character digest')
  return `"${normalized}"`
}
