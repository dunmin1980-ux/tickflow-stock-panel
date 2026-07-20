import { expect, test } from '@playwright/test'

test('serves an installable PWA without API runtime caching', async ({ request }) => {
  const manifestResponse = await request.get('/manifest.webmanifest')
  expect(manifestResponse.ok()).toBe(true)
  const manifest = await manifestResponse.json()
  expect(manifest).toMatchObject({
    name: 'TickFlow 股票面板',
    short_name: 'TickFlow',
    display: 'standalone',
    start_url: '/watchlist',
    scope: '/',
  })
  expect(manifest.icons).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ src: '/pwa-192.png', sizes: '192x192' }),
      expect.objectContaining({ src: '/pwa-512.png', sizes: '512x512' }),
    ]),
  )

  const workerResponse = await request.get('/sw.js')
  expect(workerResponse.ok()).toBe(true)
  expect(await workerResponse.text()).not.toContain('/api/')
})
