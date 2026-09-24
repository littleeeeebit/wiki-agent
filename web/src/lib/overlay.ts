import { useEffect, useRef, useState } from 'react'
import { renderAll } from '@/lib/api'

/** The Korean overlay over an English answer.
 *
 *  The agent writes English and the person reads Korean. This is the second
 *  half of that. The mirror that once did the same over a session log settled
 *  the two rules this file has to keep.
 *
 *  What is never translated: anything the person typed. Not decided by
 *  language — the user writes Korean and English both — but by who wrote it.
 *  The overlay is where someone checks what was understood, and a round trip
 *  of their own sentence makes that check worthless. Callers pass the agent's
 *  text and nothing else.
 *
 *  What is never translated either: a command, a path, a patch. Those are
 *  lifted out server-side before the request, so there is nothing to protect
 *  here.
 *
 *  Off means no request at all, not a request whose answer is discarded. */
const memory = new Map<string, string>()

export function useOverlay(texts: string[], on: boolean): string[] {
  const [done, setDone] = useState<Map<string, string>>(memory)
  // What this component is currently showing. A translation is a network call,
  // so a late answer routinely lands after the answer it was for has been
  // replaced or the toggle has been turned off. Without this the old reply
  // overwrites the new state and looks exactly like a real one.
  const showing = useRef(0)

  const wanted = on ? texts.filter((t) => t.trim() && !memory.has(t)) : []
  const key = wanted.join('\u0000')

  useEffect(() => {
    if (!on || !key) return
    const mine = ++showing.current
    let alive = true
    renderAll(key.split('\u0000'))
      .then((out) => {
        if (!alive || mine !== showing.current) return
        key.split('\u0000').forEach((text, i) => memory.set(text, out[i] ?? text))
        setDone(new Map(memory))
      })
      // A failed translation shows the English. An empty pane is worse than an
      // English one: the person can read English, they would rather not.
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [key, on])

  if (!on) return texts
  return texts.map((t) => done.get(t) ?? memory.get(t) ?? t)
}
