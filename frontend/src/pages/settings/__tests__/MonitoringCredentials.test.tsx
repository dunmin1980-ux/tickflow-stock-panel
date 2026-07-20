import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  updateFeishuWebhook: vi.fn(),
  updateWecomWebhook: vi.fn(),
  updateWecomBot: vi.fn(),
  toggleWecomBot: vi.fn(),
  updateRealtimeMonitorConfig: vi.fn(),
  updateIndicesNavPinned: vi.fn(),
  updateLimitLadderMonitor: vi.fn(),
  updateWebhookDefaultChannels: vi.fn(),
  runLimitLadderFix: vi.fn(),
  watchlistList: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ api: apiMocks }))
vi.mock('@/lib/capability-labels', () => ({ tierRank: () => 1 }))
vi.mock('@/components/Toast', () => ({ toast: vi.fn() }))
vi.mock('@/components/data/DepthConfigCard', () => ({
  DepthConfigContent: () => null,
}))
vi.mock('@/lib/useSharedQueries', () => ({
  usePreferences: () => ({
    data: {
      realtime_quotes_enabled: false,
      has_feishu_webhook: true,
      has_wecom_webhook: true,
      has_wecom_bot: true,
      wecom_bot_enabled: false,
      webhook_default_channels: [],
      sidebar_index_symbols: [],
      indices_nav_pinned: true,
      sse_refresh_pages: {},
      realtime_watchlist_symbols: [],
    },
  }),
  useCapabilities: () => ({ data: { label: 'Pro', capabilities: {} } }),
  useQuoteStatus: () => ({ data: { running: false, paused: false } }),
  useQuoteInterval: () => ({ data: { interval: 6, min_interval: 6, max_interval: 60 } }),
}))
vi.mock('@/lib/useSharedMutations', () => ({
  useUpdateQuoteInterval: () => ({ mutate: vi.fn(), isPending: false }),
  useToggleRealtimeQuotes: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

import { SettingsMonitoringPanel } from '../Monitoring'

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <SettingsMonitoringPanel />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe('Monitoring credential controls', () => {
  it('submits only entered Feishu fields and clears drafts after success', async () => {
    apiMocks.updateFeishuWebhook.mockResolvedValue({
      ok: true,
      has_feishu_webhook: true,
    })
    renderPanel()

    fireEvent.click(screen.getByText('飞书'))
    const urlInput = screen.getByLabelText('Webhook 地址')
    fireEvent.change(urlInput, {
      target: { value: 'https://open.feishu.cn/open-apis/bot/v2/hook/new' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      expect(apiMocks.updateFeishuWebhook).toHaveBeenCalledWith({
        url: 'https://open.feishu.cn/open-apis/bot/v2/hook/new',
      })
    })
    expect(urlInput).toHaveValue('')
    expect(screen.getByLabelText('签名密钥 (可选 · 启用签名校验时填)')).toHaveValue('')
  })

  it('confirms and sends independent explicit clears for all credential groups', async () => {
    apiMocks.updateFeishuWebhook.mockResolvedValue({ ok: true, has_feishu_webhook: false })
    apiMocks.updateWecomWebhook.mockResolvedValue({ ok: true, has_wecom_webhook: false })
    apiMocks.updateWecomBot.mockResolvedValue({
      ok: true,
      has_wecom_bot: false,
      wecom_bot_enabled: false,
      wecom_bot_status: {},
    })
    const confirmMock = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmMock)
    renderPanel()

    fireEvent.click(screen.getByText('飞书'))
    fireEvent.click(screen.getByRole('button', { name: '清除飞书配置' }))

    const wecomLabels = screen.getAllByText('企业微信')
    fireEvent.click(wecomLabels[0])
    fireEvent.click(screen.getByRole('button', { name: '清除企业微信 Webhook' }))
    fireEvent.click(wecomLabels[1])
    fireEvent.click(screen.getByRole('button', { name: '清除智能机器人凭证' }))

    await waitFor(() => {
      expect(apiMocks.updateFeishuWebhook).toHaveBeenCalledWith({ clear: true })
      expect(apiMocks.updateWecomWebhook).toHaveBeenCalledWith({ clear: true })
      expect(apiMocks.updateWecomBot).toHaveBeenCalledWith({ clear: true })
    })
    expect(confirmMock).toHaveBeenCalledTimes(3)
  })

  it('sends only entered WeCom webhook and bot fields', async () => {
    apiMocks.updateWecomWebhook.mockResolvedValue({ ok: true, has_wecom_webhook: true })
    apiMocks.updateWecomBot.mockResolvedValue({
      ok: true,
      has_wecom_bot: true,
      wecom_bot_enabled: false,
      wecom_bot_status: {},
    })
    renderPanel()

    const wecomLabels = screen.getAllByText('企业微信')
    fireEvent.click(wecomLabels[0])
    const webhookInput = screen.getByLabelText('Webhook 地址 或 Key')
    fireEvent.change(webhookInput, { target: { value: 'replacement-webhook-key' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      expect(apiMocks.updateWecomWebhook).toHaveBeenCalledWith({
        url: 'replacement-webhook-key',
      })
    })
    expect(webhookInput).toHaveValue('')

    fireEvent.click(wecomLabels[1])
    const secretInput = screen.getByLabelText('Secret (长连接专用密钥)')
    fireEvent.change(secretInput, { target: { value: 'replacement-bot-secret' } })
    fireEvent.click(screen.getByRole('button', { name: '保存凭证' }))

    await waitFor(() => {
      expect(apiMocks.updateWecomBot).toHaveBeenCalledWith({
        secret: 'replacement-bot-secret',
      })
    })
    expect(screen.getByLabelText('BotID')).toHaveValue('')
    expect(secretInput).toHaveValue('')
  })

  it('does not clear credentials when confirmation is canceled', async () => {
    vi.stubGlobal('confirm', vi.fn(() => false))
    renderPanel()

    fireEvent.click(screen.getByText('飞书'))
    fireEvent.click(screen.getByRole('button', { name: '清除飞书配置' }))

    expect(apiMocks.updateFeishuWebhook).not.toHaveBeenCalled()
  })
})
