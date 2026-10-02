import { useEffect, useMemo, useState } from 'react'
import * as api from '@/lib/api'
import type { Peek as PeekData } from '@/lib/api'
import { cn } from '@/lib/utils'

/** `note`: why what is shown is not what was cited — the file changed since. */
type Props = { data: PeekData | null; error?: string; note?: string; where?: string; onClose: () => void }

/** Read the cited place in a side drawer, leaving the conversation where it is.
 *
 *  While the pages move to English this is where a person reads them in
 *  Korean. The original is never replaced: the source line stays, and the
 *  rendering stands under it. Path and line number are the coordinates the
 *  citation points at, so those are never touched. */
export function Peek({ data, error, note, where, onClose }: Props) {
  const blocks = useMemo(() => (data ? chop(data) : []), [data])
  const [korean, setKorean] = useState(false)
  const [said, setSaid] = useState<Record<number, string>>({})
  const [busy, setBusy] = useState(false)
  const [fault, setFault] = useState('')

  // A new file drops the rendering with it. Kept, it would sit under another
  // file's lines — worse than wrong, because it looks right.
  useEffect(() => {
    setSaid({})
    setKorean(false)
    setFault('')
  }, [data?.path, data?.line])

  useEffect(() => {
    if (!korean || !blocks.length) return
    let stale = false
    setBusy(true)
    api
      .render(blocks.map((b) => b.text))
      .then(({ texts }) => {
        if (!stale) setSaid(Object.fromEntries(blocks.map((b, n) => [b.at, texts[n]])))
      })
      .catch(() => !stale && setFault('번역이 실패했다 — 원문만 보인다'))
      .finally(() => !stale && setBusy(false))
    return () => {
      stale = true
    }
  }, [korean, blocks])

  if (!data && !error && !note) return null
  const prose = blocks.length > 0

  return (
    <aside className="file-peek flex w-[min(34rem,55%)] shrink-0 flex-col border-l border-border bg-card">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="min-w-0 truncate font-mono text-[12px]">
          {data ? `${data.path}:${data.line}` : where ?? '…'}
          {data && <span className="ml-2 text-faint">{data.total}줄</span>}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {prose && (
            <button
              type="button"
              onClick={() => setKorean((on) => !on)}
              aria-pressed={korean}
              className={cn(
                'rounded border border-border px-2 py-0.5 text-[12.5px] transition-colors',
                korean
                  ? 'bg-secondary text-foreground'
                  : 'text-muted-foreground hover:bg-secondary',
              )}
              title="원문은 그대로 두고 한국어를 아래에 붙인다"
            >
              {busy ? '옮기는 중' : '한국어'}
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className="rounded px-2 py-0.5 text-[12.5px] text-muted-foreground hover:bg-secondary"
          >
            닫기
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-auto">
        {note && <p role="status" className="border-b border-border px-3 py-2 text-[12.5px] text-muted-foreground">{note}</p>}
        {error && <p className="p-3 text-[12.5px] text-destructive">{error}</p>}
        {fault && <p className="px-3 pt-2 text-[12.5px] text-destructive">{fault}</p>}
        {data && (
          <pre className="p-3 font-mono text-[12px] leading-[1.55]">
            {data.lines.map((l, i) => {
              const n = data.start + i
              const hit = n === data.line
              const ko = korean ? said[n] : undefined
              return (
                <div key={n}>
                  <div className={hit ? '-mx-3 bg-primary/10 px-3' : undefined}>
                    <span className="mr-3 inline-block w-8 select-none text-right text-faint">
                      {n}
                    </span>
                    {l || ' '}
                  </div>
                  {ko && (
                    <div className="-mx-3 border-l-2 border-primary/40 bg-secondary/40 px-3 py-1 pl-[3.25rem] font-sans text-[12.5px] leading-snug whitespace-pre-wrap text-muted-foreground">
                      {ko}
                    </div>
                  )}
                </div>
              )
            })}
          </pre>
        )}
      </div>
    </aside>
  )
}

/** A chunk to send. `at` is the source line the chunk started on.
 *
 *  Not line by line: a fragment translates badly and costs a request of its
 *  own. Paragraphs go out whole and the answer lands under the paragraph's
 *  first line. Fenced code is not sent at all — it is not prose. */
function chop(data: PeekData): { at: number; text: string }[] {
  if (!data.path.toLowerCase().endsWith('.md')) return []
  const out: { at: number; text: string }[] = []
  let fenced = false
  let held: string[] = []
  let at = 0

  const flush = () => {
    if (held.length) out.push({ at, text: held.join('\n') })
    held = []
  }

  data.lines.forEach((line, i) => {
    const n = data.start + i
    if (line.trim().startsWith('```')) {
      flush()
      fenced = !fenced
      return
    }
    if (fenced || !line.trim() || !/[A-Za-z가-힣]/.test(line)) {
      flush()
      return
    }
    if (!held.length) at = n
    held.push(line)
  })
  flush()
  return out
}
