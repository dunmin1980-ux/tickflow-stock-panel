import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { StockInfoBar } from '@/components/StockInfoBar'
import { ScreenerTable } from '@/components/screener/ScreenerTable'
import type { ColumnConfig } from '@/lib/screener-columns'
import { workspaceStatusStore } from '@/lib/workspace'

describe('workspace mutation controls', () => {
  afterEach(() => {
    workspaceStatusStore.markOnline()
  })

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

  it('disables StockInfoBar watchlist writes while the cloud workspace is offline', () => {
    const onToggleWatchlist = vi.fn()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    workspaceStatusStore.markOffline()

    render(
      <QueryClientProvider client={queryClient}>
        <StockInfoBar
          symbol="000403.SZ"
          name="派林生物"
          rows={[
            { date: '2026-07-20', open: 20, high: 21, low: 19, close: 20, volume: 1000 },
            { date: '2026-07-21', open: 20, high: 22, low: 20, close: 21, volume: 1200 },
          ]}
          fields={[]}
          onFieldsChange={vi.fn()}
          onToggleWatchlist={onToggleWatchlist}
          inWatchlist={false}
        />
      </QueryClientProvider>,
    )

    const button = screen.getByRole('button', { name: '离线只读，暂不能修改自选股' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(button)
    expect(onToggleWatchlist).not.toHaveBeenCalled()
  })
})
