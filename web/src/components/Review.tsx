import { useCallback, useEffect, useRef, useState } from 'react'
import { Btn } from '@/components/Modal'
import { Reply } from '@/components/Agent'
import { Toolbar, type Choice } from '@/components/Toolbar'
import { useReview } from '@/lib/work'
import { useParagraphOverlay } from '@/lib/overlay'
import * as api from '@/lib/api'
import { PROFILE_LABEL, type LocalVerification, type Spec, type VerificationConfig, type LoopSettings, type Options } from '@/lib/api'
import { LOOPING } from '@/lib/tasks'
import { cn } from '@/lib/utils'
import { DiffLines } from '@/components/LiveChanges'
import { useLoaded } from '@/lib/loaded'
import { GateOr } from '@/components/TaskRail'

/** The review tab: the loop of the selected task's spec — its rounds, why it
 *  stopped, and the buttons that act on it. Which spec and PR it is, the
 *  pane's header says. */
type Props = { spec: Spec | null; onChanged: () => void; on: boolean; progressOn?: boolean; options: Options | null;
  settings: LoopSettings | null; onSettings: (s: LoopSettings) => Promise<void>; onPeek: (path: string, line: number) => void
  /** The work model the agent pane has chosen: [계속] fills a spec that names none. */
  choice?: Choice }

export function Review(props: Props) {
  const { spec, options, settings, onSettings } = props
  const [saving, setSaving] = useState(false)
  const [fault, setFault] = useState('')
  const defaultModel = options?.models.find((m) => m.id.startsWith('codex:') && m.is_default)
  const reviewOptions = options ? { ...options, models: [
    { id: '', label: 'Codex 기본', note: '', efforts: defaultModel?.efforts ?? [] },
    ...options.models.filter((m) => m.id),
  ] } : null
  const named = spec?.reviewer
  const model = named?.model ?? settings?.review_model ?? ''
  const effort = named?.effort ?? settings?.review_effort ?? ''
  const missing = model.startsWith('codex:') && options && !options.codex_error && !options.models.some((m) => m.id === model)
  return <section aria-label="리뷰 세션" className="flex h-full min-h-0 flex-col">
    <header className="flex min-h-11 flex-wrap items-center justify-end gap-1.5 border-b border-border px-5 py-1">
      <span className="mr-auto font-heading text-[11px] font-semibold text-faint">리뷰 모델{named && (spec?.refactor?.roles ? ' · 리팩터링에서 지정' : ' · 계획에서 지정')}</span>
      <Toolbar options={reviewOptions} value={{ model, effort }} busy={saving || !settings || !!named}
        onChange={async (choice) => {
          if (!settings) return
          setSaving(true)
          setFault('')
          try { await onSettings({ ...settings, review_model: choice.model, review_effort: choice.effort }) }
          catch (err) { setFault(String(err instanceof Error ? err.message : err)) }
          finally { setSaving(false) }
        }} />
      {missing && <p role="alert" className="w-full text-[12.5px] text-destructive">저장된 모델을 사용할 수 없다. 사용 가능한 리뷰 모델을 다시 골라라.</p>}
      {fault && <p role="alert" className="w-full text-[12.5px] text-destructive">{fault}</p>}
    </header>
    <div className="min-h-0 flex-1"><ReviewBody {...props} /></div>
  </section>
}

