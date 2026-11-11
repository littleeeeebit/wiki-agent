import { useCallback, useEffect, useState, useSyncExternalStore, type ReactNode } from 'react'
import { Plus, Rocket } from 'lucide-react'
import { Body, Intake, api, useAct, type OneshotRun } from '@/components/Oneshot'
import { ModelsLine, OneshotSpec } from '@/components/OneshotSpec'
import { go, useRoute, useSpecs, type Route } from '@/lib/mode'
import { STAGES, type Models, type SpecSummary, type SpecView } from '@/lib/oneshot'
import { cn } from '@/lib/utils'

/** The oneshot window (`DESIGN.md`, "Oneshot window"): the runs on the left, the chosen run's stages
 *  in the middle, and what that stage shows or asks on the right. Maintenance is the other window;
 *  the switch at the top of both rails moves between them, and both stay mounted. */

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
const LAST = ALL.length - 1

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

const wide = window.matchMedia('(min-width: 1280px)')
const useWide = () => useSyncExternalStore((on) => { wide.addEventListener('change', on); return () => wide.removeEventListener('change', on) },
  () => wide.matches)

/** The two windows, at the same place at the top of both rails. A dot marks a oneshot run that waits. */
export function ModeSwitch({ mode, className }: { mode: Route['mode']; className?: string }) {
  const waiting = useSpecs().some((s) => s.waits)
  return (
    <div role="tablist" aria-label="창" className={cn('grid min-h-7 grid-cols-2 rounded-md border border-sidebar-border p-0.5', className)}>
      {(['main', 'oneshot'] as const).map((m) => (
        <button key={m} type="button" role="tab" aria-selected={mode === m} onClick={() => mode !== m && go(m)}
          className={cn('inline-flex items-center justify-center gap-1.5 rounded-[4px] text-[12.5px]',
            mode === m ? 'bg-sidebar-accent text-foreground' : 'text-muted-foreground hover:text-foreground')}>
          {m === 'main' ? '유지보수' : '원샷'}
          {m === 'oneshot' && waiting && <span aria-label="결정을 기다리는 실행이 있다" className="size-2 rounded-full bg-wait" />}
        </button>
      ))}
    </div>
  )
}

