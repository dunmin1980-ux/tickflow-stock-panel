import { useQuery } from '@tanstack/react-query'
import { BookOpenCheck, BotOff, ShieldCheck } from 'lucide-react'

import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import { PageHeader } from '@/components/PageHeader'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

export function DailyReview() {
  const dashboard = useQuery({
    queryKey: QK.paperTrading,
    queryFn: () => api.paperTradingDashboard(),
    retry: false,
  })

  return (
    <>
      <PageHeader title="每日复盘" subtitle="确定性 Paper Trading 日报" />
      <div className="mx-auto w-full max-w-5xl space-y-4 px-3 py-4 sm:px-5 lg:px-8 lg:py-6">
        <section className="grid border-y border-border sm:grid-cols-3" aria-label="每日复盘能力状态">
          <Status icon={BookOpenCheck} label="ChenQuant Daily" value="READY" tone="text-bull" />
          <Status icon={BotOff} label="AI Provider" value="DEFERRED" tone="text-warning" />
          <Status icon={ShieldCheck} label="Real Trading" value="DISABLED" tone="text-danger" />
        </section>

        <div className="flex flex-wrap items-center justify-between gap-2 border-y border-border px-3 py-2">
          <h2 className="text-sm font-semibold text-foreground">ChenQuant Daily</h2>
          <span className="font-mono text-[10px] text-muted">
            {dashboard.data?.latest_daily?.report_date ?? '尚未生成'}
          </span>
        </div>

        {dashboard.isLoading ? (
          <div className="py-16 text-center text-xs text-muted">正在读取已发布日报</div>
        ) : dashboard.data?.chenquant_daily_markdown ? (
          <section className="min-w-0 overflow-hidden border-y border-border bg-surface/70 px-3 py-4 sm:px-5">
            <MarkdownRenderer content={dashboard.data.chenquant_daily_markdown} />
          </section>
        ) : (
          <div className="border-y border-border px-3 py-12 text-center text-xs text-muted">
            尚无已生成的 ChenQuant Daily。请先在今日工作台完成模拟盘日结。
          </div>
        )}

        <p className="text-[11px] text-muted">
          AI Provider DEFERRED。当前页面只读取确定性日报，不调用模型，不提供交易建议。
        </p>
      </div>
    </>
  )
}

function Status({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: typeof BookOpenCheck
  label: string
  value: string
  tone: string
}) {
  return (
    <div className="min-w-0 px-3 py-3 sm:border-l sm:first:border-l-0">
      <div className="flex items-center gap-1.5 text-[10px] text-muted"><Icon className="h-3.5 w-3.5" />{label}</div>
      <div className={`mt-1 font-mono text-sm font-semibold ${tone}`}>{value}</div>
    </div>
  )
}
