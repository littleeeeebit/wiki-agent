import { useEffect, useRef, useState } from 'react'
import { Eraser } from 'lucide-react'
import { Answer } from '@/components/Answer'
import { Questions } from '@/components/Questions'
import type { Asked } from '@/components/Questions'
import { LiveChanges } from '@/components/LiveChanges'
import { ProviderUsage } from '@/components/ProviderUsage'
import { Composer } from '@/components/Composer'
import { Btn, ClearAsk } from '@/components/Modal'
import { Toolbar } from '@/components/Toolbar'
import type { Choice } from '@/components/Toolbar'
import type { Keep, Kept, Options, Rule, Worktree } from '@/lib/api'
import { useOverlay, useParagraphOverlay } from '@/lib/overlay'
import type { Step, Turn } from '@/lib/work'
import { cn } from '@/lib/utils'

type Props = {
  row: Worktree | undefined
  turns: Turn[]
  options: Options | null
  choice: Choice
  optionsOpen: boolean
  on: boolean
  onChoice: (c: Choice) => void
  onSend: (text: string) => void
  onAnswer: (turn: Turn, id: string, allow: boolean, scope?: 'once' | 'session', answers?: string[]) => void
  onStop: (turn: Turn) => void
  onSteer: (turn: Turn, text: string) => void
  /** The next instruction, waiting for this run to end. */
  queued?: string
  onQueue: (turn: Turn, text: string) => void
  /** Instructions the server would not keep waiting, each with why. */
  refused?: { text: string; reason: string }[]
  onDismiss: (at: number) => void
  onUnqueue: () => void
  rules: Rule[]
  onClearRules: () => void
  onReset: (keep: Keep) => Promise<Kept>
  onPeek: (path: string, line: number) => void
}

/** The selected worktree's agent. Its answers get the Korean overlay; what it
 *  ran and what it asks to write stay as they are, because a person approves
 *  those and a reworded command is not the command. */
