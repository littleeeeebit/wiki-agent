import { useEffect, useState } from 'react'
import { Btn } from '@/components/Modal'
import { Questions } from '@/components/Questions'
import * as api from '@/lib/api'
import { useOverlay } from '@/lib/overlay'

export type RefactorMode = 'cleanup' | 'restructure' | 'full'
export const REFACTOR_MODE: Record<RefactorMode, string> = {
  cleanup: '빠른 정리 · L0–L1', restructure: '모듈 재구성 · L0–L2', full: '전면 리펙터링 · L0–L3',
}
type Hotspot = { path: string; lines: number; dup: number; block: number; churn: number; cap: number; debt: number }
type Scan = { repo: string; rows: Hotspot[]; scope: string }
type Run = {
  id: string; mode: RefactorMode; state: 'running' | 'stopped' | 'done'; phase: string; created: number
  goal?: string; done?: string[]; out?: string[]; spec?: string; plan?: string; spent: { seconds: number; calls: number; tokens: number; unknown?: boolean }
  steps: { n: number; tier: string; files: string[]; goal: string; state: string; spec: string | null; pr?: number }[]
  tests: { spec: string; pr?: number } | null; stopped: { reason: string; detail: string } | null
  scope_change?: api.ChoiceQuestion | { questions: api.ChoiceQuestion[] }
}
const STATE = { running: '실행 중', stopped: '멈춤', done: '리뷰·승인 완료' }
const PHASE: Record<string, string> = { scan: '대상 조사', audit: '범위·단계 검토', plan: '계획 PR 머지 대기',
  tests: '현재 동작을 고정하는 테스트', steps: '단계 PR 진행', done: '단계 완료' }
const STEP: Record<string, string> = { pending: '대기', adopted: '후보 채택', published: 'PR 리뷰 중',
  awaiting: '승인 대기', done: '리뷰 통과' }
const box = 'not-prose rounded-md border border-border bg-card p-3 text-[12.5px]'

/** Diagnosis on entry; choosing a problem starts a discussion, never a run. */
export function RefactorCandidates({ repo, mode, busy, onSay }: {
  repo: string; mode: RefactorMode; busy: boolean; onSay: (text: string) => void
}) {
  const [scan, setScan] = useState<Scan | null>(null)
  const [fault, setFault] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let stale = false
    setScan(null); setFault('')
    api.get('/api/refactors/scan', repo).then((r) => api.json<Scan>(r, '리펙터링 진단')).then((next) => {
      if (!stale && next.repo === repo) setScan(next)
    }).catch((err) => { if (!stale) setFault(String(err)) })
    return () => { stale = true }
  }, [repo, mode, revision])
  return <details open className={`${box} mb-5`}>
    <summary className="cursor-pointer font-heading text-[13px] font-semibold">정리할 문제 찾기 · {REFACTOR_MODE[mode]}</summary>
    <p className="mt-2 text-muted-foreground">문제를 고르거나 원하는 목적을 입력한다. 대화로 완료 조건을 정한 뒤 명세의 [시작]을 누른다.</p>
    {fault && <p role="alert" className="mt-2 text-destructive">{fault}</p>}
    {!scan && !fault && <p role="status" className="mt-2 text-faint">저장소를 조사하는 중…</p>}
    {scan?.scope === 'hub' && <p className="mt-2 text-wait">wiki-agent 자체는 빠른 정리·모듈 재구성을 지원한다. 단계는 별도 작업트리에서 실행하며, 머지 후 앱을 다시 시작한다.</p>}
    {scan && mode === 'full' && <Btn className="mt-2" disabled={busy || scan.scope === 'hub'}
      onClick={() => onSay('Audit the whole repository and help me agree on the structural problems and completion conditions for a full refactoring.')}>
      저장소 전체 구조 검토
    </Btn>}
    {scan && mode !== 'full' && <ul className="mt-3 space-y-2">{scan.rows.map((r) => <li key={r.path}>
      <button type="button" disabled={busy} onClick={() => onSay(`Investigate ${r.path} and its callers. Help me settle the problem, scope and completion conditions for ${mode} refactoring.`)}
        className="w-full min-w-0 rounded-md border border-border p-2.5 text-left hover:bg-secondary disabled:opacity-40">
        <span className="block text-[13.5px]">{mode === 'restructure' ? '모듈 책임과 경계 재구성' : r.dup ? '중복된 처리 정리' : '긴 코드의 책임 분리'}</span>
        <span className="mt-1 block break-all font-mono text-[11.5px] text-muted-foreground">{r.path}</span>
        <span className="mt-1 block text-muted-foreground">{r.dup ? '반복된 코드가 있어 같은 동작을 여러 곳에서 유지한다.' : '코드가 길거나 큰 블록이 있어 동작과 책임을 읽기 어렵다.'} 실제 범위는 호출부를 조사해 정한다.</span>
      </button>
      <details className="mt-1 px-2 text-muted-foreground"><summary className="cursor-pointer">측정 근거 보기</summary>
        줄 {r.lines} / 기준 {r.cap} · 중복 {r.dup} · 최장 블록 {r.block} · 최근 변경 {r.churn} · 부채 {r.debt}
      </details>
    </li>)}</ul>}
    {scan && !scan.rows.length && mode !== 'full' && <p className="mt-2 text-faint">측정 기준을 넘는 후보가 없다. 문제는 대화로 직접 지정할 수 있다.</p>}
    <Btn className="mt-3" disabled={busy || (!scan && !fault)} onClick={() => setRevision((n) => n + 1)}>다시 조사</Btn>
  </details>
}

