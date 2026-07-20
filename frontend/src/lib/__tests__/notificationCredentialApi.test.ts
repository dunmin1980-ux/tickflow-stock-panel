import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '../api'

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('notification credential API patches', () => {
  it('sends only explicitly supplied Feishu fields and explicit clear', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ ok: true, has_feishu_webhook: true }))
      .mockResolvedValueOnce(response({ ok: true, has_feishu_webhook: true }))
      .mockResolvedValueOnce(response({ ok: true, has_feishu_webhook: false }))
    vi.stubGlobal('fetch', fetchMock)

    await api.updateFeishuWebhook({ url: 'https://open.feishu.cn/hook/new' })
    await api.updateFeishuWebhook({ secret: '' })
    await api.updateFeishuWebhook({ clear: true })

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      url: 'https://open.feishu.cn/hook/new',
    })
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ secret: '' })
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({ clear: true })
  })

  it('uses patch objects for WeCom webhook and bot updates', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ ok: true, has_wecom_webhook: true }))
      .mockResolvedValueOnce(response({ ok: true, has_wecom_bot: true }))
      .mockResolvedValueOnce(response({ ok: true, has_wecom_bot: false }))
    vi.stubGlobal('fetch', fetchMock)

    await api.updateWecomWebhook({ url: 'webhook-key' })
    await api.updateWecomBot({ secret: 'replacement-secret' })
    await api.updateWecomBot({ clear: true })

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ url: 'webhook-key' })
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      secret: 'replacement-secret',
    })
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({ clear: true })
  })
})
