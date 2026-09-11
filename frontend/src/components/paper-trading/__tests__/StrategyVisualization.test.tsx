import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { StrategyGraphics } from '../StrategyGraphics'
import { StrategyOverviewCards } from '../StrategyOverviewCards'
import { StrategyVisualization } from '../StrategyVisualization'
import { engineFixture, overlayFixture, overlayWindowFixture } from './strategyGraphicsFixtures'

const charts = vi.hoisted(() => [] as { root: HTMLElement; option: any; handlers: Map<string, Function> }[])
vi.mock('echarts', async original => ({
  ...await original<typeof import('echarts')>(),
  init(root: HTMLElement) {
    const chart = { root, option: null as any, handlers: new Map<string, Function>(),
      setOption(option: any) { this.option = option },
      on(name: string, fn: Function) { this.handlers.set(name, fn) },
      off(name: string) { this.handlers.delete(name) },
      resize: vi.fn(), dispose: vi.fn(),
    }
    charts.push(chart)
    return chart
  },
}))

beforeEach(() => { charts.length = 0 })
const latest = (name: string) => charts.filter(chart => chart.root.getAttribute('aria-label') === name).at(-1)!
const names = ['价格与均线图', '成交量图', 'MACD 图', 'RSI6 图']

it('renders relative volume without guessing authoritative volume units', () => {
  render(<StrategyGraphics graphics={overlayFixture()} requestedDate="2026-09-09" />)
  expect(screen.getByRole('img', { name: '成交量图' })).toBeVisible()
  expect(screen.getAllByText(/相对量能/).length).toBeGreaterThan(0)
  expect(screen.queryByText(/成交量.*(手|股)/)).not.toBeInTheDocument()
})

it('uses one selected observation date for every chart, values, evidence and published paper record', () => {
  render(<StrategyGraphics graphics={overlayFixture()} requestedDate="2026-09-09" />)
  fireEvent.change(screen.getByRole('combobox', { name: '观察日期' }), { target: { value: '2026-09-08' } })
  for (const name of names) {
    const chart = latest(name)
    expect(chart).toBeDefined()
    const dates = chart.option.series.flatMap((series: any) => series.markLine?.data ?? []).filter((item: any) => item.xAxis)
    expect(dates.length).toBeGreaterThan(0)
    expect(dates.every((item: any) => item.xAxis === '2026-09-08')).toBe(true)
    expect(chart.root).toHaveAttribute('data-observation-date', '2026-09-08')
  }
  expect(screen.getByRole('region', { name: '观察日指标' })).toHaveTextContent('2026-09-08')
  expect(screen.getByRole('region', { name: '当日模拟记录' })).toHaveTextContent('不可用')
  expect(screen.getByRole('region', { name: '当日模拟记录' })).not.toHaveTextContent('HOLD')
})

it('makes main chart layer switches display-only', () => {
  const graphics = overlayFixture()
  const before = JSON.stringify(graphics)
  render(<StrategyGraphics graphics={graphics} requestedDate="2026-09-09" />)
  for (const name of ['K线', 'MA5', 'MA10', 'MA20', 'MA60', 'BOLL', '策略']) {
    expect(screen.getByRole('checkbox', { name })).toBeInTheDocument()
  }
  fireEvent.click(screen.getByRole('checkbox', { name: 'K线' }))
  expect(latest('价格与均线图').option.series.some((s: any) => s.type === 'candlestick')).toBe(false)
  expect(JSON.stringify(graphics)).toBe(before)
})

it.each(['成交量图', 'MACD 图'])('supports tap on %s evidence marker without requiring hover', name => {
  render(<StrategyGraphics graphics={overlayFixture()} requestedDate="2026-09-09" />)
  const chart = latest(name)
  expect(chart.handlers.has('click')).toBe(true)
  const marker = { ...overlayFixture().overlay_markers![0], trade_date: '2026-09-08' }
  act(() => chart.handlers.get('click')!({ name: marker.trade_date, data: { marker } }))
  expect(screen.getByRole('combobox', { name: '观察日期' })).toHaveValue('2026-09-08')
  expect(screen.getByRole('combobox', { name: '策略筛选' })).toHaveValue('macd_golden')
})

