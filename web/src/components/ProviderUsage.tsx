import { useEffect, useState } from 'react'
import * as api from '@/lib/api'

const NAMES: Record<string, string> = {
  five_hour: '5시간', seven_day: '7일', seven_day_opus: 'Opus 7일', seven_day_sonnet: 'Sonnet 7일', overage: '추가 사용',
}
const STATES: Record<string, string> = { allowed: '사용 가능', allowed_warning: '한도에 가까움', rejected: '한도 도달' }

export function TokenUsage({ model, path }: { model: string; path?: string }) {
  const [data, setData] = useState<api.ProviderStatus | null>(null)
  const provider = model.startsWith('codex:') ? 'codex' : 'claude'
  useEffect(() => {
    if (!path) return
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    const load = async () => {
      try {
        const result = await api.providerUsage(provider, path)
        if (alive) setData(result)
      } catch { /* Retry on the next poll. */ }
      finally { if (alive) timer = setTimeout(load, 5000) }
    }
    void load()
    return () => { alive = false; clearTimeout(timer) }
  }, [provider, path])
  const usage = path ? data?.usage : undefined
  return <span aria-label="사용 토큰" className="mr-auto min-w-0 truncate font-mono text-[10.5px] text-muted-foreground tabular-nums"
    title={usage?.input_tokens == null ? '' : `${usage.scope === 'thread' ? '대화 누적' : '최근 턴'} 입력 ${usage.input_tokens.toLocaleString()} / 출력 ${(usage.output_tokens ?? 0).toLocaleString()} 토큰`}>
    {usage?.input_tokens != null ? `${usage.input_tokens.toLocaleString()} → ${(usage.output_tokens ?? 0).toLocaleString()} 토큰` : ''}
  </span>
}

/** Account limits belong to the whole workspace, including query and review CLIs. */
export function ProviderUsage() {
  const [open, setOpen] = useState(() => document.documentElement.dataset.mobileLayout === 'desktop' && window.innerWidth >= 1280)
  const [rows, setRows] = useState<api.ProviderStatus[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    const load = async () => {
      try {
        const data = await api.allProviderUsage()
        if (alive) { setRows(data.providers); setError('') }
      } catch (err) { if (alive) setError(String(err)) }
      finally { if (alive) timer = setTimeout(load, 5000) }
    }
    void load()
    return () => { alive = false; clearTimeout(timer) }
  }, [])
  return <div aria-label="CLI 사용 한도" className="provider-limits shrink-0 max-h-[35dvh] overflow-y-auto border-t border-sidebar-border p-3 text-[12.5px] leading-relaxed tabular-nums">
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer font-heading text-[11px] font-semibold text-muted-foreground">
        <span className="rail-expanded max-[1280px]:hidden">CLI 사용 한도</span>
        <span className="rail-folded min-[1280px]:hidden">한도</span>
      </summary>
      <div className="mt-2 space-y-3">
        {rows.map((data) => <div key={data.provider}>
          <p className="font-semibold">{data.provider === 'codex' ? 'Codex' : 'Claude'}</p>
          {data.quota.map((q) => <div key={q.name} className="mt-1 flex flex-wrap items-center gap-x-2">
            <span>{NAMES[q.name] ?? (q.window_minutes ? `${q.window_minutes >= 1440 ? `${q.window_minutes / 1440}일` : `${q.window_minutes / 60}시간`}` : q.name)}</span>
            <span>{q.used_percent == null ? STATES[q.status ?? ''] ?? '사용률 미제공' : `${Math.round(q.used_percent)}% 사용`}</span>
            {q.used_percent != null && <meter aria-label={`${data.provider} ${q.name} 사용률`} min={0} max={100} value={q.used_percent} className="h-3 w-16" />}
            {q.resets_at != null && <time className="basis-full text-muted-foreground" dateTime={new Date(q.resets_at * 1000).toISOString()}>
              {new Date(q.resets_at * 1000).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })} 초기화
            </time>}
          </div>)}
          {!data.quota.length && <p className="text-muted-foreground">계정 사용률·초기화 시간 미제공</p>}
          {data.error && <p role="status" className="text-muted-foreground">{data.error}</p>}
        </div>)}
        {!rows.length && <p className="text-muted-foreground">연결된 CLI 사용량 대기</p>}
        {error && <p role="status" className="text-muted-foreground">{error}</p>}
      </div>
    </details>
  </div>
}
