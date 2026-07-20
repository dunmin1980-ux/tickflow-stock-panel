export type ConnectivityMode = 'online' | 'offline-readonly'

export interface ConnectivityState {
  mode: ConnectivityMode
  lastSuccessfulSync: string | null
  cachedAt: string | null
}

let state: ConnectivityState = {
  mode:
    typeof navigator !== 'undefined' && navigator.onLine === false
      ? 'offline-readonly'
      : 'online',
  lastSuccessfulSync: null,
  cachedAt: null,
}

const listeners = new Set<() => void>()
const OFFLINE_ACCESS_KEY = 'tickflow-offline-access:v1'

function update(next: Partial<ConnectivityState>): void {
  state = { ...state, ...next }
  listeners.forEach((listener) => listener())
}

export const connectivityStore = {
  subscribe(listener: () => void) {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  },
  getSnapshot: () => state,
  markOnline(at = new Date().toISOString()) {
    update({ mode: 'online', lastSuccessfulSync: at, cachedAt: null })
  },
  markOffline(cachedAt: string | null = null) {
    update({ mode: 'offline-readonly', cachedAt })
  },
}

function browserSessionStorage(): Storage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    return null
  }
}

export const offlineSessionAccess = {
  grant() {
    browserSessionStorage()?.setItem(OFFLINE_ACCESS_KEY, 'granted')
  },
  revoke() {
    browserSessionStorage()?.removeItem(OFFLINE_ACCESS_KEY)
  },
  isGranted() {
    return browserSessionStorage()?.getItem(OFFLINE_ACCESS_KEY) === 'granted'
  },
}
