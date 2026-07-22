import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { Backtest } from '../Backtest'
import { confirmBacktestSummary, useBacktestTask } from '@/lib/backtestTask'

vi.mock('@/lib/backtestTask', () => ({
  confirmBacktestSummary: vi.fn(),
  useBacktestTask: vi.fn(),
}))

vi.mock('@/lib/useSharedQueries', () => ({
  useBacktestSummaries: () => ({ isLoading: false, data: { summaries: [] } }),
}))

vi.mock('../backtest/FactorBacktest', () => ({ FactorBacktest: () => <div /> }))
vi.mock('../backtest/StrategyBacktest', () => ({ StrategyBacktest: () => <div /> }))
vi.mock('../backtest/StrategyOptimizer', () => ({ StrategyOptimizer: () => <div /> }))
vi.mock('../backtest/StrategyWalkForward', () => ({ StrategyWalkForward: () => <div /> }))

describe('Backtest summary confirmation banner', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(confirmBacktestSummary).mockResolvedValue(true)
    vi.mocked(useBacktestTask).mockReturnValue({
      id: 1,
      isPending: false,
      result: null,
      progress: null,
      error: null,
      reconnecting: false,
      summaryConfirmation: {
        pendingSummary: { strategy_id: 'macd_golden' },
        currentRevision: 'b'.repeat(64),
        isSaving: false,
        error: null,
      },
    } as ReturnType<typeof useBacktestTask>)
  })

  it('requires an explicit click before saving a conflicted summary', () => {
    render(<Backtest />)

    expect(screen.getByRole('alert')).toHaveTextContent('回测已完成，但摘要尚未保存')
    expect(confirmBacktestSummary).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '确认保存摘要' }))

    expect(confirmBacktestSummary).toHaveBeenCalledTimes(1)
  })
})
