import { useEffect, useRef, useState, type ReactNode } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { Btn } from '@/components/Modal'
import { remote } from '@/lib/mobile'

type Agent = 'codex' | 'claude'
type Setup = { packaged: boolean; complete: boolean; agents: Agent[]; data: string; version: string
  task: { running: boolean; action: string; output: string; error: string }; restart_required: boolean }
type Detection = { tools: Record<string, { ready: boolean; version?: string; error?: string }>
  accounts: Record<string, { authenticated: boolean; error: string }> }

async function request<T>(path = '', body?: unknown): Promise<T> {
  const response = await fetch(`/api/onboarding${path}`, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  const value = await response.json()
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '준비 요청을 처리하지 못했다')
  return value as T
}

export function OnboardingGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Setup | null>(null)
  const [fault, setFault] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (remote) return
    let generation = true
    request<Setup>().then((value) => { if (generation) { setState(value); setFault('') } },
      (error) => { if (generation) setFault(String(error)) })
    return () => { generation = false }
  }, [retry])
  if (remote || state && (!state.packaged || state.complete)) return children
  if (state) return <main className="preparation-window"><header className="preferences-header">
    <div><h1>이 PC에서 시작하기</h1><p>계정을 연결한 뒤 프로젝트 경로는 앱에서 입력하세요. 다른 계정은 나중에 설정에서 추가할 수 있다.</p></div>
    <span className="font-mono text-muted-foreground">v{state.version}</span>
  </header><SetupPanel initial={state} /></main>
  return <main className="p-6"><p role="status">설치 상태를 확인하는 중…</p>
    {fault && <><p role="alert">{fault}</p><Btn onClick={() => setRetry((n) => n + 1)}>다시 시도</Btn></>}</main>
}