export function Agent({
  row, turns, options, choice, optionsOpen, on, onChoice, onSend, onAnswer, onStop, onSteer, queued, onQueue, onUnqueue, refused,
  onDismiss, rules, onClearRules, onReset, onPeek,
}: Props) {
  const end = useRef<HTMLDivElement>(null)
  const [asking, setAsking] = useState(false)
  const [note, setNote] = useState('')
  // Put back in the box only when the person asks: what they type meanwhile is theirs.
  const [seed, setSeed] = useState<{ text: string } | null>(null)
  const busy = turns.at(-1)?.pending ?? false
  const last = turns.at(-1)
  const grown = turns.length + (last?.text.length ?? 0) + (last?.steps.length ?? 0)
  useEffect(() => {
    const question = [...(end.current?.parentElement?.querySelectorAll('[data-question-pending="true"]') ?? [])].at(-1)
    if (question) question.scrollIntoView({ block: 'start' })
    else end.current?.scrollIntoView({ block: 'end' })
  }, [grown])

  return (
    <section aria-label="에이전트 세션" className="flex h-full min-h-0 flex-col">
      <header className="agent-header shrink-0 border-b border-border px-5">
        {/* The model and effort stand here before there is a worktree too:
            a spec's [시작] runs its first turn on them. */}
        <div className="agent-toolbar flex h-11 items-center justify-end gap-1.5">
          <span className="agent-model-label mr-auto truncate font-heading text-[11px] font-semibold text-faint">작업 모델</span>
          {row && busy && last?.turn && (
            <Btn tone="danger" onClick={() => onStop(last)} title="도는 턴을 멈춘다. 대화는 남아 다음 지시가 이어진다">
              멈춤
            </Btn>
          )}
          <div id="agent-model-options" className="agent-options flex items-center gap-1.5" data-open={optionsOpen}>
            <Toolbar value={choice} options={options} busy={busy} onChange={onChoice} />
            {row && (
              <Btn tone="ghost" className="px-1.5" onClick={() => setAsking(true)}
                disabled={busy} aria-label="문맥 비우기"
                title="문맥 비우기 — 이 작업트리의 대화를 새로 시작한다. 지금 대화는 메모리로 남기거나 지운다">
                <Eraser className="size-4" />
              </Btn>
            )}
          </div>
        </div>
        {row && rules.length > 0 && (
          <div className="-mt-1 flex items-center gap-2 pb-2 text-[12.5px] text-muted-foreground">
            <span className="min-w-0 truncate">
              세션 허용:{' '}
              {rules.map((r, i) => (
                <span key={i}>
                  {i > 0 && ' · '}
                  {r.kind === 'command' ? <code className="font-mono text-[12px]">{r.command}</code> : r.tool}
                </span>
              ))}
            </span>
            <button
              type="button"
              onClick={onClearRules}
              className="shrink-0 rounded px-1.5 text-[12.5px] text-primary hover:bg-secondary"
            >
              해제
            </button>
          </div>
        )}
      </header>

      <div className="agent-options shrink-0" data-open={optionsOpen}>
        <ProviderUsage key={`${choice.model.startsWith('codex:')}:${row?.path ?? ''}`} model={choice.model} path={row?.path} />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {row && <div className="agent-changes sticky top-0 z-10 bg-background px-4 py-2">
          <LiveChanges key={`${row.path}:${row.branch}`} path={row.path} busy={busy} turn={last?.turn} />
        </div>}
        <div className="space-y-5 px-4 py-4">
          {!row && (
            <p className="text-[13.5px] text-faint">
              명세의 [시작]이 선택한 저장소에 작업 브랜치를 만들고 첫 턴을 보낸다. 에이전트는 전체 접근 권한으로 작업한다.
            </p>
          )}
          {row && turns.length === 0 && (
            <p className="text-[13.5px] text-faint">지시를 보내라. 도는 동안에도 보내면 그 턴에 끼어든다.</p>
          )}
          {note && turns.length === 0 && <p className="text-[12.5px] text-muted-foreground">{note}</p>}
          {turns.map((t) =>
            t.role === 'user' ? (
              <div key={t.key} className="flex justify-end">
                <div className="max-w-[90%] rounded-lg rounded-br-sm bg-secondary px-3 py-2 text-[13.5px] leading-relaxed whitespace-pre-wrap">
                  {t.text}
                </div>
              </div>
            ) : (
              <Reply key={t.key} turn={t} on={on} onAnswer={onAnswer} onPeek={onPeek} />
            ),
          )}
          {queued && (
            <div className="flex flex-col items-end gap-1">
              <div className="max-w-[90%] rounded-lg rounded-br-sm border border-dashed border-border px-3 py-2 text-[13.5px] leading-relaxed whitespace-pre-wrap text-muted-foreground">
                {queued}
              </div>
              <span className="text-[11.5px] text-faint">
                대기 · 이 턴이 끝나면 보낸다{' '}
                <button type="button" onClick={onUnqueue} className="rounded px-1 text-primary hover:bg-secondary">취소</button>
              </span>
            </div>
          )}
          <div ref={end} />
        </div>
      </div>

      {/* While a turn runs, what is sent goes into that turn: the agent reads
          it between steps. Until the server names the turn there is nothing to
          send it to. Once it has answered, the agent reads nothing more — the
          gate is running — so it waits and goes as the next instruction. */}
      {refused?.map((r, i) => (
        <div key={i} role="alert" className="mx-4 mb-1 rounded-md border border-destructive/40 px-3 py-2 text-[12.5px]">
          <p className="text-destructive">대기 실패 · {r.reason}</p>
          <p className="mt-1 whitespace-pre-wrap text-muted-foreground">{r.text}</p>
          <div className="mt-1 flex gap-1">
            <Btn tone="ghost" onClick={() => {
              setSeed({ text: r.text })
              onDismiss(i)
            }}>입력칸에 넣기</Btn>
            <Btn tone="ghost" onClick={() => onDismiss(i)}>버리기</Btn>
          </div>
        </div>
      ))}
      {/* One waits at a time: a second would only be refused, so it stays in the box. */}
      <Composer
        busy={busy && (!last?.turn || (last.answered != null && !!queued))}
        disabled={!row}
        max={320}
        seed={seed}
        placeholder={!busy ? '지시를 입력하세요'
          : last?.answered != null
            ? queued ? '지시 하나가 이미 기다린다. 그것을 취소하면 이것을 보낼 수 있다.'
              : '답은 끝났고 마무리가 도는 중이다. 보내면 끝난 뒤 다음 지시로 보낸다.'
            : '도는 턴에 끼어든다. 에이전트가 다음 걸음 전에 읽는다.'}
        onSend={(text) => (!busy || !last ? onSend(text)
          : last.answered != null ? onQueue(last, text) : onSteer(last, text))}
      />
      {asking && <ClearAsk onClear={onReset} onClose={(said) => {
        setAsking(false)
        setNote(said)
      }} />}
    </section>
  )
}

