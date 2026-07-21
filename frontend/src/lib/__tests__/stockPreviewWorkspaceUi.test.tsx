import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import { api } from '@/lib/api'
import { workspaceStatusStore } from '@/lib/workspace'

vi.mock('@/components/StockPanel', () => ({
  getDefaultRange: () => ({ start: '2026-01-01', end: '2026-07-21' }),
  StockPanel: ({ onToggleWatchlist }: { onToggleWatchlist?: () => void }) => (
    <button type="button" onClick={onToggleWatchlist}>preview-toggle-watchlist</button>
  ),
}))

vi.mock('@/components/monitor/RuleEditor', () => ({ RuleEditor: () => null }))
vi.mock('@/lib/useSharedQueries', () => ({
  usePreferences: () => ({ data: {} }),
  useQuoteStatus: () => ({ data: { running: false } }),
}))
vi.mock('@/lib/useQuoteStream', () => ({
  setFocusSymbol: vi.fn(),
  clearFocusSymbol: vi.fn(),
}))

describe('StockPreviewDialog workspace mutation guard', () => {
  afterEach(() => {
    workspaceStatusStore.markOnline()
    vi.restoreAllMocks()
  })

  it('blocks a watchlist handler before fetch when the cloud workspace is offline', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    vi.spyOn(api, 'watchlistList').mockResolvedValue({ symbols: [] })
    const add = vi.spyOn(api, 'watchlistAdd').mockResolvedValue({
      symbols: [{ symbol: '000403.SZ' }],
    })
    workspaceStatusStore.markOffline()

    render(
      <QueryClientProvider client={queryClient}>
        <StockPreviewDialog symbol="000403.SZ" name="派林生物" onClose={vi.fn()} />
      </QueryClientProvider>,
    )

    await waitFor(() => expect(api.watchlistList).toHaveBeenCalled())
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'preview-toggle-watchlist' }))
      await Promise.resolve()
    })
    expect(add).not.toHaveBeenCalled()
  })
})
