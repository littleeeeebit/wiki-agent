import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Choices } from '@/components/Blocks'
import { Btn } from '@/components/Modal'
import { api, COLUMNS, STOP, type Decision, type Feature, type Freq, type Gate, type Palette,
  type Models, type RunSummary, type SpecView, type Talk, ROLE, modelName } from '@/lib/oneshot'
import { cn } from '@/lib/utils'

/** Which model each part of the run uses, fixed at its start. */
export function ModelsLine({ models }: { models?: Models }) {
  return (
    <p className="text-[12.5px] text-muted-foreground">
      {models
        ? `${(Object.keys(ROLE) as (keyof Models)[]).map((k) => `${ROLE[k]} ${modelName(k, models[k])}`).join(' / ')} / 화면 비평 opus · high (고정)`
        : '모델을 고르기 전에 시작한 실행이다 — 「다음 작업」 창에서 고른 모델을 쓴다'}
    </p>
  )
}

/** A oneshot run past S1 (`docs/plans/oneshot/5-screen-pr.md`): the stage strip, the S2
 *  conversation, the three gate sheets and the feature board. The server renders every
 *  sheet and owns every rule; this view only shows, folds and collects decisions. */

const error = (err: unknown) => String(err instanceof Error ? err.message : err)

export function OneshotSpec({ repo, onView }: { repo: string; onView?: (view: SpecView) => void }) {
  const [view, setView] = useState<SpecView | null>(null)
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { api.view(repo).then(setView).catch((err) => setFault(error(err))) }, [repo])
  useEffect(() => { if (view) onView?.(view) }, [view, onView])
  const working = !!view?.working
  useEffect(() => {
    if (!working) return
    const timer = window.setInterval(() => { api.view(repo).then(setView).catch((err) => setFault(error(err))) }, 1500)
    return () => window.clearInterval(timer)
  }, [repo, working])

  /** Whether the server took it: a refused answer stays in its box to be sent again. */
  async function act(fn: () => Promise<SpecView>) {
    setFault('')
    setBusy(true)
    try {
      setView(await fn())
      return true
    } catch (err) {
      setFault(error(err))
      return false
    } finally {
      setBusy(false)
    }
  }

  if (!view) return <p role="status" className="text-muted-foreground">{fault || '읽는 중…'}</p>
  const o = view.oneshot
  const idle = !view.working && !view.stopped
  return (
    <div className="flex flex-col gap-4">
      {fault && <p role="alert" className="whitespace-pre-wrap rounded-md bg-destructive/10 px-3 py-2 text-destructive">{fault}</p>}
      {view.stopped && (
        <section role="alert" className="flex flex-col gap-2 rounded-md border border-wait/40 bg-wait/10 px-3 py-2">
          <p className="font-semibold">멈췄다 — {STOP[view.stopped.reason] ?? view.stopped.reason}</p>
          <p className="whitespace-pre-wrap">{view.stopped.detail}</p>
          <div className="flex justify-end"><Btn tone="primary" disabled={busy} onClick={() => void act(() => api.resume(repo))}>재개</Btn></div>
        </section>
      )}
      {view.stage === 'spec' && <Conversation talk={o.talk ?? []} sentence={o.sentence} busy={busy || !idle}
        send={(text) => act(() => api.say(repo, text))} />}
      {idle && view.stage === 'r1' && view.sheet && o.r1 && (
        <Sheet key={`r1-${o.r1.round}`} gate="r1" text={view.sheet} items={view.items ?? []} state={o.r1} busy={busy}
          submit={(body) => act(() => api.decide(repo, 'r1', body))} />
      )}
      {idle && view.stage === 'board' && view.board && (
        <Board board={view.board} busy={busy} submit={(f, line) => act(() => api.board(repo, f, line))} />
      )}
      {idle && view.stage === 'r2' && view.front_sheet && o.r2 && (
        <Sheet key={`r2-${o.r2.round}`} gate="r2" text={view.front_sheet} items={view.front_items ?? []} state={o.r2}
          busy={busy} palettes={o.front?.tokens.palettes ?? []}
          reopen={() => act(() => api.decide(repo, 'r2', { reopen: true }))}
          submit={(body) => act(() => api.decide(repo, 'r2', body))} />
      )}
      {idle && view.stage === 'r3' && view.r3_sheet && o.r3 && (
        <Sheet key={`r3-${o.r3.round}`} gate="r3" text={view.r3_sheet} items={view.r3_items ?? []} state={o.r3}
          busy={busy} submit={(body) => act(() => api.decide(repo, 'r3', body))} />
      )}
      {view.stage === 'done' && view.summary && <Finished summary={view.summary} />}
    </div>
  )
}

