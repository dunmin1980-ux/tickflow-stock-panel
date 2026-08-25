/**
 * 统一设置页面 — Tab 切换外壳。
 *
 * 通过 URL query param ?tab=xxx 同步 Tab 状态。
 */
import { useSearchParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Key, Sparkles } from 'lucide-react'
import {
  SettingsVisualAiPanel,
  SettingsVisualDataKeyPanel,
  VisualRuntimeStatusPanel,
} from './settings/VisualRuntime'
import { PageHeader } from '@/components/PageHeader'
import { cn } from '@/lib/cn'

import type { ComponentType } from 'react'

// ===== Tab 定义 =====

type TabDef = {
  key: string
  label: string
  icon: ComponentType<{ className?: string }>
  panel: ComponentType<{ highlight?: string }>
  badge?: string
}

const TABS: readonly TabDef[] = [
  { key: 'account',    label: '数据状态',   icon: Key,       panel: SettingsVisualDataKeyPanel, badge: 'deferred' },
  { key: 'ai',         label: 'AI 状态',    icon: Sparkles,  panel: SettingsVisualAiPanel, badge: 'deferred' },
]

type TabKey = (typeof TABS)[number]['key']

export function Settings() {
  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('tab') as TabKey | null
  const activeTab = TABS.find((t) => t.key === tabParam) ?? TABS[0]
  const highlight = searchParams.get('highlight') ?? ''

  return (
    <>
      <PageHeader
        title="设置"
        subtitle="查看 Visual v1 能力边界与本地运行设置。"
      />

      <div className="px-4 py-5 sm:px-8 sm:py-6">
        <VisualRuntimeStatusPanel />
        <div className="flex flex-col gap-4 sm:flex-row sm:items-stretch sm:gap-6">
          {/* ===== 竖向 Tab 侧栏（内容垂直居中） ===== */}
          <nav className="w-full shrink-0 sm:w-36">
            <div className="grid grid-cols-2 gap-1 sm:sticky sm:top-6 sm:flex sm:min-h-[60vh] sm:flex-col sm:justify-center">
              {TABS.map(({ key, label, icon: Icon, badge }) => (
                <button
                  key={key}
                  onClick={() => setSearchParams({ tab: key }, { replace: true })}
                  className={cn(
                    'relative flex items-center gap-2 px-3 py-2 rounded-btn text-sm transition-colors duration-150 ease-smooth text-left',
                    activeTab.key === key
                      ? 'bg-accent/10 text-accent font-medium'
                      : 'text-secondary hover:text-foreground hover:bg-elevated/60',
                  )}
                >
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                  <span>{label}</span>
                  {badge && (
                    <span className="ml-auto inline-flex items-center rounded-full border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-amber-400 shrink-0">
                      {badge}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </nav>

          {/* ===== Tab 内容 ===== */}
          <motion.div
            key={activeTab.key}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.15 }}
            className="min-w-0 flex-1"
          >
            <activeTab.panel highlight={highlight} />
          </motion.div>
        </div>
      </div>
    </>
  )
}
