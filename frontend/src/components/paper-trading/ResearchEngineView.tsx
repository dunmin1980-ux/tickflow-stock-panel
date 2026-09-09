import { Fragment, useId, useState } from 'react'
import { Activity, CheckCircle2, ChevronDown, ChevronRight, CircleHelp, CircleMinus, Download } from 'lucide-react'
import type { Chan, Condition, MetricDelta, ResearchConsensus, ResearchEngine, StrategyResearch } from '@/lib/research-engine'
import { cn } from '@/lib/cn'

const CONSENSUS: Record<ResearchConsensus, string> = {
  STRONG_BULLISH_ALIGNMENT: '偏多证据高度一致', BULLISH_WITH_CONFLICTS: '偏多，存在分歧',
  MIXED: '多空证据混合', BEARISH_WITH_CONFLICTS: '偏空，存在分歧',
  STRONG_BEARISH_ALIGNMENT: '偏空证据高度一致', SETUP_WATCH: '形态观察', INSUFFICIENT_DATA: '数据不足',
}
const STATUS = { TRIGGERED: '已触发', NEAR_TRIGGER: '接近触发', NOT_TRIGGERED: '未触发', NOT_AVAILABLE: '数据不足' }
const BIAS = { BULLISH: '偏多', BEARISH: '偏空', NEUTRAL: '中性', SETUP: '形态观察' }
const STRUCTURE = { UP_STRUCTURE: '上行', DOWN_STRUCTURE: '下行', RANGE_STRUCTURE: '整理', TRANSITION: '过渡', INSUFFICIENT_DATA: '数据不足' }
const LOCATION = {
  ABOVE_CONFIRMED_RANGE: '收盘高于最近确认高点，待后续分型确认',
  BELOW_CONFIRMED_RANGE: '收盘低于最近确认低点，待后续分型确认',
  INSIDE_CONFIRMED_RANGE: '收盘位于最近确认区间内', NOT_AVAILABLE: '当前价格位置不可用',
}
const CHANGE: Record<string, string> = {
  IMPROVING: '改善', WEAKENING: '减弱', MIXED: '变化不一致', UNCHANGED: '未变化',
  NOT_AVAILABLE: '无法比较', STRENGTHENING: '增强', REVERSING: '反转', NEW_FRACTAL: '新增确认分型',
  RISING: '上升', FALLING: '下降', EXPANDING: '放大', CONTRACTING: '缩小',
}
const UNITS: Record<string, string> = { QFQ_PRICE: '前复权价格单位', RATIO: '倍', PERCENTAGE_POINTS: '个百分点' }
const numbers = new Intl.NumberFormat('en-US', { maximumFractionDigits: 8, useGrouping: false })
const numeric = (value: number | null) => value == null ? '--'
  : value !== 0 && Math.abs(value) < 1e-8 ? value.toExponential(2) : numbers.format(value)
const signed = (value: number | null) => value == null ? '--' : `${value > 0 ? '+' : ''}${numeric(value)}`

function StaleNotice({ tradeDate, requestedDate }: { tradeDate: string; requestedDate: string }) {
  return tradeDate !== requestedDate ? <p role="status" className="mt-2 border-l-2 border-warning px-3 py-2 text-xs text-warning">
    当前展示 {tradeDate} 的研究结果，尚非 {requestedDate} 当日结果。
  </p> : null
}

function GapList({ conditions }: { conditions: Condition[] }) {
  return <ul className="space-y-1">
    {conditions.map((condition, index) => <li key={`${condition.label}-${index}`}>
      {condition.label}：{condition.temporal_scope === 'PREVIOUS_DAY' ? '前日条件，不可由今日补足'
        : condition.gap === null ? '差值不可用'
        : `差 ${numeric(condition.gap)} ${UNITS[condition.unit]}`}
      {condition.passed === false && condition.gap === 0 && ['gt', 'lt', 'between'].includes(condition.operator)
        && <span className="block text-warning">严格阈值仍未满足</span>}
    </li>)}
  </ul>
}

