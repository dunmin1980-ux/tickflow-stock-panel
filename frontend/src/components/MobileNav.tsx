import { BarChart3, Database, LayoutDashboard, RadioTower, Settings, TrendingUp, WalletCards } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { cn } from '@/lib/cn'

const destinations = [
  { to: '/paper-trading', label: '今日', icon: LayoutDashboard },
  { to: '/paper-account', label: '模拟', icon: WalletCards },
  { to: '/stock-analysis', label: '个股', icon: TrendingUp },
  { to: '/indices', label: '市场', icon: BarChart3 },
  { to: '/monitor', label: '监控', icon: RadioTower },
  { to: '/data', label: '数据', icon: Database },
  { to: '/settings', label: '设置', icon: Settings },
] as const

export function MobileNav() {
  return (
    <nav
      aria-label="手机主导航"
      className="fixed inset-x-0 bottom-0 z-50 border-t border-border bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md md:hidden"
    >
      <div className="grid h-16 grid-cols-7 pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]">
        {destinations.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) => cn(
              'flex min-h-11 min-w-0 flex-col items-center justify-center gap-1 px-0.5 text-[9px] font-medium transition-colors',
              isActive ? 'text-accent' : 'text-secondary hover:text-foreground',
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            <span className="truncate">{label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  )
}
