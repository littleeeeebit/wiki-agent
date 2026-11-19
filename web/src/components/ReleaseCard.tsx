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
type Attempt = { id: string; tag: string; state: 'pending' | 'published' | 'failed'; reason?: string; last_error?: string; repair?: { task?: string; error?: string } }
type Choice = Bump | 'first'
/** `tool/main/release_api.py`: what a press would publish, read from origin's main and GitHub. */
type Card = {
  remote?: string; sha?: string; last?: string | null; ahead?: number | null; versions?: Partial<Record<Choice, string>>
  kinds?: ('android' | 'desktop')[]   // what every press ships, read from the pinned commit's `[release]`
  refused?: Partial<Record<Choice, string>>; attempt?: Attempt | null; error?: string
  read: number; rehearsal: Rehearsal   // `read`: the runs' `rev` when the card's reading began
  press: Press; key?: { keystore: string; credential: string } | null
  settled: number   // how many presses and sweeps had ended when the card's reading began
}
/** This server's last press: running, or how it ended — the attempt's tag and outcome, or why none was reserved. */
type Press = {
  state: 'none' | 'running' | 'ended'; version?: string; rev: number; boot: string; started?: number; tag?: string
  outcome?: Attempt['state']; error?: string
}
/** What the publish button's first press confirmed: the second press sends exactly this, and only while the card still shows it. */
type Armed = { version: string; bump: Bump | null; remote: string; sha: string }

// Every request is bounded, so no answer that never comes can hold the card. A card read fetches `main` and
// reads GitHub, so it gets longer.
const CARD_MS = 60_000
const STATE_MS = 10_000
const ACTION_MS = 15_000
const at = (name: string) => `/api/release/${encodeURIComponent(name)}`
const getCard = (name: string) => get(at(name), undefined, AbortSignal.timeout(CARD_MS)).then((r) => json<Card>(r, '배포 카드'))
const getRehearsal = (name: string) => get(`${at(name)}/rehearsal`, undefined, AbortSignal.timeout(STATE_MS))
  .then((r) => json<{ rehearsal: Rehearsal; press: Press; settled: number }>(r, '시험 빌드'))
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
// What the person confirmed goes with the press: the version, its choice (none before the first release), and
// the repository and commit the card read. Any of them that moved since is refused before anything is reserved.
const pressPublish = (name: string, a: Armed) => post(`${at(name)}/publish?${new URLSearchParams({
  version: a.version, remote: a.remote, sha: a.sha, ...(a.bump ? { bump: a.bump } : {}) })}`)
  .then((r) => json<{ press: Press }>(r, '게시'))
// A pending attempt carried on, as a restarted server would: nothing new is built.
// The attempt the card showed goes with it: one that ended meanwhile, or another, is refused.
const resumePublish = (name: string, remote: string, attempt: string) =>
  post(`${at(name)}/resume?${new URLSearchParams({ remote, attempt })}`).then((r) => json<{ press: Press }>(r, '게시 이어 가기'))
/** `promise`, or a rejection after `ms`. The promise goes on; what it brings later is still applied by `rev`. */
const within = <T,>(promise: Promise<T>, ms: number) => Promise.race([promise, new Promise<never>((_, reject) =>
  setTimeout(() => reject(new Error('서버가 답하지 않는다 — 상태는 계속 읽는다')), ms))])
type Hold = { run: Rehearsal; at: number }   // a build held by the card, and when it arrived (`seq`)
const newer = (cur: Rehearsal | null, next: Rehearsal) => (cur && cur.rev > next.rev ? cur : next)   // one boot's

const BUMPS: { bump: Bump; label: string }[] = [
  { bump: 'patch', label: '고침' }, { bump: 'minor', label: '기능 추가' }, { bump: 'major', label: '큰 변경' },
]
const STATE: Record<Attempt['state'], string> = { pending: '진행 중', published: '게시됨', failed: '실패' }
// The Windows installer is not code-signed yet, so Windows warns before running it.
const SHIPS = { android: 'Android APK', desktop: 'Windows 설치 파일 (코드 서명 없음)' }
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
 *  would publish, and its two buttons: the publish button, pressed twice, publishes the version shown; the test
 *  build runs the unpublished rehearsal, which builds twice with a disposable test key. */
