import { useId, useState } from 'react'
import { CheckCircle2, ChevronDown, ChevronRight, CircleHelp, CircleMinus } from 'lucide-react'
import type { Condition, ResearchEngine, StrategyResearch } from '@/lib/research-engine'
import { STRATEGY_STATUS_LABELS } from '@/lib/strategy-graphics'
import { cn } from '@/lib/cn'

const CHANGE = { IMPROVING: '改善', WEAKENING: '减弱', MIXED: '变化不一致', UNCHANGED: '未变化', NOT_AVAILABLE: '无法比较' }
const UNITS: Record<string, string> = { QFQ_PRICE: '前复权价格单位', RATIO: '倍', PERCENTAGE_POINTS: '个百分点' }
const numeric = (value: number | null) => value == null ? '--' : String(value)
const signed = (value: number | null) => value == null ? '--' : `${value > 0 ? '+' : ''}${value}`

function ConditionDetail({ condition }: { condition: Condition }) {
  const passed = condition.passed
  const label = passed === true ? '条件满足' : passed === false ? '条件未满足' : '数据缺失'
  const Icon = passed === true ? CheckCircle2 : passed === false ? CircleMinus : CircleHelp
  return <li className="min-w-0 space-y-1 border-t border-border pt-2 text-xs">
    <p className="flex flex-wrap items-center gap-1.5 font-medium">
      <Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />{condition.label}
      <span className={passed === true ? 'text-bull' : 'text-secondary'}>{label}</span>
    </p>
    <progress aria-label={`${condition.label} 条件进度`} aria-valuetext={label}
      max={1} value={passed === null ? undefined : passed ? 1 : 0}
      className="block h-1.5 w-full accent-current text-secondary" />
    <p className="font-mono">{condition.actual}</p>
    <p className="text-secondary">判定依据：{condition.required}</p>
    <p className="text-secondary">观测 {numeric(condition.observed)} · 阈值 {Array.isArray(condition.threshold)
      ? condition.threshold.map(numeric).join(' ~ ') : numeric(condition.threshold)}</p>
    <p className="text-secondary">裕量 {signed(condition.margin)} · 差值 {numeric(condition.gap)} · {UNITS[condition.unit]}</p>
    <p className="text-muted">{condition.temporal_scope === 'PREVIOUS_DAY' ? '前一交易日' : '当日'} · {condition.operator}</p>
    {passed === false && condition.gap === 0 && ['gt', 'lt', 'between'].includes(condition.operator)
      && <p className="text-warning">严格阈值仍未满足</p>}
  </li>
}

function StrategyCard({ strategy, closest }: { strategy: StrategyResearch; closest: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const id = useId()
  const delta = strategy.delta_vs_previous
  const disclosure = `${expanded ? '收起' : '展开'} ${strategy.strategy_name} 证据`
  return <article aria-labelledby={`${id}-name`}
    className={cn('min-w-0 rounded-lg border p-3', closest ? 'border-warning' : 'border-border')}>
    <div className="flex items-start justify-between gap-2">
      <div className="min-w-0">
        <h3 id={`${id}-name`} className="text-sm font-semibold">{strategy.strategy_name}</h3>
        <p className={cn('mt-1 text-xs', strategy.status === 'NEAR_TRIGGER' ? 'text-warning' : 'text-secondary')}>
          {STRATEGY_STATUS_LABELS[strategy.status]}
        </p>
      </div>
      <button type="button" aria-label={disclosure} title={disclosure}
        aria-expanded={expanded} aria-controls={`${id}-evidence`} onClick={() => setExpanded(value => !value)}
        className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded hover:bg-elevated focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
        {expanded ? <ChevronDown aria-hidden="true" className="h-4 w-4" /> : <ChevronRight aria-hidden="true" className="h-4 w-4" />}
      </button>
    </div>
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs">
      <span>条件满足 <strong className="font-mono">{strategy.conditions_met}/{strategy.conditions_total}</strong></span>
      {closest && <span className="text-warning">最接近触发</span>}
    </div>
    <progress aria-label={`${strategy.strategy_name} 条件满足进度`}
      aria-valuetext={`${strategy.conditions_met}/${strategy.conditions_total} 条件满足`}
      max={1} value={strategy.condition_ratio} className={cn('mt-2 block h-1.5 w-full accent-current', closest ? 'text-warning' : 'text-secondary')} />
    <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-secondary">
      <span>支持 {strategy.conditions_met}</span><span>限制 {strategy.failed_conditions.length}</span>
    </p>
    <p className="mt-2 text-xs text-secondary">较前日：{CHANGE[delta.change]}
      {delta.change !== 'NOT_AVAILABLE' && <span> · 满足项 {signed(delta.conditions_met_delta)} · 前日 {delta.previous_conditions_met}</span>}
    </p>
    {expanded && <div id={`${id}-evidence`} role="region" aria-label={`${strategy.strategy_name} 证据`} className="mt-3 space-y-2">
      <p className="text-[11px] text-muted">{strategy.trade_date} · {strategy.price_basis} · 日线</p>
      {strategy.evidence.length > 0 ? <ul className="space-y-3">
        {strategy.evidence.map((condition, index) => <ConditionDetail key={`${condition.label}-${index}`} condition={condition} />)}
      </ul> : <p className="text-xs text-muted">暂无条件证据</p>}
      <h4 className="border-t border-border pt-2 text-xs font-medium">逐条件较前日变化</h4>
      {delta.conditions.length > 0 ? <ul className="space-y-2 text-xs text-secondary">
        {delta.conditions.map((condition, index) => <li key={`${condition.label}-${index}`}>
          {condition.label} · {numeric(condition.previous)} → {numeric(condition.current)} · 变化 {signed(condition.value_delta)} {UNITS[condition.unit] ?? condition.unit}
          {' · '}裕量变化 {signed(condition.margin_delta)} · {CHANGE[condition.change]}
        </li>)}
      </ul> : <p className="text-xs text-muted">无可比较的前日证据</p>}
      <p className="text-[11px] text-muted">计算来源：{strategy.calculation_source}</p>
    </div>}
  </article>
}

export function StrategyOverviewCards({ engine }: { engine: ResearchEngine }) {
  return <section aria-label="六策略总览" className="min-w-0 border-y border-border py-4 text-foreground [overflow-wrap:anywhere]">
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h2 className="text-[16px] font-semibold">策略概览</h2>
      <p className="text-xs text-secondary">研究日期 {engine.trade_date} · {engine.symbol} · QFQ</p>
    </div>
    {engine.strategies.length > 0 ? <div className="mt-3 grid items-start gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {engine.strategies.map(strategy => <StrategyCard key={strategy.strategy_id} strategy={strategy}
        closest={engine.summary.closest_trigger?.strategy_id === strategy.strategy_id} />)}
    </div> : <p role="status" className="py-4 text-sm text-secondary">暂无策略研究数据。</p>}
  </section>
}
