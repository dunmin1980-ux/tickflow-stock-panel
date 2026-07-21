import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { WatchlistImportDialog } from '@/components/WatchlistImportDialog'
import { api } from '@/lib/api'

const mutationMocks = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  toast: vi.fn(),
}))

vi.mock('@/lib/useSharedMutations', () => ({
  useWatchlistBatchAdd: () => ({
    mutateAsync: mutationMocks.mutateAsync,
    isPending: false,
  }),
}))
vi.mock('@/components/Toast', () => ({ toast: mutationMocks.toast }))

describe('WatchlistImportDialog', () => {
  beforeEach(() => {
    mutationMocks.mutateAsync.mockReset()
    mutationMocks.toast.mockReset()
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:preview'),
      revokeObjectURL: vi.fn(),
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('keeps existing symbols unselectable, de-duplicates OCR matches, and reports the real added count', async () => {
    vi.spyOn(api, 'watchlistImportImage').mockResolvedValue({
      provider: 'test',
      codes: ['000403', '000403.SZ', '600489', '300059'],
      candidates: [
        { code: '000403', symbol: '000403.SZ', name: '派林生物', matched: true, already_in_watchlist: false },
        { code: '000403.SZ', symbol: '000403.SZ', name: '派林生物', matched: true, already_in_watchlist: false },
        { code: '600489', symbol: '600489.SH', name: '中金黄金', matched: true, already_in_watchlist: true },
        { code: '300059', symbol: '300059.SZ', name: '东方财富', matched: true, already_in_watchlist: false },
      ],
      matched_count: 4,
      unmatched_count: 0,
    })
    mutationMocks.mutateAsync.mockResolvedValue({
      success: true,
      added: 1,
      symbols: ['000403.SZ', '300059.SZ'],
    })
    const onClose = vi.fn()
    const { container } = render(<WatchlistImportDialog open onClose={onClose} />)
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement

    fireEvent.change(fileInput, {
      target: { files: [new File(['image'], 'watchlist.png', { type: 'image/png' })] },
    })

    await screen.findByText('已在自选')
    const existingLabel = screen.getByText('已在自选').closest('label')
    expect(existingLabel?.querySelector('input[type="checkbox"]')).toBeDisabled()
    expect(screen.getByRole('button', { name: '添加所选 (2)' })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: '添加所选 (2)' }))

    await waitFor(() => {
      expect(mutationMocks.mutateAsync).toHaveBeenCalledWith(['000403.SZ', '300059.SZ'])
      expect(mutationMocks.toast).toHaveBeenCalledWith('已添加 1 只自选', 'success')
      expect(onClose).toHaveBeenCalledTimes(1)
    })
  })
})
