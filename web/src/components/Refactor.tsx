import { useEffect, useState } from 'react'
import { Btn } from '@/components/Modal'
import { get, json, post } from '@/lib/api'
import { cn } from '@/lib/utils'

/** The `[리펙터링]` workflow (`docs/plans/refactor/3-cleanup.md`): scan, start
 *  a run, and follow its test PR and stacked step PRs. */

type Limits = { seconds: number; calls: number; tokens: number }
type Hotspot = { path: string; lines: number; dup: number; block: number; churn: number; cap: number; debt: number }
type Scan = { repo: string; rows: Hotspot[]; modes: Record<string, Limits>; scope: string }
type Step = { n: number; tier: string; files: string[]; goal: string; state: string; spec: string | null; pr?: number }
type Run = {
  id: string; mode: string; state: 'running' | 'stopped' | 'done'; phase: string; created: number
  limits: Limits; spent: Limits; steps: Step[]; tests: { spec: string; pr?: number } | null
  stopped: { reason: string; detail: string } | null; plan?: string
}

const MODE: Record<string, string> = { cleanup: '빠른 정리 · L0–L1', restructure: '모듈 재구성 · L0–L2', full: '전면 · L0–L3' }
const STATE = { running: '실행 중', stopped: '멈춤', done: '끝남' }
const STEP: Record<string, string> = { pending: '대기', adopted: '후보 채택', published: 'PR 리뷰 중',
  awaiting: '승인 대기', done: '리뷰 통과' }
const LIMIT: [keyof Limits, string][] = [['seconds', '초'], ['calls', '호출'], ['tokens', '토큰']]

const request = () => `refactor-${crypto.randomUUID()}`