function Conversation({ talk, sentence, busy, send }: {
  talk: Talk[]; sentence: string; busy: boolean; send: (text: string) => Promise<boolean>
}) {
  const [text, setText] = useState('')
  // Cleared only once the server took it, and only if nothing new was typed meanwhile.
  const submit = async (sent: string) => { if (await send(sent)) setText((now) => (now === sent ? '' : now)) }
  return (
    <section aria-label="요구 대화" className="flex flex-col gap-2">
      <p className="text-muted-foreground">{sentence}</p>
      {talk.map((t, i) => (
        <div key={i} className={cn('whitespace-pre-wrap rounded-md px-3 py-2', t.role === 'agent' ? 'bg-secondary' : 'border border-border')}>
          <span className="mr-2 text-[11.5px] text-muted-foreground">{t.role === 'agent' ? '에이전트' : '나'}</span>{t.text}
          {/* The question itself rides in `choices`, cut from the text: only the last turn's can be answered. */}
          {t.choices && <Choices value={t.choices} korean={false} busy={busy || i !== talk.length - 1} onSay={(a) => void send(a)} />}
        </div>
      ))}
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void submit(text) }}>
        <textarea aria-label="답" value={text} onChange={(e) => setText(e.target.value)} rows={2}
          className="flex-1 rounded-md border border-input bg-background px-2 py-1.5 text-foreground" />
        <Btn type="submit" tone="primary" disabled={busy || !text.trim()}>보내기</Btn>
      </form>
    </section>
  )
}

// -- The gate sheet ---------------------------------------------------------------------

/** The sheet's Markdown split at its `<details>` blocks: `[text, null]` is shown, `[text, summary]` folds. */
function parts(text: string): [string, string | null][] {
  const out: [string, string | null][] = []
  const fold = /<details><summary>([\s\S]*?)<\/summary>([\s\S]*?)<\/details>/g
  let at = 0
  for (const m of text.matchAll(fold)) {
    out.push([text.slice(at, m.index), null], [m[2], m[1]])
    at = m.index + m[0].length
  }
  out.push([text.slice(at), null])
  return out.filter(([t]) => t.trim())
}
const named = (text: string, id: string) => new RegExp(`(^|[^\\w-])${id}(?![\\w-])`).test(text)

function Mermaid({ code }: { code: string }) {
  const [svg, setSvg] = useState('')
  const id = `sheet${useId().replace(/[^\w]/g, '')}`
  useEffect(() => {
    let live = true
    import('mermaid').then(async ({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'base', htmlLabels: false })
      const result = await mermaid.render(id, code)
      if (live) setSvg(result.svg)
    }).catch(() => live && setSvg(''))
    return () => { live = false }
  }, [code, id])
  return svg ? <div className="overflow-x-auto" dangerouslySetInnerHTML={{ __html: svg }} /> : <pre><code>{code}</code></pre>
}

function Md({ text, zoom }: { text: string; zoom: (src: string) => void }) {
  // Made once: a new `img` each render is a new component, which replaces the thumbnail holding the focus.
  const components = useMemo<Components>(() => ({
        img: ({ src, alt }) => (
          <button type="button" className="inline-block" onClick={() => zoom(String(src))} title="크게 본다">
            <img src={String(src)} alt={alt ?? ''} loading="lazy" className="max-h-64 rounded border border-border" />
          </button>
        ),
        table: ({ children }) => <div className="overflow-x-auto"><table>{children}</table></div>,
        code: ({ className, children }) => className === 'language-mermaid'
          ? <Mermaid code={String(children ?? '').replace(/\n$/, '')} /> : <code className={className}>{children}</code>,
      }), [zoom])
  return (
    <div className="prose-answer">
      <Markdown remarkPlugins={[remarkGfm]} components={components}>{text}</Markdown>
    </div>
  )
}