function TextList({ items, empty = '无' }: { items: string[]; empty?: string }) {
  return items.length ? <ul className="space-y-1">{items.map((item, index) => <li key={index}>{item}</li>)}</ul>
    : <p className="text-muted">{empty}</p>
}

function CompactTextList({ items, label, empty }: { items: string[]; label: string; empty?: string }) {
  return <>
    <TextList items={items.slice(0, 2)} empty={empty} />
    {items.length > 2 && <details className="mt-1">
      <summary className="cursor-pointer text-secondary">更多{label}（{items.length - 2}）</summary>
      <div className="mt-2"><TextList items={items.slice(2)} /></div>
    </details>}
  </>
}

function ChanSummary({ chan }: { chan: Chan }) {
  return <div className="space-y-1 text-xs">
    <p>确认结构：{STRUCTURE[chan.current_structure]} <span className="break-all font-mono text-muted">{chan.current_structure}</span></p>
    <p className="text-secondary">确认高点 {numeric(chan.structure_high)} · 低点 {numeric(chan.structure_low)} · 收盘 {numeric(chan.latest_close)} · QFQ</p>
    <p className={cn(chan.price_location === 'BELOW_CONFIRMED_RANGE' || chan.price_location === 'ABOVE_CONFIRMED_RANGE'
      ? 'text-warning' : 'text-secondary')}>{LOCATION[chan.price_location]}</p>
  </div>
}

