import { defineConfig, devices } from '@playwright/test'

const port = 4173

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  outputDir: '../reports/pwa_acceptance/test-results',
  reporter: [
    ['list'],
    ['html', { outputFolder: '../reports/pwa_acceptance/html', open: 'never' }],
  ],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    channel: 'chrome',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'iPhone 14',
      use: { ...devices['iPhone 14'], browserName: 'chromium', viewport: { width: 390, height: 844 } },
    },
    {
      name: 'Pixel 7',
      use: { ...devices['Pixel 7'], browserName: 'chromium', viewport: { width: 412, height: 915 } },
    },
    {
      name: 'desktop-1440x900',
      use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    command: `./node_modules/.bin/vite preview --host 127.0.0.1 --port ${port}`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
