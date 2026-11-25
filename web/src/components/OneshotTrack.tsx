import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Reply } from '@/components/Agent'
import { Btn } from '@/components/Modal'
import { Split, Title } from '@/components/OneshotSpec'
import { getSwitch, modeOf, type Switch, type TranslationMode } from '@/lib/api'
import { api, stageLabel, type Critique, type Inflight, type Review, type SpecView } from '@/lib/oneshot'
import { useWork } from '@/lib/work'
import { cn } from '@/lib/utils'

/** A stage that runs on its own, followed while it runs (`DESIGN.md`, "Oneshot window"): what is running and for
 *  how long, the run's work turns as they stream in its worktree, and beside them the stage's record — its tries
 *  against the server's caps, each review and critique with its findings, and what the run has spent. A stopped
 *  run shows the same, under its reason and `[재개]`, so why it stopped is read off what happened. */
export function Track({ view, halt, post, apply }: {
  view: SpecView; halt?: ReactNode; post?: (text: string) => Promise<boolean>; apply?: () => Promise<boolean>
}) {
  const o = view.oneshot
  const kinds = o.intent?.kinds ?? (o.intent?.kind ? [o.intent.kind] : [])   // a record from before kinds has one
  return (
    <Split label={`${stageLabel(view.stage, kinds)} 진행`} sideLabel="기록" side={<Record view={view} />}>
      <div className="flex flex-col gap-5 px-10 pt-5 pb-8 max-[1500px]:px-7 max-[560px]:px-4">
        {halt}
        {o.inflight && <Now inflight={o.inflight} kinds={kinds} />}
        <Log path={view.worktree ?? ''} onSend={TELLS.includes(view.stage)   // as the box sends: the pane follows the answer
          ? (text) => void (post ?? ((t: string) => api.tell(view.repo, t)))(text) : undefined} />
        {TELLS.includes(view.stage) && <Tell view={view} post={post} apply={apply} />}
      </div>
    </Split>
  )
}

// The stages the owner can speak to the build agent in (`oneshot_tell.OPEN`), and those whose words wait for the
// frontend build (`WAIT`): the screens are being designed with them then.
export const TELLS = ['tests', 'build', 'measure', 'review', 'conflict', 'split', 'board', 'frontend', 'r2',
  'build-front', 'critique', 'phone', 'pc', 'r3']
const WAITS = ['split', 'board', 'frontend', 'r2']
const PARKED = ['measure', 'conflict', 'phone', 'pc', 'r3']
const FRONT = ['build-front', 'critique', 'phone', 'pc', 'r3', 'pr', 'done']
const HARD = ['kind', 'rekind', 'fake']

/** The owner's words to the build agent, any time it builds: said into the turn that runs, and the design rewritten
 *  from them before the next step, which carries on from the code as it is. `post` sends them; an idle screen
 *  passes its own, so the view the server answers with shows the rewrite starting at once; it resolves false when
 *  the server refused, and the words stay in the box. */