export function ResearchOverview({ engine, requestedDate }: { engine: ResearchEngine; requestedDate: string }) {
  const { summary, aggregate } = engine
  const current = engine.trade_date === requestedDate
  const closest = summary.closest_trigger
  const confidence = summary.research_confidence
  return (
    <section aria-label="Research Overview" className="min-w-0 border-y border-border py-3 text-foreground [overflow-wrap:anywhere]">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[16px] font-semibold text-foreground">{current ? '今日研究总览' : '研究快照总览'}</h2>
        <p className="text-xs text-secondary">研究日期 {engine.trade_date} · {engine.symbol} · QFQ 前复权日线 · Claims {summary.data_quality.claims_status} / {summary.claims_count}</p>
      </div>
      <StaleNotice tradeDate={engine.trade_date} requestedDate={requestedDate} />
      <p className="mt-2 text-xs text-muted">Research ≠ Paper Action · 研究结论不改变既有模拟动作 · 不构成投资建议</p>
      <div role="group" aria-label="当日核心指标" className="mt-3 grid grid-cols-2 gap-2 border-y border-border py-2 sm:grid-cols-4">
        {([['macd_dif', 'MACD DIF'], ['macd_dea', 'MACD DEA'], ['macd_hist', 'MACD 柱值'], ['rsi6', 'RSI6']] as const).map(([key, label]) => (
          <div key={key} className="min-w-0 text-xs"><span className="text-secondary">{label}</span>{' '}
            <span className="font-mono font-medium">{numeric(engine.indicators?.[key] ?? null)}</span>
          </div>
        ))}
      </div>
      <div className="mt-3 grid gap-3 lg:grid-cols-3">
        <div className="min-w-0">
          <h3 className="text-xs text-secondary">Research Consensus · 研究结论</h3>
          <p className="mt-1 text-[16px] font-semibold text-foreground">{CONSENSUS[summary.research_consensus]}</p>
          <p className="mt-1 break-all font-mono text-[11px] text-secondary">{summary.research_consensus}</p>
          <p className="mt-2 text-xs">证据一致性 · {confidence.category} · 不是预测概率或胜率</p>
          <p className="mt-1 text-xs text-secondary">{confidence.basis}</p>
        </div>
        <div className="min-w-0 border-t border-border pt-3 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
          <h3 className="text-xs text-secondary">Paper Action · {current ? '模拟记录' : `历史模拟记录 · ${summary.trade_date}`}</h3>
          <p className="mt-1 text-[16px] font-semibold text-foreground">{summary.paper_action === 'NOT_RUN' ? 'NOT_RUN · 未形成动作' : summary.paper_action}</p>
          {summary.paper_action === 'HOLD' && <p className="mt-1 text-sm font-medium">
            {summary.hold_reason === 'FLAT_WAIT' ? 'FLAT_WAIT · 空仓观望'
              : summary.hold_reason === 'POSITION_HOLD' ? 'POSITION_HOLD · 持仓不变' : '持仓含义未确认'}
          </p>}
          <p className="mt-1 text-[11px] text-muted">持仓状态：{summary.position_state}</p>
        </div>
        <div className="min-w-0 border-t border-border pt-3 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
          <h3 className="text-xs text-secondary">最近触发条件</h3>
          {closest ? <>
            <p className="mt-1 text-sm font-semibold">{closest.strategy_name} · {closest.conditions_met}/{closest.conditions_total}</p>
            <div className="mt-2 text-xs text-secondary"><GapList conditions={closest.distance_to_trigger} /></div>
          </> : <p className="mt-1 text-xs text-muted">暂无可比较的未触发策略</p>}
          <div className="mt-3 border-t border-border pt-2"><ChanSummary chan={summary.chan_structure} /></div>
        </div>
      </div>
      <div className="mt-3 space-y-2 border-t border-border pt-2 text-xs">
        <p className="text-secondary">较前日：{[['macd_hist', 'MACD'], ['rsi6', 'RSI6'], ['volume_ratio', '相对量能']].map(([id, label]) => {
          const direction = summary.changes_vs_previous.find(change => change.id === id)?.direction ?? 'NOT_AVAILABLE'
          return `${label}：${CHANGE[direction] ?? direction}`
        }).join(' / ')}</p>
        <div className="flex min-w-0 gap-2">
          <h3 className="shrink-0 font-medium">下一观察</h3>
          <div className="min-w-0 text-secondary"><TextList items={summary.next_observation_conditions.slice(0, 2)} empty="暂无已记录观察条件" /></div>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-border pt-2 text-[11px] text-secondary">
        <span>偏多 {aggregate.bullish_count}</span><span>偏空 {aggregate.bearish_count}</span>
        <span>中性 {aggregate.neutral_count}</span><span>形态观察 {aggregate.setup_count}</span>
        <span>已触发 {aggregate.triggered_strategies.length}</span><span>接近触发 {aggregate.near_trigger_strategies.length}</span>
      </div>
      <div className="mt-2 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
        <div className="min-w-0"><h3 className="mb-1 font-medium">支持因素</h3><CompactTextList items={summary.supporting_factors} label="支持因素" /></div>
        <div className="min-w-0"><h3 className="mb-1 font-medium">限制因素</h3><CompactTextList items={summary.limiting_factors} label="限制因素" /></div>
        <div className="min-w-0 text-warning"><h3 className="mb-1 font-medium">策略分歧</h3><CompactTextList items={summary.strategy_conflicts} label="策略分歧" empty="无已记录分歧" /></div>
        <div className="min-w-0"><h3 className="mb-1 font-medium">动作说明</h3><CompactTextList items={summary.hold_explanation} label="动作说明" empty="无额外动作说明" /></div>
      </div>
    </section>
  )
}

