import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Answer } from '@/components/Answer'
import type { AnswerProps } from '@/components/Answer'
import { KINDS } from '@/lib/api'
import type { Kind } from '@/lib/api'
import { useCheckedOverlay, useOverlay, useParagraphOverlay } from '@/lib/overlay'
import type { RunSummary, Verification } from '@/lib/api'
import type { Msg } from '@/components/Query'
import { RunDetails, RunProgress } from '@/components/Run'

type Props = {
  messages: Msg[]
  korean: boolean
  remote: string
  /** A citation's click, with the run whose answer cited it. */
  onPeek: (path: string, line: number, runId?: string) => void
  onDecide: AnswerProps['onDecide']
  onMark: (index: number, kind: Kind) => Promise<void>
  onStop: (runId: string) => void
  onMapRun: (run: RunSummary) => void
  /** The `next` focus: what an answer's blocks draw as, and the way to ask
   *  for candidates in an empty conversation. */
  blocks?: (m: Msg) => ReactNode
  empty?: ReactNode
}

export function Stream({ messages, korean, remote, onPeek, onDecide, onMark, onStop, onMapRun, blocks, empty }: Props) {
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
            위키에 물어라. 답의 근거 파일:줄 을 눌러 원문을 본다. 할 일이 정해지면
            다음 작업 초점에서 명세로 만든다.
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
                {m.pending && <RunProgress stage={m.stage} runId={m.runId} onStop={onStop} />}
                {m.tools.length > 0 && <Tools tools={m.tools} korean={korean} />}
                {m.text ? (
                  <AnswerVersions
                    m={m} korean={korean} remote={remote} onPeek={(p, l) => onPeek(p, l, m.runId)} onDecide={onDecide}
                  />
                ) : (
                  m.pending && <Blink />
                )}
                {m.cancelled ? <p role="status" className="text-[12.5px] text-muted-foreground">⏹ 멈춤 · 멈춘 질문은 아무것도 싣지 않는다</p>
                  : m.error && <p role="alert" className="text-[12.5px] text-destructive">{m.error}</p>}
                {!m.pending && m.runId && <RunDetails runId={m.runId} onPeek={(p, l) => onPeek(p, l, m.runId)} onMapRun={onMapRun} />}
                {blocks && m.blocks && m.blocks.length > 0 && blocks(m)}
                {!m.pending && (m.ms != null || m.marked) && (
                  <Foot m={m} onMark={(k) => onMark(i, k)} />
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
  const plain = useParagraphOverlay(m.text, korean && !m.pending && !m.verification)
  const checked = useCheckedOverlay(m.text, korean && !m.pending && Boolean(m.verification))
  // What was verified is the English. The Korean passed checks on numbers,
  // identifiers and negation, which a swapped or reversed sentence can still
  // pass, so it is labelled a translation and the English is one click away.
  const [english, setEnglish] = useState(false)
  const translated = Boolean(m.verification) && checked.text !== m.text
  // An analysis, or an answer shown while verification was down, goes through
  // the same checked overlay but was never verified: its English is only the original.
  const original = m.verification?.verified ? '검증된 영어 원문' : '영어 원문'
  const text = m.verification ? (english ? m.text : checked.text) : plain
  return (
    <div className="space-y-3">
      {m.verification && <Checked v={m.verification} />}
      {checked.fault && (
        <p role="status" className="text-[12.5px] text-muted-foreground">
          {checked.fault === 'changed'
            ? `표시 오류 · 한국어로 옮기며 숫자나 식별자가 바뀐 문단은 ${original} 그대로 보인다.`
            : `표시 오류 · 한국어 번역을 받지 못한 문단은 ${original} 그대로 보인다.`}
        </p>
      )}
      {translated && !simple && (
        <p role="status" className="flex flex-wrap items-center gap-x-2 text-[12.5px] text-muted-foreground">
          {english ? `${original}이다.`
            : m.verification?.verified ? '번역 · 검증된 것은 영어 원문이며, 이 번역은 뜻까지 대조하지 않았다.'
              : '번역 · 영어 원문을 옮긴 것이며, 이 번역은 뜻까지 대조하지 않았다.'}
          <button type="button" aria-pressed={english} onClick={() => setEnglish(!english)}
            className="min-h-9 rounded-md px-2 underline underline-offset-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
            {english ? '번역 보기' : '영어 원문 보기'}
          </button>
        </p>
      )}
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
          <p className="text-[12.5px] text-muted-foreground">{m.verification?.verified
            ? '쉽게 풀어 쓴 설명이며, 검증하지 않았다. 검증된 내용은 ‘정확한 답변’의 영어 원문이다.'
            : m.verification
              ? '쉽게 풀어 쓴 설명이다. 원문도 검증되지 않았으며, ‘정확한 답변’에 영어 원문이 있다.'
              : '같은 내용을 쉽게 풀었습니다. 근거와 조건은 원문에서 함께 확인할 수 있습니다.'}</p>
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

/** How the answer above was checked, as the server recorded it. Only the
 *  claims that passed were published; this says what that means here. */
const CHECKED: Record<Verification['status'], string> = {
  complete: '검증됨 · 질문의 모든 부분이 인용한 근거로 확인됐다',
  partial: '부분 답 · 근거로 확인된 부분만 싣는다',
  abstained: '답 보류 · 찾은 근거로는 확인되는 답이 없다',
  verification_unavailable: '검증 불가 · 근거 대조를 하지 못했다',
  unverified: '분석 (미검증) · 비교·판단을 물어서, 근거와 대조하지 않은 에이전트의 해석이다',
}

function Checked({ v }: { v: Verification }) {
  // An answer that cites nothing was checked against the conversation, not a source — whole or in part.
  const conversational = (v.status === 'complete' || v.status === 'partial') && !v.citations?.length
  const sourced = v.status === 'complete' && !conversational && !v.host_checked
  const line = v.degraded ? '미검증 답 · 검증이 안 돼 기본 모드로 싣는다'
    : conversational
      ? `${v.status === 'complete' ? '대화로 답함' : '대화로 일부 답함'} · 출처를 찾지 않았고, 대화에 있던 내용만 옮겼다`
      : v.host_checked
        ? `${v.status === 'complete' ? '모델 확인' : v.status === 'partial' ? '모델 확인 · 부분 답' : CHECKED[v.status]} · Jev 가 확신하지 못한 부분은 답하는 모델이 근거와 대조했다 (Jev 검증 아님)`
        : CHECKED[v.status]
  const missing = v.status === 'complete' ? 0 : v.missing_requirements.length
  return (
    <div role="status" className={`font-mono text-[10.5px] leading-snug ${sourced ? 'text-primary/80' : 'text-muted-foreground'}`}>
      {line}
      {missing > 0 && ` · 확인 못 한 부분 ${missing}`}
      {v.conflicts.length > 0 && ` · 근거와 충돌 ${v.conflicts.length}`}
    </div>
  )
}

/** The line under an answer: time, cost, model, and "that was wrong". */
function Foot({ m, onMark }: { m: Msg; onMark: (k: Kind) => Promise<void> }) {
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
