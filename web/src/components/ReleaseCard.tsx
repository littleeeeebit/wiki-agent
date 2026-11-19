import { useCallback, useEffect, useRef, useState } from 'react'
import { Btn, Modal } from '@/components/Modal'
import { get, json, post } from '@/lib/api'
import { cn } from '@/lib/utils'

type Bump = 'patch' | 'minor' | 'major'
/** A test build as the server last changed it. `boot` names the server's lifespan and `rev` the change within
 *  it; only `rev`s of one boot are compared. `idle` is a server that has run none; `id` names one build for its
 *  stop. */
type Rehearsal = {
  state: 'idle' | 'running' | 'done' | 'failed'; rev: number; boot: string; id?: string; started?: number; ended?: number; stopped?: boolean
  error?: string; ok?: boolean; sha?: string; rounds?: { version: string; failed?: string; refused: string[] }[]
  refusals?: Record<string, number>; repair?: { needed: string; task?: string; error?: string } | null
  repairing?: boolean; stopping?: boolean; left?: string[]
}
type Attempt = { tag: string; state: 'pending' | 'published' | 'failed'; reason?: string; repair?: { task?: string; error?: string } }
type Choice = Bump | 'first'
/** `tool/main/release_api.py`: what a press would publish, read from origin's main and GitHub. */
type Card = {
  remote?: string; sha?: string; last?: string | null; ahead?: number | null; versions?: Partial<Record<Choice, string>>
  refused?: Partial<Record<Choice, string>>; attempt?: Attempt | null; error?: string; locked: string
  read: number; rehearsal: Rehearsal   // `read`: the runs' `rev` when the card's reading began
}

// Every request is bounded, so no answer that never comes can hold the card. A card read fetches `main` and
// reads GitHub, so it gets longer.
const CARD_MS = 60_000
const STATE_MS = 10_000
const ACTION_MS = 15_000
const at = (name: string) => `/api/release/${encodeURIComponent(name)}`
const getCard = (name: string) => get(at(name), undefined, AbortSignal.timeout(CARD_MS)).then((r) => json<Card>(r, '배포 카드'))
const getRehearsal = (name: string) => get(`${at(name)}/rehearsal`, undefined, AbortSignal.timeout(STATE_MS))
  .then((r) => json<{ rehearsal: Rehearsal }>(r, '시험 빌드'))
// The commit the card showed goes with the start: a main that moved since is refused, never built unseen.
const startRehearsal = (name: string, sha?: string) => post(`${at(name)}/rehearse${sha ? `?sha=${sha}` : ''}`)
  .then((r) => json<{ rehearsal: Rehearsal }>(r, '시험 빌드'))
// The build the person saw goes with the stop: a stop that arrives after it ended never stops the next one.
// A refused stop carries the server's own build state (`rehearsal`), which the card takes like any answer.
const stopRehearsal = (name: string, id: string) => post(`${at(name)}/stop?run_id=${encodeURIComponent(id)}`)
  .then(async (r) => {
    const body = r.status === 409 ? await r.clone().json().catch(() => null) : null
    if (body?.rehearsal) throw Object.assign(new Error(body.detail || '시험 빌드 멈춤'), { rehearsal: body.rehearsal as Rehearsal })
    return json<{ rehearsal: Rehearsal }>(r, '시험 빌드 멈춤')
  })
/** `promise`, or a rejection after `ms`. The promise goes on; what it brings later is still applied by `rev`. */
const within = <T,>(promise: Promise<T>, ms: number) => Promise.race([promise, new Promise<never>((_, reject) =>
  setTimeout(() => reject(new Error('서버가 답하지 않는다 — 상태는 계속 읽는다')), ms))])
const newer = (cur: Rehearsal | null, next: Rehearsal) => (cur && cur.rev > next.rev ? cur : next)   // one boot's

