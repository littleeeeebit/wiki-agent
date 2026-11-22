import { useId, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Picture } from '@/components/Picture'
import { keepSources } from '@/lib/picture'
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
  onSubmit: (answers: string[], picks: (number[] | null)[]) => void
  onDecline?: () => void
  allowCustom?: boolean
}) {
  const id = useId()
  const [picked, setPicked] = useState<number[][]>(() => questions.map(() => []))   // each option by its index
  const [typed, setTyped] = useState<string[]>(() => questions.map(() => ''))
  const originals = questions.flatMap((q) => [q.header ?? '', q.question,
    ...(q.options ?? []).flatMap((o) => [o.label, o.description ?? '', o.preview ?? ''])])
  const shown = useOverlay(originals, korean)
  let at = 0
  const translated = questions.map((q) => ({ ...q, header: shown[at++], question: shown[at++],
    options: (q.options ?? []).map((o) => ({ ...o, label: shown[at++], description: shown[at++], preview: shown[at++] })) }))
  const answers = questions.map((q, i) => typed[i].trim() || picked[i].map((j) => q.options![j].label).join(', '))
  // Picks by index: the server sends the options as asked, even where the screen got them masked or alike.
  const picks = questions.map((_, i) => typed[i].trim() ? null : picked[i])
  const count = answers.filter(Boolean).length
  return (
    <form className="question-form space-y-6" onSubmit={(e) => {
      e.preventDefault()
      if (!disabled && count === questions.length && count > 0) onSubmit(answers, picks)
    }}>
      {questions.length > 1 && <nav aria-label="질문 챕터" className="flex flex-wrap gap-2">
        {translated.map((q, i) => <a key={i} href={`#${id}-${i}`} className="rounded-md border border-border px-3 py-2 text-[14px] hover:bg-secondary">
          {i + 1}. {q.header || `질문 ${i + 1}`} {answers[i] && '✓'}
        </a>)}
      </nav>}
      {translated.map((q, i) => (
        // The answers are disabled one by one, not the fieldset: a preview's picture still enlarges on a closed question.
        <fieldset id={`${id}-${i}`} key={i} className="min-w-0 scroll-mt-4 space-y-3">
          <legend className="mb-3 w-full text-[16px] font-semibold leading-relaxed break-words">
            <span className="mb-1 block text-[14px] text-muted-foreground">{i + 1} / {questions.length}{q.header && ` · ${q.header}`}</span>
            {q.question}
          </legend>
          {q.multiSelect && <p className="text-[14px] text-muted-foreground">여러 개를 고를 수 있다.</p>}
          {q.options.map((o, j) => {
            const original = questions[i].options![j].label
            const selected = picked[i].includes(j)
            const pick = () => setPicked((all) => all.map((now, k) => k !== i ? now : q.multiSelect
              ? selected ? now.filter((s) => s !== j) : [...now, j] : [j]))
            // A click on the card or its details picks, and leaves the focus on the input, as the label's click does.
            const choose = () => { if (disabled) return; pick(); document.getElementById(`${id}-${i}-${j}`)?.focus() }
            // The option is the card; only its input and title are the label, since a preview's picture is a
            // button of its own and a label may hold no control but the one it names.
            return <div key={j} className={cn('flex min-h-14 cursor-pointer items-start gap-3 rounded-lg border p-4 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-primary',
              selected ? 'border-primary bg-primary/10' : 'border-border bg-card hover:bg-secondary', disabled && 'cursor-default opacity-60')}
              onClick={(e) => { if (e.target === e.currentTarget) choose() }}>
              <input id={`${id}-${i}-${j}`} type={q.multiSelect ? 'checkbox' : 'radio'} name={`${id}-${i}`} value={original}
                aria-label={o.label} aria-describedby={o.description || o.preview ? `${id}-${i}-${j}-detail` : undefined}
                checked={selected} disabled={disabled} className="mt-1 size-4 shrink-0 accent-primary" onChange={pick} />
              <div className="min-w-0 flex-1 space-y-2 break-words">
                <label htmlFor={`${id}-${i}-${j}`} className={cn('block text-[16px] font-semibold leading-relaxed', !disabled && 'cursor-pointer')}>{j + 1}. {o.label}</label>
                <div id={`${id}-${i}-${j}-detail`} className="space-y-2" onClick={choose}>
                {o.description && <div className="question-preview text-[14px] leading-relaxed text-muted-foreground">
                  <Markdown remarkPlugins={[remarkGfm]} urlTransform={keepSources} components={{ a: ({ children }) => <span>{children}</span>,
                    img: ({ src, alt }) => <Picture src={typeof src === 'string' ? src : undefined} alt={alt} />, pre: ({ children }) => <pre className="overflow-x-auto whitespace-pre font-mono text-[12px]">{children}</pre> }}>{o.description}</Markdown>
                </div>}
                {o.preview && <div className="question-preview rounded-md bg-secondary p-3 text-[14px] leading-relaxed">
                  <Markdown remarkPlugins={[remarkGfm]} urlTransform={keepSources} components={{
                    a: ({ children }) => <span>{children}</span>,
                    img: ({ src, alt }) => <Picture src={typeof src === 'string' ? src : undefined} alt={alt} />,
                    pre: ({ children }) => <pre className="overflow-x-auto whitespace-pre font-mono text-[12px]">{children}</pre>,
                  }}>{o.preview}</Markdown>
                </div>}
                </div>
              </div>
            </div>
          })}
          {allowCustom && <label className="block text-[14px] leading-relaxed">
            직접 적기 <span className="text-muted-foreground">· 적으면 선택 대신 이 답을 보낸다</span>
            <textarea value={typed[i]} rows={2} disabled={disabled} placeholder="다른 의견이나 구체적인 조건을 적어라"
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
