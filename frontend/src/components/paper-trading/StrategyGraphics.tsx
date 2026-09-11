import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { Activity, RotateCcw } from 'lucide-react'
import type { ResearchEngine } from '@/lib/research-engine'
import {
  buildStrategyGraphicsOptions, DEFAULT_OVERLAY_FILTERS, GRAPHICS_COLUMNS, groupStrategyOverlayMarkers,
  hasStrategyCandles, strategyGraphicsPoints, strategyOverlayLabel, strategyOverlayMarkers,
  DEFAULT_CHART_LAYERS, STRATEGY_MARKER_LEGEND, type StrategyChartLayers, type StrategyGraphicsData, type StrategyOverlayFilters,
} from '@/lib/strategy-graphics'
import { useChartTheme } from '@/lib/theme'
import { ObservationDateControl, StrategyOverlayDetails } from './StrategyOverlayDetails'

export interface StrategySelection { selectedObservationDate: string | null; filters: StrategyOverlayFilters }

function ResearchChart({ name, option, height, descriptionId, observationDate, onSelectDate, onReset, onWindow }: {
  name: string; option: echarts.EChartsOption; height: number; descriptionId: string; observationDate: string
  onSelectDate: (date: string, strategyId?: string) => void; onReset: () => void; onWindow: (start: number, end: number) => void
}) {
  const root = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const callbacks = useRef({ onSelectDate, onReset, onWindow })
  callbacks.current = { onSelectDate, onReset, onWindow }
  useEffect(() => {
    if (!root.current) return
    const chart = echarts.init(root.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    const select = (params: { name?: string; data?: unknown }) => {
      const data = params.data as { marker?: { trade_date?: string; strategy_id?: string }; group?: { trade_date?: string } } | undefined
      const date = data?.marker?.trade_date ?? data?.group?.trade_date ?? params.name
      if (date && /^\d{4}-\d{2}-\d{2}$/.test(date)) callbacks.current.onSelectDate(date, data?.marker?.strategy_id)
    }
    const hover = (params: { name?: string; data?: unknown }) => {
      const date = (params.data as { marker?: { trade_date?: string } } | undefined)?.marker?.trade_date
      if (date) callbacks.current.onSelectDate(date)
    }
    const zoom = (payload: unknown) => {
      const event = payload as { start?: number; end?: number; batch?: { start?: number; end?: number }[] }
      const value = event.batch?.[0] ?? event
      if (value.start !== undefined && value.end !== undefined) callbacks.current.onWindow(value.start, value.end)
    }
    const blank = (event: { target?: unknown }) => { if (!event.target) callbacks.current.onReset() }
    chart.on('click', select)
    chart.on('mouseover', hover)
    chart.on('datazoom', zoom)
    const zr = chart.getZr?.()
    zr?.on('click', blank)
    const resize = () => chart.resize()
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(resize)
    observer?.observe(root.current)
    window.addEventListener('resize', resize)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', resize)
      chart.off('click', select)
      chart.off('mouseover', hover)
      chart.off('datazoom', zoom)
      zr?.off('click', blank)
      chart.dispose()
      chartRef.current = null
    }
  }, [])
  useEffect(() => { chartRef.current?.setOption(option, { notMerge: true }) }, [option])
  return <div ref={root} role="img" aria-label={name} aria-describedby={descriptionId}
    data-observation-date={observationDate}
    className="w-full min-w-0 max-w-full" style={{ height }} />
}

