import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Answer } from '@/components/Answer'
import type { AnswerProps } from '@/components/Answer'
import { KINDS } from '@/lib/api'
import type { Kind } from '@/lib/api'
import { useOverlay } from '@/lib/overlay'
import type { Msg } from '@/components/Query'

type Props = {
  messages: Msg[]
  korean: boolean
  remote: string
  onPeek: AnswerProps['onPeek']
  onDecide: AnswerProps['onDecide']
  onMark: (index: number, kind: Kind) => Promise<void>
  onDraft: (index: number) => void
  /** The `next` focus: what an answer's blocks draw as, and the way to ask
   *  for candidates in an empty conversation. */
  blocks?: (m: Msg) => ReactNode
  empty?: ReactNode
}

export function Stream({ messages, korean, remote, onPeek, onDecide, onMark, onDraft, blocks, empty }: Props) {
  const end = useRef<HTMLDivElement>(null)

  // The answer grows in pieces, so this follows every change in length
  // rather than every new message.
  const grown = messages.length + (messages.at(-1)?.text.length ?? 0) + (messages.at(-1)?.simpleText?.length ?? 0)
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [grown])

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-6">
        {messages.length === 0 && empty}
        {messages.length === 0 && !empty && (
          <p className="text-[13.5px] text-faint">
            위키에 물어라. 답의 근거 파일:줄 을 눌러 원문을 보고, 답 아래
            “→ 작업” 으로 그 근거를 작업트리의 에이전트에게 넘긴다.
          </p>
        )}

        <div className="space-y-6">
          {messages.map((m, i) =>
            m.role === 'result' ? (
              <div key={i} className="border-l-2 border-primary pl-2.5 text-[12.5px] text-primary">{m.text}</div>
            ) : m.role === 'user' ? (
              <div key={i} className="flex justify-end">
                <div className="max-w-[85%] rounded-lg rounded-br-sm bg-secondary px-3.5 py-2 text-[13.5px] leading-relaxed whitespace-pre-wrap">
                  {m.text}
                </div>
              </div>
            ) : (
              <div key={i} className="space-y-2">
                {m.source && (
                  <span className="inline-block rounded bg-secondary px-1.5 py-0.5 font-heading text-[10.5px] text-muted-foreground">
                    {m.source === 'standup' ? '아침 브리핑' : m.source === 'retro' ? '금요 회고' : m.source}
                  </span>
                )}
                {m.hits && m.hits.length > 0 && (
                  <div className="font-mono text-[10.5px] leading-snug text-primary/80">
                    관련 규칙 · {m.hits.join(' · ')}
                  </div>
                )}
                {m.tools.length > 0 && <Tools tools={m.tools} korean={korean} />}
                {m.text ? (
                  <AnswerVersions
                    m={m} korean={korean} remote={remote} onPeek={onPeek} onDecide={onDecide}
                  />
                ) : (
                  m.pending && <Blink />
                )}
                {m.error && <p className="text-[12.5px] text-destructive">{m.error}</p>}
                {blocks && m.blocks && m.blocks.length > 0 && blocks(m)}
                {!m.pending && (m.ms != null || m.marked) && (
                  <Foot m={m} onMark={(k) => onMark(i, k)} onDraft={() => onDraft(i)} />
                )}
              </div>
            ),
          )}
        </div>
        <div ref={end} />
      </div>
    </div>
  )
}

/** The tool lines. Translated, because they are the agent describing itself.
 *
 *  What a tool *ran* never comes through here — `chat_session` already
 *  reduced the call to its one-line description, which is the sentence the
 *  person reads. */
function Tools({ tools, korean }: { tools: string[]; korean: boolean }) {
  const shown = useOverlay(tools, korean)
  return (
    <ul className="space-y-0.5">
      {shown.map((t, j) => (
        <li key={j} className="font-mono text-[12px] leading-snug text-faint">
          · {t}
        </li>
      ))}
    </ul>
  )
}

