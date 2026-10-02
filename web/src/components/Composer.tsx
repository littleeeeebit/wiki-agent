import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

type Props = {
  busy: boolean
  onSend: (text: string) => void
  /** Text put in the box from outside — a draft. A new object each time, so the
   *  same draft twice still lands. The person edits it before sending. */
  seed?: { text: string } | null
  placeholder?: string
  disabled?: boolean
  max?: number
}

export function Composer({ busy, onSend, seed, placeholder, disabled, max = 200 }: Props) {
  const [text, setText] = useState('')
  const box = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (seed) {
      setText(seed.text)
      box.current?.focus()
    }
  }, [seed])

  // Pasting several lines in is common. It grows with the content, up to a
  // limit — past that the composer would push the conversation off screen.
  useEffect(() => {
    const el = box.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, max)}px`
    el.style.overflowY = el.scrollHeight > max ? 'auto' : 'hidden'
  }, [text, max])

  function send() {
    const value = text.trim()
    if (!value || busy || disabled) return
    onSend(value)
    setText('')
  }

  return (
    <div className="border-t border-border bg-card">
      <div className="mx-auto flex max-w-3xl items-end gap-2 px-5 py-3">
        <Textarea
          ref={box}
          rows={1}
          aria-label="질문 또는 지시"
          enterKeyHint="send"
          value={text}
          disabled={busy || disabled}
          placeholder={busy ? '답하는 중…' : (placeholder ?? '물어라. Enter 로 보내고 Shift+Enter 로 줄바꿈.')}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              send()
            }
          }}
          style={{ maxHeight: max }}
          className="min-h-9 resize-none bg-background text-[13.5px]"
        />
        <Button onClick={send} disabled={busy || disabled || !text.trim()} className="h-9 text-[12.5px] font-normal">
          보내
        </Button>
      </div>
    </div>
  )
}