export function Sheet({ gate, text, items, state, busy, submit, palettes, reopen }: {
  gate: 'r1' | 'r2' | 'r3'; text: string; items: string[]; state: Gate; busy: boolean
  submit: (body: object) => Promise<unknown>; palettes?: Palette[]; reopen?: () => Promise<unknown>
}) {
  const [decisions, setDecisions] = useState<Record<string, Decision>>(state.decisions)
  const [palette, setPalette] = useState(state.palette ?? '')
  const [opened, setOpened] = useState<string[]>([])
  const [zoom, setZoom] = useState('')
  const since = useRef(0)
  useEffect(() => { since.current = Date.now() }, [])
  const sections = parts(text)
  const shown = sections.filter(([, s]) => s === null).map(([t]) => t).join('\n')
  // An item the shown text names sits with it; the rest belong to the folded sections.
  const folded = items.filter((i) => !named(shown, i))
  const undecided = items.filter((i) => !decided(decisions[i]))
  const needPalette = !!palettes?.length && !palette
  const set = (id: string, d: Decision) => setDecisions((all) => ({ ...all, [id]: d }))
  const approveAll = (ids: string[]) => setDecisions((all) => ({ ...all, ...Object.fromEntries(ids.map((i) => [i, { approve: true }])) }))

  return (
    <section aria-label={`${gate.toUpperCase()} 시트`} className="flex flex-col gap-3">
      <div className="max-h-[60vh] overflow-y-auto rounded-md border border-border px-3 py-2">
        {sections.map(([t, summary], n) => summary === null ? <Md key={n} text={t} zoom={setZoom} /> : (
          <details key={n} onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) setOpened((o) => o.includes(summary) ? o : [...o, summary]) }}>
            <summary className="cursor-pointer font-semibold">{summary}</summary>
            <Md text={t} zoom={setZoom} />
          </details>
        ))}
      </div>
      {palettes && palettes.length > 0 && (
        <fieldset className="flex flex-wrap gap-2">
          <legend className="mb-1 font-semibold">색 배합 하나</legend>
          {palettes.map((p) => (
            <label key={p.id} className={cn('flex cursor-pointer items-center gap-2 rounded-md border px-2 py-1', palette === p.id ? 'border-primary' : 'border-border')}>
              <input type="radio" name="palette" value={p.id} checked={palette === p.id} onChange={() => setPalette(p.id)} />
              {Object.values(p.colors).map((c) => <span key={c} className="size-4 rounded-sm border border-border" style={{ background: c }} />)}
              {p.id} {p.name}
            </label>
          ))}
        </fieldset>
      )}
      <Items title="펼쳐진 항목" ids={items.filter((i) => !folded.includes(i))} decisions={decisions} marks={state.marks} set={set} />
      {folded.length > 0 && (
        <Items title="접힌 부분의 항목" ids={folded} decisions={decisions} marks={state.marks} set={set}
          approveAll={() => approveAll(folded)} />
      )}
      <div className="flex items-center justify-end gap-2">
        {reopen && <Btn disabled={busy} onClick={() => void reopen()} title="기능의 자리가 틀렸다: 화면 설계를 버리고 기능 보드로 돌아간다">기능 보드 다시 열기</Btn>}
        <span role="status" className="text-muted-foreground">
          {undecided.length ? `정하지 않은 항목 ${undecided.length}개` : needPalette ? '색 배합을 골라라' : '모두 정했다'}
        </span>
        <Btn tone="primary" disabled={busy || undecided.length > 0 || needPalette}
          onClick={() => void submit({ decisions: clean(decisions, items), palette: palette || undefined,
            reading: { seconds: (Date.now() - since.current) / 1000, opened } })}>
          제출
        </Btn>
      </div>
      {zoom && <Zoom src={zoom} close={() => setZoom('')} />}
    </section>
  )
}

