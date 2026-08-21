import { useEffect, useRef, useState, Suspense } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { ToastContainer } from '@/components/Toast'
import { AlertToastContainer } from '@/components/AlertToast'
import { AiAnalysisHost } from '@/components/financials/AiAnalysisHost'
import { AiReportBubble } from '@/components/financials/AiReportBubble'
import { StockAnalysisHost } from '@/components/stock-analysis/StockAnalysisHost'
import { StockAnalysisBubble } from '@/components/stock-analysis/StockAnalysisBubble'
import {
  usePreferences,
  useVersion,
} from '@/lib/useSharedQueries'
import { QK } from '@/lib/queryKeys'
import {
  Star,
  ScanSearch,
  History,
  FileText,
  Settings,
  Key,
  Database,
  Loader2,
  Tags,
  TrendingUp,
  Flame,
  BarChart3,
  Sparkles,
  Layers3,
  Landmark,
  RadioTower,
  CheckCircle2,
  BookOpenCheck,
  Coins,
  Sun,
  Moon,
  WifiOff,
  Cloud,
  Laptop,
  Network,
  WalletCards,
} from 'lucide-react'
import { Logo } from './Logo'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { toggleTheme, useTheme } from '@/lib/theme'
import { setCurrentTotal as setAlertTotal, useUnreadAlerts } from '@/lib/monitorBadge'
import { ConnectionBanner } from './ConnectionBanner'
import { MobileNav } from './MobileNav'
import { resolveClientBadge, type ClientBadgeTone } from '@/lib/clientMode'
import type { ClientStatus } from '@/lib/api'
import { WorkspaceEvents, useWorkspaceStatus } from '@/lib/useWorkspaceEvents'
import type { WorkspaceStatus } from '@/lib/workspace'

// 品牌色 — 只用于 logo / brand 区域,不影响功能语义色
const BRAND = '#8B5CF6'

const CLIENT_BADGE_CLASS: Record<ClientBadgeTone, string> = {
  neutral: 'border-border bg-elevated/60 text-secondary',
  success: 'border-bull/30 bg-bull/10 text-bull',
  info: 'border-accent/30 bg-accent/10 text-accent',
  warning: 'border-warning/30 bg-warning/10 text-warning',
}

type NavItem = { to: string; label: string; icon: React.ComponentType<{ className?: string }> }

