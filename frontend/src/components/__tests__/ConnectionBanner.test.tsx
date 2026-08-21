import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ConnectionBanner } from '../ConnectionBanner'
import { connectivityStore } from '@/lib/connectivity'

const CACHED_AT = '2026-07-20T20:00:00+08:00'

describe('ConnectionBanner', () => {
  afterEach(() => connectivityStore.markOnline())

  it('shows the cached time while the app is offline and read-only', () => {
    connectivityStore.markOffline(CACHED_AT)

    render(<ConnectionBanner />)

    expect(screen.getByRole('status')).toHaveTextContent(
      'TickFlow 后端未运行，当前为只读模式',
    )
    expect(screen.getByRole('status')).toHaveTextContent(
      new Date(CACHED_AT).toLocaleString(),
    )
  })
})
