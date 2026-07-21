import { useState } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { FactorBacktest } from './backtest/FactorBacktest'
import { StrategyBacktest } from './backtest/StrategyBacktest'
import { StrategyOptimizer } from './backtest/StrategyOptimizer'
import { StrategyWalkForward } from './backtest/StrategyWalkForward'
import { BarChart3, FlaskConical, SlidersHorizontal, Waypoints } from 'lucide-react'
import { useBacktestSummaries } from '@/lib/useSharedQueries'
import type { BacktestSummary } from '@/lib/workspace'

type Tab = 'factor' | 'strategy' | 'optimizer' | 'walkforward'

const MODES: Record<Tab, { title: string; subtitle: string; hint: string }> = {
  factor: {
    title: '因子回测',
    subtitle: '验证单个因子是否有预测能力',
    hint: '看 IC / IR、分层收益和多空组合，适合先筛掉无效指标。',
  },
  strategy: {
    title: '策略回测',
    subtitle: '验证完整选股和交易规则',
    hint: '看净值曲线、回撤、胜率和交易明细，适合评估策略的历史表现。',
  },
  optimizer: {
    title: '参数优化',
    subtitle: '网格搜索最优参数组合',
    hint: '在独立 worker 中复用基础数据并串行回测参数组合，按夏普/索提诺等目标排序。',
  },
  walkforward: {
    title: '步进优化',
    subtitle: '滚动窗口样本外验证',
    hint: '每折训练区间优化、测试区间验证，看样本外是否退化以识别过拟合。',
  },
}

const TAB_ICONS: Record<Tab, typeof BarChart3> = {
  factor: BarChart3,
  strategy: FlaskConical,
  optimizer: SlidersHorizontal,
  walkforward: Waypoints,
}

export function Backtest() {
  const [activeTab, setActiveTab] = useState<Tab>('strategy')
  const summaries = useBacktestSummaries()

  const modeSwitch = (
    <div className="inline-flex rounded-btn border border-border bg-surface/80 p-0.5 shadow-sm">
      {(['factor', 'strategy', 'optimizer', 'walkforward'] as const).map(tab => {
        const Icon = TAB_ICONS[tab]
        const active = activeTab === tab
        return (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`inline-flex items-center gap-1.5 rounded-[5px] px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer ${
              active
                ? 'bg-accent text-white shadow-sm'
                : 'text-secondary hover:bg-elevated hover:text-foreground'
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
            {MODES[tab].title}
            {(tab === 'optimizer' || tab === 'walkforward') && (
              <span className={`rounded border px-1 py-px text-[8px] font-semibold uppercase ${
                active ? 'border-white/40 bg-white/15 text-white' : 'border-amber-400/30 bg-amber-400/10 text-amber-400'
              }`}>
                Beta
              </span>
            )}
          </button>
        )
      })}
    </div>
  )

  return (
    <div className="min-h-full bg-base flex flex-col">
      <div className="hidden md:block">
        <PageHeader
        title="回测工作台"
        subtitle={`${MODES[activeTab].title} · ${MODES[activeTab].hint}`}
        right={modeSwitch}
        className="shrink-0 bg-base/95"
        />
      </div>

      <main className="hidden flex-1 min-h-0 px-3 pb-3 pt-3 md:block lg:px-4 lg:pb-4">
        {activeTab === 'factor' && <FactorBacktest />}
        {activeTab === 'strategy' && <StrategyBacktest />}
        {activeTab === 'optimizer' && <StrategyOptimizer />}
        {activeTab === 'walkforward' && <StrategyWalkForward />}
      </main>

      <main className="min-h-full px-3 py-4 md:hidden" aria-label="回测摘要">
        <h1 className="text-base font-semibold text-foreground">最近回测摘要</h1>
        <div className="mt-3 space-y-2">
          {summaries.isLoading ? (
            <div className="rounded-card border border-border bg-surface px-3 py-8 text-center text-xs text-muted">
              正在加载
            </div>
          ) : (summaries.data?.summaries.length ?? 0) === 0 ? (
            <div className="rounded-card border border-border bg-surface px-3 py-8 text-center text-xs text-muted">
              暂无回测摘要
            </div>
          ) : summaries.data?.summaries.map(summary => (
            <MobileBacktestSummary key={summary.id} summary={summary} />
          ))}
        </div>
      </main>
    </div>
  )
}

function MobileBacktestSummary({ summary }: { summary: BacktestSummary }) {
  return (
    <article className="rounded-card border border-border bg-surface p-3">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-medium text-foreground">{summary.strategy_id}</h2>
          <p className="mt-0.5 font-mono text-[10px] text-muted">{summary.task} · {summary.engine}</p>
        </div>
        <span className="shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] text-secondary">
          {summary.execution_target === 'local' ? '本地' : '云端'}
        </span>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2">
        {Object.entries(summary.stats).slice(0, 6).map(([label, value]) => (
          <div key={label} className="min-w-0 border-t border-border/60 pt-1.5">
            <dt className="truncate text-[10px] text-muted">{label}</dt>
            <dd className="mt-0.5 truncate font-mono text-xs text-foreground">{Number(value).toFixed(2)}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 font-mono text-[10px] text-muted">数据日期 {summary.data_as_of}</p>
    </article>
  )
}
