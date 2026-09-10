import { act, fireEvent, render, screen, within } from '@testing-library/react'
import type { EChartsOption } from 'echarts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StrategyGraphics } from '../StrategyGraphics'
import { setTheme } from '@/lib/theme'
import { deepFreeze, engineFixture, graphicsFixture } from './strategyGraphicsFixtures'

const boundary = vi.hoisted(() => ({ instances: [] as {
  root: HTMLElement; renderer: string; option?: EChartsOption;
  resize: ReturnType<typeof vi.fn>; dispose: ReturnType<typeof vi.fn>;
}[] }))

// JSDOM has no canvas renderer. Capture the options at that boundary only.
vi.mock('echarts', async importOriginal => ({
  ...await importOriginal<typeof import('echarts')>(),
  init(root: HTMLElement, _theme: unknown, options: { renderer: string }) {
    const instance = { root, renderer: options.renderer, option: undefined as EChartsOption | undefined,
      resize: vi.fn(), dispose: vi.fn(), setOption(option: EChartsOption) { this.option = option } }
    boundary.instances.push(instance)
    return instance
  },
}))

beforeEach(() => { boundary.instances.length = 0; localStorage.clear() })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('StrategyGraphics', () => {
  it('renders three named canvas charts with actual supplied values and accessible dated data', () => {
    render(<StrategyGraphics graphics={graphicsFixture()} requestedDate="2026-09-09" engine={engineFixture()} />)
    expect(screen.getByRole('img', { name: '价格与均线图' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'MACD 图' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'RSI6 图' })).toBeInTheDocument()
    expect(boundary.instances).toHaveLength(3)
    expect(boundary.instances.every(chart => chart.renderer === 'canvas')).toBe(true)
    const macd = boundary.instances.find(chart => chart.root.getAttribute('aria-label') === 'MACD 图')!
    expect(macd.option?.series).toEqual(expect.arrayContaining([
      expect.objectContaining({ name: 'DIF', data: [0, 0.0105] }),
    ]))
    const table = screen.getByRole('table', { name: '策略图表数据' })
    expect(within(table).getByRole('row', { name: /2026-09-09/ })).toHaveTextContent('50.0891')
    expect(table).toHaveTextContent('回踩支撑 · 接近触发 · 3/4 · 18.6')
    expect(screen.getByRole('region', { name: '策略图表' })).toHaveTextContent('研究日期 2026-09-09')
    expect(screen.getByRole('region', { name: '策略图表' })).toHaveTextContent('QFQ')
    expect(screen.getByRole('region', { name: '策略图表' })).toHaveTextContent('同一快照')
  })

  it('labels stale graphics with their real date instead of requested-day research', () => {
    render(<StrategyGraphics graphics={graphicsFixture()} requestedDate="2026-09-10" />)
    expect(screen.getByRole('status')).toHaveTextContent('2026-09-09')
    expect(screen.getByRole('status')).toHaveTextContent('尚非 2026-09-10 当日结果')
    expect(screen.queryByText(/今日.*图表/)).not.toBeInTheDocument()
  })

  it('shows missing graphics explicitly and retains an available engine research date', () => {
    render(<StrategyGraphics graphics={undefined} requestedDate="2026-09-10" engine={engineFixture()} />)
    const region = screen.getByRole('region', { name: '策略图表' })
    expect(region).toHaveTextContent('研究日期 2026-09-09')
    expect(region).toHaveTextContent('暂无图表数据')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(boundary.instances).toHaveLength(0)
  })

  it('does not pretend the requested date is a verified date when both inputs are missing', () => {
    render(<StrategyGraphics graphics={undefined} requestedDate="2026-09-10" />)
    expect(screen.getByRole('region', { name: '策略图表' })).toHaveTextContent('研究日期未提供')
    expect(screen.queryByText('研究日期 2026-09-10')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('暂无图表数据')
  })

  it('keeps READY empty graphics explicit and does not initialize empty canvases', () => {
    render(<StrategyGraphics graphics={{ ...graphicsFixture(), points: [], strategy_markers: [] }} requestedDate="2026-09-09" />)
    expect(screen.getByRole('status')).toHaveTextContent('暂无可绘制的日线数据')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(boundary.instances).toHaveLength(0)
  })

  it('suppresses even populated BLOCKED graphics with a clear warning and actual date', () => {
    render(<StrategyGraphics graphics={{ ...graphicsFixture(), status: 'BLOCKED' }} requestedDate="2026-09-10" />)
    expect(screen.getByRole('alert')).toHaveTextContent('图表数据校验未通过')
    expect(screen.getByRole('region', { name: '策略图表' })).toHaveTextContent('研究日期 2026-09-09')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(boundary.instances).toHaveLength(0)
  })

  it('accepts a minimal BLOCKED payload without asserting absent symbol or price-basis metadata', () => {
    render(<StrategyGraphics graphics={{ status: 'BLOCKED', trade_date: '2026-09-09' }} requestedDate="2026-09-10" />)
    expect(screen.getByRole('alert')).toHaveTextContent('图表数据校验未通过')
    const region = screen.getByRole('region', { name: '策略图表' })
    expect(region).toHaveTextContent('研究日期 2026-09-09')
    expect(region).not.toHaveTextContent('QFQ')
    expect(region).not.toHaveTextContent('000403.SZ')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(boundary.instances).toHaveLength(0)
  })

  it('uses only engine-provided metadata alongside a minimal blocked payload', () => {
    render(<StrategyGraphics graphics={{ status: 'BLOCKED', trade_date: '2026-09-09' }}
      requestedDate="2026-09-10" engine={engineFixture()} />)
    expect(screen.getByRole('alert')).toHaveTextContent('图表数据校验未通过')
    expect(screen.getByRole('region', { name: '策略图表' })).toHaveTextContent('000403.SZ')
    expect(screen.getByRole('region', { name: '策略图表' })).toHaveTextContent('QFQ')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('blocks mismatched engine and graphics dates rather than combining different research snapshots', () => {
    const engine = engineFixture()
    engine.trade_date = '2026-09-08'
    render(<StrategyGraphics graphics={graphicsFixture()} requestedDate="2026-09-10" engine={engine} />)
    expect(screen.getByRole('alert')).toHaveTextContent('图表日期与研究日期不一致')
    expect(screen.getByRole('alert')).toHaveTextContent('2026-09-08')
    expect(screen.getByRole('alert')).toHaveTextContent('2026-09-09')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(boundary.instances).toHaveLength(0)
  })

  it('shows null indicators as unavailable while preserving zero in the accessible data', () => {
    render(<StrategyGraphics graphics={graphicsFixture()} requestedDate="2026-09-09" />)
    const row = within(screen.getByRole('table', { name: '策略图表数据' })).getByRole('row', { name: /2026-09-08/ })
    const values = within(row).getAllByRole('cell').map(cell => cell.textContent)
    expect(values).toEqual(['18.25', '18.1', '18.2', '18.3', '--', '19.5', '18.3', '17.1', '0', '0.03', '-0.06', '--',
      'MACD 金叉放量 · 已触发 · 4/4 · 18.25'])
  })

  it('resizes with its container and releases charts and listeners on unmount', () => {
    const callbacks: ResizeObserverCallback[] = []
    const observers: { observe: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> }[] = []
    vi.stubGlobal('ResizeObserver', class {
      observe = vi.fn()
      disconnect = vi.fn()
      constructor(callback: ResizeObserverCallback) { callbacks.push(callback); observers.push(this) }
    })
    const { unmount } = render(<StrategyGraphics graphics={graphicsFixture()} requestedDate="2026-09-09" />)
    expect(observers).toHaveLength(3)
    boundary.instances.forEach((chart, index) => {
      expect(observers[index].observe).toHaveBeenCalledWith(chart.root)
      expect(chart.root.style.height).not.toBe('')
    })
    act(() => callbacks.forEach(callback => callback([], {} as ResizeObserver)))
    fireEvent(window, new Event('resize'))
    boundary.instances.forEach(chart => expect(chart.resize).toHaveBeenCalledTimes(2))
    unmount()
    observers.forEach(observer => expect(observer.disconnect).toHaveBeenCalledOnce())
    boundary.instances.forEach(chart => expect(chart.dispose).toHaveBeenCalledOnce())
    fireEvent(window, new Event('resize'))
    boundary.instances.forEach(chart => expect(chart.resize).toHaveBeenCalledTimes(2))
  })

  it('rebuilds chart colors for a theme change and disposes charts when data becomes blocked', () => {
    const graphics = graphicsFixture()
    const { rerender } = render(<StrategyGraphics graphics={graphics} requestedDate="2026-09-09" />)
    const old = boundary.instances.slice()
    act(() => setTheme('light'))
    expect(boundary.instances).toHaveLength(6)
    old.forEach(chart => expect(chart.dispose).toHaveBeenCalledOnce())
    expect(boundary.instances[3].option?.tooltip).toMatchObject({ textStyle: { color: '#27272A' } })
    rerender(<StrategyGraphics graphics={{ ...graphics, status: 'BLOCKED' }} requestedDate="2026-09-09" />)
    boundary.instances.forEach(chart => expect(chart.dispose).toHaveBeenCalledOnce())
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('renders frozen input without mutation or any network actions', () => {
    const fetchSpy = vi.fn(() => { throw new Error('Read-only component must not fetch') })
    vi.stubGlobal('fetch', fetchSpy)
    const graphics = deepFreeze(graphicsFixture())
    const engine = deepFreeze(engineFixture())
    const before = JSON.stringify({ graphics, engine })
    render(<StrategyGraphics graphics={graphics} requestedDate="2026-09-09" engine={engine} />)
    expect(screen.getAllByRole('img')).toHaveLength(3)
    expect(JSON.stringify({ graphics, engine })).toBe(before)
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /买入|卖出|运行|发布/ })).not.toBeInTheDocument()
  })
})
