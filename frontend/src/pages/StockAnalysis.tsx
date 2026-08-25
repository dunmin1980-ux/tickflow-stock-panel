import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { LineChart, History as HistoryIcon, Loader2, ExternalLink, Bell, AlertTriangle, BotOff, X } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'
import { StockFinancialSearch } from '@/components/financials/StockFinancialSearch'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import { LastStockChip } from '@/components/LastStockChip'
import { AnalysisKChart, type PriceLevel, type LevelType } from '@/components/stock-analysis/AnalysisKChart'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import { api, type AiStockReport } from '@/lib/api'
import { useLastStock } from '@/lib/useLastStock'
import { QK } from '@/lib/queryKeys'
import { toast } from '@/components/Toast'
import {
  useHistoryReports,
  deleteReport, loadHistory,
} from '@/lib/stockAnalysisStore'
import { WORKSPACE_RESOURCE_EVENT, useWorkspaceStatus } from '@/lib/useWorkspaceEvents'

/**
 * 个股研究页 —— 日 K、关键价位和已保存研究报告。
 * Visual v1 不调用真实 AI Provider。
 */
export function StockAnalysis() {
  const [symbol, setSymbol] = useState<string>('')
  const [name, setName] = useState<string>('')
  const [previewSymbol, setPreviewSymbol] = useState<string | null>(null)
  const [historyReport, setHistoryReport] = useState<AiStockReport | null>(null)
  const [historyLoadingId, setHistoryLoadingId] = useState<string | null>(null)
  const { last: lastStock, remember: rememberStock } = useLastStock('stock-analysis')
  const workspaceStatus = useWorkspaceStatus()
  const mutationsDisabled = workspaceStatus.offlineReadonly

  // 进入页面立即加载历史报告(供右侧常驻列表)。store 内部有 historyLoaded 去重, 重复调用安全。
  useEffect(() => {
    void loadHistory()
    const reload = (event: Event) => {
      const resource = (event as CustomEvent<{ resource?: string }>).detail?.resource
      if (resource === 'stock_reports') void loadHistory()
    }
    window.addEventListener(WORKSPACE_RESOURCE_EVENT, reload)
    return () => window.removeEventListener(WORKSPACE_RESOURCE_EVENT, reload)
  }, [])

  // 自动恢复上次选中的股票(切走再回来不丢)。useLastStock 的 last 来自 localStorage, 同步可用。
  useEffect(() => {
    if (!symbol && lastStock) {
      setSymbol(lastStock.symbol)
      setName(lastStock.name)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const onSelect = (sym: string, nm: string) => {
    setSymbol(sym)
    setName(nm)
    rememberStock(sym, nm)
  }

  const openSavedReport = async (reportId: string) => {
    if (historyLoadingId) return
    setHistoryLoadingId(reportId)
    try {
      const response = await api.stockAnalysisReportGet(reportId)
      setHistoryReport(response.report)
    } catch {
      toast('历史研究报告读取失败', 'error')
    } finally {
      setHistoryLoadingId(null)
    }
  }

  return (
    <>
      <PageHeader
        className="flex-wrap sm:flex-nowrap"
        title="个股研究"
        titleExtra={(
          <span className="inline-flex items-center gap-1 border border-warning/30 bg-warning/5 px-1.5 py-0.5 font-mono text-[9px] font-semibold text-warning">
            <BotOff className="h-3 w-3" />AI Provider DEFERRED
          </span>
        )}
        subtitle="日 K · 关键价位 · 已保存研究报告"
        right={
          <div className="flex items-center gap-2">
            <LastStockChip stock={lastStock} onSelect={onSelect} />
          </div>
        }
      />

      <div className="w-full min-w-0 space-y-6 px-3 py-4 sm:px-5 lg:px-8 lg:py-6">
        {/* 搜索栏 */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="w-full sm:w-72">
            <StockFinancialSearch onSelect={onSelect} />
          </div>
          {symbol && (
            <>
              <button
                onClick={() => setPreviewSymbol(symbol)}
                title="查看个股日 K 详情"
                className="group flex items-center gap-2 text-sm rounded-md px-1.5 py-0.5 -mx-1.5 hover:bg-elevated transition-colors"
              >
                <span className="text-foreground font-medium group-hover:text-sky-300 transition-colors">{name || symbol}</span>
                <span className="text-[10px] font-mono text-muted">{symbol}</span>
                <ExternalLink className="h-3 w-3 text-muted opacity-0 group-hover:opacity-100 transition-opacity" />
              </button>
              <button
                onClick={() => toast('点位提醒功能开发中,敬请期待', 'error')}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-btn border border-border/40 bg-elevated/40 text-muted text-xs font-medium hover:border-border/70 hover:text-secondary transition-all"
                title="当价格触及关键价位时提醒(开发中)"
              >
                <Bell className="h-3.5 w-3.5" />
                点位提醒
                <span className="rounded-full bg-amber-400/15 px-1.5 py-px text-[9px] font-semibold uppercase tracking-wider text-amber-400">
                  开发中
                </span>
              </button>
            </>
          )}
        </div>

        {/* 主体:左侧当前个股看板 + 右侧常驻历史报告 */}
        <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_288px]">
          <div className="min-w-0">
            {!symbol ? (
              <EmptyState
                icon={LineChart}
                title="选择一只股票开始分析"
                hint="搜索代码或名称，查看日 K、关键价位与既有研究记录。"
              />
            ) : (
              <StockAnalysisBoard symbol={symbol} />
            )}
          </div>
          <HistorySidebar
            mutationsDisabled={mutationsDisabled}
            loadingId={historyLoadingId}
            onOpen={openSavedReport}
          />
        </div>
      </div>

      {/* 个股日 K 详情对话框(点击名称/代码打开) */}
      {previewSymbol && (
        <StockPreviewDialog
          symbol={previewSymbol}
          name={previewSymbol === symbol ? name : undefined}
          triggerInfo={null}
          onClose={() => setPreviewSymbol(null)}
        />
      )}
      <HistoryReportDialog report={historyReport} onClose={() => setHistoryReport(null)} />
    </>
  )
}

// ===== 分析看板:日 K + 关键价位 =====
function StockAnalysisBoard({ symbol }: { symbol: string }) {
  const kline = useQuery({
    queryKey: ['kline', symbol, ''],
    queryFn: () => api.klineDaily(symbol, 250),
    enabled: !!symbol,
    staleTime: 60_000,
  })

  const levelsQ = useQuery({
    queryKey: QK.stockLevels(symbol),
    queryFn: () => api.stockAnalysisLevels(symbol, 250),
    enabled: !!symbol,
    staleTime: 60_000,
  })

  if (kline.isLoading) {
    return <div className="flex items-center justify-center py-20"><Loader2 className="h-5 w-5 animate-spin text-muted" /></div>
  }

  if (kline.isError) {
    return (
      <EmptyState
        icon={AlertTriangle}
        title="日 K 数据加载失败"
        hint="请检查网络或数据源配置后重试。"
      />
    )
  }

  const rows = kline.data?.rows ?? []
  if (rows.length === 0) {
    return <EmptyState icon={LineChart} title="暂无日 K 数据" hint="该标的尚未同步日 K,请先在数据页或自选页同步。" />
  }

  const levels = (levelsQ.data?.levels ?? {}) as Record<LevelType, PriceLevel[]>

  // 涨跌色:最后一根 K 线收 vs 前一根收(无前日则按开收判断)
  const last = rows[rows.length - 1]
  const prev = rows[rows.length - 2]
  const curClose = levelsQ.data?.close
  const isUp = prev ? (last.close >= prev.close) : (last.close >= last.open)

  return (
    <div className="rounded-card border border-border/60 bg-surface/40 overflow-hidden">
      <div className="px-4 py-3 border-b border-border/40">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <LineChart className="h-4 w-4 text-sky-400 shrink-0" />
            <span className="text-sm font-medium text-foreground">关键价位分析</span>
          </div>
          <div className="flex items-baseline gap-2 shrink-0">
            <span className="text-[10px] text-muted">{rows.length} 个交易日</span>
            <span className="text-[10px] text-muted/60">·</span>
            <span className="text-[10px] text-muted">当前价</span>
            <span className={`text-base font-mono font-bold ${isUp ? 'text-bull' : 'text-bear'}`}>
              {curClose?.toFixed(2) ?? '—'}
            </span>
          </div>
        </div>
      </div>
      <div className="p-3">
        <AnalysisKChart
          rows={rows}
          levels={levels}
          series={levelsQ.data?.series}
          seriesDates={levelsQ.data?.dates}
          defaultLevelTypes={['sr', 'pivot', 'keltner_s']}
          height={480}
        />
      </div>
    </div>
  )
}

// ===== 左侧常驻:历史报告侧栏(所有股票,按时间倒序平铺) =====
function HistorySidebar({
  mutationsDisabled,
  loadingId,
  onOpen,
}: {
  mutationsDisabled: boolean
  loadingId: string | null
  onOpen: (reportId: string) => void
}) {
  const { reports, loaded } = useHistoryReports()

  return (
    <aside className="self-start xl:sticky xl:top-0">
      <div className="rounded-card border border-border/60 bg-surface/40 overflow-hidden">
        <div className="px-3 py-2.5 border-b border-border/40 flex items-center gap-2">
          <HistoryIcon className="h-3.5 w-3.5 text-sky-400 shrink-0" />
          <span className="text-xs font-medium text-foreground">历史报告</span>
          {loaded && reports.length > 0 && (
            <span className="ml-auto text-[10px] text-muted">{reports.length}</span>
          )}
        </div>

        {!loaded ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-4 w-4 animate-spin text-muted" />
          </div>
        ) : reports.length === 0 ? (
          <div className="px-3 py-10 text-center">
            <p className="text-xs text-muted">还没有任何个股分析报告</p>
            <p className="text-[10px] text-muted/60 mt-1">AI Provider 已延后；当前只展示确定性行情与既有研究记录。</p>
          </div>
        ) : (
          <div className="max-h-[calc(100vh-220px)] overflow-y-auto p-2 space-y-1.5">
            {reports.map(r => (
              <div
                key={r.id}
                className="group rounded-lg border border-border/40 bg-elevated/20 p-2.5 hover:border-border hover:bg-elevated/40 transition-colors"
              >
                <div className="flex items-center justify-between gap-2">
                  <button
                    onClick={() => onOpen(r.id)}
                    disabled={loadingId !== null}
                    className="flex-1 text-left min-w-0"
                  >
                    <div className="flex items-center gap-1.5 min-w-0">
                      {loadingId === r.id && <Loader2 className="h-3 w-3 shrink-0 animate-spin text-muted" />}
                      <span className="text-xs font-medium text-foreground truncate">{r.name || r.symbol}</span>
                      <span className="text-[10px] font-mono text-muted shrink-0">{r.symbol}</span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-2 text-[10px] text-muted">
                      <span>{fmtRelative(r.created_at)}</span>
                      {r.close != null && <span className="font-mono">价 {r.close.toFixed(2)}</span>}
                      {r.focus && <span className="text-sky-300/70 truncate">关注: {r.focus}</span>}
                    </div>
                    {r.summary && (
                      <div className="mt-1 text-[11px] text-muted truncate">{r.summary}</div>
                    )}
                  </button>
                  <button
                    onClick={() => { deleteReport(r.id); toast('已删除', 'success') }}
                    disabled={mutationsDisabled}
                    aria-disabled={mutationsDisabled}
                    className="shrink-0 text-[10px] text-muted/60 hover:text-danger transition-colors px-1 py-0.5 opacity-0 group-hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-40"
                    title={mutationsDisabled ? '离线只读，暂不能删除报告' : '删除'}
                  >
                    删除
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  )
}

function HistoryReportDialog({ report, onClose }: { report: AiStockReport | null; onClose: () => void }) {
  if (!report) return null
  return (
    <div
      role="dialog"
      aria-label="历史研究报告"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-3 backdrop-blur-sm sm:p-5"
      onClick={event => { if (event.target === event.currentTarget) onClose() }}
    >
      <section className="flex max-h-[90dvh] w-full max-w-3xl flex-col overflow-hidden rounded-card border border-border bg-surface shadow-2xl">
        <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-foreground">历史研究报告</h2>
            <p className="mt-1 truncate text-xs text-muted">
              {report.name || report.symbol} · {report.symbol} · {report.data_as_of ?? report.created_at.slice(0, 10)}
            </p>
          </div>
          <button
            type="button"
            aria-label="关闭历史研究报告"
            onClick={onClose}
            className="rounded-btn p-2 text-muted transition-colors hover:bg-elevated hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5">
          <MarkdownRenderer content={report.content} />
        </div>
        <footer className="border-t border-border px-4 py-2 font-mono text-[10px] text-muted">
          HISTORICAL MATERIAL · can_publish=false · AI Provider DEFERRED
        </footer>
      </section>
    </div>
  )
}

function fmtRelative(iso: string): string {
  try {
    const t = new Date(iso).getTime()
    const diff = Date.now() - t
    if (diff < 60_000) return '刚刚'
    if (diff < 3600_000) return `${Math.floor(diff / 60_000)} 分钟前`
    if (diff < 86400_000) return `${Math.floor(diff / 3600_000)} 小时前`
    if (diff < 7 * 86400_000) return `${Math.floor(diff / 86400_000)} 天前`
    return new Date(iso).toLocaleDateString('zh-CN')
  } catch { return iso }
}
