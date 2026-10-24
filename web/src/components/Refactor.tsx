import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Btn } from '@/components/Modal'
import { Toolbar } from '@/components/Toolbar'
import * as api from '@/lib/api'
import { useOverlay } from '@/lib/overlay'
import { cn } from '@/lib/utils'

export type RefactorMode = api.RefactorMode
/** Every mode a record may carry; the last three only on runs of the earlier workflow. */
export const REFACTOR_MODE: Record<api.RefactorMode | api.LegacyMode, string> = {
  preserve: '동작 보존 구조 정리', simplify: '불필요한 복잡성·계약 정리', rework: '구조·성능 근본 개선',
  cleanup: '이전 · 빠른 정리', restructure: '이전 · 모듈 재구성', full: '이전 · 전면 리팩터링',
}
// `docs/plans/refactor/7-purpose-modes.md`, Modes: a purpose each, not a ladder.
const MODES: { id: RefactorMode; purpose: string; changes: string[]; not: string[] }[] = [
  { id: 'preserve', purpose: '관찰 가능한 동작은 그대로 두고 함수·클래스·모듈을 분명한 책임으로 나눈다.',
    changes: ['보호되지 않은 핵심 동작에 동작 고정·회귀 테스트부터 추가', '큰 함수를 책임 단위로 분리',
      '분리 뒤 할 일이 없어진 코드, 안 쓰임이 확인된 코드, 잘못 놓인 클래스 정리'],
    not: ['동작 변경·알려진 버그 수정', '버그를 계약으로 고정', '길이만 줄이려는 분할', '상상한 재사용을 위한 추상화'] },
  { id: 'simplify', purpose: '중복 검사, 낡은 테스트 계약, 불분명한 이름을 걷어낸다.',
    changes: ['이미 보장된 곳에서 반복되는 검사 제거', '안 쓰이는 호환 분기·대체 경로·래퍼 제거',
      '낡은 테스트 계약을 현재 요구사항에 맞게 갱신', '역할·도메인 의미로 이름 바꾸기'],
    not: ['인증·권한·입력·무결성·동시성·외부 경계의 필요한 방어 제거', '현재 코드를 통과시키려는 기대값 변경',
      '실제 호환 버전(API·스키마·마이그레이션) 제거'] },
  { id: 'rework', purpose: '다른 두 모드로 못 고치는 의존 구조와 측정된 병목을 고친다.',
    changes: ['순환 의존 끊기', '책임을 경계 너머로 옮기기', '왕복·반복 계산·조회·직렬화 제거',
      '알고리즘·자료구조·I/O·프로세스 사용 개선'],
    not: ['기본값처럼 캐시·병렬화·계층·프레임워크 추가', '같은 조건의 측정 없이 개선 주장',
      '관련 없는 명세 안에서 계약 변경·데이터 마이그레이션'] },
]
const STATE: Record<string, string> = { running: '진행 중', stopped: '멈춤', done: '완료', superseded: '새 조사로 대체',
  continued: '에이전트 작업으로 전환' }
const PHASE: Record<string, string> = { baseline: '기준 커밋 게이트', map: '저장소 지도', pipelines: '파이프라인 설명',
  integrate: '명세 정리', recheck: '명세 다시 확인', done: '끝' }
const KIND: Record<string, string> = { duplication: '중복', coupling: '결합', mixed_responsibility: '책임 혼재',
  defensive: '과잉 방어', test_contract: '테스트 계약', performance: '성능', naming: '이름', dead_code: '안 쓰는 코드' }
const LEVEL: Record<string, string> = { high: '높음', medium: '보통', low: '낮음' }
const box = 'not-prose rounded-md border border-border bg-card p-3 text-[12.5px]'
// Chosen at `[리팩터링 시작]`, then fixed for every card of that survey.
const ROLES: { key: keyof api.RefactorRoles; label: string; note: string }[] = [
  { key: 'survey', label: '조사', note: '지도·파이프라인·명세 정리, 명세의 [다시 확인]. 읽기 전용' },
  { key: 'worker', label: '구현', note: '명세 작업 셀 — 첫 턴, 리뷰 뒤 수정, 충돌 수정' },
  { key: 'reviewer', label: '리뷰', note: '명세 PR 의 리뷰 셀. 따로 연 읽기 전용 세션' },
]
const NO_ROLE = { model: '', effort: '' }
const role = (r: api.PlanRole) => `${r.model}${r.effort ? ` · ${r.effort}` : ''}`
/** `조사 a / 구현 b / 리뷰 c`, as a survey and each of its cards show them. */
export const rolesText = (roles: api.RefactorRoles) => ROLES.map((r) => `${r.label} ${role(roles[r.key])}`).join(' / ')
const where = (e: api.SurveyEvidence) => `${e.path} · ${e.symbol}`

