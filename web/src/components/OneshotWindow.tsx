import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Plus, Rocket } from 'lucide-react'
import { Btn } from '@/components/Modal'
import { Body, Intake, api, useAct, type OneshotRun } from '@/components/Oneshot'
import { Fault, Lead, ModelsList, OneshotSpec, Top } from '@/components/OneshotSpec'
import { follow, forget, go, rows, useRoute, useSpecList, useSpecs, type Route } from '@/lib/mode'
import { STAGES, type Models, type SpecSummary, type SpecView } from '@/lib/oneshot'
import { cn } from '@/lib/utils'

/** The oneshot window (`DESIGN.md`, "Oneshot window"): the runs on the left, and the chosen run on the
 *  right under a header of its name, sentence, models and the eighteen stages as one spine. Each stage
 *  lays itself out (`OneshotSpec` `Split`, `Top`). Maintenance is the other window; the switch at the top
 *  of both rails moves between them, and both stay mounted. Under 1100px the list is a drawer. */

// S1's three parts come before the spec's stages; `talk` and `gate` wait for the owner.
const ALL = [
  { id: 'intake', label: '하고 싶은 것', kind: 'talk' }, { id: 'environment', label: '환경 카드', kind: 'gate' },
  { id: 'bootstrap', label: '저장소 만들기', kind: 'auto' }, ...STAGES,
] as const satisfies readonly { id: string; label: string; kind: 'talk' | 'gate' | 'auto' }[]
const PHASES: { name: string; ids: string[] }[] = [
  { name: '시작', ids: ['intake', 'environment', 'bootstrap'] }, { name: '요구', ids: ['spec'] },
  { name: '백엔드', ids: ['design', 'r1', 'tests', 'build', 'review'] },
  { name: '화면', ids: ['split', 'board', 'frontend', 'r2', 'build-front', 'critique', 'r3'] },
  { name: '마무리', ids: ['pr', 'done'] },
]
const HINT: Record<string, string> = {
  intake: '한두 문장을 에이전트가 다시 정리한다. 맞으면 [맞다], 틀린 곳은 고쳐 적는다.',
  environment: 'OS, 로그인, 공개 범위 같은 환경을 정한다. 확정하면 GitHub 에 저장소가 생긴다.',
  bootstrap: '폴더, 첫 커밋, GitHub 저장소, 명세 작업 공간을 차례로 만든다. 혼자 돈다.',
  spec: '에이전트와 요구를 정한다. 질문에 답하고, 스택도 여기서 고른다.',
  design: '정한 요구로 백엔드 설계를 쓴다. 혼자 돈다.',
  r1: '백엔드 설계를 항목마다 승인하거나 고친다.',
  tests: '설계로 계약 테스트를 쓴다. 혼자 돈다.',
  build: '계약 테스트를 통과하는 백엔드를 만든다. 혼자 돈다.',
  review: '독립 리뷰가 백엔드를 본다. 두 번 거절하면 멈춘다.',
  split: '기능을 화면에 놓을 단위로 나눈다. 혼자 돈다.',
  board: '기능을 얼마나 자주 쓰는지 칸으로 옮기고, 첫 화면에서 하는 일을 한 줄로 적는다.',
  frontend: '기능 보드로 화면을 설계한다. 혼자 돈다.',
  r2: '화면 설계를 항목마다 정하고 색 배합을 하나 고른다.',
  'build-front': '화면을 만든다. 혼자 돈다.',
  critique: '만든 화면을 찍어 비평하고 고친다. 혼자 돈다.',
  r3: '완성 화면을 항목마다 승인하거나 고친다.',
  pr: '풀 리퀘스트를 연다. 혼자 돈다.',
  done: '끝났다. 리뷰 루프는 유지보수 창의 작업 목록에서 이어진다.',
}
const at = (stage: string) => ALL.findIndex((s) => s.id === stage)