const BUMPS: { bump: Bump; label: string }[] = [
  { bump: 'patch', label: '고침' }, { bump: 'minor', label: '기능 추가' }, { bump: 'major', label: '큰 변경' },
]
const STATE: Record<Attempt['state'], string> = { pending: '진행 중', published: '게시됨', failed: '실패' }
const minutes = (from: number, to: number) => `${Math.max(1, Math.round((to - from) / 60))}분`

/** The project row's `배포` button and the card it opens. */
export function ReleaseButton({ name, disabled }: { name: string; disabled: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Btn disabled={disabled} onClick={() => setOpen(true)}
        title="마지막 정식 버전, 그 뒤의 커밋, 다음 버전을 보고 시험 빌드를 돌린다">배포</Btn>
      {open && <ReleaseCard name={name} onClose={() => setOpen(false)} />}
    </>
  )
}

/** One project's release card: the last production version, how far `main` is ahead, the version each choice
 *  would publish, and the button. Publishing is locked until builds run under another Windows account than the
 *  key; the button runs the unpublished rehearsal, which builds twice with a disposable test key. */
export function ReleaseCard({ name, onClose }: { name: string; onClose: () => void }) {
  const [card, setCard] = useState<Card | null>(null)
  // The test build's state, apart from the card. Every answer that carries it, a card read, a state read, a
  // start's or a stop's, goes through `adopt`: within one server the one with the larger `rev` is kept.
  const [run, setRun] = useState<Rehearsal | null>(null)
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState<number | null>(null)   // the era of the start or stop awaiting its answer
  const [picked, setPicked] = useState<Bump | null>(null)
  // The last preview that read a commit, and the runs' `rev` when its reading began. A card that came back with
  // an error never counts as one.
  const [good, setGood] = useState<{ read: number } | null>(null)
  const [again, setAgain] = useState(0)   // bumped to read the card once more

  // Which server this card is following, and the card's own count of servers seen (`era`), which only it moves,
  // so it needs no order between servers. Every request takes a ticket: the era it was sent in, and its place in
  // the card's own count of requests sent (`seq`). Its answer counts only in that era.
  //
  // An era changes only on proof. An answer naming another server than the era's, to a request sent after the
  // era's server had answered (`at`), proves the server it reached took over from that one: only one server
  // binds the port, and each lifespan has a name of its own. That answer opens the next era as its first state
  // (a start that reached the new server shows its build and its stop), the card forgets the rest of what it
  // showed, every other answer still on its way counts no more, and the preview is read again. An answer to a
  // request sent before `at` may come from either server, so it changes nothing; the preview is read again, and
  // that request, sent after `at`, settles it.
  const era = useRef({ n: 0, boot: '', at: 0, ended: new Set<string>() })
  const seq = useRef(0)
  const ticket = useCallback(() => ({ n: era.current.n, sent: ++seq.current }), [])
  // A running build that a start or stop of the person's own answered, while the card could not yet place
  // that answer's server: Stop stays on it, since the server refuses a stop for any build but its current one.
  // The first build state the card takes from a request sent after it arrived (`at`) replaces it: whichever
  // server answers that request is the one that admitted the build, or a later one, which the build did not
  // outlive. A server whose era has ended is known to have stopped, so its answer is never kept; the followed
  // server's answers already go through `newer`, so a late one never stands over a newer build of its own.
  const [held, setHeld] = useState<{ run: Rehearsal; at: number } | null>(null)
  const adopt = useCallback((r: Rehearsal, t: { n: number; sent: number }) => {
    const e = era.current
    const settle = () => setHeld((h) => (h && t.sent > h.at ? null : h))
    if (t.n !== e.n) {
      // An ended era's request that the server this era follows answered: its build state is that server's, so
      // it still counts by `rev`. Its preview, its error and its busy flag stay with the era it was sent in.
      if (r.boot === e.boot) { setRun((cur) => newer(cur, r)); settle() }
      return false
    }
    if (!e.boot) {   // the card's first answer
      e.boot = r.boot
      e.at = seq.current
    } else if (e.boot !== r.boot) {
      setAgain((n) => n + 1)
      if (t.sent <= e.at) return false
      e.ended.add(e.boot)
      era.current = { n: e.n + 1, boot: r.boot, at: seq.current, ended: e.ended }
      settle()
      setRun(r)
      setCard(null)
      setGood(null)
      setFault('')
      setBusy(null)
      return true
    }
    setRun((cur) => newer(cur, r))
    settle()
    return true
  }, [])

  // One card read at a time, bounded; a state read never discards one. A read sent in an era that has ended
  // reads again once it settles.
  const reading = useRef(false)
  const read = useCallback(() => {
    if (reading.current) return
    reading.current = true
    const t = ticket()
    getCard(name)
      .then((c) => {
        if (!adopt(c.rehearsal, t)) return
        setCard(c)
        if (c.sha && !c.error) setGood({ read: c.read })
      })
      .catch((err) => { if (t.n === era.current.n) setFault(String(err instanceof Error ? err.message : err)) })
      .finally(() => {
        reading.current = false
        if (t.n !== era.current.n) setAgain((n) => n + 1)
      })
  }, [name, adopt, ticket])
  useEffect(() => { read() }, [read, again])

  // While the card is open its state is read every five seconds, one bounded read at a time, so a start whose
  // answer was lost, or that the server admits late, is still found, and a failed read is tried again.
  useEffect(() => {
    let out = false
    const timer = setInterval(() => {
      if (out) return
      out = true
      const t = ticket()
      getRehearsal(name).then((r) => adopt(r.rehearsal, t)).catch(() => {}).finally(() => { out = false })
    }, 5000)
    return () => clearInterval(timer)
  }, [name, adopt, ticket])

  // A build that ended after the last good preview began reading leaves the card stale: what it saw of `main`
  // may have moved. It is read again, every fifteen seconds until a good preview lands, and no start goes out
  // with the old commit meanwhile.
  const stale = !!good && !!run && run.state !== 'running' && run.rev > good.read
  useEffect(() => {
    if (!stale) return
    read()
    const timer = setInterval(read, 15000)
    return () => clearInterval(timer)
  }, [stale, read])
  // With no good preview on screen, the person reads it again by hand: a missing table would fail every time.
  const unread = (!card && !!fault) || !!card?.error

  // A refused stop's answer: the server that just answered the person's own stop has this build state, so the
  // card follows it whatever it was following, in whichever era, and drops any hold. However the answers
  // before it were ordered, the next Stop names the build that server has.
  const follow = useCallback((r: Rehearsal, t: { n: number; sent: number }) => {
    const e = era.current
    if (e.ended.has(r.boot)) return false   // a server known to have stopped: its buffered refusal says nothing now
    // Another server with no build running, for a stop sent before this era opened: it may have come before or
    // after the followed one, and it has nothing to stop, so it changes nothing; the preview is read again.
    if (r.boot !== e.boot && r.state !== 'running' && t.sent <= e.at) {
      setAgain((n) => n + 1)
      return false
    }
    setHeld((h) => (h && t.sent > h.at ? null : h))   // as any answer: only a stop sent after the hold arrived releases it
    if (r.boot === e.boot) { setRun((cur) => newer(cur, r)); return true }
    era.current = { n: e.n + 1, boot: r.boot, at: seq.current, ended: e.ended }
    setRun(r)
    setCard(null)
    setGood(null)
    setBusy(null)
    setAgain((n) => n + 1)
    return true
  }, [])

  async function act(fn: () => Promise<{ rehearsal: Rehearsal }>) {
    setFault('')
    const t = ticket()
    setBusy(t.n)
    const answer = fn().then(({ rehearsal: r }) => {
      const e = era.current   // the followed server's own answers are ordered by `rev`, never held
      if (!adopt(r, t) && r.state === 'running' && r.boot !== e.boot && !e.ended.has(r.boot)) setHeld({ run: r, at: seq.current })
    }, (err) => {
      const live = (err as { rehearsal?: Rehearsal }).rehearsal
      if (live) {   // taken however late it comes, past the action's deadline too
        if (follow(live, t)) setFault(err.message)   // a refusal the card did not take is not its error
      }
      throw err
    })
    try {
      await within(answer, ACTION_MS)
    } catch (err) {
      if (t.n === era.current.n && !(err as { rehearsal?: Rehearsal }).rehearsal) setFault(String(err instanceof Error ? err.message : err))
    } finally {
      setBusy((b) => (b === t.n ? null : b))   // an ended era's action never holds the new era's buttons
    }
  }

  // A held build and the run from the same server are ordered by `rev`, like any two answers of one server. From
  // two servers the card cannot order them, so a build running on the followed server is the one shown; either
  // way a Stop the live server refuses brings its own state back.
  const shown = !held ? run : run?.boot === held.run.boot ? newer(run, held.run) : run?.state === 'running' ? run : held.run
  const first = card?.versions?.first
  const bump = picked ?? BUMPS.find((b) => card?.versions?.[b.bump])?.bump ?? 'patch'
  const next = first ?? card?.versions?.[bump]
  return (
    <Modal wide title={`${name} 배포`} onClose={onClose} foot={(
      <>
        <Btn onClick={onClose}>닫기</Btn>
        {unread && <Btn onClick={() => { setFault(''); read() }} title="origin 의 main 과 GitHub 릴리스를 다시 읽는다">다시 읽기</Btn>}
        {shown?.state === 'running'
          // Only a pending start or stop holds Stop back: a build this card shows as repairing or stopping may be
          // another server's, and a stop the live server refuses brings back the build it has.
          ? <Btn tone="danger" disabled={busy !== null || !shown.id} onClick={() => void act(() => stopRehearsal(name, shown.id ?? ''))}
              title={shown.repairing ?'수정 작업을 이미 만들기 시작해서 멈출 수 없다' : undefined}>시험 빌드 멈춤</Btn>
          : <Btn disabled={busy !== null || stale || !card?.sha || !!card.error} onClick={() => void act(() => startRehearsal(name, card?.sha))}
              title="main 을 두 번 빌드하고 일회용 시험 키로 서명해 검사한다. 아무것도 올리지 않는다">시험 빌드</Btn>}
        <Btn tone="primary" disabled title={card?.locked}>{next ? `v${next} 게시` : '게시'}</Btn>
      </>
    )}>
      {!card && !fault && <p className="text-muted-foreground">origin 의 main 과 GitHub 릴리스를 읽는 중…</p>}
      {fault && <p role="alert" className="mb-3 whitespace-pre-wrap text-destructive">{fault}</p>}
      {card?.error && <p role="alert" className="mb-3 whitespace-pre-wrap text-destructive">{card.error}</p>}
      {card && !card.error && (
        <dl className="mb-3 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5">
          <dt className="text-faint">마지막 정식 버전</dt>
          <dd className="font-mono">{card.last ?? '아직 없음'}</dd>
          <dt className="text-faint">그 뒤 main 의 커밋</dt>
          <dd className="font-mono">{card.ahead ?? '모름'}</dd>
          <dt className="text-faint">다음 버전</dt>
          <dd>
            {card.last === null ? (
              first
                ? <span><span className="font-mono">v{first}</span> <span className="text-muted-foreground">— 첫 정식 릴리스라 고를 것이 없다</span></span>
                : <span className="text-destructive">{card.refused?.first}</span>
            ) : (
              <span role="radiogroup" aria-label="올릴 버전" className="flex flex-wrap gap-1.5">
                {BUMPS.map(({ bump: b, label }) => (
                  <button key={b} type="button" role="radio" aria-checked={bump === b} disabled={!card.versions?.[b]}
                    onClick={() => setPicked(b)} title={card.refused?.[b]}
                    className={cn('h-7 rounded-md border px-2.5 text-[12.5px] disabled:opacity-40',
                      bump === b ? 'border-primary bg-primary/10' : 'border-border hover:bg-secondary')}>
                    {label} <span className="font-mono">{card.versions?.[b] ? `v${card.versions[b]}` : '올릴 수 없음'}</span>
                  </button>
                ))}
              </span>
            )}
          </dd>
        </dl>
      )}
      <p role="status" className="mb-3 rounded-md border border-wait/30 bg-wait/10 px-3 py-2 text-[12.5px]">{card?.locked}</p>
      {card?.attempt && (
        <p className="mb-2 text-[12.5px] text-muted-foreground">
          지난 배포 <span className="font-mono">{card.attempt.tag}</span> · {STATE[card.attempt.state]}
          {card.attempt.reason ? ` · ${card.attempt.reason}` : ''}
          {card.attempt.repair?.task ? ` · 수정 작업 ${card.attempt.repair.task}` : ''}
        </p>
      )}
      {shown && <RehearsalLine run={shown} />}
    </Modal>
  )
}

