import { useCallback, useEffect, useRef, useState } from 'react'
import { Btn } from '@/components/Modal'
import { Reply } from '@/components/Agent'
import { Toolbar } from '@/components/Toolbar'
import { useReview } from '@/lib/work'
import { useParagraphOverlay } from '@/lib/overlay'
import * as api from '@/lib/api'
import { PROFILE_LABEL, type LocalVerification, type Spec, type VerificationConfig, type LoopSettings, type Options } from '@/lib/api'
import { LOOPING } from '@/lib/tasks'
import { cn } from '@/lib/utils'

/** The review tab: the loop of the selected task's spec — its rounds, why it
 *  stopped, and the buttons that act on it. Which spec and PR it is, the
 *  pane's header says. */
type Props = { spec: Spec | null; onChanged: () => void; on: boolean; progressOn?: boolean; options: Options | null;
  settings: LoopSettings | null; onSettings: (s: LoopSettings) => Promise<void>; onPeek: (path: string, line: number) => void }

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
      <span className="mr-auto font-heading text-[11px] font-semibold text-faint">리뷰 모델{named && ' · 계획에서 지정'}</span>
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

function ReviewBody({ spec, onChanged, on, progressOn = on, onPeek }: Props) {
  const [working, setWorking] = useState('')
  const [fault, setFault] = useState('')
  const [note, setNote] = useState('')
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
  const cloud = spec.implementation_environment === 'claude-cloud'
  const analysis = cloud && (spec.local_verification?.needs_research || ['reanalysis', 'unstable'].includes(spec.local_verification?.state ?? ''))
  const rounds = spec.rounds ?? []
  const checked = spec.validation?.round
  const final = spec.validation?.final
  const running = spec.validation?.phase === 'final_running'
  const proven = !running && spec.unproven === ''
  return (
    <div className="h-full overflow-y-auto px-5 py-4 text-[12.5px]">
      <div className="flex items-center gap-2">
        <a href={spec.pr.url} target="_blank" rel="noreferrer" className="font-mono text-[10.5px] text-primary hover:underline">
          GitHub 에서 PR #{spec.pr.number} 열기
        </a>
        {(spec.state === '머지 가능' || /^PR #\d+$/.test(spec.state)) && spec.plan_commit !== 'asked' && (
          <Btn className="ml-auto" disabled={!!working}
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

      {cloud && <VerificationPanel spec={spec} />}

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
              {(reason === '반론' || analysis) && (
                <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2}
                  aria-label={analysis ? '재분석 근거' : '발견에 대한 판단'}
                  placeholder={analysis ? '원인 · 근거 · 다음 실험 — 재분석 후 재개한다' : '그 발견에 정한 것 — 다음 라운드 지시에 들어간다'}
                  className="w-full rounded-md border border-border bg-background px-2 py-1 text-[13.5px]" />
              )}
              <Btn tone="primary" disabled={!!working || ((reason === '반론' || analysis) && !note.trim())}
                onClick={() => act('resume', () => api.resumeSpec(spec.id, note.trim(), spec.repo))}>
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
              라운드 확인 · {checked.selection === 'mapped' ? '변경이 닿는 것만' : '전체'} ·{' '}
              <span className={checked.ok ? 'text-primary' : 'text-destructive'}>{checked.ok ? '통과' : '실패'}</span>
              <span className="ml-1 font-mono text-[10.5px] text-faint">{checked.head.slice(0, 7)}</span>
            </div>
          )}
          <div>
            최종 게이트 ·{' '}
            {running ? '도는 중 — 끝나기 전에는 머지하지 않는다'
              : !final ? '아직 — 리뷰가 허용한 커밋에서 돈다'
                : !final.ok ? <span className="text-destructive">실패 — {final.reason}</span>
                  : spec.unproven ? `지난 통과는 이제 안 선다 — ${spec.unproven}`
                    : <span className="text-primary">통과</span>}
            {final && !running && <span className="ml-1 font-mono text-[10.5px] text-faint">{final.head.slice(0, 7)}</span>}
          </div>
        </div>
      )}

      {spec.state === '머지 가능' && spec.approved && (
        <div className="mt-3 rounded-md border border-st-ready/50 p-3">
          <div className="flex items-center gap-2">
            {proven ? (
              <Btn tone="primary" disabled={!!working} onClick={() => act('merge', () => api.mergeSpec(spec.id, spec.approved!, spec.repo))}>
                {working === 'merge' ? '머지하는 중…' : '머지 ▸'}
              </Btn>
            ) : (
              // The server refuses the merge and sends the spec back to run only the final gate.
              <Btn disabled={!!working || running}
                onClick={() => act('final', () => cloud
                  ? api.startLoops([spec.pr!.number], 'claude-cloud', spec.repo).then(({ results }) => {
                    if (results[0]?.error) throw new Error(results[0].error)
                  }) : api.mergeSpec(spec.id, spec.approved!, spec.repo).catch(() => undefined))}>
                {working === 'final' ? '…' : cloud ? '로컬 검증 다시 시작' : '최종 게이트 돌리기'}
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
        <p className="mt-3 text-muted-foreground">
          GitHub 이 들고 있다 — {spec.queued || '대기열'}. 실제로 머지되면 정리하고, 대기에서 빠지면 멈춘다.
        </p>
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
            <span className="truncate">{file.title}</span>
            <button type="button" className="ml-auto" onClick={() => setFile(null)}>닫기</button>
          </div>
          <pre className="mt-1 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-secondary p-2 text-[12px]">{shownFile}</pre>
        </div>
      )}
    </div>
  )
}