const nav: NavItem[] = [
  { to: '/paper-trading',   label: '模拟盘',   icon: WalletCards },
  { to: '/watchlist',  label: '自选',   icon: Star },
  { to: '/screener',   label: '策略',   icon: ScanSearch },
  { to: '/backtest',   label: '回测',   icon: History },
  { to: '/stock-analysis',    label: '个股分析', icon: TrendingUp },
  { to: '/limit-ladder', label: '连板梯队', icon: Flame },
  { to: '/concept-analysis', label: '概念分析', icon: Layers3 },
  { to: '/industry-analysis', label: '行业分析', icon: Landmark },
  { to: '/financials', label: '财务分析', icon: FileText },
  { to: '/monitor', label: '监控中心', icon: RadioTower },
  { to: '/review',      label: '复盘',   icon: BookOpenCheck },
  { to: '/indices', label: '指数', icon: BarChart3 },
  { to: '/data',       label: '数据',   icon: Database },
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

function DesktopClientStatusBar({
  status,
  workspace,
}: {
  status: ClientStatus | null | undefined
  workspace: WorkspaceStatus
}) {
  const badge = status ? resolveClientBadge(status) : null
  const isOffline = workspace.offlineReadonly
  const isLocalCompute = !isOffline && status?.configured && status.authenticated && status.mode === 'hybrid'
  const ModeIcon = isOffline ? WifiOff : isLocalCompute ? Laptop : Cloud
  const modeLabel = isOffline ? '离线只读' : isLocalCompute ? '本地计算' : '云端模式'
  const syncLabel = workspace.lastSuccessfulSync
    ? new Date(workspace.lastSuccessfulSync).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : '尚未同步'

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={`${modeLabel}，最后同步 ${syncLabel}，数据日期 ${workspace.dataAsOf ?? '未知'}`}
      className="sticky top-0 z-30 flex min-h-9 flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-border bg-base/90 px-3 py-1 backdrop-blur-md sm:px-5"
    >
      <div className="flex min-w-0 items-center gap-2 text-[11px]">
        <span className={cn(
          'inline-flex items-center gap-1.5 font-semibold',
          isOffline ? 'text-warning' : 'text-secondary',
        )}>
          <ModeIcon className="h-3.5 w-3.5 shrink-0" />
          {modeLabel}
        </span>
        <span className="text-muted" aria-label={`最后同步时间 ${syncLabel}`}>同步 {syncLabel}</span>
        <span className="font-mono text-muted" aria-label={`数据日期 ${workspace.dataAsOf ?? '未知'}`}>
          数据 {workspace.dataAsOf ?? '--'}
        </span>
      </div>
      {status && badge && <NavLink
        to="/client-connection"
        className={cn(
          'inline-flex h-7 items-center gap-1.5 rounded-btn border px-2.5 text-[11px] font-medium transition-colors hover:brightness-110',
          CLIENT_BADGE_CLASS[badge.tone],
        )}
        title="云端连接设置"
      >
        <Network className="h-3.5 w-3.5 shrink-0" />
        <span>{badge.label}</span>
        <Settings className="h-3 w-3 shrink-0 opacity-70" />
      </NavLink>}
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

// ===== 档位卡片 =====
function TierBadge({ label }: { label: string; hasKey?: boolean }) {
  const base = label.split(' ')[0].split('+')[0].toLowerCase()
  const isNone = base === 'none'

  const tierConfig: Record<string, {
    desc: string
    tagBg: React.CSSProperties
    dotStyle: React.CSSProperties
    labelTextStyle: React.CSSProperties
  }> = {
    none: {
      desc: '未配置 Key · 仅历史日K',
      tagBg: { background: 'rgba(113,113,122,0.15)' },
      dotStyle: { background: '#52525b' },
      labelTextStyle: { color: '#71717a' },
    },
    free: {
      desc: '基础日K · 自选实时',
      tagBg: { background: 'rgba(113,113,122,0.3)' },
      dotStyle: { background: '#71717a' },
      labelTextStyle: { color: '#a1a1aa' },
    },
    starter: {
      desc: '批量同步 · 行情池',
      tagBg: { background: 'rgba(59,130,246,0.2)' },
      dotStyle: { background: '#3b82f6' },
      labelTextStyle: { color: '#60a5fa' },
    },
    pro: {
      desc: '分钟K · 实时行情 · 盘口',
      tagBg: { background: 'linear-gradient(135deg, rgba(168,85,247,0.2), rgba(124,58,237,0.15))' },
      dotStyle: { background: 'linear-gradient(135deg, #a855f7, #7c3aed)' },
      labelTextStyle: { background: 'linear-gradient(135deg, #c084fc, #a855f7)', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' },
    },
    expert: {
      desc: 'WebSocket · 财务数据',
      tagBg: { background: 'linear-gradient(135deg, rgba(59,130,246,0.2), rgba(168,85,247,0.2), rgba(245,158,11,0.2))' },
      dotStyle: { background: 'linear-gradient(135deg, #3b82f6, #a855f7, #f59e0b)' },
      labelTextStyle: { background: 'linear-gradient(135deg, #60a5fa, #c084fc, #fbbf24)', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' },
    },
  }

  const t = tierConfig[base] || tierConfig.none
  // none 档显示英文「None」,无 label 时也显示「None」
  const displayLabel = isNone ? 'None' : (label || 'None')

  return (
    <NavLink
      to="/settings?tab=account"
      className="mt-2.5 group block -mx-2.5"
      title="Visual v1 历史数据配置状态"
    >
      <div className="relative overflow-hidden rounded-lg border border-blue-400/20 bg-gradient-to-br from-blue-500/[0.12] via-surface to-surface px-3 py-2 transition-all hover:border-blue-400/35 hover:from-blue-500/[0.16]">
        <div className="absolute -right-5 -top-6 h-14 w-14 rounded-full bg-blue-500/10 blur-2xl" />
        <div className="relative flex items-center gap-2">
          <div className="flex h-6 w-6 items-center justify-center rounded-md bg-blue-400/10 text-blue-300 ring-1 ring-blue-400/20">
            <Key className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-medium text-foreground">TickFlow</span>
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ ...t.dotStyle, ...(base === 'expert' ? { animation: 'pulse 2s infinite' } : {}) }}
              />
            </div>
            <div className="mt-0.5 truncate text-[10px] leading-tight text-muted">
              Visual v1 不使用此 Key
            </div>
          </div>
          <span
            className="inline-flex h-[18px] max-w-[68px] shrink-0 items-center overflow-hidden rounded px-1.5 text-[10px] font-bold font-mono leading-none"
            style={t.tagBg}
          >
            <span className="truncate" style={t.labelTextStyle}>{displayLabel}</span>
          </span>
          <Settings className="h-3 w-3 shrink-0 text-muted group-hover:text-blue-300 transition-colors" />
        </div>

      </div>
    </NavLink>
  )
}

function AIConfigBadge(_props: { configured?: boolean; model?: string }) {
  return (
    <NavLink
      to="/settings?tab=ai"
      className="mt-2 group block -mx-2.5"
      title="Visual v1 AI Provider 状态"
    >
      <div className="relative overflow-hidden rounded-lg border border-purple-400/20 bg-gradient-to-br from-purple-500/[0.12] via-surface to-surface px-3 py-2 transition-all hover:border-purple-400/35 hover:from-purple-500/[0.16]">
        <div className="absolute -right-5 -top-6 h-14 w-14 rounded-full bg-purple-500/10 blur-2xl" />
        <div className="relative flex items-center gap-2">
          <div className="flex h-6 w-6 items-center justify-center rounded-md bg-purple-400/10 text-purple-300 ring-1 ring-purple-400/20">
            <Sparkles className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-medium text-foreground">AI 配置</span>
              <span className="h-1.5 w-1.5 rounded-full bg-warning" />
            </div>
            <div className="mt-0.5 truncate text-[10px] leading-tight text-muted">
              Provider 已延后 · Visual v1 不调用
            </div>
          </div>
          <Settings className="h-3 w-3 text-muted group-hover:text-purple-300 transition-colors" />
        </div>
      </div>
    </NavLink>
  )
}

export function Layout() {
  // ===== 共享 hooks (替代内联 useQuery) =====
  const { data: versionData } = useVersion()
  const { data: prefs } = usePreferences()
  const workspaceStatus = useWorkspaceStatus()
  const { data: analysisMenus } = useQuery({
    queryKey: QK.analysisMenus,
    queryFn: api.analysisMenus,
  })
  const { data: goldStatus } = useQuery({
    queryKey: QK.goldStatus,
    queryFn: api.goldStatus,
    staleTime: 60_000,
  })
  const { data: desktopClientStatus } = useQuery({
    queryKey: ['desktop-client', 'status'],
    queryFn: api.clientStatus,
    retry: false,
    staleTime: 15_000,
    refetchInterval: query => query.state.data == null ? false : 15_000,
  })

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

  // 合并内置页面 + 可见的扩展分析菜单
  const analysisNav = (analysisMenus?.items ?? [])
    .filter(m => m.visible)
    .map<NavItem>(m => ({ to: `/analysis/${m.id}`, label: m.label, icon: m.icon === 'tags' ? Tags : BarChart3 }))
  const goldNav: NavItem[] = goldStatus?.enabled
    ? [{ to: '/gold', label: '中金黄金', icon: Coins }]
    : []

  const allNav: NavItem[] = Array.from(nav)
  for (const item of goldNav) allNav.push(item)
  for (const item of analysisNav) allNav.push(item)
  const savedOrder = prefs?.nav_order ?? []

  const navItems = savedOrder.length > 0
    ? (() => {
        const byTo = new Map(allNav.map(n => [n.to, n]))
        const ordered = savedOrder
          .map(id => byTo.get(id) ?? byTo.get(`/analysis/${id}`))
          .filter(Boolean)
        const seen = new Set(ordered.map(n => n!.to))
        return [...ordered as NavItem[], ...allNav.filter(n => !seen.has(n.to))]
      })()
    : allNav

  const hiddenIds = new Set(prefs?.nav_hidden ?? [])
  const visibleNavItems = navItems.filter(n => !hiddenIds.has(n.to) && !hiddenIds.has(n.to.replace(/^\/analysis\//, '')))

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

          <div
            className="mt-3 h-px"
            style={{ background: `linear-gradient(90deg, ${BRAND}88, transparent 80%)` }}
          />

          <TierBadge label="Deferred" />
          <AIConfigBadge />
        </div>

        <nav className="flex-1 min-h-0 overflow-y-auto px-2 py-3 space-y-0.5">
          {visibleNavItems.map(({ to, label, icon: Icon }) => (
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
                  {/* 个股分析 Beta 标识 */}
                  {to === '/stock-analysis' && (
                    <span className="inline-flex items-center rounded-full border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-amber-400 shrink-0">
                      Beta
                    </span>
                  )}
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

        <div
          className="mx-2 mb-1 flex items-center gap-2 rounded-btn px-2.5 py-2 text-left shrink-0"
          aria-label="Visual v1 Provider 已延后"
        >
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-elevated">
            <Database className="h-3 w-3 text-muted" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="truncate text-[11px] font-medium text-secondary">
                Provider
              </span>
              <span className="shrink-0 rounded bg-warning/10 px-1 py-px text-[8px] font-semibold uppercase tracking-wider text-warning">
                DEFERRED
              </span>
            </div>
            <div className="mt-0.5 truncate text-[10px] text-muted">
              Visual v1 不读取 Provider 凭据
            </div>
          </div>
        </div>

        <div className="border-t border-border px-3 py-2.5 shrink-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-secondary">实时行情</span>
            <span className="rounded bg-warning/10 px-1.5 py-0.5 text-[10px] font-medium text-warning">
              DEFERRED
            </span>
          </div>
          <div className="mt-1.5 text-[10px] leading-snug text-muted">
            Visual v1 不启动行情 Provider
          </div>
        </div>

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
        <DesktopClientStatusBar status={desktopClientStatus} workspace={workspaceStatus} />
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
      <AiAnalysisHost />
      <AiReportBubble />
      <StockAnalysisHost />
      <StockAnalysisBubble />
    </div>
  )
}
