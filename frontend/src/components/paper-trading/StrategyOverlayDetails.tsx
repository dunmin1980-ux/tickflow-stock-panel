import { useId } from 'react'
import { ArrowLeft, ArrowRight, CheckCircle2, CircleHelp, CircleMinus } from 'lucide-react'
import type { Condition } from '@/lib/research-engine'
import {
  CHAN_OVERLAY_LABEL, STRATEGY_STATUS_LABELS, strategyOverlayGroupSummary,
  type StrategyOverlayGroup, type StrategyOverlayMarker, type StrategyPaperRecord,
} from '@/lib/strategy-graphics'

const numeric = (value: number | null) => value == null ? '--' : String(value)
const signed = (value: number | null) => value == null ? '--' : `${value > 0 ? '+' : ''}${value}`
const CHANGE = { IMPROVING: '改善', WEAKENING: '减弱', MIXED: '变化不一致', UNCHANGED: '未变化', NOT_AVAILABLE: '无法比较' }

function Evidence({ conditions }: { conditions: Condition[] }) {
  return <ul className="space-y-2">
    {conditions.map((condition, index) => {
      const Icon = condition.passed === true ? CheckCircle2 : condition.passed === false ? CircleMinus : CircleHelp
      return <li key={`${condition.label}-${index}`} className="min-w-0 border-l-2 border-border pl-2">
        <p className="flex flex-wrap items-center gap-1.5 font-medium">
          <Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />{condition.label}
          <span>{condition.passed === true ? '条件满足' : condition.passed === false ? '条件未满足' : '数据缺失'}</span>
        </p>
        <p>{condition.actual} · 判定依据：{condition.required}</p>
        <p className="text-secondary">观测 {numeric(condition.observed)} · 阈值 {Array.isArray(condition.threshold)
          ? condition.threshold.join(' ~ ') : numeric(condition.threshold)} · {condition.operator}</p>
        <p className="text-secondary">裕量 {signed(condition.margin)} · 差值 {numeric(condition.gap)} · {condition.unit}
          {' · '}{condition.temporal_scope === 'PREVIOUS_DAY' ? '前一交易日' : '当日'}</p>
      </li>
    })}
  </ul>
}

function OverlayEntry({ marker }: { marker: StrategyOverlayMarker }) {
  const id = useId()
  const chan = marker.kind === 'CHAN_STRUCTURE'
  const structure = marker.structure
  const delta = marker.delta_vs_previous
  return <article aria-labelledby={id} className="min-w-0 space-y-2 border-t border-border py-3 text-xs">
    <h4 id={id} className="text-sm font-semibold">{marker.strategy_name}</h4>
    <p className="flex flex-wrap gap-x-2 gap-y-1">
      <span>{chan ? CHAN_OVERLAY_LABEL : STRATEGY_STATUS_LABELS[marker.canonical_status]}</span>
      <span className="font-mono text-secondary">{marker.canonical_status}</span>
      <span>条件进度 {chan ? 'N/A' : `${marker.conditions_met ?? '--'}/${marker.conditions_total ?? '--'}`}</span>
    </p>
    {(!chan || marker.summary !== CHAN_OVERLAY_LABEL) && <p>{marker.summary}</p>}
    <p className="text-secondary">观察日期 {marker.trade_date} · QFQ 价格 {marker.price}</p>
    {chan ? structure ? <div className="space-y-1 text-secondary">
      <p>事件类型 {structure.type}</p>
      {structure.type === 'STROKE_CANDIDATE' ? <>
        <p>发生区间 {structure.start_date} → {structure.end_date}</p>
        <p>方向 {structure.direction} · 起点价格 {structure.start_price} · 终点价格 {structure.end_price}</p>
        <p>原始 K 线间隔 {structure.source_bar_separation}</p>
      </> : <>
        <p>发生日期 {structure.date}</p>
        <p>结构价格 {structure.price}</p>
      </>}
      <p>确认日期 {structure.confirmed_at}</p>
    </div> : <p className="text-secondary">原始结构记录不可用</p> : <>
      <h5 className="font-medium">条件证据</h5>
      {marker.evidence.length ? <Evidence conditions={marker.evidence} /> : <p className="text-secondary">暂无条件证据</p>}
      <h5 className="font-medium">未满足条件</h5>
      {marker.failed_conditions.length ? <Evidence conditions={marker.failed_conditions} />
        : <p className="text-secondary">未提供未满足条件记录</p>}
      <h5 className="font-medium">较前日变化</h5>
      {delta ? <>
        <p>{CHANGE[delta.change]} · {delta.change} · 满足项 {signed(delta.conditions_met_delta)} · 前日 {delta.previous_conditions_met}</p>
        <ul className="space-y-1 text-secondary">{delta.conditions.map((condition, index) => <li key={`${condition.label}-${index}`}>
          {condition.label} · {numeric(condition.previous)} → {numeric(condition.current)}
          {' · '}变化 {signed(condition.value_delta)} {condition.unit} · 裕量变化 {signed(condition.margin_delta)}
          {' · '}{CHANGE[condition.change]} · {condition.change}
        </li>)}</ul>
      </> : <p className="text-secondary">无可比较的前日证据</p>}
    </>}
  </article>
}

