import { useEffect, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { get, json, post } from '@/lib/api'
import { leave } from '@/lib/leave'
import { Btn } from '@/components/Modal'
import { openPreferences } from '@/lib/desktop'

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

type PackageUpdate = { packaged: boolean; current?: string; available: boolean; version?: string; notes?: string
  releases?: { version: string; notes: string; date?: string }[] }

export function UpdatesWindow() {
  return <InstalledUpdateCard windowed />
}

function InstalledUpdateCard({ windowed = false }: { windowed?: boolean }) {
  const [update, setUpdate] = useState<PackageUpdate | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState('')
  const generation = useRef(0)
  const checking = useRef(0)
  useEffect(() => {
    const mine = ++generation.current
    let timer: ReturnType<typeof setTimeout>
    const load = async () => {
      const ticket = ++checking.current
      try {
        const value = await invoke<PackageUpdate>('package_update')
        if (mine === generation.current && ticket === checking.current) { setUpdate(value); setError('') }
      } catch (err) { if (mine === generation.current && ticket === checking.current) setError(`업데이트 확인 실패: ${String(err)}. 다시 확인하세요.`) }
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
    const ticket = ++checking.current
    try { const value = await invoke<PackageUpdate>('package_update'); if (mine === generation.current && ticket === checking.current) setUpdate(value) }
    catch (err) { if (mine === generation.current && ticket === checking.current) setError(String(err)) }
    finally { if (mine === generation.current && ticket === checking.current) setBusy(false) }
  }
  async function install() {
    const mine = generation.current
    if (!update?.available || !update.version || !await leave()) return
    if (mine !== generation.current) return
    setBusy(true); setError(''); setProgress('다운로드 시작…')
    try { await invoke('package_install', { version: update.version }) }
    catch (err) { if (mine === generation.current) setError(`업데이트 실패: ${String(err)}. 앱과 기록을 유지했다. 다시 시도하세요.`) }
    finally { if (mine === generation.current) setBusy(false) }
  }
  if (!windowed) return <section aria-label="설치 업데이트" className="border-t border-sidebar-border p-3 text-[12.5px]">
    <Btn onClick={() => void openPreferences('updates').catch((e) => setError(String(e)))}>{update?.available ? `새 버전 ${update.version}` : '업데이트 확인'}</Btn>
    {error && <p role="alert" className="text-destructive">{error}</p>}
  </section>
  const history = Array.isArray(update?.releases) ? update.releases.filter((r) => r && typeof r.version === 'string' && typeof r.notes === 'string').slice(0, 100) : []
  const releases = history.length ? history : update?.version ? [{ version: update.version, notes: update.notes || '이 버전의 변경 기록이 제공되지 않았다.' }] : []
  return <main className="preferences-window" aria-label="앱 업데이트">
    <header className="preferences-header"><div><h1>앱 업데이트</h1><p>설치 버전 {update?.current ?? '확인 중…'} · 게시 버전 {update?.version ?? '확인 중…'}</p></div>
      <Btn disabled={busy} onClick={() => void check()}>{busy ? '확인 중…' : '업데이트 확인'}</Btn></header>
    <div className="preferences-content">
      <section className="space-y-3 border-b border-border pb-4"><h2 className="font-heading text-[14px] font-semibold" role="status">
        {!update ? '업데이트를 확인하는 중…' : !update.packaged ? '개발 실행에서는 소스 업데이트를 사용하세요' : update.available ? `새 버전 ${update.version}을 설치할 수 있다` : '최신 버전을 사용하고 있다'}</h2>
        <p className="text-muted-foreground">업데이트 서명을 확인한 뒤 모든 창을 닫고 설치한다. 설정과 개인 기록은 유지한다.</p>
        {update?.available && <Btn tone="primary" disabled={busy} onClick={() => void install()}>{busy ? '업데이트 중…' : '업데이트 설치'}</Btn>}
        {progress && <p role="status" className="tabular-nums">{progress}</p>}
        {error && <p role="alert" className="break-all text-destructive">{error}</p>}
      </section>
      <section aria-label="버전별 변경 기록">{releases.map((release) => <article className="release-entry" key={release.version}>
        <h2>v{release.version} {typeof release.date === 'string' && <time className="ml-2 font-mono text-[12px] font-normal text-muted-foreground">{release.date}</time>}</h2>
        <pre>{release.notes}</pre>
      </article>)}</section>
    </div>
  </main>
}
