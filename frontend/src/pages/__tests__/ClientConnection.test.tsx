import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ClientConnection } from '../ClientConnection'
import { api, type ClientStatus } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    clientConfigGet: vi.fn(),
    clientConfigSave: vi.fn(),
    clientStatus: vi.fn(),
    clientLogin: vi.fn(),
    clientLogout: vi.fn(),
    health: vi.fn(),
  },
}))

const STATUS: ClientStatus = {
  configured: true,
  authenticated: false,
  reachable: true,
  mode: 'hybrid',
  error_code: null,
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <ClientConnection />
    </QueryClientProvider>,
  )
}

describe('ClientConnection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.clientConfigGet).mockResolvedValue({
      remote_base_url: 'https://vm.tail.ts.net:8443',
      preferred_mode: 'hybrid',
    })
    vi.mocked(api.clientConfigSave).mockImplementation(async config => config)
    vi.mocked(api.clientStatus).mockResolvedValue(STATUS)
    vi.mocked(api.clientLogin).mockResolvedValue({ authenticated: true })
    vi.mocked(api.clientLogout).mockResolvedValue({ authenticated: false })
    vi.mocked(api.health).mockResolvedValue({ status: 'ok', version: 'test', mode: 'desktop' })
  })

  it('checks health then auth status before confirming a saved configuration', async () => {
    renderPage()

    const urlInput = await screen.findByLabelText('云端 HTTPS URL')
    expect(urlInput).toHaveValue('https://vm.tail.ts.net:8443')
    vi.mocked(api.clientStatus).mockClear()

    fireEvent.click(screen.getByRole('button', { name: '保存并检查' }))

    await screen.findByText('配置已保存，连接检查完成')
    expect(api.clientConfigSave).toHaveBeenCalledWith({
      remote_base_url: 'https://vm.tail.ts.net:8443',
      preferred_mode: 'hybrid',
    })
    expect(vi.mocked(api.health).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(api.clientStatus).mock.invocationCallOrder[0],
    )
  })

  it('keeps the password transient and clears it in finally after a failed login', async () => {
    const localSet = vi.spyOn(Storage.prototype, 'setItem')
    vi.mocked(api.clientLogin).mockRejectedValue(new Error('authentication failed'))
    renderPage()

    const passwordInput = await screen.findByLabelText('云端密码')
    fireEvent.change(passwordInput, { target: { value: 'temporary-password' } })
    expect(passwordInput).toHaveValue('temporary-password')

    fireEvent.click(screen.getByRole('button', { name: '登录云端' }))

    await waitFor(() => expect(passwordInput).toHaveValue(''))
    expect(api.clientLogin).toHaveBeenCalledWith('temporary-password')
    expect(localSet).not.toHaveBeenCalled()
    expect(screen.queryByText('temporary-password')).not.toBeInTheDocument()
  })

  it('never sends a password while the target URL has unsaved changes', async () => {
    renderPage()

    const urlInput = await screen.findByLabelText('云端 HTTPS URL')
    const passwordInput = screen.getByLabelText('云端密码')
    fireEvent.change(passwordInput, { target: { value: 'temporary-password' } })
    fireEvent.change(urlInput, { target: { value: 'https://other.tail.ts.net:8443' } })

    expect(passwordInput).toHaveValue('')
    expect(passwordInput).toBeDisabled()
    expect(screen.getByRole('button', { name: '登录云端' })).toBeDisabled()
    expect(api.clientLogin).not.toHaveBeenCalled()
  })

  it('offers compact hybrid and cloud mode controls', async () => {
    renderPage()

    const modes = await screen.findByRole('radiogroup', { name: '运行模式' })
    expect(modes).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '混合' })).toHaveAttribute('aria-checked', 'true')

    fireEvent.click(screen.getByRole('radio', { name: '云端' }))

    expect(screen.getByRole('radio', { name: '云端' })).toHaveAttribute('aria-checked', 'true')
  })
})
