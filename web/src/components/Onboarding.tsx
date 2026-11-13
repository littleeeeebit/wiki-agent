import { useEffect, useRef, useState, type ReactNode } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { Btn } from '@/components/Modal'
import { remote } from '@/lib/mobile'

type Task = { running: boolean; action: string; output: string; error: string; code: number | null }
type Setup = { packaged: boolean; complete: boolean; agent: 'codex' | 'claude'; workspace: string
  data: string; version: string; task: Task; restart_required: boolean }
type Detection = { tools: Record<string, { ready: boolean; path?: string; version?: string; error?: string }>
  authenticated: boolean; auth_error: string; winget: boolean }

async function request<T>(path = '', body?: unknown): Promise<T> {
  const response = await fetch(`/api/onboarding${path}`, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  const value = await response.json()
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '준비 요청을 처리하지 못했다')
  return value as T
}

/** Setup belongs to the local machine. Requests carry no project or provider credentials.
 * Each mounted screen owns a generation; retired requests cannot change its state. */
export function OnboardingGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Setup | null>(null)
  const [fault, setFault] = useState('')
  const [retry, setRetry] = useState(0)
  const generation = useRef(0)
  useEffect(() => {
    if (remote) return
    const mine = ++generation.current
    request<Setup>().then((value) => { if (mine === generation.current) { setState(value); setFault('') } },
      (error) => { if (mine === generation.current) setFault(String(error)) })
    return () => { generation.current = mine + 1 }
  }, [retry])
  if (remote || state && (!state.packaged || state.complete && !new URLSearchParams(location.search).has('onboarding'))) return children
  if (state) return <Onboarding initial={state} />
  return <main className="min-h-screen bg-background p-6 text-foreground">
    <p role="status">설치 상태를 확인하는 중…</p>
    {fault && <><p role="alert">{fault}</p><Btn onClick={() => setRetry((n) => n + 1)}>다시 시도</Btn></>}
  </main>
}

const field = 'h-9 w-full min-w-0 rounded-md border border-input bg-background px-3 text-[13.5px]'
const names: Record<string, string> = { git: 'Git', pwsh: 'PowerShell 7', gh: 'GitHub CLI', fd: 'fd', jq: 'jq', codex: 'Codex', claude: 'Claude Code' }

