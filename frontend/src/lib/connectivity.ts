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
let offlineAccessGeneration = 0

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
    try {
      browserSessionStorage()?.setItem(OFFLINE_ACCESS_KEY, 'granted')
    } catch {
      // Storage can be disabled or full; online responses must continue to work.
    }
  },
  revoke() {
    offlineAccessGeneration += 1
    try {
      browserSessionStorage()?.removeItem(OFFLINE_ACCESS_KEY)
    } catch {
      // The in-memory generation still invalidates in-flight work in this page.
    }
  },
  isGranted() {
    try {
      return browserSessionStorage()?.getItem(OFFLINE_ACCESS_KEY) === 'granted'
    } catch {
      return false
    }
  },
  generation: () => offlineAccessGeneration,
}
