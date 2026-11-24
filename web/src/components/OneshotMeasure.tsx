import { useState } from 'react'
import { Btn } from '@/components/Modal'
import { Track } from '@/components/OneshotTrack'
import { api, type Conflict, type Keep, type MeasureInput, type MeasureRun, type Metric, type SpecView, type Step } from '@/lib/oneshot'

type Act = (fn: () => Promise<SpecView>) => Promise<boolean>
const PAD = 'flex flex-col gap-4 px-10 py-6 max-[1500px]:px-7 max-[560px]:px-4'
const BOX = 'flex flex-col gap-3 rounded-lg border border-border bg-card px-5 py-4'
const VIDEO = /\.(mp4|webm)$/i

/** The two backend stages the owner acts in (`owns`): ruling between the builder and the review, and the
 *  measurement, running or waiting. */
export function Owner({ repo, view, busy, act }: { repo: string; view: SpecView; busy: boolean; act: Act }) {
  if (view.stage === 'conflict') return <Conflicts repo={repo} view={view} busy={busy} act={act} />
  if (view.working) return <Track view={view} halt={<Measuring repo={repo} view={view} busy={busy} act={act} />} />
  return <Measure repo={repo} view={view} busy={busy} act={act} />
}

/** The real measurement between the backend's build and its review (`tool/main/oneshot_measure.py`). The build
 *  agent wrote every step below; the owner follows them one window at a time and pastes what the last step names
 *  into its box. The server measures by itself until every metric that needs no person passes; then the owner
 *  measures once, the program on their own screen with its sound, and approves or refuses. A stop (a wrong input,
 *  the owner's stop) shows above and the button starts again. */
