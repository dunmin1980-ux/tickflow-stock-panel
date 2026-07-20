import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  Loader2,
  RadioTower,
} from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { GoldImportComparePanel } from '@/components/gold/GoldImportComparePanel'
import { GoldObservationPanel } from '@/components/gold/GoldObservationPanel'
import { GoldSnapshotPanel } from '@/components/gold/GoldSnapshotPanel'
import {
  api,
  type GoldCandidate,
  type GoldCandidateSignal,
  type GoldImport,
  type GoldShadowSnapshot,
} from '@/lib/api'
import {
  normalizeGoldComparisonRows,
  normalizeGoldGate,
  normalizeGoldStatus,
} from '@/lib/gold'
import { QK } from '@/lib/queryKeys'

const candidateSignals = new Set<GoldCandidateSignal>(['恐慌极端', '惯性衰竭', '均值回归', '贪婪态'])
const emptyHealth = {
  latest_success_at: null,
  latest_market_date: null,
  consecutive_failures: 0,
  last_error: null,
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function rawRows(value: unknown): unknown[] {
  if (typeof value !== 'object' || value === null || !('rows' in value)) return []
  const rows = (value as { rows?: unknown }).rows
  return Array.isArray(rows) ? rows : []
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

function snapshotRowsFrom(value: unknown): GoldShadowSnapshot[] {
  return rawRows(value).flatMap(row => {
    const normalized = normalizeGoldStatus({
      enabled: true,
      symbol: '600489.SH',
      latest: row,
      latest_market_date: null,
      health: emptyHealth,
      external_send_count: 0,
    })
    return normalized?.latest ? [normalized.latest] : []
  })
}

function candidateRowsFrom(value: unknown): GoldCandidate[] {
  return rawRows(value).filter((row): row is GoldCandidate => isRecord(row)
    && typeof row.key === 'string'
    && typeof row.signal === 'string'
    && candidateSignals.has(row.signal as GoldCandidateSignal)
    && row.symbol === '600489.SH'
    && typeof row.market_date === 'string')
}

function importRowsFrom(value: unknown): GoldImport[] {
  return rawRows(value).filter((row): row is GoldImport => isRecord(row)
    && typeof row.import_id === 'string'
    && typeof row.sha256 === 'string'
    && (row.normalized_sha256 === undefined || typeof row.normalized_sha256 === 'string')
    && typeof row.filename === 'string'
    && typeof row.imported_at === 'string'
    && isNonNegativeInteger(row.sample_count))
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '请求失败'
}

function beijingDate() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date())
}

export function GoldWorkspace() {
  const statusQuery = useQuery({
    queryKey: QK.goldStatus,
    queryFn: api.goldStatus,
    refetchInterval: 30_000,
  })
  const snapshotsQuery = useQuery({
    queryKey: QK.goldSnapshots,
    queryFn: () => api.goldSnapshots(100),
    refetchInterval: 30_000,
  })
  const candidatesQuery = useQuery({
    queryKey: QK.goldCandidates,
    queryFn: () => api.goldCandidates(100),
    refetchInterval: 30_000,
  })
  const observationQuery = useQuery({
    queryKey: QK.goldObservationGate,
    queryFn: api.goldObservationGate,
    refetchInterval: 60_000,
  })
  const importsQuery = useQuery({
    queryKey: QK.goldImports,
    queryFn: () => api.goldImports(100),
  })
  const comparisonsQuery = useQuery({
    queryKey: QK.goldComparisons,
    queryFn: () => api.goldComparisons(100),
  })

  const status = normalizeGoldStatus(statusQuery.data)
  const gate = normalizeGoldGate(observationQuery.data)
  const snapshots = snapshotRowsFrom(snapshotsQuery.data)
  const candidates = candidateRowsFrom(candidatesQuery.data)
  const imports = importRowsFrom(importsQuery.data)
  const comparisons = normalizeGoldComparisonRows(comparisonsQuery.data)
  const initialMarketDate = status?.latest_market_date
    ?? status?.latest?.market_date
    ?? comparisons[0]?.market_date
    ?? beijingDate()

  const queryErrors = [
    ['状态', statusQuery.error],
    ['快照', snapshotsQuery.error],
    ['候选', candidatesQuery.error],
    ['观察门槛', observationQuery.error],
    ['导入批次', importsQuery.error],
    ['对账运行', comparisonsQuery.error],
  ].filter((entry): entry is [string, Error] => entry[1] instanceof Error)

  const invalidStatus = statusQuery.data !== undefined && status === null
  const invalidGate = observationQuery.data !== undefined && gate === null

  if (statusQuery.isLoading && !status) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <PageHeader title="中金黄金" subtitle="Gold 研究工作区" />
        <div className="flex flex-1 items-center justify-center gap-2 text-xs text-muted">
          <Loader2 className="h-4 w-4 animate-spin" />正在读取 Gold 状态
        </div>
        <footer className="border-t border-border px-5 py-2 text-center text-[10px] text-muted">仅供研究，不构成投资建议</footer>
      </div>
    )
  }

  if (status?.enabled === false) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <PageHeader
          title="中金黄金"
          subtitle="Gold 研究工作区"
        />
        <div className="flex-1 px-5 py-4">
          <div className="flex max-w-2xl items-start gap-2 border-l-2 border-border pl-3 text-xs text-secondary">
            <RadioTower className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
            <div>
              <p className="font-medium text-foreground">Gold 研究工作区未启用</p>
              <p className="mt-1 text-muted">当前配置为 GOLD_WORKSPACE_ENABLED=false。</p>
            </div>
          </div>
        </div>
        <footer className="border-t border-border px-5 py-2 text-center text-[10px] text-muted">仅供研究，不构成投资建议</footer>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <PageHeader
        title="中金黄金"
        subtitle="600489.SH · 研究工作区"
      />

      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[1500px] space-y-3 px-3 py-3 sm:px-5">
          {(queryErrors.length > 0 || invalidStatus || invalidGate) && (
            <div className="border-y border-danger/30 bg-danger/5 px-3 py-2 text-[10px] text-danger">
              <div className="flex items-start gap-1.5">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <div className="min-w-0 space-y-1">
                  {invalidStatus && <p>状态响应格式异常，已安全忽略。</p>}
                  {invalidGate && <p>观察门槛响应格式异常，已安全忽略。</p>}
                  {queryErrors.map(([label, error]) => (
                    <p key={label} className="break-words">{label}：{errorMessage(error)}</p>
                  ))}
                </div>
              </div>
            </div>
          )}

          {status ? (
            <GoldSnapshotPanel
              status={status}
              snapshots={snapshots}
              candidates={candidates}
              isFetching={statusQuery.isFetching || snapshotsQuery.isFetching || candidatesQuery.isFetching}
            />
          ) : (
            <div className="flex min-h-24 items-center justify-center border-y border-border text-xs text-muted">
              Gold 状态不可用
            </div>
          )}

          <GoldImportComparePanel
            imports={imports}
            comparisons={comparisons}
            initialMarketDate={initialMarketDate}
            isLoading={importsQuery.isLoading || comparisonsQuery.isLoading}
          />

          {gate ? (
            <GoldObservationPanel
              gate={gate}
              isFetching={observationQuery.isFetching}
            />
          ) : (
            <div className="flex min-h-24 items-center justify-center border-y border-border text-xs text-muted">
              观察门槛不可用
            </div>
          )}
        </div>
      </main>

      <footer className="border-t border-border px-5 py-2 text-center text-[10px] text-muted">仅供研究，不构成投资建议</footer>
    </div>
  )
}