const names: Record<string, string> = { git: 'Git', pwsh: 'PowerShell 7', gh: 'GitHub', fd: 'fd', jq: 'jq', codex: 'Codex', claude: 'Claude Code' }
const keys = ['GEMINI_API_KEY', 'TYPESAFE_API_KEY', 'LANGFUSE_BASE_URL', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY']

/** Machine-local setup; every refresh and action owns its result generation. */
export function SetupPanel({ initial }: { initial?: Setup }) {
  const [state, setState] = useState<Setup | null>(initial ?? null)
  const [detected, setDetected] = useState<Detection | null>(null)
  const [tab, setTab] = useState('accounts')
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState(false)
  const [source, setSource] = useState('')
  const [values, setValues] = useState<Record<string, string>>({})
  const generation = useRef(0)
  const sequence = useRef(0)
  const acting = useRef(false)
  useEffect(() => {
    const mine = ++generation.current
    let timer: ReturnType<typeof setTimeout>
    let running = false
    let checked = false
    const load = async () => {
      if (acting.current) { timer = setTimeout(load, 1000); return }
      const ticket = ++sequence.current
      try {
        const next = await request<Setup>()
        if (mine !== generation.current || ticket !== sequence.current) return
        setState(next)
        if (running && !next.task.running || !checked) {
          const result = await request<Detection>('/detect')
          if (mine === generation.current && ticket === sequence.current) { setDetected(result); checked = true }
        }
        running = next.task.running
      } catch (error) { if (mine === generation.current && ticket === sequence.current) setFault(String(error)) }
      finally { if (mine === generation.current) timer = setTimeout(load, 2000) }
    }
    void load()
    return () => { generation.current = mine + 1; clearTimeout(timer) }
  }, [])

  async function act(path: string, body: unknown = {}) {
    const mine = generation.current
    const ticket = ++sequence.current
    acting.current = true
    setFault(''); setBusy(true)
    if (path === '/configure' && state) setState({ ...state, agents: (body as { agents: Agent[] }).agents })
    try {
      await request(path, body)
      const next = await request<Setup>()
      if (mine !== generation.current || ticket !== sequence.current) return
      setState(next)
      if (path === '/finish') {
        if ('__TAURI_INTERNALS__' in window) await invoke('preparation_finished')
        if (mine === generation.current) location.href = '/'
        return
      }
      if (path === '/keys') { setValues({}); setFault('서비스 설정을 저장했다') }
      if (!next.task.running) {
        const result = await request<Detection>('/detect')
        if (mine === generation.current && ticket === sequence.current) setDetected(result)
      }
    } catch (error) {
      if (mine === generation.current && ticket === sequence.current) {
        setFault(String(error))
        if (path === '/configure') setState(state)
      }
    }
    finally { if (mine === generation.current) { acting.current = false; setBusy(false) } }
  }
  if (!state) return <p role="status">PC 준비 상태를 읽는 중…</p>
  const locked = busy || state.task.running
  return <div className="setup-panel">
    <nav aria-label="PC 준비 항목" className="setup-tabs">
      {Object.entries({ accounts: '계정 연결', tools: '필수 도구', keys: '서비스 키', data: '데이터 및 가져오기' }).map(([id, title]) =>
        <button key={id} aria-current={tab === id ? 'page' : undefined} onClick={() => setTab(id)}>{title}</button>)}
    </nav>
    <div className="setup-content">
      {tab === 'accounts' && <section aria-label="계정 연결" className="space-y-3">
        <div className="flex items-center justify-between gap-3"><p>Claude와 Codex를 함께 사용할 수 있다. 선택한 에이전트의 로그인까지 확인한다.</p>
          <Btn disabled={locked} onClick={() => void act('/configure', { agents: state.agents })}>상태 확인</Btn></div>
        <div className="account-table">
          <div className="account-row account-heading"><span>사용할 계정</span><span>설치 · 로그인 상태</span><span>관리</span></div>
          {(['codex', 'claude', 'gh'] as const).map((name) => {
            const tool = detected?.tools[name]
            const auth = detected?.accounts[name]
            return <div className="account-row" key={name}>
              {name === 'gh' ? <span className="font-semibold">GitHub <span className="text-muted-foreground font-normal">선택 사항</span></span>
                : <label className="flex items-center gap-2 font-semibold"><input type="checkbox" aria-label={`${names[name]} 사용`}
                  checked={state.agents.includes(name)} disabled={locked || state.agents.length === 1 && state.agents.includes(name)}
                  onChange={(event) => void act('/configure', { agents: event.target.checked ? [...state.agents, name] : state.agents.filter((a) => a !== name) })} />{names[name]}</label>}
              <div><p role="status">{!tool ? '확인 중…' : !tool.ready ? '설치 필요' : auth?.authenticated ? '연결됨' : '로그인 필요'}</p>
                <p className="text-muted-foreground break-all text-[12px]">{tool?.ready ? tool.version : tool?.error}{auth?.error}</p></div>
              <div className="flex flex-wrap justify-end gap-2"><Btn disabled={locked} onClick={() => void act('/install', { tool: name })}>{tool?.ready ? '업데이트' : '설치'}</Btn>
                <Btn disabled={locked || !tool?.ready} onClick={() => void act('/login', { provider: name })}>{auth?.authenticated ? '로그인 확인' : '로그인'}</Btn></div>
            </div>
          })}
        </div>
        <p className="text-muted-foreground">공식 화면에서 본인 계정을 승인하세요. 인증 정보는 각 CLI가 보관한다. GitHub 연결은 PR과 저장소 작업에 사용한다.</p>
        {!state.complete && <section aria-label="필수 도구 요약" className="setup-tools-summary">
          {['git', 'pwsh', 'gh', 'fd', 'jq'].map((name) => <div key={name}>
            <span className="font-semibold">{names[name]}</span><span className="text-muted-foreground">{!detected ? '확인 중…' : detected.tools[name]?.ready ? '사용 가능' : '설치 필요'}</span>
            {detected && !detected.tools[name]?.ready && <Btn disabled={locked} onClick={() => void act('/install', { tool: name })}>설치</Btn>}
          </div>)}
        </section>}
      </section>}
      {tab === 'tools' && <section aria-label="필수 도구"><p className="mb-3">호환되는 설치는 그대로 사용한다. 설치 후 실제 실행을 다시 확인한다.</p>
        {['git', 'pwsh', 'gh', 'fd', 'jq'].map((name) => <div className="settings-row" key={name}><span>{names[name]}</span>
          <span className="text-muted-foreground break-all">{detected?.tools[name]?.version || detected?.tools[name]?.error || '확인 중…'}</span>
          <Btn disabled={locked} onClick={() => void act('/install', { tool: name })}>{detected?.tools[name]?.ready ? '업데이트' : '설치'}</Btn></div>)}</section>}
      {tab === 'keys' && <section aria-label="서비스 키"><p className="mb-3">선택 사항이다. 빈 입력은 기존 값을 유지한다. 외부 서비스 사용에는 해당 서비스 비용이 발생할 수 있다.</p>
        {keys.map((name) => <label key={name} className="settings-row"><span className="font-mono text-[12px]">{name}</span><input className="preferences-input" type={name.endsWith('URL') ? 'url' : 'password'} autoComplete="off"
          value={values[name] ?? ''} disabled={locked} onChange={(event) => setValues((old) => ({ ...old, [name]: event.target.value }))} /></label>)}
        <div className="mt-3 flex justify-end"><Btn disabled={locked || !Object.values(values).some(Boolean)} onClick={() => void act('/keys', { values: Object.fromEntries(Object.entries(values).filter(([, v]) => v)) })}>서비스 키 저장</Btn></div></section>}
      {tab === 'data' && <section aria-label="데이터 및 가져오기" className="space-y-3"><p>설정과 기록은 앱을 교체해도 유지된다.</p><p className="font-mono text-[12px] break-all">{state.data}</p>
        <h2 className="font-semibold">기존 소스 설치에서 가져오기</h2><p>기존 앱과 세션을 먼저 닫으세요. 빈 사용자 데이터에만 가져온다. 변환 전에 복구 사본을 만들고 원본은 보존한다.</p>
        <label className="settings-row"><span>기존 wiki-agent 폴더</span><input className="preferences-input" value={source} disabled={locked} onChange={(event) => setSource(event.target.value)} placeholder="D:\팀 작업\wiki-agent" /></label>
        <Btn disabled={locked || !source} onClick={() => void act('/import', { source })}>복사하여 가져오기</Btn>
      </section>}
    </div>
    <section aria-label="준비 진행" className="setup-progress">
      <div className="flex items-center justify-between gap-3"><span role="status">{state.task.running ? `${state.task.action} 진행 중…` : '설치와 로그인 결과'}</span>
        {state.task.running && <Btn onClick={() => void act('/cancel')}>취소</Btn>}</div>
      <pre role="log" aria-live="polite">{state.task.output || '설치나 로그인을 시작하면 진행 기록이 여기에 표시된다.'}</pre>
      {(fault || state.task.error) && <p role="alert" className="break-all text-destructive">{fault || state.task.error}</p>}
    </section>
    {state.restart_required ? <div className="setup-footer"><p>가져온 기록을 읽으려면 앱을 다시 시작하세요.</p><Btn disabled={locked} onClick={() => void invoke('restart').catch((e) => setFault(String(e)))}>다시 시작</Btn></div>
      : !state.complete && <div className="setup-footer"><p>실제 hook 시험은 프로젝트 연결 후 별도로 진행한다.</p><Btn tone="primary" disabled={locked} onClick={() => void act('/finish')}>준비 확인하고 앱 열기</Btn></div>}
  </div>
}
