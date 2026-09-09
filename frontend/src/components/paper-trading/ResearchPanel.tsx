import { useState } from 'react'
import { Activity, CheckCircle2, CircleMinus, CircleHelp } from 'lucide-react'
import type { PaperResearchPanel, ResearchCondition } from '@/lib/api'
import { cn } from '@/lib/cn'
import { ResearchEngineView } from './ResearchEngineView'

const STATUS = { MATCHED: '已命中', NOT_MATCHED: '未命中', DATA_UNAVAILABLE: '数据不足' }
const CHANGE = { NEW_MATCH: '新出现', MATCH_ENDED: '已消失', UNCHANGED: '未变化', UNAVAILABLE: '无法比较' }

export function ConditionList({ conditions }: { conditions: ResearchCondition[] }) {
  return (
    <ul className="space-y-3">
      {conditions.map((condition, index) => {
        const Icon = condition.passed === true ? CheckCircle2 : condition.passed === false ? CircleMinus : CircleHelp
        return (
          <li key={`${condition.label}-${index}`} className="flex min-w-0 gap-2 text-xs">
            <Icon aria-label={condition.passed === true ? '条件满足' : condition.passed === false ? '条件未满足' : '数据缺失'}
              className={cn('mt-0.5 h-3.5 w-3.5 shrink-0', condition.passed === true ? 'text-bull' : 'text-muted')} />
            <div className="min-w-0 flex-1">
              <div className="font-medium">{condition.label}</div>
              <div className="mt-0.5 break-words font-mono text-secondary">{condition.actual}</div>
              <div className="mt-0.5 break-words text-muted">{condition.required}</div>
            </div>
          </li>
        )
      })}
    </ul>
  )
}

export function ResearchPanel({ research, requestedDate }: {
  research: PaperResearchPanel | undefined
  requestedDate: string
}) {
  const [filter, setFilter] = useState('all')
  const ready = research?.status === 'READY' ? research : null
  if (ready?.engine) return <ResearchEngineView engine={ready.engine} requestedDate={requestedDate} />
  const rows = ready?.strategies.filter(s => filter === 'all'
    || (filter === 'matched' ? s.status === 'MATCHED' : s.status === 'DATA_UNAVAILABLE')) ?? []

  return (
    <section aria-label="日线多策略研究" className="border-y border-border py-4">
      <div className="flex flex-wrap items-start justify-between gap-3 px-1">
        <div>
          <h2 className="flex items-center gap-2 text-[16px] font-semibold text-foreground"><Activity className="h-4 w-4" />日线多策略研究</h2>
          {ready && <p className="mt-1 text-xs text-secondary">
            {ready.trade_date} · 前复权日线 · 较 {ready.previous_trade_date}
          </p>}
        </div>
        {ready && <div className="flex flex-wrap gap-3 text-xs">
          <span>规则命中 <strong className="text-bull">{ready.matched_count}/{ready.strategies.length}</strong></span>
          <span>风险条件 <strong className="text-warning">{ready.risk_count}</strong></span>
          <span className="text-muted">研究结果 · 不改变模拟动作</span>
        </div>}
      </div>

      {!ready ? (
        <p role={research?.status === 'BLOCKED' ? 'alert' : 'status'} className="mt-4 px-1 text-sm text-secondary">
          {research?.status === 'BLOCKED' ? '研究数据校验未通过，暂停展示结果；模拟账户记录仍保留。'
            : '尚无已验证日线研究结果。完成当日日线输入准备后显示。'}
        </p>
      ) : <>
        {ready.trade_date !== requestedDate && <p className="mt-3 border-l-2 border-warning px-3 py-2 text-xs text-warning">
          当前展示 {ready.trade_date} 的研究结果，尚非 {requestedDate} 当日结果。
        </p>}
        {ready.matched_count > 0 && ready.risk_count > 0 && <p className="mt-3 text-xs text-warning">
          规则存在分歧：部分形态命中，同时出现风险条件，请逐项核对。
        </p>}
        <div aria-label="研究结果筛选" className="mt-4 flex w-fit max-w-full flex-wrap border-b border-border text-xs">
          {([['all', '全部规则'], ['matched', '仅看命中'], ['missing', '数据待补']] as const).map(([key, title]) => (
            <button key={key} type="button" aria-pressed={filter === key} onClick={() => setFilter(key)}
              className={cn('min-h-9 border-b-2 px-3', filter === key ? 'border-foreground font-semibold' : 'border-transparent text-muted')}>
              {title}
            </button>
          ))}
        </div>
        <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {rows.map(strategy => (
            <article key={strategy.id} className="min-w-0 rounded border border-border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold">{strategy.name}</h3>
                <span className={cn('text-xs font-medium', strategy.status === 'MATCHED' ? 'text-bull' : 'text-muted')}>
                  {STATUS[strategy.status]} · {strategy.matched_conditions}/{strategy.conditions.length}
                </span>
              </div>
              <p className="mt-2 text-xs text-secondary">{strategy.description}</p>
              <div className="my-3 flex flex-wrap justify-between gap-2 border-b border-border pb-2 text-[11px] text-muted">
                <span>较前日：{CHANGE[strategy.change]}</span>
                {strategy.risk_triggered && <span className="text-warning">风险条件出现</span>}
              </div>
              <ConditionList conditions={strategy.conditions} />
            </article>
          ))}
        </div>
        {rows.length === 0 && <p className="py-5 text-center text-sm text-muted">
          {filter === 'matched' ? '本日没有规则命中' : '没有数据不足的规则'}
        </p>}
        <div className="mt-3 space-y-1 text-[11px] text-muted">
          <p>比较使用同一前复权快照的前一交易日；量比为当日量 / 前5日均量，绝对量额单位待供应商确认。</p>
          <p>来源：stock-sdk / 腾讯日线 · 缠论：未实现 · 不构成投资建议</p>
        </div>
      </>}
    </section>
  )
}
