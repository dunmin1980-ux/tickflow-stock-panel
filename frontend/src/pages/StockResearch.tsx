import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, Loader2, ShieldCheck } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { ResearchOverview } from '@/components/paper-trading/ResearchEngineView'
import { ResearchPanel } from '@/components/paper-trading/ResearchPanel'
import { StrategyGraphics } from '@/components/paper-trading/StrategyGraphics'
import { StrategyOverviewCards } from '@/components/paper-trading/StrategyOverviewCards'
import { dashboardErrorText } from '@/components/paper-trading/presentation'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

export function StockResearch() {
  const query = useQuery({
    queryKey: QK.paperTrading,
    queryFn: () => api.paperTradingDashboard(),
    refetchOnWindowFocus: false,
    retry: false,
  })
  const data = query.data
  const research = data?.research?.status === 'READY' ? data.research : undefined
  return <div className="flex h-full min-h-0 flex-col overflow-hidden">
    <PageHeader title="个股研究" subtitle="000403.SZ · 派林生物" className="flex-wrap px-3 sm:px-5"
      right={<Link to="/paper-trading" className="inline-flex min-h-9 items-center gap-1 text-xs text-secondary">
        <ArrowLeft className="h-4 w-4" />今日工作台
      </Link>} />
    <div className="min-h-0 flex-1 overflow-y-auto pb-20 md:pb-0">
      <div className="mx-auto w-full max-w-[1400px] space-y-4 px-3 py-3 sm:px-5">
        <div className="flex flex-wrap items-center gap-2 border-y border-danger/20 bg-danger/5 px-3 py-2 text-[11px] font-semibold text-danger">
          <ShieldCheck className="h-4 w-4" />SIMULATION ONLY / REAL TRADING DISABLED
        </div>
        {query.isLoading ? <p role="status" className="flex items-center gap-2 py-8 text-xs text-muted">
          <Loader2 className="h-4 w-4 animate-spin" />正在读取个股研究
        </p> : !data || query.error ? <p role="alert" className="border-l-2 border-danger bg-danger/5 px-3 py-3 text-sm text-danger">
          {dashboardErrorText(query.error)}
        </p> : <>
          {research?.engine && <>
            <ResearchOverview engine={research.engine} requestedDate={data.requested_date} />
            <StrategyOverviewCards engine={research.engine} />
          </>}
          <StrategyGraphics graphics={research?.graphics} engine={research?.engine} requestedDate={data.requested_date} />
          <ResearchPanel research={data.research} requestedDate={data.requested_date} />
        </>}
      </div>
    </div>
  </div>
}
