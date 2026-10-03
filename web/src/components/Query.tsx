import { useCallback, useEffect, useRef, useState } from 'react'
import { Eraser } from 'lucide-react'
import { Blocks } from '@/components/Blocks'
import { Composer } from '@/components/Composer'
import { Btn, ClearAsk } from '@/components/Modal'
import { Peek } from '@/components/Peek'
import { PlanStart } from '@/components/Plan'
import { Stream } from '@/components/Stream'
import { Picker, Toolbar } from '@/components/Toolbar'
import * as api from '@/lib/api'
import type { Block, Channel, Ev, Kind, Options, Peek as PeekData, RunSummary, Spec, Tokens, Turn, Verification } from '@/lib/api'
import { cn } from '@/lib/utils'

export type Msg = {
  role: 'user' | 'assistant' | 'result'
  text: string
  blocks?: Block[]
  tools: string[]
  hits?: string[]
  source?: string
  ms?: number
  cost?: number
  tokens?: Tokens
  model?: string
  sessionId?: string
  marked?: Kind
  error?: string
  verification?: Verification
  pending?: boolean
  simpleText?: string
  simpleError?: string
  simplePending?: boolean
  simpleMs?: number
  simpleCost?: number
  /** The run behind this answer, where it is, and the last event seen. */
  runId?: string
  stage?: string
  seq?: number
  cancelled?: boolean
}

/** One event of a run onto the answer it is building. */
function onto(m: Msg, ev: Ev): Msg {
  const at = { ...m, runId: ev.run_id ?? m.runId, seq: ev.seq ?? m.seq }
  switch (ev.kind) {
    case 'hits': return { ...at, hits: ev.pages ?? [] }
    case 'delta': return { ...at, text: at.text + ev.text }
    case 'tool': return { ...at, tools: [...at.tools, ev.text] }
    case 'step': return { ...at, stage: ev.stage }
    // The final body is the server's copy. A missed chunk is corrected right here.
    case 'done': return { ...at, text: ev.text || at.text, ms: ev.ms, cost: ev.cost_usd, tokens: ev.tokens,
      model: ev.model, sessionId: ev.session_id, verification: ev.verification, pending: false }
    case 'cancelled': return { ...at, cancelled: true, error: ev.text, pending: false, simplePending: false }
    case 'error': return { ...at, error: ev.text, pending: false }
    case 'blocks': return { ...at, blocks: ev.blocks }
    case 'simple_start': return { ...at, simpleText: '', simplePending: true }
    case 'simple_delta': return { ...at, simpleText: (at.simpleText ?? '') + ev.text }
    case 'simple_done': return { ...at, simpleText: ev.text, simplePending: false, simpleMs: ev.ms, simpleCost: ev.cost_usd }
    case 'simple_error': return { ...at, simpleText: '', simpleError: ev.text, simplePending: false }
  }
  return at
}

/** A recorded row as the screen shows it. */
const toMsg = (r: Turn): Msg => ({ role: r.role, text: r.said ?? r.text, blocks: r.blocks,
  tools: [], source: r.source, error: r.error, verification: r.verification,
  ms: r.ms, cost: r.cost_usd, model: r.model, sessionId: r.session_id, tokens: r.tokens,
  simpleText: r.simple_text, simpleError: r.simple_error,
  simpleMs: r.simple_meta?.ms, simpleCost: r.simple_meta?.cost_usd, runId: r.run_id,
  cancelled: r.cancelled })

const STALE: Record<string, string> = {
  changed: '이 파일은 답이 근거로 읽은 뒤 바뀌었다 — 지금 파일을 보인다. 답이 본 판은 ‘근거와 판단 보기’의 영어 스냅숏뿐이다',
  missing: '이 파일은 답이 근거로 읽은 뒤 지워졌다 — 답이 본 판은 ‘근거와 판단 보기’의 영어 스냅숏뿐이다',
  unreadable: '이 파일을 지금 읽지 못한다 — 답이 본 판은 ‘근거와 판단 보기’의 영어 스냅숏뿐이다',
}

/** Text put in one focus's box from outside — the map's "ask about this
 *  document". A new object each time, so the same text twice still lands. */
export type Seed = { focus: string; text: string }

