import { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Banknote,
  CheckCircle2,
  CircleDollarSign,
  FileCheck2,
  Loader2,
  Play,
  ShieldCheck,
  TrendingDown,
  WalletCards,
} from 'lucide-react'

import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import { PageHeader } from '@/components/PageHeader'
import { PaperEquityChart } from '@/components/paper-trading/PaperEquityChart'
import { api, type PaperTradingDashboard } from '@/lib/api'
import { cn } from '@/lib/cn'
import { BACKEND_OFFLINE_MESSAGE } from '@/lib/connectivity'
import { QK } from '@/lib/queryKeys'

function money(value: string) {
  const amount = Number(value)
  return Number.isFinite(amount)
    ? new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' }).format(amount)
    : '--'
}

function percent(value: string) {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '--'
  return `${amount > 0 ? '+' : ''}${amount.toFixed(2)}%`
}

function signalLabel(value: string | undefined) {
  return value ? value.replaceAll('_', ' ') : '--'
}

function actionTone(action: string | undefined) {
  if (action === 'BUY') return 'text-bull'
  if (action === 'SELL') return 'text-bear'
  return 'text-warning'
}

function dashboardErrorText(error: unknown) {
  if (error instanceof TypeError) return BACKEND_OFFLINE_MESSAGE
  if (error instanceof Error && error.message === BACKEND_OFFLINE_MESSAGE) return error.message
  return `模拟账户无法安全读取。${error instanceof Error ? ` ${error.message}` : ''}`
}

function readinessText(data: PaperTradingDashboard) {
  const readiness = data.input_readiness
  if (readiness.status === 'MISSING') return '当前日期缺少已验证的离线输入'
  if (readiness.status === 'WAITING_FOR_CLOSE') return 'A 股收盘后 15:10 起可准备今日输入'
  if (readiness.status === 'BLOCKED') return '当前日期不允许生成新的模拟盘输入'
  if (readiness.status === 'PREPARABLE') return '点击运行后将校验并准备今日单票日线输入'
  if (readiness.status === 'ALREADY_PUBLISHED') return '该数据日已完成，重复运行将进行幂等校验'
  if (!readiness.is_current_date) {
    return `首次可用冻结基准 ${readiness.effective_trade_date ?? '--'} 初始化账户`
  }
  return `已验证输入 ${readiness.effective_trade_date ?? '--'}`
}

