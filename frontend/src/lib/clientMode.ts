import type { ClientStatus } from './api'

export type ClientBadgeTone = 'neutral' | 'success' | 'info' | 'warning'

export interface ClientBadge {
  label: string
  tone: ClientBadgeTone
}

export function resolveClientBadge(status: ClientStatus): ClientBadge {
  if (status.configured && !status.reachable) {
    return { label: '离线只读', tone: 'warning' }
  }
  if (status.configured && status.authenticated && status.reachable) {
    return status.mode === 'cloud'
      ? { label: '云端模式', tone: 'info' }
      : { label: '混合模式', tone: 'success' }
  }
  return { label: '本地模式', tone: 'neutral' }
}