/** Where a run is, read the same way from a run before S2, a spec past it, or the start form. */
type Now = {
  stage: string; name: string; sentence: string; models?: Models
  working: boolean; stopped: boolean; waits: boolean; line?: string
}
const fromRun = (r: OneshotRun): Now => ({
  stage: r.state, name: r.identity?.name ?? r.intent?.name ?? '새 실행', sentence: r.sentence, models: r.models,
  working: !!r.working, stopped: !!r.stopped, waits: !r.working && !r.stopped && (r.state === 'environment' || (r.state === 'intake' && !!r.intent)),
})
const fromSpec = (s: SpecSummary): Now => ({
  stage: s.stage, name: s.repo, sentence: '', working: !!s.working, stopped: !!s.stopped, waits: s.waits,
})
const fromView = (v: SpecView): Now => ({
  ...fromSpec(v), sentence: v.oneshot.sentence, models: v.oneshot.models,
  line: [v.oneshot.attempts ? `${v.oneshot.attempts + 1}번째 시도` : '',
    v.stage === 'critique' && v.oneshot.critiques?.length ? `비평 ${v.oneshot.critiques.length}회차` : ''].filter(Boolean).join(' · '),
})
const FRESH: Now = { stage: 'intake', name: '새 실행', sentence: '', working: false, stopped: false, waits: false }
/** A run that needs the owner: one that waits for a decision, or one that stopped and waits for `[재개]`. */
const needs = (n: { waits: boolean; stopped: unknown }) => n.waits || !!n.stopped

/** The two windows, at the same place at the top of both rails. The count is the runs that need the owner. */
export function ModeSwitch({ mode, className, count }: { mode: Route['mode']; className?: string; count?: number }) {
  // The oneshot window passes its own count, which also holds the runs not yet past their bootstrap.
  const specs = useSpecs()
  const n = count ?? specs.filter(needs).length
  return (
    <div role="tablist" aria-label="창" className={cn('grid min-h-8 grid-cols-2 rounded-lg border border-sidebar-border p-0.5', className)}>
      {(['main', 'oneshot'] as const).map((m) => (
        <button key={m} type="button" role="tab" aria-selected={mode === m} onClick={() => mode !== m && go(m)}
          className={cn('inline-flex items-center justify-center gap-1.5 rounded-md text-[13px]',
            mode === m ? 'bg-card text-foreground shadow-[0_0_0_1px_var(--border)]' : 'text-muted-foreground hover:text-foreground')}>
          {m === 'main' ? '유지보수' : '원샷'}
          {m === 'oneshot' && n > 0 && (
            <span aria-label={`결정이 필요한 실행 ${n}개`}
              className="min-w-[18px] rounded-full bg-wait px-[5px] py-[3px] font-mono text-[11px] leading-none tabular-nums text-wait-foreground">{n}</span>
          )}
        </button>
      ))}
    </div>
  )
}

/** The switch on the folded maintenance rail: one icon to the oneshot window, dotted when a run needs the owner. */
export function OneshotIcon() {
  const waiting = useSpecs().some(needs)
  const label = waiting ? '원샷 창 — 결정을 기다리는 실행이 있다' : '원샷 창'
  return (
    <button type="button" aria-label={label} title={label} onClick={() => go('oneshot')}
      className="relative grid size-8 place-items-center rounded-md hover:bg-sidebar-accent">
      <Rocket className="size-4" />
      {waiting && <span aria-hidden className="absolute top-1 right-1 size-1.5 rounded-full bg-wait" />}
    </button>
  )
}

