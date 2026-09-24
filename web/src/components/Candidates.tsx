import { useState } from 'react'
import { useOverlay } from '@/lib/overlay'

type Target = 'wiki' | 'claude_md' | 'drop'
type Props = {
  raw: string
  korean: boolean
  onDecide: (candidate: string, target: Target) => Promise<string>
}

type State = { busy?: Target; result?: string; done?: Target }

/** Three buttons per candidate the retro produced.
 *
 *  Step 5 of the `retrospect` skill says to ask with options, and a headless
 *  run cannot ask. This is where it asks. Nothing on disk changes here: the
 *  first two put an instruction draft in the agent's box, and the writing
 *  happens in a worktree where each write is approved. */
export function Candidates({ raw, korean, onDecide }: Props) {
  const lines = raw
    .split('\n')
    .map((l) => l.replace(/^[-*]\s*/, '').trim())
    .filter(Boolean)
  // Shown translated, decided on the original. What a button sends becomes a
  // rule on a page, and pages are English — handing `decide` a rendering
  // would write a translation of a translation into the wiki.
  const shown = useOverlay(lines, korean)
  const [state, setState] = useState<Record<number, State>>({})

  if (lines.length === 0) {
    return <p className="text-[12.5px] text-faint">위키 갱신 후보 없음.</p>
  }

  async function act(i: number, target: Target) {
    setState((s) => ({ ...s, [i]: { ...s[i], busy: target } }))
    try {
      const text = await onDecide(lines[i], target)
      setState((s) => ({ ...s, [i]: { result: text, done: target } }))
    } catch (err) {
      setState((s) => ({ ...s, [i]: { result: String(err) } }))
    }
  }

  const label: Record<Target, string> = {
    wiki: '위키로',
    claude_md: 'CLAUDE.md 로',
    drop: '버린다',
  }
  const doing: Record<Target, string> = {
    wiki: '초안을 만드는 중…',
    claude_md: '초안을 만드는 중…',
    drop: '…',
  }

  return (
    <div className="not-prose my-2 space-y-2 rounded-md border border-border bg-card p-3">
      <div className="font-heading text-[11px] text-faint">위키 갱신 후보 — 무엇이 될지는 네가 정한다</div>
      {shown.map((line, i) => {
        const s = state[i] ?? {}
        return (
          <div key={i} className="space-y-1.5">
            <div className="text-[13.5px] leading-snug">{line}</div>
            {s.done ? (
              <div className="text-[12.5px] text-primary">→ {label[s.done]}</div>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {(['wiki', 'claude_md', 'drop'] as Target[]).map((t) => (
                  <button
                    key={t}
                    type="button"
                    disabled={!!s.busy}
                    onClick={() => act(i, t)}
                    className="rounded border border-border bg-background px-2 py-0.5 text-[12.5px] hover:bg-secondary disabled:opacity-40"
                  >
                    {s.busy === t ? doing[t] : label[t]}
                  </button>
                ))}
              </div>
            )}
            {s.result && (
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-secondary p-2 text-[12.5px] leading-snug">
                {s.result}
              </pre>
            )}
          </div>
        )
      })}
    </div>
  )
}
