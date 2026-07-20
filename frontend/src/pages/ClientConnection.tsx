import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Cloud,
  CloudOff,
  Laptop,
  Loader2,
  LogIn,
  LogOut,
  Network,
  Save,
} from 'lucide-react'

import { PageHeader } from '@/components/PageHeader'
import { api, type ClientPreferredMode, type ClientStatus } from '@/lib/api'
import { cn } from '@/lib/cn'
import { resolveClientBadge, type ClientBadgeTone } from '@/lib/clientMode'

const CLIENT_CONFIG_KEY = ['desktop-client', 'config'] as const
const CLIENT_STATUS_KEY = ['desktop-client', 'status'] as const

const TONE_CLASS: Record<ClientBadgeTone, string> = {
  neutral: 'border-border bg-elevated/60 text-secondary',
  success: 'border-bull/30 bg-bull/10 text-bull',
  info: 'border-accent/30 bg-accent/10 text-accent',
  warning: 'border-warning/30 bg-warning/10 text-warning',
}

function StatusIcon({ status }: { status: ClientStatus }) {
  if (status.configured && !status.reachable) return <CloudOff className="h-4 w-4" />
  if (status.configured && status.authenticated && status.mode === 'cloud') {
    return <Cloud className="h-4 w-4" />
  }
  if (status.configured && status.authenticated) return <Network className="h-4 w-4" />
  return <Laptop className="h-4 w-4" />
}

function statusDetail(status: ClientStatus): string {
  if (!status.configured) return '尚未配置云端连接'
  if (!status.reachable) return '云端不可达，当前使用本地数据'
  if (!status.authenticated) return '云端可达，尚未登录'
  return '云端会话有效'
}

