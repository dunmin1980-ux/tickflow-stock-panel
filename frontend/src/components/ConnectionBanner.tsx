import { useSyncExternalStore } from 'react'
import { WifiOff } from 'lucide-react'
import { BACKEND_OFFLINE_MESSAGE, connectivityStore } from '@/lib/connectivity'

export function ConnectionBanner() {
  const connectivity = useSyncExternalStore(
    connectivityStore.subscribe,
    connectivityStore.getSnapshot,
    connectivityStore.getSnapshot,
  )

  if (connectivity.mode !== 'offline-readonly') return null

  const cachedTime = connectivity.cachedAt
    ? new Date(connectivity.cachedAt).toLocaleString()
    : '未知'

  return (
    <div
      role="status"
      aria-live="polite"
      className="sticky top-0 z-40 flex min-h-9 items-center justify-center gap-1.5 border-b border-warning/30 bg-warning/10 px-3 py-2 text-center text-xs font-medium text-warning backdrop-blur-md"
    >
      <WifiOff className="h-3.5 w-3.5 shrink-0" />
      <span>{BACKEND_OFFLINE_MESSAGE}</span>
      <span className="text-warning/70">· 缓存时间 {cachedTime}</span>
    </div>
  )
}
