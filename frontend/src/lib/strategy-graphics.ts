import type { EChartsOption, LineSeriesOption, ScatterSeriesOption, TooltipComponentOption } from 'echarts'
import type { ChartTheme } from './theme'
import type { Condition, Fractal, StrategyResearch, StrokeCandidate } from './research-engine'

export interface StrategyGraphicsPoint {
  trade_date: string
  close: number
  open?: number
  high?: number
  low?: number
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60: number | null
  boll_upper: number | null
  boll_middle: number | null
  boll_lower: number | null
  macd_dif: number | null
  macd_dea: number | null
  macd_hist: number | null
  rsi6: number | null
}

export interface StrategyGraphicsMarker {
  trade_date: string
  strategy_id: string
  strategy_name: string
  status: 'TRIGGERED' | 'NEAR_TRIGGER'
  price: number
  conditions_met: number
  conditions_total: number
}

export interface StrategyOverlayMarker {
  marker_id: string
  trade_date: string
  strategy_id: string
  strategy_name: string
  canonical_status: StrategyResearch['status']
  kind: 'STRATEGY' | 'CHAN_STRUCTURE'
  price: number
  conditions_met: number | null
  conditions_total: number | null
  summary: string
  evidence: Condition[]
  failed_conditions: Condition[]
  delta_vs_previous: StrategyResearch['delta_vs_previous'] | null
  structure: Fractal | StrokeCandidate | null
}

export interface StrategyPaperRecord {
  status: 'PUBLISHED' | 'NOT_AVAILABLE'
  trade_date: string
  paper_action: string | null
  research_signal: string | null
  quantity: number | null
  hold_explanation: string[] | string | null
  source: string | null
  source_sha256: string | null
}

export interface StrategyOverlayFilters {
  strategy: string
  status: 'ACTIVE' | 'ALL' | 'TRIGGERED' | 'NEAR_TRIGGER'
}

export interface StrategyOverlayGroup {
  trade_date: string
  markers: StrategyOverlayMarker[]
}

export const DEFAULT_OVERLAY_FILTERS: StrategyOverlayFilters = { strategy: 'ALL', status: 'ACTIVE' }
export const CHAN_OVERLAY_LABEL = '结构识别｜未定义策略触发合同'

export interface StrategyGraphicsReadyData {
  schema_version: 1
  status: 'READY'
  symbol: '000403.SZ'
  trade_date: string
  timeframe: '1d'
  price_basis: 'QFQ'
  history_basis: 'SAME_SNAPSHOT_PREFIX'
  window_requested: number
  points: StrategyGraphicsPoint[]
  strategy_markers: StrategyGraphicsMarker[]
  overlay_markers?: StrategyOverlayMarker[]
  paper_records?: Record<string, StrategyPaperRecord>
  indicator_source?: string
  strategy_source?: string
  source_hashes?: Record<string, string>
}

export interface StrategyGraphicsBlockedData extends Partial<Omit<StrategyGraphicsReadyData, 'status' | 'trade_date'>> {
  status: 'BLOCKED'
  trade_date: string
}

export type StrategyGraphicsData = StrategyGraphicsReadyData | StrategyGraphicsBlockedData

export const STRATEGY_STATUS_LABELS = {
  TRIGGERED: '已触发', NEAR_TRIGGER: '接近触发', NOT_TRIGGERED: '未触发', NOT_AVAILABLE: '数据不足',
} as const

export const GRAPHICS_COLUMNS = [
  ['open', '开盘'], ['high', '最高'], ['low', '最低'],
  ['close', '收盘'], ['ma5', 'MA5'], ['ma10', 'MA10'], ['ma20', 'MA20'], ['ma60', 'MA60'],
  ['boll_upper', 'BOLL 上轨'], ['boll_middle', 'BOLL 中轨'], ['boll_lower', 'BOLL 下轨'],
  ['macd_dif', 'DIF'], ['macd_dea', 'DEA'], ['macd_hist', 'MACD 柱值'], ['rsi6', 'RSI6'],
] as const