export function OneshotWindow() {
  const { mode, run: key } = useRoute()
  const { specs, removed } = useSpecList()
  const [runs, setRuns] = useState<OneshotRun[]>([])
  const [told, setTold] = useState<{ key: string; now: Now } | null>(null)   // what the open pane last read
  const [held, setHeld] = useState('')   // the run shown when the window was last on screen
  const [drawer, setDrawer] = useState(false)   // the list under 1100px, where it slides over the run
  const readRuns = useCallback(() => {
    // A failed read keeps the list it had, so the rows stay and the 3s re-read below keeps going.
    api.oneshotRuns().then((d) => setRuns(rows<OneshotRun>(d?.runs, ['id', 'state']))).catch(() => {})
  }, [])
  useEffect(() => {
    readRuns()
    window.addEventListener('focus', readRuns)
    return () => window.removeEventListener('focus', readRuns)
  }, [readRuns, key])

  const open = specs.filter((s) => s.stage !== 'done')
  const early = runs.filter((r) => r.state !== 'spec')
  // The server announces no S1 change on the feed: while one runs, the list reads again on its own.
  const running = early.some((r) => r.working)
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(readRuns, 3000)
    return () => window.clearInterval(timer)
  }, [running, readRuns])
  // Nothing picked: the first run that waits, else the first still going, else the start form. Once a run
  // is shown it stays until another is picked, so a list that reorders never swaps out a half-typed answer;
  // while maintenance shows, the run shown before stays too.
  const first = [...open.filter((s) => s.waits), ...open].map((s) => `spec:${s.repo}`)[0]
    || (early[0] ? `run:${early[0].id}` : 'new')
  // A run whose spec a later read no longer lists was deleted: it is neither kept nor reopened.
  const kept = (k: string) => !!k && !(k.startsWith('spec:') && removed.includes(k.slice(5)))
  const live = (kept(key) && key) || (kept(held) && held !== 'new' ? held : first)
  const picked = mode === 'main' && kept(held) ? held : live
  if (mode === 'oneshot' && held !== live) setHeld(live)
  useEffect(() => { removed.forEach((r) => forget(`spec:${r}`)) }, [removed])
  // Until the pane has read its run, the list's row stands for it.
  const listed = specs.find((s) => `spec:${s.repo}` === picked)
  const starting = early.find((r) => `run:${r.id}` === picked)
  const current = picked === 'new' ? FRESH : told?.key === picked ? told.now
    : listed ? fromSpec(listed) : starting ? fromRun(starting) : FRESH
  // A run picked from the drawer takes the focus with it, to the run's title, as the drawer closes over the row.
  const pick = (k: string) => {
    if (drawer) requestAnimationFrame(() => document.querySelector<HTMLElement>('.oneshot-shell main h1')?.focus())
    setDrawer(false)
    go('oneshot', k)
  }
  // What the open S1 pane reads is also the list's row for it, so the row and the count move with the pane.
  const onRun = useCallback((run: OneshotRun) => {
    setTold({ key: picked, now: fromRun(run) })
    setRuns((all) => all.some((r) => r.id === run.id) ? all.map((r) => r.id === run.id ? run : r) : [...all, run])
  }, [picked])
  const onView = useCallback((v: SpecView) => setTold({ key: picked, now: fromView(v) }), [picked])

  // The drawer closes on Escape, and on its own once the window is wide enough to show the list beside the run.
  useEffect(() => {
    if (!drawer) return
    const wide = window.matchMedia('(min-width: 1100px)')
    // The toggle is inert until the drawer has closed, so the focus goes back to it on the next frame.
    const shut = () => {
      setDrawer(false)
      requestAnimationFrame(() => document.querySelector<HTMLElement>('[aria-controls="oneshot-runs"]')?.focus())
    }
    // Tab and Shift+Tab go round the drawer's own controls; the window behind is inert while it is open.
    const key = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') return shut()
      if (e.key !== 'Tab') return
      const stops = [...document.querySelectorAll<HTMLElement>('#oneshot-runs button, #oneshot-runs summary')]
        .filter((el) => el.offsetParent !== null)
      if (!stops.length) return
      const at = stops.indexOf(document.activeElement as HTMLElement)
      const to = at < 0 ? 0 : (at + (e.shiftKey ? -1 : 1) + stops.length) % stops.length
      e.preventDefault()
      stops[to].focus()
    }
    const grow = () => { if (wide.matches) setDrawer(false) }
    document.querySelector<HTMLElement>('#oneshot-runs [aria-current=page], #oneshot-runs nav button')?.focus()
    window.addEventListener('keydown', key)
    wide.addEventListener('change', grow)
    return () => { window.removeEventListener('keydown', key); wide.removeEventListener('change', grow) }
  }, [drawer])

  const active = [
    ...open.map((s) => ({ key: `spec:${s.repo}`, now: fromSpec(s) })),
    ...early.map((r) => ({ key: `run:${r.id}`, now: fromRun(r) })),
  ]
  const need = active.filter((r) => needs(r.now))
  const going = active.filter((r) => !needs(r.now))
  const done = specs.filter((s) => s.stage === 'done').map((s) => ({ key: `spec:${s.repo}`, now: fromSpec(s) }))
  const row = (r: { key: string; now: Now }) => <Row key={r.key} now={r.now} on={picked === r.key} pick={() => pick(r.key)} />

  const lead = picked !== 'new' && (
    <div className="flex flex-col gap-1">
      <h2 className="flex flex-wrap items-center gap-2.5 font-heading text-[20px] font-semibold">
        {ALL[Math.max(0, at(current.stage))].label} <Badge now={current} />
      </h2>
      {HINT[current.stage] && <p className="max-w-[44em] text-pretty text-muted-foreground">{HINT[current.stage]}</p>}
    </div>
  )

  return (
    <div className="oneshot-shell relative grid h-dvh overflow-hidden bg-background text-foreground grid-cols-[17rem_minmax(0,1fr)]
      max-[1100px]:grid-cols-1 max-[1100px]:grid-rows-[auto_minmax(0,1fr)]">
      <div inert={drawer} className="flex items-center justify-between gap-2.5 border-b border-sidebar-border bg-sidebar px-4 py-2 min-[1100px]:hidden">
        <ModeSwitch mode="oneshot" count={need.length} className="flex-[0_1_14rem]" />
        <Btn aria-expanded={drawer} aria-controls="oneshot-runs" onClick={() => setDrawer((o) => !o)}>실행 목록</Btn>
      </div>
      {drawer && <div aria-hidden className="absolute inset-0 z-20 bg-black/35 min-[1100px]:hidden" onClick={() => setDrawer(false)} />}

      <aside id="oneshot-runs" className={cn('flex min-h-0 flex-col border-r border-sidebar-border bg-sidebar',
        'max-[1100px]:absolute max-[1100px]:inset-y-0 max-[1100px]:left-0 max-[1100px]:z-30 max-[1100px]:w-[min(20rem,88%)] max-[1100px]:shadow-[8px_0_32px_rgb(0_0_0/0.25)]',
        !drawer && 'max-[1100px]:hidden')}>
        <div className="flex shrink-0 flex-col gap-2.5 border-b border-sidebar-border p-3.5">
          <span className="font-heading text-[16px] font-semibold">wiki-agent</span>
          <ModeSwitch mode="oneshot" count={need.length} className="max-[1100px]:hidden" />
          <button type="button" onClick={() => pick('new')} aria-current={picked === 'new' ? 'page' : undefined}
            className={cn('flex min-h-9 items-center gap-1.5 rounded-lg border border-dashed border-sidebar-border px-3 text-[13px] text-muted-foreground hover:bg-sidebar-accent',
              picked === 'new' && 'border-solid bg-card text-foreground')}>
            <Plus className="size-3.5" /> 새 실행
          </button>
        </div>
        <nav aria-label="원샷 실행" className="flex min-h-0 flex-1 flex-col gap-[18px] overflow-y-auto px-2.5 pt-3 pb-5">
          {need.length > 0 && <Group label="결정이 필요한 것" count={need.length} tone="wait">{need.map(row)}</Group>}
          {going.length > 0 && <Group label="도는 중" count={going.length}>{going.map(row)}</Group>}
          {!active.length && <p className="px-2 text-[12.5px] text-faint">도는 실행이 없다. [새 실행] 으로 시작한다.</p>}
          {done.length > 0 && (
            <details className="group">
              <summary className="flex cursor-pointer list-none justify-between px-2 pb-2 font-heading text-[11.5px] font-semibold tracking-wide text-faint">
                <span>끝남 {done.length}</span><span className="font-normal group-open:hidden">펼치기</span><span className="hidden font-normal group-open:inline">접기</span>
              </summary>
              <div className="flex flex-col gap-0.5">{done.map(row)}</div>
            </details>
          )}
        </nav>
      </aside>

      <main inert={drawer} className="flex min-h-0 min-w-0 flex-col">
        {picked !== 'new' && (
          <header className="grid shrink-0 grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-6 gap-y-0.5 border-b border-border bg-card px-8 pt-4 pb-3.5
            max-[1500px]:px-7 max-[560px]:px-4">
            <h1 tabIndex={-1} className="truncate font-mono text-[17px] font-semibold outline-none">{current.name}</h1>
            <ModelsList models={current.models} className="row-span-2 self-start max-[1100px]:hidden" />
            <p title={current.sentence} className="truncate text-muted-foreground max-[1100px]:col-span-2">{current.sentence}</p>
            <Spine now={current} />
          </header>
        )}
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          <Lead.Provider value={lead}>
            {picked === 'new' && <Start onStarted={readRuns} />}
            {picked.startsWith('run:') && <RunPane key={picked} id={picked.slice(4)} onRun={onRun} />}
            {picked.startsWith('spec:') && <OneshotSpec key={picked} repo={picked.slice(5)} onView={onView} />}
          </Lead.Provider>
        </div>
      </main>
    </div>
  )
}