export function ReleaseCard({ name, onClose }: { name: string; onClose: () => void }) {
  const [card, setCard] = useState<Card | null>(null)
  // The test build's state, apart from the card. Every answer that carries it, a card read, a state read, a
  // start's or a stop's, goes through `adopt`: within one server the one with the larger `rev` is kept.
  const [run, setRun] = useState<Rehearsal | null>(null)
  // The largest `rev` taken from each server, kept as each answer is taken rather than when the card renders,
  // so a refusal is compared with every answer before it.
  const top = useRef(new Map<string, number>())
  const take = useCallback((r: Rehearsal) => {
    top.current.set(r.boot, Math.max(top.current.get(r.boot) ?? -1, r.rev))
    setRun((cur) => newer(cur, r))
  }, [])
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState<number | null>(null)   // the era of the start or stop awaiting its answer
  const [picked, setPicked] = useState<Bump | null>(null)
  const [armed, setArmed] = useState<Armed | null>(null)   // the publish button's first press; the second publishes
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
  // The followed server's last press: only that server's answers count, ordered by `rev` like its runs, its
  // `none` before any press included. A new era starts with none (`dropPress`). A press seen ending reads the
  // card again, so its attempt comes from the ledger.
  const [press, setPress] = useState<Press | null>(null)
  const pressNow = useRef<Press | null>(null)
  const takePress = useCallback((p: Press | undefined, boot: string) => {
    if (!p || boot !== era.current.boot) return
    const cur = pressNow.current
    if (cur && cur.rev > p.rev) return
    if (p.state === 'ended' && (cur?.state !== 'ended' || cur.rev !== p.rev)) setAgain((n) => n + 1)
    pressNow.current = p
    setPress(p)
  }, [])
  const settledRead = useRef<number | null>(null)
  const dropPress = useCallback(() => {
    pressNow.current = null
    setPress(null)
  }, [])
  // A running build that a start or stop of the person's own answered, while the card could not yet place
  // that answer's server: Stop stays on it, since the server refuses a stop for any build but its current one.
  // The first build state the card takes from a request sent after it arrived (`at`) replaces it: whichever
  // server answers that request is the one that admitted the build, or a later one, which the build did not
  // outlive. A server whose era has ended is known to have stopped, so its answer is never kept; the followed
  // server's answers already go through `newer`, so a late one never stands over a newer build of its own.
  //
  // One hold per server, since nothing orders two servers' builds: holding one never discards another's. Within a
  // server the larger `rev` is kept, and `hold` says whether the build was taken. `holding` is the holds as each
  // answer leaves them; `held` is what renders. A server proven to have stopped loses its hold (`end`).
  const holding = useRef(new Map<string, Hold>())
  const [held, setHeld] = useState<Hold[]>([])
  const sync = useCallback(() => setHeld([...holding.current.values()]), [])
  const hold = useCallback((r: Rehearsal) => {
    const h = holding.current.get(r.boot)
    if ((h && h.run.rev >= r.rev) || (top.current.get(r.boot) ?? -1) >= r.rev) return false
    holding.current.set(r.boot, { run: r, at: seq.current })
    sync()
    return true
  }, [sync])
  // Another server's finished build that cannot change the followed server still orders that server's own
  // builds: its held build with a smaller `rev` has ended, and none older is held after it.
  const mark = useCallback((r: Rehearsal) => {
    if (r.state === 'running') return
    top.current.set(r.boot, Math.max(top.current.get(r.boot) ?? -1, r.rev))
    const h = holding.current.get(r.boot)
    if (h && h.run.rev < r.rev && holding.current.delete(r.boot)) sync()
  }, [sync])
  const release = useCallback((sent: number) => {   // an answer releases the holds that arrived before its request was sent
    let gone = false
    for (const [boot, h] of holding.current) if (sent > h.at) gone = holding.current.delete(boot) || gone
    if (gone) sync()
  }, [sync])
  const end = useCallback((boot: string) => {
    era.current.ended.add(boot)
    if (holding.current.delete(boot)) sync()
  }, [sync])
  const adopt = useCallback((r: Rehearsal, t: { n: number; sent: number }) => {
    const e = era.current
    const settle = () => release(t.sent)
    if (t.n !== e.n) {
      // An ended era's request that the server this era follows answered: its build state is that server's, so
      // it still counts by `rev`. Its preview, its error and its busy flag stay with the era it was sent in.
      if (r.boot === e.boot) { take(r); settle() }
      return false
    }
    if (!e.boot) {   // the card's first answer
      e.boot = r.boot
      e.at = seq.current
    } else if (e.boot !== r.boot) {
      setAgain((n) => n + 1)
      if (t.sent <= e.at) {
        mark(r)
        return false
      }
      end(e.boot)
      era.current = { n: e.n + 1, boot: r.boot, at: seq.current, ended: e.ended }
      settle()
      setRun(null)
      dropPress()
      take(r)
      setCard(null)
      setGood(null)
      setFault('')
      setBusy(null)
      return true
    }
    take(r)
    settle()
    return true
  }, [take, release, end, mark, dropPress])

  // One card read at a time, bounded; a state read never discards one. A read sent in an era that has ended
  // reads again once it settles.
  const reading = useRef(false)
  const read = useCallback(() => {
    if (reading.current) return
    reading.current = true
    const t = ticket()
    getCard(name)
      .then((c) => {
        const taken = adopt(c.rehearsal, t)
        takePress(c.press, c.rehearsal.boot)
        if (!taken) return
        settledRead.current = c.settled
        setCard(c)
        if (c.sha && !c.error) setGood({ read: c.read })
      })
      .catch((err) => { if (t.n === era.current.n) setFault(String(err instanceof Error ? err.message : err)) })
      .finally(() => {
        reading.current = false
        if (t.n !== era.current.n) setAgain((n) => n + 1)
      })
  }, [name, adopt, ticket, takePress])
  useEffect(() => { read() }, [read, again])

  // While the card is open its state is read every five seconds, one bounded read at a time, so a start whose
  // answer was lost, or that the server admits late, is still found, and a failed read is tried again.
  useEffect(() => {
    let out = false
    const timer = setInterval(() => {
      if (out) return
      out = true
      const t = ticket()
      getRehearsal(name).then((r) => {
        adopt(r.rehearsal, t)
        takePress(r.press, r.rehearsal.boot)
        // A press or a sweep that ended after the shown card began reading changed the ledger and maybe GitHub.
        // Only a card read that lands moves `settledRead`, so a failed read is asked for again on the next poll.
        if (r.rehearsal.boot === era.current.boot && settledRead.current !== null && r.settled > settledRead.current) {
          setAgain((n) => n + 1)
        }
      }).catch(() => {}).finally(() => { out = false })
    }, 5000)
    return () => clearInterval(timer)
  }, [name, adopt, ticket, takePress])

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
    // Another server with no build running, for a stop issued before the followed server's answer arrived: either
    // server may be the later one (the order requests are issued in is not the order they reach a server), so it
    // never takes a build off the screen; it changes nothing and the preview is read again. A stop issued after
    // that answer arrived can reach only that server or a later one, so its refusal is followed.
    if (r.boot !== e.boot && r.state !== 'running' && t.sent <= e.at) {
      mark(r)
      setAgain((n) => n + 1)
      return false
    }
    // Another server's running build, for a stop issued before that answer arrived: no proof either way, so it is
    // held like a start's answer. It never takes the followed server's running build off the screen; a Stop
    // issued now settles which one is live.
    if (r.boot !== e.boot && t.sent <= e.at) {
      setAgain((n) => n + 1)
      return hold(r)
    }
    release(t.sent)   // as any answer: only a stop sent after a hold arrived releases it
    if (r.boot === e.boot) {   // ordered by `rev`, held builds too; an older snapshot is not taken, and its refusal is not shown
      const older = Math.max(top.current.get(r.boot) ?? -1, holding.current.get(r.boot)?.run.rev ?? -1) > r.rev
      take(r)
      return !older
    }
    if (t.sent > e.at) end(e.boot)   // a proven transition, as in `adopt`: the followed server has stopped
    era.current = { n: e.n + 1, boot: r.boot, at: seq.current, ended: e.ended }
    setRun(null)
    dropPress()
    take(r)
    setCard(null)
    setGood(null)
    setBusy(null)
    setAgain((n) => n + 1)
    return true
  }, [take, release, end, hold, mark, dropPress])

  async function act(fn: () => Promise<{ rehearsal: Rehearsal }>) {
    setFault('')
    const t = ticket()
    setBusy(t.n)
    const answer = fn().then(({ rehearsal: r }) => {
      const e = era.current   // the followed server's own answers are ordered by `rev`, never held
      if (!adopt(r, t) && r.state === 'running' && r.boot !== e.boot && !e.ended.has(r.boot)) hold(r)
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
  // two servers the card cannot order them, so a build running on the followed server is the one shown, and
  // otherwise the build held last; either way a Stop the live server refuses brings its own state back.
  const mine = held.find((h) => h.run.boot === run?.boot)?.run
  const own = mine ? newer(run, mine) : run
  const other = held.filter((h) => h.run.boot !== run?.boot).reduce<Hold | null>((a, h) => (a && a.at > h.at ? a : h), null)
  const shown = own?.state === 'running' || !other ? own : other.run
  // A press that runs, or an attempt the ledger holds pending, is read again every fifteen seconds until it ends.
  const publishing = press?.state === 'running' || card?.attempt?.state === 'pending'
  useEffect(() => {
    if (!publishing) return
    const timer = setInterval(read, 15000)
    return () => clearInterval(timer)
  }, [publishing, read])
  const first = card?.versions?.first
  const bump = picked ?? BUMPS.find((b) => card?.versions?.[b.bump])?.bump ?? 'patch'
  const next = first ?? card?.versions?.[bump]
  // One project runs one press or one test build at a time, and a press never goes out with a stale preview.
  const blocked = busy !== null || stale || !card?.sha || !card.remote || !!card.error || !next || publishing
    || shown?.state === 'running'
  const choice = card?.last === null ? null : bump
  // The confirmation holds only while the card still shows what it confirmed; any change asks for a new first press.
  const live = !!armed && !!card && armed.version === next && armed.bump === choice && armed.sha === card.sha
    && armed.remote === card.remote
  // An attempt the ledger holds pending with no press of this server on it: GitHub stopped answering, or the
  // server stopped it. The person carries it on.
  const stuck = card?.attempt?.state === 'pending' && press?.state !== 'running'
  async function send(call: () => Promise<{ press: Press }>) {
    setArmed(null)
    setFault('')
    const n = era.current.n
    setBusy(n)
    try {
      const { press: p } = await within(call(), ACTION_MS)
      takePress(p, p.boot)
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
    } finally {
      setBusy((b) => (b === n ? null : b))
      setAgain((k) => k + 1)   // the attempt it reserved is read from the ledger
    }
  }
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
          : <Btn disabled={busy !== null || stale || !card?.sha || !!card.error || publishing} onClick={() => void act(() => startRehearsal(name, card?.sha))}
              title="main 을 두 번 빌드하고 일회용 시험 키로 서명해 검사한다. 아무것도 올리지 않는다">시험 빌드</Btn>}
        {stuck
          ? <Btn tone="primary" disabled={busy !== null || shown?.state === 'running'} onClick={() => card?.remote && card.attempt && void send(() => resumePublish(name, card.remote ?? '', card.attempt?.id ?? ''))}
              title="멈춘 게시를 남은 것부터 이어 간다. 새로 빌드하지 않는다">게시 이어 가기</Btn>
          : <Btn tone={live ? 'danger' : 'primary'} disabled={blocked}
              onClick={() => (live && armed ? void send(() => pressPublish(name, armed))
                : next && card?.sha && card.remote && setArmed({ version: next, bump: choice, sha: card.sha, remote: card.remote }))}
              title="main 을 빌드하고 정식 키로 서명·검사한 뒤 GitHub 릴리스로 올린다">{live ? `v${next} 정말 게시` : next ? `v${next} 게시` : '게시'}</Btn>}
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
          <dt className="text-faint">올리는 것</dt>
          <dd>{(card.kinds ?? []).map((k) => SHIPS[k]).join(' · ') || '모름'}</dd>
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
                    onClick={() => { setPicked(b); setArmed(null) }} title={card.refused?.[b]}
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
      {live && !blocked && (
        <p role="status" className="mb-3 rounded-md border border-wait/30 bg-wait/10 px-3 py-2 text-[12.5px]">
          v{next} 를 빌드·서명해 GitHub 릴리스로 올린다 — 올린 버전은 되돌리지 않는다. 한 번 더 누르면 시작한다
          {card?.last === null && card.kinds?.includes('android') ? '. 첫 정식 릴리스라 정식 서명 키를 이 PC 에 새로 만든다' : ''}
        </p>
      )}
      {press?.state === 'running' && (
        <p className="mb-2 animate-pulse text-[12.5px]">v{press.version} 게시 중… 빌드·서명·검사한 뒤 GitHub 에 올린다 · 수십 분 걸릴 수 있다</p>
      )}
      {press?.error && <p className="mb-2 whitespace-pre-wrap text-[12.5px] text-destructive">v{press.version} 를 게시하지 못했다 — {press.error}</p>}
      {stuck && (
        <p className="mb-2 whitespace-pre-wrap text-[12.5px] text-wait">
          {card?.attempt?.tag} 게시가 GitHub 에 다 올라가지 못하고 멈췄다{card?.attempt?.last_error ? ` — ${card.attempt.last_error}` : ''}.
          이어 가면 남은 것부터 올린다
        </p>
      )}
      {card?.attempt && (
        <p className="mb-2 text-[12.5px] text-muted-foreground">
          지난 배포 <span className="font-mono">{card.attempt.tag}</span> · {STATE[card.attempt.state]}
          {card.attempt.reason ? ` · ${card.attempt.reason}` : ''}
          {card.attempt.repair?.task ? ` · 수정 작업 ${card.attempt.repair.task} — 작업 목록에서 그 PR 을 본다` : ''}
          {card.attempt.repair?.error ? ` · 수정 작업을 시작하지 않았다: ${card.attempt.repair.error}` : ''}
        </p>
      )}
      {card?.key && (
        <p className="mb-2 text-[12.5px] text-muted-foreground">
          서명 키 백업: <span className="font-mono">{card.key.keystore}</span> 와 자격 증명 관리자의 '{card.key.credential}' 를 함께
          백업한다. 둘 중 하나라도 잃으면 설치된 앱을 업데이트할 수 없다
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
