import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ResearchPanel } from '../ResearchPanel'
import type { PaperResearchPanel } from '@/lib/api'

const research: PaperResearchPanel = {
  status: 'READY', trade_date: '2026-09-09', previous_trade_date: '2026-09-08',
  price_basis: 'qfq', source_provider: 'stocksdk_tencent_fallback',
  affects_paper_action: false, chan_status: 'NOT_IMPLEMENTED',
  matched_count: 0, risk_count: 0,
  strategies: [{
    id: 'macd_golden', name: 'MACD 金叉放量', description: 'MACD 金叉当日 + 放量',
    status: 'NOT_MATCHED', previous_status: 'NOT_MATCHED', change: 'UNCHANGED',
    matched_conditions: 0, risk_triggered: false, calculation_source: 'builtin.macd_golden',
    conditions: [{ label: '今日 MACD', actual: 'DIF=0.0105 / DEA=0.0470',
      required: 'DIF > DEA', passed: false }],
  }],
  paper_rule: { signal: 'MIXED_OBSERVATION', positive_conditions: [
    { label: 'MACD 线关系', actual: 'DIF=0.0105 / DEA=0.0470', required: 'DIF > DEA', passed: false },
    { label: 'RSI6', actual: 'RSI6=50.0891', required: 'RSI6 ≥ 50', passed: true },
  ] },
}

describe('ResearchPanel', () => {
  it('shows measured reasons, unmet rules and previous-day change', () => {
    render(<ResearchPanel research={research} requestedDate="2026-09-09" />)
    expect(screen.getByRole('heading', { name: '日线多策略研究' })).toBeInTheDocument()
    expect(screen.getByText('MACD 金叉放量')).toBeInTheDocument()
    expect(screen.getByText('DIF=0.0105 / DEA=0.0470')).toBeInTheDocument()
    expect(screen.getByText('DIF > DEA')).toBeInTheDocument()
    expect(screen.getByText('较前日：未变化')).toBeInTheDocument()
    expect(screen.getByText(/缠论：未实现/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '仅看命中' }))
    expect(screen.queryByText('MACD 金叉放量')).not.toBeInTheDocument()
    expect(screen.getByText('本日没有规则命中')).toBeInTheDocument()
  })

  it('labels the actual evidence date when showing previous data', () => {
    render(<ResearchPanel research={research} requestedDate="2026-09-10" />)
    expect(screen.getByText(/当前展示 2026-09-09 的研究结果/)).toBeInTheDocument()
  })

  it('shows unavailable research without pretending it is a negative signal', () => {
    render(<ResearchPanel research={{ status: 'BLOCKED' }} requestedDate="2026-09-09" />)
    expect(screen.getByRole('alert')).toHaveTextContent('研究数据校验未通过')
    expect(screen.queryByText('未命中')).not.toBeInTheDocument()
  })
})
