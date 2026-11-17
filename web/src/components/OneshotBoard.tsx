import { useLayoutEffect, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Btn } from '@/components/Modal'
import { Decide, GO, Split, Title } from '@/components/OneshotSpec'
import { COLUMNS, type Feature, type Freq, type SpecView } from '@/lib/oneshot'
import { cn } from '@/lib/utils'

// -- The feature board ------------------------------------------------------------------

export function Board({ name, board, busy, submit }: {
  name: string; board: NonNullable<SpecView['board']>; busy: boolean
  submit: (f: Record<string, Freq>, line: string | Record<string, string>) => Promise<unknown>
}) {
  const [map, setMap] = useState<Record<string, Freq>>(board.frequency)
  // Several kinds: one first-screen line for each, and the preview shows one kind at a time (its features only).
  const kinds = board.kinds ?? []
  const start = board.first_screen
  const [lines, setLines] = useState<Record<string, string>>(() => kinds.length
    ? Object.fromEntries(kinds.map((k) => [k.id, (typeof start === 'string' ? start : start?.[k.id]) ?? '']))
    : { '': typeof start === 'string' ? start : '' })
  const [shown, setShown] = useState(kinds[0]?.id ?? '')
  const line = lines[shown] ?? ''
  const named = (ids: string[]) => kinds.filter((k) => ids.includes(k.id)).map((k) => k.name).join('·')
  const [dragged, setDragged] = useState('')
  const [moved, setMoved] = useState('')
  const move = (id: string, to: Freq) => setMap((m) => ({ ...m, [id]: to }))
  // A card moved by keys is a new element in its new column: the focus follows it there.
  useLayoutEffect(() => { if (moved) document.querySelector<HTMLElement>(`[data-card="${moved}"]`)?.focus() }, [map, moved])
  const step = (e: KeyboardEvent, f: Feature) => {
    const at = COLUMNS.findIndex((c) => c.id === map[f.id])
    const to = e.key === 'ArrowRight' ? at + 1 : e.key === 'ArrowLeft' ? at - 1 : -1
    if (to < 0 || to >= COLUMNS.length) return
    e.preventDefault()
    move(f.id, COLUMNS[to].id)
    setMoved(f.id)
  }
  const at = (freq: Freq) => board.features.filter((f) => map[f.id] === freq)
  const seen = (freq: Freq) => at(freq).filter((f) => !shown || !f.kinds || f.kinds.includes(shown))
  // The columns on the left; beside them the first screen they make, drawn as the frequencies place it.
  return (
    <Split label="기능 보드" sideLabel="첫 화면" side={<>
      <Title><span>첫 화면</span></Title>
      {kinds.length > 0 && (
        <div role="group" aria-label="미리 볼 종류" className="flex flex-wrap gap-1.5">
          {kinds.map((k) => (
            <button key={k.id} type="button" aria-pressed={shown === k.id} onClick={() => setShown(k.id)}
              className={cn('rounded-md border px-2.5 py-1 text-[12.5px]',
                shown === k.id ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card')}>{k.name}</button>
          ))}
        </div>
      )}
      <div role="img" aria-label="보드로 정해지는 첫 화면"
        className="aspect-[9/17] w-full max-w-[18rem] shrink-0 self-center rounded-[26px] bg-[#0b0c0f] p-[9px] shadow-[0_0_0_1px_var(--border)] max-[1500px]:max-w-[15rem]">
        <div className="flex h-full flex-col gap-2.5 overflow-hidden rounded-[18px] bg-background px-4 py-[18px] text-[13px]">
          <div className="flex justify-between text-[11px] text-muted-foreground"><span>9:41</span><span className="font-mono">{name}</span></div>
          <p className="font-heading text-[17px] font-semibold">{line || '첫 화면에서 하는 일'}</p>
          {seen('every').map((f) => <div key={f.id} className="rounded-[10px] bg-primary px-3.5 py-3 text-center font-semibold text-primary-foreground">{f.name}</div>)}
          {seen('sometimes').map((f) => <div key={f.id} className="rounded-lg bg-secondary px-3 py-2.5">{f.name}</div>)}
          {seen('rarely').length > 0 && <p className="mt-auto text-[12px] text-muted-foreground">더 보기 — {seen('rarely').map((f) => f.name).join(', ')}</p>}
        </div>
      </div>
      {(kinds.length ? kinds : [{ id: '', name: '' }]).map((k) => (
        <label key={k.id} className="flex flex-col gap-1.5">
          <span className="font-semibold">첫 화면에서 바로 하는 일{k.name && ` — ${k.name}`}</span>
          <input value={lines[k.id] ?? ''} onFocus={() => setShown(k.id)}
            onChange={(e) => setLines((l) => ({ ...l, [k.id]: e.target.value }))}
            className="h-9 rounded-md border border-input bg-background px-3 text-foreground max-[560px]:text-base" />
        </label>
      ))}
      <Decide>
        <Btn tone="wait" className={GO} disabled={busy || Object.values(lines).some((l) => !l.trim())}
          onClick={() => void submit(map, kinds.length ? lines : lines[''])}>보드 제출</Btn>
      </Decide>
    </>}>
      <div className="flex flex-col gap-4 px-10 py-6 max-[1500px]:px-7 max-[560px]:px-4">
        <div className="grid grid-cols-4 gap-3 max-[1500px]:grid-cols-2 max-[560px]:grid-cols-1">
          {COLUMNS.map((c) => (
            <Column key={c.id} label={c.label} count={at(c.id).length} empty={c.id === 'never'} drop={() => dragged && move(dragged, c.id)}>
              {at(c.id).map((f) => (
                <div key={f.id} data-card={f.id} tabIndex={0} draggable role="button"
                  aria-label={`${f.name} — ${c.label}`} onDragStart={() => setDragged(f.id)} onDragEnd={() => setDragged('')}
                  onKeyDown={(e) => step(e, f)}
                  className="cursor-grab rounded-lg bg-secondary px-3 py-2.5 focus-visible:outline-2 focus-visible:outline-primary">
                  <p className="font-semibold">{f.name}</p>
                  {f.kinds && f.kinds.length < kinds.length && <p className="text-[12px] text-wait">{named(f.kinds)} 만</p>}
                  <p className="text-[12px] text-muted-foreground">{f.why}</p>
                </div>
              ))}
            </Column>
          ))}
        </div>
        <p className="text-[12.5px] text-muted-foreground">끌어서 옮기거나, 기능에 초점을 두고 ← → 로 옮긴다.</p>
      </div>
    </Split>
  )
}

function Column({ label, count, empty, drop, children }: { label: string; count: number; empty: boolean; drop: () => void; children: ReactNode }) {
  return (
    <div aria-label={label} role="group" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); drop() }}
      className={cn('flex min-h-[28rem] flex-col gap-2 rounded-xl p-3 max-[1500px]:min-h-[14rem] max-[560px]:min-h-[7rem]',
        empty ? 'border border-dashed border-border' : 'border border-border bg-card')}>
      <h3 className="flex justify-between px-1 pb-1 font-heading text-[12px] font-semibold tracking-wide text-faint">
        <span>{label}</span><span className="font-mono">{count}</span></h3>
      {children}
      {!count && <p className="mt-auto px-1 text-[12px] text-faint">여기로 끌어 온다</p>}
    </div>
  )
}