/** What the test build could not remove, however it ended: a checkout or a test key the person deletes. */
function Leftover({ left }: { left?: string[] }) {
  if (!left?.length) return null
  return <p className="mt-1 whitespace-pre-wrap text-wait">지우지 못한 빌드 폴더·시험 키 — 직접 지워라: {left.join('; ')}</p>
}

function RehearsalLine({ run }: { run: Rehearsal }) {
  if (run.state === 'idle') return null
  const started = run.started ?? 0
  if (run.state === 'running') {
    const since = new Date(started * 1000).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })
    return (
      <p className="animate-pulse text-[12.5px]">
        {run.repairing ? '빌드가 실패해 수정 작업을 만드는 중…' : run.stopping ? '시험 빌드를 멈추는 중… 빌드 폴더와 시험 키를 지운다'
          : `시험 빌드 중… ${since} 에 시작 · 수십 분 걸릴 수 있다`}
      </p>
    )
  }
  if (run.state === 'failed') {
    // A stop ends the build with the machine's error; the person pressed it, so the line says only that,
    // and what the stop could not clean up.
    if (run.stopped) {
      return (
        <div className="text-[12.5px]">
          <p className="text-muted-foreground">시험 빌드를 멈췄다 — 수정 작업은 만들지 않았다</p>
          <Leftover left={run.left} />
        </div>
      )
    }
    const [head, ...log] = (run.error ?? '').split('\n')
    return (
      <div className="text-[12.5px] text-destructive">
        <p className="whitespace-pre-wrap">시험 빌드가 끝나지 못했다 — {head}</p>
        {log.length > 0 && (
          <details className="mt-1"><summary className="cursor-pointer text-muted-foreground">로그 {log.length}줄</summary>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">{log.join('\n')}</pre>
          </details>
        )}
        <Leftover left={run.left} />
      </div>
    )
  }
  const failed = run.rounds?.find((r) => r.failed || r.refused.length)
  return (
    <div className="text-[12.5px]">
      <p className={run.ok ? 'text-primary' : 'text-destructive'}>
        {run.stopped ? '시험 빌드를 멈췄다' : run.ok
          ? `시험 빌드 통과 — ${run.rounds?.map((r) => `v${r.version}`).join(', ')} 를 빌드·서명·검사했고, 다른 키와 바뀐 앱 ID 는 거부됐다`
          : '시험 빌드 실패'}
        {run.ended ? ` · ${minutes(started, run.ended)}` : ''}
      </p>
      {failed && <p className="mt-1 whitespace-pre-wrap font-mono text-[11.5px] text-muted-foreground">v{failed.version}: {failed.failed ?? failed.refused.join('; ')}</p>}
      {run.repair?.task && <p className="mt-1">수정 작업 <span className="font-mono">{run.repair.task}</span> 을 시작했다 — 작업 목록에서 본다</p>}
      {run.repair?.error && <p className="mt-1 text-muted-foreground">수정 작업을 시작하지 않았다 — {run.repair.error}</p>}
      <Leftover left={run.left} />
    </div>
  )
}
