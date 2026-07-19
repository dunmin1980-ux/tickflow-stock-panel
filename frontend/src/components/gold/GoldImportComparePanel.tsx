import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  FileJson,
  GitCompareArrows,
  Loader2,
  Play,
  Upload,
} from 'lucide-react'
import {
  api,
  type GoldComparisonRun,
  type GoldComparisonSummary,
  type GoldImport,
} from '@/lib/api'
import { cn } from '@/lib/cn'
import { QK } from '@/lib/queryKeys'

interface GoldImportComparePanelProps {
  imports: GoldImport[]
  comparisons: GoldComparisonSummary[]
  initialMarketDate: string
  isLoading: boolean
}

interface ComparisonMetrics {
  availability: number
  passRates: Record<'price' | 'p' | 'v' | 'a' | 'state' | 'signals', number>
  missingShadow: number
}

const acceptedExtensions = new Set(['log', 'txt', 'json', 'jsonl'])
const rateLabels = [
  ['price', '价格'],
  ['p', 'P'],
  ['v', 'V'],
  ['a', 'A'],
  ['state', 'SJM'],
  ['signals', '候选信号'],
] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function finiteRate(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
}

function comparisonMetrics(run: GoldComparisonRun | undefined): ComparisonMetrics | null {
  if (!run || !Array.isArray(run.rows)) return null
  const summary = [...run.rows].reverse().find(row => isRecord(row) && row.type === 'summary')
  if (!summary) return null
  const passRates = summary.pass_rates
  const totals = summary.totals
  if (!isRecord(passRates) || !isRecord(totals)) return null
  const availability = summary.availability_rate
  const missingShadow = totals.missing_shadow
  if (!finiteRate(availability)
    || typeof missingShadow !== 'number'
    || !Number.isInteger(missingShadow)
    || missingShadow < 0
    || !rateLabels.every(([key]) => finiteRate(passRates[key]))) {
    return null
  }
  return {
    availability,
    passRates: {
      price: passRates.price as number,
      p: passRates.p as number,
      v: passRates.v as number,
      a: passRates.a as number,
      state: passRates.state as number,
      signals: passRates.signals as number,
    },
    missingShadow,
  }
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '请求失败'
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function shortId(value: string | null | undefined) {
  return value ? `${value.slice(0, 8)}…${value.slice(-4)}` : '—'
}

function formatRate(value: number | undefined) {
  return typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '—'
}

export function GoldImportComparePanel({
  imports,
  comparisons,
  initialMarketDate,
  isLoading,
}: GoldImportComparePanelProps) {
  const queryClient = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState('')
  const [selectedImportId, setSelectedImportId] = useState(imports[0]?.import_id ?? '')
  const [marketDate, setMarketDate] = useState(initialMarketDate)
  const [selectedRunId, setSelectedRunId] = useState('')

  useEffect(() => {
    if (!selectedImportId && imports[0]) setSelectedImportId(imports[0].import_id)
  }, [imports, selectedImportId])

  useEffect(() => {
    if (!marketDate && initialMarketDate) setMarketDate(initialMarketDate)
  }, [initialMarketDate, marketDate])

  useEffect(() => {
    if (!selectedRunId && comparisons[0]) {
      const preferred = comparisons.find(row => !comparisons.some(candidate => candidate.supersedes_run_id === row.run_id))
      setSelectedRunId((preferred ?? comparisons[0]).run_id)
    }
  }, [comparisons, selectedRunId])

  const supersededIds = useMemo(
    () => new Set(comparisons.flatMap(row => row.supersedes_run_id ? [row.supersedes_run_id] : [])),
    [comparisons],
  )
  const selectedComparison = comparisons.find(row => row.run_id === selectedRunId) ?? null

  const detailQuery = useQuery({
    queryKey: [...QK.goldComparisons, selectedRunId],
    queryFn: () => api.goldComparison(selectedRunId),
    enabled: Boolean(selectedRunId),
  })
  const metrics = comparisonMetrics(detailQuery.data)

  const importMutation = useMutation({
    mutationFn: api.goldImportLegacy,
    onSuccess: imported => {
      setSelectedImportId(imported.import_id)
      queryClient.invalidateQueries({ queryKey: QK.goldImports })
    },
  })

  const comparisonMutation = useMutation({
    mutationFn: api.goldRunComparison,
    onSuccess: comparison => {
      setSelectedRunId(comparison.run_id)
      queryClient.invalidateQueries({ queryKey: QK.goldComparisons })
      queryClient.invalidateQueries({ queryKey: QK.goldObservationGate })
    },
  })

  const handleFile = (candidate: File | null) => {
    importMutation.reset()
    if (!candidate) {
      setFile(null)
      setFileError('')
      return
    }
    const extension = candidate.name.split('.').pop()?.toLowerCase() ?? ''
    if (!acceptedExtensions.has(extension)) {
      setFile(null)
      setFileError('仅接受 .log、.txt、.json、.jsonl')
      return
    }
    setFile(candidate)
    setFileError('')
  }

  return (
    <section className="min-w-0 border-y border-border" aria-labelledby="gold-import-title">
      <div className="flex min-h-10 items-center gap-2 px-3 py-2">
        <GitCompareArrows className="h-4 w-4 text-accent" />
        <h2 id="gold-import-title" className="text-sm font-medium text-foreground">Legacy 导入与 Shadow 对账</h2>
      </div>

      <div className="grid min-w-0 border-t border-border xl:grid-cols-[0.85fr_1.15fr] xl:divide-x xl:divide-border">
        <div className="min-w-0 px-3 py-3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="min-w-0 flex-1">
              <div className="mb-1 text-[10px] text-muted">导入文件</div>
              <label className="flex h-8 cursor-pointer items-center justify-center gap-1.5 rounded-btn border border-border bg-elevated/40 px-3 text-xs text-secondary transition-colors hover:border-accent/40 hover:text-accent">
                <Upload className="h-3.5 w-3.5" />选择文件
                <input
                  type="file"
                  accept=".log,.txt,.json,.jsonl"
                  className="sr-only"
                  onChange={event => handleFile(event.target.files?.[0] ?? null)}
                />
              </label>
            </div>
            <button
              type="button"
              onClick={() => file && importMutation.mutate(file)}
              disabled={!file || importMutation.isPending}
              className="inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-btn bg-accent px-3 text-xs font-medium text-white transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
            >
              {importMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileJson className="h-3.5 w-3.5" />}
              解析并导入
            </button>
          </div>

          <div className="mt-3 min-h-[4.5rem] border-y border-border/70 py-2 text-[10px]">
            <div className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-2 gap-y-1">
              <span className="text-muted">文件名</span>
              <span className="truncate text-secondary" title={file?.name}>{file?.name ?? '未选择'}</span>
              <span className="text-muted">文件大小</span>
              <span className="font-mono text-secondary">{file ? formatBytes(file.size) : '—'}</span>
              <span className="text-muted">解析结果</span>
              <span className={cn('min-w-0 break-words', fileError || importMutation.isError ? 'text-danger' : 'text-secondary')}>
                {fileError
                  || (importMutation.isError ? errorMessage(importMutation.error) : '')
                  || (importMutation.data
                    ? `完成 · ${shortId(importMutation.data.import_id)} · ${importMutation.data.sample_count} 个样本`
                    : '待解析')}
              </span>
            </div>
          </div>

          <div className="mt-3">
            <div className="mb-1.5 flex items-center justify-between gap-3">
              <span className="text-[10px] font-medium text-secondary">导入批次</span>
              <span className="font-mono text-[10px] text-muted">{imports.length} 批</span>
            </div>
            <div className="max-h-40 overflow-auto border border-border">
              {imports.map(row => (
                <button
                  type="button"
                  key={row.import_id}
                  onClick={() => setSelectedImportId(row.import_id)}
                  className={cn(
                    'grid w-full grid-cols-[minmax(0,1fr)_5rem_5.5rem] items-center gap-2 border-b border-border/70 px-2 py-1.5 text-left text-[10px] last:border-b-0',
                    selectedImportId === row.import_id ? 'bg-accent/10 text-accent' : 'text-secondary hover:bg-elevated/40',
                  )}
                >
                  <span className="min-w-0 truncate" title={row.filename}>{row.filename}</span>
                  <span className="font-mono">{shortId(row.import_id)}</span>
                  <span className="text-right font-mono">{row.sample_count} 样本</span>
                </button>
              ))}
              {!isLoading && imports.length === 0 && <p className="px-2 py-6 text-center text-[10px] text-muted">暂无导入批次</p>}
              {isLoading && imports.length === 0 && <p className="px-2 py-6 text-center text-[10px] text-muted">正在读取导入批次</p>}
            </div>
          </div>
        </div>

        <div className="min-w-0 border-t border-border px-3 py-3 xl:border-t-0">
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_10rem_auto] sm:items-end">
            <label className="min-w-0 text-[10px] text-muted">
              导入批次
              <select
                value={selectedImportId}
                onChange={event => setSelectedImportId(event.target.value)}
                className="mt-1 h-8 w-full min-w-0 rounded-input border border-border bg-base px-2 font-mono text-xs text-secondary outline-none focus:border-accent/50"
              >
                <option value="">请选择</option>
                {imports.map(row => <option key={row.import_id} value={row.import_id}>{shortId(row.import_id)} · {row.sample_count}</option>)}
              </select>
            </label>
            <label className="text-[10px] text-muted">
              市场日期
              <input
                type="date"
                value={marketDate}
                onChange={event => setMarketDate(event.target.value)}
                className="mt-1 h-8 w-full rounded-input border border-border bg-base px-2 font-mono text-xs text-secondary outline-none focus:border-accent/50"
              />
            </label>
            <button
              type="button"
              onClick={() => comparisonMutation.mutate({ import_id: selectedImportId, market_date: marketDate })}
              disabled={!selectedImportId || !marketDate || comparisonMutation.isPending}
              className="inline-flex h-8 items-center justify-center gap-1.5 rounded-btn bg-accent px-3 text-xs font-medium text-white transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
            >
              {comparisonMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
              运行对账
            </button>
          </div>

          {comparisonMutation.isError && (
            <div className="mt-2 flex items-start gap-1.5 text-[10px] text-danger">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              <span className="break-words">{errorMessage(comparisonMutation.error)}</span>
            </div>
          )}

          <div className="mt-3 grid grid-cols-2 border border-border sm:grid-cols-4">
            <div className="border-b border-r border-border px-2 py-2 sm:border-b-0">
              <div className="text-[9px] text-muted">Availability</div>
              <div className="mt-0.5 font-mono text-sm font-medium text-foreground">{formatRate(metrics?.availability)}</div>
            </div>
            <div className="border-b border-border px-2 py-2 sm:border-b-0 sm:border-r">
              <div className="text-[9px] text-muted">Missing Shadow</div>
              <div className="mt-0.5 font-mono text-sm font-medium text-foreground">{metrics?.missingShadow ?? '—'}</div>
            </div>
            <div className="border-r border-border px-2 py-2">
              <div className="text-[9px] text-muted">Legacy / Shadow</div>
              <div className="mt-0.5 font-mono text-sm font-medium text-foreground">
                {selectedComparison ? `${selectedComparison.legacy_sample_count} / ${selectedComparison.shadow_sample_count}` : '—'}
              </div>
            </div>
            <div className="px-2 py-2">
              <div className="text-[9px] text-muted">运行状态</div>
              <div className={cn(
                'mt-0.5 text-xs font-medium',
                selectedComparison && supersededIds.has(selectedComparison.run_id) ? 'text-warning' : 'text-bear',
              )}>
                {selectedComparison
                  ? supersededIds.has(selectedComparison.run_id) ? '已被 supersede' : 'canonical'
                  : '—'}
              </div>
            </div>
          </div>

          <div className="mt-2 grid grid-cols-3 gap-px overflow-hidden border border-border bg-border sm:grid-cols-6">
            {rateLabels.map(([key, label]) => (
              <div key={key} className="min-w-0 bg-base px-2 py-2">
                <div className="truncate text-[9px] text-muted">{label}通过率</div>
                <div className="mt-0.5 font-mono text-xs font-medium text-secondary">{formatRate(metrics?.passRates[key])}</div>
              </div>
            ))}
          </div>

          <div className="mt-2 flex min-h-5 flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted">
            <span>run <span className="font-mono text-secondary">{shortId(selectedComparison?.run_id)}</span></span>
            <span>日期 <span className="font-mono text-secondary">{selectedComparison?.market_date ?? '—'}</span></span>
            <span>
              supersedes <span className="font-mono text-secondary">{shortId(selectedComparison?.supersedes_run_id)}</span>
            </span>
            {detailQuery.isFetching && <Loader2 className="h-3 w-3 animate-spin" />}
            {detailQuery.isError && <span className="text-danger">明细不可用：{errorMessage(detailQuery.error)}</span>}
          </div>

          <div className="mt-3 max-h-40 overflow-auto border border-border">
            {comparisons.map(row => {
              const isSuperseded = supersededIds.has(row.run_id)
              return (
                <button
                  type="button"
                  key={row.run_id}
                  onClick={() => setSelectedRunId(row.run_id)}
                  className={cn(
                    'grid w-full grid-cols-[5.5rem_minmax(0,1fr)_6rem] items-center gap-2 border-b border-border/70 px-2 py-1.5 text-left text-[10px] last:border-b-0',
                    selectedRunId === row.run_id ? 'bg-accent/10 text-accent' : 'text-secondary hover:bg-elevated/40',
                  )}
                >
                  <span className="font-mono">{row.market_date}</span>
                  <span className="min-w-0 truncate font-mono" title={row.run_id}>{shortId(row.run_id)}</span>
                  <span className={cn('text-right', isSuperseded ? 'text-warning' : 'text-bear')}>
                    {isSuperseded ? 'superseded' : 'canonical'}
                  </span>
                </button>
              )
            })}
            {!isLoading && comparisons.length === 0 && <p className="px-2 py-6 text-center text-[10px] text-muted">暂无对账运行</p>}
          </div>
          {selectedComparison?.supersedes_run_id && (
            <div className="mt-2 flex items-center gap-1.5 text-[10px] text-secondary">
              <CheckCircle2 className="h-3 w-3 text-bear" />当前 run 替代 {shortId(selectedComparison.supersedes_run_id)}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
