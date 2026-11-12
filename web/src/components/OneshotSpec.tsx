import { createContext, useContext, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Choices } from '@/components/Blocks'
import { Btn } from '@/components/Modal'
import { api, COLUMNS, STOP, type Decision, type Feature, type Freq, type Gate, type Palette,
  type Models, type RunSummary, type SpecView, type Talk, ROLE, modelName } from '@/lib/oneshot'
import { cn } from '@/lib/utils'

/** Which model each part of the run uses, fixed at its start. */
export function ModelsList({ models, className }: { models?: Models; className?: string }) {
  if (!models) return <p title="「다음 작업」 창에서 고른 모델을 쓴다" className={cn('text-[12px] text-faint', className)}>모델을 고르기 전에 시작한 실행</p>
  return (
    <dl className={cn('grid grid-cols-[auto_auto] gap-x-2.5 gap-y-0.5 text-[12px] text-faint', className)}>
      {(Object.keys(ROLE) as (keyof Models)[]).map((k) => [
        <dt key={`${k}-t`}>{ROLE[k]}</dt>, <dd key={`${k}-d`} className="text-muted-foreground">{modelName(k, models[k])}</dd>,
      ])}
      <dt>화면 비평</dt><dd className="text-muted-foreground">opus · high (고정)</dd>
    </dl>
  )
}

// -- The stage's layouts (`DESIGN.md`, "Oneshot window") ---------------------------------

/** The stage's title, badge and hint, which the window hands down and the layout puts at its top-left. */
export const Lead = createContext<ReactNode>(null)

/** The work on the left and what it makes or rests on beside it, each scrolling on its own;
 *  under 1100px the side goes below the work and the window scrolls as one. */
export function Split({ label, side, sideLabel, children }: { label: string; side: ReactNode; sideLabel: string; children: ReactNode }) {
  const lead = useContext(Lead)
  return (
    <section aria-label={label} className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_34rem] max-[1500px]:grid-cols-[minmax(0,1fr)_24rem]
      max-[1100px]:flex max-[1100px]:flex-none max-[1100px]:flex-col">
      <div className="flex min-h-0 flex-col overflow-y-auto max-[1100px]:shrink-0 max-[1100px]:overflow-visible">
        {lead && <div className="shrink-0 px-10 pt-6 max-[1500px]:px-7 max-[560px]:px-4">{lead}</div>}
        <Lead.Provider value={null}>{children}</Lead.Provider>
      </div>
      <aside aria-label={sideLabel} className="flex min-h-0 flex-col gap-4 overflow-y-auto border-l border-border bg-card px-7 pt-6 pb-0
        max-[1100px]:shrink-0 max-[1100px]:overflow-visible max-[1100px]:border-t max-[1100px]:border-l-0 max-[560px]:px-4">{side}</aside>
    </section>
  )
}

/** One column under the stage's title, with the title's left edge. */
export function Top({ wide, children }: { wide?: boolean; children: ReactNode }) {
  return <div className={cn('flex w-full flex-col gap-5 px-10 pt-6 pb-8 max-[1500px]:px-7 max-[560px]:px-4', !wide && 'max-w-[60rem]')}>{useContext(Lead)}{children}</div>
}

/** A side or card's label. */
export function Title({ children }: { children: ReactNode }) {
  return <h3 className="flex justify-between gap-2 font-heading text-[12px] font-semibold tracking-wide text-faint">{children}</h3>
}

/** The button that sends a decision: larger than the row buttons, and amber, since the run waits on it. */
export const GO = 'h-9 justify-center px-4 text-[13px] font-medium'

/** The decision, held at the bottom of the side while what it decides on scrolls. */
function Decide({ children }: { children: ReactNode }) {
  return <div className="sticky bottom-0 -mx-7 mt-auto flex flex-col gap-2 border-t border-border bg-card px-7 py-4 max-[560px]:-mx-4 max-[560px]:px-4">{children}</div>
}

export function Fault({ text }: { text: string }) {
  return text ? <p role="alert" className="whitespace-pre-wrap rounded-md bg-destructive/10 px-3 py-2 text-destructive">{text}</p> : null
}

/** A run that stopped: the reason first, and `[재개]`. */
export function Halt({ title, detail, children }: { title: string; detail: string; children: ReactNode }) {
  return (
    <section role="alert" className="flex flex-col gap-3 rounded-lg border border-wait/40 bg-wait/10 px-5 py-4">
      <p className="font-heading text-[15px] font-semibold">{title}</p>
      <p className="max-w-[44em] whitespace-pre-wrap">{detail}</p>
      <div className="flex flex-wrap gap-2">{children}</div>
    </section>
  )
}