/** The switch on the folded maintenance rail: one icon to the oneshot window, dotted when a run waits. */
export function OneshotIcon() {
  const waiting = useSpecs().some((s) => s.waits)
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
  const { run: key } = useRoute()
  const specs = useSpecs()
  const [runs, setRuns] = useState<OneshotRun[]>([])
  const [told, setTold] = useState<{ key: string; now: Now } | null>(null)   // what the open pane last read
  const [list, setList] = useState(false)   // the list under 1100px, where it folds above the run
  const readRuns = useCallback(() => {
    api.oneshotRuns().then((d) => setRuns(Array.isArray(d?.runs) ? d.runs : [])).catch(() => setRuns([]))
  }, [])
  useEffect(() => {
    readRuns()
    window.addEventListener('focus', readRuns)
    return () => window.removeEventListener('focus', readRuns)
  }, [readRuns, key])

  const open = specs.filter((s) => s.stage !== 'done')
  const early = runs.filter((r) => r.state !== 'spec')
  // Nothing picked: the first run that waits, else the first still going, else the start form.
  const picked = key || [...open.filter((s) => s.waits), ...open].map((s) => `spec:${s.repo}`)[0]
    || (early[0] ? `run:${early[0].id}` : 'new')
  // Until the pane has read its run, the list's row stands for it.
  const listed = specs.find((s) => `spec:${s.repo}` === picked)
  const starting = early.find((r) => `run:${r.id}` === picked)
  const current = picked === 'new' ? FRESH : told?.key === picked ? told.now
    : listed ? fromSpec(listed) : starting ? fromRun(starting) : FRESH
  const pick = (k: string) => { setList(false); go('oneshot', k) }
  const onRun = useCallback((now: Now) => setTold({ key: picked, now }), [picked])
  const onView = useCallback((v: SpecView) => setTold({ key: picked, now: fromView(v) }), [picked])

  const big = useWide()
  const stages = <Stages now={current} />

  return (
    <div className="oneshot-shell grid h-dvh overflow-hidden bg-background text-foreground grid-cols-[15rem_17rem_minmax(0,1fr)]
      max-[1280px]:grid-cols-[15rem_minmax(0,1fr)] max-[1100px]:grid-cols-1 max-[1100px]:grid-rows-[auto_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col border-r border-sidebar-border bg-sidebar max-[1100px]:border-r-0 max-[1100px]:border-b">
        <div className="flex h-11 shrink-0 items-center gap-2 border-b border-sidebar-border px-3">
          <span className="font-heading text-[15px] font-semibold">wiki-agent</span>
          <button type="button" aria-expanded={list} onClick={() => setList((o) => !o)}
            className="ml-auto h-7 rounded-md px-2 text-[12.5px] text-muted-foreground hover:bg-sidebar-accent min-[1100px]:hidden">
            실행 목록 {list ? '−' : '+'}
          </button>
        </div>
        <div className="flex shrink-0 flex-col gap-2 border-b border-sidebar-border p-3">
          <ModeSwitch mode="oneshot" />
          <button type="button" onClick={() => pick('new')} aria-current={picked === 'new' ? 'page' : undefined}
            className={cn('flex h-7 items-center justify-center gap-1.5 rounded-md border border-sidebar-border text-[12.5px] hover:bg-sidebar-accent',
              picked === 'new' && 'bg-sidebar-accent')}>
            <Plus className="size-3.5" /> 새 실행
          </button>
        </div>
        <nav aria-label="원샷 실행" className={cn('min-h-0 flex-1 overflow-y-auto p-2', !list && 'max-[1100px]:hidden')}>
          <Group label="진행 중">
            {[...open].sort((a, b) => Number(b.waits) - Number(a.waits)).map((s) => (
              <Row key={s.repo} now={fromSpec(s)} on={picked === `spec:${s.repo}`} pick={() => pick(`spec:${s.repo}`)} />
            ))}
            {early.map((r) => <Row key={r.id} now={fromRun(r)} on={picked === `run:${r.id}`} pick={() => pick(`run:${r.id}`)} />)}
            {!open.length && !early.length && <p className="px-2 text-[12.5px] text-faint">없다 — [새 실행] 으로 시작한다</p>}
          </Group>
          {specs.some((s) => s.stage === 'done') && (
            <details className="mt-4">
              <summary className="cursor-pointer px-2 font-heading text-[11px] font-semibold text-faint">끝남</summary>
              <div className="mt-1 flex flex-col gap-0.5">
                {specs.filter((s) => s.stage === 'done').map((s) => (
                  <Row key={s.repo} now={fromSpec(s)} on={picked === `spec:${s.repo}`} pick={() => pick(`spec:${s.repo}`)} />
                ))}
              </div>
            </details>
          )}
        </nav>
      </aside>

      {big && (
        <section aria-label="실행의 단계" className="flex min-h-0 flex-col border-r border-border">
          <header className="flex h-11 shrink-0 items-center border-b border-border bg-card px-4">
            <h2 className="truncate font-mono text-[12px] text-muted-foreground">{current.name}</h2>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto px-3 py-4">
            <About now={current} />
            {stages}
          </div>
        </section>
      )}

      <main className="flex min-h-0 min-w-0 flex-col">
        <header className="flex h-11 shrink-0 items-center gap-3 border-b border-border bg-card px-6">
          <h1 className="truncate font-heading text-[14px] font-semibold">
            {picked === 'new' ? '새 실행' : ALL[Math.max(0, at(current.stage))].label}
          </h1>
          <State now={current} quiet={picked === 'new'} />
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="flex max-w-[60rem] flex-col gap-4 px-6 py-5">
            {!big && (
              <details className="rounded-md border border-border bg-card px-3 py-2">
                <summary className="cursor-pointer text-[12.5px] text-muted-foreground">
                  단계 {Math.max(0, at(current.stage)) + 1}/{ALL.length} — {current.name}
                </summary>
                <div className="mt-3"><About now={current} />{stages}</div>
              </details>
            )}
            <p className="text-[13.5px] text-muted-foreground">
              {picked === 'new' ? '만들고 싶은 것을 한두 문장으로 적으면, 단계 목록을 차례로 지나 풀 리퀘스트까지 간다. ' +
                '「정함」 이 붙은 단계에서만 결정을 기다린다.' : HINT[current.stage]}
            </p>
            {picked === 'new' && <Start />}
            {picked.startsWith('run:') && <RunPane key={picked} id={picked.slice(4)} onNow={onRun} />}
            {picked.startsWith('spec:') && <OneshotSpec key={picked} repo={picked.slice(5)} onView={onView} />}
          </div>
        </div>
      </main>
    </div>
  )
}

function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section aria-label={label} className="flex flex-col gap-0.5">
      <h3 className="px-2 pb-1 font-heading text-[11px] font-semibold text-faint">{label}</h3>
      {children}
    </section>
  )
}

