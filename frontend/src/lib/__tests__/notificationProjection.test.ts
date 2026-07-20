import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const CONSUMERS = [
  '../../components/monitor/RuleEditor.tsx',
  '../../pages/Review.tsx',
  '../../pages/settings/Monitoring.tsx',
]

describe('notification preference projection', () => {
  it.each(CONSUMERS)('%s uses has_* state instead of raw credentials', (relativePath) => {
    const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8')

    expect(source).not.toMatch(/prefs(?:\.data)?\?\.(?:feishu_webhook_url|feishu_webhook_secret)/)
    expect(source).not.toMatch(/prefs(?:\.data)?\?\.(?:wecom_webhook_url|wecom_bot_id|wecom_bot_secret)/)
  })

  it('uses each server-projected notification configuration flag', () => {
    const source = CONSUMERS
      .map((relativePath) => readFileSync(new URL(relativePath, import.meta.url), 'utf8'))
      .join('\n')

    expect(source).toContain('has_feishu_webhook')
    expect(source).toContain('has_wecom_webhook')
    expect(source).toContain('has_wecom_bot')
  })

  it('fetches market recap bodies before selecting history', () => {
    const reviewPath = CONSUMERS[1]
    const source = readFileSync(new URL(reviewPath, import.meta.url), 'utf8')

    expect(source).toContain('api.reviewReportGet(r.id)')
    expect(source).not.toContain('setViewing(r)')
  })
})