function ReviewBody({ spec, onChanged, on, progressOn = on, onPeek, choice }: Props) {
  const [working, setWorking] = useState('')
  const [fault, setFault] = useState('')
  const [note, setNote] = useState('')
  const [limits, setLimits] = useState({ seconds: '', calls: '', tokens: '' })
  const [file, setFile] = useState<{ title: string; text: string } | null>(null)
  const generation = useRef(0)
  const turns = useReview(spec?.repo ?? '', spec?.id ?? '')
  const shownFile = useParagraphOverlay(file?.text ?? '', on)
  useEffect(() => () => { generation.current++ }, [])

  if (!spec) return <p className="p-5 text-[13.5px] text-faint">명세가 없는 작업이다. 리뷰 루프는 명세의 PR 에서 돈다.</p>
  if (!spec.pr) return <p className="p-5 text-[13.5px] text-faint">아직 PR 이 없다. 작업이 끝나 PR 이 서면 여기서 라운드가 돈다.</p>

  async function act(what: string, fn: () => Promise<unknown>) {
    const ticket = generation.current
    setFault('')
    setWorking(what)
    try {
      await fn()
      if (generation.current === ticket) setNote('')
    } catch (err) {
      if (generation.current === ticket) setFault(String(err instanceof Error ? err.message : err))
    } finally {
      if (generation.current === ticket) {
        setWorking('')
        onChanged()
      }
    }
  }

  async function open(n: number, what: 'order' | 'result') {
    const ticket = generation.current
    try {
      const { path, text } = await api.roundFile(spec!.id, n, what, spec!.repo)
      if (generation.current === ticket) setFile({ title: path, text })
    } catch (err) {
      if (generation.current === ticket) setFault(String(err instanceof Error ? err.message : err))
    }
  }

  const reason = spec.stopped?.reason ?? ''
  const recovering = reason === RECOVERY ? Object.values(spec.recovery ?? {}).find((r) => STOPPED.includes(r.phase)) : undefined
  const needsLimits = !!recovering && ['limits', 'budget', 'deadline'].includes(recovering.phase)
  const needsRuling = recovering?.phase === 'exhausted'
  const cloud = spec.implementation_environment === 'claude-cloud'
  const analysis = spec.local_verification?.needs_research || ['reanalysis', 'unstable'].includes(spec.local_verification?.state ?? '')
  const rounds = spec.rounds ?? []
  const checked = spec.validation?.round
  const final = spec.validation?.final
  const running = spec.validation?.phase === 'final_running'
  const proven = !running && spec.unproven === ''
  const merging = !!spec.merge_progress && ['running', 'waiting_review', 'queued'].includes(spec.merge_progress.state)
  return (
    <div className="h-full overflow-y-auto px-5 py-4 text-[12.5px]">
      <div className="flex items-center gap-2">
        <a href={spec.pr.url} target="_blank" rel="noreferrer" className="font-mono text-[10.5px] text-primary hover:underline">
          GitHub 에서 PR #{spec.pr.number} 열기
        </a>
        {(spec.state === '머지 가능' || /^PR #\d+$/.test(spec.state)) && spec.plan_commit !== 'asked' && (
          <Btn className="ml-auto" disabled={!!working || merging}
            onClick={() => act('review', () => api.reviewSpec(spec.id, spec.repo))}>
            {working === 'review' ? '…' : rounds.length ? '다음 리뷰 라운드' : '리뷰 시작'}
          </Btn>
        )}
        {LOOPING.test(spec.state) && (
          <Btn tone="danger" className="ml-auto" disabled={!!working} onClick={() => act('halt', () => api.haltSpec(spec.id, spec.repo))}
            title="이 루프를 멈춘다. [계속] 으로 잇는다">
            멈춤
          </Btn>
        )}
      </div>

      {(cloud || spec.local_verification) && <VerificationPanel spec={spec} />}

      {Object.keys(spec.recovery ?? {}).length > 0 && <RecoveryPanel recovery={spec.recovery!} />}

      {(spec.merge_progress || working === 'merge') && <MergeProgress spec={spec} pending={working === 'merge'} />}

      {spec.state === '멈춤' && spec.stopped && (
        <div className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 p-3">
          <div className="text-destructive">멈춤 — {spec.stopped.reason}</div>
          {spec.stopped.detail && <Detail text={spec.stopped.detail} on={on} />}
          {reason === '검토하지 않은 base 에 머지됨' ? (
            <div className="mt-2 space-y-1.5">
              <div className="flex gap-1.5">
                <Btn disabled={!!working} onClick={() => act('accept', () => api.settleSpec(spec.id, 'accept', spec.repo))}>받아들임</Btn>
                <Btn disabled={!!working} onClick={() => act('reopen', () => api.settleSpec(spec.id, 'reopen', spec.repo))}>다시 PR</Btn>
              </div>
              <p className="text-faint">
                `{spec.merge?.base}` 의 머지는 그대로다. 되돌리려면 GitHub 에서. [다시 PR] 은 원래 base 로 새 PR 을 올린다.
              </p>
            </div>
          ) : (
            <div className="mt-2 space-y-1.5">
              {spec.refactor?.mode === 'preserve' && spec.refactor_frozen && <Refreeze spec={spec} onDone={onChanged} />}
              {needsLimits && <LimitsInput value={limits} onChange={setLimits} spent={recovering!.spent} />}
              {(reason === '반론' || analysis || needsRuling) && (
                <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2}
                  aria-label={analysis ? '재분석 근거' : needsRuling ? '반복 발견에 정한 것' : '발견에 대한 판단'}
                  placeholder={analysis ? '원인 · 근거 · 다음 실험 — 재분석 후 재개한다'
                    : needsRuling ? '목표·제약 변경, 페이지의 다른 방안, 또는 이 발견을 미룸 — 다음 라운드 지시에 들어간다'
                    : '그 발견에 정한 것 — 다음 라운드 지시에 들어간다'}
                  className="w-full rounded-md border border-border bg-background px-2 py-1 text-[13.5px]" />
              )}
              <Btn tone="primary" disabled={!!working || ((reason === '반론' || analysis || needsRuling) && !note.trim())
                  || (needsLimits && !limitsOk(limits))}
                onClick={() => act('resume', () => api.resumeSpec(spec.id, note.trim(), spec.repo,
                  needsLimits ? parsedLimits(limits) : undefined, choice))}>
                {working === 'resume' ? '…' : reason === '라운드 상한' ? '계속 (+4 라운드)' : cloud ? '로컬 검증 재개' : reason === '외부 수정 대기' ? '수정된 PR 로 다음 라운드' : '계속'}
              </Btn>
            </div>
          )}
        </div>
      )}

      {turns.length > 0 && <section aria-label="리뷰 진행상황" className="mt-4 space-y-4 border-t border-border pt-3">
        <div className="font-heading text-[11px] font-semibold text-faint">독립 리뷰 진행상황</div>
        {turns.map((turn) => <Reply key={turn.key} turn={turn} on={on} progressOn={progressOn} onPeek={onPeek} onAnswer={() => {}} />)}
      </section>}

      {/^PR #\d+$/.test(spec.state) && spec.fault && (
        <div className="mt-3 rounded-md border border-border p-3">
          <div className="text-muted-foreground">PR 은 올라갔는데 리뷰에 들어가지 못했다 — {spec.fault}</div>
          {spec.plan_commit === 'asked' ? (
            <div className="mt-2">계획 행 커밋이 아직이다 — 에이전트 탭에서 행을 고쳐 커밋하게 하면 리뷰로 간다</div>
          ) : null}
        </div>
      )}

      {(checked || final || running) && (
        <div className="mt-3 space-y-0.5 text-muted-foreground">
          {checked && (
            <div>
              라운드 확인 · {checked.selection === 'mapped' ? '변경이 닿는 것만' : checked.selection === 'deferred' ? '패치 확인 · 넓은 검사는 최종 담당 대기' : '전체'} ·{' '}
              <span className={checked.ok ? 'text-primary' : 'text-destructive'}>{checked.ok ? '통과' : '실패'}</span>
              <span className="ml-1 font-mono text-[10.5px] text-faint">{checked.head.slice(0, 7)}</span>
            </div>
          )}
          <div>
            최종 검증 · {final?.verification?.backend === 'ci' ? 'GitHub CI · ' : final?.verification?.backend === 'local' ? '로컬 · ' : ''}
            {running ? <GateOr gate={spec.gating} className="text-primary">도는 중</GateOr>
              : !final ? '대기'
                : !final.ok ? <span className={final.pending ? 'text-muted-foreground' : 'text-destructive'}>{final.pending ? '준비 대기' : '실패'} — {final.reason}</span>
                  : spec.unproven ? `지난 통과는 이제 안 선다 — ${spec.unproven}`
                    : <span className="text-primary">통과</span>}
            {final && !running && <span className="ml-1 font-mono text-[10.5px] text-faint">{final.head.slice(0, 7)}</span>}
            {final?.verification?.url && <a className="ml-2 underline" href={final.verification.url} target="_blank" rel="noreferrer">CI 결과</a>}
          </div>
        </div>
      )}

      {spec.state === '머지 가능' && spec.approved && (
        <div className="mt-3 rounded-md border border-st-ready/50 p-3">
          <div className="flex items-center gap-2">
            {proven ? (
              <Btn tone="primary" disabled={!!working || merging} onClick={() => act('merge', () => api.mergeSpec(spec.id, spec.approved!, spec.repo))}>
                {working === 'merge' || merging ? '머지 진행 중…' : '머지 ▸'}
              </Btn>
            ) : (
              // The server refuses the merge and sends the spec back to run only the final gate.
              <Btn disabled={!!working || running}
                onClick={() => act('final', () => cloud
                  ? api.startLoops([spec.pr!.number], 'claude-cloud', spec.repo).then(({ results }) => {
                    if (results[0]?.error) throw new Error(results[0].error)
                  }) : api.mergeSpec(spec.id, spec.approved!, spec.repo).catch(() => undefined))}>
                {working === 'final' ? '…' : cloud ? '로컬 검증 확인' : '최종 검증 확인'}
              </Btn>
            )}
            <span className="font-mono text-[10.5px] text-faint">squash · {spec.approved.slice(0, 7)} 에 묶인다</span>
          </div>
          {spec.p2_comment && (
            <>
              <div className="mt-2 text-faint">머지할 때 PR 에 달 코멘트</div>
              <pre className="mt-1 whitespace-pre-wrap rounded bg-secondary p-2 text-[12px]">{spec.p2_comment}</pre>
            </>
          )}
        </div>
      )}

      {spec.state === '머지 대기' && (
        <p className="mt-3 text-muted-foreground">GitHub 머지 대기 · {spec.queued || '대기열'}</p>
      )}

      {spec.cleanup && spec.cleanup.length > 0 && (
        <ul className="mt-3 list-disc pl-5 text-muted-foreground">
          {spec.cleanup.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      )}

      <table className="mt-4 w-full border-collapse text-left">
        <thead className="font-heading text-[11px] font-semibold text-faint">
          <tr><th className="py-1">라운드</th><th>판정</th><th>P0·P1·P2</th><th>기준 · 리뷰어</th><th>게이트</th><th /></tr>
        </thead>
        <tbody>
          {rounds.length === 0 && <tr><td colSpan={6} className="py-1 text-faint">아직 라운드가 없다</td></tr>}
          {rounds.map((r, i) => (
            <tr key={i} className={cn('border-t border-border', r.stale && 'text-faint line-through')}>
              <td className="py-1 font-mono">R{r.n}{r.stale && ' (버림)'}</td>
              <td className={r.verdict === 'allow' ? 'text-primary' : 'text-destructive'}>
                {r.verdict === 'allow' ? '머지 허용' : '머지 불가'}
              </td>
              <td className="font-mono">
                {r.findings.P0}·{r.findings.P1}·{r.findings.P2}
                {r.identity !== 'full' && r.findings.P0 + r.findings.P1 + r.findings.P2 > 0 && (
                  <span className="ml-1 font-sans text-faint" title="finding-meta 가 없어 발견에 id 가 없다 — 반복으로 세지 않는다">id 없음</span>
                )}
              </td>
              <td title={r.reviewer?.session_id ? `리뷰 세션 ${r.reviewer.session_id}` : undefined}>
                {r.profile ? PROFILE_LABEL[r.profile] : '—'}
                {r.reviewer?.model && (
                  <span className="ml-1 font-mono text-[10.5px] text-faint">
                    {r.reviewer.model.replace(/^codex:/, '')}{r.reviewer.effort ? ` · ${r.reviewer.effort}` : ''}
                  </span>
                )}
              </td>
              <td>{r.gate?.ok == null ? '—' : r.gate.ok ? '통과' : '실패'}</td>
              <td className="text-right">
                {!r.stale && (
                  <>
                    <button type="button" className="text-primary hover:underline" onClick={() => open(r.n, 'order')}>지시</button>
                    <span className="mx-1 text-faint">·</span>
                    <button type="button" className="text-primary hover:underline" onClick={() => open(r.n, 'result')}>결과</button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {fault && <p role="alert" className="mt-2 text-destructive">{fault}</p>}
      {file && (
        <div className="mt-3">
          <div className="flex items-center gap-2 font-mono text-[10.5px] text-faint">
            <span className="min-w-0 break-all">{file.title}</span>
            <button type="button" className="ml-auto" onClick={() => setFile(null)}>닫기</button>
          </div>
          <pre className="mt-1 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-secondary p-2 text-[12px]">{shownFile}</pre>
        </div>
      )}
    </div>
  )
}

function MergeProgress({ spec, pending }: { spec: Spec; pending: boolean }) {
  const record = spec.merge_progress
  const active = record ? ['running', 'waiting_review', 'queued'].includes(record.state) : pending
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])
  const elapsed = record ? Math.max(0, Math.floor(now / 1000 - record.started_at)) : 0
  return <section aria-label="머지 진행상황" aria-busy={active} className="mt-3 rounded-md border border-border p-3">
    <div role="status" className="flex items-center gap-2">
      {active && <span aria-hidden="true" className="size-3 shrink-0 animate-spin rounded-full border-2 border-primary border-t-transparent motion-reduce:animate-none" />}
      <span>{record?.stage ?? '머지 요청 보내는 중'}</span>
      {active && <span aria-hidden="true" className="ml-auto shrink-0 text-faint">{elapsed}초</span>}
    </div>
    {record?.state === 'waiting_review' && <p className="mt-1 text-faint">
      {spec.state} · {spec.validation?.phase === 'final_running' ? '최종 게이트 실행 중' : '독립 리뷰와 최종 게이트가 끝나면 요청한 커밋을 머지한다'}
    </p>}
    {!!record?.steps.length && <details className="mt-2 text-muted-foreground">
      <summary className="cursor-pointer">머지 과정 · {record.steps.length}단계</summary>
      <ol className="mt-1 list-decimal space-y-1 pl-5">{record.steps.map((step, index) => <li key={index}>{step.text}</li>)}</ol>
    </details>}
  </section>
}

/** A frozen characterization test that had to change: the person reads its diff and approves it; the server
 *  runs the changed tests on the code before the refactoring and freezes them at the current commit. */
type FrozenChange = { head: string; from: string; files: string[]; diff: string; digest: string }

function Refreeze({ spec, onDone }: { spec: Spec; onDone: () => void }) {
  const url = `/api/refactors/specs/${spec.id}`
  // Read again whenever the stop changes: a fix the worker made is what the next read shows.
  // A refused re-freeze reads again at once: the read puts back what the interrupted one left.
  const [change, fault, setFault, reload] = useLoaded<FrozenChange>(`${url}/frozen`, spec.repo, '고정 테스트 변경', spec.stopped?.detail)
  const [working, setWorking] = useState(false)
  if (!change) return fault ? <p role="alert" className="whitespace-pre-wrap text-[12.5px] text-destructive">{fault}</p> : null
  if (!change.files.length) return <p className="text-faint">고정한 뒤 바뀐 고정 테스트가 없다.</p>
  return <div className="space-y-1.5">
    <p className="text-muted-foreground">바뀐 고정 테스트 {change.files.length}개 · <span className="font-mono">{change.from.slice(0, 7)}</span>
      {' → '}<span className="font-mono">{change.head.slice(0, 7)}</span></p>
    <DiffLines lines={change.diff.split('\n')} label="고정 테스트 변경" className="max-h-80 rounded-md border border-border bg-secondary p-2" />
    <Btn tone="primary" disabled={working} title="바꾸기 전 코드에서 돌려 통과하면 이 커밋에 다시 고정한다" onClick={async () => {
      setFault('')
      setWorking(true)
      try {
        await api.post(`${url}/refreeze`, { head: change.head, digest: change.digest }, 'POST', spec.repo).then((r) => api.json<Spec>(r, '다시 고정'))
        onDone()
      } catch (err) { setFault(String(err instanceof Error ? err.message : err)); reload() } finally { setWorking(false) }
    }}>{working ? '바꾸기 전 코드에서 도는 중…' : '고정 테스트 변경 승인'}</Btn>
    {fault && <p role="alert" className="whitespace-pre-wrap text-[12.5px] text-destructive">{fault}</p>}
  </div>
}

function Detail({ text, on }: { text: string; on: boolean }) {
  const shown = useParagraphOverlay(text, on)
  return <div className="mt-0.5 whitespace-pre-wrap break-words text-muted-foreground">{shown}</div>
}

const VERIFICATION_STATE: Record<LocalVerification['state'], string> = {
  waiting_environment: '환경 준비 대기', waiting_review: '로컬 리뷰 대기', running: '주요 흐름 검증 중',
  runtime_passed: '실행 검증 통과 · 리뷰 대기', verified: '로컬 검증·리뷰 통과', waiting_cloud: '클라우드 수정 대기',
  reanalysis: '두 수정 주기 실패 · 재분석 필요', unstable: '간헐적 실패 · 원인 확인 필요', interrupted: '중단 · 재개 대기',
  failed: '실행 검증 실패 · 구현 수정 필요',
}

/** Repository-specific setup and receipts stay in the existing review tab.
 * Requests retain the repository captured on mount; retired results are dropped. */
function VerificationPanel({ spec }: { spec: Spec }) {
  const cloud = spec.implementation_environment === 'claude-cloud'
  const [config, setConfig] = useState<VerificationConfig | null>(null)
  const [editing, setEditing] = useState(false)
  const [values, setValues] = useState<Record<string, string>>({})
  const [fault, setFault] = useState('')
  const [working, setWorking] = useState(false)
  const [handoff, setHandoff] = useState('')
  const generation = useRef(0)
  const [owner] = useState(() => ({ repo: spec.repo, id: spec.id }))
  const retire = useCallback(() => { generation.current++ }, [])

  useEffect(() => {
    const ticket = ++generation.current
    api.getVerificationConfig(owner.repo, owner.id).then((found) => {
      if (generation.current === ticket) setConfig(found)
    }).catch((err) => {
      if (generation.current === ticket) setFault(String(err instanceof Error ? err.message : err))
    })
    return retire
  }, [owner, retire])

  async function configure() {
    const ticket = ++generation.current
    setFault('')
    setWorking(true)
    try {
      const found = await api.getVerificationConfig(owner.repo, owner.id)
      if (generation.current !== ticket) return
      setConfig(found)
      const saved = found.settings
      const revisions = saved.revisions as Record<string, string> | undefined
      const fields: Record<string, string> = {}
      for (const key of ['environment_id', 'test_scope', 'env_file', 'browser_tool', 'setup', 'cleanup']) {
        fields[key] = String(saved[key] ?? '')
      }
      fields.allowed_origins = (saved.allowed_origins as string[] | undefined)?.join('\n') ?? ''
      if (found.manifest?.flows.some((f) => f.kind === 'desktop')) {
        const native = saved.native as Record<string, unknown> | undefined
        for (const key of ['application', 'executable', 'executable_sha256', 'profile']) fields[`native:${key}`] = String(native?.[key] ?? '')
      }
      for (const key of new Set(found.manifest?.flows.flatMap((f) => f.environments) ?? [])) fields[`revision:${key}`] = revisions?.[key] ?? ''
      setValues(fields)
      setEditing(true)
    } catch (err) {
      if (generation.current === ticket) setFault(String(err instanceof Error ? err.message : err))
    } finally {
      if (generation.current === ticket) setWorking(false)
    }
  }

  async function save() {
    const ticket = ++generation.current
    setWorking(true)
    setFault('')
    try {
      const revisions = Object.fromEntries(Object.entries(values).filter(([k]) => k.startsWith('revision:'))
        .map(([k, v]) => [k.slice(9), v]))
      const native = config?.manifest?.flows.some((f) => f.kind === 'desktop') ? {
        ...Object.fromEntries(Object.entries(values).filter(([k]) => k.startsWith('native:')).map(([k, v]) => [k.slice(7), v])),
        version: 1, host: 'windows-win32', ownership: 'launch-disposable', desktop: 'attempt-owned',
      } : null
      const body = { ...config?.settings, ...Object.fromEntries(Object.entries(values).filter(([k]) => !k.startsWith('revision:') && !k.startsWith('native:'))),
        revisions, allowed_origins: values.allowed_origins.split('\n').map((v) => v.trim()).filter(Boolean),
        ...(native ? { version: 2, native } : {}),
        manifest_digest: config!.manifest_digest }
      const found = await api.saveVerificationConfig(owner.repo, owner.id, body)
      if (generation.current === ticket) { setConfig(found); setEditing(false) }
    } catch (err) {
      if (generation.current === ticket) setFault(String(err instanceof Error ? err.message : err))
    } finally {
      if (generation.current === ticket) setWorking(false)
    }
  }

  async function protect() {
    const ticket = ++generation.current
    setWorking(true)
    setFault('')
    try {
      await api.configureVerificationProtection(owner.repo, spec.pr?.base ?? 'main')
    } catch (err) {
      if (generation.current === ticket) setFault(String(err instanceof Error ? err.message : err))
    } finally {
      if (generation.current === ticket) setWorking(false)
    }
  }

  const record = spec.local_verification
  const labels: Record<string, string> = { environment_id: '테스트 환경 이름', test_scope: '테스트 계정·데이터 범위',
    env_file: '로컬 .env 파일의 절대 경로', browser_tool: '브라우저 검증 도구', setup: '환경 준비 명령 (선택)',
    cleanup: '테스트 데이터·서버 정리 명령 (선택)' }
  const nativeLabels: Record<string, string> = { application: '등록된 네이티브 앱 이름', executable: '승인할 실행 파일의 절대 경로',
    executable_sha256: '실행 파일 SHA-256', profile: '격리된 테스트 프로필 이름' }
  return <section aria-label={cloud ? '클라우드 구현의 로컬 검증' : '등록 흐름의 로컬 검증'} className="mt-3 rounded-md border border-border p-3">
    <div className="font-heading text-[11px] font-semibold">{cloud ? 'Claude Code Cloud → 로컬 검증' : '등록 흐름 → 로컬 검증'}</div>
    <p className="mt-1">{record ? VERIFICATION_STATE[record.state] : '로컬 검증 대기'}</p>
    {record?.reason && <p className="mt-1 whitespace-pre-wrap text-muted-foreground">{record.reason}</p>}
    <div className="mt-2 flex flex-wrap gap-2">
      {cloud && <Btn disabled={working} onClick={async () => {
        try { setHandoff((await api.cloudInstructions(spec.id, spec.repo)).text) }
        catch (err) { setFault(String(err instanceof Error ? err.message : err)) }
      }}>클라우드 인계 지시 보기</Btn>}
      <Btn disabled={working || LOOPING.test(spec.state)} onClick={() => void configure()}>프로젝트 검증 설정</Btn>
      {cloud && <Btn disabled={working || LOOPING.test(spec.state)} onClick={() => void protect()}>GitHub 필수 검사 설정</Btn>}
    </div>
    {handoff && <label className="mt-2 block">클라우드 구현자에게 전달할 지시
      <textarea readOnly value={handoff} rows={7} onFocus={(e) => e.target.select()}
        className="mt-1 w-full rounded-md border border-border bg-background p-2 font-mono text-[12px]" />
    </label>}
    {config?.problem && <p className="mt-2 text-muted-foreground">검증 준비 필요 — {config.problem}</p>}
    {editing && config?.manifest && <form className="mt-3 space-y-2" onSubmit={(e) => { e.preventDefault(); void save() }}>
      {Object.entries(labels).map(([key, label]) => <label key={key} className="block">
        <span className="block text-faint">{label}</span>
        <input required={!['setup', 'cleanup'].includes(key)} disabled={working} value={values[key] ?? ''}
          onChange={(e) => setValues((was) => ({ ...was, [key]: e.target.value }))}
          className="h-7 w-full min-w-0 rounded-md border border-border bg-background px-2" />
      </label>)}
      <label className="block"><span className="block text-faint">허용 테스트 API origin · 한 줄에 하나</span>
        <textarea required disabled={working} rows={2} value={values.allowed_origins ?? ''}
          onChange={(e) => setValues((was) => ({ ...was, allowed_origins: e.target.value }))}
          className="w-full rounded-md border border-border bg-background px-2 py-1" /></label>
      {Object.keys(values).filter((k) => k.startsWith('revision:')).map((key) => <label key={key} className="block">
        <span className="block text-faint">{key.slice(9)} 버전 · 환경이 바뀌면 갱신</span>
        <input required disabled={working} value={values[key]} onChange={(e) => setValues((was) => ({ ...was, [key]: e.target.value }))}
          className="h-7 w-full rounded-md border border-border bg-background px-2" /></label>)}
      {Object.keys(values).filter((k) => k.startsWith('native:')).map((key) => <label key={key} className="block">
        <span className="block text-faint">{nativeLabels[key.slice(7)]}</span>
        <input required disabled={working} value={values[key]} onChange={(e) => setValues((was) => ({ ...was, [key]: e.target.value }))}
          className="h-7 w-full rounded-md border border-border bg-background px-2" /></label>)}
      {config.manifest.flows.some((f) => f.kind === 'desktop') && <p className="text-faint">Windows 테스트 앱을 작업 전용 데스크톱에서 실행한다. 기존 앱에 연결하지 않는다.</p>}
      <details><summary className="cursor-pointer text-primary">승인할 주요 흐름과 실행 명령</summary>
        <ul className="mt-1 space-y-1">{config.manifest.flows.map((f) => <li key={f.id}>
          {f.title} · {f.kind}<code className="block break-all font-mono text-[12px]">{f.command}</code>
          {f.native != null && <pre className="whitespace-pre-wrap break-all">{JSON.stringify(f.native, null, 2)}</pre>}
        </li>)}</ul></details>
      <div className="flex gap-2"><Btn type="submit" tone="primary" disabled={working}>명령 확인 후 설정 저장</Btn>
        <Btn disabled={working} onClick={() => setEditing(false)}>닫기</Btn></div>
    </form>}
    {record?.flows?.map((flow) => <details key={flow.id} className="mt-2">
      <summary className="cursor-pointer">{flow.title} · {flow.finished_at == null ? '미완료' : flow.ok ? '통과' : '실패'}</summary>
      {flow.reason && <p className="mt-1 text-muted-foreground">{flow.reason}</p>}
      {flow.reuse_reason && <p className="mt-1 text-faint">재사용 근거 · {flow.reuse_reason}</p>}
      <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-secondary p-2 font-mono text-[12px]">
        {JSON.stringify(flow.evidence, null, 2)}{flow.log ? `\n\n${flow.log}` : ''}
      </pre>
      {!!flow.attempts?.length && <details className="mt-1"><summary className="cursor-pointer text-faint">이전 실행 증거 · {flow.attempts.length}회</summary>
        <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-[12px]">{JSON.stringify(flow.attempts, null, 2)}</pre>
      </details>}
    </details>)}
    {fault && <p role="alert" className="mt-2 whitespace-pre-wrap text-destructive">{fault}</p>}
  </section>
}

const RECOVERY = '반복 발견 재조사'
/** The recovery phases a `[계속]` acts on; the others run or wait for the next round. */
const STOPPED = ['limits', 'budget', 'deadline', 'unknown', 'exhausted', 'host', 'web_unavailable', 'format', 'page']
const KIND: Record<string, string> = { repeat: '고쳤다고 한 뒤 같은 발견', oscillation: '사라졌다 다시 나온 발견', backstop: '7라운드 이후 거절' }
const PHASE: Record<string, string> = {
  required: '리서치 필요', limits: '한도 입력 대기', researching: '리서치 중', fixing: '리서치 반영 수정 중', fixed: '다음 라운드 대기',
  exhausted: '두 번 뒤에도 반복 — 결정 대기', ruled: '사람이 정함', budget: '한도 소진', deadline: '시간 한도', unknown: '사용량 모름',
  host: '리서치 턴 실패', web_unavailable: '웹 도구 없음', format: '답 형식 오류', page: '페이지 기록 실패',
}
type LimitsDraft = { seconds: string; calls: string; tokens: string }

const parsedLimits = (d: LimitsDraft): api.ResearchLimits => ({ seconds: Number(d.seconds), calls: Number(d.calls), tokens: Number(d.tokens) })
const limitsOk = (d: LimitsDraft) => {
  const l = parsedLimits(d)
  return Number.isFinite(l.seconds) && l.seconds > 0 && [l.calls, l.tokens].every((v) => Number.isInteger(v) && v > 0)
}

function LimitsInput({ value, onChange, spent }: { value: LimitsDraft; onChange: (v: LimitsDraft) => void; spent: api.Recovery['spent'] }) {
  const fields: [keyof LimitsDraft, string, number][] = [['seconds', '초', spent.seconds], ['calls', '호출', spent.calls], ['tokens', '토큰', spent.tokens]]
  return <fieldset className="grid grid-cols-3 gap-1.5" aria-label="리서치 한도">
    {fields.map(([key, label, used]) => <label key={key} className="block min-w-0">
      <span className="block text-faint">{label} · 쓴 양 {used}</span>
      <input inputMode="numeric" value={value[key]} onChange={(e) => onChange({ ...value, [key]: e.target.value })}
        className="h-7 w-full min-w-0 rounded-md border border-border bg-background px-2" />
    </label>)}
  </fieldset>
}

/** Recurring findings: what triggered research, the cycle, its pages and what followed. */
function RecoveryPanel({ recovery }: { recovery: Record<string, api.Recovery> }) {
  return <section aria-label="반복 발견 재조사" className="mt-3 space-y-2 rounded-md border border-border p-3">
    <div className="font-heading text-[11px] font-semibold text-faint">반복 발견 재조사</div>
    {Object.values(recovery).map((r) => {
      const pages = [...r.attempts.map((a) => ({ page: a.page, note: a.status === 'abandoned' ? ' · 수정 전에 끊김'
        : a.outcome ? ` · 다음 라운드 ${a.outcome.result === 'cleared' ? '해소' : '반복'}` : '' })),
        { page: r.active?.page, note: ' · 이번 수정' }].filter((p) => p.page)
      return <div key={r.issue_id}>
        <div>
          <span className="font-mono text-[11.5px]">{r.issue_id}</span> · {PHASE[r.phase] ?? r.phase} · 사이클 {r.cycles_started}/2
          {r.trigger && <span className="text-faint"> · {KIND[r.trigger.kind] ?? r.trigger.kind} (라운드 {r.trigger.rounds.join(', ')})</span>}
        </div>
        {r.trigger?.evidence?.map((e) => <div key={e} className="text-muted-foreground">{e}</div>)}
        {pages.map((p) => <div key={p.page} className="font-mono text-[11.5px] text-faint">{p.page}{p.note}</div>)}
        {r.limits && <div className="text-faint">한도 {r.limits.seconds}초 · {r.limits.calls}호출 · {r.limits.tokens}토큰 —
          쓴 양 {r.spent.seconds}초 · {r.spent.calls}호출 · {r.spent.tokens}토큰{r.spent.unknown ? ' 이상(사용량 모름)' : ''}</div>}
      </div>
    })}
  </section>
}
