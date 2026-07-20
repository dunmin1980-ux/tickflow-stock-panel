import { describe, expect, it } from 'vitest'

import { resolveClientBadge } from '../clientMode'

describe('resolveClientBadge', () => {
  it('shows hybrid mode for an authenticated reachable hybrid client', () => {
    expect(resolveClientBadge({
      configured: true,
      authenticated: true,
      reachable: true,
      mode: 'hybrid',
      error_code: null,
    })).toEqual({ label: '混合模式', tone: 'success' })
  })

  it('shows cloud mode for an authenticated reachable cloud client', () => {
    expect(resolveClientBadge({
      configured: true,
      authenticated: true,
      reachable: true,
      mode: 'cloud',
      error_code: null,
    })).toEqual({ label: '云端模式', tone: 'info' })
  })

  it('shows offline read-only whenever a configured cloud is unreachable', () => {
    expect(resolveClientBadge({
      configured: true,
      authenticated: true,
      reachable: false,
      mode: 'hybrid',
      error_code: 'UNREACHABLE',
    })).toEqual({ label: '离线只读', tone: 'warning' })
  })

  it.each([
    {
      configured: false,
      authenticated: false,
      reachable: false,
      mode: 'hybrid' as const,
      error_code: 'NOT_CONFIGURED',
    },
    {
      configured: true,
      authenticated: false,
      reachable: true,
      mode: 'cloud' as const,
      error_code: null,
    },
  ])('shows local mode when no authenticated cloud session is active', (status) => {
    expect(resolveClientBadge(status)).toEqual({ label: '本地模式', tone: 'neutral' })
  })
})
