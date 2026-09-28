import { useEffect, useRef, useState } from 'react'
import { renderAll, renderChecked } from '@/lib/api'

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

/** An answer's paragraphs: split at blank lines, never inside a ``` or ~~~
 *  fence — a code block with a blank line in it is one piece, which the
 *  translator keeps byte for byte. Joined back with one blank line. */
export function paragraphs(text: string): string[] {
  const out: string[] = []
  let fence = ''
  let piece: string[] = []
  for (const line of text.split('\n')) {
    const mark = line.match(/^\s*(`{3,}|~{3,})/)?.[1]
    if (mark && (!fence || (mark[0] === fence[0] && mark.length >= fence.length))) fence = fence ? '' : mark
    if (!fence && !mark && line.trim() === '') {
      if (piece.length) out.push(piece.join('\n'))
      piece = []
    } else {
      piece.push(line)
    }
  }
  if (piece.length) out.push(piece.join('\n'))
  return out
}

/** The overlay over a whole answer, a paragraph at a time: one request for
 *  a long answer came back cut or merged, and a paragraph seen before is
 *  not asked for again. */
export function useParagraphOverlay(text: string, on: boolean): string {
  return useOverlay(paragraphs(text), on).join('\n\n')
}

/** The overlay over a verified answer. The server refuses a rendering that
 *  changed a number or an identifier (`changed`), and any other failure
 *  (`failed`) leaves the English too: either way that paragraph's accepted
 *  original is shown, and the rest in Korean. A translation cannot add a fact
 *  to an answer that was checked. Found in the window: one refused paragraph
 *  of 24 once left the whole answer English.
 *
 *  Sent a paragraph at a time: asked for one multi-paragraph string, the
 *  translator has answered with one string per paragraph, and a reply of the
 *  wrong length is no reply. Split by `paragraphs`, so a fence stays whole. */
export type Fault = '' | 'changed' | 'failed'
const checkedMemory = new Map<string, { text: string; fault: Fault }>()
const SHOWN = ['translated', 'cached', 'skipped']

export function useCheckedOverlay(text: string, on: boolean): { text: string; fault: Fault } {
  const [, setTick] = useState(0)
  const wanted = on && text.trim() !== '' && !checkedMemory.has(text)

  useEffect(() => {
    if (!wanted) return
    let alive = true
    const parts = paragraphs(text)
    renderChecked(parts)
      .then((r) => {
        const statuses = parts.map((_, i) => r.statuses?.[i] ?? '')
        const fault: Fault = r.off
          ? ''
          : statuses.includes('meaning_changed')
            ? 'changed'
            : statuses.every((s) => SHOWN.includes(s))
              ? ''
              : 'failed'
        const shown = parts.map((p, i) => (SHOWN.includes(statuses[i]) ? r.texts[i] ?? p : p))
        checkedMemory.set(text, { text: r.off ? text : shown.join('\n\n'), fault })
      })
      .catch(() => checkedMemory.set(text, { text, fault: 'failed' }))
      .finally(() => alive && setTick((n) => n + 1))
    return () => {
      alive = false
    }
  }, [text, wanted])

  if (!on) return { text, fault: '' }
  return checkedMemory.get(text) ?? { text, fault: '' }
}
