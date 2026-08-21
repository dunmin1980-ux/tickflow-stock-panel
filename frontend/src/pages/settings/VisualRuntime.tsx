import { BotOff, FlaskConical, KeyRound, ShieldOff } from 'lucide-react'

const RUNTIME_STATUS = [
  {
    label: 'Paper Trading Engine',
    value: 'ACTIVE',
    detail: 'Option C deterministic engine',
    icon: FlaskConical,
    tone: 'text-bull',
  },
  {
    label: 'AI Provider',
    value: 'DEFERRED',
    detail: 'No model call in Visual v1',
    icon: BotOff,
    tone: 'text-warning',
  },
  {
    label: 'Real Trading',
    value: 'DISABLED',
    detail: 'Broker connectivity unavailable',
    icon: ShieldOff,
    tone: 'text-danger',
  },
] as const

export function VisualRuntimeStatusPanel() {
  return (
    <section
      aria-label="Visual v1 runtime status"
      className="mb-5 grid border-y border-border sm:grid-cols-3"
    >
      {RUNTIME_STATUS.map(({ label, value, detail, icon: Icon }, index) => (
        <div
          key={label}
          className={`min-w-0 px-3 py-3 ${index > 0 ? 'border-t border-border sm:border-l sm:border-t-0' : ''}`}
        >
          <div className="flex items-center gap-1.5 text-[10px] text-muted">
            <Icon className="h-3.5 w-3.5" />
            <span>{label}</span>
          </div>
          <div className={`mt-1 font-mono text-sm font-semibold ${RUNTIME_STATUS[index].tone}`}>
            {value}
          </div>
          <div className="mt-0.5 text-[10px] text-secondary">{detail}</div>
        </div>
      ))}
    </section>
  )
}

function VisualDeferredCredentialsPanel({ title, detail }: { title: string; detail: string }) {
  return (
    <section className="max-w-2xl border-y border-border px-4 py-5" aria-labelledby="deferred-provider-title">
      <div className="flex items-start gap-3">
        <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="deferred-provider-title" className="text-sm font-semibold text-foreground">{title}</h2>
            <span className="border border-border bg-elevated px-1.5 py-0.5 font-mono text-[9px] font-semibold text-muted">
              NOT USED
            </span>
          </div>
          <p className="mt-2 text-sm text-secondary">当前 Visual v1 不使用此配置</p>
          <p className="mt-1 text-xs leading-relaxed text-muted">{detail}</p>
        </div>
      </div>
    </section>
  )
}

export function SettingsVisualDataKeyPanel() {
  return (
    <VisualDeferredCredentialsPanel
      title="历史 TickFlow API Key 配置"
      detail="Visual v1 的日用链路使用已发布的确定性数据与 Paper Trading 引擎，此历史表单已隐藏。"
    />
  )
}

export function SettingsVisualAiPanel() {
  return (
    <VisualDeferredCredentialsPanel
      title="历史 AI Provider 配置"
      detail="AI Provider 在 Visual v1 中保持 DEFERRED；Option C 不读取该配置，也不会发起真实 AI 请求。"
    />
  )
}