function Group({ label, count, tone, children }: { label: string; count: number; tone?: 'wait'; children: ReactNode }) {
  return (
    <section aria-label={label} className="flex flex-col gap-0.5">
      <h3 className={cn('flex justify-between px-2 pb-2 font-heading text-[11.5px] font-semibold tracking-wide', tone ? 'text-wait' : 'text-faint')}>
        <span>{label}</span><span className="font-mono">{count}</span>
      </h3>
      {children}
    </section>
  )
}

/** A run in the list: its name and how far it is, then the stage it is at, or why it stopped. */
function Row({ now, on, pick }: { now: Now; on: boolean; pick: () => void }) {
  const done = now.stage === 'done'
  const step = Math.max(0, at(now.stage))
  return (
    <button type="button" onClick={pick} aria-current={on ? 'page' : undefined}
      className={cn('grid w-full grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-2 rounded-lg px-2.5 py-2 text-left [@media(hover:hover)]:hover:bg-sidebar-accent',
        on && 'bg-card shadow-[0_0_0_1px_var(--border)]')}>
      <span className="truncate font-mono text-[13px]">{now.name}</span>
      <span className="font-mono text-[11px] tabular-nums text-faint">{done ? '' : `${step + 1}/${ALL.length}`}</span>
      <span className={cn('col-span-2 truncate text-[12.5px]', now.stopped ? 'text-wait' : 'text-muted-foreground')}>
        {now.stopped ? `멈춤 · ${ALL[step].label}` : ALL[step].label}
      </span>
    </button>
  )
}