function Measure({ repo, view, busy, act }: { repo: string; view: SpecView; busy: boolean; act: Act }) {
  const m = view.measure
  if (!m) return null
  const last = m.runs.at(-1)
  const current = last && last.head === m.head ? last : undefined
  const waiting = !!current && verdictDue(current)
  const failed = current?.status === 'failed'   // stopped at the cap: the button hands it to the build agent, no input needed
  const missing = failed ? [] : m.plan?.inputs.filter((x) => !x.given) ?? []
  const owner = m.next === 'owner'
  return (
    <div className={PAD}>
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold">진짜 환경 실측</h1>
        <p className="text-muted-foreground">
          백엔드가 빌드 검사를 통과했다 (<span className="font-mono">{m.head.slice(0, 12)}</span>). 리뷰 전에 진짜 서비스에 붙여 돌려 본다.
          {m.pr && <> 코드는 <a className="underline" href={m.pr.url} target="_blank" rel="noreferrer">PR #{m.pr.number}</a> 초안에 올라가 있다.</>}
        </p>
      </header>
      {view.stopped && (
        <p role="alert" className="rounded-md bg-wait/10 px-3 py-2">{view.stopped.detail}</p>
      )}
      {m.error && (
        <div role="alert" className="flex flex-col items-start gap-2 rounded-md bg-destructive/10 px-3 py-2">
          <p>실측 계획을 읽지 못했다 — {m.error}</p>
          <Btn tone="wait" disabled={busy || !!view.working}
            onClick={() => void act(() => api.measureStart(repo, m.head))}>빌드 에이전트에게 계획을 고치게 하기</Btn>
        </div>
      )}
      {m.plan && <>
        {m.plan.inputs.length > 0 && (
          <section aria-label="넣을 값" className="flex flex-col gap-3">
            <h2 className="text-[15px] font-semibold">1. 넣을 값 — 순서대로 따라 한다</h2>
            {m.plan.inputs.map((x) => <Input key={`${m.head}-${x.id}-${x.env}`} repo={repo} head={m.head} x={x} busy={busy} act={act} />)}
          </section>
        )}
        <section aria-label="실측" className={BOX}>
          <h2 className="text-[15px] font-semibold">
            {waiting ? '2. 주인 실측 — 판정을 기다린다' : owner ? '2. 주인 실측 — 한 번만 한다' : '2. 자동 실측 — 서버가 먼저 잰다'}</h2>
          {waiting ? <p>주인 실측이 끝났다. 아래 결과를 보고 듣고, 승인하거나 거절한다.</p> : owner ? <>
            <p>사람이 필요 없는 항목은 서버가 모두 통과시켰다. {m.plan.app
              ? '이제 통과한 그 PC 프로그램이 이 화면에 뜨고 소리가 난다. 직접 보고 듣는다.'
              : '이제 서버가 백엔드를 다시 띄우고, 사람이 필요한 항목까지 잰다. 끝나면 클립을 직접 듣는다.'}</p>
            {m.plan.during.length > 0 && <><p>[주인 실측 시작]을 누른 뒤, 도는 동안 이것을 한다:</p><Steps steps={m.plan.during} /></>}
          </> : (
            <p>{m.plan.app ? '서버가 이 PC 에서 PC 프로그램을 띄워 사람이 필요 없는 항목을 혼자 잰다. 화면에 뜨지 않고 소리도 나지 않는다.'
              : '서버가 이 PC 에서 백엔드를 띄워 사람이 필요 없는 항목을 혼자 잰다.'}
              {' '}통과하지 못한 항목만 빌드 에이전트가 고치고, 서버가 다시 잰다. 모두 통과하면 주인 실측을 한 번 부른다.</p>
          )}
          <p className="text-muted-foreground">한 번에 약 {m.plan.measure.minutes}분 걸린다. 진짜 서비스로 아래를 잰다.</p>
          <Metrics run={current} plan={m.plan.metrics} />
          <div className="flex flex-wrap items-center gap-3">
            <Btn tone="wait" disabled={busy || !!view.working || missing.length > 0 || waiting}
              onClick={() => void act(() => api.measureStart(repo, m.head))}>
              {failed ? '빌드 에이전트에게 고치게 하기' : owner ? '주인 실측 시작' : '자동 실측 시작'}</Btn>
            {missing.length > 0 && <span className="text-wait">아직 넣지 않은 값: {missing.map((x) => x.id).join(', ')}</span>}
          </div>
        </section>
      </>}
      {current && current.status !== 'input' && <Result repo={repo} run={current} busy={busy} act={act} />}
      {m.runs.length > 1 && (
        <details className="text-muted-foreground">
          <summary>지난 실측 {m.runs.length - 1}회</summary>
          <ul className="mt-2 flex flex-col gap-1">
            {m.runs.slice(0, -1).map((r) => <li key={r.n}>{r.n}회 · <span className="font-mono">{r.head.slice(0, 12)}</span> · {said(r)}</li>)}
          </ul>
        </details>
      )}
    </div>
  )
}

/** The running measurement, shown with the track: the owner's `during` steps stay in view, with a stop. */
function Measuring({ repo, view, busy, act }: { repo: string; view: SpecView; busy: boolean; act: Act }) {
  const during = view.measure?.plan?.during ?? []
  const owner = view.measure?.next === 'owner'   // the server's own measurement asks nothing of the owner
  return (
    <section aria-label="실측 중" className={BOX}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p>{view.working === 'publish' ? 'PR #1 초안을 올리는 중이다.'
          : owner ? '주인 실측 중이다. 화면에 뜬 프로그램을 보고 소리를 듣는다.' : '서버가 혼자 실측하는 중이다. 할 일은 없다.'}</p>
        {view.working === 'measure' && <Btn disabled={busy} onClick={() => void act(() => api.measureStop(repo))}>멈춤</Btn>}
      </div>
      {view.working === 'measure' && owner && during.length > 0 && <><p>지금 할 일:</p><Steps steps={during} /></>}
    </section>
  )
}

function Steps({ steps }: { steps: Step[] }) {
  return (
    <ol className="flex flex-col gap-2">
      {steps.map((s, i) => (
        <li key={i} className="grid grid-cols-[2rem_1fr] gap-x-2 rounded-md bg-secondary/40 px-3 py-2">
          <span className="font-mono text-muted-foreground">{i + 1}</span>
          <div className="flex flex-col gap-1">
            <span className="text-[13px] text-muted-foreground">창: {s.window}</span>
            {s.url && <a className="self-start break-all underline" href={s.url} target="_blank" rel="noreferrer">이 페이지 열기 — {s.url}</a>}
            <span>{s.do}</span>
            <span className="text-muted-foreground">그러면: {s.see}</span>
            {s.copy && <span className="font-medium">이 화면의 「{s.copy}」를 복사해 아래 칸에 붙여 넣는다.</span>}
          </div>
        </li>
      ))}
    </ol>
  )
}

function Input({ repo, head, x, busy, act }: { repo: string; head: string; x: MeasureInput; busy: boolean; act: Act }) {
  const [value, setValue] = useState('')
  const [open, setOpen] = useState(!x.given)
  const label = `${x.id} ${x.what}`
  async function send() {
    if (await act(() => api.measureGive(repo, head, x, value))) { setValue(''); setOpen(false) }
  }
  return (
    <article className={BOX}>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-mono">{x.id}</span>
        <span className="font-medium">{x.what}</span>
        <span className="text-muted-foreground">{x.kind === 'secret' ? '비밀 값 · Windows 자격 증명 관리자에만 들어간다' : '보통 값'}</span>
        <span role="status" className={x.given ? 'text-st-ready' : 'text-wait'}>{x.given ? '넣음' : '없음'}</span>
        {x.given && <Btn tone="ghost" onClick={() => setOpen(!open)}>{open ? '안내 접기' : '안내 보기'}</Btn>}
      </div>
      {open && <Steps steps={x.guide} />}
      <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); void send() }}>
        <input aria-label={`${label} 값`} value={value} onChange={(e) => setValue(e.target.value)}
          type={x.kind === 'secret' ? 'password' : 'text'} autoComplete="off" spellCheck={false}
          placeholder={x.given ? '바꾸려면 새 값을 붙여 넣는다' : `「${x.guide.at(-1)?.copy ?? '값'}」를 여기에 붙여 넣는다`}
          className="min-w-0 flex-1 rounded-md border border-input bg-background px-2 py-1 text-foreground max-[560px]:text-base" />
        <Btn type="submit" tone="primary" disabled={busy || !value.trim()} aria-label={`${label} 넣기`}>넣기</Btn>
        {x.given && <Btn tone="ghost" disabled={busy} aria-label={`${label} 지우기`}
          onClick={() => void act(() => api.measureTakeBack(repo, head, x))}>지우기</Btn>}
      </form>
    </article>
  )
}

