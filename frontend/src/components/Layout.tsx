import { useEffect, useRef, useState, Suspense } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { ToastContainer } from '@/components/Toast'
import { AlertToastContainer } from '@/components/AlertToast'
import { useVersion } from '@/lib/useSharedQueries'
import { QK } from '@/lib/queryKeys'
import {
  Settings,
  Database,
  Loader2,
  TrendingUp,
  BarChart3,
  RadioTower,
  CheckCircle2,
  Sun,
  Moon,
  WifiOff,
  Laptop,
  WalletCards,
  LayoutDashboard,
} from 'lucide-react'
import { Logo } from './Logo'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { toggleTheme, useTheme } from '@/lib/theme'
import { setCurrentTotal as setAlertTotal, useUnreadAlerts } from '@/lib/monitorBadge'
import { ConnectionBanner } from './ConnectionBanner'
import { MobileNav } from './MobileNav'
import { WorkbenchDates } from './WorkbenchDates'
import { WorkspaceEvents, useWorkspaceStatus } from '@/lib/useWorkspaceEvents'
import type { WorkspaceStatus } from '@/lib/workspace'

// 品牌色 — 只用于 logo / brand 区域,不影响功能语义色
const BRAND = '#8B5CF6'

type NavItem = { to: string; label: string; icon: React.ComponentType<{ className?: string }> }

const nav: NavItem[] = [
  { to: '/paper-trading', label: '今日工作台', icon: LayoutDashboard },
  { to: '/paper-account', label: '模拟盘', icon: WalletCards },
  { to: '/stock-research', label: '个股研究', icon: TrendingUp },
  { to: '/indices', label: '市场', icon: BarChart3 },
  { to: '/monitor', label: '监控', icon: RadioTower },
  { to: '/data', label: '数据', icon: Database },
] as const

/** 亮/暗主题切换 — 状态存 localStorage, 生效见 lib/theme.ts */
function ThemeToggle() {
  const theme = useTheme()
  const dark = theme === 'dark'
  return (
    <button
      onClick={() => toggleTheme()}
      className="flex items-center justify-center rounded-btn p-2 text-foreground/80 transition-colors duration-150 ease-smooth hover:bg-elevated hover:text-foreground cursor-pointer"
      title={dark ? '切换到亮色模式' : '切换到暗色模式'}
    >
      {dark ? <Sun className="h-4 w-4 shrink-0" /> : <Moon className="h-4 w-4 shrink-0" />}
    </button>
  )
}

function DesktopWorkbenchStatusBar({ workspace }: { workspace: WorkspaceStatus }) {
  const isOffline = workspace.offlineReadonly
  const ModeIcon = isOffline ? WifiOff : Laptop
  const modeLabel = isOffline ? '离线只读' : '本地模式'
  const syncLabel = workspace.lastSuccessfulSync
    ? new Date(workspace.lastSuccessfulSync).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : '尚未同步'

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={`${modeLabel}，最后同步 ${syncLabel}`}
      className="sticky top-0 z-30 flex min-h-9 flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-border bg-base/90 px-3 py-1 backdrop-blur-md sm:px-5"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-[11px]">
        <span className={cn(
          'inline-flex items-center gap-1.5 font-semibold',
          isOffline ? 'text-warning' : 'text-secondary',
        )}>
          <ModeIcon className="h-3.5 w-3.5 shrink-0" />
          {modeLabel}
        </span>
        <span className={cn('inline-flex items-center gap-1 font-medium', isOffline ? 'text-warning' : 'text-bull')}>
          <span className={cn('h-1.5 w-1.5 rounded-full', isOffline ? 'bg-warning' : 'bg-bull')} />
          Backend {isOffline ? 'Offline' : 'Online'}
        </span>
        <span className="text-muted" aria-label={`最后同步时间 ${syncLabel}`}>同步 {syncLabel}</span>
      </div>
      <span className="font-mono text-[10px] font-semibold text-danger">SIMULATION ONLY</span>
      <WorkbenchDates enrichedCacheDate={workspace.dataAsOf} />
    </div>
  )
}

/** 监控中心未读徽标 — 仅在非监控页且有未读时显示。 */
function MonitorBadge({ active }: { active: boolean }) {
  const unread = useUnreadAlerts()
  // 尊重用户设置: 可在菜单设置里关闭数字提示
  const badgeEnabled = (() => {
    try { return localStorage.getItem('monitor_badge_enabled') !== '0' } catch { return true }
  })()
  if (active || unread <= 0 || !badgeEnabled) return null
  return (
    <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[9px] font-bold text-white animate-pulse">
      {unread > 99 ? '99+' : unread}
    </span>
  )
}