export function strategyMarkerLabel(marker: StrategyGraphicsMarker) {
  return `${marker.strategy_name} · ${STRATEGY_STATUS_LABELS[marker.status]} · ${marker.conditions_met}/${marker.conditions_total} · ${marker.price}`
}

export function strategyOverlayLabel(marker: StrategyOverlayMarker) {
  return marker.kind === 'CHAN_STRUCTURE'
    ? `${marker.strategy_name} · ${CHAN_OVERLAY_LABEL} · N/A`
    : `${marker.strategy_name} · ${STRATEGY_STATUS_LABELS[marker.canonical_status]} · ${marker.conditions_met ?? '--'}/${marker.conditions_total ?? '--'} · ${marker.price}`
}

export function strategyGraphicsPoints(graphics: StrategyGraphicsReadyData) {
  return graphics.points.filter(point => point.trade_date <= graphics.trade_date).slice(-30)
}

export function hasStrategyCandles(graphics: StrategyGraphicsReadyData) {
  const points = strategyGraphicsPoints(graphics)
  return points.length > 0 && points.every(point =>
    [point.open, point.close, point.low, point.high].every(value => typeof value === 'number' && Number.isFinite(value)))
}

export function strategyOverlayMarkers(graphics: StrategyGraphicsReadyData): StrategyOverlayMarker[] {
  const dates = new Set(strategyGraphicsPoints(graphics).map(point => point.trade_date))
  // V1 has no recorded evidence or deltas. Adapt only its supplied observation fields.
  const markers = graphics.overlay_markers ?? graphics.strategy_markers.map(marker => ({
    ...marker, marker_id: `${marker.trade_date}:${marker.strategy_id}`, kind: 'STRATEGY' as const,
    canonical_status: marker.status, summary: strategyMarkerLabel(marker),
    evidence: [], failed_conditions: [], delta_vs_previous: null, structure: null,
  }))
  return markers.filter(marker => dates.has(marker.trade_date))
}

export function groupStrategyOverlayMarkers(graphics: StrategyGraphicsReadyData,
  filters: StrategyOverlayFilters = DEFAULT_OVERLAY_FILTERS): StrategyOverlayGroup[] {
  const groups = new Map<string, StrategyOverlayMarker[]>()
  for (const marker of strategyOverlayMarkers(graphics)) {
    if (filters.strategy !== 'ALL' && marker.strategy_id !== filters.strategy) continue
    const structure = marker.kind === 'CHAN_STRUCTURE'
    if (filters.status === 'ACTIVE') {
      if (!structure && !['TRIGGERED', 'NEAR_TRIGGER'].includes(marker.canonical_status)) continue
    } else if (filters.status !== 'ALL' && (structure || marker.canonical_status !== filters.status)) continue
    const day = groups.get(marker.trade_date) ?? []
    day.push(marker)
    groups.set(marker.trade_date, day)
  }
  return strategyGraphicsPoints(graphics).flatMap(point => {
    const markers = groups.get(point.trade_date)
    return markers ? [{ trade_date: point.trade_date, markers }] : []
  })
}

export function strategyOverlayGroupSummary(group: StrategyOverlayGroup) {
  const count = (status: StrategyResearch['status']) => group.markers.filter(marker =>
    marker.kind !== 'CHAN_STRUCTURE' && marker.canonical_status === status).length
  const structures = group.markers.filter(marker => marker.kind === 'CHAN_STRUCTURE').length
  return `${group.markers.length} 条观察 · 已触发 ${count('TRIGGERED')} · 接近触发 ${count('NEAR_TRIGGER')} · 结构 ${structures}`
    + (count('NOT_TRIGGERED') + count('NOT_AVAILABLE') > 0
      ? ` · 未触发 ${count('NOT_TRIGGERED')} · 数据不足 ${count('NOT_AVAILABLE')}` : '')
}