export function Tell({ view, post, apply }: {
  view: SpecView; post?: (text: string) => Promise<boolean>; apply?: () => Promise<boolean>
}) {
  const o = view.oneshot
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [fault, setFault] = useState('')
  const run = (fn: () => Promise<boolean>, done = () => {}) => {
    setBusy(true)
    setFault('')
    fn().then((taken) => { if (taken) done() })
      .catch((err) => setFault(String(err?.message ?? err))).finally(() => setBusy(false))
  }
  const send = () => text.trim() && run(() => (post ?? ((t: string) => api.tell(view.repo, t).then(() => true)))(text.trim()),
    () => setText(''))
  const waiting = o.tell_pending?.length ?? 0
  // A parked run (`oneshot_tell.parked`) takes waiting words in only when the owner says so, and only words a
  // rewrite would take now (`oneshot_tell.taken`): those said while the screens are designed wait for their build.
  const parked = !view.working && (view.stopped ? !HARD.includes(view.stopped.reason) : PARKED.includes(view.stage))
  const due = (o.tell_pending ?? []).some((w) => !WAITS.includes(w.stage) || FRONT.includes(view.stage))
  return (
    <section aria-label="구현 에이전트에게 말하기" className="flex flex-col gap-2 rounded-lg border border-border bg-card px-4 py-3">
      <Title><span>구현 에이전트에게 말하기</span></Title>
      <p className="text-muted-foreground">
        {WAITS.includes(view.stage)
          ? '화면을 정하는 중이라, 적은 말은 프런트엔드 빌드가 시작될 때 설계에 반영된다.'
          : '도는 작업 턴에 바로 들어가고, 다음 단계 전에 설계 모델이 이 말대로 설계를 고친다. 모델을 바꾸라는 말도 된다. 처음부터 다시 하지 않고 지금 코드에서 이어 간다.'}
      </p>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={4000} disabled={busy}
        aria-label="구현 에이전트에게 할 말" className="w-full resize-y rounded-md border border-border bg-background px-3 py-2" />
      <div className="flex flex-wrap items-center gap-3">
        <Btn disabled={busy || !text.trim()} onClick={send}>보내기</Btn>
        {waiting > 0 && <span className="text-[12.5px] text-muted-foreground">설계에 반영을 기다리는 말 {waiting}개</span>}
        {due && parked && (
          <Btn disabled={busy} onClick={() => run(apply ?? (() => api.applyTell(view.repo).then(() => true)))}>지금 반영</Btn>
        )}
        {fault && <span role="alert" className="text-destructive">{fault}</span>}
      </div>
      {(o.retold?.length ?? 0) > 0 && (
        <ul className="flex flex-col gap-1 text-[12.5px] text-muted-foreground">
          {o.retold!.map((r) => (
            <li key={r.ts}>
              반영됨 — {r.changed.length ? `바뀐 항목 ${r.changed.join(', ')}` : '설계는 그대로, 빌드에 전달'}: {r.words.join(' / ')}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

// The roles `inflight` names that are not a stage of their own.
const ROLE_LABEL: Record<string, string> = { critic: '화면 비평', talk: '요구 대화', intake: '하고 싶은 것', profile: '환경 카드' }
const roleLabel = (role: string, kinds: readonly string[]) => ROLE_LABEL[role] ?? stageLabel(role, kinds)

function clock(s: number) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = Math.floor(s % 60)
  return h ? `${h}시간 ${m}분` : m ? `${m}분 ${r}초` : `${r}초`
}

/** Seconds since `from` (ms), ticking. */
function useSince(from: number) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])
  return Math.max(0, (now - from) / 1000)
}

/** What runs now. A work turn streams below; a model call answers once, into the record beside. */
function Now({ inflight, kinds }: { inflight: Inflight; kinds: readonly string[] }) {
  const s = useSince(inflight.sent * 1000)
  const work = !!inflight.turn
  return (
    <section aria-label="지금" className="flex flex-col gap-1 rounded-lg border border-border bg-card px-4 py-3">
      <p className="flex flex-wrap items-center gap-x-2 font-heading text-[15px] font-semibold">
        <span aria-hidden className="size-2 shrink-0 rounded-full bg-progress motion-safe:animate-pulse" />
        {roleLabel(inflight.role, kinds)} {work ? '작업 턴' : '모델 호출'}
        <span role="timer" className="font-mono text-[13px] font-normal tabular-nums text-muted-foreground">{clock(s)}째</span>
        <span className="text-[12px] font-normal text-faint">{inflight.codex ? 'Codex' : 'Claude'}</span>
      </p>
      <p className="text-muted-foreground">
        {work ? '에이전트가 작업트리에서 하는 일이 아래 작업 기록에 그대로 흐른다.'
          : '답이 한 번에 온다. 오면 그 결과가 기록에 남고 다음 단계가 열린다.'}
      </p>
    </section>
  )
}

const SHOWN = 6   // turns kept open; earlier ones fold behind one button

/** The run's worktree turns, live: the same record and stream maintenance shows for a task, read-only here. */
function Log({ path, onSend }: { path: string; onSend?: (text: string) => void }) {
  // Its own record: a turn the server starts here (each attempt, a review's repair) reaches it on the feed.
  const work = useWork()
  const { load, forget } = work
  const [mode, setMode] = useState<TranslationMode>('off')
  const [all, setAll] = useState(false)
  const [failed, setFailed] = useState('')   // the path whose record could not be read
  const end = useRef<HTMLDivElement>(null)
  // The agent's answers come in English: shown in Korean as maintenance shows them, by the same switch, read
  // again whenever it is set here (`setSwitch`) or anywhere else (the feed's `sync` resyncs every window).
  useEffect(() => {
    const set = (event: Event) => setMode(modeOf((event as CustomEvent<Switch>).detail))
    const read = () => { getSwitch().then((s) => setMode(modeOf(s))).catch(() => {}) }
    read()
    window.addEventListener('switch', set)
    window.addEventListener('server-resync', read)
    return () => {
      window.removeEventListener('switch', set)
      window.removeEventListener('server-resync', read)
    }
  }, [])
  // Leaving the run closes its stream: each turn awaiting an answer would otherwise keep a connection open.
  const read = useCallback(() => { load(path).then((ok) => setFailed(ok ? '' : path)) }, [path, load])
  useEffect(() => {
    if (!path) return
    read()
    return () => forget(path)
  }, [path, read, forget])
  const turns = (path && work.turns[path]) || []
  const last = turns.at(-1)
  const grown = turns.length + (last?.text.length ?? 0) + (last?.steps.length ?? 0)
  useEffect(() => { if (last?.pending) end.current?.scrollIntoView({ block: 'end' }) }, [grown, last?.pending])
  const shown = all ? turns : turns.slice(-SHOWN)

  return (
    <section aria-label="작업 기록" className="flex flex-col gap-3">
      <Title><span>작업 기록</span>{turns.length > 0 && <span className="font-mono">턴 {turns.length}개</span>}</Title>
      {failed === path && !turns.length && (
        <p role="alert" className="flex flex-wrap items-center gap-3 text-destructive">
          작업 기록을 읽지 못했다.<Btn tone="ghost" onClick={read}>다시 읽기</Btn>
        </p>
      )}
      {failed !== path && !turns.length && (
        <p className="text-muted-foreground">
          아직 이 실행의 작업 턴이 없다. 설계와 리뷰처럼 답이 한 번에 오는 단계의 결과는 기록에 남는다.
        </p>
      )}
      {turns.length > shown.length && (
        <Btn tone="ghost" className="self-start" onClick={() => setAll(true)}>앞선 턴 {turns.length - shown.length}개 보기</Btn>
      )}
      {shown.map((t) => t.role === 'user' ? (
        <details key={t.key} className="rounded-md border border-border px-3 py-2">
          <summary className="cursor-pointer text-[12.5px] text-muted-foreground">서버가 보낸 지시</summary>
          <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[12px] text-faint">{t.text}</pre>
        </details>
      ) : (
        <div key={t.key} className="rounded-md border border-border bg-card px-3 py-2.5">
          <Reply turn={t} on={mode !== 'off'} progressOn={mode === 'full'} onPeek={() => {}}
            onSend={t === turns.at(-1) ? onSend : undefined}   // a choice the build agent offered goes back as the owner's words
            onAnswer={(turn, id, allow, scope, answers, picks) => work.answer(path, turn, id, allow, scope, answers, picks)} />
        </div>
      ))}
      <div ref={end} />
    </section>
  )
}

const ATTEMPTED = new Set(['tests', 'build', 'build-front', 'pr'])   // the stages the server's check retries

function money(v?: number) { return v == null ? '' : `$${v.toFixed(2)}` }

/** The stage's record: its tries against the caps, every review and critique, and the spend so far. */
function Record({ view }: { view: SpecView }) {
  const o = view.oneshot
  const lim = view.limits
  const spent = o.spend ?? {}
  const of = (n: number, cap?: number) => `${n}${cap ? ` / ${cap}` : ''}`
  // A running check is on its next try; a stopped one counts the checks that failed, as the server does.
  const tried: [string, string] = view.working
    ? ['시도', of((o.attempts ?? 0) + 1, lim?.attempts)] : ['검사 실패', of(o.attempts ?? 0, lim?.attempts)]
  // Only the critiques since the count restarted go against the cap; a cap stop restarts it at once, and keeps
  // the count it reached (`counted`) — a failed critique call stops for the same reason with none.
  // Several kinds count each kind's own, the kind of the last critique (`oneshot_critique.counted`).
  const looks = o.critiques?.length ?? 0
  const last = o.critiques?.at(-1)?.kind
  const base = Math.max(o.critique_base ?? 0, last ? o.critique_bases?.[last] ?? 0 : 0)
  const since = (o.critiques ?? []).slice(base).filter((c) => c.kind === last).length
  // A cap stop saved before `counted` says the count only in its detail, which the server words the same way.
  const before = view.stopped?.reason === 'critique' && /^(\d+)회차에도 높은 발견/.exec(view.stopped.detail)
  const reached = view.stopped?.counted ?? (before ? Number(before[1]) : 0)
  const counted = since === 0 && reached ? reached : since
  const facts: [string, string][] = [
    ...(ATTEMPTED.has(view.stage) ? [tried] : []),
    ...(o.reviews?.length ? [['리뷰 거절', of(o.refused ?? 0, lim?.refusals)] as [string, string]] : []),
    ...(looks ? [['화면 비평', of(counted, lim?.critiques) + (counted !== looks ? ` · 모두 ${looks}회` : '')] as [string, string]] : []),
    ...(o.cases?.length ? [['계약 테스트', `${o.cases.length}개`] as [string, string]] : []),
    // Beside the spending, the gate runs that reached each outside call (`oneshot_calls.reached`).
    ...(o.design?.outside?.length
      ? [['바깥 호출', o.design.outside.map((x) => `${x.id} ${o.calls?.[x.id] ?? 0}번`).join(' · ')] as [string, string]] : []),
    ['쓴 것', [money(spent.usd), spent.seconds != null && clock(spent.seconds), spent.calls != null && `호출 ${spent.calls}번`]
      .filter(Boolean).join(' · ') || '아직 없다'],
  ]
  return (
    <>
      <Title><span>기록</span></Title>
      <dl className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-4 gap-y-2">
        {facts.map(([k, v]) => [
          <dt key={`${k}-t`} className="text-muted-foreground">{k}</dt>,
          <dd key={`${k}-d`} className="font-mono tabular-nums">{v}</dd>,
        ])}
      </dl>
      {!!o.reviews?.length && <Reviews reviews={o.reviews} />}
      {!!o.critiques?.length && <Critiques critiques={o.critiques} />}
      <div className="pb-6" />
    </>
  )
}

// `wait` is only for what waits on the owner (`DESIGN.md`, "Color"): a finding is not, so the worst is `warn`
// and the rest step down in ink.
const SEVERE: Record<string, string> = {
  P0: 'bg-destructive/15 text-destructive', high: 'bg-destructive/15 text-destructive',
  P1: 'bg-secondary text-foreground', medium: 'bg-secondary text-foreground',
}
function Severity({ s }: { s: string }) {
  return <span className={cn('rounded px-1 font-mono text-[11px]', SEVERE[s] ?? 'bg-secondary text-muted-foreground')}>{s}</span>
}
const tally = (list: { severity: string }[]) =>
  Object.entries(list.reduce<Record<string, number>>((n, f) => ({ ...n, [f.severity]: (n[f.severity] ?? 0) + 1 }), {}))
    .map(([s, n]) => `${s} ${n}`).join(' · ')

const blocking = (r: Review) => r.findings.filter(f => f.severity !== 'P2' && !f.device)
const area = (f: { area?: string }) => (f.area ?? '').split(/\s+/).filter(Boolean).join(' ').toLowerCase()

/** Per round, in order: the blocking findings, the areas no earlier round named, and the diff's size. A count
 * that does not fall while the diff grows is the loop failing to converge. */
function trend(reviews: Review[]) {
  const seen = new Set<string>()
  return reviews.map(r => {
    const areas = new Set(blocking(r).map(area).filter(Boolean))
    const fresh = [...areas].filter(a => !seen.has(a)).length
    areas.forEach(a => seen.add(a))
    return { blocking: blocking(r).length, fresh, added: r.added, narrow: r.focus === 'narrow' }
  })
}

/** Every review, the latest first and open: its verdict, the HEAD it read and each finding, under the trend. */
function Reviews({ reviews }: { reviews: Review[] }) {
  const rounds = trend(reviews)
  return (
    <section aria-label="백엔드 리뷰" className="flex flex-col gap-2">
      <Title><span>백엔드 리뷰</span><span className="font-mono">{reviews.length}회</span></Title>
      {reviews.length > 1 && (
        <p aria-label="리뷰 추이" className="text-[12px] text-faint">
          막는 발견 <span className="font-mono">{rounds.map(t => t.blocking).join(' → ')}</span>
          {' '}· 새 영역 <span className="font-mono">{rounds.map(t => t.fresh).join(' → ')}</span>
          {rounds.some(t => t.added !== undefined) && <>
            {' '}· 추가 줄 <span className="font-mono">{rounds.map(t => t.added ?? '?').join(' → ')}</span></>}
        </p>
      )}
      {[...reviews].reverse().map((r, i) => (
        <details key={reviews.length - i} open={i === 0} className="rounded-md border border-border bg-background px-3 py-2">
          <summary className="cursor-pointer">
            <span className="font-medium">{reviews.length - i}회차</span>{' '}
            <span className={r.verdict === 'approve' ? 'text-muted-foreground' : 'text-destructive'}>{r.verdict === 'approve' ? '승인' : '거절'}</span>
            <span className="text-[12px] text-faint"> · <code>{r.head.slice(0, 8)}</code>{r.findings.length > 0 && ` · ${tally(r.findings)}`}
              {rounds[reviews.length - 1 - i].fresh > 0 && ` · 새 영역 ${rounds[reviews.length - 1 - i].fresh}`}
              {r.focus === 'narrow' && ' · 바뀐 줄 위주로 읽음'}</span>
          </summary>
          <ul className="mt-2 flex flex-col gap-2">
            {r.findings.map((f, j) => (
              <li key={j} className="flex flex-col gap-0.5">
                <span className="flex flex-wrap items-center gap-1.5"><Severity s={f.severity} /><code className="break-all text-[12px] text-muted-foreground">{f.location}</code></span>
                <span className="break-keep">{f.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      ))}
    </section>
  )
}

/** Every look at the built screens, the latest first and open. */
function Critiques({ critiques }: { critiques: Critique[] }) {
  return (
    <section aria-label="화면 비평" className="flex flex-col gap-2">
      <Title><span>화면 비평</span><span className="font-mono">{critiques.length}회</span></Title>
      {[...critiques].reverse().map((c, i) => (
        <details key={critiques.length - i} open={i === 0} className="rounded-md border border-border bg-background px-3 py-2">
          <summary className="cursor-pointer">
            <span className="font-medium">{critiques.length - i}회차</span>{' '}
            <span className={c.clean ? 'text-muted-foreground' : 'text-destructive'}>{c.clean ? '높은 발견 없음' : '높은 발견 있음'}</span>
            {/* The shots it read: a clean look is read twice, so two critiques can share one. */}
            <span className="text-[12px] text-faint"> · 촬영 {c.look}{c.findings.length > 0 && ` · ${tally(c.findings)}`}</span>
          </summary>
          <ul className="mt-2 flex flex-col gap-2">
            {c.findings.map((f, j) => (
              <li key={j} className="flex flex-col gap-0.5">
                <span className="flex flex-wrap items-center gap-1.5"><Severity s={f.severity} />
                  <span className="text-[12px] text-muted-foreground">{f.route} · {f.viewport} · {f.state}</span></span>
                <span className="break-keep">{f.what}</span>
              </li>
            ))}
          </ul>
        </details>
      ))}
    </section>
  )
}