export function Layout() {
  const { data: versionData } = useVersion()
  const workspaceStatus = useWorkspaceStatus()

  // 数据同步状态轮询: 有活跃 job 时「数据」菜单项显示转圈
  const { data: pipelineJobs } = useQuery({
    queryKey: QK.pipelineJobs,
    queryFn: () => api.pipelineJobs(1),
    refetchInterval: (query) => (query.state.data?.active_id ? 2000 : 15000),
    refetchIntervalInBackground: true,
  })
  const isDataSyncing = !!pipelineJobs?.active_id

  // 数据同步完成的"瞬时反馈": isDataSyncing 从 true→false 时显示绿色对勾,
  // 闪烁约 3 秒后自动消失。
  const [dataSyncJustDone, setDataSyncJustDone] = useState(false)
  const prevSyncingRef = useRef(false)
  useEffect(() => {
    // 仅在"刚结束"(true→false)且非首次挂载时触发
    if (prevSyncingRef.current && !isDataSyncing) {
      setDataSyncJustDone(true)
      const t = setTimeout(() => setDataSyncJustDone(false), 3000)
      prevSyncingRef.current = isDataSyncing
      return () => clearTimeout(t)
    }
    prevSyncingRef.current = isDataSyncing
  }, [isDataSyncing])

  const version = versionData?.version

  // 轮询触发记录总数 → 更新监控中心徽标 (每 15 秒)
  const alertsTotalQuery = useQuery({
    queryKey: ['alerts-total'],
    queryFn: () => api.alertsList({ days: 7, limit: 1 }),
    refetchInterval: 15000,
    refetchIntervalInBackground: true,
    select: (data) => data.total,
  })
  // 只在拿到真实总数时同步徽标 (避免 data=undefined 时传 0 重置 lastSeen)
  const alertsTotal = alertsTotalQuery.data
  useEffect(() => {
    if (alertsTotal != null) setAlertTotal(alertsTotal)
  }, [alertsTotal])

  return (
    <div className="grid h-[100dvh] grid-cols-1 overflow-hidden bg-base text-foreground md:grid-cols-[14rem_minmax(0,1fr)]">
      <WorkspaceEvents />
      <aside className="hidden h-full min-h-0 flex-col overflow-hidden border-r border-border bg-surface md:flex">
        <div className="px-5 py-5 border-b border-border shrink-0">
          {/* Brand block — 原创 logo + 等宽 wordmark */}
          <div className="flex items-center gap-2.5">
            <Logo
              size={28}
              className="shrink-0 drop-shadow-[0_0_8px_rgba(139,92,246,0.5)]"
              style={{ color: BRAND }}
            />
            <div
              className="font-mono font-bold text-[13px] tracking-[0.06em] text-foreground leading-tight"
              style={{ textShadow: `0 0 10px ${BRAND}44` }}
            >
              <div>TickFlow</div>
              <div>Stock Panel</div>
            </div>
          </div>

          <div className="mt-2.5 text-[10px] uppercase tracking-[0.22em] text-secondary">
            Quant · Terminal
          </div>

          <div className="mt-3 h-px bg-border" />
        </div>

        <nav aria-label="主导航" className="flex-1 min-h-0 overflow-y-auto px-2 py-3 space-y-0.5">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 px-3 py-2 rounded-btn text-sm transition-colors duration-150 ease-smooth',
                  isActive
                    ? 'bg-elevated text-foreground font-medium'
                    : 'text-foreground/80 hover:bg-elevated hover:text-foreground',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className="h-4 w-4 shrink-0" />
                  <span className="flex-1">{label}</span>
                  {/* 数据同步状态: 同步中转圈, 刚完成显示绿色对勾闪烁 3 秒 */}
                  {to === '/data' && isDataSyncing && (
                    <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" />
                  )}
                  {to === '/data' && !isDataSyncing && dataSyncJustDone && (
                    <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-bull animate-pulse" />
                  )}
                  {/* 监控中心徽标: 仅非监控页且有未读时显示 */}
                  {to === '/monitor' && <MonitorBadge active={isActive} />}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-border px-2 py-3 shrink-0">
          <div className="flex items-center gap-1">
            <ThemeToggle />
            <NavLink
              to="/settings"
              className={({ isActive }) =>
                cn(
                  'flex flex-1 items-center justify-between gap-3 px-3 py-2 rounded-btn text-sm transition-colors duration-150 ease-smooth',
                  isActive
                    ? 'bg-elevated text-foreground font-medium'
                    : 'text-foreground/80 hover:bg-elevated hover:text-foreground',
                )
              }
            >
              <span className="flex items-center gap-3">
                <Settings className="h-4 w-4 shrink-0" />
                <span>设置</span>
              </span>
              <span className="font-mono text-[10px] text-muted/50 select-none">
                {version ?? ''}
              </span>
            </NavLink>
          </div>
        </div>
      </aside>

      <motion.main
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
        className="h-full min-w-0 overflow-auto pb-[calc(4rem+env(safe-area-inset-bottom))] scrollbar-gutter-stable md:pb-0"
      >
        <DesktopWorkbenchStatusBar workspace={workspaceStatus} />
        <ConnectionBanner />
        <Suspense
          fallback={
            <div className="flex items-center justify-center py-24">
              <Loader2 className="h-5 w-5 animate-spin text-muted" />
            </div>
          }
        >
          <Outlet />
        </Suspense>
      </motion.main>
      <MobileNav />
      <ToastContainer />
      <AlertToastContainer />
    </div>
  )
}