export function Reply({ turn, on, onAnswer, onPeek }: {
  turn: Turn
  on: boolean
  onAnswer: Props['onAnswer']
  onPeek: Props['onPeek']
}) {
  // Only once the answer is finished: a half-streamed paragraph translated
  // reads exactly like a whole one.
  const text = useParagraphOverlay(turn.text, on && (!turn.pending || turn.answered != null))
  // The steps after the answer — the gate — stand after it, in the order they ran.
  const cut = turn.answered ?? turn.steps.length
  const lastStep = turn.steps.at(-1)
  // What is running is the latest thing that came: a step gets the marker
  // under it, text or nothing yet gets it at the end.
  const marker = turn.pending && (
    <Running since={turn.since}
      label={lastStep?.kind === 'approval' && lastStep.answer === undefined && turn.latest === 'step' ? '답을 기다린다'
        : turn.latest !== 'step' && turn.answered != null ? '마무리 중' : '실행 중'} />
  )
  const list = (from: number, to: number) => to > from && (
    <ul className="space-y-1.5">
      {turn.steps.slice(from, to).map((s, j) => (
        <li key={from + j}>
          {s.kind === 'progress' ? <Progress text={s.text} on={on} onPeek={onPeek} />
            : s.kind === 'tool' ? <Tool text={s.text} on={on && !s.command} />
            : s.kind === 'said' ? <Said text={s.text} />
              : s.kind === 'hook' ? <Hook text={s.text} context={s.context} />
                : QUESTIONS.has(s.tool) ? <Question step={s} turn={turn} korean={on} onAnswer={onAnswer} />
                  : <Ask step={s} turn={turn} onAnswer={onAnswer} />}
          {turn.latest === 'step' && from + j === turn.steps.length - 1 && marker}
        </li>
      ))}
    </ul>
  )
  return (
    <div className="space-y-2">
      {list(0, cut)}
      {turn.text && <Answer text={text} korean={on} remote="" onPeek={onPeek} />}
      {list(cut, turn.steps.length)}
      {turn.latest !== 'step' && marker}
      {turn.error && <p role="alert" className="text-[12.5px] text-destructive">{turn.error}</p>}
      {!turn.pending && turn.ms != null && (
        <p className="font-mono text-[10.5px] text-faint">
          {[`${(turn.ms / 1000).toFixed(1)}s`, turn.cost != null && `$${turn.cost.toFixed(3)}`, tokens(turn),
            turn.model].filter(Boolean).join(' · ')}
        </p>
      )}
    </div>
  )
}

/** The same shape as the query pane's: in→out, and what came from the cache. */
function tokens(turn: Turn): string {
  const t = turn.tokens
  if (!t || (t.in == null && t.out == null)) return ''
  const n = (v?: number) => (v == null ? '?' : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v))
  return `${n(t.in)}→${n(t.out)}${t.cache_read ? ` · 캐시 ${n(t.cache_read)}` : ''}`
}

/** "⟳ 실행 중 · 3분 12초": nothing newer has come, and the run has not ended,
 *  so this is what is running and for how long. The same on both hosts: it
 *  reads only the events' arrival. */
