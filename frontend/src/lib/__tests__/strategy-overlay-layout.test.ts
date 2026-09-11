// @vitest-environment node
import { graphic, init, type EChartsOption, type TooltipComponentOption } from 'echarts'
import { describe, expect, it } from 'vitest'
import { buildStrategyGraphicsOptions, DEFAULT_CHART_LAYERS, type StrategyOverlayMarker } from '../strategy-graphics'
import { chartTheme } from '../theme'
import { overlayWindowFixture } from '@/components/paper-trading/__tests__/strategyGraphicsFixtures'

describe('strategy overlay rendered layout', () => {
  it.each([366, 1180])('aligns each date at the exact same pixel across all four charts at %spx', width => {
    const graphics = overlayWindowFixture()
    graphics.points.forEach(point => { point.volume = 123456789; point.close = 18.123456789 })
    const options = buildStrategyGraphicsOptions(graphics, chartTheme('dark'))
    const positions: number[][] = []
    for (const option of Object.values(options)) {
      const chart = init(null, undefined, { renderer: 'svg', ssr: true, width, height: 260 })
      try {
        chart.setOption(option)
        positions.push(graphics.points.slice(-5).map(point => chart.convertToPixel({ xAxisIndex: 0 }, point.trade_date) as number))
      } finally { chart.dispose() }
    }
    for (const panel of positions.slice(1)) expect(panel).toEqual(positions[0])
  })

  it('renders the selected date on every mobile chart even with candles hidden and missing indicator data', () => {
    const graphics = overlayWindowFixture()
    graphics.points.forEach(point => Object.assign(point, { volume: null, macd_dif: null, macd_dea: null, macd_hist: null, rsi6: null }))
    const options = buildStrategyGraphicsOptions(graphics, chartTheme('dark'), undefined, {
      selectedObservationDate: '2026-08-13', layers: {
        ...DEFAULT_CHART_LAYERS, candles: false, ma5: false, ma20: false, strategies: false,
      },
    })
    for (const [panel, option] of Object.entries(options)) {
      const chart = init(null, undefined, { renderer: 'svg', ssr: true, width: 390, height: panel === 'price' ? 344 : 224 })
      try {
        chart.setOption(option, { notMerge: true })
        expect(chart.renderToSVGString(), panel).toContain('观察日 2026-08-13')
        expect((chart.getOption().dataZoom as { startValue: number; endValue: number }[])[0]).toMatchObject({ startValue: 0, endValue: 9 })
      } finally {
        chart.dispose()
      }
    }
  })

  it.each([390, 600, 1440])('keeps all semantic glyphs physically separate at %spx on their evidence charts', width => {
    const graphics = overlayWindowFixture()
    graphics.points.forEach(point => { point.volume = 120000 })
    const options = buildStrategyGraphicsOptions(graphics, chartTheme('light'), { strategy: 'ALL', status: 'ALL' })
    const identities = new Set<string>()
    for (const [panel, input] of Object.entries(options)) {
      const height = panel === 'price' ? 344 : 224
      const chart = init(null, undefined, { renderer: 'svg', ssr: true, width, height })
      try {
        chart.setOption(input)
        const option = chart.getOption() as EChartsOption
        const zoom = (option.dataZoom as { startValue: number; endValue: number; maxValueSpan: number }[])[0]
        expect(zoom, panel).toBeDefined()
        expect(zoom.endValue - zoom.startValue).toBeLessThanOrEqual(width === 390 ? 9 : width === 600 ? 15 : 29)
        expect(zoom.maxValueSpan).toBe(width === 390 ? 9 : width === 600 ? 15 : 29)
        const scatter = (option.series as { type: string; data: { marker: StrategyOverlayMarker }[] }[]).filter(series => series.type === 'scatter')
        scatter.flatMap(series => series.data).forEach(item => {
          expect(item).toHaveProperty('marker')
          identities.add(item.marker.marker_id)
        })
        const centers = graphics.points.slice(zoom.startValue, zoom.endValue + 1).map(point => chart.convertToPixel({ xAxisIndex: 0 }, point.trade_date) as number)
        for (let index = 1; index < centers.length; index++) expect(centers[index] - centers[index - 1]).toBeGreaterThan(22)
        expect(chart.renderToSVGString()).toContain('<path')
        const model = (chart as unknown as { getModel(): { getSeries(): {
          subType: string
          getData(): { count(): number; getItemGraphicEl(index: number): InstanceType<typeof graphic.Group> | undefined }
        }[] } }).getModel()
        const bounds = model.getSeries().filter(series => series.subType === 'scatter').flatMap(series => {
          const data = series.getData()
          return Array.from({ length: data.count() }, (_, index) => data.getItemGraphicEl(index)).flatMap(element => {
            if (!element) return []
            const box = element.getBoundingRect().clone()
            const transform = element.getComputedTransform()
            if (transform) box.applyTransform(transform)
            return [box]
          })
        })
        if (panel !== 'rsi') expect(bounds.length).toBeGreaterThan(0)
        for (let i = 0; i < bounds.length; i++) {
          const box = bounds[i]
          expect(box.width, `${panel} glyph width`).toBeGreaterThan(10)
          expect(box.y, `${panel} glyph clears legend`).toBeGreaterThanOrEqual(36)
          expect(box.x).toBeGreaterThanOrEqual(0)
          expect(box.x + box.width).toBeLessThanOrEqual(width)
          expect(box.y + box.height).toBeLessThanOrEqual(height - 28)
          for (const other of bounds.slice(i + 1)) expect(box.intersect(other),
            `${panel} overlapping glyphs: ${JSON.stringify(box)} / ${JSON.stringify(other)}`).toBe(false)
        }
        chart.resize({ width: 390 })
        expect((chart.getOption().dataZoom as { maxValueSpan: number }[])[0].maxValueSpan).toBe(9)
        chart.resize({ width: 1440 })
        expect((chart.getOption().dataZoom as { maxValueSpan: number }[])[0].maxValueSpan).toBe(29)
      } finally {
        chart.dispose()
      }
    }
    expect(identities.size).toBe(188)
  })

  it('bounds the actual rich-text tooltip box with all price series and long aggregate entries', () => {
    const graphics = overlayWindowFixture()
    graphics.overlay_markers!.forEach(marker => { marker.strategy_name = '很长的原始策略名称'.repeat(12) })
    const { price } = buildStrategyGraphicsOptions(graphics, chartTheme('light'), { strategy: 'ALL', status: 'ALL' })
    const params = (price.series as { name: string; type: string; data: unknown[] }[]).map(series => ({
      name: '2026-09-09', seriesName: series.name, seriesType: series.type, value: series.data.at(-1), data: series.data.at(-1),
    }))
    const formatter = (price.tooltip as TooltipComponentOption).formatter
    expect(formatter).toBeTypeOf('function')
    const text = typeof formatter === 'function' ? String(formatter(params as never, '', () => {})) : ''
    expect(text).toContain('5 条观察')
    // ECharts 5 rich-text tooltips use a fixed 22px line height and ignore textStyle.width/overflow.
    const bounds = new graphic.Text({ style: { text, lineHeight: 22, padding: [8, 10] } }).getBoundingRect()
    expect(bounds.width).toBeLessThanOrEqual(280)
    expect(bounds.height).toBeLessThanOrEqual(324)
  })
})