type Props = {
  channels: Channel[]
  options: Options | null
  on: boolean
  seed: Seed | null
  onChannels: (list: Channel[]) => void
  onBusy: (busy: boolean) => void
  specs: Spec[]
  selectedSpec: Spec | null
  onSpecs: () => void
  onStart: (id: string) => Promise<void>
  /** A plan the Plan action started: select its worktree. */
  onPlanned: (spec: Spec) => void
  /** Show a finished run's graph paths on the map. */
  onMapRun: (run: RunSummary) => void
}

// What a retro candidate carries into the `next` focus when a person sends
// it somewhere: the old worktree instructions, split into the spec's goal and
// what stays out. English, because the agent reads it.
const MATERIAL: Record<'wiki' | 'claude_md', (candidate: string) => string> = {
  wiki: (c) => [
    'Turn this retro candidate into a spec.',
    "Goal: one wiki page for it, following `SCHEMA.md`'s page minimum structure, severity set by the evidence. "
      + "If the candidate names a page that already exists, climb that page's ladder instead of adding a page.",
    'Out: every file but that one page.',
    '', `Candidate: ${c}`,
  ].join('\n'),
  claude_md: (c) => [
    'Turn this retro candidate into a spec.',
    "Goal: one imperative sentence for it under the right section of this repository's `CLAUDE.md`. "
      + 'If a sentence already says it, point at that one instead.',
    'Out: every file but `CLAUDE.md`; no `CLAUDE.md` or no right place means stop and say so.',
    '', `Candidate: ${c}`,
  ].join('\n'),
}

/** The documentation a question searches (reliability PR 3). `all` sends no
 *  scope: every audience, as before. Shared rules answer every scope. */
const SCOPES = [
  { value: 'all', label: '모든 문서', note: '범위를 좁히지 않는다' },
  { value: 'product', label: '제품 유지보수', note: '개발·구조 문서' },
  { value: 'hooks', label: '훅 연결', note: '설치·연결 문서' },
  { value: 'jev', label: 'Jev 유지보수', note: 'Jev 안내와 계획' },
]

/** The middle pane's conversation: ask the wiki under one focus, read the
 *  grounds, and settle the next task into a spec. */