export function ClientConnection() {
  const queryClient = useQueryClient()
  const [remoteUrl, setRemoteUrl] = useState('')
  const [mode, setMode] = useState<ClientPreferredMode>('hybrid')
  const [password, setPassword] = useState('')
  const [saving, setSaving] = useState(false)
  const [loggingIn, setLoggingIn] = useState(false)
  const [loggingOut, setLoggingOut] = useState(false)
  const [feedback, setFeedback] = useState<{ tone: 'success' | 'warning' | 'error'; text: string } | null>(null)

  const configQuery = useQuery({
    queryKey: CLIENT_CONFIG_KEY,
    queryFn: api.clientConfigGet,
    retry: false,
  })
  const statusQuery = useQuery({
    queryKey: CLIENT_STATUS_KEY,
    queryFn: api.clientStatus,
    retry: false,
    refetchInterval: query => query.state.data == null ? false : 15_000,
  })

  useEffect(() => {
    if (!configQuery.data) return
    setRemoteUrl(configQuery.data.remote_base_url)
    setMode(configQuery.data.preferred_mode)
  }, [configQuery.data])

  if (configQuery.isLoading || statusQuery.isLoading) {
    return (
      <div className="grid min-h-48 place-items-center" role="status" aria-label="加载云端连接">
        <Loader2 className="h-5 w-5 animate-spin text-muted" />
      </div>
    )
  }

  if (statusQuery.data == null) {
    return (
      <>
        <PageHeader title="云端连接" subtitle="桌面客户端设置" />
        <div className="px-4 py-8 text-sm text-muted sm:px-8">当前环境不支持桌面连接。</div>
      </>
    )
  }

  const status = statusQuery.data
  const badge = resolveClientBadge(status)
  const configDirty = !configQuery.data
    || remoteUrl.trim() !== configQuery.data.remote_base_url
    || mode !== configQuery.data.preferred_mode

  const refreshStatus = async () => {
    const next = await api.clientStatus()
    if (next) queryClient.setQueryData(CLIENT_STATUS_KEY, next)
    return next
  }

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setFeedback(null)
    try {
      const saved = await api.clientConfigSave({
        remote_base_url: remoteUrl.trim(),
        preferred_mode: mode,
      })
      queryClient.setQueryData(CLIENT_CONFIG_KEY, saved)
      setRemoteUrl(saved.remote_base_url)
      await api.health()
      const checked = await refreshStatus()
      if (!checked) throw new Error('desktop client unavailable')
      setFeedback(checked.reachable
        ? { tone: 'success', text: '配置已保存，连接检查完成' }
        : { tone: 'warning', text: '配置已保存，云端暂不可达' })
    } catch {
      setFeedback({ tone: 'error', text: '配置保存或连接检查失败' })
    } finally {
      setSaving(false)
    }
  }

  const handleLogin = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!password) return
    setLoggingIn(true)
    setFeedback(null)
    try {
      await api.clientLogin(password)
      await refreshStatus()
      setFeedback({ tone: 'success', text: '云端登录成功' })
    } catch {
      setFeedback({ tone: 'error', text: '登录失败，请检查密码与云端状态' })
    } finally {
      setPassword('')
      setLoggingIn(false)
    }
  }

  const handleLogout = async () => {
    setLoggingOut(true)
    setFeedback(null)
    try {
      await api.clientLogout()
      await refreshStatus()
      setFeedback({ tone: 'success', text: '云端会话已注销' })
    } catch {
      setFeedback({ tone: 'error', text: '注销失败' })
    } finally {
      setLoggingOut(false)
    }
  }

  return (
    <>
      <PageHeader title="云端连接" subtitle="桌面客户端设置" />
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-8">
        <div
          role="status"
          aria-live="polite"
          className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-5"
        >
          <div className="min-w-0">
            <div className="text-xs text-muted">连接状态</div>
            <div className="mt-1 text-sm text-secondary">{statusDetail(status)}</div>
          </div>
          <span className={cn('inline-flex h-8 items-center gap-2 rounded-btn border px-3 text-xs font-medium', TONE_CLASS[badge.tone])}>
            <StatusIcon status={status} />
            {badge.label}
          </span>
        </div>

        <form onSubmit={handleSave} className="grid gap-5 border-b border-border py-5">
          <label className="grid gap-1.5 text-xs font-medium text-secondary">
            云端 HTTPS URL
            <input
              type="url"
              inputMode="url"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              required
              value={remoteUrl}
              onChange={event => {
                setRemoteUrl(event.target.value)
                setPassword('')
              }}
              placeholder="https://host.tailnet.ts.net:8443"
              className="h-9 w-full rounded-btn border border-border bg-surface px-3 text-sm text-foreground outline-none placeholder:text-muted/50 focus:border-accent/60"
            />
          </label>

          <div className="grid gap-1.5">
            <span className="text-xs font-medium text-secondary">运行模式</span>
            <div
              role="radiogroup"
              aria-label="运行模式"
              className="grid h-9 w-full grid-cols-2 overflow-hidden rounded-btn border border-border bg-elevated/40 p-0.5 sm:w-64"
            >
              {([
                ['hybrid', '混合', Network],
                ['cloud', '云端', Cloud],
              ] as const).map(([value, label, Icon]) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={mode === value}
                  onClick={() => {
                    setMode(value)
                    setPassword('')
                  }}
                  className={cn(
                    'inline-flex min-w-0 items-center justify-center gap-1.5 rounded-[4px] px-3 text-xs font-medium transition-colors',
                    mode === value
                      ? 'bg-surface text-foreground shadow-sm'
                      : 'text-muted hover:text-secondary',
                  )}
                >
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                  {label}
                </button>
              ))}
            </div>
          </div>

          <button
            type="submit"
            disabled={saving || !remoteUrl.trim()}
            className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-btn bg-accent px-4 text-xs font-medium text-white transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50 sm:w-fit"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            保存并检查
          </button>
        </form>

        <form onSubmit={handleLogin} className="grid gap-4 py-5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
          <label className="grid min-w-0 gap-1.5 text-xs font-medium text-secondary">
            云端密码
            <input
              type="password"
              autoComplete="off"
              value={password}
              onChange={event => setPassword(event.target.value)}
              disabled={!status.configured || configDirty || loggingIn || status.authenticated}
              className="h-9 w-full rounded-btn border border-border bg-surface px-3 text-sm text-foreground outline-none focus:border-accent/60 disabled:opacity-50"
            />
          </label>
          {status.authenticated ? (
            <button
              type="button"
              onClick={handleLogout}
              disabled={loggingOut}
              className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-btn border border-border bg-elevated px-4 text-xs font-medium text-secondary transition-colors hover:text-foreground disabled:opacity-50 sm:w-fit"
            >
              {loggingOut ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogOut className="h-4 w-4" />}
              注销云端
            </button>
          ) : (
            <button
              type="submit"
              disabled={!status.configured || configDirty || !password || loggingIn}
              className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-btn border border-accent/30 bg-accent/10 px-4 text-xs font-medium text-accent transition-colors hover:bg-accent/15 disabled:cursor-not-allowed disabled:opacity-50 sm:w-fit"
            >
              {loggingIn ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogIn className="h-4 w-4" />}
              登录云端
            </button>
          )}
        </form>

        {feedback && (
          <div
            role="alert"
            className={cn(
              'border-t border-border pt-4 text-xs',
              feedback.tone === 'success' && 'text-bull',
              feedback.tone === 'warning' && 'text-warning',
              feedback.tone === 'error' && 'text-danger',
            )}
          >
            {feedback.text}
          </div>
        )}
      </div>
    </>
  )
}
