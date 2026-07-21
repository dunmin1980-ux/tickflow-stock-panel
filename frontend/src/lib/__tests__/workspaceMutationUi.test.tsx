import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ScreenerTable } from '@/components/screener/ScreenerTable'
import type { ColumnConfig } from '@/lib/screener-columns'

describe('workspace mutation controls', () => {
  it('uses a native and accessible disabled state for Screener watchlist writes', () => {
    const onToggleWatchlist = vi.fn()
    const columns: ColumnConfig[] = [{
      id: 'builtin:symbol',
      source: { type: 'builtin', key: 'symbol' },
      label: '标的',
      visible: true,
      pinned: true,
      align: 'left',
    }]

    render(
      <ScreenerTable
        rows={[{ symbol: '000403.SZ', name: '派林生物' }]}
        columns={columns}
        strategyIdToName={{}}
        symbolStrategyMap={new Map()}
        activeStrategy={null}
        watchlistSet={new Set()}
        onPreview={vi.fn()}
        onToggleWatchlist={onToggleWatchlist}
        watchlistPending={false}
        workspaceMutationsDisabled
      />,
    )

    const button = screen.getByRole('button', { name: '离线只读，暂不能修改自选股' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(button)
    expect(onToggleWatchlist).not.toHaveBeenCalled()
  })
})
