import type { PaperTradingDashboard } from '@/lib/api'

export function money(value: string | number) {
  const amount = Number(value)
  return Number.isFinite(amount)
    ? new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' }).format(amount)
    : '--'
}

export function percent(value: string | number) {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '--'
  return `${amount > 0 ? '+' : ''}${amount.toFixed(2)}%`
}

export function signalLabel(value: string | undefined) {
  return value ? value.replaceAll('_', ' ') : '--'
}

export function actionTone(action: string | undefined) {
  if (action === 'BUY') return 'text-bull'
  if (action === 'SELL') return 'text-bear'
  return 'text-warning'
}

export function dashboardErrorText(error: unknown) {
  const offline = 'TickFlow 后端未运行，当前为只读模式'
  if (error instanceof TypeError) return offline
  if (error instanceof Error && error.message === offline) return error.message
  return `模拟账户无法安全读取。${error instanceof Error ? ` ${error.message}` : ''}`
}

export function readinessCopy(data: PaperTradingDashboard) {
  const status = data.input_readiness.status
  if (status === 'WAITING_FOR_CLOSE') {
    return { title: '收盘后可运行', detail: '15:10 后准备今日研究输入' }
  }
  if (status === 'PREPARABLE') {
    return { title: '今日待运行', detail: '点击运行今日模拟盘' }
  }
  if (status === 'ALREADY_PUBLISHED') {
    return { title: '今日模拟盘已完成', detail: '再次运行不会产生重复交易' }
  }
  if (status === 'BLOCKED') {
    return { title: '今日运行暂不可用', detail: data.input_readiness.message }
  }
  if (status === 'MISSING') {
    return { title: '今日输入尚未准备', detail: '缺少已验证的日线研究输入' }
  }
  return { title: '今日输入已就绪', detail: '可运行今日模拟盘' }
}

function todayPointIndex(data: PaperTradingDashboard) {
  return data.equity_history.map(point => point.trade_date).lastIndexOf(data.requested_date)
}

function todayMetricChange(
  data: PaperTradingDashboard,
  key: 'total_equity_cny' | 'realized_pnl_cny' | 'unrealized_pnl_cny',
) {
  const points = data.equity_history
  const currentIndex = todayPointIndex(data)
  if (currentIndex < 0) return Number.NaN
  const current = Number(points[currentIndex][key])
  const previous = currentIndex > 0
    ? Number(points[currentIndex - 1][key])
    : key === 'total_equity_cny'
      ? Number(data.account.initial_cash_cny)
      : 0
  return current - previous
}

export function todayEquityChange(data: PaperTradingDashboard) {
  return todayMetricChange(data, 'total_equity_cny')
}

export function todayRealizedPnl(data: PaperTradingDashboard) {
  return todayMetricChange(data, 'realized_pnl_cny')
}

export function todayUnrealizedPnl(data: PaperTradingDashboard) {
  return todayMetricChange(data, 'unrealized_pnl_cny')
}

export function totalPositionQuantity(data: PaperTradingDashboard) {
  return data.positions.reduce((total, lot) => total + lot.remaining_quantity, 0)
}

export function sellableQuantity(data: PaperTradingDashboard) {
  const valuationDate = data.last_completed_trade_date ?? data.requested_date
  return data.positions.reduce(
    (total, lot) => total + (lot.sellable_from_trade_date <= valuationDate ? lot.remaining_quantity : 0),
    0,
  )
}

export function averageCost(cost: string, quantity: number) {
  return quantity > 0 ? Number(cost) / quantity : Number.NaN
}

export function impliedCurrentPrice(data: PaperTradingDashboard) {
  const quantity = totalPositionQuantity(data)
  return quantity > 0 ? Number(data.account.market_value_cny) / quantity : Number.NaN
}

export function executionState(decision: PaperTradingDashboard['decisions'][number]) {
  if (decision.pending_execution) return 'PENDING'
  if (decision.paper_action === 'HOLD') return 'NO TRADE'
  return 'COMPLETED'
}

export function identityLabel(value: unknown) {
  const identity = typeof value === 'string' ? value : ''
  return identity ? `${identity.slice(0, 12)}…` : '--'
}
