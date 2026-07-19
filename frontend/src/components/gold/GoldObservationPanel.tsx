import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CalendarCheck2,
  CheckCircle2,
  CircleDashed,
  Loader2,
  RotateCcw,
  ShieldCheck,
} from 'lucide-react'
import {
  api,
  type GoldGateStatus,
  type GoldObservationDayState,
  type GoldObservationReviewRequest,
  type GoldObservationGate,
} from '@/lib/api'
import { cn } from '@/lib/cn'
import { QK } from '@/lib/queryKeys'

interface GoldObservationPanelProps {
  gate: GoldObservationGate
  isFetching: boolean
}

const statusLabels: Record<GoldGateStatus, string> = {
  collecting: '观察中',
  failed: '未通过',
  review_eligible: '可进入独立评审',
}

function shortId(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-4)}`
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '复核记录失败'
}

function automaticState(day: GoldObservationDayState) {
  if (day.automatic_passed === null) return { label: '自动：无法确认', tone: 'text-muted' }
  return day.automatic_passed
    ? { label: '自动：通过', tone: 'text-bear' }
    : { label: '自动：未通过', tone: 'text-danger' }
}

function reviewState(day: GoldObservationDayState) {
  if (day.review_recorded === null) return { label: '复核：无法确认', tone: 'text-muted' }
  if (!day.review_recorded) return { label: '复核：未记录', tone: 'text-warning' }
  if (day.review_verified === null) return { label: '复核：无法确认', tone: 'text-muted' }
  return day.review_verified
    ? { label: '复核：通过', tone: 'text-bear' }
    : { label: '复核：未通过', tone: 'text-danger' }
}

export function GoldObservationPanel({
  gate,
  isFetching,
}: GoldObservationPanelProps) {
  const queryClient = useQueryClient()
  const [selectedRunId, setSelectedRunId] = useState('')
  const [dayVerified, setDayVerified] = useState(false)
  const [dayNote, setDayNote] = useState('')
  const [restartVerified, setRestartVerified] = useState(false)
  const [restartNote, setRestartNote] = useState('')
  const canonicalDays = useMemo(() => gate.canonical_days.slice(0, 10), [gate.canonical_days])

  useEffect(() => {
    if (!canonicalDays.some(day => day.run_id === selectedRunId)) {
      setSelectedRunId(canonicalDays[0]?.run_id ?? '')
    }
  }, [canonicalDays, selectedRunId])

  const reviewMutation = useMutation({
    mutationFn: (payload: GoldObservationReviewRequest) => api.goldReviewObservation(payload),
    onSuccess: data => {
      queryClient.setQueryData(QK.goldObservationGate, data)
      queryClient.invalidateQueries({ queryKey: QK.goldObservationGate })
    },
  })

  const selectedRun = canonicalDays.find(day => day.run_id === selectedRunId)

  const submitDayReview = (event: FormEvent) => {
    event.preventDefault()
    if (!selectedRun || !dayNote.trim()) return
    reviewMutation.mutate({
      kind: 'day',
      market_date: selectedRun.market_date,
      run_id: selectedRun.run_id,
      complete_window_verified: dayVerified,
      note: dayNote.trim(),
    })
  }

  const submitRestartReview = (event: FormEvent) => {
    event.preventDefault()
    if (!restartNote.trim()) return
    reviewMutation.mutate({
      kind: 'restart',
      verified: restartVerified,
      note: restartNote.trim(),
    })
  }

  const completeDays = Math.min(10, gate.complete_trading_days)
  const restartState = gate.restart_review.recorded === null
    ? '无法确认'
    : !gate.restart_review.recorded
      ? '未记录'
      : gate.restart_review.verified === null
        ? '无法确认'
        : gate.restart_review.verified ? '已通过' : '未通过'
  const externalSendValue = gate.external_send_count === null ? '无法确认' : `${gate.external_send_count} 次`
  const externalSendRequirement = gate.external_send_count === null
    ? '无法确认'
    : gate.external_send_count === 0 ? '满足' : '未满足'
  const externalSendTone = gate.external_send_count === null
    ? 'text-muted'
    : gate.external_send_count === 0 ? 'text-bear' : 'text-danger'

  return (
    <section className="min-w-0 border-y border-border" aria-labelledby="gold-observation-title">
      <div className="flex min-h-10 items-center justify-between gap-3 px-3 py-2">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-accent" />
          <h2 id="gold-observation-title" className="text-sm font-medium text-foreground">10 日观察门槛</h2>
        </div>
        {isFetching && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted" />}
      </div>

      <div className="grid min-w-0 border-t border-border lg:grid-cols-[0.8fr_1.2fr] lg:divide-x lg:divide-border">
        <div className="min-w-0 px-3 py-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <span className={cn(
                'inline-flex rounded border px-1.5 py-0.5 text-[10px] font-medium',
                gate.status === 'review_eligible'
                  ? 'border-bear/30 bg-bear/10 text-bear'
                  : gate.status === 'failed'
                    ? 'border-danger/30 bg-danger/10 text-danger'
                    : 'border-warning/30 bg-warning/10 text-warning',
              )}>
                {statusLabels[gate.status]}
              </span>
            </div>
            <div className="font-mono text-sm font-semibold tabular-nums text-foreground">
              {completeDays}<span className="text-xs font-normal text-muted"> / 10 个完整交易日</span>
            </div>
          </div>

          <div className="mt-3 grid grid-cols-10 gap-1" aria-label={`${completeDays} / 10 个完整交易日`}>
            {Array.from({ length: 10 }, (_, index) => (
              <div
                key={index}
                className={cn(
                  'h-2 rounded-sm border',
                  index < completeDays ? 'border-bear/40 bg-bear/60' : 'border-border bg-elevated/40',
                )}
              />
            ))}
          </div>

          <dl className="mt-3 grid grid-cols-[7rem_minmax(0,1fr)] gap-x-2 gap-y-1.5 border-y border-border/70 py-2 text-[10px]">
            <dt className="text-muted">要求天数</dt>
            <dd className="font-mono text-secondary">{gate.required_complete_trading_days}</dd>
            <dt className="text-muted">重启复核状态</dt>
            <dd className={cn(
              'break-words',
              gate.restart_review.recorded === null
                ? 'text-muted'
                : gate.restart_review.verified === false ? 'text-danger' : 'text-secondary',
            )}>{restartState}</dd>
            <dt className="text-muted">外发尝试</dt>
            <dd className={cn('font-mono', externalSendTone)}>{externalSendValue}</dd>
            <dt className="text-muted">零外发要求</dt>
            <dd className={externalSendTone}>{externalSendRequirement}</dd>
          </dl>

          <div className="mt-3">
            <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-medium text-secondary">
              {gate.reasons.length > 0
                ? <AlertTriangle className="h-3 w-3 text-danger" />
                : <CheckCircle2 className="h-3 w-3 text-bear" />}
              未通过原因
            </div>
            {gate.reasons.length > 0 ? (
              <ul className="space-y-1">
                {gate.reasons.map(reason => (
                  <li key={reason} className="break-all font-mono text-[10px] leading-relaxed text-danger">{reason}</li>
                ))}
              </ul>
            ) : (
              <p className="text-[10px] text-muted">当前无自动失败原因；天数与人工复核仍按门槛累计。</p>
            )}
          </div>
        </div>

        <div className="min-w-0 border-t border-border lg:border-t-0">
          <div className="px-3 py-3">
            <div className="mb-2 flex items-center justify-between gap-3">
              <div className="flex items-center gap-1.5 text-[10px] font-medium text-secondary">
                <CalendarCheck2 className="h-3 w-3 text-muted" />逐日 run 状态
              </div>
              <span className="font-mono text-[10px] text-muted">最近 {canonicalDays.length} 日</span>
            </div>
            <div className="grid min-h-20 grid-cols-1 gap-px overflow-hidden border border-border bg-border sm:grid-cols-2 xl:grid-cols-5">
              {canonicalDays.map(day => {
                const automatic = automaticState(day)
                const review = reviewState(day)
                return (
                  <div key={day.run_id} className="min-w-0 bg-base px-2 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-[10px] text-secondary">{day.market_date}</span>
                      <span className="text-[9px] text-bear">canonical</span>
                    </div>
                    <div className="mt-1 truncate font-mono text-[9px] text-muted" title={day.run_id}>{shortId(day.run_id)}</div>
                    <div className={cn('mt-1 text-[9px]', automatic.tone)}>{automatic.label}</div>
                    <div className={cn('mt-0.5 text-[9px]', review.tone)}>{review.label}</div>
                  </div>
                )
              })}
              {canonicalDays.length === 0 && (
                <div className="col-span-full flex min-h-20 items-center justify-center gap-1.5 bg-base text-[10px] text-muted">
                  <CircleDashed className="h-3 w-3" />暂无逐日 run
                </div>
              )}
            </div>
          </div>

          <div className="grid border-t border-border md:grid-cols-2 md:divide-x md:divide-border">
            <form onSubmit={submitDayReview} className="min-w-0 px-3 py-3">
              <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-secondary">
                <CalendarCheck2 className="h-3.5 w-3.5 text-muted" />完整日复核
              </div>
              <label className="block text-[10px] text-muted">
                canonical run
                <select
                  value={selectedRunId}
                  onChange={event => setSelectedRunId(event.target.value)}
                  className="mt-1 h-8 w-full rounded-input border border-border bg-base px-2 font-mono text-xs text-secondary outline-none focus:border-accent/50"
                >
                  <option value="">请选择</option>
                  {canonicalDays.map(day => (
                    <option key={day.run_id} value={day.run_id}>{day.market_date} · {shortId(day.run_id)}</option>
                  ))}
                </select>
              </label>
              <label className="mt-2 flex min-h-7 items-center gap-2 text-[10px] text-secondary">
                <input
                  type="checkbox"
                  checked={dayVerified}
                  onChange={event => setDayVerified(event.target.checked)}
                  className="h-3.5 w-3.5 rounded-input border-border bg-base accent-[hsl(var(--accent))]"
                />
                完整交易窗口已核验
              </label>
              <label className="mt-2 block text-[10px] text-muted">
                复核说明
                <textarea
                  value={dayNote}
                  onChange={event => setDayNote(event.target.value)}
                  rows={2}
                  className="mt-1 w-full resize-none rounded-input border border-border bg-base px-2 py-1.5 text-xs text-secondary outline-none focus:border-accent/50"
                />
              </label>
              <button
                type="submit"
                disabled={!selectedRun || !dayNote.trim() || reviewMutation.isPending}
                className="mt-2 inline-flex h-8 items-center justify-center gap-1.5 rounded-btn border border-border bg-elevated/50 px-3 text-xs font-medium text-secondary hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
              >
                {reviewMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                记录完整日复核
              </button>
            </form>

            <form onSubmit={submitRestartReview} className="min-w-0 border-t border-border px-3 py-3 md:border-t-0">
              <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-secondary">
                <RotateCcw className="h-3.5 w-3.5 text-muted" />重启恢复复核
              </div>
              <label className="flex min-h-7 items-center gap-2 text-[10px] text-secondary">
                <input
                  type="checkbox"
                  checked={restartVerified}
                  onChange={event => setRestartVerified(event.target.checked)}
                  className="h-3.5 w-3.5 rounded-input border-border bg-base accent-[hsl(var(--accent))]"
                />
                重启后恢复核验已通过
              </label>
              <label className="mt-2 block text-[10px] text-muted">
                复核说明
                <textarea
                  value={restartNote}
                  onChange={event => setRestartNote(event.target.value)}
                  rows={2}
                  className="mt-1 w-full resize-none rounded-input border border-border bg-base px-2 py-1.5 text-xs text-secondary outline-none focus:border-accent/50"
                />
              </label>
              <button
                type="submit"
                disabled={!restartNote.trim() || reviewMutation.isPending}
                className="mt-2 inline-flex h-8 items-center justify-center gap-1.5 rounded-btn border border-border bg-elevated/50 px-3 text-xs font-medium text-secondary hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
              >
                {reviewMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
                记录重启复核
              </button>
            </form>
          </div>

          {reviewMutation.isError && (
            <div className="flex items-start gap-1.5 border-t border-border px-3 py-2 text-[10px] text-danger">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              <span className="break-words">{errorMessage(reviewMutation.error)}</span>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