function Running({ since, label }: { since?: number; label: string }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  const s = Math.max(0, Math.floor((now - (since ?? now)) / 1000))
  return (
    <p role="status" className="mt-0.5 flex items-center gap-1 font-mono text-[11.5px] text-primary">
      <span aria-hidden className={cn('inline-block', label !== '답을 기다린다' && 'motion-safe:animate-spin')}>⟳</span>
      {label} · {s >= 60 ? `${Math.floor(s / 60)}분 ${s % 60}초` : `${s}초`}
    </p>
  )
}

function Progress({ text, on, onPeek }: { text: string; on: boolean; onPeek: Props['onPeek'] }) {
  const shown = useParagraphOverlay(text, on)
  return <Answer text={shown} korean={on} remote="" onPeek={onPeek} />
}

function Tool({ text, on }: { text: string; on: boolean }) {
  // Claude supplies a description before the command. Only that prose is mirrored.
  const at = text.indexOf(' · $ ')
  const [shown] = useOverlay([at < 0 ? text : text.slice(0, at)], on)
  return <p className="whitespace-pre-wrap break-words font-mono text-[12px] leading-snug text-faint">· {shown}{at < 0 ? '' : text.slice(at)}</p>
}

/** A hook that spoke — the wiki's injection, its auto-update. What it put into
 *  the agent's context folds under it, as it went in. */
function Hook({ text, context }: { text: string; context?: string }) {
  return (
    <div className="font-mono text-[12px] leading-snug text-faint">
      <p className="whitespace-pre-wrap">↳ {text}</p>
      {context && (
        <details className="pl-3">
          <summary className="cursor-pointer hover:text-muted-foreground">주입된 문맥 {context.length.toLocaleString()}자</summary>
          <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap break-all">{context}</pre>
        </details>
      )}
    </div>
  )
}

/** The two hosts' question tools: Claude's `AskUserQuestion`, Codex's `request_user_input`. */
const QUESTIONS = new Set(['AskUserQuestion', 'requestUserInput'])

/** Keep the question input for replay; submit original option labels. */
function Question({ step, turn, korean, onAnswer }: {
  step: Extract<Step, { kind: 'approval' }>
  turn: Turn
  korean: boolean
  onAnswer: Props['onAnswer']
}) {
  const asked = (Array.isArray(step.input.questions) ? step.input.questions : []) as Asked[]
  const open = step.answer === undefined
  return (
    <div data-question-pending={open && turn.pending ? 'true' : undefined}
      className={cn('rounded-lg border p-4', open ? 'border-wait bg-wait/10' : 'border-border bg-secondary/40')}>
      <p className="mb-4 text-[14px] font-semibold">{open ? '에이전트 질문' : step.answer ? '답함' : '답하지 않음'}</p>
      {open ? <Questions questions={asked} korean={korean} disabled={!turn.pending || !!step.sending}
        onSubmit={(answers) => onAnswer(turn, step.id, true, 'once', answers)}
        onDecline={() => onAnswer(turn, step.id, false)} />
        : <div className="space-y-2 text-[14px] leading-relaxed">
          {asked.map((q, i) => <AnsweredQuestion key={i} question={q.question} answer={step.answers?.[i]} korean={korean} />)}
          {!asked.length && <p className="whitespace-pre-wrap">{step.text}</p>}
        </div>}
      {step.error && <p role="alert" className="mt-3 text-[14px] text-destructive">{step.error}</p>}
      {open && !turn.pending && <p className="mt-3 text-[14px] text-faint">이 턴은 끝났다. 물은 프로세스가 없어 답할 수 없다.</p>}
    </div>
  )
}

function AnsweredQuestion({ question, answer, korean }: { question: string; answer?: string; korean: boolean }) {
  const [shown] = useOverlay([question], korean)
  // The submitted answer may be the person's own prose, so it is not translated.
  return <p>{shown}{answer && <span className="block text-muted-foreground">→ {answer}</span>}</p>
}

/** What the person said while the turn ran, where in the turn it landed. */
function Said({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[90%] rounded-lg rounded-br-sm bg-secondary px-3 py-1.5 text-[13px] leading-relaxed whitespace-pre-wrap">
        {text}
      </div>
    </div>
  )
}