function Detail({ text, on }: { text: string; on: boolean }) {
  const shown = useParagraphOverlay(text, on)
  return <div className="mt-0.5 whitespace-pre-wrap break-words text-muted-foreground">{shown}</div>
}

const VERIFICATION_STATE: Record<LocalVerification['state'], string> = {
  waiting_environment: '환경 준비 대기', waiting_review: '로컬 리뷰 대기', running: '주요 흐름 검증 중',
  runtime_passed: '실행 검증 통과 · 리뷰 대기', verified: '로컬 검증·리뷰 통과', waiting_cloud: '클라우드 수정 대기',
  reanalysis: '두 수정 주기 실패 · 재분석 필요', unstable: '간헐적 실패 · 원인 확인 필요', interrupted: '중단 · 재개 대기',
}

/** Repository-specific setup and receipts stay in the existing review tab.
 * Requests retain the repository captured on mount; retired results are dropped. */
function VerificationPanel({ spec }: { spec: Spec }) {
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
      const body = { ...config?.settings, ...Object.fromEntries(Object.entries(values).filter(([k]) => !k.startsWith('revision:'))),
        revisions, allowed_origins: values.allowed_origins.split('\n').map((v) => v.trim()).filter(Boolean),
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
  return <section aria-label="클라우드 구현의 로컬 검증" className="mt-3 rounded-md border border-border p-3">
    <div className="font-heading text-[11px] font-semibold">Claude Code Cloud → 로컬 검증</div>
    <p className="mt-1">{record ? VERIFICATION_STATE[record.state] : '로컬 검증 대기'}</p>
    <p className="mt-1 text-faint">클라우드에서 PR 인계·주요 흐름 명세 준비 → 이 기계의 테스트 환경 설정 → 로컬 실행 검증 → 독립 리뷰. 실패하면 클라우드에서 고친 뒤 재개한다.</p>
    {record?.reason && <p className="mt-1 whitespace-pre-wrap text-muted-foreground">{record.reason}</p>}
    <div className="mt-2 flex flex-wrap gap-2">
      <Btn disabled={working} onClick={async () => {
        try { setHandoff((await api.cloudInstructions(spec.id, spec.repo)).text) }
        catch (err) { setFault(String(err instanceof Error ? err.message : err)) }
      }}>클라우드 인계 지시 보기</Btn>
      <Btn disabled={working || LOOPING.test(spec.state)} onClick={() => void configure()}>프로젝트 검증 설정</Btn>
      <Btn disabled={working || LOOPING.test(spec.state)} onClick={() => void protect()}>GitHub 필수 검사 설정</Btn>
    </div>
    {handoff && <label className="mt-2 block">클라우드 구현자에게 전달할 지시
      <textarea readOnly value={handoff} rows={7} onFocus={(e) => e.target.select()}
        className="mt-1 w-full rounded-md border border-border bg-background p-2 font-mono text-[12px]" />
    </label>}
    <p className="mt-1 text-faint">.env 는 로컬에 유지한다. GitHub 의 기존 보호 규칙에 필수 검사를 추가한다.</p>
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
      <details><summary className="cursor-pointer text-primary">승인할 주요 흐름과 실행 명령</summary>
        <ul className="mt-1 space-y-1">{config.manifest.flows.map((f) => <li key={f.id}>
          {f.title} · {f.kind}<code className="block break-all font-mono text-[12px]">{f.command}</code>
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