/** The refactoring focus: a purpose mode, `[리팩터링 시작]` and the selected
 *  mode's latest survey of this repository. A survey only reads; its specs are
 *  ordinary cards below the conversation, each started on its own. */
export function RefactorSurvey({ repo, mode, generation, options, korean, busy, onMode, onSpecs }: {
  repo: string; mode: RefactorMode; generation: number; options: api.Options | null; korean: boolean
  busy: boolean; onMode: (mode: RefactorMode) => void; onSpecs: () => void
}) {
  // Nothing preset: every role is picked here, never taken from the chat's toolbar or a CLI default.
  const [roles, setRoles] = useState<api.RefactorRoles>({ survey: NO_ROLE, worker: NO_ROLE, reviewer: NO_ROLE })
  const unpicked = ROLES.filter((r) => !roles[r.key].model).map((r) => r.label)
  const [runs, setRuns] = useState<api.Survey[] | null>(null)
  const [scope, setScope] = useState('')
  const [fault, setFault] = useState('')
  const [working, setWorking] = useState(false)
  const [revision, setRevision] = useState(0)
  const running = useRef(false)
  const changed = useRef(onSpecs)
  useEffect(() => { changed.current = onSpecs })
  useEffect(() => {
    let stale = false
    let timer: number | undefined
    const read = () => api.get('/api/refactors', repo)
      .then((r) => api.json<{ repo: string; scope: string; runs: api.Survey[] }>(r, '리팩터링 조사'))
      .then((next) => {
        if (stale || next.repo !== repo) return
        setRuns(next.runs); setScope(next.scope); setFault('')
        const now = next.runs.some((r) => r.kind === 'survey' && r.state === 'running')
        // A survey that just ended has written or rewritten cards.
        if (running.current && !now) changed.current()
        running.current = now
        if (now) timer = window.setTimeout(read, 3000)
      }).catch((err) => { if (!stale) setFault(String(err)) })
    void read()
    const again = () => setRevision((n) => n + 1)
    window.addEventListener('refactor-survey', again)
    return () => { stale = true; window.clearTimeout(timer); window.removeEventListener('refactor-survey', again) }
  }, [repo, revision])
  const surveys = (runs ?? []).filter((r) => r.kind === 'survey')
  // A stopped survey with no cards yet resumes into the current generation.
  const latest = surveys.find((r) => !r.recheck && r.mode === mode
    && ((r.generation ?? 0) === generation || (r.state === 'stopped' && !r.specs?.length)))
  const rechecks = surveys.filter((r) => r.recheck && r.mode === mode && r.state !== 'done' && r.state !== 'superseded')
  const live = surveys.find((r) => r.state === 'running')
  const legacy = (runs ?? []).filter((r) => r.kind !== 'survey')
  async function act(path: string, body?: unknown) {
    setWorking(true)
    try {
      await api.post(path, body, 'POST', repo).then((r) => api.json(r, '리팩터링 조사'))
      setFault('')
    } catch (err) { setFault(String(err instanceof Error ? err.message : err)) }
    finally { setWorking(false); setRevision((n) => n + 1) }
  }
  return <div className="mb-5 space-y-3">
    <div role="radiogroup" aria-label="리팩터링 모드" className="grid gap-2 md:grid-cols-3">
      {MODES.map((m) => <button key={m.id} type="button" role="radio" aria-checked={m.id === mode}
        disabled={busy || working || m.id === mode} onClick={() => onMode(m.id)}
        className={cn('min-w-0 rounded-md border p-3 text-left text-[12.5px] disabled:cursor-default',
          m.id === mode ? 'border-primary bg-primary/5' : 'border-border bg-card hover:bg-secondary')}>
        <span className="block font-heading text-[13.5px] font-semibold">{REFACTOR_MODE[m.id]}</span>
        <span className="mt-1 block">{m.purpose}</span>
        <span className="mt-2 block text-faint">대표 변경</span>
        <ul className="text-muted-foreground">{m.changes.map((c) => <li key={c}>· {c}</li>)}</ul>
        <span className="mt-2 block text-faint">하지 않는 것</span>
        <ul className="text-muted-foreground">{m.not.map((c) => <li key={c}>· {c}</li>)}</ul>
      </button>)}
    </div>
    <div className={box}>
      <div aria-label="리팩터링 모델" className="mb-3 space-y-2">
        {ROLES.map((r) => <div key={r.key} className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <div className="w-44 shrink-0">
            <div className="font-heading text-[11px] font-semibold">{r.label} 모델</div>
            <div className="text-[10.5px] text-faint">{r.note}</div>
          </div>
          <Toolbar value={roles[r.key]} options={options} busy={busy || working}
            onChange={(next) => setRoles((prev) => ({ ...prev, [r.key]: { model: next.model, effort: next.effort } }))} />
        </div>)}
        <p className="text-[11.5px] text-muted-foreground">이 조사에서 나온 모든 명세가 이 모델로 조사·구현·리뷰된다. 시작한 뒤에는 바뀌지 않는다.</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Btn tone="primary" disabled={busy || working || !runs || !!live || !!unpicked.length}
          title={live ? `조사 ${live.id} 가 끝나거나 멈춘 뒤 시작한다` : unpicked.length ? `${unpicked.join('·')} 모델을 먼저 고른다`
            : '코드를 읽기만 한다 — 바꾸기·커밋·푸시·PR 없음'}
          onClick={() => void act('/api/refactors', { request_id: crypto.randomUUID(), mode, roles })}>
          리팩터링 시작
        </Btn>
        <span className="min-w-0 text-muted-foreground">
          <span className="font-mono">{repo}</span> · {REFACTOR_MODE[mode]} · {unpicked.length
            ? <span className="text-wait">{unpicked.join('·')} 모델을 고르지 않았다</span> : rolesText(roles)}
        </span>
      </div>
      <p className="mt-2 text-muted-foreground">
        조사는 코드를 읽기만 한다. 파이프라인별 설명과 실행할 명세가 나오고, 명세의 [시작]은 따로 누른다 — 작업트리·리뷰·검증을 거쳐 PR 까지만 가며 머지는 직접 한다.
      </p>
      {scope === 'hub' && <p className="mt-2 text-wait">wiki-agent 자체는 명세마다 별도 작업트리에서 실행한다. 머지와 앱 재시작은 직접 한다.</p>}
      {fault && <p role="alert" className="mt-2 whitespace-pre-wrap break-words text-destructive">{fault}</p>}
      {!runs && !fault && <p role="status" className="mt-2 text-faint">조사 기록을 읽는 중…</p>}
      {live && live.mode !== mode && <p className="mt-2 text-wait">다른 모드의 조사 `{live.id}` 가 진행 중이다 — {REFACTOR_MODE[live.mode]}</p>}
      {runs && !latest && <p className="mt-2 text-faint">이 모드로 한 조사가 아직 없다.</p>}
    </div>
    {latest && <Report run={latest} korean={korean} working={working} onAct={act} />}
    {rechecks.map((r) => <Report key={r.id} run={r} korean={korean} working={working} onAct={act} />)}
    {!!legacy.length && <details className="text-[12.5px]">
      <summary className="cursor-pointer text-muted-foreground">이전 방식의 실행 {legacy.length}개 · 읽기 전용</summary>
      <ul className="mt-2 space-y-2">{legacy.map((r) => <li key={r.id}><LegacyRun run={r} korean={korean} /></li>)}</ul>
    </details>}
  </div>
}

