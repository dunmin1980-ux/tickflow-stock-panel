import { useEffect, useId, useMemo, useRef } from 'react'
import * as echarts from 'echarts'
import { Activity } from 'lucide-react'
import type { ResearchEngine } from '@/lib/research-engine'
import { buildStrategyGraphicsOptions, GRAPHICS_COLUMNS, strategyMarkerLabel, type StrategyGraphicsData } from '@/lib/strategy-graphics'
import { useChartTheme } from '@/lib/theme'

function ResearchChart({ name, option, height, descriptionId }: {
  name: string; option: echarts.EChartsOption; height: number; descriptionId: string
}) {
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!root.current) return
    const chart = echarts.init(root.current, undefined, { renderer: 'canvas' })
    chart.setOption(option)
    const resize = () => chart.resize()
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(resize)
    observer?.observe(root.current)
    window.addEventListener('resize', resize)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', resize)
      chart.dispose()
    }
  }, [option])
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
  const tradeDate = graphics?.trade_date ?? engine?.trade_date
  const symbol = graphics?.symbol ?? engine?.symbol
  const priceBasis = graphics?.price_basis ?? engine?.price_basis
  const mismatch = Boolean(graphics && engine && graphics.trade_date !== engine.trade_date)
  const ready = graphics?.status === 'READY' && graphics.points.length > 0 && !mismatch
  const options = useMemo(() => ready && graphics?.status === 'READY' ? buildStrategyGraphicsOptions(graphics, theme) : null,
    [graphics, ready, theme])

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
          同一快照前缀回溯观察 · {graphics.points.length}/{graphics.window_requested} 个交易日 · 策略标记不是成交记录
        </p>
        <div className="mt-3 min-w-0 space-y-3">
          {([
            ['价格 / 均线 / BOLL', '价格与均线图', options.price, 304],
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
            <tbody>{graphics.points.map(point => <tr key={point.trade_date}>
              <th scope="row">{point.trade_date}</th>
              {GRAPHICS_COLUMNS.map(([key]) => <td key={key}>{point[key] ?? '--'}</td>)}
              <td>{graphics.strategy_markers.filter(marker => marker.trade_date === point.trade_date)
                .map(strategyMarkerLabel).join('；') || '无'}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </>}
  </section>
}
