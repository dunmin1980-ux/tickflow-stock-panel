import type { EChartsOption, TooltipComponentOption } from 'echarts'
import { describe, expect, it } from 'vitest'
import { buildStrategyGraphicsOptions } from '../strategy-graphics'
import { chartTheme } from '../theme'
import { deepFreeze, graphicsFixture, overlayFixture } from '@/components/paper-trading/__tests__/strategyGraphicsFixtures'

function series(option: EChartsOption, name: string) {
  return (option.series as { name: string; data: unknown[] }[]).find(item => item.name === name)
}

function tooltipText(option: EChartsOption, params: unknown) {
  const formatter = (option.tooltip as TooltipComponentOption).formatter
  expect(formatter).toBeTypeOf('function')
  // Ignore visual wrapping here; the real renderer layout tests check the tooltip bounds.
  return typeof formatter === 'function' ? String(formatter(params as never, '', () => {})).replace(/\n/g, '') : ''
}

describe('strategy graphics options', () => {
  it('passes every supplied price indicator through without recalculating or filling nulls', () => {
    const { price } = buildStrategyGraphicsOptions(graphicsFixture(), chartTheme('light'))
    expect(price.xAxis).toMatchObject({ type: 'category', data: ['2026-09-08', '2026-09-09'] })
    for (const [name, values] of [
      ['MA5', [18.1, 18.35]], ['MA10', [18.2, 18.26]],
      ['MA20', [18.3, 18.31]], ['MA60', [null, 17.8]], ['BOLL 上轨', [19.5, 19.6]],
      ['BOLL 中轨', [18.3, 18.31]], ['BOLL 下轨', [17.1, 17.02]],
    ] as const) {
      expect(series(price, name)).toMatchObject({ type: 'line', data: values, connectNulls: false })
    }
    expect(series(price, 'QFQ 日K')).toMatchObject({ type: 'candlestick', data: [[18, 18.25, 17.9, 18.5], [18.7, 18.6, 18.4, 18.8]] })
    expect(series(price, '收盘')).toBeUndefined()
  })

  it('preserves exact MACD DIF DEA and histogram values including zero and signs', () => {
    const { macd } = buildStrategyGraphicsOptions(graphicsFixture(), chartTheme('dark'))
    expect(series(macd, 'DIF')).toMatchObject({ type: 'line', data: [0, 0.0105] })
    expect(series(macd, 'DEA')).toMatchObject({ type: 'line', data: [0.03, 0.047] })
    expect(series(macd, 'MACD 柱值')).toMatchObject({ type: 'bar', data: [-0.06, -0.073] })
  })

  it('plots supplied RSI6 on a 0 to 100 scale with exactly the 50 reference threshold', () => {
    const { rsi } = buildStrategyGraphicsOptions(graphicsFixture(), chartTheme('light'))
    expect(rsi.yAxis).toMatchObject({ min: 0, max: 100 })
    expect(series(rsi, 'RSI6')).toMatchObject({ type: 'line', data: [null, 50.0891],
      markLine: { data: expect.arrayContaining([expect.objectContaining({ yAxis: 50 })]) } })
  })

  it('highlights the actual observation date on all three charts', () => {
    const options = buildStrategyGraphicsOptions(graphicsFixture(), chartTheme('dark'))
    for (const [option, name] of [[options.price, 'QFQ 日K'], [options.macd, 'DIF'], [options.rsi, 'RSI6']] as const) {
      expect(series(option, name)).toMatchObject({ markLine: {
        data: expect.arrayContaining([expect.objectContaining({ xAxis: '2026-09-09',
          label: expect.objectContaining({ position: 'end', rotate: 0, align: 'right' }) })]),
      } })
    }
  })

  it('uses original marker date price status and counts without inferring trades', () => {
    const { price } = buildStrategyGraphicsOptions(graphicsFixture(), chartTheme('light'))
    expect(series(price, '策略观察')).toMatchObject({ type: 'scatter', data: [
      expect.objectContaining({ name: '2026-09-08', group: { trade_date: '2026-09-08', markers: [expect.objectContaining({
        strategy_id: 'macd_golden', canonical_status: 'TRIGGERED', price: 18.25, conditions_met: 4, conditions_total: 4,
      })] } }),
      expect.objectContaining({ name: '2026-09-09', group: { trade_date: '2026-09-09', markers: [expect.objectContaining({
        strategy_id: 'pullback_to_support', canonical_status: 'NEAR_TRIGGER', price: 18.6, conditions_met: 3, conditions_total: 4,
      })] } }),
    ] })
  })

  it('formats exact numeric and marker evidence in a confined non-HTML tooltip', () => {
    const graphics = graphicsFixture()
    const { price, macd } = buildStrategyGraphicsOptions(graphics, chartTheme('light'))
    expect(price.tooltip).toMatchObject({ trigger: 'axis', confine: true, renderMode: 'richText' })
    const marker = series(price, '策略观察')!.data[1]
    expect(tooltipText(price, [{ axisValue: '2026-09-09', name: '2026-09-09', seriesName: '接近触发',
      value: ['2026-09-09', 18.6], data: marker }])).toContain('回踩支撑 · 接近触发 · 3/4 · 18.6')
    const values = tooltipText(macd, [{ axisValue: '2026-09-09', name: '2026-09-09', seriesName: 'DIF', value: 0.0105 },
      { name: '2026-09-09', seriesName: 'DEA', value: null },
      { name: '2026-09-09', seriesName: 'MACD 柱值', value: 0 }])
    expect(values).toContain('DIF: 0.0105')
    expect(values).toContain('DEA: --')
    expect(values).toContain('MACD 柱值: 0')
  })

  it('retains all simultaneous strategy observations when the axis returns only one marker per series', () => {
    const graphics = graphicsFixture()
    graphics.strategy_markers.push({ trade_date: '2026-09-09', strategy_id: 'boll_breakout',
      strategy_name: 'BOLL 突破', status: 'NEAR_TRIGGER', price: 18.6, conditions_met: 2, conditions_total: 4 })
    const { price } = buildStrategyGraphicsOptions(deepFreeze(graphics), chartTheme('light'))
    const marker = series(price, '策略观察')!.data[1]
    const text = tooltipText(price, [{ name: '2026-09-09', seriesName: '收盘', value: 18.6 },
      { name: '2026-09-09', seriesName: '接近触发', value: ['2026-09-09', 18.6], data: marker }])
    expect(text).toContain('收盘: 18.6')
    expect(text).toContain('回踩支撑 · 接近触发 · 3/4 · 18.6')
    expect(text).toContain('BOLL 突破 · 接近触发 · 2/4 · 18.6')
    expect(text).not.toContain('MACD 金叉放量')
  })

  it('reserves separate space for a scrollable legend and plot in each theme', () => {
    for (const mode of ['light', 'dark'] as const) {
      const theme = chartTheme(mode)
      const options = buildStrategyGraphicsOptions(graphicsFixture(), theme)
      for (const option of Object.values(options)) {
        expect(option.legend).toMatchObject({ type: 'scroll', top: 0, selectedMode: true,
          textStyle: { color: theme.text } })
        expect(option.grid).toMatchObject({ containLabel: true })
        expect((option.grid as { top: number }).top).toBeGreaterThanOrEqual(48)
        expect(option.tooltip).toMatchObject({ textStyle: { color: theme.tooltipText } })
      }
    }
  })

  it('keeps all-null indicators as gaps and does not mutate the source snapshot', () => {
    const graphics = graphicsFixture()
    graphics.points.forEach(point => Object.assign(point, {
      ma5: null, ma10: null, ma20: null, ma60: null, boll_upper: null, boll_middle: null,
      boll_lower: null, macd_dif: null, macd_dea: null, macd_hist: null, rsi6: null,
    }))
    const before = JSON.stringify(graphics)
    const options = buildStrategyGraphicsOptions(deepFreeze(graphics), chartTheme('light'))
    expect(series(options.price, 'MA5')?.data).toEqual([null, null])
    expect(series(options.macd, 'DIF')?.data).toEqual([null, null])
    expect(series(options.macd, 'MACD 柱值')?.data).toEqual([null, null])
    expect(series(options.rsi, 'RSI6')?.data).toEqual([null, null])
    expect(JSON.stringify(graphics)).toBe(before)
  })

  it('does not fabricate points or markers for an empty snapshot', () => {
    const graphics = { ...graphicsFixture(), points: [], strategy_markers: [] }
    const options = buildStrategyGraphicsOptions(graphics, chartTheme('light'))
    expect(options.price.series).toEqual([])
    expect(series(options.macd, 'DIF')?.data).toEqual([])
    expect(series(options.rsi, 'RSI6')?.data).toEqual([])
  })

  it('groups all active observations and confirmed Chan into one count glyph per date above the candles', () => {
    const graphics = deepFreeze(overlayFixture())
    const { price } = buildStrategyGraphicsOptions(graphics, chartTheme('light'))
    const markerSeries = series(price, '策略观察')!
    expect(markerSeries).toMatchObject({ type: 'scatter', yAxisIndex: 1, clip: false,
      symbolOffset: [0, -30], symbolSize: 20, emphasis: { scale: false },
      data: [expect.objectContaining({ name: '2026-09-09', value: ['2026-09-09', 1],
        group: { trade_date: '2026-09-09', markers: [graphics.overlay_markers![0], graphics.overlay_markers![2],
          graphics.overlay_markers![4], graphics.overlay_markers![6], graphics.overlay_markers![7]] } })] })
    expect(price.yAxis).toEqual(expect.arrayContaining([expect.objectContaining({ min: 0, max: 1, show: false })]))
    expect((price.grid as { top: number }).top).toBeGreaterThanOrEqual(104)
    expect(price.media).toEqual(expect.arrayContaining([expect.objectContaining({ query: { maxWidth: 480 },
      option: expect.objectContaining({ dataZoom: expect.any(Array) }) })]))
  })

  it.each([
    ['ALL', 'ACTIVE', ['macd_golden', 'boll_breakout', 'pullback_to_support', 'chan_daily_structure', 'chan_daily_structure']],
    ['ALL', 'ALL', ['macd_golden', 'bullish_alignment', 'boll_breakout', 'volume_price_surge', 'pullback_to_support', 'boll_lower_reclaim', 'chan_daily_structure', 'chan_daily_structure']],
    ['ALL', 'TRIGGERED', ['macd_golden']],
    ['ALL', 'NEAR_TRIGGER', ['boll_breakout', 'pullback_to_support']],
    ['chan_daily_structure', 'ACTIVE', ['chan_daily_structure', 'chan_daily_structure']],
    ['chan_daily_structure', 'TRIGGERED', []],
    ['chan_daily_structure', 'NEAR_TRIGGER', []],
    ['volume_price_surge', 'ACTIVE', []],
    ['volume_price_surge', 'ALL', ['volume_price_surge']],
    ['pullback_to_support', 'TRIGGERED', []],
    ['pullback_to_support', 'NEAR_TRIGGER', ['pullback_to_support']],
  ] as const)('intersects strategy %s with status %s without re-evaluating research', (strategy, status, ids) => {
    const graphics = deepFreeze(overlayFixture())
    const { price } = buildStrategyGraphicsOptions(graphics, chartTheme('light'), { strategy, status })
    const items = series(price, '策略观察')!.data as { group: { markers: { strategy_id: string }[] } }[]
    expect(items.flatMap(item => item.group.markers.map(marker => marker.strategy_id))).toEqual(ids)
  })

  it('uses overlay data as authoritative even when empty and ignores markers outside the last 30 supplied days', () => {
    const graphics = overlayFixture()
    const point = graphics.points[1]
    graphics.points = Array.from({ length: 31 }, (_, i) => ({ ...point, trade_date: `2026-08-${String(i + 1).padStart(2, '0')}` }))
    graphics.trade_date = '2026-08-31'
    graphics.overlay_markers = [
      { ...graphics.overlay_markers![0], trade_date: '2026-08-01' },
      { ...graphics.overlay_markers![1], trade_date: '2026-08-02', canonical_status: 'TRIGGERED' },
      { ...graphics.overlay_markers![2], trade_date: '2026-09-01' },
    ]
    const { price } = buildStrategyGraphicsOptions(graphics, chartTheme('light'))
    expect(price.xAxis).toMatchObject({ data: graphics.points.slice(1).map(p => p.trade_date) })
    expect(series(price, '策略观察')!.data).toEqual([expect.objectContaining({ name: '2026-08-02' })])
    expect(series(buildStrategyGraphicsOptions({ ...graphics, overlay_markers: [] }, chartTheme('light')).price, '策略观察')!.data).toEqual([])
  })

  it('shows group summary even when hovering a candle without scatter params and bounds tooltip content', () => {
    const { price } = buildStrategyGraphicsOptions(overlayFixture(), chartTheme('light'))
    const text = tooltipText(price, [{ name: '2026-09-09', seriesName: 'QFQ 日K', seriesType: 'candlestick',
      value: [1, 18.7, 18.6, 18.4, 18.8], data: [18.7, 18.6, 18.4, 18.8] }])
    expect(text).toContain('开 18.7 · 收 18.6 · 低 18.4 · 高 18.8')
    expect(text).toContain('5 条观察')
    expect(text).toContain('已触发 1 · 接近触发 2 · 结构 2')
    expect(price.tooltip).toMatchObject({ confine: true, renderMode: 'richText' })
    expect(String(text).length).toBeLessThan(500)
  })

  it.each(['open', 'high', 'low'] as const)('never fabricates candles when %s is missing', key => {
    const graphics = graphicsFixture()
    delete graphics.points[0][key]
    const options = buildStrategyGraphicsOptions(graphics, chartTheme('light'))
    expect(options.price.series).toEqual([])
    expect(series(options.macd, 'DIF')!.data).toEqual([0, 0.0105])
  })

  it('does not plot invalid non-finite OHLC', () => {
    const graphics = graphicsFixture()
    graphics.points[0].high = Number.NaN
    expect(buildStrategyGraphicsOptions(graphics, chartTheme('light')).price.series).toEqual([])
  })
})
