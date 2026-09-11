import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { PaperTradingDashboard } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

const shanghaiDate = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
})

function systemDate() {
  return shanghaiDate.format(new Date())
}

function DateValue({ label, value, title }: { label: string; value?: string | null; title: string }) {
  const display = value || '不可用'
  return (
    <span aria-label={`${label} ${display}`} title={title} className="inline-flex flex-wrap items-baseline gap-x-1">
      <span>{label}</span>{' '}
      <span className="font-mono tabular-nums">{display}</span>
    </span>
  )
}

export function WorkbenchDates({ enrichedCacheDate }: { enrichedCacheDate: string | null }) {
  const queryClient = useQueryClient()
  // Observe only: the workbench pages own the dashboard's existing request workflow.
  const subscribe = useCallback((onChange: () => void) => queryClient.getQueryCache().subscribe(onChange), [queryClient])
  const snapshot = useCallback(() => queryClient.getQueryData<PaperTradingDashboard>(QK.paperTrading), [queryClient])
  const dashboard = useSyncExternalStore(subscribe, snapshot, snapshot)
  const [today, setToday] = useState(systemDate)

  useEffect(() => {
    const refresh = () => setToday(systemDate())
    const timer = window.setInterval(refresh, 60_000)
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [])

  const researchDate = dashboard?.research?.status === 'READY' ? dashboard.research.trade_date : null
  const readiness = dashboard?.input_readiness
  const validatedInputDate = readiness?.source_fixture === 'VALIDATED_DAILY_INPUT'
    && (readiness.status === 'READY_VALIDATED_DAILY' || readiness.status === 'ALREADY_PUBLISHED')
    ? readiness.effective_trade_date : null
  // READY research is built from a validated raw/QFQ bundle for its trade_date.
  const marketDate = [validatedInputDate, researchDate].filter((value): value is string => !!value).sort().at(-1)

  return (
    <div aria-label="工作台日期" className="flex w-full min-w-0 flex-wrap items-baseline gap-x-4 gap-y-1 text-[11px] text-muted">
      <DateValue label="系统日期" value={today} title="Asia/Shanghai" />
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-secondary">000403.SZ 工作台缓存</span>
        <DateValue label="最新行情" value={marketDate} title="已缓存的已验证行情输入或 READY 研究输入日期；不代表全市场实时行情" />
        <DateValue label="最新研究" value={researchDate} title="已缓存的 READY 研究面板日期；不代表历史真实已发布研究记录" />
        <DateValue label="最新模拟盘" value={dashboard?.last_completed_trade_date} title="模拟盘账本最后完成的交易日期；不是最近成交日期" />
      </div>
      <DateValue label="股票日线指标缓存日期" value={enrichedCacheDate}
        title="workspace.data_as_of：get_enriched_latest() 返回的股票日线 enriched 缓存日期" />
    </div>
  )
}
