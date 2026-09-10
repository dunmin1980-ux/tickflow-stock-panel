import type { EChartsOption, LineSeriesOption, ScatterSeriesOption, TooltipComponentOption } from 'echarts'
import type { ChartTheme } from './theme'

export interface StrategyGraphicsPoint {
  trade_date: string
  close: number
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
  ['close', '收盘'], ['ma5', 'MA5'], ['ma10', 'MA10'], ['ma20', 'MA20'], ['ma60', 'MA60'],
  ['boll_upper', 'BOLL 上轨'], ['boll_middle', 'BOLL 中轨'], ['boll_lower', 'BOLL 下轨'],
  ['macd_dif', 'DIF'], ['macd_dea', 'DEA'], ['macd_hist', 'MACD 柱值'], ['rsi6', 'RSI6'],
] as const

export function strategyMarkerLabel(marker: StrategyGraphicsMarker) {
  return `${marker.strategy_name} · ${STRATEGY_STATUS_LABELS[marker.status]} · ${marker.conditions_met}/${marker.conditions_total} · ${marker.price}`
}

export function buildStrategyGraphicsOptions(graphics: StrategyGraphicsReadyData, theme: ChartTheme): {
  price: EChartsOption; macd: EChartsOption; rsi: EChartsOption
} {
  const tooltip: TooltipComponentOption = {
    trigger: 'axis', confine: true, renderMode: 'richText',
    backgroundColor: theme.tooltipBg, borderColor: theme.tooltipBorder,
    textStyle: { color: theme.tooltipText, fontSize: 11 },
    axisPointer: { type: 'line', lineStyle: { color: theme.crosshair } },
    formatter(params) {
      const items = Array.isArray(params) ? params : [params]
      const markerStatuses = new Set<StrategyGraphicsMarker['status']>()
      const lines = items.flatMap(item => {
        const marker = (item.data as { marker?: StrategyGraphicsMarker } | undefined)?.marker
        if (marker) {
          markerStatuses.add(marker.status)
          return []
        }
        const value = Array.isArray(item.value) ? item.value[1] : item.value
        return [`${item.seriesName}: ${value == null ? '--' : String(value)}`]
      })
      const date = items[0]?.name ?? ''
      // Axis tooltips may return only one of several coincident scatter points.
      const markers = graphics.strategy_markers.filter(marker => marker.trade_date === date && markerStatuses.has(marker.status))
      return [date, ...lines, ...markers.map(strategyMarkerLabel)].join('\n')
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
      type: 'category', data: graphics.points.map(point => point.trade_date),
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
    name, type: 'line', data: graphics.points.map(point => point[key]),
    connectNulls: false, smooth: false, symbol: graphics.points.length === 1 ? 'circle' : 'none', symbolSize: 5,
    lineStyle: { color, width: key === 'close' ? 2 : 1.5, type: key.startsWith('boll_') ? 'dashed' : 'solid' },
    itemStyle: { color }, emphasis: { focus: 'series' },
  })
  const markerSeries = (status: StrategyGraphicsMarker['status']): ScatterSeriesOption => ({
    name: STRATEGY_STATUS_LABELS[status], type: 'scatter',
    symbol: status === 'TRIGGERED' ? 'triangle' : 'diamond', symbolSize: status === 'TRIGGERED' ? 12 : 10,
    itemStyle: { color: status === 'TRIGGERED' ? '#ef4444' : '#d97706', borderColor: theme.tooltipBg, borderWidth: 1 },
    z: 5,
    data: graphics.strategy_markers.filter(marker => marker.status === status).map(marker => ({
      name: marker.trade_date, value: [marker.trade_date, marker.price], marker: { ...marker },
    })),
  })
  const colors = ['#ef4444', '#2563eb', '#d97706', '#16a34a', '#a855f7', '#0891b2', '#64748b', '#db2777']
  const price: EChartsOption = {
    ...base,
    series: [
      ...GRAPHICS_COLUMNS.slice(0, 8).map(([key, name], index) => ({
        ...line(key, name, colors[index]), ...(key === 'close' ? { markLine } : {}),
      })),
      markerSeries('TRIGGERED'), markerSeries('NEAR_TRIGGER'),
    ],
  }
  const macd: EChartsOption = {
    ...base,
    series: [
      { ...line('macd_dif', 'DIF', '#2563eb'), markLine },
      line('macd_dea', 'DEA', '#d97706'),
      { name: 'MACD 柱值', type: 'bar', data: graphics.points.map(point => point.macd_hist), barMaxWidth: 14,
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
