import { Database } from 'lucide-react'

import { PageHeader } from '@/components/PageHeader'

export function FinancialUnavailable() {
  return (
    <>
      <PageHeader title="Financial Data" subtitle="Visual v1 capability status" />
      <div className="px-3 py-8 sm:px-5 lg:px-8">
        <section className="mx-auto max-w-xl border-y border-border px-4 py-10 text-center">
          <Database className="mx-auto h-8 w-8 text-muted" />
          <h1 className="mt-4 text-base font-semibold text-foreground">当前 Visual v1 尚未接入财务数据源</h1>
          <div className="mt-3 font-mono text-sm font-semibold text-warning">NOT CONNECTED</div>
          <p className="mt-3 text-xs leading-relaxed text-secondary">
            此页面仅说明真实能力边界，不提供套餐升级或 Provider 配置入口。
          </p>
        </section>
      </div>
    </>
  )
}