/** One survey: its progress and controls, the baseline, the coverage, and
 *  each pipeline explained. The model's English reads through the overlay. */
function Report({ run, korean, working, onAct }: {
  run: api.Survey; korean: boolean; working: boolean; onAct: (path: string) => Promise<void>
}) {
  const reports = run.pipelines ?? {}
  const piped = run.map?.pipelines ?? []
  const originals = [
    ...piped.flatMap((p) => [p.name, p.role]),
    ...Object.values(reports).flatMap((p) => [p.what, p.inputs, p.outputs, p.owns, p.change, p.low_debt_reason ?? '',
      ...p.links.map((l) => l.how), ...p.misplaced.map((m) => m.what), ...p.checked, ...p.unchecked,
      ...p.debts.flatMap((d) => [d.summary, d.scope, d.if_left, d.size.why, d.difficulty.why, d.risk.why])]),
    ...(run.map?.unchecked ?? []), ...(run.map?.omm.diverged ?? []), ...(run.map?.excluded ?? []).map((e) => e.reason),
    run.survey?.coverage.scope ?? '', ...(run.survey?.coverage.unchecked ?? []),
    ...(run.survey?.coverage.excluded ?? []).map((e) => e.reason), run.survey?.nothing ?? '',
  ]
  const translated = useOverlay(originals, korean)
  const overlay = new Map(originals.map((text, i) => [text, translated[i]]))
  const t = (text: string) => overlay.get(text) ?? text
  const gate = run.baseline
  const unconfirmed = (run.models ?? []).filter((m) => !m.reported)
  const level = (name: string, l: api.SurveyLevel) => <li>{name} {LEVEL[l.level] ?? l.level} — {t(l.why)}</li>
  return <section aria-label={run.recheck ? `명세 다시 확인 ${run.recheck}` : '리팩터링 조사'} className={box}>
    <div className="flex flex-wrap items-center gap-2 font-heading text-[13px] font-semibold">
      <span>{run.recheck ? `다시 확인 · ${run.recheck}` : `조사 · ${REFACTOR_MODE[run.mode]}`}</span>
      <span role="status">{STATE[run.state] ?? run.state}{run.state !== 'done' && ` · ${PHASE[run.phase] ?? run.phase}`}
        {run.phase === 'pipelines' && run.state === 'running' && ` ${Object.keys(reports).length}/${piped.length}`}</span>
      <span className="ml-auto font-mono text-[10.5px] font-normal text-muted-foreground">{run.id}</span>
    </div>
    <div className="mt-2 flex flex-wrap gap-2">
      {run.state === 'running' && <Btn disabled={working} onClick={() => void onAct(`/api/refactors/${run.id}/cancel`)}>멈추기</Btn>}
      {run.state === 'stopped' && <Btn disabled={working} onClick={() => void onAct(`/api/refactors/${run.id}/resume`)}>재개</Btn>}
    </div>
    {run.stopped && run.state === 'stopped' && <p className="mt-2 whitespace-pre-wrap break-words text-destructive">{run.stopped.detail || run.stopped.reason}</p>}
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
      {run.base && <><dt className="text-faint">기준 커밋</dt><dd className="font-mono">{run.base.slice(0, 12)}{run.branch && ` · ${run.branch}`}</dd></>}
      {gate && <><dt className="text-faint">기준 게이트</dt><dd className="min-w-0 break-words">
        <span className="font-mono">{gate.cmd}</span> · {gate.ok ? '통과' : `종료 코드 ${gate.code ?? '없음'}`}
        {!!gate.failures.length && ` · 이미 실패 ${gate.failures.length}건`}{!gate.ok && !gate.parsed && ' · 실패 이름을 읽지 못함'}
        {!!gate.failures.length && <ul className="font-mono text-[11.5px]">{gate.failures.slice(0, 20).map((f) => <li key={f}>{f}</li>)}</ul>}
      </dd></>}
      {run.roles && <><dt className="text-faint">고정 모델</dt><dd>{rolesText(run.roles)}</dd></>}
      {!!run.models?.length && <><dt className="text-faint">모델</dt><dd>{run.role?.model || '기본'} · 답한 모델 {
        [...new Set(run.models.map((m) => m.reported).filter(Boolean))].join(', ') || '확인 안 됨'}
        {!!unconfirmed.length && ` · ${unconfirmed.length}턴 미확인`}</dd></>}
      {run.map && <><dt className="text-faint">.omm</dt><dd>{run.map.omm.used ? '읽고 코드와 대조함' : '없거나 쓰지 않음'}</dd></>}
    </dl>
    {run.survey && <div className="mt-3">
      <div className="font-heading text-[11px] text-faint">조사 범위</div>
      <p className="mt-1">{t(run.survey.coverage.scope)}</p>
      {run.survey.nothing && <p className="mt-1 text-wait">명세 없음 — {t(run.survey.nothing)}</p>}
    </div>}
    {(!!run.map?.unchecked.length || !!run.survey?.coverage.unchecked.length || !!run.map?.omm.diverged.length
      || !!run.map?.excluded.length) && <details className="mt-2">
      <summary className="cursor-pointer text-primary">못 본 곳 · 제외 · .omm 불일치</summary>
      <ul className="mt-1 space-y-0.5">
        {[...(run.map?.unchecked ?? []), ...(run.survey?.coverage.unchecked ?? [])].map((u, i) => <li key={`u${i}`}>· 미확인: {t(u)}</li>)}
        {(run.map?.excluded ?? []).map((e) => <li key={`e${e.path}`}>· 제외 <span className="font-mono">{e.path}</span> — {t(e.reason)}</li>)}
        {(run.map?.omm.diverged ?? []).map((d, i) => <li key={`d${i}`}>· .omm 불일치: {t(d)}</li>)}
      </ul>
    </details>}
    {!!piped.length && <div className="mt-3 space-y-2">
      <div className="font-heading text-[11px] text-faint">파이프라인 {piped.length}개</div>
      {piped.map((p) => {
        const r = reports[p.id]
        return <details key={p.id} className="rounded-md border border-border p-2">
          <summary className="cursor-pointer">
            <span className="font-semibold">{t(p.name)}</span>
            {r ? ` · 부채 ${r.debts.length}건` : ' · 설명 대기'}
            <span className="block text-muted-foreground">{t(p.role)}</span>
          </summary>
          <p className="mt-1 break-all font-mono text-[11.5px] text-muted-foreground">{p.entry} · {p.paths.join(', ')}</p>
          {r && <div className="mt-2 space-y-2 break-words">
            <p>{t(r.what)}</p>
            <p><span className="text-faint">입력</span> {t(r.inputs)} · <span className="text-faint">출력</span> {t(r.outputs)}</p>
            {!!r.links.length && <ul>{r.links.map((l, i) => <li key={i}>· <span className="font-mono">{l.pipeline}</span> — {t(l.how)}</li>)}</ul>}
            <p><span className="text-faint">맡아야 할 책임</span> {t(r.owns)}</p>
            {r.misplaced.map((m, i) => <p key={i}><span className="text-faint">잘못 맡은 책임</span> {t(m.what)}
              <span className="block font-mono text-[11.5px] text-muted-foreground">{m.evidence.map(where).join(', ')}</span></p>)}
            {r.debts.map((d, i) => <div key={i} className="rounded border border-border p-2">
              <p><span className="font-semibold">{KIND[d.kind] ?? d.kind}</span> · {t(d.summary)}</p>
              <p className="font-mono text-[11.5px] text-muted-foreground">{d.evidence.map(where).join(', ')}</p>
              <p><span className="text-faint">범위</span> {t(d.scope)}</p>
              <ul>{level('크기', d.size)}{level('난이도', d.difficulty)}{level('위험', d.risk)}</ul>
              <p><span className="text-faint">그대로 두면</span> {t(d.if_left)}</p>
            </div>)}
            {r.low_debt_reason && <p><span className="text-faint">부채가 적은 이유</span> {t(r.low_debt_reason)}</p>}
            <p><span className="text-faint">이 모드로 바꿀 것</span> {t(r.change)}</p>
            <details><summary className="cursor-pointer text-muted-foreground">읽은 곳 {r.checked.length} · 못 본 곳 {r.unchecked.length}</summary>
              <ul>{r.checked.map((c, i) => <li key={`c${i}`}>· {t(c)}</li>)}{r.unchecked.map((c, i) => <li key={`n${i}`}>· 미확인: {t(c)}</li>)}</ul>
            </details>
          </div>}
        </details>
      })}
    </div>}
    <details className="mt-3 text-muted-foreground"><summary className="cursor-pointer">사용량</summary>
      작업 시간 {Math.round(run.spent.seconds)}초 · 호출 {run.spent.calls} · 확인된 토큰 {run.spent.tokens.toLocaleString()}
      {run.spent.unknown && ' · 일부 토큰 사용량은 알 수 없음'} · 시작 {new Date(run.created * 1000).toLocaleString('ko-KR')}
    </details>
    {run.state === 'done' && !run.recheck && <p className="mt-2 text-muted-foreground">명세 {run.specs?.length ?? 0}개 — 대화 아래에 있다.</p>}
  </section>
}

