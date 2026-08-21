import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { Settings } from '../Settings'

vi.mock('@/lib/useSharedQueries', () => ({
  useSettings: () => ({
    data: {
      mode: 'none',
      tier_label: 'None',
      probe_log: [],
      missing_caps: [],
      extras_caps: [],
      has_ai_key: false,
      ai_configured: false,
    },
  }),
  useCapabilities: () => ({ data: { label: 'None', capabilities: {} } }),
}))

function renderSettings(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Visual v1 settings boundaries', () => {
  it('shows the released runtime status and hides the historical TickFlow key form', () => {
    renderSettings('/settings?tab=account')

    expect(screen.getByText('Paper Trading Engine')).toBeInTheDocument()
    expect(screen.getByText('ACTIVE')).toBeInTheDocument()
    expect(screen.getByText('AI Provider')).toBeInTheDocument()
    expect(screen.getByText('DEFERRED')).toBeInTheDocument()
    expect(screen.getByText('Real Trading')).toBeInTheDocument()
    expect(screen.getByText('DISABLED')).toBeInTheDocument()
    expect(screen.getByText('当前 Visual v1 不使用此配置')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('粘贴 TickFlow API Key')).not.toBeInTheDocument()
  })

  it('keeps the historical AI provider form unavailable in Visual v1', () => {
    renderSettings('/settings?tab=ai')

    expect(screen.getByText('AI Provider')).toBeInTheDocument()
    expect(screen.getByText('DEFERRED')).toBeInTheDocument()
    expect(screen.getByText('当前 Visual v1 不使用此配置')).toBeInTheDocument()
    expect(screen.queryByLabelText('API Key')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '保存配置' })).not.toBeInTheDocument()
  })
})
