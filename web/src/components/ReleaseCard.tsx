import { useCallback, useEffect, useState } from 'react'
import { Btn, Modal } from '@/components/Modal'
import { get, json, post } from '@/lib/api'
import { cn } from '@/lib/utils'

type Bump = 'patch' | 'minor' | 'major'
type Rehearsal = {
  state: 'running' | 'done' | 'failed'; started: number; ended?: number; stopped?: boolean; error?: string
  ok?: boolean; rounds?: { version: string; failed?: string; refused: string[] }[]
  refusals?: Record<string, number>; repair?: { needed: string; task?: string; error?: string } | null
}
type Attempt = { tag: string; state: 'pending' | 'published' | 'failed'; reason?: string; repair?: { task?: string; error?: string } }
/** `tool/main/release_api.py`: what a press would publish, read from origin's main and GitHub. */
type Card = {
  remote?: string; last?: string | null; ahead?: number | null; versions?: Partial<Record<Bump | 'first', string>>
  attempt?: Attempt | null; error?: string; locked: string; rehearsal: Rehearsal | null
}

const at = (name: string) => `/api/release/${encodeURIComponent(name)}`
const getCard = (name: string) => get(at(name)).then((r) => json<Card>(r, '배포 카드'))
const startRehearsal = (name: string) => post(`${at(name)}/rehearse`).then((r) => json<{ rehearsal: Rehearsal }>(r, '시험 빌드'))
const stopRehearsal = (name: string) => post(`${at(name)}/stop`).then((r) => json<{ rehearsal: Rehearsal }>(r, '시험 빌드 멈춤'))

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
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState(false)
  const [bump, setBump] = useState<Bump>('patch')

  const read = useCallback(() => getCard(name).then(setCard).catch((err) => setFault(String(err instanceof Error ? err.message : err))), [name])
  useEffect(() => { void read() }, [read])
  // While the rehearsal runs, its state is read again; it takes minutes, so every five seconds is enough.
  const running = card?.rehearsal?.state === 'running'
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => void read(), 5000)
    return () => clearInterval(timer)
  }, [running, read])

  async function act(fn: () => Promise<unknown>) {
    setFault('')
    setBusy(true)
    try {
      await fn()
      await read()
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
    } finally {
      setBusy(false)
    }
  }

  const first = card?.versions?.first
  const next = first ?? card?.versions?.[bump]
  return (
    <Modal wide title={`${name} 배포`} onClose={onClose} foot={(
      <>
        <Btn onClick={onClose}>닫기</Btn>
        {running
          ? <Btn tone="danger" disabled={busy} onClick={() => void act(() => stopRehearsal(name))}>시험 빌드 멈춤</Btn>
          : <Btn disabled={busy || !card || !!card.error} onClick={() => void act(() => startRehearsal(name))}
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
            {first ? (
              <span><span className="font-mono">v{first}</span> <span className="text-muted-foreground">— 첫 정식 릴리스라 고를 것이 없다</span></span>
            ) : (
              <span role="radiogroup" aria-label="올릴 버전" className="flex flex-wrap gap-1.5">
                {BUMPS.map(({ bump: b, label }) => (
                  <button key={b} type="button" role="radio" aria-checked={bump === b} onClick={() => setBump(b)}
                    className={cn('h-7 rounded-md border px-2.5 text-[12.5px]',
                      bump === b ? 'border-primary bg-primary/10' : 'border-border hover:bg-secondary')}>
                    {label} <span className="font-mono">v{card.versions?.[b]}</span>
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
      {card?.rehearsal && <RehearsalLine run={card.rehearsal} />}
    </Modal>
  )
}

function RehearsalLine({ run }: { run: Rehearsal }) {
  if (run.state === 'running') {
    const since = new Date(run.started * 1000).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })
    return <p className="animate-pulse text-[12.5px]">시험 빌드 중… {since} 에 시작 · 수십 분 걸릴 수 있다</p>
  }
  if (run.state === 'failed') {
    // A stop ends the build with the machine's error; the person pressed it, so that is all the line says.
    if (run.stopped) return <p className="text-[12.5px] text-muted-foreground">시험 빌드를 멈췄다 — 수정 작업은 만들지 않았다</p>
    const [head, ...log] = (run.error ?? '').split('\n')
    return (
      <div className="text-[12.5px] text-destructive">
        <p className="whitespace-pre-wrap">시험 빌드가 끝나지 못했다 — {head}</p>
        {log.length > 0 && (
          <details className="mt-1"><summary className="cursor-pointer text-muted-foreground">로그 {log.length}줄</summary>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">{log.join('\n')}</pre>
          </details>
        )}
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
        {run.ended ? ` · ${minutes(run.started, run.ended)}` : ''}
      </p>
      {failed && <p className="mt-1 whitespace-pre-wrap font-mono text-[11.5px] text-muted-foreground">v{failed.version}: {failed.failed ?? failed.refused.join('; ')}</p>}
      {run.repair?.task && <p className="mt-1">수정 작업 <span className="font-mono">{run.repair.task}</span> 을 시작했다 — 작업 목록에서 본다</p>}
      {run.repair?.error && <p className="mt-1 text-muted-foreground">수정 작업을 시작하지 않았다 — {run.repair.error}</p>}
    </div>
  )
}
