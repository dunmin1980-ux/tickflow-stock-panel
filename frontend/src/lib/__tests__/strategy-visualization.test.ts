import type { EChartsOption, TooltipComponentOption } from 'echarts'
import { describe, expect, it } from 'vitest'
import {
  buildStrategyGraphicsOptions, DEFAULT_CHART_LAYERS, DEFAULT_OVERLAY_FILTERS,
  groupStrategyOverlayMarkers, STRATEGY_MARKER_LEGEND, strategyMarkerAppearance,
  type StrategyOverlayMarker,
} from '../strategy-graphics'
import { chartTheme } from '../theme'
import { deepFreeze, overlayFixture, overlayWindowFixture } from '@/components/paper-trading/__tests__/strategyGraphicsFixtures'

type MarkerDatum = {
  name: string
  value: [string, number | null]
  marker: StrategyOverlayMarker
  symbol: string
  symbolOffset: [number, number]
  label: { formatter: string; color: string }
  itemStyle: { color: string; borderColor: string; opacity: number }
}
type ChartSeries = {
  name: string
  type: string
  data: unknown[]
  lineStyle?: { width: number; opacity?: number }
  itemStyle?: { opacity: number }
  markLine?: { data: { xAxis?: string }[] }
}

function series(option: EChartsOption) {
  return option.series as ChartSeries[]
}

function markers(option: EChartsOption) {
  return series(option).filter(item => item.type === 'scatter').flatMap(item => item.data as MarkerDatum[])
}

function fixture() {
  const graphics = overlayFixture()
  graphics.points = graphics.points.map((point, index) => ({ ...point, volume: [120000, 0][index] }))
  return graphics
}

const all = { strategy: 'ALL', status: 'ALL' } as const

