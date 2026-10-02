import { useEffect, useState } from 'react'
import * as api from '@/lib/api'

const NAMES: Record<string, string> = {
  five_hour: '5시간', seven_day: '주간', seven_day_opus: 'Opus 주간', seven_day_sonnet: 'Sonnet 주간', overage: '추가 사용',
}
const STATES: Record<string, string> = { allowed: '사용 가능', allowed_warning: '한도에 가까움', rejected: '한도 도달' }

export function ProviderUsage({ model, path }: { model: string; path?: string }) {
  const selected = model.startsWith('codex:') ? 'codex' : 'claude'
  const [data, setData] = useState<api.ProviderStatus | null>(null)
  const [error, setError] = useState('')
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    const load = async () => {
      try {
        const result = await api.providerUsage(selected, path)
        if (alive) { setData(result); setError(result.error); setNow(Date.now()) }
      } catch (err) {
        if (alive) setError(String(err instanceof Error ? err.message : err))
      } finally {
        if (alive) timer = setTimeout(load, 5000)
      }
    }
    void load()
    return () => { alive = false; clearTimeout(timer) }
  }, [selected, path])
  const provider = data?.provider ?? selected
  const reset = (seconds: number) => {
    const minutes = Math.max(0, Math.ceil((seconds * 1000 - now) / 60000))
    return minutes > 0 ? `${Math.floor(minutes / 60)}시간 ${minutes % 60}분 후 초기화` : '초기화 예정 · 사용량 갱신 대기'
  }
  const usage = data?.usage
  return <div aria-label="연결 및 사용량" className="space-y-1 border-b border-border px-5 py-2 text-[12.5px] leading-relaxed tabular-nums">
    <p>{provider === 'codex' ? 'Codex' : 'Claude'} · {data?.live ? '연결됨' : '세션 대기'}
      {data?.connection_ms != null && ` · 연결 준비 ${(data.connection_ms / 1000).toFixed(1)}초`}
      {usage?.input_tokens != null && ` · ${usage.scope === 'thread' ? '대화 누적' : '최근 턴'} 입력 ${usage.input_tokens.toLocaleString()} / 출력 ${(usage.output_tokens ?? 0).toLocaleString()} 토큰`}
      {usage?.cost_usd != null && ` · $${usage.cost_usd.toFixed(3)}`}
    </p>
    {data?.quota.map((q) => <div key={q.name} className="flex flex-wrap items-center gap-x-2">
      <span>{NAMES[q.name] ?? (q.window_minutes ? `${q.window_minutes >= 1440 ? `${q.window_minutes / 1440}일` : `${q.window_minutes / 60}시간`} 한도` : q.name)}</span>
      <span>{q.used_percent == null ? STATES[q.status ?? ''] ?? '사용률 미제공' : `${Math.round(q.used_percent)}% 사용`}</span>
      {q.used_percent != null && <meter aria-label="사용률" min={0} max={100} value={q.used_percent} className="h-3 w-20" />}
      {q.resets_at != null && <time dateTime={new Date(q.resets_at * 1000).toISOString()} title={new Date(q.resets_at * 1000).toLocaleString('ko-KR')}>
        {reset(q.resets_at)} · {new Date(q.resets_at * 1000).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
      </time>}
    </div>)}
    {!data?.quota.length && <p className="text-muted-foreground">{!data ? '사용량 읽는 중…' : '계정 사용률·초기화 시간 미제공'}</p>}
    {error && <p role="status" className="text-muted-foreground">{error}</p>}
  </div>
}