/** A run of the earlier cleanup/restructure/full workflow, as it was left:
 *  nothing resumes or approves it any more; its pull requests are ordinary tasks. */
function LegacyRun({ run, korean }: { run: api.Survey; korean: boolean }) {
  const [goal] = useOverlay([run.goal ?? ''], korean)
  return <div aria-label="이전 방식의 실행" className={box}>
    <div className="flex flex-wrap items-center gap-2 font-heading text-[13px] font-semibold">
      <span>{REFACTOR_MODE[run.mode] ?? run.mode}</span><span>{STATE[run.state] ?? run.state} · {run.phase}</span>
      <span className="ml-auto font-mono text-[10.5px] font-normal text-muted-foreground">{run.id}</span>
    </div>
    {run.goal && <p className="mt-1 break-words">{goal}</p>}
    {!!run.steps?.length && <ol className="mt-1">{run.steps.map((s) => <li key={s.n}>
      {s.n}. {s.state}{s.pr ? ` · PR #${s.pr}` : ''}{s.spec ? ` · ${s.spec}` : ''}</li>)}</ol>}
    {run.stopped && <p className="mt-1 break-words text-muted-foreground">{run.stopped.detail || run.stopped.reason}</p>}
    <p className="mt-1 text-faint">시작 {new Date(run.created * 1000).toLocaleString('ko-KR')}</p>
  </div>
}