export function Query({ channels, options, on, seed, onChannels, onBusy, specs, selectedSpec, onSpecs, onStart, onPlanned,
  onMapRun }: Props) {
  const [active, setActive] = useState('wiki')
  const [planning, setPlanning] = useState(false)
  const [mobileOptions, setMobileOptions] = useState(false)
  const [scope, setScope] = useState('all')
  const [typed, setTyped] = useState<{ text: string } | null>(null)
  useEffect(() => {
    if (!seed) return
    setActive(seed.focus)
    setTyped({ text: seed.text })
  }, [seed])
  const [messages, setMessages] = useState<Msg[]>([])
  const [legacy, setLegacy] = useState<api.Turn[]>([])
  const [configuring, setConfiguring] = useState(false)
  const selectedRepo = channels[0]?.repo ?? ''
  // Which focuses are answering. One global boolean would block every other
  // focus while one answers — the `#위키` composer really did lock up that way.
  // Each is kept by project and focus: a run of the project left behind never
  // lands in, or blocks, the one selected now.
  const [busyOn, setBusyOn] = useState<string[]>([])
  const inFlight = useRef(new Map<string, Msg>())
  const slot = useCallback((cid: string) => `${selectedRepo}\u0000${cid}`, [selectedRepo])
  // The conversation on screen, for a late event to check against.
  const slotRef = useRef('')
  useEffect(() => {
    slotRef.current = slot(active)
  }, [slot, active])
  const [fault, setFault] = useState('')
  const [note, setNote] = useState('')
  const [peek, setPeek] = useState<{ data: PeekData | null; error?: string; note?: string; where?: string } | null>(null)

  useEffect(() => onBusy(busyOn.length > 0 || configuring), [busyOn, configuring, onBusy])

  /** Put one run's events onto a new answer at the end of `cid`'s list.
   *  `attach`: the question is already on screen (a reattach), so no user row. */
  const follow = useCallback(
    async (cid: string, stream: (onEvent: (ev: Ev) => void) => Promise<void>, attach = false, said = '') => {
      const key = slot(cid)
      if (inFlight.current.has(key)) return
      const placeholder: Msg = { role: 'assistant', text: '', tools: [], pending: true }
      inFlight.current.set(key, placeholder)
      setBusyOn((prev) => [...prev, key])
      setFault('')
      setMessages((prev) => attach ? [...prev, placeholder]
        : [...prev, { role: 'user', text: said, tools: [] }, placeholder])

      // Switching focus or project mid-stream leaves the list on screen
      // belonging to another conversation, and appending a chunk onto it
      // corrupts that one. A clear empties the list, so nothing matches
      // either. The server records it, so coming back restores it.
      const patch = (fn: (m: Msg) => Msg) => {
        const previous = inFlight.current.get(key)!
        const nextMessage = fn(previous)
        inFlight.current.set(key, nextMessage)
        setMessages((prev) => {
          if (slotRef.current !== key || prev.at(-1) !== previous) return prev
          const next = [...prev]
          next[next.length - 1] = nextMessage
          return next
        })
      }

      try {
        await stream((ev) => {
          patch((m) => onto(m, ev))
          if (ev.kind === 'blocks' && ev.blocks?.some((b) => b.name === 'spec')) onSpecs()
        })
        // A stream that dropped without an end still leaves no spinner.
        patch((m) => (m.pending ? { ...m, pending: false, error: m.error ?? '연결이 끊겼다. 새로 고치면 이어 본다' } : m))
      } catch (err) {
        patch((m) => m.simplePending
          ? ({ ...m, simpleText: '', simpleError: String(err), simplePending: false })
          : ({ ...m, error: String(err), pending: false }))
      } finally {
        inFlight.current.delete(key)
        setBusyOn((prev) => prev.filter((id) => id !== key))
        api.getChannels().then(onChannels).catch(() => {})
        window.dispatchEvent(new CustomEvent('conversation-changed', { detail: cid }))
      }
    },
    [slot, onChannels, onSpecs],
  )

  // Switching focus restores that focus's record. The server holds the
  // process, so there is nothing for the screen to remember.
  //
  // Three guards. A further switch discards this one (`stale`), a clear
  // since it was asked discards it too (`clears`), and a record arriving
  // after the person has typed something does not overwrite it.
  const clears = useRef(0)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const changed = (event: Event) => {
      if (event instanceof CustomEvent && event.detail !== active) return
      setRevision((n) => n + 1)
    }
    window.addEventListener('conversation-changed', changed)
    window.addEventListener('server-resync', changed)
    window.addEventListener('focus', changed)
    return () => {
      window.removeEventListener('conversation-changed', changed)
      window.removeEventListener('server-resync', changed)
      window.removeEventListener('focus', changed)
    }
  }, [active])
  useEffect(() => {
    setMessages([])
    setLegacy([])
    setNote('')
    setPeek(null)
  }, [active, selectedRepo])
  useEffect(() => {
    if (!active || !selectedRepo) return
    let stale = false
    const asked = clears.current
    api
      .getLog(active)
      .then((rows) => {
        if (stale || asked !== clears.current) return
        const restored = rows.map(toMsg)
        const live = inFlight.current.get(slot(active))
        if (live && (!live.runId || restored.at(-1)?.runId !== live.runId)) restored.push(live)
        // Never replace an answer this client is streaming. Its completion
        // invalidates the record again, so updates received meanwhile land.
        setMessages((prev) => live && prev.at(-1) === live ? prev : restored)
        // A question still running with no screen on it — the page reloaded,
        // or another window asked: follow it from its first event.
        if (!live && restored.at(-1)?.role === 'user') {
          api.getKnowledge().then(({ runs }) => {
            const run = runs.find((r) => r.focus === active)
            if (stale || asked !== clears.current || inFlight.current.has(slot(active))) return
            if (run) return void follow(active, (onEvent) => api.runEvents(run.run_id, -1, onEvent), true)
            // It ended between the two reads: its answer is in the record now.
            api.getLog(active).then((again) => {
              if (stale || asked !== clears.current) return
              setMessages((prev) => (prev === restored ? again.map(toMsg) : prev))
            }).catch(() => {})
          }).catch(() => {})
        }
      })
      .catch(() => !stale && setFault('기록을 못 읽었다'))
    api.getLog(active, true).then((rows) => !stale && setLegacy(rows))
      .catch(() => !stale && setFault('이전 기록을 못 읽었다'))
    return () => {
      stale = true
    }
  }, [active, selectedRepo, slot, follow, revision])

  const send = useCallback(
    (text: string, propose = false) => {
      const cid = active
      const audiences = scope === 'all' ? null : [scope as api.Audience]
      return follow(cid, (onEvent) => api.say(cid, text, onEvent, propose, audiences), false,
        propose ? '(후보 요청)' : text)
    },
    [active, follow, scope],
  )

  const stop = useCallback((runId: string) => {
    api.cancelRun(runId).catch((err) => setFault(String(err instanceof Error ? err.message : err)))
  }, [])

  const here = channels.find((c) => c.id === active)
  const busy = busyOn.includes(slot(active)) || configuring

  const apply = useCallback(
    async (cfg: { model: string; effort: string }) => {
      if (!here) return
      setFault('')
      setConfiguring(true)
      try {
        const { kept } = await api.setConfig(active, { repo: here.repo, ...cfg })
        onChannels(await api.getChannels())
        if (!kept) {
          setMessages([])
          setNote('Claude/Codex 를 바꿔 새 대화를 시작했다. 지난 기록은 그대로 남아 있다.')
        } else {
          setNote('다음 질문부터 적용된다. 지금까지 한 대화는 이어진다.')
        }
      } catch (err) {
        setFault(String(err))
      } finally {
        setConfiguring(false)
      }
    },
    [active, here, onChannels],
  )

  // Always asked: an empty pane is not an empty conversation — its record may
  // still be on the way, and a silent delete took it.
  const [asking, setAsking] = useState(false)
  const clear = useCallback(async (keep: api.Keep) => {
    const kept = await api.reset(active, keep)
    clears.current++
    setMessages([])
    setNote('')
    api.getChannels().then(onChannels).catch(() => {})
    return kept
  }, [active, onChannels])
  const wipe = useCallback(() => setAsking(true), [])

  // "That was wrong" — recorded in the census's format, together with the
  // utterance immediately before that answer.
  const markTurn = useCallback(
    async (index: number, kind: Kind) => {
      const answer = messages[index]
      const question = [...messages.slice(0, index)].reverse().find((m) => m.role === 'user')
      await api.mark(active, { kind, user_text: question?.text ?? '', assistant_text: answer?.text ?? '',
        session_id: answer?.sessionId })
      setMessages((prev) => prev.map((m, i) => (i === index ? { ...m, marked: kind } : m)))
    },
    [active, messages],
  )

  // The candidate as written, never the overlay: a page is English, and a
  // rendering sent back would write a translation of a translation.
  const decideOne = useCallback(
    async (candidate: string, target: 'wiki' | 'claude_md' | 'drop') => {
      if (target === 'drop') return '버렸다.'
      setActive('next')
      setTyped({ text: MATERIAL[target](candidate) })
      return '다음 작업 입력칸에 넣었다. 보내면 명세로 정리한다.'
    },
    [],
  )

  // A citation of a run's answer opens the file as it is now; when that is no
  // longer the revision the run read, the drawer says so.
  const showPeek = useCallback(
    async (path: string, line: number, runId?: string) => {
      if (!here) return
      setPeek({ data: null })
      const run = runId ? api.getRun(runId).catch(() => null) : Promise.resolve(null)
      try {
        const data = await api.peek(here.repo, path, line)
        const now = (await run)?.evidence?.find((e) => e.locator.path === path)?.now
        setPeek({ data, note: now && now !== 'same' ? STALE[now] : undefined })
      } catch (err) {
        const now = (await run)?.evidence?.find((e) => e.locator.path === path)?.now
        setPeek(now && now !== 'same' ? { data: null, note: STALE[now], where: `${path}:${line}` }
          : { data: null, error: String(err) })
      }
    },
    [here],
  )

  return (
    <section aria-label="대화" className="flex h-full min-w-0 flex-col">
      <header className="query-toolbar flex h-11 shrink-0 items-center justify-between gap-3 border-b border-border bg-card px-5">
        <nav aria-label="초점" className="flex min-w-0 gap-1">
          {channels.map((c) => (
            <button
              key={c.id}
              type="button"
              aria-pressed={c.id === active}
              onClick={() => setActive(c.id)}
              title={c.blurb}
              className={cn(
                'flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2.5 font-heading text-[14px] font-semibold',
                c.id === active ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:bg-secondary/60',
              )}
            >
              <span className={cn('size-1.5 rounded-full', c.live ? 'bg-primary' : 'bg-border')} />
              {c.label}
            </button>
          ))}
        </nav>
        <Btn className="query-options-toggle hidden" aria-expanded={mobileOptions} aria-controls="query-options"
          onClick={() => setMobileOptions((shown) => !shown)}>옵션</Btn>
        <div id="query-options" data-open={mobileOptions} className="query-options flex shrink-0 items-center gap-1.5">
          <Picker label="문서 범위" hideLabel width="w-32" items={SCOPES} value={scope} disabled={busy}
            onPick={(v) => setScope(v ?? 'all')} />
          {here && <Toolbar value={here} options={options} busy={busy} onChange={apply} />}
          <Btn onClick={() => setPlanning(true)}
            title="계획 세우기 — 계획자가 조사해 새 계획 폴더를 쓰고 PR 을 올린 뒤, 수정자와 리뷰어에게 넘긴다">
            계획
          </Btn>
          <Btn tone="ghost" className="px-1.5" onClick={wipe} disabled={busy} aria-label="문맥 비우기"
            title="문맥 비우기 — 이 초점의 대화를 새로 시작한다. 지금 대화는 메모리로 남기거나 지운다">
            <Eraser className="size-4" />
          </Btn>
        </div>
      </header>

      {fault && (
        <div role="alert" className="border-b border-destructive/30 bg-destructive/10 px-5 py-2 text-[12.5px] text-destructive">
          {fault}
        </div>
      )}
      {note && (
        <div className="border-b border-border bg-secondary px-5 py-2 text-[12.5px] text-muted-foreground">{note}</div>
      )}
      {legacy.length > 0 && (
        <details key={active} className="max-h-64 overflow-auto border-b border-border px-5 py-2 text-xs">
          <summary className="cursor-pointer">프로젝트 미분류 이전 기록 ({legacy.length}개)</summary>
          <p className="my-2 text-muted-foreground">예전 기록에는 프로젝트가 저장되지 않았다. 지금 프로젝트의 기록으로 치지 않는다.</p>
          {legacy.map((row, i) => <pre key={i} className="my-3 whitespace-pre-wrap">{row.role === 'user' ? '나' : '답'}: {row.text}</pre>)}
        </details>
      )}

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <Stream
            messages={active === 'next' && selectedSpec && !messages.some((m) => m.blocks?.some((b) => b.name === 'spec' && 'id' in b && b.id === selectedSpec.id))
              ? [...messages, { role: 'assistant', text: '', tools: [], blocks: [{ name: 'spec', id: selectedSpec.id }] }]
              : messages}
            korean={on}
            remote={here?.remote ?? ''}
            onPeek={showPeek}
            onDecide={active === 'retro' ? decideOne : undefined}
            onMark={markTurn}
            onStop={stop}
            onMapRun={onMapRun}
            blocks={active === 'next' ? (m) => (
              <Blocks blocks={m.blocks ?? []} specs={specs} korean={on} busy={busy}
                onSay={(text) => void send(text)} onSpecs={onSpecs} onStart={onStart} />
            ) : undefined}
            empty={active === 'next' ? (
              <div className="space-y-2 text-[13.5px] text-faint">
                <p>계획의 남은 행, 열린 PR, 최근 결정, 경고를 모아 다음 작업 후보를 낸다. 직접 물어도 된다.</p>
                <Btn tone="primary" disabled={busy} onClick={() => void send('', true)}>후보 내기</Btn>
              </div>
            ) : undefined}
          />
          <Composer busy={busy} onSend={send} seed={typed} />
        </div>
        {peek && <Peek data={peek.data} error={peek.error} note={peek.note} where={peek.where} onClose={() => setPeek(null)} />}
      </div>
      {planning && <PlanStart options={options} onClose={() => setPlanning(false)} onStarted={(spec) => {
        setPlanning(false)
        onPlanned(spec)
      }} />}
      {asking && <ClearAsk onClear={clear} onClose={(said) => {
        setAsking(false)
        if (said) setNote(said)
      }} />}
    </section>
  )
}
