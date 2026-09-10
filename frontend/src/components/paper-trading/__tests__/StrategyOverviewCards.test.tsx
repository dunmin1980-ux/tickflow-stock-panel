import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { StrategyOverviewCards } from '../StrategyOverviewCards'
import { deepFreeze, engineFixture, failedCondition } from './strategyGraphicsFixtures'

afterEach(() => { vi.unstubAllGlobals() })

describe('StrategyOverviewCards', () => {
  it('renders all six original strategies with canonical statuses and only the designated 3/4 closest badge', () => {
    render(<StrategyOverviewCards engine={engineFixture()} />)
    expect(screen.getAllByRole('article')).toHaveLength(6)
    expect(screen.getByRole('region', { name: '六策略总览' })).toHaveTextContent('研究日期 2026-09-09')
    expect(screen.getByRole('article', { name: 'MACD 金叉放量' })).toHaveTextContent('已触发')
    expect(screen.getByRole('article', { name: '均线多头排列' })).toHaveTextContent('未触发')
    expect(screen.getByRole('article', { name: '量价齐升' })).toHaveTextContent('数据不足')
    const closest = screen.getByRole('article', { name: '回踩支撑' })
    expect(closest).toHaveTextContent('接近触发')
    expect(closest).toHaveTextContent('3/4')
    expect(within(closest).getByText('最接近触发')).toBeVisible()
    expect(screen.getAllByText('最接近触发')).toHaveLength(1)
    expect(screen.getByRole('article', { name: 'BOLL 突破' })).not.toHaveTextContent('最接近触发')
  })

  it('presents supplied condition ratio, support count, failed count and daily delta without deriving a status', () => {
    const engine = engineFixture()
    Object.assign(engine.strategies[4], { condition_ratio: 0.625,
      failed_conditions: [failedCondition, { ...failedCondition, label: '另一限制' }] })
    render(<StrategyOverviewCards engine={engine} />)
    const card = screen.getByRole('article', { name: '回踩支撑' })
    expect(within(card).getByRole('progressbar', { name: '回踩支撑 条件满足进度' })).toHaveAttribute('value', '0.625')
    expect(card).toHaveTextContent('支持 3')
    expect(card).toHaveTextContent('限制 2')
    expect(card).toHaveTextContent('较前日：改善')
    expect(card).toHaveTextContent('满足项 +1')
    expect(card).toHaveTextContent('前日 2')
    expect(card).toHaveTextContent('接近触发')
  })

  it('independently expands any card using named icon disclosures and preserves all actual numeric evidence', () => {
    render(<StrategyOverviewCards engine={engineFixture()} />)
    for (const name of ['MACD 金叉放量', '回踩支撑']) {
      const button = screen.getByRole('button', { name: `展开 ${name} 证据` })
      expect(button).toHaveAttribute('title', `展开 ${name} 证据`)
      expect(button).toHaveAttribute('aria-expanded', 'false')
      fireEvent.click(button)
      const region = screen.getByRole('region', { name: `${name} 证据` })
      expect(button).toHaveAttribute('aria-controls', region.id)
      expect(region).toHaveTextContent('量比=1.38')
      expect(region).toHaveTextContent('量比 > 1.5')
      expect(region).toHaveTextContent('观测 1.38')
      expect(region).toHaveTextContent('阈值 1.5')
      expect(region).toHaveTextContent('差值 0.12')
      expect(region).toHaveTextContent('裕量 -0.12')
      expect(region).toHaveTextContent('1.2 → 1.38')
      expect(region).toHaveTextContent('+0.18')
      expect(within(region).getByRole('progressbar', { name: '量比 条件进度' })).toHaveAttribute('value', '0')
    }
    fireEvent.click(screen.getByRole('button', { name: '收起 回踩支撑 证据' }))
    expect(screen.queryByRole('region', { name: '回踩支撑 证据' })).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'MACD 金叉放量 证据' })).toBeVisible()
  })

  it('keeps failed zero, passed and unknown conditions distinct without inventing threshold percentages', () => {
    const engine = engineFixture()
    engine.strategies[4].evidence = [
      { ...failedCondition, label: '严格边界', actual: '差值=0', observed: 0, threshold: 0, gap: 0, margin: 0 },
      { ...failedCondition, label: '已满足项', passed: true, observed: 2, gap: 0, margin: 0.5 },
      { ...failedCondition, label: '未知项', actual: '日线长度不足', passed: null,
        observed: null, threshold: null, gap: null, margin: null },
    ]
    render(<StrategyOverviewCards engine={engine} />)
    fireEvent.click(screen.getByRole('button', { name: '展开 回踩支撑 证据' }))
    const region = screen.getByRole('region', { name: '回踩支撑 证据' })
    const failed = within(region).getByRole('progressbar', { name: '严格边界 条件进度' })
    const passed = within(region).getByRole('progressbar', { name: '已满足项 条件进度' })
    const unknown = within(region).getByRole('progressbar', { name: '未知项 条件进度' })
    expect(failed).toHaveAttribute('value', '0')
    expect(failed).toHaveAttribute('aria-valuetext', '条件未满足')
    expect(passed).toHaveAttribute('value', '1')
    expect(passed).toHaveAttribute('aria-valuetext', '条件满足')
    expect(unknown).not.toHaveAttribute('value')
    expect(unknown).toHaveAttribute('aria-valuetext', '数据缺失')
    expect(region).toHaveTextContent('观测 0 · 阈值 0')
    expect(region).toHaveTextContent('观测 -- · 阈值 --')
    expect(region).toHaveTextContent('严格阈值仍未满足')
    expect(region).toHaveTextContent('日线长度不足')
  })

  it('retains previous-day scope and range thresholds instead of making new current-day targets', () => {
    const engine = engineFixture()
    engine.strategies[4].evidence = [{ ...failedCondition, temporal_scope: 'PREVIOUS_DAY',
      operator: 'between', threshold: [1, 2], observed: 1, margin: 0, gap: 0 }]
    render(<StrategyOverviewCards engine={engine} />)
    fireEvent.click(screen.getByRole('button', { name: '展开 回踩支撑 证据' }))
    const evidence = screen.getByRole('region', { name: '回踩支撑 证据' })
    expect(evidence).toHaveTextContent('前一交易日')
    expect(evidence).toHaveTextContent('阈值 1 ~ 2')
    expect(evidence).toHaveTextContent('严格阈值仍未满足')
  })

  it('does not elect a closest strategy when the supplied closest is null or unmatched', () => {
    const engine = engineFixture()
    engine.summary.closest_trigger = null
    const { rerender } = render(<StrategyOverviewCards engine={engine} />)
    expect(screen.getAllByRole('article')).toHaveLength(6)
    expect(screen.queryByText('最接近触发')).not.toBeInTheDocument()
    engine.summary.closest_trigger = { ...engine.aggregate.closest_trigger!, strategy_id: 'missing' }
    rerender(<StrategyOverviewCards engine={engine} />)
    expect(screen.queryByText('最接近触发')).not.toBeInTheDocument()
  })

  it('does not present unavailable daily deltas or evidence as zero change', () => {
    const engine = engineFixture()
    Object.assign(engine.strategies[3], { evidence: [], conditions_met: 0, condition_ratio: 0,
      delta_vs_previous: { previous_conditions_met: 0, conditions_met_delta: 0,
        change: 'NOT_AVAILABLE', conditions: [] } })
    render(<StrategyOverviewCards engine={engine} />)
    const card = screen.getByRole('article', { name: '量价齐升' })
    expect(card).toHaveTextContent('较前日：无法比较')
    expect(card).not.toHaveTextContent('满足项 0')
    fireEvent.click(within(card).getByRole('button'))
    expect(card).toHaveTextContent('暂无条件证据')
  })

  it('shows an empty strategy collection explicitly without synthesizing cards', () => {
    const engine = engineFixture()
    engine.strategies = []
    render(<StrategyOverviewCards engine={engine} />)
    expect(screen.getByRole('status')).toHaveTextContent('暂无策略研究数据')
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
  })

  it('keeps frozen research and account conclusions unchanged through disclosure without network actions', () => {
    const fetchSpy = vi.fn(() => { throw new Error('Cards must remain read-only') })
    vi.stubGlobal('fetch', fetchSpy)
    const engine = deepFreeze(engineFixture())
    const before = JSON.stringify(engine)
    render(<StrategyOverviewCards engine={engine} />)
    screen.getAllByRole('button').forEach(button => fireEvent.click(button))
    expect(screen.getAllByRole('region', { name: /证据$/ })).toHaveLength(6)
    expect(JSON.stringify(engine)).toBe(before)
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /买入|卖出|运行|发布/ })).not.toBeInTheDocument()
  })
})
