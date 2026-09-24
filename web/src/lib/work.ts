import { useCallback, useRef, useState } from 'react'
import * as api from '@/lib/api'
import type { WorkEv } from '@/lib/api'

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
      answer?: boolean
      sending?: boolean
      error?: string
    }

export type Turn = {
  key: number
  role: 'user' | 'assistant'
  text: string
  steps: Step[]
  sessionId?: string
  pending?: boolean
  error?: string
  ms?: number
  cost?: number
  model?: string
}

let seq = 0

/** Every worktree's agent conversation, keyed by the worktree's path.
 *
 *  Keyed by path so a stream keeps landing on its own worktree while the
 *  person looks at another. A turn is found by its `key`, never by position:
 *  a reset empties the list, and a late event then finds nothing and is
 *  dropped. Within a turn, the first event names the session, and an event
 *  from any other session is dropped too. */
export function useWork() {
  const [turns, setTurns] = useState<Record<string, Turn[]>>({})
  const loading = useRef(new Set<string>())

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

  const load = useCallback((path: string) => {
    if (!path || loading.current.has(path)) return
    loading.current.add(path)
    api.workLog(path)
      .then(({ rows }) => {
        const restored: Turn[] = rows.map((r) => ({
          key: ++seq, role: r.role, text: r.text, error: r.error, ms: r.ms, cost: r.cost_usd, model: r.model,
          steps: (r.tools ?? []).map((text) => ({ kind: 'tool' as const, text })),
        }))
        // A turn sent before the record came back stays after it.
        setTurns((all) => ({ ...all, [path]: [...restored, ...(all[path] ?? [])] }))
      })
      .catch(() => loading.current.delete(path))
  }, [])

  const send = useCallback(
    async (path: string, text: string, choice: { model: string; effort: string }) => {
      const mine: Turn = { key: ++seq, role: 'assistant', text: '', steps: [], pending: true }
      setTurns((all) => ({
        ...all,
        [path]: [...(all[path] ?? []), { key: ++seq, role: 'user', text, steps: [] }, mine],
      }))
      let owner = ''
      const on = (ev: WorkEv) => {
        if (ev.session_id) {
          owner ||= ev.session_id
          if (ev.session_id !== owner) return
        }
        patch(path, mine.key, (t) => {
          const at = { ...t, sessionId: owner || t.sessionId }
          if (ev.kind === 'delta') return { ...at, text: at.text + ev.text }
          if (ev.kind === 'tool') return { ...at, steps: [...at.steps, { kind: 'tool', text: ev.text }] }
          if (ev.kind === 'approval') {
            return { ...at, steps: [...at.steps, { kind: 'approval', text: ev.text, id: String(ev.meta.id ?? ''),
              tool: String(ev.meta.tool ?? ''), input: ev.meta.input ?? {} }] }
          }
          if (ev.kind === 'done') {
            return { ...at, text: ev.text || at.text, pending: false, ms: ev.meta.ms, cost: ev.meta.cost_usd,
              model: ev.meta.model }
          }
          return { ...at, error: ev.text, pending: false }
        })
      }
      try {
        await api.workSay({ path, text, ...choice }, on)
      } catch (err) {
        patch(path, mine.key, (t) => ({ ...t, error: String(err), pending: false }))
      } finally {
        patch(path, mine.key, (t) => (t.pending ? { ...t, pending: false, error: t.error || '스트림이 끊겼다' } : t))
      }
    },
    [patch],
  )

  const answer = useCallback(
    async (path: string, turn: Turn, id: string, allow: boolean) => {
      const step = (fn: (s: Extract<Step, { kind: 'approval' }>) => Step) =>
        patch(path, turn.key, (t) => ({
          ...t,
          steps: t.steps.map((s) => (s.kind === 'approval' && s.id === id ? fn(s) : s)),
        }))
      step((s) => ({ ...s, sending: true, error: undefined }))
      try {
        await api.workAnswer({ path, session_id: turn.sessionId ?? '', id, allow })
        step((s) => ({ ...s, sending: false, answer: allow }))
      } catch (err) {
        step((s) => ({ ...s, sending: false, error: String(err) }))
      }
    },
    [patch],
  )

  const forget = useCallback((path: string) => {
    loading.current.delete(path)
    setTurns(({ [path]: _gone, ...rest }) => rest)
  }, [])

  const reset = useCallback(async (path: string) => {
    await api.workReset(path)
    setTurns((all) => ({ ...all, [path]: [] }))
  }, [])

  return { turns, load, send, answer, reset, forget }
}
