import { useEffect, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { get, json, post } from '@/lib/api'
import { leave } from '@/lib/leave'
import { Btn } from '@/components/Modal'

/** `origin/main` against the build this server loaded (`tool/main/update.py`). */
type UpdateState = { state: 'current' | 'unknown' | 'available' | 'ready'; running?: string; remote?: string
  behind?: number; commits?: { sha: string; subject: string; at: string }[]; blocked?: string; error?: string }
const getUpdate = () => get('/api/update').then((r) => json<UpdateState>(r, '업데이트 확인'))
const applyUpdate = () => post('/api/update').then((r) => json<UpdateState>(r, '업데이트'))

/** In the app window the card restarts it; a browser tab can only say how. */
const WINDOW = '__TAURI_INTERNALS__' in window
const RESTART = navigator.userAgent.includes('Windows') ? 'tool\\app.cmd' : 'tool/app.command'

function hidden(): string {
  try { return localStorage.getItem('update-later') ?? '' } catch { return '' }
}

/** Orca's update card: quiet until there is something, never a dialog, and
 *  "나중에" hides only the version it was pressed for. */
export function UpdateCard() {
  const [data, setData] = useState<UpdateState | null>(null)
  const [later, setLater] = useState(hidden)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    const load = async () => {
      // The server fetches at most hourly; this only reads its answer.
      try { const found = await getUpdate(); if (alive) setData(found) } catch { /* A failed check stays silent. */ }
      finally { if (alive) timer = setTimeout(load, 600_000) }
    }
    void load()
    return () => { alive = false; clearTimeout(timer) }
  }, [])
  if (!data || (data.state !== 'available' && data.state !== 'ready') || (data.state === 'available' && later === data.remote)) return null
  const ready = data.state === 'ready'
  const update = async () => {
    setBusy(true)
    setError('')
    try {
      const found = await applyUpdate()
      setData(found)
      if (found.error) setError(found.error)
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err))
    } finally {
      setBusy(false)
    }
  }
  // The shell runs the launcher once the server is down (`restart` in main.rs).
  const restart = async () => {
    if (!leave()) return
    setBusy(true)
    try {
      await invoke('restart')
    } catch (err) {
      setBusy(false)
      setError(String(err instanceof Error ? err.message : err))
    }
  }
  return <div aria-label="업데이트" className="shrink-0 border-t border-sidebar-border p-3 text-[12.5px] leading-relaxed">
    <details>
      <summary className="cursor-pointer font-heading text-[11px] font-semibold text-st-ready">
        <span className="rail-expanded max-[1280px]:hidden">{ready ? '업데이트 준비됨 · 재시작 필요' : `업데이트 있음 · ${data.behind}개 커밋`}</span>
        <span className="rail-folded min-[1280px]:hidden">업데이트</span>
      </summary>
      <div className="mt-2 space-y-2">
        {ready ? WINDOW ? <>
          <p>받은 업데이트({data.remote})는 다시 시작하면 적용됩니다.</p>
          <Btn tone="primary" disabled={busy} onClick={() => void restart()}>{busy ? '다시 시작하는 중…' : '다시 시작'}</Btn>
        </> : <p>받은 업데이트({data.remote})는 앱을 다시 열면 적용됩니다. 창을 닫고 <code className="font-mono text-[12px]">{RESTART}</code> 로 다시 여세요.</p> : <>
          <p className="text-muted-foreground">실행 중 <span className="font-mono">{data.running}</span> → origin/main <span className="font-mono">{data.remote}</span></p>
          <ul className="max-h-40 space-y-1 overflow-y-auto">
            {data.commits?.map((c) => <li key={c.sha} className="flex gap-2"><span className="shrink-0 font-mono text-[10.5px] text-muted-foreground">{c.sha}</span><span className="min-w-0">{c.subject}</span></li>)}
          </ul>
          {data.blocked && <p role="status" className="text-wait">{data.blocked} — 터미널에서 직접 <code className="font-mono text-[12px]">git pull</code> 하세요.</p>}
          <div className="flex gap-2">
            <Btn tone="primary" disabled={busy || !!data.blocked} onClick={() => void update()}>{busy ? '받는 중…' : '업데이트'}</Btn>
            <Btn tone="ghost" disabled={busy} onClick={() => {
              try { localStorage.setItem('update-later', data.remote ?? '') } catch { /* Shown again next load. */ }
              setLater(data.remote ?? '')
            }}>나중에</Btn>
          </div>
        </>}
        {error && <p role="alert" className="text-destructive">{error}</p>}
      </div>
    </details>
  </div>
}
