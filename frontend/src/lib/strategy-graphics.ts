import type { EChartsOption, LineSeriesOption, ScatterSeriesOption, TooltipComponentOption } from 'echarts'
import type { ChartTheme } from './theme'
import type { Condition, Fractal, StrategyResearch, StrokeCandidate } from './research-engine'

export interface StrategyGraphicsPoint {
  trade_date: string
  close: number
  open?: number
  high?: number
  low?: number
  volume?: number | null
  volume_ma5?: number | null
  volume_ma10?: number | null
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

export interface StrategyChartLayers {
  candles: boolean
  ma5: boolean
  ma10: boolean
  ma20: boolean
  ma60: boolean
  boll: boolean
  strategies: boolean
}

export interface StrategyChartView {
  selectedObservationDate?: string
  layers?: StrategyChartLayers
  window?: { start: number; end: number }
}

export const DEFAULT_CHART_LAYERS: StrategyChartLayers = {
  candles: true, ma5: true, ma10: false, ma20: true, ma60: false, boll: false, strategies: true,
}

export const STRATEGY_MARKER_LEGEND: Record<string, { symbol: string; label: string; name: string }> = {
  macd_golden: { symbol: 'diamond', label: 'MC', name: 'MACD 金叉放量' },
  bullish_alignment: { symbol: 'triangle', label: 'MA', name: '均线多头' },
  boll_breakout: { symbol: 'rect', label: 'BU', name: '布林突破' },
  volume_price_surge: { symbol: 'circle', label: 'VP', name: '量价齐升' },
  pullback_to_support: { symbol: 'roundRect', label: 'PB', name: '缩量回踩' },
  boll_lower_reclaim: { symbol: 'arrow', label: 'BL', name: '布林下轨回升' },
  chan_daily_structure: { symbol: 'pin', label: 'CH', name: 'Chan 结构' },
}

export function strategyMarkerAppearance(marker: StrategyOverlayMarker) {
  const identity = STRATEGY_MARKER_LEGEND[marker.strategy_id] ?? { symbol: 'rect', label: '?', name: marker.strategy_name }
  const styles = {
    TRIGGERED: { color: '#dc2626', borderColor: '#dc2626', borderWidth: 1.5, borderType: 'solid' as const, opacity: 1, labelColor: '#fff' },
    NEAR_TRIGGER: { color: 'transparent', borderColor: '#d97706', borderWidth: 2, borderType: 'solid' as const, opacity: 1, labelColor: '#d97706' },
    NOT_TRIGGERED: { color: '#a1a1aa', borderColor: '#71717a', borderWidth: 1, borderType: 'solid' as const, opacity: 0.42, labelColor: '#52525b' },
    NOT_AVAILABLE: { color: 'transparent', borderColor: '#71717a', borderWidth: 1.5, borderType: 'dashed' as const, opacity: 0.8, labelColor: '#71717a' },
  }
  return { ...identity, ...styles[marker.canonical_status] }
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
  ['volume', '相对量能'], ['volume_ma5', 'VOL5'], ['volume_ma10', 'VOL10'],
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
  const wrapped = lines.flatMap(line => line.split(/\r?\n/)).flatMap(line => {
    const characters = Array.from(line)
    return Array.from({ length: Math.ceil(characters.length / 20) }, (_, index) =>
      characters.slice(index * 20, (index + 1) * 20).join(''))
  })
  return (wrapped.length > 13 ? [...wrapped.slice(0, 12), '…'] : wrapped).join('\n')
}

export function buildStrategyGraphicsOptions(graphics: StrategyGraphicsReadyData, theme: ChartTheme,
  filters: StrategyOverlayFilters = DEFAULT_OVERLAY_FILTERS, view: StrategyChartView = {}): {
  price: EChartsOption; macd: EChartsOption; volume: EChartsOption; rsi: EChartsOption
} {
  const points = strategyGraphicsPoints(graphics)
  const dates = points.map(point => point.trade_date)
  const pointByDate = new Map(points.map(point => [point.trade_date, point]))
  const layers = view.layers ?? DEFAULT_CHART_LAYERS
  const markers = layers.strategies ? groupStrategyOverlayMarkers(graphics, filters).flatMap(group => group.markers) : []
  const selectedDate = view.selectedObservationDate && dates.includes(view.selectedObservationDate)
    ? view.selectedObservationDate : dates.at(-1) ?? graphics.trade_date
  const priceStrategies = ['bullish_alignment', 'boll_breakout', 'pullback_to_support', 'boll_lower_reclaim']
  const volumeStrategies = ['volume_price_surge', 'pullback_to_support', 'macd_golden']
  const priceMarkers = markers.filter(marker => marker.kind === 'CHAN_STRUCTURE' || priceStrategies.includes(marker.strategy_id))
  const macdMarkers = markers.filter(marker => marker.kind === 'STRATEGY' && marker.strategy_id === 'macd_golden')
  const volumeMarkers = markers.filter(marker => marker.kind === 'STRATEGY' && volumeStrategies.includes(marker.strategy_id))
  const volumeSelected = volumeStrategies.includes(filters.strategy)
  const relatedLines: Record<string, string[]> = {
    bullish_alignment: ['ma5', 'ma10', 'ma20', 'ma60'],
    pullback_to_support: ['ma5', 'ma10', 'ma20', 'volume_ma5', 'volume_ma10'],
    boll_breakout: ['boll_upper', 'boll_middle', 'boll_lower'],
    boll_lower_reclaim: ['boll_upper', 'boll_middle', 'boll_lower'],
    macd_golden: ['macd_dif', 'macd_dea', 'volume_ma5', 'volume_ma10'],
    volume_price_surge: ['volume_ma5', 'volume_ma10'],
  }
  const tooltip = (panelMarkers: StrategyOverlayMarker[]): TooltipComponentOption => ({
    trigger: 'axis', confine: true, renderMode: 'richText',
    backgroundColor: theme.tooltipBg, borderColor: theme.tooltipBorder,
    textStyle: { color: theme.tooltipText, fontSize: 11 },
    axisPointer: { type: 'line', lineStyle: { color: theme.crosshair } },
    formatter(params) {
      const items = Array.isArray(params) ? params : [params]
      const hovered = items.map(item => (item.data as { marker?: StrategyOverlayMarker } | undefined)?.marker).find(Boolean)
      const date = hovered?.trade_date ?? items[0]?.name ?? ''
      const dayMarkers = panelMarkers.filter(marker => marker.trade_date === date)
      const group = dayMarkers.length ? { trade_date: date, markers: dayMarkers } : undefined
      const lines = items.flatMap(item => {
        if ((item.data as { marker?: StrategyOverlayMarker } | undefined)?.marker || item.seriesName === '观察日') return []
        if (item.seriesType === 'candlestick') {
          const point = pointByDate.get(item.name)
          return point ? [`开 ${point.open} · 收 ${point.close} · 低 ${point.low} · 高 ${point.high}`] : []
        }
        const value = Array.isArray(item.value) ? item.value[1] : item.value
        return [`${item.seriesName}: ${value == null ? '--' : String(value)}`]
      })
      const structure = hovered?.structure
      const details = hovered ? [
        strategyOverlayLabel(hovered),
        ...(structure ? [
          `结构日 ${structure.type === 'STROKE_CANDIDATE' ? structure.end_date : structure.date}`,
          `确认日 ${structure.confirmed_at}`,
          `结构价 ${structure.type === 'STROKE_CANDIDATE' ? structure.end_price : structure.price}`,
        ] : []),
        ...[...hovered.failed_conditions, ...hovered.evidence.filter(condition => condition.passed)]
          .map(condition => `${condition.label}: ${condition.actual} · ${condition.required}`),
      ] : []
      return boundedTooltip([date, ...(group ? [strategyOverlayGroupSummary(group)] : []), ...details, ...lines,
        ...dayMarkers.filter(marker => marker !== hovered).map(strategyOverlayLabel)])
    },
  })
  const zoom = (visible: number): EChartsOption['dataZoom'] => {
    const last = Math.max(0, points.length - 1)
    const selected = Math.max(0, dates.indexOf(selectedDate))
    const clamp = (index: number) => Math.max(0, Math.min(last, Math.round(index)))
    let start = Math.max(0, Math.min(points.length - visible, selected - Math.floor(visible / 2)))
    let end = Math.min(last, start + visible - 1)
    if (view.window && Number.isFinite(view.window.start) && Number.isFinite(view.window.end)) {
      start = clamp(Math.min(view.window.start, view.window.end))
      end = clamp(Math.max(view.window.start, view.window.end))
      start = Math.max(start, end - visible + 1)
    }
    return [{
      id: 'overlay-window', type: 'slider', xAxisIndex: 0, bottom: 0, height: 18,
      startValue: start, endValue: end, rangeMode: ['value', 'value'],
      maxValueSpan: visible - 1, minValueSpan: Math.min(1, last),
      showDataShadow: false, showDetail: false, brushSelect: false,
      borderColor: theme.border, textStyle: { color: theme.text },
    }]
  }
  const base: EChartsOption = {
    animation: false,
    // Identical plot bounds keep each trading date vertically aligned across panels.
    grid: { left: 64, right: 16, top: 56, bottom: 40, containLabel: false },
    legend: {
      type: 'scroll', top: 0, left: 0, right: 0, selectedMode: true,
      itemWidth: 14, itemHeight: 8, itemGap: 12,
      textStyle: { color: theme.text, fontSize: 11 },
      pageTextStyle: { color: theme.text }, pageIconColor: theme.textStrong,
      pageIconInactiveColor: theme.border, pageIconSize: 10,
    },
    dataZoom: zoom(30),
    media: [
      { query: { maxWidth: 480 }, option: { dataZoom: zoom(10) } },
      { query: { minWidth: 481, maxWidth: 760 }, option: { dataZoom: zoom(16) } },
      { option: { dataZoom: zoom(30) } },
    ],
    xAxis: {
      type: 'category', data: dates,
      boundaryGap: true,
      axisLabel: { color: theme.text, fontSize: 10, hideOverlap: true, formatter: (value: string) => value.slice(5) },
      axisLine: { lineStyle: { color: theme.border } }, axisTick: { show: false },
    },
    yAxis: {
      type: 'value', scale: true,
      axisLabel: { color: theme.text, fontSize: 10,
        formatter: (value: number) => new Intl.NumberFormat('en-US', { notation: 'compact', maximumSignificantDigits: 4 }).format(value) },
      splitLine: { lineStyle: { color: theme.grid } },
    },
  }
  const observation = {
    xAxis: selectedDate,
    label: { formatter: `观察日 ${selectedDate}`, position: 'end' as const,
      rotate: 0, align: 'right' as const, color: theme.text, fontSize: 10 },
    lineStyle: { color: theme.crosshair, type: 'dashed' as const },
  }
  const markLine: LineSeriesOption['markLine'] = { silent: true, symbol: 'none', data: points.length ? [observation] : [] }
  const line = (key: typeof GRAPHICS_COLUMNS[number][0], name: string, color: string): LineSeriesOption => ({
    id: key, name, type: 'line', data: points.map(point => point[key] ?? null),
    connectNulls: false, smooth: false, symbol: points.length === 1 ? 'circle' : 'none', symbolSize: 5,
    lineStyle: { color, width: relatedLines[filters.strategy]?.includes(key) ? 3 : 1.5,
      type: key.startsWith('boll_') ? 'dashed' : 'solid' },
    itemStyle: { color }, emphasis: { focus: 'series' },
  })
  const anchor = (marker: StrategyOverlayMarker, panel: 'price' | 'macd' | 'volume') => {
    const point = pointByDate.get(marker.trade_date)
    if (panel === 'macd') return point?.macd_dif ?? null
    if (panel === 'volume') return point?.volume ?? null
    const structure = marker.structure
    return marker.kind === 'CHAN_STRUCTURE' && structure
      ? structure.type === 'STROKE_CANDIDATE' ? structure.end_price : structure.price : marker.price
  }
  const markerSeries = (panelMarkers: StrategyOverlayMarker[], panel: 'price' | 'macd' | 'volume'): ScatterSeriesOption[] => {
    if (!layers.strategies) return []
    const offsets = new Map<string, number>()
    for (const date of dates) {
      // Ascending anchors plus upward offsets cannot cross, even when structure prices differ.
      panelMarkers.filter(marker => marker.trade_date === date && anchor(marker, panel) !== null)
        .sort((a, b) => anchor(a, panel)! - anchor(b, panel)! || a.marker_id.localeCompare(b.marker_id))
        .forEach((marker, index) => offsets.set(marker.marker_id, -12 - index * 24))
    }
    return [{
      id: `${panel}-strategy-markers`, name: '策略观察', type: 'scatter', clip: true,
      symbolSize: 20, emphasis: { scale: false }, z: 5,
      data: panelMarkers.map(marker => {
        const { symbol, label, labelColor, name: _name, ...itemStyle } = strategyMarkerAppearance(marker)
        const selected = filters.strategy === marker.strategy_id
        const grey = ['NOT_TRIGGERED', 'NOT_AVAILABLE'].includes(marker.canonical_status)
        // Native arrow/pin symbols are tip-anchored; center their visible bodies in the stack.
        const centerOffset = symbol === 'arrow' ? -10 : symbol === 'pin' ? 8 : 0
        return {
          name: marker.trade_date, value: [marker.trade_date, anchor(marker, panel)], marker,
          symbol, symbolSize: symbol === 'arrow' ? [15, 20] : symbol === 'pin' ? [24, 20] : 20,
          symbolOffset: [0, (offsets.get(marker.marker_id) ?? 0) + centerOffset],
          itemStyle: { ...itemStyle, opacity: selected ? 1 : itemStyle.opacity },
          label: { show: true, position: 'inside', formatter: label, color: selected && grey ? theme.textStrong : labelColor,
            fontSize: 8, fontWeight: 'bold' },
        }
      }),
    }]
  }
  const paddedAxis = (panelMarkers: StrategyOverlayMarker[], panel: 'price' | 'macd' | 'volume'): EChartsOption['yAxis'] => {
    const maxStack = Math.max(0, ...dates.map(date => panelMarkers.filter(marker =>
      marker.trade_date === date && anchor(marker, panel) !== null).length))
    const reserve = maxStack ? Math.min(0.85, (34 + (maxStack - 1) * 24) / (panel === 'price' ? 224 : 104)) : 0.08
    const span = ({ min, max }: { min: number; max: number }) => Math.max(
      max - (panel === 'volume' ? 0 : min), Math.abs(max) * 0.02, 0.01)
    return {
      ...base.yAxis,
      min: panel === 'volume' ? 0 : values => values.min - span(values) * 0.08,
      max: values => values.max + span(values) * reserve / (1 - reserve),
    }
  }
  const colors = ['#ef4444', '#2563eb', '#d97706', '#16a34a', '#a855f7', '#0891b2', '#64748b', '#db2777']
  const priceLines = GRAPHICS_COLUMNS.slice(4, 11).filter(([key]) => key.startsWith('boll_')
    ? layers.boll : layers[key as 'ma5' | 'ma10' | 'ma20' | 'ma60'])
  const price: EChartsOption = {
    ...base, tooltip: tooltip(priceMarkers), yAxis: paddedAxis(priceMarkers, 'price'),
    legend: { ...base.legend, data: [
      ...(layers.candles && hasStrategyCandles(graphics) ? ['QFQ 日K'] : []),
      ...priceLines.map(([, name]) => name), ...(layers.strategies ? ['策略观察'] : []),
    ] },
    series: [
      ...(layers.candles && hasStrategyCandles(graphics) ? [
      { name: 'QFQ 日K', type: 'candlestick',
        data: points.map(point => [point.open!, point.close, point.low!, point.high!]),
        itemStyle: { color: '#ef4444', color0: '#16a34a', borderColor: '#ef4444', borderColor0: '#16a34a' } } as const,
      ] : []),
      ...priceLines.map(([key, name]) => line(key, name, colors[GRAPHICS_COLUMNS.findIndex(([column]) => column === key) - 3])),
      ...markerSeries(priceMarkers, 'price'),
      // A hidden close carrier gives the date line a real axis even with every layer switched off.
      { id: 'observation-date', name: '观察日', type: 'line', data: points.map(point => point.close),
        silent: true, symbol: 'none', lineStyle: { opacity: 0 }, itemStyle: { opacity: 0 },
        emphasis: { disabled: true }, tooltip: { show: false }, markLine },
    ],
  }
  const macd: EChartsOption = {
    ...base, tooltip: tooltip(macdMarkers), yAxis: paddedAxis(macdMarkers, 'macd'),
    series: [
      { ...line('macd_dif', 'DIF', '#2563eb'), markLine },
      line('macd_dea', 'DEA', '#d97706'),
      { name: 'MACD 柱值', type: 'bar', data: points.map(point => point.macd_hist), barMaxWidth: 14,
        itemStyle: { opacity: filters.strategy === 'macd_golden' ? 1 : 0.65,
          color: params => typeof params.value === 'number' && params.value < 0 ? '#16a34a' : '#ef4444' } },
      ...markerSeries(macdMarkers, 'macd'),
    ],
  }
  const volume: EChartsOption = {
    ...base, tooltip: tooltip(volumeMarkers), yAxis: paddedAxis(volumeMarkers, 'volume'),
    series: [
      { name: '相对量能', type: 'bar', data: points.map(point => point.volume ?? null), barMaxWidth: 18,
        itemStyle: { color: '#0891b2', opacity: volumeSelected ? 1 : 0.6 }, markLine },
      ...(['volume_ma5', 'volume_ma10'] as const).filter(key => points.some(point => point[key] !== undefined))
        .map((key, index) => line(key, key === 'volume_ma5' ? 'VOL5' : 'VOL10', index === 0 ? '#d97706' : '#2563eb')),
      ...markerSeries(volumeMarkers, 'volume'),
    ],
  }
  const rsi: EChartsOption = {
    ...base, tooltip: tooltip([]),
    yAxis: { ...base.yAxis, min: 0, max: 100 },
    series: [{
      ...line('rsi6', 'RSI6', '#0891b2'),
      markLine: { ...markLine, data: [...(points.length ? [observation] : []), { yAxis: 50,
        label: { formatter: '50', position: 'insideEndTop', color: theme.text },
        lineStyle: { color: theme.text, type: 'dashed' } }] },
    }],
  }
  return { price, macd, volume, rsi }
}