export function StrategyGraphics({ graphics, requestedDate, engine, selection, onSelectionChange }: {
  graphics: StrategyGraphicsData | undefined
  requestedDate: string
  engine?: ResearchEngine
  selection?: StrategySelection; onSelectionChange?: (selection: StrategySelection) => void
}) {
  const theme = useChartTheme()
  const id = useId()
  const [localSelection, setLocalSelection] = useState<StrategySelection>({ selectedObservationDate: null, filters: DEFAULT_OVERLAY_FILTERS })
  const activeSelection = selection ?? localSelection
  const { filters, selectedObservationDate: selectedDate } = activeSelection
  const [layers, setLayers] = useState<StrategyChartLayers>(DEFAULT_CHART_LAYERS)
  const [windowRange, setWindowRange] = useState<{ start: number; end: number }>()
  // An explicit selection (including the same card again) restores its date viewport.
  // Zoom alone does not change selection and remains under the user's control.
  useEffect(() => { setWindowRange(undefined) }, [activeSelection])
  useEffect(() => {
    const strategy = filters.strategy
    if (strategy === 'ALL') setLayers(DEFAULT_CHART_LAYERS)
    else setLayers(current => ({ ...current, candles: true, strategies: true,
      ...(strategy === 'bullish_alignment' ? { ma5: true, ma10: true, ma20: true, ma60: true } : {}),
      ...(strategy === 'pullback_to_support' ? { ma5: true, ma20: true } : {}),
      ...(['boll_breakout', 'boll_lower_reclaim'].includes(strategy) ? { boll: true } : {}),
    }))
  }, [filters.strategy])
  const changeSelection = useCallback((next: StrategySelection) => {
    if (onSelectionChange) onSelectionChange(next)
    else setLocalSelection(next)
  }, [onSelectionChange])
  const selectDate = useCallback((date: string, strategyId?: string) => {
    if (date === selectedDate && (!strategyId || strategyId === filters.strategy)) return
    setWindowRange(undefined)
    changeSelection({ selectedObservationDate: date, filters: strategyId ? { strategy: strategyId, status: 'ALL' } : filters })
  }, [filters, selectedDate, changeSelection])
  const setFilters = (next: StrategyOverlayFilters) => changeSelection({ ...activeSelection, filters: next })
  const reset = () => { setLayers(DEFAULT_CHART_LAYERS); setFilters(DEFAULT_OVERLAY_FILTERS) }
  const tradeDate = graphics?.trade_date ?? engine?.trade_date
  const symbol = graphics?.symbol ?? engine?.symbol
  const priceBasis = graphics?.price_basis ?? engine?.price_basis
  const mismatch = Boolean(graphics && engine && graphics.trade_date !== engine.trade_date)
  const ready = graphics?.status === 'READY' && graphics.points.length > 0 && !mismatch
  const points = graphics?.status === 'READY' ? strategyGraphicsPoints(graphics) : []
  const groups = graphics?.status === 'READY' ? groupStrategyOverlayMarkers(graphics, filters) : []
  const markers = graphics?.status === 'READY' ? strategyOverlayMarkers(graphics) : []
  const strategies = new Map((engine?.strategies ?? []).map(strategy => [strategy.strategy_id, strategy.strategy_name]))
  markers.forEach(marker => strategies.set(marker.strategy_id, marker.strategy_name))
  if (!strategies.has('chan_daily_structure')) strategies.set('chan_daily_structure', 'Chan Daily Structure')
  const dates = points.map(point => point.trade_date)
  const date = selectedDate && dates.includes(selectedDate) ? selectedDate : dates.at(-1) ?? ''
  const group = groups.find(group => group.trade_date === date)
  const point = points.find(point => point.trade_date === date)
  const options = useMemo(() => ready && graphics?.status === 'READY'
    ? buildStrategyGraphicsOptions(graphics, theme, filters, { selectedObservationDate: date, layers, window: windowRange }) : null,
  [graphics, ready, theme, filters, date, layers, windowRange])
  const chartProps = { descriptionId: `${id}-date ${id}-basis`, observationDate: date, onSelectDate: selectDate,
    onReset: reset, onWindow: (start: number, end: number) => setWindowRange({
      start: Math.round(start * (points.length - 1) / 100), end: Math.round(end * (points.length - 1) / 100),
    }) }

  return <section aria-label="策略图表" className="min-w-0 border-y border-border py-4 text-foreground [overflow-wrap:anywhere]">
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h2 className="flex items-center gap-2 text-[16px] font-semibold"><Activity aria-hidden="true" className="h-4 w-4 shrink-0" />策略图表</h2>
      <p id={`${id}-date`} className="text-xs text-secondary">
        {tradeDate ? `研究日期 ${tradeDate}` : '研究日期未提供'}{symbol && ` · ${symbol}`}{priceBasis && ` · ${priceBasis} 前复权日线`}
      </p>
    </div>
    {tradeDate && tradeDate !== requestedDate && <p role="status" className="mt-2 border-l-2 border-warning px-3 py-2 text-xs text-warning">
      当前展示 {tradeDate} 的研究快照，尚非 {requestedDate} 当日结果。
    </p>}
    {graphics?.status === 'BLOCKED' ? <p role="alert" className="py-4 text-sm text-warning">图表数据校验未通过，暂停展示图表。</p>
      : mismatch ? <p role="alert" className="py-4 text-sm text-warning">
        图表日期与研究日期不一致：图表 {graphics?.trade_date}，研究 {engine?.trade_date}，暂停展示图表。
      </p>
      : !graphics ? <p role="status" className="py-4 text-sm text-secondary">暂无图表数据。</p>
      : !options ? <p role="status" className="py-4 text-sm text-secondary">暂无可绘制的日线数据。</p>
      : <>
        <p id={`${id}-basis`} className="mt-2 text-[11px] text-muted">
          同一快照前缀回溯观察 · {points.length}/{graphics.window_requested} 个交易日 · 策略标记不是成交记录
        </p>
        <div className="mt-3 grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-2">
          <label className="min-w-0 text-xs text-secondary">策略筛选
            <select aria-label="策略筛选" value={filters.strategy} onChange={event => {
              const strategy = event.target.value
              setFilters(strategy === 'ALL' ? DEFAULT_OVERLAY_FILTERS : { strategy, status: 'ALL' })
            }}
              className="mt-1 block h-10 w-full min-w-0 rounded border border-border bg-surface px-2 text-xs text-foreground focus-visible:outline-accent">
              <option value="ALL">全部策略</option>
              {[...strategies].map(([key, name]) => <option key={key} value={key}>{name}</option>)}
            </select>
          </label>
          <label className="min-w-0 text-xs text-secondary">状态筛选
            <select aria-label="状态筛选" value={filters.status}
              onChange={event => setFilters({ ...filters, status: event.target.value as StrategyOverlayFilters['status'] })}
              className="mt-1 block h-10 w-full min-w-0 rounded border border-border bg-surface px-2 text-xs text-foreground focus-visible:outline-accent">
              <option value="ACTIVE">活跃观察与结构</option>
              <option value="ALL">全部状态</option>
              <option value="TRIGGERED">仅已触发</option>
              <option value="NEAR_TRIGGER">仅接近触发</option>
            </select>
          </label>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2" role="group" aria-label="价格图层">
          {([['candles', 'K线'], ['ma5', 'MA5'], ['ma10', 'MA10'], ['ma20', 'MA20'], ['ma60', 'MA60'], ['boll', 'BOLL'], ['strategies', '策略']] as const)
            .map(([key, label]) => <label key={key} className="inline-flex min-h-9 items-center gap-1.5 text-xs">
              <input type="checkbox" checked={layers[key]} onChange={event => setLayers(current => ({ ...current, [key]: event.target.checked }))}
                className="h-4 w-4 accent-current" />{label}
            </label>)}
          <button type="button" aria-label="全部策略" onClick={reset} className="inline-flex min-h-9 items-center gap-1 text-xs">
            <RotateCcw aria-hidden="true" className="h-3.5 w-3.5" />全部策略
          </button>
        </div>
        <p className="mt-2 text-xs text-secondary" aria-label="标记状态图例">实心：已触发 · 空心：接近触发 · 淡色：未触发 · 灰色：不可用 / Chan 结构</p>
        <div role="group" aria-label="策略标记图例" className="mt-2 flex flex-wrap gap-x-4 gap-y-2 text-xs">
          {Object.entries(STRATEGY_MARKER_LEGEND).map(([key, item]) => <button key={key} type="button"
            aria-pressed={filters.strategy === key} onClick={() => setFilters({ strategy: key, status: 'ALL' })}
            className="inline-flex min-h-9 items-center gap-1.5 text-secondary focus-visible:outline-accent">
            <span className="font-mono font-semibold">{item.label}</span>{item.name}
          </button>)}
        </div>
        <section aria-label="观察日指标" className="mt-3 border-y border-border py-2 text-xs">
          <ObservationDateControl dates={dates} selectedDate={date} onDateChange={selectDate} />
          <h3 className="font-medium">观察日 {date} · 同一 QFQ 快照回溯</h3>
          <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-2 sm:grid-cols-4">
            {([['close', '收盘'], ['ma5', 'MA5'], ['ma10', 'MA10'], ['ma20', 'MA20'], ['volume', '相对量能'],
              ['macd_dif', 'DIF'], ['macd_dea', 'DEA'], ['macd_hist', 'MACD 柱值'], ['rsi6', 'RSI6']] as const).map(([key, label]) =>
              <div key={key} className="min-w-0"><dt className="text-secondary">{label}</dt><dd className="font-mono">{point?.[key] ?? '--'}</dd></div>)}
          </dl>
          {filters.strategy !== 'ALL' && group?.markers.map(marker => <div key={marker.marker_id}
            className="mt-3 min-w-0 border-t border-border pt-2" aria-label="聚焦策略证据">
            <p className="font-medium">{strategyOverlayLabel(marker)}</p>
            {marker.kind === 'STRATEGY' && <ul className="mt-2 grid gap-2 sm:grid-cols-2">
              {marker.evidence.map((condition, index) => <li key={`${condition.label}-${index}`} className="min-w-0 text-secondary">
                <span className={condition.passed === true ? 'text-bull' : 'text-warning'}>
                  {condition.passed === true ? '满足' : condition.passed === false ? '未满足' : '不可用'}
                </span>{' · '}{condition.label} · {condition.actual}
              </li>)}
            </ul>}
          </div>)}
        </section>
        <div className="mt-3 min-w-0 space-y-3">
          <div className="min-w-0 border-t border-border pt-3">
            <h3 className="mb-2 text-xs font-medium">QFQ 日K / 均线 / BOLL</h3>
            {graphics.status === 'READY' && hasStrategyCandles(graphics)
              ? <ResearchChart name="价格与均线图" option={options.price} height={380} {...chartProps} />
              : <p className="py-4 text-sm text-secondary">缺少完整 OHLC 数据，K 线图不可用。</p>}
          </div>
          {([
            ['Volume · 相对量能', '成交量图', options.volume, 260],
            ['MACD', 'MACD 图', options.macd, 224],
            ['RSI6', 'RSI6 图', options.rsi, 224],
          ] as const).map(([title, name, option, height]) => <div key={name} className="min-w-0 border-t border-border pt-3">
            <h3 className="mb-2 text-xs font-medium text-secondary">{title}</h3>
            {name === '成交量图' && <p className="mb-2 text-[11px] text-muted">原始量序列 · 单位待确认 · VOL5/VOL10 含当日</p>}
            {name === '成交量图' && !points.some(point => typeof point.volume === 'number' && Number.isFinite(point.volume))
              && <p className="text-xs text-secondary">量能数据不可用</p>}
            <ResearchChart name={name} option={option} height={height} {...chartProps} />
          </div>)}
          <StrategyOverlayDetails dates={dates} selectedDate={date} onDateChange={selectDate} group={group}
            record={graphics.paper_records?.[date]} showDateControl={false} />
        </div>
        <div className="sr-only">
          <table aria-label="策略图表数据">
            <caption>{graphics.symbol} · {graphics.trade_date} · QFQ · 同一快照前缀回溯观察</caption>
            <thead><tr><th scope="col">交易日期</th>
              {GRAPHICS_COLUMNS.map(([key, label]) => <th scope="col" key={key}>{label}</th>)}
              <th scope="col">策略观察标记</th>
            </tr></thead>
            <tbody>{points.map(point => <tr key={point.trade_date}>
              <th scope="row">{point.trade_date}</th>
              {GRAPHICS_COLUMNS.map(([key]) => <td key={key}>{point[key] ?? '--'}</td>)}
              <td>{groups.find(group => group.trade_date === point.trade_date)?.markers
                .map(strategyOverlayLabel).join('；') || '无'}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </>}
  </section>
}