/** The eighteen stages as ticks under their phases: filled behind, solid now (amber when it needs the owner),
 *  and outlined ahead where the owner decides. */
function Spine({ now }: { now: Now }) {
  const here = at(now.stage)
  const done = now.stage === 'done'
  return (
    <ol aria-label={`원샷 단계 ${Math.min(here + 1, ALL.length)}/${ALL.length}`} className="col-span-2 mt-3 flex max-w-[64rem] gap-4 max-[560px]:gap-1.5">
      {PHASES.map((p) => (
        <li key={p.name} className="flex min-w-0 flex-col gap-1.5" style={{ flex: `${p.ids.length} 1 0` }}>
          <span aria-hidden className={cn('whitespace-nowrap font-heading text-[11px] font-semibold tracking-wide',
            p.ids.includes(now.stage) ? 'text-foreground' : 'text-faint')}>{p.name}</span>
          <ol className="flex gap-[3px]">
            {p.ids.map((id) => {
              const i = at(id)
              const s = ALL[i]
              const past = i < here || done
              const current = i === here && !done
              return (
                <li key={id} aria-current={current ? 'step' : undefined} title={`${s.label}${s.kind !== 'auto' ? ' · 주인이 정함' : ''}`}
                  className={cn('h-1.5 min-w-1.5 flex-1 rounded-[2px]',
                    past ? 'bg-progress/50' : current ? (needs(now) ? 'bg-wait' : 'bg-progress')
                      : s.kind !== 'auto' ? 'bg-secondary shadow-[inset_0_0_0_1px_var(--faint)]' : 'bg-secondary shadow-[inset_0_0_0_1px_var(--border)]')}>
                  <span className="sr-only">{s.label} — {past ? '지남' : current ? '지금' : '남음'}</span>
                </li>
              )
            })}
          </ol>
        </li>
      ))}
    </ol>
  )
}

