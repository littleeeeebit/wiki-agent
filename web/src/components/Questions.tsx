import { useId, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useOverlay } from '@/lib/overlay'
import { cn } from '@/lib/utils'

export type Asked = {
  id?: string
  header?: string
  question: string
  multiSelect?: boolean
  options?: { label: string; description?: string; preview?: string }[] | null
}

/** Display translations only. Answers always retain the provider's labels. */
export function Questions({ questions, korean, disabled, onSubmit, onDecline, allowCustom = true }: {
  questions: Asked[]
  korean: boolean
  disabled: boolean
  onSubmit: (answers: string[]) => void
  onDecline?: () => void
  allowCustom?: boolean
}) {
  const id = useId()
  const [picked, setPicked] = useState<string[][]>(() => questions.map(() => []))
  const [typed, setTyped] = useState<string[]>(() => questions.map(() => ''))
  const originals = questions.flatMap((q) => [q.header ?? '', q.question,
    ...(q.options ?? []).flatMap((o) => [o.label, o.description ?? '', o.preview ?? ''])])
  const shown = useOverlay(originals, korean)
  let at = 0
  const translated = questions.map((q) => ({ ...q, header: shown[at++], question: shown[at++],
    options: (q.options ?? []).map((o) => ({ ...o, label: shown[at++], description: shown[at++], preview: shown[at++] })) }))
  const answers = questions.map((_, i) => typed[i].trim() || picked[i].join(', '))
  const count = answers.filter(Boolean).length
  return (
    <form className="question-form space-y-6" onSubmit={(e) => {
      e.preventDefault()
      if (!disabled && count === questions.length && count > 0) onSubmit(answers)
    }}>
      {questions.length > 1 && <nav aria-label="질문 챕터" className="flex flex-wrap gap-2">
        {translated.map((q, i) => <a key={i} href={`#${id}-${i}`} className="rounded-md border border-border px-3 py-2 text-[14px] hover:bg-secondary">
          {i + 1}. {q.header || `질문 ${i + 1}`} {answers[i] && '✓'}
        </a>)}
      </nav>}
      {translated.map((q, i) => (
        <fieldset id={`${id}-${i}`} key={i} className="min-w-0 scroll-mt-4 space-y-3" disabled={disabled}>
          <legend className="mb-3 w-full text-[16px] font-semibold leading-relaxed break-words">
            <span className="mb-1 block text-[14px] text-muted-foreground">{i + 1} / {questions.length}{q.header && ` · ${q.header}`}</span>
            {q.question}
          </legend>
          {q.multiSelect && <p className="text-[14px] text-muted-foreground">여러 개를 고를 수 있다.</p>}
          {q.options.map((o, j) => {
            const original = questions[i].options![j].label
            const selected = picked[i].includes(original)
            return <label key={j} className={cn('flex min-h-14 cursor-pointer items-start gap-3 rounded-lg border p-4 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-primary',
              selected ? 'border-primary bg-primary/10' : 'border-border bg-card hover:bg-secondary', disabled && 'cursor-default opacity-60')}>
              <input type={q.multiSelect ? 'checkbox' : 'radio'} name={`${id}-${i}`} value={original}
                aria-label={o.label} aria-describedby={o.description || o.preview ? `${id}-${i}-${j}-detail` : undefined}
                checked={selected} className="mt-1 size-4 shrink-0 accent-primary"
                onChange={() => setPicked((all) => all.map((now, k) => k !== i ? now : q.multiSelect
                  ? selected ? now.filter((s) => s !== original) : [...now, original] : [original]))} />
              <div className="min-w-0 flex-1 space-y-2 break-words">
                <span className="block text-[16px] font-semibold leading-relaxed">{j + 1}. {o.label}</span>
                <div id={`${id}-${i}-${j}-detail`} className="space-y-2">
                {o.description && <div className="question-preview text-[14px] leading-relaxed text-muted-foreground">
                  <Markdown remarkPlugins={[remarkGfm]} components={{ a: ({ children }) => <span>{children}</span>,
                    img: ({ alt }) => <span>{alt}</span>, pre: ({ children }) => <pre className="overflow-x-auto whitespace-pre font-mono text-[12px]">{children}</pre> }}>{o.description}</Markdown>
                </div>}
                {o.preview && <div className="question-preview rounded-md bg-secondary p-3 text-[14px] leading-relaxed">
                  <Markdown remarkPlugins={[remarkGfm]} components={{
                    a: ({ children }) => <span>{children}</span>,
                    img: ({ alt }) => <span>{alt}</span>,
                    pre: ({ children }) => <pre className="overflow-x-auto whitespace-pre font-mono text-[12px]">{children}</pre>,
                  }}>{o.preview}</Markdown>
                </div>}
                </div>
              </div>
            </label>
          })}
          {allowCustom && <label className="block text-[14px] leading-relaxed">
            직접 적기 <span className="text-muted-foreground">· 적으면 선택 대신 이 답을 보낸다</span>
            <textarea value={typed[i]} rows={2} placeholder="다른 의견이나 구체적인 조건을 적어라"
              onChange={(e) => setTyped((all) => all.map((t, j) => j === i ? e.target.value : t))}
              className="mt-2 w-full resize-y rounded-md border border-input bg-background p-3 text-[16px]" />
          </label>}
        </fieldset>
      ))}
      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4 text-[14px]">
        <span className="mr-auto text-muted-foreground">{count} / {questions.length} 답변</span>
        {onDecline && <button type="button" disabled={disabled} onClick={onDecline}
          className="min-h-11 rounded-md border border-border px-4 hover:bg-secondary disabled:opacity-40">답하지 않기</button>}
        <button type="submit" disabled={disabled || count !== questions.length || !count}
          className="min-h-11 rounded-md bg-primary px-4 text-primary-foreground hover:opacity-90 disabled:opacity-40">답하고 이어 가기</button>
      </div>
    </form>
  )
}