function Row({ now, on, pick }: { now: Now; on: boolean; pick: () => void }) {
  const done = now.stage === 'done'
  const step = Math.max(0, at(now.stage))
  return (
    <button type="button" onClick={pick} aria-current={on ? 'page' : undefined}
      className={cn('flex w-full flex-col gap-1 rounded-md px-2 py-1.5 text-left hover:bg-sidebar-accent', on && 'bg-sidebar-accent')}>
      <span className="flex items-center gap-1.5">
        <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{now.name}</span>
        {now.waits && <span aria-label="결정을 기다린다" className="size-2 shrink-0 rounded-full bg-wait" />}
      </span>
      <span className="font-mono text-[10.5px] text-muted-foreground">
        {now.stopped ? <span className="text-wait">멈춤 — {ALL[step].label}</span> : ALL[step].label}
        {done ? '' : ` · ${step + 1}/${ALL.length}`}
      </span>
      <span aria-hidden className="block h-0.5 overflow-hidden rounded-full bg-border">
        <span className="block h-full rounded-full bg-progress" style={{ width: `${(done ? 1 : step / LAST) * 100}%` }} />
      </span>
    </button>
  )
}

/** What the run was asked to make, and the models it fixed at its start. */
function About({ now }: { now: Now }) {
  if (!now.sentence) return null
  return (
    <div className="mb-5 flex flex-col gap-1.5 px-2">
      <p className="text-[13.5px]">{now.sentence}</p>
      <ModelsLine models={now.models} />
    </div>
  )
}

/** Every stage in its phase: `✓` passed, `●` now, `○` to come, and `정함` where the owner decides. */
function Stages({ now }: { now: Now }) {
  const here = at(now.stage)
  const done = now.stage === 'done'
  return (
    <nav aria-label="원샷 단계">
      <ol className="flex flex-col gap-4">
        {PHASES.map((p) => (
          <li key={p.name}>
            <h3 className="px-2 pb-1 font-heading text-[11px] font-semibold text-faint">{p.name}</h3>
            <ol className="flex flex-col">
              {p.ids.map((id) => {
                const i = at(id)
                const s = ALL[i]
                const past = i < here || done
                const current = i === here && !done
                return (
                  <li key={id} aria-current={current ? 'step' : undefined}
                    className={cn('flex h-7 items-center gap-2 rounded-md px-2 text-[12.5px]',
                      past && 'text-muted-foreground', current && 'bg-secondary text-foreground', !past && !current && 'text-faint')}>
                    <span aria-hidden className={cn('w-3 text-center font-mono text-[10.5px]',
                      current && (now.waits || now.stopped ? 'text-wait' : 'text-progress'))}>
                      {past ? '✓' : current ? '●' : '○'}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{s.label}</span>
                    {s.kind !== 'auto' && <span title="주인이 정하는 단계" className="font-mono text-[10.5px] text-faint">정함</span>}
                  </li>
                )
              })}
            </ol>
          </li>
        ))}
      </ol>
    </nav>
  )
}

function State({ now, quiet }: { now: Now; quiet: boolean }) {
  if (quiet) return null
  const [text, tone] = now.stopped ? ['멈춤 — 아래에 이유', 'text-wait'] : now.waits ? ['결정을 기다린다', 'text-wait']
    : now.working ? [`진행 중…${now.line ? ` (${now.line})` : ''}`, 'text-muted-foreground'] : now.stage === 'done' ? ['끝', 'text-faint'] : ['', '']
  return text ? <span role="status" className={cn('shrink-0 font-mono text-[10.5px]', tone)}>{text}</span> : null
}

function Fault({ text }: { text: string }) {
  return text ? <p role="alert" className="whitespace-pre-wrap rounded-md bg-destructive/10 px-3 py-2 text-destructive">{text}</p> : null
}

function Start() {
  const { fault, busy, act } = useAct((run) => go('oneshot', `run:${run.id}`))
  return <><Fault text={fault} /><Intake busy={busy} act={act} /></>
}

/** A run before S2, read until its stage settles. Once the bootstrap hands it to its spec, the spec opens. */
function RunPane({ id, onNow }: { id: string; onNow: (now: Now) => void }) {
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
    if (run.state === 'spec' && run.spec) go('oneshot', `spec:${run.spec.repo}`)
    else onNow(fromRun(run))
  }, [run, onNow])
  return <><Fault text={fault} />{run ? <Body run={run} busy={busy} act={act} /> : !fault && <p role="status" className="text-muted-foreground">읽는 중…</p>}</>
}