function ConditionEvidence({ conditions }: { conditions: Condition[] }) {
  return <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
    {conditions.map((condition, index) => {
      const Icon = condition.passed === true ? CheckCircle2 : condition.passed === false ? CircleMinus : CircleHelp
      return <li key={`${condition.label}-${index}`} className="min-w-0 border-l border-border pl-3 text-xs">
        <div className="flex items-center gap-2 font-medium"><Icon className="h-3.5 w-3.5 shrink-0" aria-label={condition.passed === null ? '数据缺失' : condition.passed ? '条件满足' : '条件未满足'} />{condition.label}</div>
        <p className="mt-1 font-mono">{condition.actual}</p>
        <p className="mt-1 text-secondary">{condition.required}</p>
        <p className="mt-1 text-muted">{condition.temporal_scope === 'PREVIOUS_DAY' ? '前一交易日' : '当日'} · {condition.operator} · {UNITS[condition.unit]}</p>
        <p className="mt-1 text-secondary">观测 {numeric(condition.observed)} · 阈值 {Array.isArray(condition.threshold) ? condition.threshold.map(numeric).join(' ~ ') : numeric(condition.threshold)}</p>
        <p className="mt-1 text-secondary">裕量 {signed(condition.margin)} · 差值 {numeric(condition.gap)}</p>
      </li>
    })}
  </ul>
}

function StrategyEvidence({ strategy }: { strategy: StrategyResearch }) {
  return <>
    <p className="mb-3 text-xs text-secondary">{strategy.trade_date} · {strategy.timeframe} · {strategy.price_basis} · 未满足条件 {strategy.failed_conditions.length} 项</p>
    <ConditionEvidence conditions={strategy.evidence} />
    <h4 className="mb-2 mt-4 text-xs font-semibold">逐条件较前日变化</h4>
    <ul className="space-y-2 text-xs text-secondary">
      {strategy.delta_vs_previous.conditions.map((delta, index) => <li key={`${delta.label}-${index}`}>
        {delta.label} · {numeric(delta.previous)} → {numeric(delta.current)} · 变化 {signed(delta.value_delta)} {UNITS[delta.unit] ?? delta.unit}
        {' · '}裕量变化 {signed(delta.margin_delta)} · {CHANGE[delta.change]} ({delta.change})
      </li>)}
    </ul>
    {strategy.delta_vs_previous.conditions.length === 0 && <p className="text-xs text-muted">无可比较的前日证据</p>}
    <p className="mt-3 text-xs text-secondary">风险条件：{strategy.risk_triggered === null ? '数据不可用' : strategy.risk_triggered ? '已出现' : '未出现'}</p>
    <p className="mt-2 break-all text-xs text-muted">计算来源：{strategy.calculation_source}</p>
    <pre className="mt-2 whitespace-pre-wrap break-all text-[11px] text-secondary">{JSON.stringify(strategy.parameters, null, 2)}</pre>
  </>
}

function ChanEvidence({ chan }: { chan: Chan }) {
  return <>
    <p className="mb-3 break-all text-xs text-muted">{chan.mode} · 研究日期 {chan.trade_date ?? '--'} · 前日确认结构 {STRUCTURE[chan.previous_structure]}</p>
    <ChanSummary chan={chan} />
    <div className="mt-4 grid gap-4 md:grid-cols-2">
      {([['已确认顶分型', chan.top_fractals], ['已确认底分型', chan.bottom_fractals]] as const).map(([title, fractals]) => (
        <div key={title} className="min-w-0">
          <h4 className="mb-2 text-xs font-semibold">{title} · {fractals.length}</h4>
          <ul className="space-y-2 text-xs text-secondary">{fractals.map((fractal, index) => <li key={`${fractal.date}-${index}`}>
            <p>分型日期 {fractal.date} · 确认日期 {fractal.confirmed_at}</p>
            <p className="mt-1">{numeric(fractal.price)} QFQ · {fractal.type}</p>
          </li>)}</ul>
          {fractals.length === 0 && <p className="text-xs text-muted">尚无已确认分型</p>}
        </div>
      ))}
    </div>
    <h4 className="mb-2 mt-4 text-xs font-semibold">候选笔 · {chan.stroke_candidates.length}</h4>
    <ul className="space-y-2 text-xs text-secondary">{chan.stroke_candidates.map((stroke, index) => <li key={`${stroke.start_date}-${index}`}>
      <p>{stroke.start_date} → {stroke.end_date} · {stroke.direction}</p>
      <p className="mt-1">{numeric(stroke.start_price)} → {numeric(stroke.end_price)} QFQ · 确认日期 {stroke.confirmed_at} · 间隔 {stroke.source_bar_separation} 根日线</p>
    </li>)}</ul>
    {chan.stroke_candidates.length === 0 && <p className="text-xs text-muted">尚无候选笔</p>}
    <p className="mt-3 text-xs text-muted">候选笔，不等同于完整缠论笔；不包含线段、背驰或多周期确认。</p>
    <p className="mt-1 text-xs text-muted">中枢：{chan.central_zone} · 未实现</p>
  </>
}