/** A picture enlarged in its own modal dialog above the oneshot window: Escape, the backdrop or
 *  [닫기] close only it, and the focus goes back to the thumbnail that opened it. React passes a
 *  dialog's `close` and `cancel` up its tree, so they stop here before the window's own close. */
function Zoom({ src, close }: { src: string; close: () => void }) {
  const box = useRef<HTMLDialogElement>(null)
  const opener = useRef<Element | null>(null)
  useEffect(() => {
    if (!box.current || box.current.open) return
    opener.current = document.activeElement
    box.current.showModal()
  }, [])
  return (
    <dialog ref={box} aria-label="화면 사진" onCancel={(e) => e.stopPropagation()}
      onClose={(e) => { e.stopPropagation(); close(); (opener.current as HTMLElement | null)?.focus() }}
      onClick={(e) => { e.stopPropagation(); if (e.target === box.current) box.current.close() }}
      className="m-auto max-h-[95dvh] max-w-[95vw] bg-transparent p-0 backdrop:bg-black/70">
      <div className="flex flex-col items-end gap-2">
        <Btn autoFocus onClick={() => box.current?.close()}>닫기</Btn>
        <img src={src} alt="" className="max-h-[85dvh] max-w-full" />
      </div>
    </dialog>
  )
}

const decided = (d?: Decision) => !!d && (d.approve === true || !!d.edit?.trim())
const clean = (all: Record<string, Decision>, items: string[]) =>
  Object.fromEntries(items.map((i) => [i, all[i]?.edit?.trim() ? { edit: all[i].edit!.trim() } : { approve: true }]))

function Items({ title, ids, decisions, marks, set, approveAll }: {
  title: string; ids: string[]; decisions: Record<string, Decision>; marks?: Record<string, string>
  set: (id: string, d: Decision) => void; approveAll?: () => void
}) {
  if (!ids.length) return null
  // What needs a decision again comes first and open; what stays approved sits collapsed below.
  const again = ids.filter((i) => !decided(decisions[i]) || marks?.[i])
  const settled = ids.filter((i) => !again.includes(i))
  return (
    <section aria-label={title} className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <h3 className="font-semibold">{title}</h3>
        {approveAll && <Btn tone="ghost" onClick={approveAll}>모두 승인 ({ids.length})</Btn>}
      </div>
      {again.map((i) => <Item key={i} id={i} d={decisions[i]} mark={marks?.[i]} set={set} />)}
      {settled.length > 0 && (
        <details>
          <summary className="cursor-pointer text-muted-foreground">승인한 항목 {settled.length}개</summary>
          {settled.map((i) => <Item key={i} id={i} d={decisions[i]} set={set} />)}
        </details>
      )}
    </section>
  )
}

function Item({ id, d, mark, set }: { id: string; d?: Decision; mark?: string; set: (id: string, d: Decision) => void }) {
  const editing = d?.edit !== undefined
  return (
    <div className="flex flex-col gap-1 rounded-md border border-border px-2 py-1.5" data-item={id}>
      <div className="flex items-center gap-2">
        <span className="font-mono">{id}</span>
        {mark && <span className="rounded bg-wait/15 px-1 text-[11px] text-wait">다시 볼 것</span>}
        <span className="flex-1" />
        <Btn tone={d?.approve ? 'primary' : 'plain'} aria-pressed={!!d?.approve} onClick={() => set(id, { approve: true })}>승인</Btn>
        <Btn tone={editing ? 'wait' : 'plain'} aria-pressed={editing} onClick={() => set(id, { edit: d?.edit ?? '' })}>고침</Btn>
      </div>
      {editing && (
        <textarea aria-label={`${id} 고칠 내용`} value={d?.edit ?? ''} rows={2} onChange={(e) => set(id, { edit: e.target.value })}
          className="rounded-md border border-input bg-background px-2 py-1 text-foreground" />
      )}
    </div>
  )
}

// -- The feature board ------------------------------------------------------------------

