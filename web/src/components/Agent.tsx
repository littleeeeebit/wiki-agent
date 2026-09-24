import { useEffect, useRef } from 'react'
import { Answer } from '@/components/Answer'
import { Composer } from '@/components/Composer'
import { Toolbar } from '@/components/Toolbar'
import type { Choice } from '@/components/Toolbar'
import type { Options, Worktree } from '@/lib/api'
import { useOverlay } from '@/lib/overlay'
import type { Step, Turn } from '@/lib/work'
import { cn } from '@/lib/utils'

type Props = {
  row: Worktree | undefined
  turns: Turn[]
  options: Options | null
  choice: Choice
  on: boolean
  seed: { text: string } | null
  onChoice: (c: Choice) => void
  onSend: (text: string) => void
  onAnswer: (turn: Turn, id: string, allow: boolean) => void
  onReset: () => void
  onPeek: (path: string, line: number) => void
}

/** The selected worktree's agent. Its answers get the Korean overlay; what it
 *  ran and what it asks to write stay as they are, because a person approves
 *  those and a reworded command is not the command. */
export function Agent({ row, turns, options, choice, on, seed, onChoice, onSend, onAnswer, onReset, onPeek }: Props) {
  const end = useRef<HTMLDivElement>(null)
  const busy = turns.at(-1)?.pending ?? false
  const last = turns.at(-1)
  const grown = turns.length + (last?.text.length ?? 0) + (last?.steps.length ?? 0)
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [grown])

  return (
    <section aria-label="에이전트 세션" className="flex h-full min-h-0 flex-col">
      <header className="border-b border-border bg-card px-5 pt-3 pb-2.5">
        <div className="flex h-7 items-center gap-2">
          <h2 className="truncate font-heading text-[14px] font-semibold">
            {row ? row.name : '에이전트'}
          </h2>
          <p className="truncate font-mono text-[10.5px] text-faint">
            {row ? `${row.branch}${row.dirty ? ' · 변경 있음' : row.merged ? ' · HEAD 에 다 있음' : ''}` : '작업트리를 고르면 여기서 일을 시킨다'}
          </p>
        </div>
        {/* Held open without a worktree too, so this header's edge stays level
            with the query pane's. */}
        <div className="mt-2 flex h-7 items-center justify-end gap-1.5">
          {row && (
            <>
              <Toolbar value={choice} options={options} busy={busy} onChange={onChoice} />
              <button
                type="button"
                onClick={onReset}
                disabled={busy}
                className="h-7 rounded-md border border-border px-2 text-[12.5px] text-muted-foreground hover:bg-secondary disabled:opacity-40"
              >
                문맥 비우기
              </button>
            </>
          )}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="space-y-5 px-4 py-4">
          {!row && (
            <p className="text-[13.5px] text-faint">
              {seed ? '초안이 입력칸에서 기다린다. 왼쪽에서 작업트리를 고르거나 새로 만든다.'
                : '왼쪽에서 작업트리를 고르거나 새로 만든다. 에이전트는 그 안에서만 쓰고, 쓰기마다 여기서 묻는다.'}
            </p>
          )}
          {row && turns.length === 0 && (
            <p className="text-[13.5px] text-faint">지시를 보내라. 파일을 고치거나 명령을 돌리기 전에 여기서 허용을 묻는다.</p>
          )}
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
        seed={seed}
        max={320}
        placeholder="지시를 적어라. Enter 로 보내고 Shift+Enter 로 줄바꿈."
        onSend={onSend}
      />
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
          {[`${(turn.ms / 1000).toFixed(1)}s`, turn.cost != null && `$${turn.cost.toFixed(3)}`, turn.model]
            .filter(Boolean).join(' · ')}
        </p>
      )}
    </div>
  )
}

function Tool({ text }: { text: string }) {
  return <p className="font-mono text-[12px] leading-snug text-faint">· {text}</p>
}

/** One write the agent wants to make. What it will do is shown as it will be
 *  done — the command, the path, the content — and nothing happens until a
 *  person presses one of the two. */
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
          {open ? '쓰기 허용?' : step.answer ? '허용함' : '거절함'}
          <span className="ml-1.5 font-mono text-[11px] font-normal text-muted-foreground">{step.tool}</span>
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
