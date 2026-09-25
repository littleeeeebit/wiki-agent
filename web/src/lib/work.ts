import { useCallback, useRef, useState } from 'react'
import * as api from '@/lib/api'
import type { AnsweredBy, Rule, Tokens, WorkEv, WorkStep } from '@/lib/api'

/** What happened inside one agent turn, in order. Tool lines and approvals are
 *  never translated: they are what ran, not the agent describing itself. */
export type Step =
  | { kind: 'tool'; text: string }
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
  pending?: boolean
  error?: string
  ms?: number
  cost?: number
  model?: string
  tokens?: Tokens
}

export type Rules = { session: string; list: Rule[] }

let seq = 0

function restored(s: WorkStep): Step {
  if (s.kind === 'tool') return s
  return { kind: 'approval', text: s.text, id: '', tool: s.tool, input: {}, by: s.by,
    answer: s.answer === 'none' ? undefined : s.answer === 'allow' }
}

/** One event folded into its turn. */
function apply(t: Turn, ev: WorkEv): Turn {
  const m = ev.meta
  if (ev.kind === 'delta') return { ...t, text: t.text + ev.text }
  if (ev.kind === 'tool') return { ...t, steps: [...t.steps, { kind: 'tool', text: ev.text }] }
  if (ev.kind === 'approval') {
    return { ...t, steps: [...t.steps, { kind: 'approval', text: ev.text, id: String(m.id ?? ''),
      tool: String(m.tool ?? ''), input: m.input ?? {}, session: m.session,
      ...(m.by ? { by: m.by, answer: m.answer === 'allow' } : {}) }] }
  }
  if (ev.kind === 'answered') {
    return { ...t, steps: t.steps.map((s) => (s.kind === 'approval' && s.id === m.id
      ? { ...s, answer: m.allow, by: m.by, sending: false, error: undefined } : s)) }
  }
  if (ev.kind === 'done') {
    return { ...t, text: ev.text || t.text, pending: false, ms: m.ms, cost: m.cost_usd, model: m.model,
      tokens: m.tokens }
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
  }, [])

  const readRules = useCallback((path: string) => {
    const life = lives.current.get(path) ?? 0
    api.workLog(path)
      .then(({ session_id, rules: list }) => {
        if ((lives.current.get(path) ?? 0) === life) setRules((all) => ({ ...all, [path]: { session: session_id, list } }))
      })
      .catch(() => {})
  }, [])

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
      }
      if (ev.seq !== undefined) {
        if (ev.seq <= last) return
        last = ev.seq
      }
      if (ev.kind === 'done' || ev.kind === 'error') ended = true
      patch(path, key, (t) => apply({ ...t, sessionId: owner || t.sessionId, turn: turn || t.turn }, ev))
    }
    let gone = false
    try {
      gone = (await first(on)) === 'gone'
    } catch {
      // Cut; tried once more below.
    }
    // A cut stream is not the end of the turn: once more, after the last event seen.
    if (!ended && !gone && turn) {
      try {
        gone = (await api.workEvents(path, turn, last, on)) === 'gone'
      } catch {
        // The server itself is gone; nothing more to read.
      }
    }
    patch(path, key, (t) => (t.pending
      ? { ...t, pending: false, error: t.error || (gone ? '다른 턴이 시작됐다. 기록을 다시 읽는다' : '스트림이 끊겼다') }
      : t))
    return gone
  }, [patch])

  // Named, so the reload after a replaced turn calls itself, not the outer binding.
  const load = useCallback(function load(path: string) {
    if (!path || loading.current.has(path)) return
    loading.current.add(path)
    const life = lives.current.get(path) ?? 0
    api.workLog(path)
      .then(({ rows, running, rules: list, session_id }) => {
        if ((lives.current.get(path) ?? 0) !== life) return
        const past: Turn[] = rows.map((r) => ({
          key: ++seq, role: r.role, text: r.text, error: r.error, ms: r.ms, cost: r.cost_usd, model: r.model,
          tokens: r.tokens, steps: (r.steps ?? []).map(restored),
        }))
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
  }, [follow, forget])

  const send = useCallback(
    async (path: string, text: string, choice: { model: string; effort: string }) => {
      const mine: Turn = { key: ++seq, role: 'assistant', text: '', steps: [], pending: true }
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
      if (gone) {
        forget(path)
        load(path)
        return
      }
      // Another CLI is another session, and its rules start empty.
      readRules(path)
    },
    [follow, forget, load, patch, readRules],
  )

  const answer = useCallback(
    async (path: string, turn: Turn, id: string, allow: boolean, scope: 'once' | 'session' = 'once') => {
      const step = (fn: (s: Extract<Step, { kind: 'approval' }>) => Step) =>
        patch(path, turn.key, (t) => ({
          ...t,
          steps: t.steps.map((s) => (s.kind === 'approval' && s.id === id ? fn(s) : s)),
        }))
      step((s) => ({ ...s, sending: true, error: undefined }))
      try {
        await api.workAnswer({ path, session_id: turn.sessionId ?? '', id, allow, scope })
        step((s) => ({ ...s, sending: false, answer: allow, by: 'person' }))
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

  const clearRules = useCallback(async (path: string) => {
    const mine = rules[path]
    if (!mine) return
    await api.clearRules(path, mine.session)
    setRules((all) => ({ ...all, [path]: { ...mine, list: [] } }))
  }, [rules])

  const reset = useCallback(async (path: string) => {
    await api.workReset(path)
    setTurns((all) => ({ ...all, [path]: [] }))
    setRules((all) => ({ ...all, [path]: { session: '', list: [] } }))
  }, [])

  /** A turn the server started in `path` — the plan row's, a loop's. A
   *  window that already shows that worktree reads it again, which attaches
   *  to the running turn; this window's own sends are already on screen. */
  const attach = useCallback((path: string) => {
    if (sending.current.has(path) || !loading.current.has(path)) return
    forget(path)
    load(path)
  }, [forget, load])

  return { turns, rules, load, send, answer, stop, clearRules, reset, forget, attach }
}
