import { useCallback, useEffect, useRef, useState } from 'react'
import { Btn, Modal } from '@/components/Modal'
import { get, json, post } from '@/lib/api'
import { cn } from '@/lib/utils'

type Bump = 'patch' | 'minor' | 'major'
/** A test build as the server last changed it. Within one server process (`boot`), `rev` grows with every
 *  change, so the newest wins whatever order the answers arrive in; a snapshot from another process, a restarted
 *  server's, always replaces it. `idle` is a server that has run none; `id` names one build for its stop. */
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
const stopRehearsal = (name: string, id: string) => post(`${at(name)}/stop?run_id=${encodeURIComponent(id)}`)
  .then((r) => json<{ rehearsal: Rehearsal }>(r, '시험 빌드 멈춤'))
/** `promise`, or a rejection after `ms`. The promise goes on; what it brings later is still applied by `rev`. */
const within = <T,>(promise: Promise<T>, ms: number) => Promise.race([promise, new Promise<never>((_, reject) =>
  setTimeout(() => reject(new Error('서버가 답하지 않는다 — 상태는 계속 읽는다')), ms))])
const newer = (cur: Rehearsal | null, next: Rehearsal) => (cur && cur.boot === next.boot && cur.rev > next.rev ? cur : next)

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
  // start's or a stop's, goes through `adopt`: the one with the larger `rev` is kept, whatever arrives when.
  const [run, setRun] = useState<Rehearsal | null>(null)
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState(false)
  const [picked, setPicked] = useState<Bump | null>(null)
  // The server process this card follows, and the ones it has seen replaced. Once an answer from a new process
  // arrives, every answer still on its way from an older one is dropped, its preview with it.
  const boots = useRef({ now: '', retired: new Set<string>() })
  const adopt = useCallback((r: Rehearsal) => {
    const b = boots.current
    if (b.retired.has(r.boot)) return false
    if (b.now && b.now !== r.boot) b.retired.add(b.now)
    b.now = r.boot
    setRun((cur) => newer(cur, r))
    return true
  }, [])

  // One card read at a time, bounded; a state read never discards one.
  const reading = useRef(false)
  // The last preview that read a commit: which server read it, and the runs' `rev` when its reading began.
  // A card that came back with an error never counts as one.
  const [good, setGood] = useState<{ boot: string; read: number } | null>(null)
  const read = useCallback(() => {
    if (reading.current) return
    reading.current = true
    getCard(name)
      .then((c) => {
        if (!adopt(c.rehearsal)) return
        setCard(c)
        if (c.sha && !c.error) setGood({ boot: c.rehearsal.boot, read: c.read })
      })
      .catch((err) => setFault(String(err instanceof Error ? err.message : err)))
      .finally(() => { reading.current = false })
  }, [name, adopt])
  useEffect(() => { read() }, [read])

  // While the card is open its state is read every five seconds, one bounded read at a time, so a start whose
  // answer was lost, or that the server admits late, is still found, and a failed read is tried again.
  useEffect(() => {
    let out = false
    const timer = setInterval(() => {
      if (out) return
      out = true
      getRehearsal(name).then((r) => adopt(r.rehearsal)).catch(() => {}).finally(() => { out = false })
    }, 5000)
    return () => clearInterval(timer)
  }, [name, adopt])

  // A build that ended after the last good preview began reading, or a server other than the one that read it,
  // leaves the card stale: what it saw of `main` may have moved. It is read again, every fifteen seconds until a
  // good preview lands, and no start goes out with the old commit meanwhile.
  const stale = !!good && !!run && run.state !== 'running' && (run.boot !== good.boot || run.rev > good.read)
  useEffect(() => {
    if (!stale) return
    read()
    const timer = setInterval(read, 15000)
    return () => clearInterval(timer)
  }, [stale, read])
  // With no good preview on screen, the person reads it again by hand: a missing table would fail every time.
  const unread = (!card && !!fault) || !!card?.error

  async function act(fn: () => Promise<{ rehearsal: Rehearsal }>) {
    setFault('')
    setBusy(true)
    try {
      await within(fn().then((r) => adopt(r.rehearsal)), ACTION_MS)
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
    } finally {
      setBusy(false)
    }
  }

  const first = card?.versions?.first
  const bump = picked ?? BUMPS.find((b) => card?.versions?.[b.bump])?.bump ?? 'patch'
  const next = first ?? card?.versions?.[bump]
  return (
    <Modal wide title={`${name} 배포`} onClose={onClose} foot={(
      <>
        <Btn onClick={onClose}>닫기</Btn>
        {unread && <Btn onClick={() => { setFault(''); read() }} title="origin 의 main 과 GitHub 릴리스를 다시 읽는다">다시 읽기</Btn>}
        {run?.state === 'running'
          ? <Btn tone="danger" disabled={busy || run.repairing || run.stopping || !run.id} onClick={() => void act(() => stopRehearsal(name, run.id ?? ''))}
              title={run?.repairing ? '수정 작업을 이미 만들기 시작해서 멈출 수 없다' : undefined}>시험 빌드 멈춤</Btn>
          : <Btn disabled={busy || stale || !card?.sha || !!card.error} onClick={() => void act(() => startRehearsal(name, card?.sha))}
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
      {run && <RehearsalLine run={run} />}
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
