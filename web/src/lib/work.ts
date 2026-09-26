import { useCallback, useRef, useState } from 'react'
import * as api from '@/lib/api'
import type { AnsweredBy, Rule, Tokens, WorkEv, WorkStep } from '@/lib/api'

/** What happened inside one agent turn, in order. Tool lines and approvals are
 *  never translated: they are what ran, not the agent describing itself. */
export type Step =
  | { kind: 'tool'; text: string }
  /** What the person said into the turn while it ran. */
  | { kind: 'said'; text: string }
  /** A hook that said something; `context` is what it put in, live turns only. */
  | { kind: 'hook'; text: string; context?: string }
  | {
      kind: 'approval'
      text: string
      id: string
      tool: string
      input: Record<string, unknown>
      /** Can "allow for this session" cover it. */
      session?: boolean
      answer?: boolean
      by?: AnsweredBy
      /** A question's answers, one per question. */
      answers?: string[]
      sending?: boolean
      error?: string
    }

export type Turn = {
  key: number
  role: 'user' | 'assistant'
  text: string
  steps: Step[]
  sessionId?: string
  /** The server's id for this turn, once an event named it. */
  turn?: string
  /** Until the server's run ends — not at the answer: the gate runs after it,
   *  still holding the worktree. */
  pending?: boolean
  /** How many steps stood when the answer came. Later ones — the gate — ran
   *  after it. */
  answered?: number
  /** When the latest event came, in ms, and what it was: the clock on the
   *  running marker counts from it. */
  since?: number
  latest?: 'step' | 'text'
  error?: string
  ms?: number
  cost?: number
  model?: string
  tokens?: Tokens
}

export type Rules = { session: string; list: Rule[] }

let seq = 0

function restored(s: WorkStep): Step {
  if (s.kind !== 'approval') return s
  return { kind: 'approval', text: s.text, id: '', tool: s.tool, input: {}, by: s.by, answers: s.answers,
    answer: s.answer === 'none' ? undefined : s.answer === 'allow' }
}

/** One event folded into its turn. */
function apply(t: Turn, ev: WorkEv): Turn {
  const m = ev.meta
  // The server's time, so a reattach that reads the buffer again still
  // counts from when the step started, not from the reread.
  t = { ...t, since: ev.ts ? ev.ts * 1000 : Date.now() }
  if (ev.kind === 'delta') return { ...t, text: t.text + ev.text, latest: 'text' }
  if (ev.kind === 'tool' || ev.kind === 'said') {
    return { ...t, steps: [...t.steps, { kind: ev.kind, text: ev.text }], latest: 'step' }
  }
  if (ev.kind === 'hook') {
    return { ...t, steps: [...t.steps, { kind: 'hook', text: ev.text, context: m.context }], latest: 'step' }
  }
  if (ev.kind === 'approval') {
    return { ...t, latest: 'step', steps: [...t.steps, { kind: 'approval', text: ev.text, id: String(m.id ?? ''),
      tool: String(m.tool ?? ''), input: m.input ?? {}, session: m.session,
      ...(m.by ? { by: m.by, answer: m.answer === 'allow' } : {}) }] }
  }
  if (ev.kind === 'answered') {
    return { ...t, steps: t.steps.map((s) => (s.kind === 'approval' && s.id === m.id
      ? { ...s, answer: m.allow, by: m.by, answers: m.answers, sending: false, error: undefined } : s)) }
  }
  if (ev.kind === 'done') {
    return { ...t, text: ev.text || t.text, answered: t.steps.length, latest: 'text', ms: m.ms, cost: m.cost_usd,
      model: m.model, tokens: m.tokens }
  }
  return { ...t, error: ev.text, pending: false }
}

/** Every worktree's agent conversation, keyed by the worktree's path.
 *
 *  Keyed by path so a stream keeps landing on its own worktree while the
 *  person looks at another. A turn is found by its `key`, never by position:
 *  a reset empties the list, and a late event then finds nothing and is
 *  dropped. Within a turn, the first event names the session and the server's
 *  turn, and an event from any other is dropped too.
 *
 *  The turn runs on the server, not in this window. A reload finds it in the
 *  record's `running` and reads its events again from the start; a cut stream
 *  picks up once after the last `seq` it saw. */
