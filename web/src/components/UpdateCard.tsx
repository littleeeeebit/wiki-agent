import { useEffect, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { get, json, post } from '@/lib/api'
import { leave } from '@/lib/leave'
import { Btn } from '@/components/Modal'

/** `origin/main` against the build this server loaded (`tool/main/update.py`). */
type UpdateState = { state: 'current' | 'unknown' | 'available' | 'ready'; running?: string; remote?: string
  behind?: number; commits?: { sha: string; subject: string; at: string }[]; blocked?: string; error?: string; later?: string; packaged?: boolean }
const getUpdate = () => get('/api/update').then((r) => json<UpdateState>(r, '업데이트 확인'))
const applyUpdate = () => post('/api/update').then((r) => json<UpdateState>(r, '업데이트'))
// Kept by the server: the app's page gets a new port, so new browser storage, every launch.
const putLater = (remote: string) => post('/api/update/later', { remote }).then((r) => json<UpdateState>(r, '나중에'))

/** In the app window the card restarts it; a browser tab can only say how. */
const WINDOW = '__TAURI_INTERNALS__' in window
const RESTART = navigator.userAgent.includes('Windows') ? 'tool\\app.cmd' : 'tool/app.command'

/** Orca's update card: quiet until there is something, never a dialog, and
 *  "나중에" hides only the version it was pressed for. */
export function UpdateCard() {
  const [data, setData] = useState<UpdateState | null>(null)
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
  if (data?.packaged) return WINDOW ? <InstalledUpdateCard /> : null
  if (!data || (data.state !== 'available' && data.state !== 'ready') || (data.state === 'available' && data.later === data.remote)) return null
  const ready = data.state === 'ready'
  // A failed preparation comes back as `data.error`, with the button kept for a retry.
  const update = async () => {
    setBusy(true)
    setError('')
    try {
      setData(await applyUpdate())
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err))
    } finally {
      setBusy(false)
    }
  }
  // The shell runs the launcher once the server is down (`restart` in main.rs).
  const restart = async () => {
    if (!await leave()) return
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
            <Btn tone="ghost" disabled={busy} onClick={() => void putLater(data.remote ?? '').then(setData, (err) =>
              setError(String(err instanceof Error ? err.message : err)))}>나중에</Btn>
          </div>
        </>}
        {(error || data.error) && <p role="alert" className="text-destructive">{error || data.error}</p>}
      </div>
    </details>
  </div>
}

function InstalledUpdateCard() {
  const [update, setUpdate] = useState<{ version?: string; notes?: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState('')
  const generation = useRef(0)
  useEffect(() => {
    const mine = ++generation.current
    let timer: ReturnType<typeof setTimeout>
    const load = async () => {
      try {
        const value = await invoke<{ version?: string; notes?: string }>('package_update')
        if (mine === generation.current) { setUpdate(value); setError('') }
      } catch (err) { if (mine === generation.current) setError(`업데이트 확인 실패: ${String(err)}. 다시 확인하세요.`) }
      finally { if (mine === generation.current) timer = setTimeout(load, 3_600_000) }
    }
    void load()
    const listening = listen<{ received: number; total?: number }>('package-download', ({ payload }) => {
      if (mine === generation.current) setProgress(payload.total ? `${Math.round(payload.received / payload.total * 100)}%` : `${Math.round(payload.received / 1024 / 1024)} MB`)
    })
    return () => { generation.current = mine + 1; clearTimeout(timer); void listening.then((unlisten) => unlisten()) }
  }, [])
  async function check() {
    const mine = generation.current
    setBusy(true); setError('')
    try { const value = await invoke<{ version?: string; notes?: string }>('package_update'); if (mine === generation.current) setUpdate(value) }
    catch (err) { if (mine === generation.current) setError(String(err)) }
    finally { if (mine === generation.current) setBusy(false) }
  }
  async function install() {
    const mine = generation.current
    if (!update?.version || !await leave()) return
    if (mine !== generation.current) return
    setBusy(true); setError(''); setProgress('다운로드 시작…')
    try { await invoke('package_install', { version: update.version }) }
    catch (err) { if (mine === generation.current) setError(`업데이트 실패: ${String(err)}. 앱과 기록을 유지했다. 다시 시도하세요.`) }
    finally { if (mine === generation.current) setBusy(false) }
  }
  return <section aria-label="설치 업데이트" className="shrink-0 border-t border-sidebar-border p-3 text-[12.5px] leading-relaxed">
    <details open={!!update?.version || !!error}>
      <summary className="cursor-pointer font-heading text-[11px] font-semibold">{update?.version ? `새 버전 ${update.version}` : '앱 업데이트'}</summary>
      <div className="mt-2 space-y-2">
        {update?.notes && <p className="whitespace-pre-wrap">{update.notes}</p>}
        <p>업데이트 서명을 확인한 뒤 모든 창을 닫고 설치한다. 개인 기록은 유지한다.</p>
        <div className="flex flex-wrap gap-2"><Btn disabled={busy} onClick={() => void check()}>다시 확인</Btn>
          {update?.version && <Btn tone="primary" disabled={busy} onClick={() => void install()}>{busy ? '업데이트 중…' : '업데이트 설치'}</Btn>}</div>
        {progress && <p role="status">{progress}</p>}
        {error && <p role="alert" className="break-all text-destructive">{error}</p>}
      </div>
    </details>
  </section>
}
