import { BookOpenCheck, Layers3, Settings, Star, TrendingUp } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { cn } from '@/lib/cn'

const destinations = [
  { to: '/watchlist', label: '自选', icon: Star },
  { to: '/stock-analysis', label: '个股', icon: TrendingUp },
  { to: '/review', label: '复盘', icon: BookOpenCheck },
  { to: '/concept-analysis', label: '概念', icon: Layers3 },
  { to: '/settings', label: '设置', icon: Settings },
] as const

export function MobileNav() {
  return (
    <nav
      aria-label="手机主导航"
      className="fixed inset-x-0 bottom-0 z-50 border-t border-border bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md md:hidden"
    >
      <div className="grid h-16 grid-cols-5 pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]">
        {destinations.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) => cn(
              'flex min-h-11 min-w-0 flex-col items-center justify-center gap-1 px-1 text-[10px] font-medium transition-colors',
              isActive ? 'text-accent' : 'text-secondary hover:text-foreground',
            )}
          >
            <Icon className="h-5 w-5 shrink-0" />
            <span className="truncate">{label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  )
}
