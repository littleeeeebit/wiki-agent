import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { Btn } from '@/components/Modal'
import type { Spec } from '@/lib/api'
import { cn } from '@/lib/utils'

/** The selected task's spec, folded to its goal. Open, it shows what the
 *  work session was given — what stays out, the done conditions, the grounds
 *  and the decisions — and what the server made of the result. Editing is the
 *  card's, in the `next` conversation; a spec not started yet starts here too. */
export function SpecSummary({ spec, onStart }: { spec: Spec | null; onStart: (id: string) => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [working, setWorking] = useState(false)
  const [fault, setFault] = useState('')
  if (!spec) return <p className="flex h-11 items-center px-5 text-[12.5px] text-faint">명세 없음 — 이 작업트리는 명세 없이 만들어졌다</p>

  const list = (label: string, items: string[]) => items.length > 0 && (
    <div>
      <div className="font-heading text-[11px] font-semibold text-faint">{label}</div>
      <ul className="mt-0.5 space-y-0.5">{items.map((x, i) => <li key={i}>· {x}</li>)}</ul>
    </div>
  )

  return (
    <div className="border-b border-border">
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        className="flex h-11 w-full items-center gap-2 px-5 text-left hover:bg-secondary/50">
        <ChevronRight className={cn('size-3.5 shrink-0 text-faint transition-transform', open && 'rotate-90')} />
        <span className="shrink-0 font-heading text-[11px] font-semibold text-faint">명세</span>
        <span className="min-w-0 truncate text-[12.5px]">{spec.goal}</span>
      </button>
      {open && (
        <div className="max-h-72 space-y-3 overflow-y-auto px-5 pb-4 text-[12.5px]">
          {list('빼는 것', spec.out)}
          {list('완료 조건', spec.done)}
          {list('근거', [...spec.grounds.pages, ...spec.grounds.files, ...spec.grounds.rules])}
          {list('결정', spec.decisions.map((d) => `${d.what}${d.why ? ` — ${d.why}` : ''}`))}
          {spec.report && list('작업 셀의 보고', spec.report.map((r) => `${r.pass ? '통과' : '실패'} · ${r.item}`))}
          {spec.gate && !spec.gate.ok && (
            <div className="text-destructive">
              판정 실패 — {spec.gate.reason}
              {spec.gate.tail && <pre className="mt-1 max-h-40 overflow-auto rounded bg-secondary p-2 font-mono text-[12px] text-foreground">{spec.gate.tail}</pre>}
            </div>
          )}
          {spec.fault && <p className="text-destructive">{spec.fault}</p>}
          {spec.state === '정리됨' && (
            <div className="flex items-center gap-2">
              <Btn tone="primary" disabled={working} onClick={async () => {
                setFault('')
                setWorking(true)
                try {
                  await onStart(spec.id)
                } catch (err) {
                  setFault(String(err instanceof Error ? err.message : err))
                } finally {
                  setWorking(false)
                }
              }}>
                {working ? '시작하는 중…' : '시작 ▸'}
              </Btn>
              <span className="text-faint">고칠 것은 다음 작업 대화의 카드에서</span>
            </div>
          )}
          {fault && <p role="alert" className="text-destructive">{fault}</p>}
        </div>
      )}
    </div>
  )
}