/** Why an unstarted survey spec cannot start, as far as the screen can tell.
 *  Files changed since the survey's base are the server's to find at Start. */
export function locks(spec: api.Spec, specs: api.Spec[], mode?: RefactorMode, generation?: number): string[] {
  const r = spec.refactor
  if (!r?.survey) return []
  const out: string[] = []
  if (mode && (r.mode !== mode || (spec.source.refactor_generation ?? 0) !== (generation ?? 0)))
    out.push('모드가 바뀌었다 — 지금 모드로 다시 조사한다')
  for (const dep of r.depends ?? []) {
    const state = specs.find((s) => s.id === dep)?.state
    if (state !== '머지됨') out.push(`선행 명세 ${dep} 가 머지된 뒤 시작한다${state ? ` (지금 ${state})` : ' (없어짐)'}`)
  }
  if (r.detail?.hypothesis && !r.detail.measure) out.push('측정 명령이 없는 성능 가설이다 — [다시 확인]으로 측정할 수 있게 고친다')
  return out
}

/** Tell the survey panel a run began elsewhere on the screen (a card's `[다시 확인]`). */
export const surveyStarted = () => window.dispatchEvent(new Event('refactor-survey'))

/** A survey spec's own fields, beside the ordinary goal, scope and done items. */
export function SurveyDetail({ spec, korean }: { spec: api.Spec; korean: boolean }) {
  const r = spec.refactor
  const d = r?.detail
  const originals = d ? [d.problem, d.change, d.boundary, d.impact, d.rollback, d.risk.why, d.together ?? '',
    ...d.tests.add, ...d.tests.remove.map((x) => x.reason), ...d.tests.contract.flatMap((c) => [c.old, c.new, c.why]),
    d.measure?.metric ?? '', d.measure?.task ?? '', d.hypothesis?.claim ?? '', d.hypothesis?.check ?? '',
    d.contract_change?.impact ?? '', d.contract_change?.compatibility ?? '', d.contract_change?.migration ?? '',
    d.contract_change?.rollback ?? ''] : []
  const translated = useOverlay(originals, korean)
  const overlay = new Map(originals.map((text, i) => [text, translated[i]]))
  const t = (text: string) => overlay.get(text) ?? text
  if (!r || !d) return null
  const row = (name: string, body: ReactNode) => <><dt className="text-faint">{name}</dt><dd className="min-w-0 break-words">{body}</dd></>
  return <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12.5px]">
    {row('파이프라인', <span className="font-mono">{r.pipeline}</span>)}
    {row('조사', <span className="font-mono">{r.survey} · 기준 커밋 {r.base?.slice(0, 12)}</span>)}
    {row('문제', t(d.problem))}
    {row('바꿀 것', t(d.change))}
    {row('경계', t(d.boundary))}
    {row('근거', <span className="font-mono text-[11.5px]">{d.evidence.map(where).join(', ')}</span>)}
    {row('대상 파일', <span className="font-mono text-[11.5px]">{d.files.join(', ')}</span>)}
    {!!d.creates?.length && row('만들 파일', <span className="font-mono text-[11.5px]">{d.creates.join(', ')}</span>)}
    {row('위험', `${LEVEL[d.risk.level] ?? d.risk.level} — ${t(d.risk.why)}`)}
    {row('영향', t(d.impact))}
    {row('되돌리기', t(d.rollback))}
    {d.together && row('함께 바꾸는 이유', t(d.together))}
    {!!r.depends?.length && row('선행 명세', <span className="font-mono">{r.depends.join(', ')}</span>)}
    {row('테스트', <ul>
      {d.tests.add.map((x, i) => <li key={`a${i}`}>· 추가: {t(x)}</li>)}
      {d.tests.remove.map((x, i) => <li key={`r${i}`}>· 삭제 <span className="font-mono">{x.test}</span> — {t(x.reason)}</li>)}
      {d.tests.contract.map((x, i) => <li key={`c${i}`}>· 계약 갱신 <span className="font-mono">{x.test}</span>: {t(x.old)} → {t(x.new)} ({t(x.why)})</li>)}
      {!d.tests.add.length && !d.tests.remove.length && !d.tests.contract.length && <li>기존 테스트를 그대로 지킨다</li>}
    </ul>)}
    {d.measure && row('측정', <>{t(d.measure.metric)} · {t(d.measure.task)} · <span className="font-mono">{d.measure.argv.join(' ')}</span> × {d.measure.repeat}</>)}
    {d.hypothesis && row('성능 가설', `${t(d.hypothesis.claim)} — 확인 방법: ${t(d.hypothesis.check)}`)}
    {d.contract_change && row('계약 변경', `${t(d.contract_change.impact)} · 호환 ${t(d.contract_change.compatibility)} · 이전 ${t(d.contract_change.migration)} · 되돌리기 ${t(d.contract_change.rollback)}`)}
  </dl>
}

