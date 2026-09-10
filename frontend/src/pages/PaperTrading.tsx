import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  CalendarDays,
  ClipboardCheck,
  Database,
  ListTodo,
  Loader2,
  Play,
  Server,
  ShieldCheck,
  TrendingUp,
  WalletCards,
} from 'lucide-react'

import { PageHeader } from '@/components/PageHeader'
import { ConditionList, ResearchPanel } from '@/components/paper-trading/ResearchPanel'
import { ResearchOverview } from '@/components/paper-trading/ResearchEngineView'
import { StrategyGraphics } from '@/components/paper-trading/StrategyGraphics'
import { StrategyOverviewCards } from '@/components/paper-trading/StrategyOverviewCards'
import {
  actionTone,
  dashboardErrorText,
  money,
  readinessCopy,
  sellableQuantity,
  signalLabel,
  todayEquityChange,
  todayRealizedPnl,
  todayUnrealizedPnl,
  totalPositionQuantity,
} from '@/components/paper-trading/presentation'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { QK } from '@/lib/queryKeys'

function EmptyState({ children }: { children: React.ReactNode }) {
  return <div className="py-7 text-center text-xs text-muted">{children}</div>
}

export function PaperTrading() {
  const queryClient = useQueryClient()
  const runningRef = useRef(false)
  const [runMessage, setRunMessage] = useState<string | null>(null)
  const query = useQuery({
    queryKey: QK.paperTrading,
    queryFn: () => api.paperTradingDashboard(),
    refetchOnWindowFocus: false,
    retry: false,
  })
  const mutation = useMutation({
    mutationFn: (targetDate: string) => api.paperTradingRun(targetDate),
    onSuccess: result => {
      queryClient.setQueryData(QK.paperTrading, result)
      setRunMessage(result.run_status === 'DAY_PUBLISHED' ? '今日模拟盘已完成' : '今日模拟盘此前已完成')
    },
    onSettled: () => {
      runningRef.current = false
    },
  })

  const data = query.data
  const canRun = data ? [
    'READY_REFERENCE',
    'READY_STAGED',
    'READY_VALIDATED_DAILY',
    'PREPARABLE',
  ].includes(data.input_readiness.status) : false

  if (query.isLoading) {
    return (
      <div className="grid h-full place-items-center text-xs text-muted">
        <span className="flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" />正在读取今日工作台</span>
      </div>
    )
  }

  if (!data || query.error) {
    return (
      <div className="flex h-full flex-col">
        <PageHeader title="今日工作台" subtitle="000403.SZ · 派林生物" />
        <div className="m-5 border-l-2 border-danger bg-danger/5 px-3 py-3 text-sm text-danger" role="alert">
          {dashboardErrorText(query.error)}
        </div>
      </div>
    )
  }

  const todayDaily = data.latest_daily?.report_date === data.requested_date ? data.latest_daily : null
  const todayDecision = data.decisions.slice().reverse().find(decision => decision.trade_date === data.requested_date)
  const signal = todayDaily?.research_signal ?? todayDecision?.research_signal
  const action = todayDaily?.paper_action ?? todayDecision?.paper_action
  const claimsAreCurrent = data.claims.trade_date === data.requested_date
  const claimsSummary = claimsAreCurrent
    ? `Claims ${data.claims.status} / ${data.claims.claim_count}`
    : 'Claims 待生成'
  const readiness = readinessCopy(data)
  const quantity = totalPositionQuantity(data)
  const sellable = sellableQuantity(data)
  const research = data.research?.status === 'READY' && data.research.trade_date === data.requested_date
    ? data.research : null
  const tradingDay = data.input_readiness.effective_trade_date === data.requested_date
    || data.latest_daily?.report_date === data.requested_date

  const runToday = () => {
    if (!canRun || runningRef.current) return
    runningRef.current = true
    setRunMessage(null)
    mutation.mutate(data.requested_date)
  }

  const actionSummary = !action
    ? '等待今日确定性决策'
    : action === 'HOLD'
    ? quantity === 0 ? '当前空仓，无需交易' : '当前持仓不变，无需交易'
    : todayDaily?.pending_action
      ? `${todayDaily.pending_action} 等待执行`
      : `${action ?? '--'} 已记录`
  const taskTitle = data.input_readiness.status === 'ALREADY_PUBLISHED'
    ? '今日决策已完成'
    : readiness.title

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <PageHeader
        title="今日工作台"
        subtitle={`${data.symbol} · ${data.name}`}
        className="flex-wrap px-3 sm:flex-nowrap sm:px-5"
        right={(
          <button
            type="button"
            onClick={runToday}
            disabled={!canRun || mutation.isPending}
            className="inline-flex h-9 w-full items-center justify-center gap-2 whitespace-nowrap rounded-btn bg-foreground px-3 text-xs font-semibold text-base transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
          >
            {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            运行今日模拟盘
          </button>
        )}
      />

      <div className="min-h-0 flex-1 overflow-y-auto pb-20 md:pb-0">
        <div className="mx-auto w-full max-w-[1400px] space-y-4 px-3 py-3 sm:px-5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-y border-danger/20 bg-danger/5 px-3 py-2 text-[11px]">
            <div className="flex items-center gap-2 font-semibold text-danger">
              <ShieldCheck className="h-4 w-4" />
              <span>SIMULATION ONLY</span>
              <span className="text-muted">/</span>
              <span>REAL TRADING DISABLED</span>
            </div>
            <span className="font-mono text-muted">今日范围 · {data.requested_date}</span>
          </div>

          {data.research?.status === 'READY' && data.research.engine && (
            <>
              <ResearchOverview engine={data.research.engine} requestedDate={data.requested_date} />
              <StrategyOverviewCards engine={data.research.engine} />
            </>
          )}

          <div className="flex justify-end">
            <Link to="/stock-research" className="inline-flex min-h-9 items-center gap-1 text-xs text-secondary">
              <TrendingUp className="h-4 w-4" />完整个股研究
            </Link>
          </div>
          <StrategyGraphics graphics={data.research?.status === 'READY' ? data.research.graphics : undefined}
            engine={data.research?.status === 'READY' ? data.research.engine : undefined} requestedDate={data.requested_date} />

          {(mutation.error || runMessage) && (
            <div
              role={mutation.error ? 'alert' : 'status'}
              className={cn(
                'border-l-2 px-3 py-2 text-xs',
                mutation.error ? 'border-danger bg-danger/5 text-danger' : 'border-bull bg-bull/5 text-bull',
              )}
            >
              {mutation.error instanceof Error ? mutation.error.message : runMessage}
            </div>
          )}

          <section aria-label="今日市场状态" className="grid border-y border-border sm:grid-cols-5">
            {[
              { icon: CalendarDays, label: '日期', value: data.requested_date },
              { icon: TrendingUp, label: '交易日状态', value: tradingDay ? 'A 股交易日' : '交易状态待确认' },
              { icon: Database, label: '数据状态', value: data.latest_daily?.report_date === data.requested_date ? '数据 READY' : '数据待准备' },
              { icon: Server, label: '服务状态', value: 'Backend Online' },
              { icon: ClipboardCheck, label: '今日输入状态', value: readiness.title },
            ].map(({ icon: Icon, label, value }, index) => (
              <div key={label} className={cn('min-w-0 px-3 py-3', index > 0 && 'border-t border-border sm:border-l sm:border-t-0')}>
                <div className="flex items-center gap-1.5 text-[10px] text-muted"><Icon className="h-3.5 w-3.5" />{label}</div>
                <div className="mt-1 truncate text-sm font-semibold">{value}</div>
              </div>
            ))}
          </section>

          <div className="grid gap-3 lg:grid-cols-6">
            <section aria-label="今日 Research Signal" className="order-1 border-y border-border px-4 py-4 lg:col-span-2 lg:order-1">
              <div className="text-[10px] uppercase text-muted">Research Signal</div>
              <div className="mt-2 text-xl font-semibold text-foreground">{signalLabel(signal)}</div>
              <div className={cn('mt-3 text-xs', claimsAreCurrent ? 'text-bull' : 'text-muted')}>
                {claimsSummary}
              </div>
            </section>

            <section aria-label="今日 Paper Action" className="order-2 border-y border-border px-4 py-4 lg:col-span-2 lg:order-2">
              <div className="text-[10px] uppercase text-muted">Paper Action</div>
              <div className={cn('mt-2 text-xl font-semibold', actionTone(action))}>
                {action === 'HOLD' ? quantity === 0 ? '空仓观望 · HOLD' : '持仓不变 · HOLD' : action ?? '--'}
              </div>
              <div className="mt-2 text-xs text-secondary">{actionSummary}</div>
              {research && action === 'HOLD' && <p className="mt-2 text-xs text-secondary">
                {research.paper_rule.signal === 'RISK_OBSERVATION' && quantity === 0
                  ? '指标偏弱，账户没有可卖持仓。'
                  : research.paper_rule.signal === 'MIXED_OBSERVATION'
                    ? 'MACD 与 RSI6 条件未同时同向满足。'
                    : '当前规则不产生新增模拟订单。'}
              </p>}
            </section>

            <section aria-label="今日待办" className="order-3 border-y border-border px-4 py-4 lg:col-span-3 lg:order-5">
              <div className="flex items-center gap-2 text-sm font-semibold"><ListTodo className="h-4 w-4" />今日待办</div>
              <div className="mt-3 text-sm font-medium">{taskTitle}</div>
              <div className="mt-1 text-xs text-secondary">{readiness.detail}</div>
              {action === 'HOLD' && <div className="mt-2 text-xs text-warning">无需交易，继续观察</div>}
              {todayDaily?.pending_action && (
                <div className="mt-2 text-xs text-warning">Pending {todayDaily.pending_action} · 等待既有执行条件</div>
              )}
            </section>

            <section aria-label="今日持仓" className="order-4 border-y border-border px-4 py-4 lg:col-span-3 lg:order-4">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-sm font-semibold"><WalletCards className="h-4 w-4" />今日持仓</div>
                <span className="text-[10px] text-muted">A 股 T+1</span>
              </div>
              {quantity === 0 ? (
                <div className="mt-5 text-sm text-secondary">当前空仓</div>
              ) : (
                <div className="mt-3 grid grid-cols-3 gap-3 text-xs">
                  <div><span className="text-muted">当前持仓</span><div className="mt-1 font-medium">{data.symbol}</div></div>
                  <div><span className="text-muted">持仓数量</span><div className="mt-1 font-mono">{quantity} 股</div></div>
                  <div><span className="text-muted">可卖数量</span><div className="mt-1 font-mono">{sellable} 股</div></div>
                </div>
              )}
              <div className="mt-3 text-xs text-muted">持仓市值 <span className="font-mono text-foreground">{money(data.account.market_value_cny)}</span></div>
            </section>

            <section aria-label="今日 PnL" className="order-5 border-y border-border px-4 py-4 lg:col-span-2 lg:order-3">
              <div className="text-[10px] uppercase text-muted">今日 PnL</div>
              <div className="mt-2 text-lg font-semibold">今日权益变化 {money(todayEquityChange(data))}</div>
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                <div><span className="text-muted">当日未实现</span><div className="mt-1 font-mono">{money(todayUnrealizedPnl(data))}</div></div>
                <div><span className="text-muted">当日已实现</span><div className="mt-1 font-mono">{money(todayRealizedPnl(data))}</div></div>
              </div>
              <div className="mt-3 text-xs text-muted">总资产 <span className="font-mono text-foreground">{money(data.account.total_equity_cny)}</span></div>
            </section>
          </div>

          {research && <section aria-label="本次模拟动作依据" className="border-y border-border px-4 py-4">
            <h2 className="text-sm font-semibold">本次模拟动作依据 · MACD + RSI6</h2>
            <p className="mb-3 mt-1 text-xs text-secondary">技术偏强条件：三项同时满足；空仓时才能产生模拟建仓决策。</p>
            <div className="grid gap-3 sm:grid-cols-3">
              {research.paper_rule.positive_conditions.map(condition => (
                <ConditionList key={condition.label} conditions={[condition]} />
              ))}
            </div>
          </section>}

          <ResearchPanel research={data.research} requestedDate={data.requested_date} />

          <section aria-label="ChenQuant Daily 摘要" className="border-y border-border px-4 py-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">ChenQuant Daily 摘要</h2>
              {todayDaily && <Link to="/review" className="text-xs font-medium text-accent hover:underline">查看完整日报</Link>}
            </div>
            {!todayDaily ? <EmptyState>今日复盘尚未生成</EmptyState> : (
              <div className="mt-3 space-y-3">
                <div className="grid gap-2 sm:grid-cols-3">
                  <div className="bg-elevated/40 px-3 py-2 text-xs"><span className="text-muted">今日 Signal</span><div className="mt-1 font-semibold">{signalLabel(signal)}</div></div>
                  <div className="bg-elevated/40 px-3 py-2 text-xs"><span className="text-muted">今日 Action</span><div className={cn('mt-1 font-semibold', actionTone(action))}>{action}</div></div>
                  <div className="bg-elevated/40 px-3 py-2 text-xs"><span className="text-muted">核心 Claims</span><div className={cn('mt-1 font-semibold', claimsAreCurrent ? 'text-bull' : 'text-muted')}>{claimsSummary}</div></div>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div><div className="text-[10px] uppercase text-muted">Risk Notes</div><ul className="mt-1 space-y-1 text-xs text-secondary">{todayDaily.risk_notes.map(note => <li key={note}>{signalLabel(note)}</li>)}</ul></div>
                  <div><div className="text-[10px] uppercase text-muted">Next Observation Condition</div><ul className="mt-1 space-y-1 text-xs text-secondary">{todayDaily.next_observation_conditions.map(item => <li key={item}>{signalLabel(item)}</li>)}</ul></div>
                </div>
              </div>
            )}
          </section>

          <section aria-label="最近 Signal Timeline" className="border-y border-border">
            <h2 className="px-4 py-3 text-sm font-semibold">最近 Signal Timeline</h2>
            {data.decisions.length === 0 ? <EmptyState>尚无决策记录</EmptyState> : (
              <div>
                {data.decisions.slice(-5).reverse().map(decision => (
                  <div key={`${decision.trade_date}-${String(decision.action_id)}`} className="grid grid-cols-[92px_1fr_auto] gap-3 border-t border-border px-4 py-2 text-xs">
                    <span className="font-mono text-muted">{decision.trade_date}</span>
                    <span>{signalLabel(decision.research_signal)}</span>
                    <span className={cn('font-semibold', actionTone(decision.paper_action))}>{decision.paper_action}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