function MetricChanges({ changes }: { changes: MetricDelta[] }) {
  return changes.length ? <div className="overflow-x-auto">
    <table aria-label="较前日指标变化" className="w-full min-w-[540px] text-left text-xs">
      <thead className="text-muted"><tr>{['指标', '前日', '当日', '变化', '单位', '方向'].map(title => <th key={title} scope="col" className="px-2 py-2 font-medium">{title}</th>)}</tr></thead>
      <tbody>{changes.map(delta => <tr key={delta.id} className="border-t border-border">
        <th scope="row" className="px-2 py-2 font-medium">{delta.label}</th>
        <td className="px-2 py-2 font-mono">{numeric(delta.previous)}</td><td className="px-2 py-2 font-mono">{numeric(delta.current)}</td>
        <td className="px-2 py-2 font-mono">{signed(delta.delta)}</td><td className="px-2 py-2">{UNITS[delta.unit] ?? delta.unit}</td>
        <td className="px-2 py-2">{CHANGE[delta.direction] ?? delta.direction}</td>
      </tr>)}</tbody>
    </table>
  </div> : <p className="text-xs text-muted">无可比较的前日指标</p>
}

function downloadResearch(engine: ResearchEngine, format: 'json' | 'md') {
  const content = format === 'json' ? JSON.stringify(engine, null, 2) : engine.daily_markdown
  const type = format === 'json' ? 'application/json;charset=utf-8' : 'text/markdown;charset=utf-8'
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = `research-${engine.symbol}-${engine.trade_date}.${format}`
  document.body.appendChild(link)
  try { link.click() } finally {
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
}

export function ResearchEngineView({ engine, requestedDate }: { engine: ResearchEngine; requestedDate: string }) {
  const [expanded, setExpanded] = useState<string[]>([])
  const id = useId()
  const { chan_structure: chan, summary } = engine
  const toggle = (key: string) => setExpanded(rows => rows.includes(key) ? rows.filter(row => row !== key) : [...rows, key])
  const evidenceButton = (key: string, name: string) => <button type="button"
    aria-label={`${expanded.includes(key) ? '收起' : '展开'} ${name} 证据`}
    title={`${expanded.includes(key) ? '收起' : '展开'} ${name} 证据`}
    aria-expanded={expanded.includes(key)} aria-controls={`${id}-${key}`} onClick={() => toggle(key)}
    className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded hover:bg-elevated focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
    {expanded.includes(key) ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
  </button>
  return (
    <section aria-label="日线多策略研究" className="min-w-0 border-y border-border py-4 text-foreground [overflow-wrap:anywhere]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-[16px] font-semibold text-foreground"><Activity className="h-4 w-4 shrink-0" />日线多策略研究</h2>
          <p className="mt-1 text-xs text-secondary">研究日期 {engine.trade_date} · {engine.symbol} · QFQ 前复权日线 · Research Engine v{engine.schema_version}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {([['json', 'JSON'], ['md', 'Markdown']] as const).map(([format, label]) => <button key={format}
            type="button" aria-label={`下载研究 ${label}`} title={`下载研究 ${label}`} onClick={() => downloadResearch(engine, format)}
            className="inline-flex min-h-9 items-center gap-2 rounded border border-border px-3 text-xs hover:bg-elevated focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
            <Download className="h-3.5 w-3.5" />{label}
          </button>)}
        </div>
      </div>
      <StaleNotice tradeDate={engine.trade_date} requestedDate={requestedDate} />
      <div className="mt-3 max-w-full overflow-x-auto">
        <table aria-label="七项研究矩阵" className="w-full min-w-[850px] table-fixed text-left text-xs">
          <colgroup><col className="w-[22%]" /><col className="w-[10%]" /><col className="w-[12%]" /><col className="w-[31%]" /><col className="w-[17%]" /><col className="w-[8%]" /></colgroup>
          <thead className="bg-elevated/40 text-secondary"><tr>
            {['研究项 / 状态', '条件满足', '研究偏向', '距触发条件', '较前日', '证据'].map(title => <th scope="col" key={title} className="px-3 py-2 font-medium">{title}</th>)}
          </tr></thead>
          <tbody>{engine.strategies.map(strategy => <Fragment key={strategy.strategy_id}>
            <tr className="border-t border-border align-top">
              <th scope="row" className="px-3 py-3 font-normal">
                <h3 className="text-sm font-semibold">{strategy.strategy_name}</h3>
                <p className={cn('mt-1', strategy.status === 'NEAR_TRIGGER' ? 'text-warning' : 'text-secondary')}>{STATUS[strategy.status]}</p>
                <p className="mt-1 break-all font-mono text-[10px] text-muted">{strategy.status}</p>
              </th>
              <td className="px-3 py-3 font-mono">{strategy.conditions_met}/{strategy.conditions_total}
                {strategy.status === 'NOT_AVAILABLE' && <p className="mt-1 font-sans text-muted">数据不足</p>}</td>
              <td className="px-3 py-3">{BIAS[strategy.research_bias]}<p className="mt-1 text-[10px] text-muted">{strategy.research_bias}</p>
                {strategy.risk_triggered && <p className="mt-1 text-warning">风险条件出现</p>}</td>
              <td className="px-3 py-3 text-secondary">{strategy.status === 'NOT_AVAILABLE' ? '差值不可用'
                : strategy.status === 'TRIGGERED' ? '条件已全部满足'
                : <GapList conditions={strategy.distance_to_trigger} />}</td>
              <td className="px-3 py-3">{CHANGE[strategy.delta_vs_previous.change]}
                <p className="mt-1 break-all font-mono text-[10px] text-muted">{strategy.delta_vs_previous.change}</p>
                {strategy.delta_vs_previous.change !== 'NOT_AVAILABLE' && <p className="mt-1 text-secondary">满足项 {signed(strategy.delta_vs_previous.conditions_met_delta)} · 前日 {strategy.delta_vs_previous.previous_conditions_met}</p>}</td>
              <td className="px-3 py-2">{evidenceButton(strategy.strategy_id, strategy.strategy_name)}</td>
            </tr>
            {expanded.includes(strategy.strategy_id) && <tr className="border-t border-border"><td colSpan={6} className="p-3">
              <div role="region" aria-label={`${strategy.strategy_name} 证据`} id={`${id}-${strategy.strategy_id}`} className="max-w-[calc(100vw-4rem)] xl:max-w-none">
                <StrategyEvidence strategy={strategy} />
              </div>
            </td></tr>}
          </Fragment>)}
            <tr className="border-t border-border align-top">
              <th scope="row" className="px-3 py-3 font-normal"><h3 className="text-sm font-semibold">缠论日线结构</h3><p className="mt-1 text-secondary">确认结构：{STRUCTURE[chan.current_structure]}</p><p className="mt-1 break-all font-mono text-[10px] text-muted">{chan.current_structure}</p></th>
              <td className="px-3 py-3 text-secondary">不适用<p className="mt-1">顶 {chan.top_fractals.length} / 底 {chan.bottom_fractals.length}</p></td>
              <td className="px-3 py-3 text-secondary">结构观察<p className="mt-1 text-muted">非交易触发器</p></td>
              <td className="px-3 py-3 text-secondary"><p>{LOCATION[chan.price_location]}</p><p className="mt-1 break-all text-[10px] text-muted">{chan.price_location}</p><p className="mt-1">高 {numeric(chan.structure_high)} / 低 {numeric(chan.structure_low)} / 收盘 {numeric(chan.latest_close)} QFQ</p></td>
              <td className="px-3 py-3">{CHANGE[chan.change]}<p className="mt-1 break-all font-mono text-[10px] text-muted">{chan.change}</p><p className="mt-1 text-secondary">前日 {STRUCTURE[chan.previous_structure]}</p></td>
              <td className="px-3 py-2">{evidenceButton('chan', '缠论日线结构')}</td>
            </tr>
            {expanded.includes('chan') && <tr className="border-t border-border"><td colSpan={6} className="p-3">
              <div role="region" aria-label="缠论日线结构 证据" id={`${id}-chan`} className="max-w-[calc(100vw-4rem)] xl:max-w-none"><ChanEvidence chan={chan} /></div>
            </td></tr>}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-muted">条件满足数与差值仅描述规则证据，不是概率；不同单位不合并。比较基于同一前复权快照的前一交易日。</p>
      <div className="mt-4 border-t border-border pt-3 text-xs">
        <h3 className="mb-2 font-semibold">下一观察条件</h3><TextList items={summary.next_observation_conditions} empty="暂无已记录观察条件" />
      </div>
      <details className="mt-3 border-t border-border pt-3">
        <summary className="cursor-pointer text-sm font-medium">较前日指标变化</summary>
        {engine.aggregate.delta_vs_previous && <div role="group" aria-label="研究结论较前日变化" className="my-3 space-y-1 text-xs">
          <p className="text-secondary">{engine.aggregate.delta_vs_previous.trade_date} → {engine.trade_date} · {engine.aggregate.delta_vs_previous.changed ? '结论已变化' : '结论未变化'}</p>
          <p className="break-all font-mono">{engine.aggregate.delta_vs_previous.previous_consensus} → {engine.aggregate.delta_vs_previous.current_consensus}</p>
        </div>}
        <div className="mt-2"><MetricChanges changes={summary.changes_vs_previous} /></div>
      </details>
      <details className="mt-3 border-t border-border pt-3 text-xs">
        <summary className="cursor-pointer text-sm font-medium">数据质量与规则追踪</summary>
        <div className="mt-3 space-y-2 text-secondary">
          <p>来源：{summary.data_quality.source_provider} · {summary.data_quality.price_basis} · Claims {summary.data_quality.claims_status} / {summary.claims_count}</p>
          <p>财务数据：{summary.data_quality.financial_data} · 未接入</p>
          <p>重大新闻：{summary.data_quality.material_news} · 未接入</p>
          <h4 className="font-semibold text-foreground">供应商待确认</h4><TextList items={summary.data_quality.vendor_pending} />
          <h4 className="font-semibold text-foreground">来源校验值</h4>
          <dl>{Object.entries(summary.data_quality.source_hashes).map(([key, value]) => <div key={key} className="mt-2"><dt>{key}</dt><dd className="mt-1 break-all font-mono">{value}</dd></div>)}</dl>
          <h4 className="font-semibold text-foreground">规则追踪</h4><TextList items={engine.aggregate.rule_trace} />
          <p>仅研究 / 仅模拟 · 禁止发布 · 实盘禁用 · 不构成投资建议</p>
        </div>
      </details>
      <details className="mt-3 border-t border-border pt-3">
        <summary className="cursor-pointer text-sm font-medium">研究日报 Markdown</summary>
        <pre className="mt-3 whitespace-pre-wrap break-words text-xs leading-relaxed text-secondary">{engine.daily_markdown}</pre>
      </details>
    </section>
  )
}