function Onboarding({ initial }: { initial: Setup }) {
  const [state, setState] = useState(initial)
  const [agent, setAgent] = useState(initial.agent)
  const [workspace, setWorkspace] = useState(initial.workspace)
  const [source, setSource] = useState('')
  const [detected, setDetected] = useState<Detection | null>(null)
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState(false)
  const [values, setValues] = useState<Record<string, string>>({})
  const generation = useRef(0)
  const listing = useRef(0)
  const detection = useRef(0)
  useEffect(() => {
    const mine = ++generation.current
    let timer: ReturnType<typeof setTimeout>
    const load = async () => {
      const ticket = ++listing.current
      try {
        const next = await request<Setup>()
        if (mine !== generation.current || ticket !== listing.current) return
        setState(next)
      } catch (error) { if (mine === generation.current) setFault(String(error)) }
      finally { if (mine === generation.current) timer = setTimeout(load, 1000) }
    }
    void load()
    const check = ++detection.current
    request<Detection>('/detect').then((value) => { if (mine === generation.current && check === detection.current) setDetected(value) },
      (error) => { if (mine === generation.current && check === detection.current) setFault(String(error)) })
    return () => { generation.current = mine + 1; listing.current = 0; clearTimeout(timer) }
  }, [])

  async function act(path: string, body: unknown = {}) {
    const mine = generation.current
    const check = ++detection.current
    listing.current++
    setFault(''); setBusy(true)
    try {
      await request(path, body)
      const next = await request<Setup>()
      if (mine !== generation.current) return
      setState(next)
      if (path === '/configure') setDetected(null)
      if (path === '/keys') { setValues({}); setFault('서비스 설정을 저장했다. 기존 기록과 계정은 유지한다') }
      if (path === '/finish') { location.href = '/'; return }
      if (!next.task.running) {
        const result = await request<Detection>('/detect')
        if (mine === generation.current && check === detection.current) setDetected(result)
      }
    } catch (error) { if (mine === generation.current) setFault(String(error)) }
    finally { if (mine === generation.current) setBusy(false) }
  }
  const locked = busy || state.task.running
  async function browse() {
    const mine = generation.current
    setBusy(true); setFault('')
    try {
      const result = await request<{ path: string }>('/browse', {})
      if (mine === generation.current && result.path) setWorkspace(result.path)
    } catch (error) { if (mine === generation.current) setFault(String(error)) }
    finally { if (mine === generation.current) setBusy(false) }
  }
  return <main className="min-h-screen overflow-auto bg-background px-4 py-8 text-foreground">
    <div className="mx-auto max-w-[720px] space-y-6 text-[13.5px] leading-relaxed">
      <header className="space-y-2">
        <p className="font-mono text-[10.5px] text-muted-foreground">wiki-agent {state.version}</p>
        <h1 className="font-heading text-[16px] font-semibold">이 PC에서 시작하기</h1>
        <p>사용할 에이전트와 프로젝트를 준비한다. 기존 도구와 로그인은 그대로 사용한다.</p>
        <p className="break-all text-muted-foreground">설정과 기록: {state.data}</p>
      </header>
      <details className="rounded-md border border-border p-4">
        <summary className="cursor-pointer font-heading text-[14px] font-semibold">기존 소스 설치에서 가져오기</summary>
        <div className="mt-3 space-y-3">
          <p>기존 앱과 에이전트 세션을 먼저 닫으세요. 빈 사용자 데이터에만 가져온다. 변환 전에 복구 사본을 만들고 원본은 보존한다.</p>
          <label className="block">기존 wiki-agent 폴더<input className={field} value={source} disabled={locked}
            onChange={(event) => setSource(event.target.value)} placeholder="D:\팀 작업\wiki-agent" /></label>
          <Btn disabled={locked || !source} onClick={() => void act('/import', { source })}>복사하여 가져오기</Btn>
        </div>
      </details>
      {state.restart_required ? <section className="space-y-3" aria-label="가져오기 완료">
        <p>가져온 기록을 읽으려면 앱을 다시 시작하세요.</p>
        <Btn disabled={locked} onClick={() => void invoke('restart').catch((error) => setFault(String(error)))}>다시 시작</Btn>
      </section> : <>
        <section className="space-y-3" aria-labelledby="setup-agent">
          <h2 id="setup-agent" className="font-heading text-[14px] font-semibold">1. 에이전트 선택</h2>
          <div className="flex flex-wrap items-end gap-3">
            <label className="min-w-44 flex-1">사용할 도구<select className={field} value={agent} disabled={locked}
              onChange={(event) => { detection.current++; setAgent(event.target.value as typeof agent); setDetected(null) }}>
              <option value="codex">Codex</option><option value="claude">Claude Code</option>
            </select></label>
            <Btn disabled={locked} onClick={() => void act('/configure', { agent })}>선택하고 확인</Btn>
          </div>
          {!detected && <p role="status">선택한 에이전트를 확인한 뒤 도구 상태를 읽으세요.</p>}
          {detected && agent === state.agent && <ul className="divide-y divide-border rounded-md border border-border px-3">
            {Object.entries(detected.tools).map(([name, tool]) => <li key={name} className="flex flex-wrap items-center justify-between gap-2 py-3">
              <div className="min-w-0 flex-1"><span>{names[name]} · {tool.ready ? '사용 가능' : '준비 필요'}</span>
                <p className="break-all font-mono text-[10.5px] text-muted-foreground">{tool.version || tool.error}</p></div>
              {!tool.ready && <Btn disabled={locked} onClick={() => void act('/install', { tool: name })}>설치</Btn>}
            </li>)}
          </ul>}
          <Btn disabled={locked || agent !== state.agent} onClick={() => void act('/configure', { agent })}>상태 다시 확인</Btn>
        </section>
        <section className="space-y-3" aria-labelledby="setup-login">
          <h2 id="setup-login" className="font-heading text-[14px] font-semibold">2. 본인 계정으로 로그인</h2>
          <p>공식 로그인 화면에서 승인하세요. 앱은 CLI의 토큰을 저장하거나 복사하지 않는다.</p>
          <p role="status">{detected?.authenticated ? '로그인 확인됨' : '로그인 확인 필요'}</p>
          {detected?.auth_error && <p role="alert">{detected.auth_error}</p>}
          <Btn disabled={locked || agent !== state.agent} onClick={() => void act('/login')}>공식 로그인 열기</Btn>
        </section>
        <section className="space-y-3" aria-labelledby="setup-workspace">
          <h2 id="setup-workspace" className="font-heading text-[14px] font-semibold">3. 프로젝트 위치</h2>
          <label className="block">Git 프로젝트들이 들어 있는 폴더<input className={field} value={workspace} disabled={locked}
            onChange={(event) => setWorkspace(event.target.value)} /></label>
          <div className="flex flex-wrap gap-3"><Btn disabled={locked} onClick={() => void browse()}>폴더 찾기</Btn>
            <Btn disabled={locked || !workspace} onClick={() => void act('/configure', { agent, workspace })}>폴더 저장</Btn></div>
        </section>
        <details className="rounded-md border border-border p-4">
          <summary className="cursor-pointer font-heading text-[14px] font-semibold">선택 사항 · 서비스 키</summary>
          <div className="mt-3 space-y-3">
            <p>번역, Jev, 추적 서비스 키를 이 PC에 저장한다. 빈 입력은 기존 값을 유지한다. 외부 서비스 사용에는 해당 서비스 비용이 발생할 수 있다.</p>
            {['GEMINI_API_KEY', 'TYPESAFE_API_KEY', 'LANGFUSE_BASE_URL', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY'].map((name) =>
              <label key={name} className="block font-mono text-[12px]">{name}<input className={field}
                type={name.endsWith('URL') ? 'url' : 'password'} autoComplete="off" value={values[name] ?? ''} disabled={locked}
                onChange={(event) => setValues((old) => ({ ...old, [name]: event.target.value }))} /></label>)}
            <Btn disabled={locked || !Object.values(values).some(Boolean)} onClick={() => void act('/keys', {
              values: Object.fromEntries(Object.entries(values).filter(([, value]) => value)),
            })}>서비스 키 저장</Btn>
          </div>
        </details>
        <footer className="space-y-3 border-t border-border pt-4">
          <p>준비 확인 후 앱에서 프로젝트를 연결하세요. 실제 hook 시험은 에이전트 사용량을 쓸 수 있으므로 연결 화면에서 따로 시작한다.</p>
          <Btn tone="primary" disabled={locked || agent !== state.agent} onClick={() => void act('/finish')}>준비 확인하고 앱 열기</Btn>
        </footer>
      </>}
      <section aria-label="준비 진행" className="space-y-2">
        {state.task.running && <div className="flex flex-wrap items-center gap-3"><p role="status">{state.task.action} 진행 중…</p>
          <Btn onClick={() => void act('/cancel')}>취소</Btn></div>}
        {state.task.output && <pre role="log" aria-live="polite" className="max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted p-3 font-mono text-[12px]">{state.task.output}</pre>}
        {(fault || state.task.error) && <p role="alert" className="whitespace-pre-wrap break-all text-destructive">{fault || state.task.error}</p>}
      </section>
    </div>
  </main>
}
