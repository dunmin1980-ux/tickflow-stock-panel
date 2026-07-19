import {
  Activity,
  AlertTriangle,
  BellOff,
  CheckCircle2,
  Clock3,
  Database,
  Loader2,
  RadioTower,
} from 'lucide-react'
import type {
  GoldCandidate,
  GoldShadowSnapshot,
  GoldState,
  GoldStatus,
} from '@/lib/api'
import { cn } from '@/lib/cn'

export interface GoldSnapshotPanelProps {
  status: GoldStatus
  snapshots: GoldShadowSnapshot[]
  candidates: GoldCandidate[]
  isFetching: boolean
}

const stateStyles: Record<GoldState, string> = {
  恐慌: 'border-danger/40 bg-danger/10 text-danger',
  死机: 'border-accent/40 bg-accent/10 text-accent',
  贪婪: 'border-warning/40 bg-warning/10 text-warning',
}

function formatNumber(value: number | null | undefined, digits = 2) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—'
}

function formatSigned(value: number | null | undefined, suffix = '') {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}${suffix}`
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return '暂无'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(parsed)
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-0 border-b border-r border-border px-3 py-2.5 last:border-r-0 lg:border-b-0">
      <div className="text-[10px] text-muted">{label}</div>
      <div className="mt-1 break-words font-mono text-base font-semibold leading-tight tabular-nums text-foreground">
        {value}
      </div>
      {hint && <div className="mt-1 truncate text-[10px] text-muted" title={hint}>{hint}</div>}
    </div>
  )
}

function StateBadge({ state }: { state: GoldState }) {
  return (
    <span className={cn('inline-flex rounded border px-1.5 py-0.5 text-[10px] font-medium', stateStyles[state])}>
      {state}
    </span>
  )
}

export function GoldSnapshotPanel({
  status,
  snapshots,
  candidates,
  isFetching,
}: GoldSnapshotPanelProps) {
  const latest = status.latest ?? snapshots[0] ?? null
  const health = status.health

  return (
    <section className="min-w-0 border-y border-border" aria-labelledby="gold-snapshot-title">
      <div className="flex min-h-10 items-center justify-between gap-3 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <Activity className="h-4 w-4 shrink-0 text-accent" />
          <div className="min-w-0">
            <h2 id="gold-snapshot-title" className="text-sm font-medium text-foreground">快照与运行健康</h2>
            <p className="truncate text-[10px] text-muted">{status.symbol} · schema v{latest?.schema_version ?? 1}</p>
          </div>
        </div>
        {isFetching && (
          <span className="inline-flex shrink-0 items-center gap-1 text-[10px] text-muted">
            <Loader2 className="h-3 w-3 animate-spin" />更新中
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 border-t border-border sm:grid-cols-3 lg:grid-cols-6">
        <Metric label="最新价" value={formatNumber(latest?.price)} hint={`前收 ${formatNumber(latest?.previous_close)}`} />
        <Metric label="P" value={formatSigned(latest?.P, '%')} />
        <Metric label="V" value={formatSigned(latest?.V)} />
        <Metric label="A" value={formatSigned(latest?.A)} />
        <Metric label="60 日兼容参考" value={formatNumber(latest?.legacy_reference_60)} />
        <Metric label="原生 EMA60" value={formatNumber(latest?.native_ema60)} />
      </div>

      <div className="grid min-w-0 border-t border-border lg:grid-cols-[1fr_1.15fr_1.4fr] lg:divide-x lg:divide-border">
        <div className="min-w-0 px-3 py-3">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-secondary">
            <RadioTower className="h-3.5 w-3.5 text-muted" />SJM 状态
          </div>
          <div className="grid grid-cols-3 gap-1">
            {(['恐慌', '死机', '贪婪'] as const).map(state => (
              <div
                key={state}
                className={cn(
                  'flex h-8 items-center justify-center rounded border text-xs font-medium',
                  latest?.state === state ? stateStyles[state] : 'border-border bg-elevated/30 text-muted',
                )}
              >
                {state}
              </div>
            ))}
          </div>
          <div className="mt-2 space-y-1 text-[10px] text-muted">
            <p>观测时间：<span className="text-secondary">{formatDateTime(latest?.observed_at)}</span></p>
            <p>行情时间戳：<span className="break-all font-mono text-secondary">{latest?.quote_ts ?? '—'}</span></p>
            <p>来源：<span className="font-mono uppercase text-secondary">{latest?.quote_source ?? '—'}</span></p>
          </div>
        </div>

        <div className="min-w-0 border-t border-border px-3 py-3 lg:border-t-0">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-secondary">
            <Database className="h-3.5 w-3.5 text-muted" />候选信号
          </div>
          <div className="flex min-h-7 flex-wrap gap-1">
            {(latest?.candidate_signals ?? []).map(signal => (
              <span key={signal} className="rounded border border-warning/30 bg-warning/10 px-1.5 py-1 text-[10px] text-warning">
                {signal}
              </span>
            ))}
            {!latest?.candidate_signals.length && <span className="text-[11px] text-muted">当前无候选信号</span>}
          </div>
          <p className="mt-2 text-[10px] text-muted">
            本次新增：{latest?.new_candidate_signals.join('、') || '无'}
          </p>
          <div className="mt-2 max-h-24 overflow-y-auto border-t border-border/70 pt-1.5">
            {candidates.slice(0, 8).map(candidate => (
              <div key={candidate.key} className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-3 py-1 text-[10px]">
                <span className="min-w-0 text-secondary">
                  <span className="block">{candidate.signal}</span>
                  <span className="block break-all font-mono text-[9px] text-muted">{candidate.key}</span>
                </span>
                <span className="text-right font-mono text-muted">
                  <span className="block">{candidate.symbol}</span>
                  <span className="block">{candidate.market_date} · 当日已去重</span>
                </span>
              </div>
            ))}
            {candidates.length === 0 && <p className="py-1 text-[10px] text-muted">暂无登记记录</p>}
          </div>
        </div>

        <div className="min-w-0 border-t border-border px-3 py-3 lg:border-t-0">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-secondary">
            {health.last_error ? <AlertTriangle className="h-3.5 w-3.5 text-danger" /> : <CheckCircle2 className="h-3.5 w-3.5 text-bear" />}
            运行健康
          </div>
          <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-2 gap-y-1.5 text-[10px]">
            <dt className="text-muted">最近成功</dt>
            <dd className="break-words text-secondary">{formatDateTime(health.latest_success_at)}</dd>
            <dt className="text-muted">健康交易日</dt>
            <dd className="font-mono text-secondary">{health.latest_market_date ?? '—'}</dd>
            <dt className="text-muted">状态交易日</dt>
            <dd className="font-mono text-secondary">{status.latest_market_date ?? '—'}</dd>
            <dt className="text-muted">连续失败</dt>
            <dd className="font-mono text-secondary">{health.consecutive_failures}</dd>
            <dt className="text-muted">最后错误</dt>
            <dd className={cn('min-w-0 break-words', health.last_error ? 'text-danger' : 'text-secondary')}>
              {health.last_error ? `${health.last_error.code}: ${health.last_error.message}` : '无结构化错误'}
            </dd>
          </dl>
          <div className="mt-3 flex min-w-0 items-start gap-2 border-t border-border/70 pt-2 text-[10px] text-secondary">
            <BellOff className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" />
            <span className="min-w-0 break-words">
              Gold 外部通知已关闭 · 外发尝试 <strong className="font-mono font-medium text-foreground">{status.external_send_count}</strong> 次
            </span>
          </div>
        </div>
      </div>

      <div className="border-t border-border">
        <div className="flex items-center justify-between gap-3 px-3 py-2">
          <div className="flex items-center gap-1.5 text-xs font-medium text-secondary">
            <Clock3 className="h-3.5 w-3.5 text-muted" />最近快照
          </div>
          <span className="font-mono text-[10px] text-muted">{snapshots.length} 条</span>
        </div>
        <div className="w-full overflow-x-auto border-t border-border/70">
          <table className="w-full min-w-[1180px] border-collapse text-left text-[10px]">
            <thead className="bg-elevated/40 text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">观测时间</th>
                <th className="px-3 py-2 font-medium">交易日</th>
                <th className="px-3 py-2 text-right font-medium">价格 / 前收</th>
                <th className="px-3 py-2 text-right font-medium">P</th>
                <th className="px-3 py-2 text-right font-medium">V</th>
                <th className="px-3 py-2 text-right font-medium">A</th>
                <th className="px-3 py-2 text-right font-medium">兼容 60 / EMA60</th>
                <th className="px-3 py-2 font-medium">SJM</th>
                <th className="px-3 py-2 font-medium">候选 / 本次新增</th>
                <th className="px-3 py-2 font-medium">来源</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/70">
              {snapshots.map(row => (
                <tr key={`${row.quote_ts}-${row.observed_at}`} className="text-secondary">
                  <td className="whitespace-nowrap px-3 py-2 font-mono">{formatDateTime(row.observed_at)}</td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono">{row.market_date}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-right font-mono tabular-nums text-foreground">
                    {formatNumber(row.price)} / {formatNumber(row.previous_close)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums">{formatSigned(row.P, '%')}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums">{formatSigned(row.V)}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums">{formatSigned(row.A)}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-right font-mono tabular-nums">
                    {formatNumber(row.legacy_reference_60)} / {formatNumber(row.native_ema60)}
                  </td>
                  <td className="px-3 py-2"><StateBadge state={row.state} /></td>
                  <td className="max-w-72 px-3 py-2">
                    <span className="block truncate" title={`${row.candidate_signals.join('、')} / ${row.new_candidate_signals.join('、')}`}>
                      {row.candidate_signals.join('、') || '无'} / {row.new_candidate_signals.join('、') || '无'}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono uppercase">
                    <span className="block">{row.quote_source}</span>
                    <span className="block text-[9px] text-muted">{row.quote_ts}</span>
                  </td>
                </tr>
              ))}
              {snapshots.length === 0 && (
                <tr><td colSpan={10} className="px-3 py-8 text-center text-muted">暂无快照</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