/** A run past S1 (`docs/plans/oneshot/5-screen-pr.md`): the S2 conversation beside the requirements it
 *  gathered, the three gate sheets beside their decisions, the feature board beside the first screen it
 *  makes, and the summary. The server renders every sheet and owns every rule; this view only shows,
 *  folds and collects decisions. */

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

  if (!view) return <Top><p role="status" className="text-muted-foreground">{fault || '읽는 중…'}</p></Top>
  const o = view.oneshot
  const idle = !view.working && !view.stopped
  const faulted = fault && <div className="px-10 pt-4 max-[1500px]:px-7 max-[560px]:px-4"><Fault text={fault} /></div>
  if (view.stage === 'spec') {
    return <>{faulted}<Conversation talk={o.talk ?? []} requirements={view.requirements ?? []} stack={o.stack ?? []}
      halt={view.stopped && <Halt title={STOP[view.stopped.reason] ?? view.stopped.reason} detail={view.stopped.detail}>
        <Btn tone="wait" className={GO} disabled={busy} onClick={() => void act(() => api.resume(repo))}>재개</Btn></Halt>}
      busy={busy || !idle} send={(text) => act(() => api.say(repo, text))} /></>
  }
  if (view.stopped) {
    return (
      <Top>
        <Fault text={fault} />
        <Halt title={STOP[view.stopped.reason] ?? view.stopped.reason} detail={view.stopped.detail}>
          <Btn tone="wait" className={GO} disabled={busy} onClick={() => void act(() => api.resume(repo))}>재개</Btn>
        </Halt>
      </Top>
    )
  }
  return (
    <>
      {faulted}
      {idle && view.stage === 'r1' && view.sheet && o.r1 && (
        <Sheet key={`r1-${o.r1.round}`} gate="r1" text={view.sheet} items={view.items ?? []} state={o.r1} busy={busy}
          submit={(body) => act(() => api.decide(repo, 'r1', body))} />
      )}
      {idle && view.stage === 'board' && view.board && (
        <Board name={view.repo} board={view.board} busy={busy} submit={(f, line) => act(() => api.board(repo, f, line))} />
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
      {view.working && view.stage !== 'done' && (
        <Top><p className="text-muted-foreground">혼자 도는 단계다. 끝나면 다음 단계가 이 자리에 열린다.</p></Top>
      )}
    </>
  )
}

const PART: Record<string, string> = {
  backend: '백엔드', frontend: '화면', styling: '스타일', data_store: '저장소', tests: '테스트', deploy: '배포',
}

/** The S2 conversation filling the height with its answer box held at the bottom, beside the requirements and
 *  the stack it has gathered so far. */
function Conversation({ talk, requirements, stack, halt, busy, send }: {
  talk: Talk[]; requirements: NonNullable<SpecView['requirements']>; stack: NonNullable<SpecView['oneshot']['stack']>
  halt: ReactNode; busy: boolean; send: (text: string) => Promise<boolean>
}) {
  const [text, setText] = useState('')
  const thread = useRef<HTMLDivElement>(null)
  useEffect(() => { thread.current?.scrollTo({ top: thread.current.scrollHeight }) }, [talk.length])
  // Cleared only once the server took it, and only if nothing new was typed meanwhile.
  const submit = async (sent: string) => { if (await send(sent)) setText((now) => (now === sent ? '' : now)) }
  const open = stack.filter((s) => s.status !== 'confirmed').length
  return (
    <Split label="요구 대화" sideLabel="모인 요구" side={<>
      <Title><span>모인 요구</span><span className="font-mono">{requirements.length}</span></Title>
      {requirements.length ? (
        <ol className="flex flex-col">
          {requirements.map((r) => (
            <li key={r.id} className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-2 border-b border-border py-2.5 last:border-b-0">
              <span className="font-mono text-[12px] text-faint">{r.id}</span><span>{r.text}</span>
            </li>
          ))}
        </ol>
      ) : <p className="text-muted-foreground">아직 없다. 질문에 답하면 여기에 쌓인다.</p>}
      <Title><span>스택</span><span className="font-mono">{open ? `${open}개 남음` : ''}</span></Title>
      <dl className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-3 gap-y-2 pb-6">
        {Object.keys(PART).map((p) => {
          const s = stack.find((x) => x.part === p)
          return [
            <dt key={`${p}-t`} className="text-muted-foreground">{PART[p]}</dt>,
            <dd key={`${p}-d`} title={s?.why} className={cn(!s || s.status !== 'confirmed' ? 'text-faint' : '')}>
              {s ? `${s.choice}${s.status === 'confirmed' ? '' : ' (아직 묻는 중)'}` : '아직 안 정함'}
            </dd>,
          ]
        })}
      </dl>
      <p className="pb-6 text-[12.5px] text-muted-foreground">스택이 다 정해지고 물을 것이 없으면 대화가 끝나고 백엔드 설계로 넘어간다.</p>
    </>}>
      <div className="flex min-h-0 flex-1 flex-col max-[1100px]:min-h-[34rem]">
        <div ref={thread} className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-10 py-5 max-[1500px]:px-7 max-[560px]:px-4">
          {halt}
          {talk.map((t, i) => (
            <div key={i} className={cn('flex max-w-[44rem] flex-col gap-1', t.role === 'owner' && 'items-end self-end', t.choices && 'max-w-[60rem]')}>
              <span className="text-[12px] text-faint">{t.role === 'agent' ? '에이전트' : '나'}</span>
              <div className={cn('whitespace-pre-wrap rounded-xl px-4 py-3', t.role === 'agent' ? 'border border-border bg-card' : 'bg-secondary')}>
                {t.text}
                {/* The question itself rides in `choices`, cut from the text: only the last turn's can be answered. */}
                {t.choices && <Choices value={t.choices} korean={false} busy={busy || i !== talk.length - 1} onSay={(a) => void send(a)} />}
              </div>
            </div>
          ))}
          {busy && !halt && <p role="status" className="text-muted-foreground">에이전트가 답을 쓰는 중…</p>}
        </div>
        <form className="flex shrink-0 items-end gap-3 border-t border-border bg-card px-10 py-3.5 max-[1500px]:px-7 max-[560px]:px-4"
          onSubmit={(e) => { e.preventDefault(); void submit(text) }}>
          <textarea aria-label="답" value={text} onChange={(e) => setText(e.target.value)} rows={2}
            placeholder="질문에 답하거나 요구를 더 적는다"
            className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-foreground max-[560px]:text-base" />
          <Btn type="submit" tone="primary" disabled={busy || !text.trim()}>보내기</Btn>
        </form>
      </div>
    </Split>
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

  // The sheet reads wide on the left; every decision on it sits beside, with [제출] held at the bottom.
  return (
    <Split label={`${gate.toUpperCase()} 시트`} sideLabel="결정" side={<>
      <Title><span>결정</span><span className="font-mono">{items.length - undecided.length}/{items.length}</span></Title>
      {palettes && palettes.length > 0 && (
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-2 font-semibold">색 배합 하나</legend>
          {palettes.map((p) => (
            <label key={p.id} className={cn('flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2', palette === p.id ? 'border-primary' : 'border-border')}>
              <input type="radio" name="palette" value={p.id} checked={palette === p.id} onChange={() => setPalette(p.id)} />
              {Object.values(p.colors).map((c) => <span key={c} className="size-4 rounded-sm border border-border" style={{ background: c }} />)}
              <span className="font-mono text-[12px]">{p.id}</span> {p.name}
            </label>
          ))}
        </fieldset>
      )}
      <Items title="펼쳐진 항목" ids={items.filter((i) => !folded.includes(i))} decisions={decisions} marks={state.marks} set={set} />
      {folded.length > 0 && (
        <Items title="접힌 부분의 항목" ids={folded} decisions={decisions} marks={state.marks} set={set}
          approveAll={() => approveAll(folded)} />
      )}
      <Decide>
        <span role="status" className="text-muted-foreground">
          {undecided.length ? `정하지 않은 항목 ${undecided.length}개` : needPalette ? '색 배합을 골라라' : '모두 정했다'}
        </span>
        <div className="flex flex-wrap justify-end gap-2">
          {reopen && <Btn disabled={busy} onClick={() => void reopen()} title="기능의 자리가 틀렸다: 화면 설계를 버리고 기능 보드로 돌아간다">기능 보드 다시 열기</Btn>}
          <Btn tone="wait" className={GO} disabled={busy || undecided.length > 0 || needPalette}
            onClick={() => void submit({ decisions: clean(decisions, items), palette: palette || undefined,
              reading: { seconds: (Date.now() - since.current) / 1000, opened } })}>
            제출
          </Btn>
        </div>
      </Decide>
    </>}>
      <div className="w-full max-w-[60rem] px-10 py-6 max-[1500px]:px-7 max-[560px]:px-4">
        {sections.map(([t, summary], n) => summary === null ? <Md key={n} text={t} zoom={setZoom} /> : (
          <details key={n} className="my-3 rounded-md border border-border px-4 py-2"
            onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) setOpened((o) => o.includes(summary) ? o : [...o, summary]) }}>
            <summary className="cursor-pointer font-semibold">{summary}</summary>
            <Md text={t} zoom={setZoom} />
          </details>
        ))}
      </div>
      {zoom && <Zoom src={zoom} close={() => setZoom('')} />}
    </Split>
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
      <div className="flex items-center justify-between gap-2">
        <h4 className="font-semibold">{title}</h4>
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
    <div className="flex flex-col gap-1.5 rounded-md border border-border bg-background px-3 py-2" data-item={id}>
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

function Board({ name, board, busy, submit }: {
  name: string; board: NonNullable<SpecView['board']>; busy: boolean; submit: (f: Record<string, Freq>, line: string) => Promise<unknown>
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
  const at = (freq: Freq) => board.features.filter((f) => map[f.id] === freq)
  // The columns on the left; beside them the first screen they make, drawn as the frequencies place it.
  return (
    <Split label="기능 보드" sideLabel="첫 화면" side={<>
      <Title><span>첫 화면</span></Title>
      <div role="img" aria-label="보드로 정해지는 첫 화면"
        className="aspect-[9/17] w-full max-w-[18rem] shrink-0 self-center rounded-[26px] bg-[#0b0c0f] p-[9px] shadow-[0_0_0_1px_var(--border)] max-[1500px]:max-w-[15rem]">
        <div className="flex h-full flex-col gap-2.5 overflow-hidden rounded-[18px] bg-background px-4 py-[18px] text-[13px]">
          <div className="flex justify-between text-[11px] text-muted-foreground"><span>9:41</span><span className="font-mono">{name}</span></div>
          <p className="font-heading text-[17px] font-semibold">{line || '첫 화면에서 하는 일'}</p>
          {at('every').map((f) => <div key={f.id} className="rounded-[10px] bg-primary px-3.5 py-3 text-center font-semibold text-primary-foreground">{f.name}</div>)}
          {at('sometimes').map((f) => <div key={f.id} className="rounded-lg bg-secondary px-3 py-2.5">{f.name}</div>)}
          {at('rarely').length > 0 && <p className="mt-auto text-[12px] text-muted-foreground">더 보기 — {at('rarely').map((f) => f.name).join(', ')}</p>}
        </div>
      </div>
      <label className="flex flex-col gap-1.5">
        <span className="font-semibold">첫 화면에서 바로 하는 일</span>
        <input value={line} onChange={(e) => setLine(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-foreground max-[560px]:text-base" />
      </label>
      <Decide>
        <Btn tone="wait" className={GO} disabled={busy || !line.trim()} onClick={() => void submit(map, line)}>보드 제출</Btn>
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

// -- The finished run -------------------------------------------------------------------

const GATE_NAMES: Record<string, string> = { r1: 'R1 백엔드 설계', r2: 'R2 화면 설계', r3: 'R3 완성 화면' }

/** The one summary a finished run keeps: what was asked, each review and how it was read, what it cost. */
function Finished({ summary }: { summary: RunSummary }) {
  const s = summary.spend
  const hours = ((s.seconds ?? 0) / 3600).toFixed(1)
  return (
    <Top wide>
      <section aria-label="실행 요약" className="flex max-w-[72rem] flex-col gap-6">
        <dl className="grid grid-cols-4 gap-px overflow-hidden rounded-xl border border-border bg-border max-[1100px]:grid-cols-2">
          {([
            ['풀 리퀘스트', summary.pr
              ? <a href={summary.pr.url} target="_blank" rel="noreferrer" className="underline underline-offset-4">#{summary.pr.number}</a> : '없음'],
            ['걸린 시간', `${hours}시간`],
            [s.unknown ? `비용 (추정 ${s.unknown}턴 포함)` : '비용', `$${(s.usd ?? 0).toFixed(2)}`],
            ['턴', String(s.calls ?? 0)],
          ] as [string, ReactNode][]).map(([k, v]) => (
            <div key={k} className="flex flex-col-reverse gap-1 bg-card px-[18px] py-4">
              <dt className="text-[12.5px] text-faint">{k}</dt>
              <dd className="font-heading text-[26px] font-semibold tabular-nums">{v}</dd>
            </div>
          ))}
        </dl>
        <div className="overflow-x-auto">
          <table className="w-full text-left [&_td]:border-t [&_td]:border-border [&_td]:py-2.5 [&_td]:pr-6 [&_th]:pb-2 [&_th]:pr-6">
            <thead><tr className="text-[12.5px] text-faint"><th>검토</th><th>회차</th><th>고친 항목</th><th>읽은 시간</th><th>펼친 부분</th></tr></thead>
            <tbody className="tabular-nums">
              {summary.gates.map((g) => (
                <tr key={g.gate}>
                  <td>{GATE_NAMES[g.gate] ?? g.gate}</td><td>{g.rounds}</td><td>{g.edited.length}</td>
                  <td>{Math.round(g.reading.reduce((n, r) => n + (r.seconds ?? 0), 0) / 60)}분</td>
                  <td>{[...new Set(g.reading.flatMap((r) => r.opened))].join(', ') || '없음'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-[12.5px] text-muted-foreground">화면 찍기 {summary.looks}회 · 화면 비평 {summary.critiques}회</p>
      </section>
    </Top>
  )
}
