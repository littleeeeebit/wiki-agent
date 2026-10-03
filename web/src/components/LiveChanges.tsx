import { useEffect, useState } from 'react'
import * as api from '@/lib/api'
import { cn } from '@/lib/utils'

/** Poll actual Git state while the agent runs, regardless of the host's edit tool. */
export function LiveChanges({ path, busy, turn }: {
  path: string; busy: boolean; turn?: string
}) {
  const [changes, setChanges] = useState<api.Changes | null>(null)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(() => document.documentElement.dataset.mobileLayout === 'desktop')
  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const data = await api.workDiff(path)
        if (alive) { setChanges(data); setError('') }
      } catch (err) {
        if (alive) setError(String(err instanceof Error ? err.message : err))
      } finally {
        if (alive) timer = setTimeout(poll, busy ? 1000 : 5000)
      }
    }
    void poll()
    return () => { alive = false; clearTimeout(timer) }
  }, [path, busy, turn])
  const lines = changes?.diff.split('\n') ?? []
  const { added = 0, deleted = 0, files = 0, binary = 0, unknown = 0 } = changes?.totals ?? {}
  return (
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)} className="live-changes min-w-0 rounded-lg border border-border bg-card">
      <summary className="cursor-pointer px-4 py-3 text-[14px] font-semibold">
        코드 변경 현황 <span className="font-normal text-muted-foreground"><span className="live-change-mode">· {busy ? '실시간' : '현재 변경'}</span> · {files}개 파일</span>
        {' '}<span className="text-add">+{added}</span> <span className="text-del">−{deleted}</span>
        {!!binary && <span className="font-normal text-muted-foreground"> · 바이너리 {binary}개</span>}
        {!!unknown && <span className="font-normal text-muted-foreground"> · 줄 수 미확인 {unknown}개</span>}
      </summary>
      {error && <p role="status" className="px-4 pb-3 text-[14px] text-destructive">{error}{changes && ' · 마지막으로 읽은 변경을 표시한다'}</p>}
      {!changes?.diff && !error && <p className="px-4 pb-3 text-[14px] text-muted-foreground">{changes ? '코드 변경 없음' : '변경 읽는 중…'}</p>}
      {changes?.diff && <pre tabIndex={0} aria-label="실시간 코드 diff" className="max-h-[35dvh] overflow-auto border-t border-border p-3 font-mono text-[12px] leading-relaxed">
        {lines.map((line, i) => <div key={i} className={cn('min-w-max', line.startsWith('+') ? 'bg-add/10 text-add'
          : line.startsWith('-') ? 'bg-del/10 text-del' : line.startsWith('@@') ? 'text-primary' : 'text-muted-foreground')}>
          {line || ' '}
        </div>)}
      </pre>}
      {changes?.truncated && <p className="px-4 py-2 text-[14px] text-muted-foreground">변경이 커서 일부만 표시한다. 전체 diff는 터미널에서 확인할 수 있다.</p>}
      {!!changes?.omitted.length && <p className="px-4 py-2 text-[14px] text-muted-foreground">내용 생략: {changes.omitted.join(', ')} · 큰 파일 또는 심볼릭 링크</p>}
    </details>
  )
}