/** How an answered card reads, by who answered it. */
const ANSWERED: Record<string, [string, string]> = {
  person: ['허용함', '거절함'],
  session: ['세션 동안 허용', '거절함'],
  outside: ['허용함', '거절 · 작업트리 밖'],
  read: ['허용함', '거절 · 읽기 세션'],
}

/** One write the agent wants to make. What it will do is shown as it will be
 *  done — the command, the path, the content — and nothing happens until a
 *  person presses one of the buttons. */
function Ask({ step, turn, onAnswer }: {
  step: Extract<Step, { kind: 'approval' }>
  turn: Turn
  onAnswer: Props['onAnswer']
}) {
  const detail = describe(step)
  const open = step.answer === undefined
  return (
    <div className={cn('rounded-md border p-2.5', open ? 'border-wait bg-wait/10' : 'border-border bg-secondary/40')}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-heading text-[11px] font-semibold">
          {open ? '쓰기 허용?' : ANSWERED[step.by ?? 'person'][step.answer ? 0 : 1]}
          <span className="ml-1.5 font-mono text-[10.5px] font-normal text-muted-foreground">{step.tool}</span>
        </span>
        {open && (
          <span className="flex gap-1.5">
            <button
              type="button"
              disabled={step.sending || !turn.pending}
              onClick={() => onAnswer(turn, step.id, true)}
              className="rounded-md bg-wait px-2.5 py-1 text-[12.5px] text-wait-foreground hover:opacity-90 disabled:opacity-40"
            >
              허용
            </button>
            {step.session && (
              <button
                type="button"
                disabled={step.sending || !turn.pending}
                onClick={() => onAnswer(turn, step.id, true, 'session')}
                className="rounded-md border border-wait px-2.5 py-1 text-[12.5px] hover:bg-wait/10 disabled:opacity-40"
                title={command(step) ? '글자까지 같은 이 명령은 이 세션 동안 묻지 않는다'
                  : `${step.tool} 는 이 세션 동안 묻지 않는다. 작업트리 밖은 그래도 거절한다`}
              >
                {command(step) ? '이 명령은 세션 동안' : '세션 동안'}
              </button>
            )}
            <button
              type="button"
              disabled={step.sending || !turn.pending}
              onClick={() => onAnswer(turn, step.id, false)}
              className="rounded-md border border-border px-2.5 py-1 text-[12.5px] hover:bg-secondary disabled:opacity-40"
            >
              거절
            </button>
          </span>
        )}
      </div>
      <pre className="mt-1.5 max-h-56 overflow-auto whitespace-pre-wrap break-all font-mono text-[12px] leading-snug">
        {detail}
      </pre>
      {step.error && <p role="alert" className="mt-1 text-[12.5px] text-destructive">{step.error}</p>}
      {open && !turn.pending && <p className="mt-1 text-[12.5px] text-faint">이 턴은 끝났다. 물은 프로세스가 없어 답할 수 없다.</p>}
    </div>
  )
}

const command = (step: Extract<Step, { kind: 'approval' }>) => step.tool === 'Bash' || step.tool === 'command'

/** The exact thing to approve. Never the agent's summary of it. */
function describe(step: Extract<Step, { kind: 'approval' }>): string {
  const input = step.input
  const pick = (key: string) => (typeof input[key] === 'string' ? (input[key] as string) : '')
  if (pick('command')) return `${pick('cwd') ? `(${pick('cwd')})\n` : ''}$ ${pick('command')}`
  if (Array.isArray(input.paths)) return (input.paths as string[]).join('\n')
  const path = pick('file_path') || pick('notebook_path')
  if (path && pick('content')) return `${path}\n\n${pick('content')}`
  if (path && pick('old_string')) return `${path}\n\n- ${pick('old_string')}\n+ ${pick('new_string')}`
  return step.text + (Object.keys(input).length ? `\n\n${JSON.stringify(input, null, 2)}` : '')
}