export function useWork() {
  const [turns, setTurns] = useState<Record<string, Turn[]>>({})
  const [rules, setRules] = useState<Record<string, Rules>>({})
  const loading = useRef(new Set<string>())
  // Paths this window is sending to. The record's running turn is then that
  // send's own, already on screen.
  const sending = useRef(new Set<string>())
  // Which worktree a path names. Removing a worktree and making one with the
  // same task name gives the same path, so a record fetched for the old one
  // and landing after `forget` would otherwise fill the new one's pane.
  const lives = useRef(new Map<string, number>())
  // An instruction written after the answer, while the gate still holds the
  // worktree: there is no turn left to steer, so the server keeps it and
  // sends it once the worktree is let go. This is only what the record says.
  const [queued, setQueued] = useState<Record<string, string>>({})
  const showQueued = useCallback((path: string, text: string | null) => {
    setQueued(({ [path]: _gone, ...rest }) => (text ? { ...rest, [path]: text } : rest))
  }, [])
  // An instruction the server would not keep waiting, until the person puts
  // it back or lets it go. Here, not in the pane or the box: it outlives a
  // reattach, a switch to another pane, and whatever is typed meanwhile.
  // Every one of them: each was cleared from the box when it was sent.
  const [refused, setRefused] = useState<Record<string, { text: string; reason: string }[]>>({})
  /** One refused instruction by its place, or all of the path's. */
  const dismiss = useCallback((path: string, at?: number) => {
    setRefused(({ [path]: list = [], ...rest }) => {
      const left = at === undefined ? [] : list.filter((_, i) => i !== at)
      return left.length ? { ...rest, [path]: left } : rest
    })
  }, [])
  // Paths a server-started turn was announced for while this window's own
  // send still followed its turn: read again once that send lets go.
  const missed = useRef(new Set<string>())
  // The server's turns this window already follows.
  const own = useRef(new Set<string>())

  const patch = useCallback((path: string, key: number, fn: (t: Turn) => Turn) => {
    setTurns((all) => {
      const list = all[path]
      const i = list?.findIndex((t) => t.key === key) ?? -1
      if (!list || i < 0) return all
      const next = [...list]
      next[i] = fn(list[i])
      return { ...all, [path]: next }
    })
  }, [])

  const forget = useCallback((path: string) => {
    lives.current.set(path, (lives.current.get(path) ?? 0) + 1)
    loading.current.delete(path)
    setTurns(({ [path]: _gone, ...rest }) => rest)
    setRules(({ [path]: _gone, ...rest }) => rest)
    showQueued(path, null)
  }, [showQueued])

  const readRules = useCallback((path: string) => {
    const life = lives.current.get(path) ?? 0
    api.workLog(path)
      .then(({ session_id, rules: list, queued: waiting }) => {
        if ((lives.current.get(path) ?? 0) !== life) return
        setRules((all) => ({ ...all, [path]: { session: session_id, list } }))
        showQueued(path, waiting)
      })
      .catch(() => {})
  }, [showQueued])

  /** Follow one turn's events into `key` until it ends. `first` is the
   *  stream that starts it — the instruction's own, or a reattach. */
  const follow = useCallback(async (
    path: string, key: number, first: (on: (ev: WorkEv) => void) => Promise<'gone' | void>,
    known: { session?: string; turn?: string } = {},
  ) => {
    let owner = known.session ?? ''
    let turn = known.turn ?? ''
    let last = -1
    let ended = false
    const on = (ev: WorkEv) => {
      if (ev.session_id) {
        owner ||= ev.session_id
        if (ev.session_id !== owner) return
      }
      if (ev.turn) {
        turn ||= ev.turn
        if (ev.turn !== turn) return
        own.current.add(turn)
      }
      if (ev.seq !== undefined) {
        if (ev.seq <= last) return
        last = ev.seq
      }
      if (ev.kind === 'done' || ev.kind === 'error') ended = true
      patch(path, key, (t) => apply({ ...t, sessionId: owner || t.sessionId, turn: turn || t.turn }, ev))
    }
    // The stream closes when the server's run ends; the answer comes before
    // that, and the gate runs between the two.
    let gone = false
    let cut = false
    try {
      gone = (await first(on)) === 'gone'
    } catch {
      cut = true
    }
    // A cut stream is not the end of the turn, even after the answer: once
    // more, after the last event seen.
    if ((cut || !ended) && !gone && turn) {
      try {
        gone = (await api.workEvents(path, turn, last, on)) === 'gone'
        cut = false
      } catch {
        // The server itself is gone; nothing more to read.
      }
    }
    patch(path, key, (t) => (t.pending
      ? { ...t, pending: false, error: t.error
          || (gone ? '다른 턴이 시작됐다. 기록을 다시 읽는다' : cut || !ended ? '스트림이 끊겼다' : undefined) }
      : t))
    // Another CLI is another session, and its rules start empty; and what
    // waited was sent, kept for a turn the server started, or dropped by a stop.
    if (!gone) readRules(path)
    return gone
  }, [patch, readRules])

  // Named, so the reload after a replaced turn calls itself, not the outer binding.
  const load = useCallback(function load(path: string) {
    if (!path || loading.current.has(path)) return
    loading.current.add(path)
    const life = lives.current.get(path) ?? 0
    api.workLog(path)
      .then(({ rows, running, rules: list, session_id, queued: waiting }) => {
        if ((lives.current.get(path) ?? 0) !== life) return
        const past: Turn[] = rows.map((r) => ({
          key: ++seq, role: r.role, text: r.text, error: r.error, ms: r.ms, cost: r.cost_usd, model: r.model,
          tokens: r.tokens, steps: (r.steps ?? []).map(restored), answered: r.answered,
        }))
        showQueued(path, waiting)
        // The turn still running: its reply is not on record yet. Its events
        // are, in the server's buffer, from the first.
        const live = running && !sending.current.has(path)
          ? { key: ++seq, role: 'assistant' as const, text: '', steps: [], pending: true,
              sessionId: running.session_id, turn: running.turn }
          : null
        // A turn sent before the record came back stays after it.
        setTurns((all) => ({ ...all, [path]: [...past, ...(live ? [live] : []), ...(all[path] ?? [])] }))
        setRules((all) => ({ ...all, [path]: { session: session_id, list } }))
        if (live && running) {
          follow(path, live.key, (on) => api.workEvents(path, running.turn, -1, on),
            { session: running.session_id, turn: running.turn })
            .then((gone) => {
              if (gone) {
                forget(path)
                load(path)
              }
            })
        }
      })
      .catch(() => {
        if ((lives.current.get(path) ?? 0) === life) loading.current.delete(path)
      })
  }, [follow, forget, showQueued])

  const send = useCallback(
    async (path: string, text: string, choice: { model: string; effort: string }) => {
      const mine: Turn = { key: ++seq, role: 'assistant', text: '', steps: [], pending: true, since: Date.now() }
      setTurns((all) => ({
        ...all,
        [path]: [...(all[path] ?? []), { key: ++seq, role: 'user', text, steps: [] }, mine],
      }))
      sending.current.add(path)
      let gone = false
      try {
        gone = await follow(path, mine.key, (on) => api.workSay({ path, text, ...choice }, on))
      } catch (err) {
        patch(path, mine.key, (t) => ({ ...t, error: String(err), pending: false }))
      } finally {
        sending.current.delete(path)
      }
      if (gone || missed.current.delete(path)) {
        forget(path)
        load(path)
      }
    },
    [follow, forget, load, patch],
  )

  /** `text` as the next instruction, which the server sends once `turn`'s
   *  run lets go of the worktree. Refused, it is never sent from here: a
   *  lost answer and a refusal look alike, and the server may already have
   *  sent it. What the server holds is read again, and the text is kept as
   *  refused for the person to act on. */
  const queue = useCallback(async (path: string, turn: Turn, text: string,
    choice: { model: string; effort: string }) => {
    try {
      await api.workQueue({ path, turn: turn.turn ?? '', text, ...choice })
      showQueued(path, text)
    } catch (err) {
      readRules(path)
      setRefused((all) => ({ ...all, [path]: [...(all[path] ?? []),
        { text, reason: err instanceof Error ? err.message : String(err) }] }))
    }
  }, [readRules, showQueued])

  /** Refused when it no longer waits — already sent, or dropped by a stop:
   *  the refusal goes to the caller, and the card follows the server. */
  const unqueue = useCallback(async (path: string) => {
    try {
      await api.workUnqueue(path)
      showQueued(path, null)
    } catch (err) {
      readRules(path)
      throw err
    }
  }, [readRules, showQueued])

  const answer = useCallback(
    async (path: string, turn: Turn, id: string, allow: boolean, scope: 'once' | 'session' = 'once',
      answers?: string[]) => {
      const step = (fn: (s: Extract<Step, { kind: 'approval' }>) => Step) =>
        patch(path, turn.key, (t) => ({
          ...t,
          steps: t.steps.map((s) => (s.kind === 'approval' && s.id === id ? fn(s) : s)),
        }))
      step((s) => ({ ...s, sending: true, error: undefined }))
      try {
        await api.workAnswer({ path, session_id: turn.sessionId ?? '', id, allow, scope, answers })
        step((s) => ({ ...s, sending: false, answer: allow, by: 'person', answers }))
        if (scope === 'session') readRules(path)
      } catch (err) {
        step((s) => ({ ...s, sending: false, error: String(err) }))
      }
    },
    [patch, readRules],
  )

  const stop = useCallback(async (path: string, turn: Turn) => {
    if (turn.turn) await api.workStop(path, turn.turn)
  }, [])

  /** Say `text` into the running `turn`. It comes back through the turn's
   *  own events as a `said` step; a refusal is shown in its place. */
  const steer = useCallback(async (path: string, turn: Turn, text: string) => {
    try {
      if (!turn.turn) throw new Error('턴이 아직 시작되지 않았다')
      await api.workSteer(path, turn.turn, text)
    } catch (err) {
      patch(path, turn.key, (t) => ({ ...t, steps: [...t.steps,
        { kind: 'tool', text: `끼어들기 실패 · ${err instanceof Error ? err.message : err} — "${text}"` }] }))
    }
  }, [patch])

  const clearRules = useCallback(async (path: string) => {
    const mine = rules[path]
    if (!mine) return
    await api.clearRules(path, mine.session)
    setRules((all) => ({ ...all, [path]: { ...mine, list: [] } }))
  }, [rules])

  const reset = useCallback(async (path: string, keep: api.Keep) => {
    const kept = await api.workReset(path, keep)
    // A record read before the clear and landing after it would bring the
    // cleared conversation back.
    lives.current.set(path, (lives.current.get(path) ?? 0) + 1)
    setTurns((all) => ({ ...all, [path]: [] }))
    setRules((all) => ({ ...all, [path]: { session: '', list: [] } }))
    return kept
  }, [])

  /** A turn the server started in `path` — the plan row's, a loop's. A
   *  window that already shows that worktree reads it again, which attaches
   *  to the running turn; this window's own sends are already on screen. */
  const attach = useCallback((path: string, turn?: string) => {
    if (!loading.current.has(path) || (turn && own.current.has(turn))) return
    // Announced while this window's send still follows its own turn — the
    // instruction that waited, the plan row's: read once that send lets go.
    if (sending.current.has(path)) {
      missed.current.add(path)
      return
    }
    forget(path)
    load(path)
  }, [forget, load])

  return { turns, rules, queued, refused, load, send, queue, unqueue, dismiss, answer, stop, steer, clearRules, reset,
    forget, attach }
}
