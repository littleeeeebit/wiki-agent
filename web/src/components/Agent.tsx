import { useEffect, useRef, useState } from 'react'
import { Eraser } from 'lucide-react'
import { Answer } from '@/components/Answer'
import { Composer } from '@/components/Composer'
import { Btn, ClearAsk } from '@/components/Modal'
import { Toolbar } from '@/components/Toolbar'
import type { Choice } from '@/components/Toolbar'
import type { Keep, Kept, Options, Rule, Worktree } from '@/lib/api'
import { useOverlay } from '@/lib/overlay'
import type { Step, Turn } from '@/lib/work'
import { cn } from '@/lib/utils'

type Props = {
  row: Worktree | undefined
  turns: Turn[]
  options: Options | null
  choice: Choice
  on: boolean
  onChoice: (c: Choice) => void
  onSend: (text: string) => void
  onAnswer: (turn: Turn, id: string, allow: boolean, scope?: 'once' | 'session') => void
  onStop: (turn: Turn) => void
  rules: Rule[]
  onClearRules: () => void
  onReset: (keep: Keep) => Promise<Kept>
  onPeek: (path: string, line: number) => void
}

/** The selected worktree's agent. Its answers get the Korean overlay; what it
 *  ran and what it asks to write stay as they are, because a person approves
 *  those and a reworded command is not the command. */
export function Agent({
  row, turns, options, choice, on, onChoice, onSend, onAnswer, onStop, rules, onClearRules, onReset, onPeek,
}: Props) {
  const end = useRef<HTMLDivElement>(null)
  const [asking, setAsking] = useState(false)
  const [note, setNote] = useState('')
  const busy = turns.at(-1)?.pending ?? false
  const last = turns.at(-1)
  const grown = turns.length + (last?.text.length ?? 0) + (last?.steps.length ?? 0)
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [grown])

  return (
    <section aria-label="에이전트 세션" className="flex h-full min-h-0 flex-col">
      <header className="border-b border-border px-5">
        {/* The model and effort stand here before there is a worktree too:
            a spec's [시작] runs its first turn on them. */}
        <div className="flex h-11 items-center justify-end gap-1.5">
          <span className="mr-auto truncate font-heading text-[11px] font-semibold text-faint">작업 모델</span>
          {row && busy && last?.turn && (
            <Btn tone="danger" onClick={() => onStop(last)} title="도는 턴을 멈춘다. 대화는 남아 다음 지시가 이어진다">
              멈춤
            </Btn>
          )}
          <Toolbar value={choice} options={options} busy={busy} onChange={onChoice} />
          {row && (
            <Btn tone="ghost" className="px-1.5" onClick={() => (turns.length ? setAsking(true)
                : void onReset('delete').catch((err) => setNote(String(err instanceof Error ? err.message : err))))}
              disabled={busy} aria-label="문맥 비우기"
              title="문맥 비우기 — 이 작업트리의 대화를 새로 시작한다. 지금 대화는 메모리로 남기거나 지운다">
              <Eraser className="size-4" />
            </Btn>
          )}
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

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="space-y-5 px-4 py-4">
          {!row && (
            <p className="text-[13.5px] text-faint">
              아직 작업트리가 없다. 명세의 [시작] 이 작업트리를 만들고 위의 작업 모델로 첫 턴을 보낸다. 에이전트는 그 안에서만 쓰고, 쓰기마다 여기서 묻는다.
            </p>
          )}
          {row && turns.length === 0 && (
            <p className="text-[13.5px] text-faint">지시를 보내라. 파일을 고치거나 명령을 돌리기 전에 여기서 허용을 묻는다.</p>
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
          <div ref={end} />
        </div>
      </div>

      <Composer
        busy={busy}
        disabled={!row}
        max={320}
        placeholder="지시를 적어라. Enter 로 보내고 Shift+Enter 로 줄바꿈."
        onSend={onSend}
      />
      {asking && <ClearAsk onClear={onReset} onClose={(said) => {
        setAsking(false)
        setNote(said)
      }} />}
    </section>
  )
}

function Reply({ turn, on, onAnswer, onPeek }: {
  turn: Turn
  on: boolean
  onAnswer: Props['onAnswer']
  onPeek: Props['onPeek']
}) {
  // Only once the answer is finished: a half-streamed paragraph translated
  // reads exactly like a whole one.
  const [text] = useOverlay([turn.text], on && !turn.pending)
  return (
    <div className="space-y-2">
      {turn.steps.length > 0 && (
        <ul className="space-y-1.5">
          {turn.steps.map((s, i) => (
            <li key={i}>{s.kind === 'tool' ? <Tool text={s.text} /> : <Ask step={s} turn={turn} onAnswer={onAnswer} />}</li>
          ))}
        </ul>
      )}
      {turn.text ? (
        <Answer text={text} korean={on} remote="" onPeek={onPeek} />
      ) : (
        turn.pending && <span className="inline-block h-3.5 w-1.5 animate-pulse rounded-[1px] bg-faint align-middle" />
      )}
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

function Tool({ text }: { text: string }) {
  return <p className="font-mono text-[12px] leading-snug text-faint">· {text}</p>
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