it('links historical cards to the same date and never borrows latest-day counts', () => {
  const graphics = overlayFixture()
  const latestMarker = graphics.overlay_markers!.find(marker => marker.strategy_id === 'bullish_alignment')!
  graphics.overlay_markers!.push({ ...latestMarker, trade_date: '2026-09-08', marker_id: 'older-alignment', conditions_met: 1 })
  render(<StrategyVisualization graphics={graphics} engine={engineFixture()} requestedDate="2026-09-09" />)
  fireEvent.change(screen.getByRole('combobox', { name: '观察日期' }), { target: { value: '2026-09-08' } })
  const cards = screen.getByRole('region', { name: '六策略总览' })
  expect(cards).toHaveTextContent('回溯观察日期 2026-09-08')
  expect(cards).toHaveTextContent('1/4')
  expect(cards).not.toHaveTextContent('3/4')
  fireEvent.click(within(cards).getByRole('button', { name: '定位 均线多头排列' }))
  expect(screen.getByRole('combobox', { name: '策略筛选' })).toHaveValue('bullish_alignment')
  expect(screen.getByRole('combobox', { name: '状态筛选' })).toHaveValue('ALL')
  expect(screen.getByRole('region', { name: '策略观察详情' })).toHaveTextContent('条件进度 1/4')
  for (const name of ['MA5', 'MA10', 'MA20', 'MA60']) expect(screen.getByRole('checkbox', { name })).toBeChecked()
})

it('selecting a BOLL card reveals its price evidence without changing its result', () => {
  render(<StrategyVisualization graphics={overlayFixture()} engine={engineFixture()} requestedDate="2026-09-09" />)
  expect(screen.getByRole('checkbox', { name: 'BOLL' })).not.toBeChecked()
  fireEvent.click(screen.getByRole('button', { name: '定位 BOLL 突破' }))
  expect(screen.getByRole('checkbox', { name: 'BOLL' })).toBeChecked()
  expect(screen.getByRole('region', { name: '策略观察详情' })).toHaveTextContent('NEAR_TRIGGER')
})

it('re-centers all charts when re-clicking the active strategy card after manual zoom', () => {
  render(<StrategyVisualization graphics={overlayWindowFixture()} engine={engineFixture()} requestedDate="2026-09-09" />)
  const card = screen.getByRole('button', { name: '定位 均线多头排列' })
  fireEvent.click(card)
  act(() => latest('价格与均线图').handlers.get('datazoom')!({ start: 0, end: 9 / 29 * 100 }))
  for (const name of names) expect(latest(name).option.dataZoom[0].endValue).toBe(9)
  fireEvent.click(card)
  for (const name of names) expect(latest(name).option.dataZoom[0].endValue).toBe(29)
  expect(screen.getByRole('combobox', { name: '观察日期' })).toHaveValue('2026-09-09')
})

it('lets a strategy card select its real strategy and open the supplied evidence', () => {
  const select = vi.fn()
  render(<StrategyOverviewCards engine={engineFixture()} onSelectStrategy={select} selectedStrategy="ALL" />)
  fireEvent.click(screen.getByRole('button', { name: '定位 均线多头排列' }))
  expect(select).toHaveBeenCalledWith('bullish_alignment')
  expect(screen.getByRole('region', { name: '均线多头排列 证据' })).toHaveTextContent('量比=1.38')
})

it('allows explicit all-strategy reset after focusing a non-triggered strategy', () => {
  render(<StrategyGraphics graphics={overlayFixture()} requestedDate="2026-09-09" />)
  fireEvent.change(screen.getByRole('combobox', { name: '策略筛选' }), { target: { value: 'bullish_alignment' } })
  fireEvent.click(screen.getByRole('button', { name: '全部策略' }))
  expect(screen.getByRole('combobox', { name: '策略筛选' })).toHaveValue('ALL')
  expect(screen.getByRole('combobox', { name: '状态筛选' })).toHaveValue('ACTIVE')
  expect(within(screen.getByRole('region', { name: '策略观察详情' })).getAllByRole('article').length).toBeGreaterThan(0)
})