function Board({ board, busy, submit }: {
  board: NonNullable<SpecView['board']>; busy: boolean; submit: (f: Record<string, Freq>, line: string) => Promise<unknown>
}) {
  const [map, setMap] = useState<Record<string, Freq>>(board.frequency)
  const [line, setLine] = useState(board.first_screen ?? '')
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
  return (
    <section aria-label="기능 보드" className="flex flex-col gap-3">
      <label className="flex flex-col gap-1">
        <span className="font-semibold">첫 화면에서 바로 하는 일</span>
        <input value={line} onChange={(e) => setLine(e.target.value)}
          className="h-7 rounded-md border border-input bg-background px-2 text-foreground" />
      </label>
      <p className="text-muted-foreground">카드를 끌어 옮기거나, 카드에 초점을 두고 ← → 로 옮긴다.</p>
      <div className="grid gap-2 sm:grid-cols-4">
        {COLUMNS.map((c) => (
          <Column key={c.id} label={c.label} drop={() => dragged && move(dragged, c.id)}>
            {board.features.filter((f) => map[f.id] === c.id).map((f) => (
              <div key={f.id} data-card={f.id} tabIndex={0} draggable role="button"
                aria-label={`${f.name} — ${c.label}`} onDragStart={() => setDragged(f.id)} onDragEnd={() => setDragged('')}
                onKeyDown={(e) => step(e, f)}
                className="cursor-grab rounded-md border border-border bg-background px-2 py-1.5 focus:outline-2 focus:outline-primary">
                <p className="font-semibold">{f.name}</p>
                <p className="text-[11.5px] text-muted-foreground">{f.why}</p>
              </div>
            ))}
          </Column>
        ))}
      </div>
      <div className="flex justify-end">
        <Btn tone="primary" disabled={busy || !line.trim()} onClick={() => void submit(map, line)}>보드 제출</Btn>
      </div>
    </section>
  )
}

function Column({ label, drop, children }: { label: string; drop: () => void; children: ReactNode }) {
  return (
    <div aria-label={label} role="group" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); drop() }}
      className="flex min-h-24 flex-col gap-1.5 rounded-md bg-secondary/50 p-2">
      <h3 className="text-[12px] font-semibold">{label}</h3>
      {children}
    </div>
  )
}

// -- The finished run -------------------------------------------------------------------

const GATE_NAMES: Record<string, string> = { r1: 'R1 백엔드 설계', r2: 'R2 화면 설계', r3: 'R3 완성 화면' }

/** The one summary a finished run keeps: what was asked, each review and how it was read, what it cost. */
function Finished({ summary }: { summary: RunSummary }) {
  const s = summary.spend
  const hours = ((s.seconds ?? 0) / 3600).toFixed(1)
  return (
    <section aria-label="실행 요약" className="flex flex-col gap-3">
      <p className="text-muted-foreground">{summary.sentence}</p>
      {summary.pr && (
        <p>풀 리퀘스트 <a href={summary.pr.url} target="_blank" rel="noreferrer" className="font-mono underline">#{summary.pr.number}</a>
          {' '}— 리뷰 루프는 작업 목록의 이 명세에서 시작한다.</p>
      )}
      <table className="w-full text-left">
        <thead><tr className="text-muted-foreground"><th>검토</th><th>회차</th><th>고친 항목</th><th>읽은 시간</th><th>펼친 부분</th></tr></thead>
        <tbody>
          {summary.gates.map((g) => (
            <tr key={g.gate}>
              <td>{GATE_NAMES[g.gate] ?? g.gate}</td><td>{g.rounds}</td><td>{g.edited.length}</td>
              <td>{Math.round(g.reading.reduce((n, r) => n + (r.seconds ?? 0), 0) / 60)}분</td>
              <td>{[...new Set(g.reading.flatMap((r) => r.opened))].join(', ') || '없음'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>
        비용 ${(s.usd ?? 0).toFixed(2)}{s.unknown ? ` (추정 ${s.unknown}턴 포함)` : ''} · 시간 {hours}시간 · 턴 {s.calls ?? 0}개 ·
        화면 찍기 {summary.looks}회 · 화면 비평 {summary.critiques}회
      </p>
    </section>
  )
}
