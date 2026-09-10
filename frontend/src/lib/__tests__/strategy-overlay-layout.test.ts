// @vitest-environment node
import { graphic, init, type EChartsOption, type TooltipComponentOption } from 'echarts'
import { describe, expect, it } from 'vitest'
import { buildStrategyGraphicsOptions } from '../strategy-graphics'
import { chartTheme } from '../theme'
import { overlayWindowFixture } from '@/components/paper-trading/__tests__/strategyGraphicsFixtures'

describe('strategy overlay rendered layout', () => {
  it.each([390, 600, 1024])('keeps adjacent all-status glyphs separate at %spx while retaining 30 days and 188 observations', width => {
    const graphics = overlayWindowFixture()
    const { price } = buildStrategyGraphicsOptions(graphics, chartTheme('light'), { strategy: 'ALL', status: 'ALL' })
    const chart = init(null, undefined, { renderer: 'svg', ssr: true, width, height: 344 })
    try {
      chart.setOption(price)
      const option = chart.getOption() as EChartsOption
      const zoom = (option.dataZoom as { startValue: number; endValue: number; maxValueSpan: number }[])[0]
      expect(zoom.endValue - zoom.startValue).toBeLessThanOrEqual(width === 390 ? 9 : width === 600 ? 15 : 29)
      expect(zoom.maxValueSpan).toBe(width === 390 ? 9 : width === 600 ? 15 : 29)
      const series = (option.series as { name: string; data: { group: { markers: unknown[] } }[] }[]).find(series => series.name === '策略观察')!
      expect(series.data).toHaveLength(30)
      expect(series.data.reduce((total, item) => total + item.group.markers.length, 0)).toBe(188)
      const centers = graphics.points.slice(zoom.startValue, zoom.endValue + 1).map(point => chart.convertToPixel({ xAxisIndex: 0 }, point.trade_date) as number)
      for (let index = 1; index < centers.length; index++) expect(centers[index] - centers[index - 1]).toBeGreaterThan(22)
      const laneY = chart.convertToPixel({ yAxisIndex: 1 }, 1) as number
      expect(laneY - 30).toBe(74)
      expect(laneY - (laneY - 30 + 10)).toBeGreaterThan(10)
      expect(chart.renderToSVGString()).toContain('#ef4444')
      chart.resize({ width: 390 })
      expect((chart.getOption().dataZoom as { maxValueSpan: number }[])[0].maxValueSpan).toBe(9)
      chart.resize({ width: 1024 })
      expect((chart.getOption().dataZoom as { maxValueSpan: number }[])[0].maxValueSpan).toBe(29)
    } finally {
      chart.dispose()
    }
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
    expect(text).toContain('7 条观察')
    // ECharts 5 rich-text tooltips use a fixed 22px line height and ignore textStyle.width/overflow.
    const bounds = new graphic.Text({ style: { text, lineHeight: 22, padding: [8, 10] } }).getBoundingRect()
    expect(bounds.width).toBeLessThanOrEqual(280)
    expect(bounds.height).toBeLessThanOrEqual(324)
  })
})
