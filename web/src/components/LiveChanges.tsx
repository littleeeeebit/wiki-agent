import { useEffect, useState } from 'react'
import * as api from '@/lib/api'
import { cn } from '@/lib/utils'

/** Poll actual Git state while the agent runs, regardless of the host's edit tool. */
export function LiveChanges({ path, busy, turn }: {
  path: string; busy: boolean; turn?: string
}) {
  const [changes, setChanges] = useState<api.Changes | null>(null)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)
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
  const { added = 0, deleted = 0, files = 0, binary = 0, unknown = 0 } = changes?.totals ?? {}
  return (
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)} className="live-changes min-w-0 border-b border-border bg-card">
      <summary className="cursor-pointer px-5 py-3 text-[12.5px]">
        코드 변경 현황 <span className="font-normal text-muted-foreground"><span className="live-change-mode">· {busy ? '실시간' : '현재 변경'}</span> · {files}개 파일</span>
        {' '}<span className="text-add">+{added}</span> <span className="text-del">−{deleted}</span>
        {!!binary && <span className="font-normal text-muted-foreground"> · 바이너리 {binary}개</span>}
        {!!unknown && <span className="font-normal text-muted-foreground"> · 줄 수 미확인 {unknown}개</span>}
      </summary>
      {error && <p role="status" className="px-4 pb-3 text-[12.5px] text-destructive">{error}{changes && ' · 마지막으로 읽은 변경을 표시한다'}</p>}
      {!files && !error && <p className="px-4 pb-3 text-[12.5px] text-muted-foreground">{changes ? '코드 변경 없음' : '변경 읽는 중…'}</p>}
      {open && <div className="max-h-[35dvh] overflow-y-auto border-t border-border" aria-label="변경된 파일 목록">
        {changes?.files.map((file) => <FileChange key={file.path} file={file} path={path} busy={busy} turn={turn} />)}
      </div>}
    </details>
  )
}

function FileChange({ file, path, busy, turn }: { file: api.ChangedFile; path: string; busy: boolean; turn?: string }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<api.Changes | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!open) return
    let generation = true
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const next = await api.workDiff(path, file.path)
        if (generation) { setData(next); setError('') }
      } catch (err) {
        if (generation) setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (generation) timer = setTimeout(poll, busy ? 1000 : 5000)
      }
    }
    void poll()
    return () => { generation = false; clearTimeout(timer) }
  }, [open, path, file.path, busy, turn])
  return <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)} className="border-b border-border last:border-b-0">
    <summary className="cursor-pointer px-4 py-2 text-[12.5px]">
      <span className="break-all font-mono text-[12px]">{file.path}</span>
      {' '}<span className="text-muted-foreground">{file.untracked ? '새 파일' : '변경'}{file.binary && ' · 바이너리'}</span>
      {file.added !== null && <> <span className="text-add">+{file.added}</span> <span className="text-del">−{file.deleted}</span></>}
    </summary>
    {open && <>
      {error && <p role="status" className="px-4 py-2 text-[12.5px] text-destructive">{error}</p>}
      {!error && !data && <p className="px-4 py-2 text-[12.5px] text-muted-foreground">diff 읽는 중…</p>}
      {data?.diff && <pre tabIndex={0} aria-label={`${file.path} diff`} className="max-h-[25dvh] overflow-auto border-t border-border p-3 font-mono text-[12px] leading-relaxed">
        {data.diff.split('\n').map((line, i) => <div key={i} className={cn('min-w-max', line.startsWith('+') && !line.startsWith('+++') ? 'bg-add/10 text-add'
          : line.startsWith('-') && !line.startsWith('---') ? 'bg-del/10 text-del' : line.startsWith('@@') ? 'text-primary' : 'text-muted-foreground')}>{line || ' '}</div>)}
      </pre>}
      {data && !data.diff && !error && <p className="px-4 py-2 text-[12.5px] text-muted-foreground">{file.binary ? '바이너리 파일은 텍스트 diff가 없다' : '표시할 텍스트 변경이 없다'}</p>}
      {data?.truncated && <p className="px-4 py-2 text-[12.5px] text-muted-foreground">이 파일의 diff가 커서 일부만 표시한다. 전체 내용은 Git에서 확인할 수 있다.</p>}
      {data?.omitted.includes(file.path) && <p className="px-4 py-2 text-[12.5px] text-muted-foreground">큰 파일 또는 심볼릭 링크의 내용은 생략한다</p>}
    </>}
  </details>
}
