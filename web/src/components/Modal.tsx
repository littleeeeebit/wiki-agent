import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { X } from 'lucide-react'
import type { Keep, Kept } from '@/lib/api'
import { cn } from '@/lib/utils'

/** Every modal of the window, on the platform's `<dialog>`: it traps focus,
 *  closes on Escape and draws its own backdrop. A click on the backdrop
 *  closes it too. */
export function Modal({ title, onClose, children, foot, wide }: {
  title: string
  onClose: () => void
  children: ReactNode
  /** The buttons, right-aligned under the body. */
  foot?: ReactNode
  wide?: boolean
}) {
  const box = useRef<HTMLDialogElement>(null)
  // Opened once; unmounting removes it. Closing it in a cleanup would fire
  // `close`, and so `onClose`, on StrictMode's rehearsal unmount.
  useEffect(() => {
    if (box.current && !box.current.open) box.current.showModal()
  }, [])
  return (
    <dialog
      ref={box}
      aria-label={title}
      onClose={onClose}
      onClick={(e) => e.target === box.current && onClose()}
      className={cn('m-auto max-h-[85vh] max-w-[calc(100vw-2rem)] rounded-lg border border-border bg-card p-0 text-foreground',
        'backdrop:bg-black/50', wide ? 'w-[40rem]' : 'w-[28rem]')}
    >
      <div className="flex max-h-[85vh] flex-col">
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
          <h2 className="font-heading text-[14px] font-semibold">{title}</h2>
          <button type="button" onClick={onClose} aria-label="닫기"
            className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-secondary">
            <X className="size-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 text-[12.5px]">{children}</div>
        {foot && <div className="flex shrink-0 justify-end gap-2 border-t border-border px-4 py-3">{foot}</div>}
      </div>
    </dialog>
  )
}

/** A clear's question: what becomes of the conversation. `onClose` gets the
 *  line to show after — empty when nothing was done. */
export function ClearAsk({ onClear, onClose }: {
  onClear: (keep: Keep) => Promise<Kept>
  onClose: (said: string) => void
}) {
  const [working, setWorking] = useState<Keep | ''>('')
  const [fault, setFault] = useState('')
  const pick = async (keep: Keep) => {
    setFault('')
    setWorking(keep)
    try {
      const kept = await onClear(keep)
      onClose(keep === 'delete' ? '대화를 지웠다.'
        : kept.fault ?? (kept.memory ? `메모리로 남겼다 — ${kept.memory} · 원시 대화 ${kept.raw}` : '남길 대화가 없었다.'))
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
      setWorking('')
    }
  }
  return (
    <Modal title="문맥 비우기" onClose={() => !working && onClose('')} foot={(
      <>
        <Btn disabled={!!working} onClick={() => onClose('')}>취소</Btn>
        <Btn tone="danger" disabled={!!working} onClick={() => void pick('delete')}>
          {working === 'delete' ? '지우는 중…' : '지우기'}
        </Btn>
        <Btn tone="primary" disabled={!!working} onClick={() => void pick('memory')}>
          {working === 'memory' ? '정리하는 중…' : '메모리로 남기기'}
        </Btn>
      </>
    )}>
      <p>비우면 다음 지시부터 새 대화다. 지금까지의 대화는 어떻게 할까?</p>
      <ul className="mt-2 space-y-1 text-muted-foreground">
        <li>· 메모리로 남기기 — 원시 대화와, 그것을 정리한 메모리 한 쌍을 이 저장소의 <code className="font-mono text-[12px]">.wiki/memory/</code> 에 쓴다. 위키 검색이 메모리를 찾는다. 정리에 모델 한 턴이 든다</li>
        <li>· 지우기 — 기록에서도 지운다. 되돌릴 수 없다</li>
      </ul>
      {fault && <p role="alert" className="mt-2 whitespace-pre-wrap text-destructive">{fault}</p>}
    </Modal>
  )
}

/** The window's buttons: one height (28px), three weights. `primary` is the
 *  one action a modal or card exists for; `wait` answers a person-waiting
 *  write; everything else is plain. */
export function Btn({ tone = 'plain', className, ...props }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: 'plain' | 'primary' | 'wait' | 'danger' | 'ghost' }) {
  return (
    <button
      type="button"
      {...props}
      className={cn('inline-flex h-7 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 text-[12.5px] disabled:opacity-40',
        tone === 'plain' && 'border border-border bg-background hover:bg-secondary',
        tone === 'primary' && 'bg-primary text-primary-foreground hover:opacity-90',
        tone === 'wait' && 'bg-wait text-wait-foreground hover:opacity-90',
        tone === 'danger' && 'border border-destructive/40 text-destructive hover:bg-destructive/10',
        tone === 'ghost' && 'text-muted-foreground hover:bg-secondary hover:text-foreground',
        className)}
    />
  )
}