export function StrategyOverlayDetails({ dates, selectedDate, onDateChange, group, record }: {
  dates: string[]
  selectedDate: string
  onDateChange: (date: string) => void
  group: StrategyOverlayGroup | undefined
  record: StrategyPaperRecord | undefined
}) {
  const currentIndex = dates.indexOf(selectedDate)
  const published = record?.status === 'PUBLISHED' && record.trade_date === selectedDate ? record : undefined
  const buttonClass = 'inline-flex h-10 w-10 shrink-0 items-center justify-center rounded border border-border hover:bg-elevated disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent'
  return <div className="min-w-0 space-y-3 border-t border-border pt-3">
    <div className="flex min-w-0 items-end gap-2">
      <label className="min-w-0 flex-1 text-xs text-secondary">观察日期
        <select aria-label="观察日期" value={selectedDate} onChange={event => onDateChange(event.target.value)}
          className="mt-1 block h-10 w-full min-w-0 rounded border border-border bg-surface px-2 text-xs text-foreground focus-visible:outline-accent">
          {dates.map(date => <option key={date} value={date}>{date}</option>)}
        </select>
      </label>
      <button type="button" aria-label="上一观察日" title="上一观察日" className={buttonClass}
        disabled={currentIndex <= 0} onClick={() => onDateChange(dates[currentIndex - 1])}>
        <ArrowLeft aria-hidden="true" className="h-4 w-4" />
      </button>
      <button type="button" aria-label="下一观察日" title="下一观察日" className={buttonClass}
        disabled={currentIndex < 0 || currentIndex >= dates.length - 1} onClick={() => onDateChange(dates[currentIndex + 1])}>
        <ArrowRight aria-hidden="true" className="h-4 w-4" />
      </button>
    </div>
    <section aria-label="当日模拟记录" className="min-w-0 space-y-1 text-xs">
      <h3 className="font-medium">{selectedDate} · 当日模拟记录</h3>
      {published ? <>
        <p>PUBLISHED · 动作 {published.paper_action ?? '--'} · 信号 {published.research_signal ?? '--'} · 数量 {published.quantity ?? '--'}</p>
        <p className="text-secondary">{published.hold_explanation === null
          ? '已发布记录未保存 HOLD 原因，不回填或推断'
          : Array.isArray(published.hold_explanation) ? published.hold_explanation.join('；') : published.hold_explanation}</p>
        <p className="break-all text-muted">来源 {published.source ?? '--'}</p>
        <p className="break-all text-muted">SHA256 {published.source_sha256 ?? '--'}</p>
      </> : <p className="text-secondary">当日模拟记录不可用 · NOT_AVAILABLE</p>}
    </section>
    <section aria-label="策略观察详情" tabIndex={0}
      className="max-h-[32rem] min-w-0 overflow-y-auto overscroll-contain pr-2 [overflow-wrap:anywhere] focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
      <h3 className="py-2 text-xs font-medium" aria-live="polite">{selectedDate} · {group ? strategyOverlayGroupSummary(group) : '0 条观察'}</h3>
      {group?.markers.length ? group.markers.map(marker => <OverlayEntry key={marker.marker_id} marker={marker} />)
        : <p className="py-3 text-xs text-secondary">当日无符合筛选的观察</p>}
    </section>
  </div>
}