function Badge({ now }: { now: Now }) {
  const base = 'whitespace-nowrap rounded-[10px] px-[9px] py-1 font-sans text-[12px] font-medium leading-none'
  if (now.stopped) return <span className={cn(base, 'bg-wait/10 text-wait shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--wait)_40%,transparent)]')}>멈춤</span>
  if (now.waits) return <span className={cn(base, 'bg-wait/10 text-wait shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--wait)_40%,transparent)]')}>결정 필요</span>
  if (now.working) return <span role="status" className={cn(base, 'bg-progress/15 text-progress')}>진행 중{now.line ? ` · ${now.line}` : ''}</span>
  return null
}

/** The start form, alone in the middle of an empty window. */
function Start({ onStarted }: { onStarted: () => void }) {
  // An answer that lands after another run was picked only joins the list; it opens only while this form shows.
  const shown = useRef(true)
  useEffect(() => {
    shown.current = true
    return () => { shown.current = false }
  }, [])
  const { fault, busy, act } = useAct((run) => {
    onStarted()
    if (shown.current) follow(`run:${run.id}`)
  })
  return (
    <div className="m-auto flex w-full max-w-[44rem] flex-col gap-5 px-6 py-10 max-[560px]:px-4">
      <div className="flex flex-col gap-1">
        <h1 tabIndex={-1} className="font-heading text-[22px] font-semibold outline-none">새 실행</h1>
        <p className="text-pretty text-muted-foreground">만들고 싶은 것을 한두 문장으로 적으면, 18단계를 차례로 지나 풀 리퀘스트까지 간다.
          처음의 확인과 환경 카드, 요구 대화, 세 번의 검토, 기능 보드에서만 결정을 기다린다.</p>
      </div>
      <Fault text={fault} />
      <div className="rounded-xl border border-border bg-card p-6 max-[560px]:p-4"><Intake busy={busy} act={act} /></div>
    </div>
  )
}

/** A run before S2, read until its stage settles. Once the bootstrap hands it to its spec, the spec opens. */
function RunPane({ id, onRun }: { id: string; onRun: (run: OneshotRun) => void }) {
  const [run, setRun] = useState<OneshotRun | null>(null)
  const { fault, setFault, busy, act } = useAct(setRun)
  useEffect(() => { api.oneshotRun(id).then(setRun).catch((err) => setFault(String(err instanceof Error ? err.message : err))) }, [id, setFault])
  const working = !!run?.working
  useEffect(() => {
    if (!working) return
    const timer = window.setInterval(() => { api.oneshotRun(id).then(setRun).catch(() => {}) }, 1500)
    return () => window.clearInterval(timer)
  }, [id, working])
  useEffect(() => {
    if (!run) return
    if (run.state === 'spec' && run.spec) follow(`spec:${run.spec.repo}`)
    else onRun(run)
  }, [run, onRun])
  return run ? <Body run={run} busy={busy} act={act} fault={fault} />
    : <Top><Fault text={fault} />{!fault && <p role="status" className="text-muted-foreground">읽는 중…</p>}</Top>
}