function AnswerVersions(
  { m, korean, ...props }: { m: Msg; korean: boolean } & Omit<AnswerProps, 'text'>,
) {
  const [simple, setSimple] = useState(false)
  const available = m.simpleText !== undefined || m.simplePending || Boolean(m.simpleError)
  // Only the answer, and only once it is finished. The plain explanation is
  // written in Korean by `chat-explain.md` — a different feature from this
  // one — so translating it again would pay twice for the same words and
  // reword what that prompt deliberately said.
  //
  // `!m.pending` is what keeps this from translating a partial answer on
  // every chunk. Measured before it was added: one answer made three
  // requests, each for a prefix that was about to be replaced. A cut
  // translation also reads exactly like a whole one, so a half-streamed
  // paragraph is the wrong thing to render Korean.
  const [text] = useOverlay([m.text], korean && !m.pending)
  return (
    <div className="space-y-3">
      {available && (
        <div role="group" aria-label="답변 보기" className="inline-flex gap-1 rounded-lg border border-border p-1">
          {[{ value: false, label: '1. 정확한 답변' }, { value: true, label: '2. 쉬운 설명' }].map((option) => (
            <button key={option.label} type="button" aria-pressed={simple === option.value}
              onClick={() => setSimple(option.value)}
              className={`min-h-9 rounded-md px-3 text-[12.5px] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${simple === option.value ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:bg-secondary/50'}`}>
              {option.label}
            </button>
          ))}
        </div>
      )}
      {simple ? (
        <div className="space-y-2">
          <p className="text-[12.5px] text-muted-foreground">같은 내용을 쉽게 풀었습니다. 근거와 조건은 원문에서 함께 확인할 수 있습니다.</p>
          {m.simpleText && (
            <Answer text={m.simpleText} korean={korean} {...props} onDecide={undefined} />
          )}
          {m.simplePending && <p role="status" className="text-[12.5px] text-muted-foreground">의미와 조건을 유지하며 쉽게 풀어 쓰는 중…</p>}
          {m.simpleError && <p role="alert" className="text-[12.5px] text-destructive">쉬운 설명을 만들지 못했습니다. ‘정확한 답변’에서 원문을 볼 수 있습니다. {m.simpleError}</p>}
        </div>
      ) : <Answer text={text} korean={korean} {...props} />}
      {!simple && m.simplePending && <p role="status" className="text-[12.5px] text-muted-foreground">원문을 읽는 동안 쉬운 설명을 준비하고 있습니다.</p>}
    </div>
  )
}

/** The line under an answer: time, cost, model, "that was wrong", and the
 *  way over to a worktree's agent. */
function Foot({ m, onMark, onDraft }: { m: Msg; onMark: (k: Kind) => Promise<void>; onDraft: () => void }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  const bits: string[] = []
  if (m.ms != null) bits.push(`원문 ${(m.ms / 1000).toFixed(1)}s`)
  if (m.cost != null) bits.push(`$${m.cost.toFixed(3)}`)
  if (m.tokens) {
    const t = m.tokens
    const io = `${fmt(t.in)}→${fmt(t.out)}`
    const cache = t.cache_read ? ` · 캐시 ${fmt(t.cache_read)}` : ''
    bits.push(io + cache)
  }
  if (m.model) bits.push(m.model.replace('claude-', ''))
  if (m.simpleMs != null) bits.push(`쉬운 설명 ${(m.simpleMs / 1000).toFixed(1)}s`)
  if (m.simpleCost != null) bits.push(`설명 $${m.simpleCost.toFixed(3)}`)

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10.5px] text-faint">
      <span>{bits.join(' · ')}</span>
      {m.text && !m.error && (
        <button
          type="button"
          onClick={onDraft}
          className="rounded px-1.5 font-sans text-[12.5px] text-primary hover:bg-secondary"
          title="이 답의 근거를 담은 지시 초안을 에이전트 입력칸에 넣는다"
        >
          → 작업
        </button>
      )}
      {m.marked ? (
        <span className="text-destructive">어긋남 · {m.marked}</span>
      ) : open ? (
        <span className="flex items-center gap-1">
          {KINDS.map((k) => (
            <button
              key={k}
              type="button"
              disabled={busy}
              onClick={async () => {
                setBusy(true)
                try {
                  await onMark(k)
                } finally {
                  setBusy(false)
                  setOpen(false)
                }
              }}
              className="rounded border border-border px-1.5 py-[1px] hover:bg-secondary disabled:opacity-40"
            >
              {k}
            </button>
          ))}
          <button type="button" onClick={() => setOpen(false)} className="px-1 hover:text-foreground">
            취소
          </button>
        </span>
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="rounded px-1 hover:bg-secondary hover:text-foreground"
          title="틀린 그 순간에 부류를 찍는다. census 형식으로 쌓인다."
        >
          어긋났다
        </button>
      )}
    </div>
  )
}

function fmt(n?: number) {
  if (n == null) return '?'
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

function Blink() {
  return (
    <span className="inline-block h-3.5 w-1.5 animate-pulse rounded-[1px] bg-faint align-middle" />
  )
}