/** What `refactor_run` checked before the pull request: the PR body's `리팩터링 검증` on the card. */
export function RefactorCheck({ spec }: { spec: api.Spec }) {
  const c = spec.refactor_check
  const frozen = spec.refactor_frozen
  if (!c && !frozen) return null
  return <div aria-label="리팩터링 검증" className="mt-2 rounded-md border border-border p-2 text-[12.5px]">
    <div className="font-heading text-[11px] text-faint">리팩터링 검증</div>
    <ul className="mt-1 space-y-0.5 break-words">
      {c?.changed && <li>조사 뒤 바뀐 파일 {c.changed.length}개 — 대상·근거 파일은 그대로</li>}
      {frozen && <li>동작 고정 테스트 {frozen.tests.length}개 · <span className="font-mono">{frozen.head.slice(0, 12)}</span> 에 고정</li>}
      {c?.final === 'ci' && <li>최종 검증은 CI 가 맡는다</li>}
      {c?.final === 'local' && <li>최종 명령 {c.ok ? '통과' : '실패'}{c.baseline && <> · 기준 게이트 <span className="font-mono">{c.baseline.cmd}</span> 종료 코드 {c.baseline.code ?? '없음'}</>}</li>}
      {c?.unparsed && <li className="text-destructive">실패한 검사를 이름으로 가리지 못해 기준과 비교하지 못했다</li>}
      {!!c?.pre_existing?.length && <li>시작 전부터 있던 실패 {c.pre_existing.length}건: <span className="font-mono">{c.pre_existing.slice(0, 10).join(', ')}</span></li>}
      {c?.new && <li className={c.new.length ? 'text-destructive' : ''}>새 실패 {c.new.length}건{!!c.new.length && <>: <span className="font-mono">{c.new.slice(0, 10).join(', ')}</span></>}</li>}
      {c?.measured && <li>측정 {c.measured.metric} · 중앙값 {c.measured.before.median_ms} ms → {c.measured.after.median_ms} ms ({c.measured.repeat}회)</li>}
    </ul>
  </div>
}