const shown = (v: unknown) => v === null || v === undefined ? '—' : typeof v === 'boolean' ? (v ? '예' : '아니오') : String(v)

function Metrics({ run, plan }: { run?: MeasureRun; plan: Metric[] }) {
  const rows = run?.metrics ?? plan.map((m) => ({ ...m, value: null, ok: false }))
  return (
    <table className="w-full text-left">
      <thead className="text-[13px] text-muted-foreground">
        <tr><th className="py-1 pr-3 font-normal">잴 것</th><th className="pr-3 font-normal">기준</th>
          {run?.metrics && <><th className="pr-3 font-normal">잰 값</th><th className="font-normal">결과</th></>}</tr>
      </thead>
      <tbody>
        {rows.map((m) => (
          <tr key={m.id} className="border-t border-border">
            <td className="py-1 pr-3">{m.what} <span className="text-muted-foreground">({m.requirements.join(', ')})</span>
              {m.person && <span className="ml-1 text-wait">· 사람 필요</span>}</td>
            <td className="pr-3 font-mono">{m.pass.op} {shown(m.pass.value)}{m.unit}</td>
            {run?.metrics && <><td className="pr-3 font-mono">{shown(m.value)}{m.value !== null ? m.unit : ''}</td>
              {run.mode === 'auto' && m.person && !m.ok
                ? <td className="text-muted-foreground">주인 실측에서</td>
                : <td className={m.ok ? 'text-st-ready' : 'text-destructive'}>{m.ok ? '통과' : '실패'}</td>}</>}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** The owner's own measurement, waiting for their verdict: a result from before the server measured first
 *  (no `mode`) is never one (`oneshot_measure.waiting`). */
const verdictDue = (r: MeasureRun) => r.status === 'owner' && r.mode === 'owner' && !r.owner

const said = (r: MeasureRun) => (r.owner ? (r.owner.approve ? '주인 승인' : '주인 거절')
  : r.status === 'owner' ? (r.mode === 'owner' ? '판정 기다림' : '판정하지 않음 — 자동 실측부터 다시') : r.status === 'passed' ? '자동 실측 통과 — 주인 실측 차례'
    : r.status === 'input' ? '넣은 값이 틀림' : r.status === 'stale' ? '판정하지 않음'
      : r.mode === 'auto' ? '자동 실측 실패 — 빌드가 고친다' : '실패 — 빌드가 고친다') + (r.comment_error ? ` · ${r.comment_error}` : '')

function Result({ repo, run, busy, act }: { repo: string; run: MeasureRun; busy: boolean; act: Act }) {
  const [note, setNote] = useState('')
  const waiting = verdictDue(run)
  return (
    <section aria-label="실측 결과" className={BOX}>
      <h2 className="text-[15px] font-semibold">3. 결과 — {run.n}회째 · {said(run)}</h2>
      {run.error && <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2">{run.error}</p>}
      {run.notes && <p className="whitespace-pre-wrap">{run.notes}</p>}
      {!!run.clips?.length && (
        <div className="flex flex-col gap-2">
          <p>만든 소리와 화면을 직접 들어 본다:</p>
          {run.clips.map((c) => VIDEO.test(c)
            ? <video key={c} controls preload="metadata" src={api.clip(repo, run.n, c)} className="max-w-full rounded-md" aria-label={c} />
            : <div key={c} className="flex flex-wrap items-center gap-2"><span className="font-mono text-[13px]">{c}</span>
                <audio controls preload="metadata" src={api.clip(repo, run.n, c)} aria-label={c} /></div>)}
        </div>
      )}
      {run.status === 'failed' && <p className="text-muted-foreground">빌드 에이전트가 통과하지 못한 항목만 받아 고치고, 서버가 고친 커밋으로 다시 잰다.</p>}
      {run.status === 'passed' && <p className="text-muted-foreground">사람이 필요 없는 항목이 모두 통과했다. 위의 [주인 실측 시작]으로 한 번 직접 보고 듣는다.</p>}
      {waiting && (
        <form className="flex flex-col gap-2" onSubmit={(e) => e.preventDefault()}>
          <label className="flex flex-col gap-1">
            <span>들어 본 느낌 (거절하면 무엇이 틀렸는지 꼭 적는다)</span>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} maxLength={2000}
              className="rounded-md border border-input bg-background px-2 py-1 text-foreground max-[560px]:text-base" />
          </label>
          <div className="flex flex-wrap gap-2">
            <Btn tone="wait" disabled={busy} onClick={() => void act(() => api.measureDecide(repo, run, true, note))}>승인 — 리뷰로</Btn>
            <Btn tone="danger" disabled={busy || !note.trim()}
              onClick={() => void act(() => api.measureDecide(repo, run, false, note))}>거절 — 빌드가 고친다</Btn>
          </div>
        </form>
      )}
    </section>
  )
}

const WHY: Record<Conflict['why'], string> = {
  replaced: '빌더가 이 영역의 방식을 한 번 통째로 바꿨는데도 리뷰가 또 거절했다.',
  answered: '빌더가 두 회차 동안 증거와 고정 테스트로 답했는데도 리뷰가 또 거절했다.',
}
const KEEP: { id: Keep; label: string; note: string }[] = [
  { id: 'review', label: '리뷰어 쪽', note: '빌더가 지적대로 고친다' },
  { id: 'build', label: '빌더 쪽', note: '지금 코드가 맞다 — 리뷰가 다시 막지 않는다' },
  { id: 'owner', label: '직접 정함', note: '어떻게 할지 적는다' },
]

/** The areas only the owner can settle (`oneshot_review.rule`): each side's words, then one choice per area. */
function Conflicts({ repo, view, busy, act }: { repo: string; view: SpecView; busy: boolean; act: Act }) {
  const conflicts = view.oneshot.conflicts ?? []
  const head = view.oneshot.reviews?.at(-1)?.head ?? ''
  const [picked, setPicked] = useState<Record<string, { keep?: Keep; note: string }>>({})
  const ready = conflicts.every((c) => picked[c.key]?.keep && (picked[c.key]?.keep !== 'owner' || picked[c.key]?.note.trim()))
  const set = (key: string, part: { keep?: Keep; note?: string }) =>
    setPicked((p) => ({ ...p, [key]: { note: p[key]?.note ?? '', keep: p[key]?.keep, ...part } }))
  return (
    <div className={PAD}>
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold">주인 결정</h1>
        <p className="text-muted-foreground">빌더와 리뷰가 같은 곳에서 계속 맞선다. 양쪽 근거를 읽고 영역마다 하나를 고른다.</p>
      </header>
      {conflicts.map((c) => (
        <article key={c.key} className={BOX}>
          <h2 className="text-[15px] font-semibold">{c.area}</h2>
          <p className="text-muted-foreground">{WHY[c.why]}</p>
          <div className="grid grid-cols-2 gap-3 max-[900px]:grid-cols-1">
            <div className="flex flex-col gap-2">
              <h3 className="font-medium">리뷰어가 본 것</h3>
              {c.findings.map((f, i) => (
                <p key={i} className="rounded-md bg-secondary/40 px-3 py-2">
                  <span className="font-mono text-[13px]">[{f.severity}] {f.location}</span><br />{f.reason}
                  {f.evidence && <><br /><span className="text-muted-foreground">근거: {f.evidence}</span></>}
                </p>
              ))}
            </div>
            <div className="flex flex-col gap-2">
              <h3 className="font-medium">빌더의 답</h3>
              {c.answers.length ? c.answers.map((a, i) => (
                <p key={i} className="rounded-md bg-secondary/40 px-3 py-2">
                  <span className="font-mono text-[13px]">{a.round}회차 · {a.location}</span><br />{a.evidence}
                  <br /><span className="text-muted-foreground">고치면 깨지는 것: {a.breaks.join(', ')} · 고정 테스트 {a.test}</span>
                </p>
              )) : <p className="text-muted-foreground">답 없이 방식을 바꿨다.</p>}
            </div>
          </div>
          <fieldset className="flex flex-col gap-2">
            <legend className="sr-only">{c.area} 결정</legend>
            {KEEP.map((k) => (
              <label key={k.id} className="flex items-baseline gap-2">
                <input type="radio" name={`keep-${c.key}`} checked={picked[c.key]?.keep === k.id} onChange={() => set(c.key, { keep: k.id })} />
                <span className="font-medium">{k.label}</span><span className="text-muted-foreground">{k.note}</span>
              </label>
            ))}
            <textarea aria-label={`${c.area} 메모`} rows={2} maxLength={2000} value={picked[c.key]?.note ?? ''}
              placeholder={picked[c.key]?.keep === 'owner' ? '어떻게 할지 적는다' : '덧붙일 말 (없어도 된다)'}
              onChange={(e) => set(c.key, { note: e.target.value })}
              className="rounded-md border border-input bg-background px-2 py-1 text-foreground max-[560px]:text-base" />
          </fieldset>
        </article>
      ))}
      <Btn tone="wait" className="self-start" disabled={busy || !ready || !conflicts.length}
        onClick={() => void act(() => api.rule(repo, head, Object.fromEntries(conflicts.map((c) => [c.key, {
          keep: picked[c.key]!.keep!, note: picked[c.key]?.note ?? '' }]))))}>결정 보내기</Btn>
    </div>
  )
}
