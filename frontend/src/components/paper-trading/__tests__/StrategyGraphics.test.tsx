import { act, fireEvent, render, screen, within } from '@testing-library/react'
import type { EChartsOption } from 'echarts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StrategyGraphics } from '../StrategyGraphics'
import { setTheme } from '@/lib/theme'
import { deepFreeze, engineFixture, graphicsFixture, overlayFixture } from './strategyGraphicsFixtures'

const boundary = vi.hoisted(() => ({ instances: [] as {
  root: HTMLElement; renderer: string; option?: EChartsOption;
  resize: ReturnType<typeof vi.fn>; dispose: ReturnType<typeof vi.fn>;
  handlers: Map<string, (params: unknown) => void>;
  off: ReturnType<typeof vi.fn>;
}[] }))

// JSDOM has no canvas renderer. Capture the options at that boundary only.
vi.mock('echarts', async importOriginal => ({
  ...await importOriginal<typeof import('echarts')>(),
  init(root: HTMLElement, _theme: unknown, options: { renderer: string }) {
    const instance = { root, renderer: options.renderer, option: undefined as EChartsOption | undefined,
      resize: vi.fn(), dispose: vi.fn(), setOption(option: EChartsOption) { this.option = option },
      handlers: new Map<string, (params: unknown) => void>(),
      on(event: string, handler: (params: unknown) => void) { this.handlers.set(event, handler) },
      off: vi.fn(function (this: { handlers: Map<string, unknown> }, event: string) { this.handlers.delete(event) }),
    }
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
    expect(values).toEqual(['18', '18.5', '17.9', '18.25', '18.1', '18.2', '18.3', '--', '19.5', '18.3', '17.1', '0', '0.03', '-0.06', '--',
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

  it('shows exact active research details by default with scrollable below-chart evidence and canonical Chan semantics', () => {
    const graphics = deepFreeze(overlayFixture())
    render(<div style={{ width: 390 }}><StrategyGraphics graphics={graphics} requestedDate="2026-09-09" /></div>)
    expect(screen.getByRole('combobox', { name: '策略筛选' })).toHaveValue('ALL')
    expect(screen.getByRole('combobox', { name: '状态筛选' })).toHaveValue('ACTIVE')
    const details = screen.getByRole('region', { name: '策略观察详情' })
    expect(details).toHaveTextContent('2026-09-09')
    expect(within(details).getAllByRole('article')).toHaveLength(5)
    const rule = within(details).getByRole('article', { name: 'MACD 金叉放量' })
    expect(rule).toHaveTextContent('TRIGGERED')
    expect(rule).toHaveTextContent('3/4')
    expect(rule).toHaveTextContent('MACD 金叉放量：满足 3/4 条件')
    expect(rule).toHaveTextContent('条件满足')
    expect(rule).toHaveTextContent('条件未满足')
    expect(rule).toHaveTextContent('量比=1.38')
    expect(rule).toHaveTextContent('量比 > 1.5')
    expect(rule).toHaveTextContent('0.12')
    expect(rule).toHaveTextContent('IMPROVING')
    expect(rule).toHaveTextContent('1.2 → 1.38')
    expect(rule).toHaveTextContent('+0.18')
    expect(rule).toHaveTextContent('前日 2')
    for (const chan of within(details).getAllByRole('article', { name: 'Chan Daily Structure' })) {
      expect(chan).toHaveTextContent('NOT_AVAILABLE')
      expect(chan).toHaveTextContent('结构识别｜未定义策略触发合同')
      expect(chan).toHaveTextContent('条件进度 N/A')
      expect(chan).not.toHaveTextContent('已触发')
      expect(chan).not.toHaveTextContent('0/0')
      expect(within(chan).queryByRole('progressbar')).not.toBeInTheDocument()
      expect(chan).toHaveTextContent('确认日期 2026-09-09')
      expect(chan).toHaveTextContent('2026-09-08')
    }
    expect(details).toHaveTextContent('事件类型 TOP_FRACTAL')
    expect(details).toHaveTextContent('发生日期 2026-09-08')
    expect(details).toHaveTextContent('事件类型 STROKE_CANDIDATE')
    expect(details).toHaveTextContent('发生区间 2026-09-07 → 2026-09-08')
    expect(details).toHaveTextContent('UP_STROKE')
    expect(details).toHaveClass('overflow-y-auto')
    expect(details).toHaveAttribute('tabindex', '0')
    expect(screen.getByRole('img', { name: '价格与均线图' }).compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(details.compareDocumentPosition(screen.getByRole('img', { name: 'MACD 图' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('applies strategy and status intersections to chart glyphs and details including explicit inactive rows', () => {
    render(<StrategyGraphics graphics={overlayFixture()} requestedDate="2026-09-09" />)
    const strategy = screen.getByRole('combobox', { name: '策略筛选' })
    const status = screen.getByRole('combobox', { name: '状态筛选' })
    expect(within(strategy).getAllByRole('option')).toHaveLength(8)
    fireEvent.change(status, { target: { value: 'ALL' } })
    expect(within(screen.getByRole('region', { name: '策略观察详情' })).getAllByRole('article')).toHaveLength(8)
    fireEvent.change(strategy, { target: { value: 'volume_price_surge' } })
    expect(screen.getByRole('region', { name: '策略观察详情' })).toHaveTextContent('NOT_AVAILABLE')
    fireEvent.change(status, { target: { value: 'ACTIVE' } })
    expect(screen.getByRole('region', { name: '策略观察详情' })).toHaveTextContent('当日无符合筛选的观察')
    fireEvent.change(strategy, { target: { value: 'chan_daily_structure' } })
    expect(within(screen.getByRole('region', { name: '策略观察详情' })).getAllByRole('article')).toHaveLength(2)
    fireEvent.change(status, { target: { value: 'TRIGGERED' } })
    expect(within(screen.getByRole('region', { name: '策略观察详情' })).queryByRole('article')).not.toBeInTheDocument()
    const chart = boundary.instances.filter(chart => chart.root.getAttribute('aria-label') === '价格与均线图').at(-1)!
    expect((chart.option?.series as { name: string; data: unknown[] }[]).find(series => series.name === '策略观察')?.data).toEqual([])
  })

  it.each(['click', 'mouseover'])('selects every constituent observation through the real chart %s callback', event => {
    const graphics = overlayFixture()
    graphics.overlay_markers!.push({ ...graphics.overlay_markers![0], marker_id: 'older', trade_date: '2026-09-08' })
    render(<StrategyGraphics graphics={graphics} requestedDate="2026-09-09" />)
    const chart = boundary.instances.find(chart => chart.root.getAttribute('aria-label') === '价格与均线图')!
    expect(chart.handlers.has(event)).toBe(true)
    const data = (chart.option?.series as { name: string; data: unknown[] }[]).find(series => series.name === '策略观察')!.data
    act(() => chart.handlers.get(event)!({ componentType: 'series', seriesType: 'scatter', seriesName: '策略观察', data: data[0], name: '2026-09-08' }))
    expect(screen.getByRole('combobox', { name: '观察日期' })).toHaveValue('2026-09-08')
    expect(within(screen.getByRole('region', { name: '策略观察详情' })).getAllByRole('article')).toHaveLength(1)
    act(() => chart.handlers.get(event)!({ componentType: 'series', seriesType: 'scatter', seriesName: '策略观察', data: data[1], name: '2026-09-09' }))
    expect(within(screen.getByRole('region', { name: '策略观察详情' })).getAllByRole('article')).toHaveLength(5)
    expect(boundary.instances).toHaveLength(3)
    act(() => chart.handlers.get(event)!({ componentType: 'series', seriesType: 'line', name: '2026-09-08' }))
    expect(screen.getByRole('combobox', { name: '观察日期' })).toHaveValue('2026-09-09')
  })

  it('supports native keyboard date controls and never substitutes current HOLD for missing historical records', () => {
    const engine = engineFixture()
    engine.summary.hold_explanation = ['TODAY ONLY MUST NOT LEAK']
    render(<StrategyGraphics graphics={overlayFixture()} requestedDate="2026-09-09" engine={engine} />)
    const record = () => screen.getByRole('region', { name: '当日模拟记录' })
    expect(record()).toHaveTextContent('PUBLISHED')
    expect(record()).toHaveTextContent('动作 HOLD')
    expect(record()).toHaveTextContent('信号 WATCH')
    expect(record()).toHaveTextContent('数量 0')
    expect(record()).toHaveTextContent('days/2026-09-09/chenquant_daily.json')
    expect(record()).toHaveTextContent('已发布记录未保存 HOLD 原因，不回填或推断')
    const date = screen.getByRole('combobox', { name: '观察日期' })
    act(() => date.focus())
    expect(date).toHaveFocus()
    fireEvent.change(date, { target: { value: '2026-09-08' } })
    expect(record()).toHaveTextContent('当日模拟记录不可用')
    expect(record()).not.toHaveTextContent('HOLD')
    expect(screen.getByRole('button', { name: '上一观察日' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '下一观察日' }))
    expect(date).toHaveValue('2026-09-09')
    expect(screen.getByRole('button', { name: '下一观察日' })).toBeDisabled()
    expect(screen.queryByText('TODAY ONLY MUST NOT LEAK')).not.toBeInTheDocument()
  })

  it.each(['missing', 'unavailable', 'mismatched'] as const)('does not present %s paper records as published', kind => {
    const graphics = overlayFixture()
    if (kind === 'missing') delete graphics.paper_records
    if (kind === 'unavailable') graphics.paper_records!['2026-09-09'].status = 'NOT_AVAILABLE'
    if (kind === 'mismatched') graphics.paper_records!['2026-09-09'].trade_date = '2026-09-08'
    render(<StrategyGraphics graphics={graphics} requestedDate="2026-09-09" />)
    const record = screen.getByRole('region', { name: '当日模拟记录' })
    expect(record).toHaveTextContent('当日模拟记录不可用')
    expect(record).not.toHaveTextContent('HOLD')
    expect(record).not.toHaveTextContent('WATCH')
  })

  it('shows an OHLC-unavailable price chart without losing MACD RSI or legacy date data', () => {
    const graphics = graphicsFixture()
    delete graphics.points[0].open
    render(<StrategyGraphics graphics={graphics} requestedDate="2026-09-09" />)
    expect(screen.getByText('缺少完整 OHLC 数据，K 线图不可用。')).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: '价格与均线图' })).not.toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'MACD 图' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'RSI6 图' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '当日模拟记录' })).toHaveTextContent('当日模拟记录不可用')
    expect(boundary.instances).toHaveLength(2)
  })

  it('replaces selected details with new snapshot data and unregisters chart interactions on filter, block and unmount', () => {
    const graphics = overlayFixture()
    const { rerender, unmount } = render(<StrategyGraphics graphics={graphics} requestedDate="2026-09-09" />)
    fireEvent.change(screen.getByRole('combobox', { name: '状态筛选' }), { target: { value: 'TRIGGERED' } })
    const first = boundary.instances[0]
    expect(first.off).toHaveBeenCalledWith('click', expect.any(Function))
    expect(first.off).toHaveBeenCalledWith('mouseover', expect.any(Function))
    expect(first.handlers.size).toBe(0)
    rerender(<StrategyGraphics graphics={{ ...graphics, overlay_markers: [], paper_records: {} }} requestedDate="2026-09-09" />)
    expect(screen.getByRole('region', { name: '策略观察详情' })).toHaveTextContent('当日无符合筛选的观察')
    expect(screen.getByRole('region', { name: '当日模拟记录' })).not.toHaveTextContent('HOLD')
    rerender(<StrategyGraphics graphics={{ ...graphics, status: 'BLOCKED' }} requestedDate="2026-09-09" />)
    expect(screen.queryByRole('region', { name: '策略观察详情' })).not.toBeInTheDocument()
    unmount()
    boundary.instances.forEach(chart => {
      expect(chart.handlers.size).toBe(0)
      expect(chart.dispose).toHaveBeenCalledOnce()
    })
  })

  it.each(['light', 'dark'] as const)('gives all three native selects explicit themed surface and foreground colors in %s mode', theme => {
    act(() => setTheme(theme))
    render(<StrategyGraphics graphics={overlayFixture()} requestedDate="2026-09-09" />)
    for (const name of ['策略筛选', '状态筛选', '观察日期']) {
      expect.soft(screen.getByRole('combobox', { name })).toHaveClass('bg-surface', 'text-foreground')
    }
  })
})
