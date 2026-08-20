import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '../api'

describe('desktop client API', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('silently treats a missing client status endpoint as a normal PWA', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(new Response('', { status: 404 }))
    vi.stubGlobal('fetch', fetchSpy)

    await expect(api.clientStatus()).resolves.toBeNull()
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/client/status',
      expect.objectContaining({ credentials: 'same-origin', cache: 'no-store' }),
    )
  })

  it('sends only the public URL and mode when saving desktop config', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        remote_base_url: 'https://vm.tail.ts.net:8443',
        preferred_mode: 'hybrid',
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchSpy)

    await api.clientConfigSave({
      remote_base_url: 'https://vm.tail.ts.net:8443',
      preferred_mode: 'hybrid',
    })

    const init = fetchSpy.mock.calls[0][1] as RequestInit
    expect(fetchSpy.mock.calls[0][0]).toBe('/api/client/config')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(String(init.body))).toEqual({
      remote_base_url: 'https://vm.tail.ts.net:8443',
      preferred_mode: 'hybrid',
    })
  })

  it('surfaces the stable public message from structured API errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: 'PAPER_INPUT_MISSING',
        message: '当前日期没有已验证的离线输入',
      },
    }), {
      status: 409,
      headers: { 'Content-Type': 'application/json' },
    })))

    await expect(api.paperTradingRun('2026-08-20')).rejects.toMatchObject({
      message: '当前日期没有已验证的离线输入',
    })
  })
})
