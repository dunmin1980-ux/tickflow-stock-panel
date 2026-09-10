import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { Activity } from 'lucide-react'
import type { ResearchEngine } from '@/lib/research-engine'
import {
  buildStrategyGraphicsOptions, DEFAULT_OVERLAY_FILTERS, GRAPHICS_COLUMNS, groupStrategyOverlayMarkers,
  hasStrategyCandles, strategyGraphicsPoints, strategyOverlayLabel, strategyOverlayMarkers,
  type StrategyGraphicsData, type StrategyOverlayFilters,
} from '@/lib/strategy-graphics'
import { useChartTheme } from '@/lib/theme'
import { StrategyOverlayDetails } from './StrategyOverlayDetails'

function ResearchChart({ name, option, height, descriptionId, onSelectDate }: {
  name: string; option: echarts.EChartsOption; height: number; descriptionId: string
  onSelectDate?: (date: string) => void
}) {
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!root.current) return
    const chart = echarts.init(root.current, undefined, { renderer: 'canvas' })
    chart.setOption(option)
    const select = (params: { seriesName?: string; data?: unknown }) => {
      if (params.seriesName !== '策略观察') return
      const date = (params.data as { group?: { trade_date?: string } } | undefined)?.group?.trade_date
      if (date) onSelectDate?.(date)
    }
    if (onSelectDate) {
      chart.on('click', select)
      chart.on('mouseover', select)
    }
    const resize = () => chart.resize()
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(resize)
    observer?.observe(root.current)
    window.addEventListener('resize', resize)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', resize)
      if (onSelectDate) {
        chart.off('click', select)
        chart.off('mouseover', select)
      }
      chart.dispose()
    }
  }, [option, onSelectDate])
  return <div ref={root} role="img" aria-label={name} aria-describedby={descriptionId}
    className="w-full min-w-0 max-w-full" style={{ height }} />
}

export function StrategyGraphics({ graphics, requestedDate, engine }: {
  graphics: StrategyGraphicsData | undefined
  requestedDate: string
  engine?: ResearchEngine
}) {
  const theme = useChartTheme()
  const id = useId()
  const [filters, setFilters] = useState<StrategyOverlayFilters>(DEFAULT_OVERLAY_FILTERS)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const selectDate = useCallback((date: string) => setSelectedDate(date), [])
  const tradeDate = graphics?.trade_date ?? engine?.trade_date
  const symbol = graphics?.symbol ?? engine?.symbol
  const priceBasis = graphics?.price_basis ?? engine?.price_basis
  const mismatch = Boolean(graphics && engine && graphics.trade_date !== engine.trade_date)
  const ready = graphics?.status === 'READY' && graphics.points.length > 0 && !mismatch
  const options = useMemo(() => ready && graphics?.status === 'READY' ? buildStrategyGraphicsOptions(graphics, theme, filters) : null,
    [graphics, ready, theme, filters])
  const points = graphics?.status === 'READY' ? strategyGraphicsPoints(graphics) : []
  const groups = graphics?.status === 'READY' ? groupStrategyOverlayMarkers(graphics, filters) : []
  const markers = graphics?.status === 'READY' ? strategyOverlayMarkers(graphics) : []
  const strategies = new Map((engine?.strategies ?? []).map(strategy => [strategy.strategy_id, strategy.strategy_name]))
  markers.forEach(marker => strategies.set(marker.strategy_id, marker.strategy_name))
  if (!strategies.has('chan_daily_structure')) strategies.set('chan_daily_structure', 'Chan Daily Structure')
  const dates = points.map(point => point.trade_date)
  const date = selectedDate && dates.includes(selectedDate) ? selectedDate : groups.at(-1)?.trade_date ?? dates.at(-1) ?? ''
  const group = groups.find(group => group.trade_date === date)

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
            <select aria-label="策略筛选" value={filters.strategy} onChange={event => setFilters(current => ({ ...current, strategy: event.target.value }))}
              className="mt-1 block h-10 w-full min-w-0 rounded border border-border bg-surface px-2 text-xs text-foreground focus-visible:outline-accent">
              <option value="ALL">全部策略</option>
              {[...strategies].map(([key, name]) => <option key={key} value={key}>{name}</option>)}
            </select>
          </label>
          <label className="min-w-0 text-xs text-secondary">状态筛选
            <select aria-label="状态筛选" value={filters.status}
              onChange={event => setFilters(current => ({ ...current, status: event.target.value as StrategyOverlayFilters['status'] }))}
              className="mt-1 block h-10 w-full min-w-0 rounded border border-border bg-surface px-2 text-xs text-foreground focus-visible:outline-accent">
              <option value="ACTIVE">活跃观察与结构</option>
              <option value="ALL">全部状态</option>
              <option value="TRIGGERED">仅已触发</option>
              <option value="NEAR_TRIGGER">仅接近触发</option>
            </select>
          </label>
        </div>
        <div className="mt-3 min-w-0 space-y-3">
          <div className="min-w-0 border-t border-border pt-3">
            <h3 className="mb-2 text-xs font-medium">QFQ 日K / 均线 / BOLL</h3>
            {graphics.status === 'READY' && hasStrategyCandles(graphics)
              ? <ResearchChart name="价格与均线图" option={options.price} height={344}
                descriptionId={`${id}-date ${id}-basis`} onSelectDate={selectDate} />
              : <p className="py-4 text-sm text-secondary">缺少完整 OHLC 数据，K 线图不可用。</p>}
          </div>
          <StrategyOverlayDetails dates={dates} selectedDate={date} onDateChange={selectDate} group={group}
            record={graphics.paper_records?.[date]} />
          {([
            ['MACD', 'MACD 图', options.macd, 224],
            ['RSI6', 'RSI6 图', options.rsi, 224],
          ] as const).map(([title, name, option, height]) => <div key={name} className="min-w-0 border-t border-border pt-3">
            <h3 className="mb-2 text-xs font-medium text-secondary">{title}</h3>
            <ResearchChart name={name} option={option} height={height} descriptionId={`${id}-date ${id}-basis`} />
          </div>)}
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
