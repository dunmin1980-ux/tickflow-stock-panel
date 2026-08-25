import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  ArrowLeft,
  Banknote,
  CheckCircle2,
  CircleDollarSign,
  Loader2,
  TrendingDown,
  WalletCards,
} from 'lucide-react'

import { PageHeader } from '@/components/PageHeader'
import { PaperEquityChart } from '@/components/paper-trading/PaperEquityChart'
import {
  actionTone,
  averageCost,
  dashboardErrorText,
  executionState,
  identityLabel,
  impliedCurrentPrice,
  money,
  percent,
  sellableQuantity,
  signalLabel,
  totalPositionQuantity,
} from '@/components/paper-trading/presentation'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { QK } from '@/lib/queryKeys'

function EmptyState({ children }: { children: React.ReactNode }) {
  return <div className="py-8 text-center text-xs text-muted">{children}</div>
}

export function PaperAccount() {
  const query = useQuery({
    queryKey: QK.paperTrading,
    queryFn: () => api.paperTradingDashboard(),
    refetchOnWindowFocus: false,
    retry: false,
  })

  if (query.isLoading) {
    return (
      <div className="grid h-full place-items-center text-xs text-muted">
        <span className="flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" />正在恢复模拟账户</span>
      </div>
    )
  }

  const data = query.data
  if (!data || query.error) {
    return (
      <div className="flex h-full flex-col">
        <PageHeader title="模拟账户" subtitle="账户生命周期视图" />
        <div className="m-5 border-l-2 border-danger bg-danger/5 px-3 py-3 text-sm text-danger" role="alert">
          {dashboardErrorText(query.error)}
        </div>
      </div>
    )
  }

  const totalQuantity = totalPositionQuantity(data)
  const totalSellable = sellableQuantity(data)
  const currentPrice = impliedCurrentPrice(data)
  const kpis = [
    { label: '总资产', value: money(data.account.total_equity_cny), icon: CircleDollarSign },
    { label: '现金', value: money(data.account.cash_cny), icon: Banknote },
    { label: '持仓市值', value: money(data.account.market_value_cny), icon: WalletCards },
    { label: '累计收益', value: percent(data.account.cumulative_return_percent), icon: CheckCircle2 },
    { label: '最大回撤', value: money(data.account.max_drawdown_cny), icon: TrendingDown },
  ]

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <PageHeader
        title="模拟账户"
        subtitle={`${data.symbol} · ${data.name} · 账户生命周期`}
        className="flex-wrap px-3 sm:flex-nowrap sm:px-5"
        right={(
          <Link to="/paper-trading" className="inline-flex h-9 items-center gap-2 rounded-btn border border-border px-3 text-xs font-medium text-secondary hover:bg-elevated hover:text-foreground">
            <ArrowLeft className="h-4 w-4" />返回今日工作台
          </Link>
        )}
      />

      <div className="min-h-0 flex-1 overflow-y-auto pb-20 md:pb-0">
        <div className="mx-auto w-full max-w-[1500px] space-y-4 px-3 py-3 sm:px-5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-y border-danger/20 bg-danger/5 px-3 py-2 text-[11px]">
            <span className="font-semibold text-danger">SIMULATION ONLY / REAL TRADING DISABLED</span>
            <span className="font-mono text-muted">账户更新至 {data.last_completed_trade_date ?? '尚未运行'}</span>
          </div>

          <section aria-label="账户总览" className="grid grid-cols-2 border-y border-border sm:grid-cols-3 lg:grid-cols-5">
            {kpis.map(({ label, value, icon: Icon }, index) => (
              <div key={label} className={cn('min-w-0 px-3 py-3', index > 0 && 'border-l border-border')}>
                <div className="flex items-center gap-1.5 text-[10px] text-muted"><Icon className="h-3.5 w-3.5" />{label}</div>
                <div className="mt-1 truncate font-mono text-[15px] font-semibold text-foreground">{value}</div>
              </div>
            ))}
          </section>

          <section aria-label="当前持仓" className="border-y border-border">
            <div className="flex items-center justify-between px-3 py-2">
              <h2 className="text-sm font-semibold">当前持仓</h2>
              <span className="text-[10px] text-muted">A 股 T+1 · 可卖 {totalSellable} 股</span>
            </div>
            {data.positions.length === 0 ? (
              <EmptyState>当前空仓 · 可卖数量 0 股</EmptyState>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[980px] text-left text-xs">
                  <thead className="border-y border-border bg-elevated/40 text-[10px] text-muted">
                    <tr>
                      <th className="px-3 py-2">标的</th><th>持仓数量</th><th>可卖数量</th><th>成本价</th><th>当前价</th><th>持仓市值</th><th>未实现 PnL</th><th>收益率</th><th>买入日</th><th>可卖日</th>
                    </tr>
                  </thead>
                  <tbody>{data.positions.map(lot => {
                    const lotMarketValue = Number.isFinite(currentPrice) ? currentPrice * lot.remaining_quantity : Number.NaN
                    const lotPnl = lotMarketValue - Number(lot.remaining_cost_cny)
                    const lotReturn = Number(lot.remaining_cost_cny) > 0 ? (lotPnl / Number(lot.remaining_cost_cny)) * 100 : Number.NaN
                    const lotSellable = lot.sellable_from_trade_date <= (data.last_completed_trade_date ?? data.requested_date)
                      ? lot.remaining_quantity
                      : 0
                    return (
                      <tr key={lot.lot_id} className="border-b border-border/60">
                        <td className="px-3 py-2"><div className="font-medium">{lot.symbol}</div><div className="text-[10px] text-muted">{data.name}</div></td>
                        <td>{lot.remaining_quantity} 股</td>
                        <td>可卖 {lotSellable} 股</td>
                        <td className="font-mono">{money(averageCost(lot.remaining_cost_cny, lot.remaining_quantity))}</td>
                        <td className="font-mono">{money(currentPrice)}</td>
                        <td className="font-mono">{money(lotMarketValue)}</td>
                        <td className="font-mono">{money(lotPnl)}</td>
                        <td className="font-mono">{percent(lotReturn)}</td>
                        <td>{lot.acquired_trade_date}</td>
                        <td>{lot.sellable_from_trade_date}</td>
                      </tr>
                    )
                  })}</tbody>
                </table>
              </div>
            )}
            {totalQuantity > 0 && <div className="px-3 py-2 text-[10px] text-muted">合计持仓 {totalQuantity} 股</div>}
          </section>

          <section aria-label="账户 PnL" className="grid border-y border-border sm:grid-cols-4">
            {[
              ['累计收益', percent(data.account.cumulative_return_percent)],
              ['Realized PnL', money(data.account.realized_pnl_cny)],
              ['Unrealized PnL', money(data.account.unrealized_pnl_cny)],
              ['Max Drawdown', money(data.account.max_drawdown_cny)],
            ].map(([label, value], index) => (
              <div key={label} className={cn('px-3 py-3', index > 0 && 'border-t border-border sm:border-l sm:border-t-0')}>
                <div className="text-[10px] text-muted">{label}</div>
                <div className="mt-1 font-mono text-sm font-semibold">{value}</div>
              </div>
            ))}
          </section>

          <section className="border-y border-border px-3 py-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">权益曲线</h2>
              <span className="text-[10px] text-muted">Total Equity / Cash</span>
            </div>
            <PaperEquityChart points={data.equity_history} />
          </section>

          <section aria-label="历史交易" className="border-y border-border">
            <h2 className="px-3 py-2 text-sm font-semibold">历史交易</h2>
            {data.trades.length === 0 ? <EmptyState>暂无模拟成交</EmptyState> : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-left text-xs">
                  <thead className="border-y border-border bg-elevated/40 text-[10px] text-muted">
                    <tr><th className="px-3 py-2">Date</th><th>Symbol</th><th>Side</th><th>Quantity</th><th>Execution Price</th><th>Fees</th><th>Realized PnL</th><th>Status</th></tr>
                  </thead>
                  <tbody>{data.trades.slice().reverse().map(trade => (
                    <tr key={trade.execution_id} className="border-b border-border/60">
                      <td className="px-3 py-2 font-mono">{trade.trade_date}</td>
                      <td>{String(trade.symbol ?? data.symbol)}</td>
                      <td className={actionTone(trade.side)}>{trade.side}</td>
                      <td>{trade.quantity} 股</td>
                      <td className="font-mono">{money(trade.execution_price)}</td>
                      <td className="font-mono">{money(trade.fees_cny)}</td>
                      <td className="font-mono">{money(trade.realized_pnl_cny)}</td>
                      <td>{String(trade.status ?? 'EXECUTED')}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </section>

          <section aria-label="历史决策" className="border-y border-border">
            <h2 className="px-3 py-2 text-sm font-semibold">历史决策</h2>
            {data.decisions.length === 0 ? <EmptyState>尚无决策记录</EmptyState> : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[700px] text-left text-xs">
                  <thead className="border-y border-border bg-elevated/40 text-[10px] text-muted">
                    <tr><th className="px-3 py-2">Decision Date</th><th>Signal</th><th>Action</th><th>Claims Identity</th><th>Execution State</th></tr>
                  </thead>
                  <tbody>{data.decisions.slice().reverse().map(decision => (
                    <tr key={`${decision.trade_date}-${String(decision.action_id)}`} className="border-b border-border/60">
                      <td className="px-3 py-2 font-mono">{decision.trade_date}</td>
                      <td>{signalLabel(decision.research_signal)}</td>
                      <td className={cn('font-semibold', actionTone(decision.paper_action))}>{decision.paper_action}</td>
                      <td className="font-mono" title={String(decision.claims_sha256 ?? '')}>{identityLabel(decision.claims_sha256)}</td>
                      <td>{executionState(decision)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