function EmptyRow({ children }: { children: React.ReactNode }) {
  return <div className="py-8 text-center text-xs text-muted">{children}</div>
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
      setRunMessage(
        result.run_status === 'DAY_PUBLISHED'
          ? '本次已写入日结'
          : '该输入已完成，未重复写入',
      )
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
  const latestDecision = data?.decisions.at(-1)
  const signal = data?.latest_daily?.research_signal ?? latestDecision?.research_signal
  const action = data?.latest_daily?.paper_action ?? latestDecision?.paper_action

  const runToday = () => {
    if (!data || !canRun || runningRef.current) return
    runningRef.current = true
    setRunMessage(null)
    mutation.mutate(data.requested_date)
  }

  const kpis = useMemo(() => data ? [
    { label: '总资产', value: money(data.account.total_equity_cny), icon: CircleDollarSign },
    { label: '现金', value: money(data.account.cash_cny), icon: Banknote },
    { label: '持仓市值', value: money(data.account.market_value_cny), icon: WalletCards },
    { label: '累计收益', value: percent(data.account.cumulative_return_percent), icon: CheckCircle2 },
    { label: '最大回撤', value: money(data.account.max_drawdown_cny), icon: TrendingDown },
  ] : [], [data])

  if (query.isLoading) {
    return (
      <div className="grid h-full place-items-center text-xs text-muted">
        <span className="flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" />正在恢复模拟账户</span>
      </div>
    )
  }

  if (!data || query.error) {
    return (
      <div className="flex h-full flex-col">
        <PageHeader title="模拟投研工作台" subtitle="000403.SZ · 派林生物" />
        <div className="m-5 border-l-2 border-danger bg-danger/5 px-3 py-3 text-sm text-danger" role="alert">
          {dashboardErrorText(query.error)}
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <PageHeader
        title="模拟投研工作台"
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
        <div className="mx-auto w-full max-w-[1500px] space-y-4 px-3 py-3 sm:px-5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-y border-danger/20 bg-danger/5 px-3 py-2 text-[11px]">
            <div className="flex items-center gap-2 font-semibold text-danger">
              <ShieldCheck className="h-4 w-4" />
              <span>SIMULATION ONLY</span>
              <span className="text-muted">/</span>
              <span>REAL TRADING DISABLED</span>
            </div>
            <span className="font-mono text-muted">最新日结 {data.last_completed_trade_date ?? '尚未运行'}</span>
          </div>

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

          <section className="grid grid-cols-2 border-y border-border sm:grid-cols-3 lg:grid-cols-5" aria-label="账户概览">
            {kpis.map(({ label, value, icon: Icon }, index) => (
              <div key={label} className={cn(
                'min-w-0 px-3 py-3',
                index % 2 === 1 && 'border-l border-border',
                index % 3 === 0 ? 'sm:border-l-0' : 'sm:border-l sm:border-border',
                index > 0 ? 'lg:border-l lg:border-border' : 'lg:border-l-0',
              )}>
                <div className="flex items-center gap-1.5 text-[10px] text-muted"><Icon className="h-3.5 w-3.5" />{label}</div>
                <div className="mt-1 truncate font-mono text-base font-semibold text-foreground">{value}</div>
              </div>
            ))}
          </section>

          <section className="grid gap-3 lg:grid-cols-[1.2fr_0.8fr]">
            <div className="border-y border-border px-3 py-3">
              <div className="mb-3 flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold">研究决策状态</h2>
                <span className="text-[10px] text-muted">{data.latest_daily?.report_date ?? '--'}</span>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <div className="text-[10px] text-muted">Research Signal</div>
                  <div className="mt-1 text-xs font-semibold">{signalLabel(signal)}</div>
                </div>
                <div>
                  <div className="text-[10px] text-muted">Paper Action</div>
                  <div className={cn('mt-1 text-xs font-semibold', actionTone(action))}>{action ?? '--'}</div>
                </div>
                <div>
                  <div className="text-[10px] text-muted">Claims</div>
                  <div className="mt-1 text-xs font-semibold text-bull">
                    {data.claims.status} / {data.claims.claim_count} claims
                    {data.claims.trade_date ? ` · ${data.claims.trade_date}` : ''}
                  </div>
                </div>
              </div>
            </div>
            <div className="border-y border-border px-3 py-3">
              <div className="flex items-start gap-2">
                {canRun ? <FileCheck2 className="mt-0.5 h-4 w-4 shrink-0 text-bull" /> : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />}
                <div>
                  <h2 className="text-sm font-semibold">今日输入门禁</h2>
                  <p className="mt-1 text-xs text-secondary">{readinessText(data)}</p>
                  <p className="mt-1 font-mono text-[10px] text-muted">requested {data.requested_date}</p>
                </div>
              </div>
            </div>
          </section>

          <section className="border-y border-border" aria-labelledby="positions-title">
            <div className="flex items-center justify-between px-3 py-2">
              <h2 id="positions-title" className="text-sm font-semibold">持仓</h2>
              <span className="text-[10px] text-muted">A 股 T+1</span>
            </div>
            {data.positions.length === 0 ? <EmptyRow>当前无持仓</EmptyRow> : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[680px] text-left text-xs">
                  <thead className="border-y border-border bg-elevated/40 text-[10px] text-muted">
                    <tr><th className="px-3 py-2">标的</th><th>数量</th><th>成本</th><th>买入日</th><th>可卖日</th><th>状态</th></tr>
                  </thead>
                  <tbody>{data.positions.map(lot => (
                    <tr key={lot.lot_id} className="border-b border-border/60">
                      <td className="px-3 py-2 font-medium">{lot.symbol}</td>
                      <td>{lot.remaining_quantity} 股</td>
                      <td className="font-mono">{money(lot.remaining_cost_cny)}</td>
                      <td>{lot.acquired_trade_date}</td>
                      <td>{lot.sellable_from_trade_date}</td>
                      <td className="text-bull">T+1 可卖</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </section>

          <section className="grid gap-4 xl:grid-cols-2">
            <div className="border-y border-border">
              <h2 className="px-3 py-2 text-sm font-semibold">决策记录 / Signal Timeline</h2>
              {data.decisions.length === 0 ? <EmptyRow>尚无决策记录</EmptyRow> : (
                <div className="max-h-72 overflow-auto">
                  {data.decisions.slice().reverse().map(decision => (
                    <div key={`${decision.trade_date}-${String(decision.action_id)}`} className="grid grid-cols-[92px_1fr_auto] gap-3 border-t border-border px-3 py-2 text-xs">
                      <span className="font-mono text-muted">{decision.trade_date}</span>
                      <span>{signalLabel(decision.research_signal)}</span>
                      <span className={cn('font-semibold', actionTone(decision.paper_action))}>{decision.paper_action}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="border-y border-border">
              <h2 className="px-3 py-2 text-sm font-semibold">交易记录</h2>
              {data.trades.length === 0 ? <EmptyRow>当前无模拟成交</EmptyRow> : (
                <div className="max-h-72 overflow-auto">
                  {data.trades.slice().reverse().map(trade => (
                    <div key={trade.execution_id} className="grid grid-cols-[84px_48px_1fr_1fr] gap-2 border-t border-border px-3 py-2 text-xs">
                      <span className="font-mono text-muted">{trade.trade_date}</span>
                      <span className={actionTone(trade.side)}>{trade.side}</span>
                      <span>{trade.quantity} 股 @ {money(trade.execution_price)}</span>
                      <span className="text-right font-mono">PnL {money(trade.realized_pnl_cny)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>

          <section className="border-y border-border px-3 py-3">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-semibold">权益与 PnL</h2>
              <span className="text-[10px] text-muted">已实现 {money(data.account.realized_pnl_cny)} / 未实现 {money(data.account.unrealized_pnl_cny)}</span>
            </div>
            <PaperEquityChart points={data.equity_history} />
          </section>

          <section className="border-y border-border px-3 py-3">
            <h2 className="text-sm font-semibold">ChenQuant Daily</h2>
            {data.chenquant_daily_markdown ? (
              <div className="mt-2 max-w-4xl text-xs"><MarkdownRenderer content={data.chenquant_daily_markdown} /></div>
            ) : <EmptyRow>运行首个模拟盘日结后生成 Daily</EmptyRow>}
          </section>
        </div>
      </div>
    </div>
  )
}