/** A persisted run updated in place. Usage is disclosed only in details. */
export function RefactorStatus({ repo, id, specs, korean, onSay, onChanged }: {
  repo: string; id: string; specs: api.Spec[]; korean: boolean; onSay: (text: string) => void; onChanged: () => void
}) {
  const [run, setRun] = useState<Run | null>(null)
  const [fault, setFault] = useState('')
  const [working, setWorking] = useState(false)
  useEffect(() => {
    let stale = false
    let timer: number | undefined
    const read = async () => {
      try {
        const next = await api.get(`/api/refactors/${id}`, repo).then((r) => api.json<Run>(r, '리펙터링 진행'))
        if (!stale) { setRun(next); setFault('') }
      } catch (err) { if (!stale) setFault(String(err)) }
      finally { if (!stale) timer = window.setTimeout(read, 3000) }
    }
    void read()
    return () => { stale = true; window.clearTimeout(timer) }
  }, [repo, id])
  const originals = [run?.goal ?? '', ...(run?.steps ?? []).map((s) => s.goal), ...(run?.done ?? []), ...(run?.out ?? [])]
  const shown = useOverlay(originals, korean)
  const translated = (text: string) => shown[originals.indexOf(text)] ?? text
  async function act(action: string) {
    setWorking(true)
    try {
      const next = await api.post(`/api/refactors/${id}/${action}`, undefined, 'POST', repo).then((r) => api.json<Run>(r, '리펙터링'))
      setRun(next); setFault(''); onChanged()
    } catch (err) { setFault(String(err)) }
    finally { setWorking(false) }
  }
  const pr = (sid: string | null | undefined, number?: number) => {
    const spec = specs.find((s) => s.id === sid)
    return spec?.pr ? <a href={spec.pr.url} target="_blank" rel="noreferrer" className="text-primary">PR #{spec.pr.number}</a>
      : number ? <span>PR #{number}</span> : null
  }
  const questions = run?.scope_change ? ('questions' in run.scope_change ? run.scope_change.questions : [run.scope_change]) : []
  return <div className={`${box} mt-3`} aria-label="리펙터링 실행">
    {fault && <p role="alert" className="text-destructive">{fault}</p>}
    {!run && !fault && <p role="status">실행 상태를 읽는 중…</p>}
    {run && <>
      <div className="flex flex-wrap items-center gap-2 font-heading text-[13px] font-semibold">
        <span>{REFACTOR_MODE[run.mode]}</span><span role="status">{STATE[run.state]} · {PHASE[run.phase] ?? run.phase}</span>
      </div>
      {run.goal && <p className="mt-2 break-words text-[13.5px]">{shown[0]}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {run.state === 'running' && <Btn disabled={working} onClick={() => void act('cancel')}>멈추기</Btn>}
        {run.state === 'stopped' && <Btn disabled={working} onClick={() => void act('cancel')}>저장소 놓기</Btn>}
        {run.state === 'stopped' && !run.scope_change && <Btn disabled={working} onClick={() => void act('resume')}>재개</Btn>}
        {run.steps.some((s) => s.state === 'awaiting') && <Btn tone="primary" disabled={working} onClick={() => void act('approve')}>승인</Btn>}
      </div>
      {run.stopped && <p className="mt-2 whitespace-pre-wrap break-words text-destructive">{run.stopped.detail || run.stopped.reason}</p>}
      {!!questions.length && <Questions korean={korean} disabled={working} questions={questions.map((q) => ({
        question: q.question, header: q.header, multiSelect: q.multi, options: q.options.map((o) => ({ label: o.label, description: o.note, preview: o.preview })),
      }))} onSubmit={(answers) => onSay(`Refactoring ${id} stopped because the agreed scope must change. My choices:\n${answers.join('\n')}\nReconsider the specification before starting a replacement run.`)} />}
      {run.tests && <p className="mt-2">현재 동작 고정 · {pr(run.tests.spec, run.tests.pr) || '테스트 작성 중'}</p>}
      {run.plan && <p className="mt-2">계획 · {pr(run.plan) || run.plan}</p>}
      <ol className="mt-2 space-y-1">{run.steps.map((s) => <li key={s.n}>
        {s.n}. [{s.tier}] {STEP[s.state] ?? s.state} {pr(s.spec, s.pr)}
        {!['L0', 'L1'].includes(s.tier) && s.pr && specs.find((p) => p.id === s.spec)?.state === `PR #${s.pr}` &&
          <Btn className="ml-2" disabled={working} onClick={async () => {
            setWorking(true)
            try { await api.reviewSpec(s.spec!, repo); onChanged() }
            catch (err) { setFault(String(err)) }
            finally { setWorking(false) }
          }}>리뷰 시작</Btn>}
      </li>)}</ol>
      <details className="mt-3 text-muted-foreground"><summary className="cursor-pointer">단계·사용량 상세</summary>
        {!!run.done?.length && <div className="mt-2">완료 조건<ul>{run.done.map((s, i) => <li key={i}>· {translated(s)}</li>)}</ul></div>}
        {!!run.out?.length && <div className="mt-2">범위에서 제외<ul>{run.out.map((s, i) => <li key={i}>· {translated(s)}</li>)}</ul></div>}
        <p className="mt-2">작업 시간 {Math.round(run.spent.seconds)}초 · 호출 {run.spent.calls} · 확인된 토큰 {run.spent.tokens.toLocaleString()}
          {run.spent.unknown && ' · 일부 토큰 사용량은 알 수 없음'}</p>
        <p className="mt-1">시작 {new Date(run.created * 1000).toLocaleString('ko-KR')}</p>
        <ol className="mt-2 space-y-2">{run.steps.map((s, i) => <li key={s.n} className="break-words">
          {s.n}. {shown[i + 1]}<span className="block break-all font-mono text-[11.5px]">{s.files.join(', ')}</span>
        </li>)}</ol>
      </details>
      {run.state === 'done' && <p className="mt-2 text-muted-foreground">단계의 리뷰·승인이 끝났다. 각 PR의 머지는 직접 진행한다.</p>}
    </>}
  </div>
}

/** Old runs remain accessible after the independent tab is removed. */
export function RefactorHistory({ repo, ...props }: Omit<Parameters<typeof RefactorStatus>[0], 'id'>) {
  const [ids, setIds] = useState<string[]>([])
  const [fault, setFault] = useState('')
  useEffect(() => {
    let stale = false
    api.get('/api/refactors', repo).then((r) => api.json<{ runs: Run[] }>(r, '지난 리펙터링')).then((r) => {
      if (!stale) setIds(r.runs.filter((run) => !run.spec).map((run) => run.id))
    }).catch((err) => { if (!stale) setFault(String(err)) })
    return () => { stale = true }
  }, [repo])
  if (fault) return <p role="alert" className="text-destructive">{fault}</p>
  if (!ids.length) return null
  return <details className="mt-5"><summary className="cursor-pointer text-[12.5px] text-muted-foreground">이전 화면에서 만든 실행</summary>
    {ids.map((id) => <RefactorStatus key={id} id={id} repo={repo} {...props} />)}
  </details>
}
