import { useCallback, useEffect, useRef, useState } from 'react'
import { Blocks } from '@/components/Blocks'
import { Composer } from '@/components/Composer'
import { Peek } from '@/components/Peek'
import { Stream } from '@/components/Stream'
import { Toolbar } from '@/components/Toolbar'
import * as api from '@/lib/api'
import type { Block, Channel, Kind, Options, Peek as PeekData, Spec, Tokens } from '@/lib/api'
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
  pending?: boolean
  simpleText?: string
  simpleError?: string
  simplePending?: boolean
  simpleMs?: number
  simpleCost?: number
}

type Props = {
  channels: Channel[]
  options: Options | null
  on: boolean
  onChannels: (list: Channel[]) => void
  onBusy: (busy: boolean) => void
  onDraft: (text: string) => void
  specs: Spec[]
  onSpecs: () => void
  onStart: (id: string) => Promise<void>
}

/** The main pane: ask the wiki under one focus, read the grounds, and carry
 *  an answer over to a worktree's agent. */
export function Query({ channels, options, on, onChannels, onBusy, onDraft, specs, onSpecs, onStart }: Props) {
  const [active, setActive] = useState('wiki')
  const [messages, setMessages] = useState<Msg[]>([])
  const [legacy, setLegacy] = useState<api.Turn[]>([])
  const [configuring, setConfiguring] = useState(false)
  const selectedRepo = channels[0]?.repo ?? ''
  // Which focuses are answering. One global boolean would block every other
  // focus while one answers — the `#위키` composer really did lock up that way.
  const [busyOn, setBusyOn] = useState<string[]>([])
  const activeRef = useRef('')
  const inFlight = useRef(new Map<string, Msg>())
  activeRef.current = active
  const [fault, setFault] = useState('')
  const [note, setNote] = useState('')
  const [peek, setPeek] = useState<{ data: PeekData | null; error?: string } | null>(null)

  useEffect(() => onBusy(busyOn.length > 0 || configuring), [busyOn, configuring, onBusy])

  // Switching focus restores that focus's record. The server holds the
  // process, so there is nothing for the screen to remember.
  //
  // Two guards. A further switch discards this one (`stale`), and a record
  // arriving after the person has typed something does not overwrite it.
  useEffect(() => {
    if (!active || !selectedRepo) return
    let stale = false
    setMessages([])
    setLegacy([])
    setNote('')
    setPeek(null)
    api
      .getLog(active)
      .then((rows) => {
        if (stale) return
        const restored: Msg[] = rows.map((r) => ({ role: r.role, text: r.said ?? r.text, blocks: r.blocks,
          tools: [], source: r.source, error: r.error,
          ms: r.ms, cost: r.cost_usd, model: r.model, sessionId: r.session_id, tokens: r.tokens,
          simpleText: r.simple_text, simpleError: r.simple_error,
          simpleMs: r.simple_meta?.ms, simpleCost: r.simple_meta?.cost_usd }))
        const live = inFlight.current.get(active)
        if (live && restored.at(-1)?.role === 'user') restored.push(live)
        setMessages((prev) => (prev.length ? prev : restored))
      })
      .catch(() => !stale && setFault('기록을 못 읽었다'))
    api.getLog(active, true).then((rows) => !stale && setLegacy(rows))
      .catch(() => !stale && setFault('이전 기록을 못 읽었다'))
    return () => {
      stale = true
    }
  }, [active, selectedRepo])

  const send = useCallback(
    async (text: string, propose = false) => {
      const cid = active
      const placeholder: Msg = { role: 'assistant', text: '', tools: [], pending: true }
      inFlight.current.set(cid, placeholder)
      setBusyOn((prev) => [...prev, cid])
      setFault('')
      setMessages((prev) => [...prev, { role: 'user', text: propose ? '(후보 요청)' : text, tools: [] }, placeholder])

      // Switching focus mid-stream leaves the list on screen belonging to
      // another focus, and appending a chunk onto it corrupts that
      // conversation. The server records it, so coming back restores it.
      const patch = (fn: (m: Msg) => Msg) => {
        const previous = inFlight.current.get(cid)!
        const nextMessage = fn(previous)
        inFlight.current.set(cid, nextMessage)
        setMessages((prev) => {
          if (activeRef.current !== cid || prev.at(-1) !== previous) return prev
          const next = [...prev]
          next[next.length - 1] = nextMessage
          return next
        })
      }

      try {
        await api.say(cid, text, (ev) => {
          if (ev.kind === 'hits') {
            patch((m) => ({ ...m, hits: ev.pages ?? [] }))
          } else if (ev.kind === 'delta') {
            patch((m) => ({ ...m, text: m.text + ev.text }))
          } else if (ev.kind === 'tool') {
            patch((m) => ({ ...m, tools: [...m.tools, ev.text] }))
          } else if (ev.kind === 'done') {
            // The final body is the server's copy. A missed chunk is corrected
            // right here.
            patch((m) => ({ ...m, text: ev.text || m.text, ms: ev.ms, cost: ev.cost_usd, tokens: ev.tokens,
              model: ev.model, sessionId: ev.session_id, pending: false }))
          } else if (ev.kind === 'error') {
            patch((m) => ({ ...m, error: ev.text, pending: false }))
          } else if (ev.kind === 'blocks') {
            patch((m) => ({ ...m, blocks: ev.blocks }))
            if (ev.blocks?.some((b) => b.name === 'spec')) onSpecs()
          } else if (ev.kind === 'simple_start') {
            patch((m) => ({ ...m, simpleText: '', simplePending: true }))
          } else if (ev.kind === 'simple_delta') {
            patch((m) => ({ ...m, simpleText: (m.simpleText ?? '') + ev.text }))
          } else if (ev.kind === 'simple_done') {
            patch((m) => ({ ...m, simpleText: ev.text, simplePending: false, simpleMs: ev.ms, simpleCost: ev.cost_usd }))
          } else if (ev.kind === 'simple_error') {
            patch((m) => ({ ...m, simpleText: '', simpleError: ev.text, simplePending: false }))
          }
        }, propose)
      } catch (err) {
        patch((m) => m.simplePending
          ? ({ ...m, simpleText: '', simpleError: String(err), simplePending: false })
          : ({ ...m, error: String(err), pending: false }))
      } finally {
        inFlight.current.delete(cid)
        setBusyOn((prev) => prev.filter((id) => id !== cid))
        api.getChannels().then(onChannels).catch(() => {})
      }
    },
    [active, onChannels, onSpecs],
  )

  const here = channels.find((c) => c.id === active)
  const busy = busyOn.includes(active) || configuring

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

  const wipe = useCallback(async () => {
    try {
      await api.reset(active)
      setMessages([])
      setNote('')
      api.getChannels().then(onChannels).catch(() => {})
    } catch (err) {
      setFault(String(err))
    }
  }, [active, onChannels])

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

  const draftFrom = useCallback(
    async (index: number) => {
      const answer = messages[index]
      const question = [...messages.slice(0, index)].reverse().find((m) => m.role === 'user')
      try {
        // The original answer, never the overlay: the agent reads English,
        // and a translation of its grounds is a rewording of them.
        onDraft((await api.draft({ question: question?.text ?? '', answer: answer.text, hits: answer.hits })).text)
      } catch (err) {
        setFault(String(err))
      }
    },
    [messages, onDraft],
  )

  const decideOne = useCallback(
    async (candidate: string, target: 'wiki' | 'claude_md' | 'drop') => {
      if (target === 'drop') return '버렸다.'
      onDraft((await api.draft({ question: candidate, target })).text)
      return '초안을 에이전트 입력칸에 넣었다. 작업트리를 고르고, 마지막 절을 적어 보낸다.'
    },
    [onDraft],
  )

  const showPeek = useCallback(
    async (path: string, line: number) => {
      if (!here) return
      setPeek({ data: null })
      try {
        setPeek({ data: await api.peek(here.repo, path, line) })
      } catch (err) {
        setPeek({ data: null, error: String(err) })
      }
    },
    [here],
  )

  return (
    <section aria-label="위키 질의" className="flex h-full min-w-0 flex-col">
      <header className="border-b border-border bg-card px-5 pt-3 pb-2.5">
        <nav aria-label="초점" className="flex h-7 gap-1 overflow-hidden">
          {channels.map((c) => (
            <button
              key={c.id}
              type="button"
              aria-pressed={c.id === active}
              onClick={() => setActive(c.id)}
              title={c.blurb}
              className={cn(
                'flex items-center gap-1.5 rounded-md px-2.5 py-1 font-heading text-[14px] font-semibold',
                c.id === active ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:bg-secondary/60',
              )}
            >
              <span className={cn('size-1.5 rounded-full', c.live ? 'bg-primary' : 'bg-border')} />
              {c.label}
            </button>
          ))}
        </nav>
        <div className="mt-2 flex items-center justify-between gap-2">
          <p className="min-w-0 truncate text-[12.5px] text-muted-foreground">
            {here?.blurb ?? ''}
            {here?.model_name && (
              <span className="ml-2 font-mono text-[10.5px] text-faint">{here.model_name.replace('claude-', '')}</span>
            )}
          </p>
          <div className="flex shrink-0 items-center gap-1.5">
            {here && <Toolbar value={here} options={options} busy={busy} onChange={apply} />}
            <button
              type="button"
              onClick={wipe}
              disabled={busy}
              className="h-7 rounded-md border border-border px-2 text-[12.5px] text-muted-foreground hover:bg-secondary disabled:opacity-40"
            >
              문맥 비우기
            </button>
          </div>
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
            messages={messages}
            korean={on}
            remote={here?.remote ?? ''}
            onPeek={showPeek}
            onDecide={active === 'retro' ? decideOne : undefined}
            onMark={markTurn}
            onDraft={draftFrom}
            blocks={active === 'next' ? (m) => (
              <Blocks blocks={m.blocks ?? []} specs={specs} korean={on} busy={busy}
                onSay={(text) => void send(text)} onSpecs={onSpecs} onStart={onStart} />
            ) : undefined}
            empty={active === 'next' ? (
              <div className="space-y-2 text-[13.5px] text-faint">
                <p>계획의 남은 행, 열린 PR, 최근 결정, 경고를 모아 다음 작업 후보를 낸다. 직접 물어도 된다.</p>
                <button type="button" disabled={busy} onClick={() => void send('', true)}
                  className="rounded-md border border-primary px-3 py-1 text-[13px] text-primary hover:bg-secondary disabled:opacity-40">
                  후보 내기
                </button>
              </div>
            ) : undefined}
          />
          <Composer busy={busy} onSend={send} />
        </div>
        {peek && <Peek data={peek.data} error={peek.error} onClose={() => setPeek(null)} />}
      </div>
    </section>
  )
}