describe('strategy visualization V2.1 options', () => {
  it('provides raw-volume bars, preserving zero and gaps without inventing a volume average or unit', () => {
    const graphics = fixture()
    graphics.points[0].volume = null
    const options = buildStrategyGraphicsOptions(deepFreeze(graphics), chartTheme('light'))
    expect(options).toHaveProperty('volume')
    expect(series(options.volume).find(item => item.type === 'bar')).toMatchObject({ data: [null, 0] })
    expect(series(options.volume).filter(item => /VOL|均量/.test(item.name))).toEqual([])
    expect(JSON.stringify(options.volume)).not.toMatch(/成交量（手）|成交量（股）|volume_ratio/)
  })

  it('leaves missing raw volume null and retains unplottable marker payloads for details', () => {
    const graphics = overlayFixture()
    graphics.points.forEach(point => { delete point.volume })
    const options = buildStrategyGraphicsOptions(graphics, chartTheme('light'), all)
    expect(options).toHaveProperty('volume')
    expect(series(options.volume).find(item => item.type === 'bar')?.data).toEqual([null, null])
    expect(markers(options.volume).map(item => item.value)).toEqual([
      ['2026-09-09', null], ['2026-09-09', null], ['2026-09-09', null],
    ])
    expect(markers(options.volume).map(item => item.marker.strategy_id).sort()).toEqual([
      'macd_golden', 'pullback_to_support', 'volume_price_surge',
    ])
  })

  it('plots only supplied volume averages verbatim, including nulls and zero', () => {
    const graphics = fixture()
    graphics.points[0].volume_ma5 = null
    graphics.points[1].volume_ma5 = 98765.4321
    let options = buildStrategyGraphicsOptions(graphics, chartTheme('light'))
    expect(options).toHaveProperty('volume')
    expect(series(options.volume).find(item => item.name === 'VOL5')).toMatchObject({ data: [null, 98765.4321], connectNulls: false })
    expect(series(options.volume).find(item => item.name === 'VOL10')).toBeUndefined()
    graphics.points[1].volume_ma10 = 0
    options = buildStrategyGraphicsOptions(graphics, chartTheme('light'))
    expect(series(options.volume).find(item => item.name === 'VOL10')).toMatchObject({ data: [null, 0], connectNulls: false })
  })

  it('places each price strategy and Chan structure on its original price, not a count lane', () => {
    const graphics = fixture()
    const { price } = buildStrategyGraphicsOptions(graphics, chartTheme('light'), all)
    const data = markers(price)
    expect(data.map(item => item.marker?.strategy_id).sort()).toEqual([
      'boll_breakout', 'boll_lower_reclaim', 'bullish_alignment', 'chan_daily_structure',
      'chan_daily_structure', 'pullback_to_support',
    ])
    expect(data.filter(item => item.marker.kind === 'STRATEGY').map(item => item.value)).toEqual(
      Array.from({ length: 4 }, () => ['2026-09-09', 18.6]))
    expect(data.filter(item => item.marker.kind === 'CHAN_STRUCTURE').map(item => item.value)).toEqual([
      ['2026-09-09', 18.5], ['2026-09-09', 18.5],
    ])
    expect(Array.isArray(price.yAxis)).toBe(false)
    for (const item of data) {
      expect(item.name).toBe(item.marker.trade_date)
      expect(item.marker).toBe(graphics.overlay_markers!.find(marker => marker.marker_id === item.marker.marker_id))
      expect(item).not.toHaveProperty('group')
    }
  })

  it('plots MACD at supplied DIF and volume evidence at raw volume, without RSI strategy markers', () => {
    const graphics = fixture()
    const options = buildStrategyGraphicsOptions(graphics, chartTheme('light'), all)
    expect(markers(options.macd).map(item => [item.marker.strategy_id, item.value])).toEqual([
      ['macd_golden', ['2026-09-09', 0.0105]],
    ])
    expect(markers(options.volume).map(item => [item.marker.strategy_id, item.value])).toEqual([
      ['macd_golden', ['2026-09-09', 0]],
      ['volume_price_surge', ['2026-09-09', 0]],
      ['pullback_to_support', ['2026-09-09', 0]],
    ])
    expect(markers(options.rsi)).toEqual([])
    graphics.points[1].macd_dif = null
    expect(markers(buildStrategyGraphicsOptions(graphics, chartTheme('light'), all).macd)[0].value).toEqual(['2026-09-09', null])
  })

  it('exports seven distinct type shapes and shortcodes that survive canonical status changes', () => {
    expect(strategyMarkerAppearance).toBeTypeOf('function')
    expect(STRATEGY_MARKER_LEGEND).toBeTypeOf('object')
    const graphics = fixture()
    const ids = [...new Set(graphics.overlay_markers!.map(marker => marker.strategy_id))]
    const appearances = ids.map(id => strategyMarkerAppearance(graphics.overlay_markers!.find(marker => marker.strategy_id === id)!))
    expect(new Set(appearances.map(item => item.symbol)).size).toBe(7)
    expect(new Set(appearances.map(item => item.label)).size).toBe(7)
    for (const marker of graphics.overlay_markers!) {
      const appearance = strategyMarkerAppearance(marker)
      expect(appearance.label).toMatch(/^[A-Z]{2,3}$/)
      for (const canonical_status of ['TRIGGERED', 'NEAR_TRIGGER', 'NOT_TRIGGERED', 'NOT_AVAILABLE'] as const) {
        expect(strategyMarkerAppearance({ ...marker, canonical_status })).toMatchObject({ symbol: appearance.symbol, label: appearance.label })
      }
    }
  })

  it('encodes all four statuses independently through fill, stroke and opacity without confidence fields', () => {
    expect(strategyMarkerAppearance).toBeTypeOf('function')
    const marker = fixture().overlay_markers![0]
    const appearances = ['TRIGGERED', 'NEAR_TRIGGER', 'NOT_TRIGGERED', 'NOT_AVAILABLE'].map(canonical_status =>
      strategyMarkerAppearance({ ...marker, canonical_status: canonical_status as StrategyOverlayMarker['canonical_status'] }))
    expect(new Set(appearances.map(({ color, borderColor, opacity, borderType }) => JSON.stringify({ color, borderColor, opacity, borderType }))).size).toBe(4)
    expect(appearances[2].opacity).toBeLessThan(appearances[0].opacity)
    expect(JSON.stringify(appearances)).not.toMatch(/confidence|probability|score|condition_ratio/)
  })

  it.each(['light', 'dark'] as const)('makes selected grey-status markers legible in the %s theme without recategorizing them', mode => {
    const theme = chartTheme(mode)
    const options = buildStrategyGraphicsOptions(fixture(), theme, { strategy: 'bullish_alignment', status: 'ALL' })
    const datum = markers(options.price)[0]
    expect(datum.itemStyle.opacity).toBe(1)
    expect(datum.label.color).toBe(theme.textStrong)
    expect(datum.marker.canonical_status).toBe('NOT_TRIGGERED')
    expect(datum.itemStyle.borderColor).toBe('#71717a')
  })

  it('preserves all original evidence, identifiers and date groups while defaulting to active strategies and Chan', () => {
    const graphics = deepFreeze(fixture())
    const before = JSON.stringify(graphics)
    const options = buildStrategyGraphicsOptions(graphics, chartTheme('dark'))
    const data = Object.values(options).flatMap(markers)
    data.forEach(item => expect(item).toHaveProperty('marker'))
    expect(new Set(data.map(item => item.marker.marker_id)).size).toBe(5)
    const groups = groupStrategyOverlayMarkers(graphics, DEFAULT_OVERLAY_FILTERS)
    expect(groups[0].markers).toHaveLength(5)
    for (const datum of data) {
      expect(datum.marker).toEqual(graphics.overlay_markers!.find(marker => marker.marker_id === datum.marker.marker_id))
      expect(datum.label.formatter).not.toMatch(/^\d+$/)
    }
    expect(JSON.stringify(graphics)).toBe(before)
    expect(data.filter(item => item.marker.kind === 'CHAN_STRUCTURE').every(item =>
      item.marker.canonical_status === 'NOT_AVAILABLE' && item.marker.conditions_met === null && item.marker.conditions_total === null)).toBe(true)
  })

  it('uses the selected observation date on all four charts and keeps the entire 30-point input', () => {
    const graphics = overlayWindowFixture()
    const options = buildStrategyGraphicsOptions(graphics, chartTheme('light'), all, { selectedObservationDate: '2026-08-13' })
    for (const option of Object.values(options)) {
      expect(option.xAxis).toMatchObject({ data: graphics.points.map(point => point.trade_date) })
      expect(series(option).flatMap(item => item.markLine?.data ?? []).filter(item => item.xAxis)).toEqual([
        expect.objectContaining({ xAxis: '2026-08-13' }),
      ])
      const media = option.media!.find(item => item.query?.maxWidth === 480)!
      expect(media.option.dataZoom).toEqual([expect.objectContaining({ startValue: 0, endValue: 9, maxValueSpan: 9 })])
      expect(series(option).filter(item => item.type !== 'scatter').every(item => item.data.length === 30)).toBe(true)
    }
    const ids = new Set(Object.values(options).flatMap(markers).map(item => item.marker.marker_id))
    expect(ids.size).toBe(188)
  })

  it('interprets shared windows as point indices, not percentages, on every chart', () => {
    const options = buildStrategyGraphicsOptions(overlayWindowFixture(), chartTheme('light'), all, {
      selectedObservationDate: '2026-08-13', window: { start: 5, end: 14 },
    })
    for (const option of Object.values(options)) {
      expect(option.dataZoom).toEqual([expect.objectContaining({ startValue: 5, endValue: 14 })])
      expect(option.media!.find(item => item.query?.maxWidth === 480)!.option.dataZoom).toEqual([
        expect.objectContaining({ startValue: 5, endValue: 14 }),
      ])
    }
  })

  it('hides all requested layers without losing observation lines or mutating the data', () => {
    expect(DEFAULT_CHART_LAYERS).toMatchObject({ candles: true, ma5: true, ma10: false, ma20: true, ma60: false, boll: false, strategies: true })
    const graphics = deepFreeze(fixture())
    const options = buildStrategyGraphicsOptions(graphics, chartTheme('light'), all, {
      selectedObservationDate: '2026-09-08', layers: {
        candles: false, ma5: false, ma10: false, ma20: false, ma60: false, boll: false, strategies: false,
      },
    })
    expect(series(options.price).map(item => item.name)).toEqual(['观察日'])
    for (const option of Object.values(options)) {
      expect(markers(option)).toEqual([])
      expect(series(option).flatMap(item => item.markLine?.data ?? [])).toContainEqual(expect.objectContaining({ xAxis: '2026-09-08' }))
    }
    expect(graphics.overlay_markers).toHaveLength(8)
  })

  it.each([
    ['bullish_alignment', 'price', ['MA5', 'MA10', 'MA20', 'MA60']],
    ['pullback_to_support', 'price', ['MA5', 'MA10', 'MA20']],
    ['boll_breakout', 'price', ['BOLL 上轨', 'BOLL 中轨', 'BOLL 下轨']],
    ['boll_lower_reclaim', 'price', ['BOLL 上轨', 'BOLL 中轨', 'BOLL 下轨']],
    ['macd_golden', 'macd', ['DIF', 'DEA']],
  ] as const)('increases %s evidence line weights without changing values', (strategy, chart, names) => {
    const graphics = fixture()
    const layers = { candles: true, ma5: true, ma10: true, ma20: true, ma60: true, boll: true, strategies: true }
    const baseline = buildStrategyGraphicsOptions(graphics, chartTheme('light'), all, { layers })
    const selected = buildStrategyGraphicsOptions(graphics, chartTheme('light'), { strategy, status: 'ALL' }, { layers })
    for (const name of names) {
      const before = series(baseline[chart]).find(item => item.name === name)!
      const after = series(selected[chart]).find(item => item.name === name)!
      expect(after.lineStyle!.width).toBeGreaterThan(before.lineStyle!.width)
      expect(after.data).toEqual(before.data)
    }
    expect(Object.values(selected).flatMap(markers).every(item => item.marker.strategy_id === strategy)).toBe(true)
  })

  it.each(['macd_golden', 'pullback_to_support', 'volume_price_surge'])('increases raw volume weight for %s', strategy => {
    const graphics = fixture()
    const baseline = buildStrategyGraphicsOptions(graphics, chartTheme('light'), all)
    const selected = buildStrategyGraphicsOptions(graphics, chartTheme('light'), { strategy, status: 'ALL' })
    expect(selected).toHaveProperty('volume')
    const before = series(baseline.volume).find(item => item.type === 'bar')!
    const after = series(selected.volume).find(item => item.type === 'bar')!
    expect(after.itemStyle!.opacity).toBeGreaterThan(before.itemStyle!.opacity)
    expect(after.data).toEqual(before.data)
  })

  it('keeps original conditions in a non-HTML marker tooltip without invented research or paper outcomes', () => {
    const graphics = fixture()
    const { price } = buildStrategyGraphicsOptions(graphics, chartTheme('light'), { strategy: 'pullback_to_support', status: 'ALL' })
    const datum = markers(price)[0]
    expect(datum).toHaveProperty('marker')
    expect(price.tooltip).toMatchObject({ confine: true, renderMode: 'richText' })
    const formatter = (price.tooltip as TooltipComponentOption).formatter!
    const text = typeof formatter === 'function'
      ? String(formatter({ name: datum.name, value: datum.value, data: datum } as never, '', () => {})).replace(/\n/g, '') : ''
    expect(text).toContain('量比 > 1.5')
    expect(text).toContain('量比=1.38')
    expect(text).toContain('3/4')
    expect(text).not.toMatch(/<br|<div|confidence|probability|历史真实信号|历史成交|HOLD|WATCH/)
    expect(datum.marker.evidence).toEqual(graphics.overlay_markers![4].evidence)
  })
})