function boundedTooltip(lines: string[]) {
  // ECharts 5 rich-text tooltips ignore width/overflow and use 22px line spacing.
  const wrapped = lines.flatMap(line => {
    const characters = Array.from(line)
    return Array.from({ length: Math.ceil(characters.length / 20) }, (_, index) =>
      characters.slice(index * 20, (index + 1) * 20).join(''))
  })
  return (wrapped.length > 13 ? [...wrapped.slice(0, 12), '…'] : wrapped).join('\n')
}

export function buildStrategyGraphicsOptions(graphics: StrategyGraphicsReadyData, theme: ChartTheme,
  filters: StrategyOverlayFilters = DEFAULT_OVERLAY_FILTERS): {
  price: EChartsOption; macd: EChartsOption; rsi: EChartsOption
} {
  const points = strategyGraphicsPoints(graphics)
  const groups = groupStrategyOverlayMarkers(graphics, filters)
  const tooltip: TooltipComponentOption = {
    trigger: 'axis', confine: true, renderMode: 'richText',
    backgroundColor: theme.tooltipBg, borderColor: theme.tooltipBorder,
    textStyle: { color: theme.tooltipText, fontSize: 11 },
    axisPointer: { type: 'line', lineStyle: { color: theme.crosshair } },
    formatter(params) {
      const items = Array.isArray(params) ? params : [params]
      const lines = items.flatMap(item => {
        if ((item.data as { group?: StrategyOverlayGroup } | undefined)?.group) return []
        if (item.seriesType === 'candlestick') {
          const point = points.find(point => point.trade_date === item.name)
          return point ? [`开 ${point.open} · 收 ${point.close} · 低 ${point.low} · 高 ${point.high}`] : []
        }
        const value = Array.isArray(item.value) ? item.value[1] : item.value
        return [`${item.seriesName}: ${value == null ? '--' : String(value)}`]
      })
      const date = items[0]?.name ?? ''
      const priceHover = items.some(item => item.seriesType === 'candlestick'
        || (item.data as { group?: StrategyOverlayGroup } | undefined)?.group)
      const group = priceHover ? groups.find(group => group.trade_date === date) : undefined
      return boundedTooltip([date, ...(group ? [strategyOverlayGroupSummary(group)] : []), ...lines, ...(group ? [
        ...group.markers.slice(0, 3).map(marker => strategyOverlayLabel(marker).slice(0, 80)),
        ...(group.markers.length > 3 ? [`其余 ${group.markers.length - 3} 条`] : []),
      ] : [])])
    },
  }
  const base: EChartsOption = {
    animation: false,
    grid: { left: 12, right: 16, top: 56, bottom: 12, containLabel: true },
    tooltip,
    legend: {
      type: 'scroll', top: 0, left: 0, right: 0, selectedMode: true,
      itemWidth: 14, itemHeight: 8, itemGap: 12,
      textStyle: { color: theme.text, fontSize: 11 },
      pageTextStyle: { color: theme.text }, pageIconColor: theme.textStrong,
      pageIconInactiveColor: theme.border, pageIconSize: 10,
    },
    xAxis: {
      type: 'category', data: points.map(point => point.trade_date),
      boundaryGap: true,
      axisLabel: { color: theme.text, fontSize: 10, hideOverlap: true, formatter: (value: string) => value.slice(5) },
      axisLine: { lineStyle: { color: theme.border } }, axisTick: { show: false },
    },
    yAxis: {
      type: 'value', scale: true,
      axisLabel: { color: theme.text, fontSize: 10 },
      splitLine: { lineStyle: { color: theme.grid } },
    },
  }
  const observation = {
    xAxis: graphics.trade_date,
    label: { formatter: `观察日 ${graphics.trade_date}`, position: 'end' as const,
      rotate: 0, align: 'right' as const, color: theme.text, fontSize: 10 },
    lineStyle: { color: theme.crosshair, type: 'dashed' as const },
  }
  const markLine: LineSeriesOption['markLine'] = { silent: true, symbol: 'none', data: [observation] }
  const line = (key: typeof GRAPHICS_COLUMNS[number][0], name: string, color: string): LineSeriesOption => ({
    name, type: 'line', data: points.map(point => point[key]),
    connectNulls: false, smooth: false, symbol: points.length === 1 ? 'circle' : 'none', symbolSize: 5,
    lineStyle: { color, width: key === 'close' ? 2 : 1.5, type: key.startsWith('boll_') ? 'dashed' : 'solid' },
    itemStyle: { color }, emphasis: { focus: 'series' },
  })
  const markerSeries: ScatterSeriesOption = {
    name: '策略观察', type: 'scatter', yAxisIndex: 1, clip: false,
    symbol: 'circle', symbolSize: 20, symbolOffset: [0, -30],
    itemStyle: { color: '#0369a1', borderColor: theme.tooltipBg, borderWidth: 1, opacity: 1 },
    label: { show: true, position: 'inside', color: '#fff', fontSize: 10, fontWeight: 'bold',
      formatter: params => String((params.data as { group: StrategyOverlayGroup }).group.markers.length) },
    emphasis: { scale: false }, z: 5,
    data: groups.map(group => ({ name: group.trade_date, value: [group.trade_date, 1], group })),
  }
  const zoom = (visible: number): EChartsOption['dataZoom'] => [{
    id: 'overlay-window', type: 'slider', xAxisIndex: 0, bottom: 0, height: 18,
    startValue: Math.max(0, points.length - visible), endValue: Math.max(0, points.length - 1),
    maxValueSpan: visible - 1, minValueSpan: Math.min(1, Math.max(0, points.length - 1)),
    showDataShadow: false, showDetail: false, brushSelect: false,
    borderColor: theme.border, textStyle: { color: theme.text },
  }]
  const colors = ['#ef4444', '#2563eb', '#d97706', '#16a34a', '#a855f7', '#0891b2', '#64748b', '#db2777']
  const price: EChartsOption = {
    ...base,
    // A price-independent annotation lane keeps every glyph clear of candles and the legend.
    grid: { left: 12, right: 16, top: 104, bottom: 40, containLabel: true },
    yAxis: [base.yAxis as object, { type: 'value', min: 0, max: 1, show: false, axisPointer: { show: false } }],
    dataZoom: zoom(30),
    media: [
      { query: { maxWidth: 480 }, option: { dataZoom: zoom(10) } },
      { query: { minWidth: 481, maxWidth: 760 }, option: { dataZoom: zoom(16) } },
      { option: { dataZoom: zoom(30) } },
    ],
    series: hasStrategyCandles(graphics) ? [
      { name: 'QFQ 日K', type: 'candlestick',
        data: points.map(point => [point.open!, point.close, point.low!, point.high!]),
        itemStyle: { color: '#ef4444', color0: '#16a34a', borderColor: '#ef4444', borderColor0: '#16a34a' }, markLine },
      ...GRAPHICS_COLUMNS.slice(4, 11).map(([key, name], index) => line(key, name, colors[index + 1])),
      markerSeries,
    ] : [],
  }
  const macd: EChartsOption = {
    ...base,
    series: [
      { ...line('macd_dif', 'DIF', '#2563eb'), markLine },
      line('macd_dea', 'DEA', '#d97706'),
      { name: 'MACD 柱值', type: 'bar', data: points.map(point => point.macd_hist), barMaxWidth: 14,
        itemStyle: { color: params => typeof params.value === 'number' && params.value < 0 ? '#16a34a' : '#ef4444' } },
    ],
  }
  const rsi: EChartsOption = {
    ...base,
    yAxis: { ...base.yAxis, min: 0, max: 100 },
    series: [{
      ...line('rsi6', 'RSI6', '#0891b2'),
      markLine: { ...markLine, data: [observation, { yAxis: 50,
        label: { formatter: '50', position: 'insideEndTop', color: theme.text },
        lineStyle: { color: theme.text, type: 'dashed' } }] },
    }],
  }
  return { price, macd, rsi }
}