export function Refactor({ repo }: { repo: string }) {
  const [scan, setScan] = useState<Scan | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [fault, setFault] = useState('')
  const [mode, setMode] = useState('cleanup')
  const [top, setTop] = useState(3)
  const [files, setFiles] = useState('')   // the module a restructure audits, one path per line
  const [limits, setLimits] = useState<Limits | null>(null)
  const [rid, setRid] = useState(request)   // one key per start, so a double click is one run
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    get('/api/refactors/scan', repo).then((r) => json<Scan>(r, '부채 스캔')).then((s) => {
      setScan(s)
      setLimits(s.modes.cleanup ?? null)
    }).catch((err) => setFault(String(err instanceof Error ? err.message : err)))
  }, [repo])
  useEffect(() => {
    let stopped = false
    let timer: number | undefined
    const read = async () => {
      try {
        const next = await get('/api/refactors', repo).then((r) => json<{ repo: string; runs: Run[] }>(r, '리펙터링 목록'))
        if (!stopped && next.repo === repo) setRuns(next.runs)
      } catch (err) {
        if (!stopped) setFault(String(err instanceof Error ? err.message : err))
      } finally {
        if (!stopped) timer = window.setTimeout(read, 3000)
      }
    }
    void read()
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [repo])

  const act = async (url: string, body?: unknown) => {
    setBusy(true)
    try {
      const run = await post(url, body, 'POST', repo).then((r) => json<Run>(r, '리펙터링'))
      setRuns((old) => [run, ...old.filter((o) => o.id !== run.id)])
      setFault('')
      return true
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
      return false
    } finally {
      setBusy(false)
    }
  }
  const start = async () => {
    const chosen = files.split('\n').map((f) => f.trim()).filter(Boolean)
    if (limits && await act('/api/refactors', { request_id: rid, mode, top, limits, files: mode === 'restructure' ? chosen : [] })) {
      setRid(request())
    }
  }

  return <section aria-label="리펙터링" className="flex h-full min-h-0 flex-col overflow-y-auto">
    <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-5 py-3">
      <h2 className="font-heading text-[14px] font-semibold">리펙터링</h2>
      <span className="text-[12.5px] text-muted-foreground">동작을 테스트로 먼저 묶고, 부채가 가장 많이 준 후보만 단계 PR 로 올린다</span>
    </header>
    {fault && <p role="alert" className="whitespace-pre-wrap px-5 py-3 text-[12.5px] text-destructive">{fault}</p>}

    <div className="space-y-5 px-5 py-4">
      <div className="space-y-2">
        <h3 className="font-heading text-[13px] font-semibold">새 실행</h3>
        <div className="flex flex-wrap items-end gap-3 text-[12.5px]">
          <label className="flex flex-col gap-1">모드
            <select value={mode} onChange={(e) => { setMode(e.target.value); setLimits(scan?.modes[e.target.value] ?? null) }}
              className="min-h-11 rounded-md border border-border bg-card px-2">
              {Object.keys(scan?.modes ?? { cleanup: null }).filter((m) => !(scan?.scope === 'hub' && m === 'full'))
                .map((m) => <option key={m} value={m}>{MODE[m] ?? m}</option>)}
            </select>
          </label>
          {mode === 'cleanup' && <label className="flex flex-col gap-1">대상 파일 수
            <input type="number" min={1} max={10} value={top} onChange={(e) => setTop(Number(e.target.value))}
              className="min-h-11 w-20 rounded-md border border-border bg-card px-2" />
          </label>}
          {mode === 'restructure' && <label className="flex min-w-0 flex-1 flex-col gap-1">모듈 파일 (한 줄에 하나)
            <textarea rows={3} value={files} onChange={(e) => setFiles(e.target.value)} placeholder="src/orders/service.py"
              className="min-h-11 rounded-md border border-border bg-card px-2 py-1 font-mono text-[11.5px]" />
          </label>}
          {limits && LIMIT.map(([key, label]) => <label key={key} className="flex flex-col gap-1">{label} 한도
            <input type="number" min={1} value={limits[key]} onChange={(e) => setLimits({ ...limits, [key]: Number(e.target.value) })}
              className="min-h-11 w-28 rounded-md border border-border bg-card px-2" />
          </label>)}
          <Btn tone="primary" className="min-h-11" onClick={() => void start()}
            disabled={busy || !limits || (mode === 'cleanup' ? !scan?.rows.length : mode === 'restructure' && !files.trim())}>
            {busy ? '여는 중…' : '시작'}
          </Btn>
        </div>
        <p className="text-[12.5px] text-muted-foreground">L0–L1 단계 PR 은 이 요청으로 리뷰까지 자동으로 넘어간다. 실패한 단계는 한 번 다시 시도하고, 그래도 안 되면 더 작게 나누라고 멈춘다.</p>
        {scan?.scope === 'hub' && <p className="text-[12.5px] text-wait">wiki-agent 자신이다. 단계마다 따로 만든 worktree 에서 돌고, 지금 도는 서버의 체크아웃은 건드리지 않는다. 머지한 뒤 앱을 다시 시작해야 반영된다.</p>}
        {mode === 'full' && <p className="text-[12.5px] text-muted-foreground">저장소 전체를 감사한 뒤 계획 PR 을 연다. 계획이 리뷰를 거쳐 머지되어야 단계가 시작된다.</p>}
        {mode !== 'cleanup' && <p className="text-[12.5px] text-muted-foreground">L2 이상 단계는 열린 작업이 없을 때만 시작하고, 도는 동안 이 저장소의 새 작업을 막는다. 리뷰는 직접 시작하고, 끝나면 [승인] 해야 다음 단계로 간다.</p>}
      </div>

      <div className="space-y-2">
        <h3 className="font-heading text-[13px] font-semibold">진행 중·지난 실행</h3>
        {!runs.length && <p className="text-[13px] text-muted-foreground">아직 실행이 없다.</p>}
        <ul className="space-y-2">{runs.map((run) => <li key={run.id} className="rounded-md border border-border bg-card px-3 py-3 text-[12.5px]">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-heading font-semibold">{MODE[run.mode] ?? run.mode}</span>
            <span className={cn(run.state === 'stopped' ? 'text-destructive' : 'text-muted-foreground')}>{STATE[run.state]} · {run.phase}</span>
            <span className="font-mono text-[10.5px] text-muted-foreground">{new Date(run.created * 1000).toLocaleString('ko-KR')}</span>
            <span className="ml-auto flex gap-2">
              {(run.state === 'running' || (run.state === 'stopped' && run.steps.some((s) => ['L2', 'L3'].includes(s.tier) && s.spec && s.state !== 'done'))) && (
                <Btn tone="danger" disabled={busy} onClick={() => void act(`/api/refactors/${run.id}/cancel`)}>{run.state === 'running' ? '멈추기' : '저장소 놓기'}</Btn>)}
              {run.steps.some((s) => s.state === 'awaiting') && (
                <Btn tone="primary" disabled={busy} onClick={() => void act(`/api/refactors/${run.id}/approve`)}>승인</Btn>)}
              {run.state === 'stopped' && <Btn disabled={busy} onClick={() => void act(`/api/refactors/${run.id}/resume`)}>재개</Btn>}
            </span>
          </div>
          <p className="mt-1 text-muted-foreground">
            {LIMIT.map(([key, label]) => `${label} ${Math.round(run.spent[key])}/${run.limits[key]}`).join(' · ')}
          </p>
          {run.plan && <p className="mt-1">계획 <code className="font-mono text-[11.5px]">{run.plan}</code>
            {run.phase === 'plan' ? ' — 계획 PR 이 머지되면 단계가 시작된다' : ''}</p>}
          {run.stopped && <p className="mt-1 whitespace-pre-wrap break-words text-destructive">{run.stopped.reason} — {run.stopped.detail}</p>}
          <ol className="mt-2 space-y-1">
            {run.tests && <li>0. 특성 테스트 · {run.tests.pr ? `PR #${run.tests.pr}` : '작성 중'}</li>}
            {run.steps.map((s) => <li key={s.n} className="break-words">
              {s.n}. [{s.tier}] {s.files.join(', ')} · {STEP[s.state] ?? s.state}{s.pr ? ` · PR #${s.pr}` : ''}
            </li>)}
          </ol>
        </li>)}</ul>
      </div>

      <div className="space-y-2">
        <h3 className="font-heading text-[13px] font-semibold">부채 상위 파일</h3>
        {!scan && !fault && <p role="status" className="text-[13px] text-muted-foreground">저장소를 재는 중…</p>}
        {scan && !scan.rows.length && <p className="text-[13px] text-muted-foreground">기준을 넘는 파일이 없다.</p>}
        {!!scan?.rows.length && <div className="overflow-x-auto">
          <table className="w-full text-left text-[12.5px]">
            <thead className="text-muted-foreground"><tr>
              <th className="py-1 pr-3 font-normal">파일</th><th className="pr-3 font-normal">줄/기준</th>
              <th className="pr-3 font-normal">중복</th><th className="pr-3 font-normal">최장 블록</th>
              <th className="pr-3 font-normal">최근 변경</th><th className="font-normal">부채</th>
            </tr></thead>
            <tbody>{scan.rows.map((r) => <tr key={r.path} className="border-t border-border">
              <td className="break-all py-1 pr-3 font-mono text-[11.5px]">{r.path}</td>
              <td className="pr-3">{r.lines}/{r.cap}</td><td className="pr-3">{r.dup}</td>
              <td className="pr-3">{r.block}</td><td className="pr-3">{r.churn}</td><td>{r.debt}</td>
            </tr>)}</tbody>
          </table>
        </div>}
      </div>
    </div>
  </section>
}
