import { useState } from 'react'
import { FolderGit2, Repeat, Settings as Gear } from 'lucide-react'
import { Btn, Modal } from '@/components/Modal'
import { Picker } from '@/components/Toolbar'
import type { Item } from '@/components/Toolbar'
import type { Options, Pr } from '@/lib/api'
import type { Elsewhere, Group, Phase, Task } from '@/lib/tasks'
import { cn } from '@/lib/utils'

/** What the middle pane shows. */
export type View = 'chat' | 'map' | 'projects'

type Props = {
  repo: string
  options: Options | null
  projectBusy: boolean
  view: View
  tasks: Task[]
  others: Elsewhere[]
  selected: string
  prs: Pr[]
  onProject: (repo: string) => void
  onView: (view: View) => void
  onSelect: (key: string) => void
  onLoop: (prs: number[]) => Promise<void>
  onSettings: () => void
}

const DOT: Record<Phase, string> = {
  draft: 'bg-st-draft', work: 'bg-st-work', review: 'bg-st-review', ready: 'bg-st-ready',
  queued: 'bg-st-queued', stop: 'bg-st-stop', done: 'bg-border', none: 'bg-border',
}
const GROUPS: { id: Exclude<Group, 'done'>; label: string }[] = [
  { id: 'act', label: '할 것' }, { id: 'run', label: '도는 것' }, { id: 'idle', label: '정리됨' },
]

// Below 1280px the rail folds to its icons and dots (`max-[1280px]:`).
const WIDE = 'max-[1280px]:hidden'
const NARROW = 'min-[1280px]:hidden'

/** The project, the window's settings and review loop, and the project's
 *  tasks in the order a person reads them: what they have to do, what runs,
 *  what waits to be started, what is finished. */
export function TaskRail(props: Props) {
  const { repo, options, view, tasks, selected, prs } = props
  const [fault, setFault] = useState('')
  const [looping, setLooping] = useState(false)
  const [picking, setPicking] = useState<number[] | null>(null)
  const pickable = prs.filter((p) => p.pickable)
  const done = tasks.filter((t) => t.group === 'done')

  const projects: Item[] = options?.projects.map((p) => ({ value: p.id, label: p.id,
    note: p.state === '미연결' ? undefined : p.state })) ?? []

  async function loop(numbers: number[]) {
    setFault('')
    setLooping(true)
    try {
      await props.onLoop(numbers)
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
    } finally {
      setLooping(false)
    }
  }
  // Always through the list, even for one: a loop spends review rounds.
  const openLoop = () => setPicking(pickable.map((p) => p.number))
  const loopLabel = looping ? '루프를 여는 중…' : `리뷰 루프 (${pickable.length})`

  return (
    <aside className="flex h-full min-h-0 flex-col border-r border-sidebar-border bg-sidebar">
      <div className="flex h-11 shrink-0 items-center justify-between gap-2 border-b border-sidebar-border px-3 max-[1280px]:justify-center max-[1280px]:px-0">
        <span className={cn('font-heading text-[15px] font-semibold', WIDE)}>wiki-agent</span>
        <button type="button" onClick={props.onSettings} aria-label="설정" title="설정"
          className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-sidebar-accent hover:text-foreground">
          <Gear className="size-4" />
        </button>
      </div>

      <div className={cn('shrink-0 space-y-2 border-b border-sidebar-border p-3', WIDE)}>
        <Picker label="프로젝트" hideLabel width="w-full" mono items={projects} value={repo}
          disabled={props.projectBusy || !options} onPick={(v) => v && v !== repo && props.onProject(v)} />
        <div className="grid grid-cols-2 gap-1.5">
          <button type="button" aria-pressed={view === 'projects'}
            onClick={() => props.onView(view === 'projects' ? 'chat' : 'projects')}
            className={cn('h-7 rounded-md border border-sidebar-border px-2 text-[12.5px] hover:bg-sidebar-accent',
              view === 'projects' && 'bg-sidebar-accent')}
            title="저장소마다 위키가 붙었는지, [연결]">
            모든 프로젝트
          </button>
          <button type="button" disabled={pickable.length === 0 || looping} onClick={openLoop}
            className="h-7 rounded-md border border-sidebar-border px-2 text-[12.5px] hover:bg-sidebar-accent disabled:opacity-40"
            title="명세 없는 PR 과 멈춘 루프를 리뷰 루프에 넣는다">
            {loopLabel}
          </button>
        </div>
        {fault && <p role="alert" className="text-[12.5px] text-destructive">{fault}</p>}
      </div>

      <div className={cn('flex shrink-0 flex-col items-center gap-1 border-b border-sidebar-border py-2', NARROW)}>
        <button type="button" aria-pressed={view === 'projects'} aria-label="모든 프로젝트" title={`모든 프로젝트 — 지금 ${repo}`}
          onClick={() => props.onView(view === 'projects' ? 'chat' : 'projects')}
          className={cn('grid size-8 place-items-center rounded-md hover:bg-sidebar-accent', view === 'projects' && 'bg-sidebar-accent')}>
          <FolderGit2 className="size-4" />
        </button>
        <button type="button" aria-label={loopLabel} title={loopLabel} disabled={pickable.length === 0 || looping} onClick={openLoop}
          className="relative grid size-8 place-items-center rounded-md hover:bg-sidebar-accent disabled:opacity-40">
          <Repeat className="size-4" />
          {pickable.length > 0 && (
            <span className="absolute -top-0.5 -right-0.5 rounded-full bg-primary px-1 font-mono text-[10.5px] text-primary-foreground">
              {pickable.length}
            </span>
          )}
        </button>
      </div>

      <nav aria-label="작업" className="min-h-0 flex-1 overflow-y-auto px-2 py-2 max-[1280px]:px-1">
        {tasks.length === 0 && (
          <p className={cn('px-2 py-1 text-[12.5px] text-faint', WIDE)}>
            아직 작업이 없다. 다음 작업 대화에서 명세를 정하고 [시작] 한다.
          </p>
        )}
        {GROUPS.map((g) => {
          const list = tasks.filter((t) => t.group === g.id)
          return list.length > 0 && (
            <div key={g.id} className="mb-2">
              <Label>{g.label}</Label>
              {list.map((t) => <Row key={t.key} task={t} selected={t.key === selected} onSelect={props.onSelect} />)}
            </div>
          )
        })}
        {done.length > 0 && (
          <details className="mb-2">
            <summary className="cursor-pointer list-none rounded-md px-2 py-1 font-heading text-[11px] font-semibold text-faint hover:bg-sidebar-accent max-[1280px]:text-center">
              <span className={WIDE}>끝난 것 {done.length}</span>
              <span className={NARROW}>{done.length}</span>
            </summary>
            {done.map((t) => <Row key={t.key} task={t} selected={t.key === selected} onSelect={props.onSelect} />)}
          </details>
        )}
        {props.others.length > 0 && (
          <div className="mb-2">
            <Label>다른 프로젝트</Label>
            {props.others.map((o) => (
              <Row key={o.path} selected={o.path === selected} onSelect={props.onSelect}
                title="프로젝트를 바꾸지 않고 그 작업트리를 연다"
                task={{ key: o.path, name: `${o.repo}/${o.name}`, pr: o.pr, round: o.round, phase: o.phase,
                  waiting: o.waiting, busy: false, line: o.line }} />
            ))}
          </div>
        )}
      </nav>

      {picking && (
        <Modal title="리뷰 루프에 넣을 PR" onClose={() => setPicking(null)} foot={(
          <>
            <Btn onClick={() => setPicking(null)}>닫기</Btn>
            <Btn tone="primary" disabled={picking.length === 0} onClick={() => {
              const chosen = picking
              setPicking(null)
              void loop(chosen)
            }}>
              시작 ({picking.length})
            </Btn>
          </>
        )}>
          <ul className="space-y-2">
            {prs.map((p) => (
              <li key={p.number}>
                <label className={cn('flex items-start gap-2', !p.pickable && 'opacity-50')}>
                  <input type="checkbox" className="mt-0.5 accent-primary" disabled={!p.pickable}
                    checked={picking.includes(p.number)}
                    onChange={(e) => setPicking((now) => (e.target.checked
                      ? [...(now ?? []), p.number] : (now ?? []).filter((n) => n !== p.number)))} />
                  <span className="min-w-0">
                    <span className="font-mono text-[10.5px] text-muted-foreground">#{p.number}</span> {p.title}
                    {p.why && <span className="block text-[12.5px] text-faint">{p.why}</span>}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </Modal>
      )}
    </aside>
  )
}

function Label({ children }: { children: string }) {
  return <div className={cn('px-2 pt-1 pb-1 font-heading text-[11px] font-semibold text-faint', WIDE)}>{children}</div>
}

type RowTask = Pick<Task, 'key' | 'name' | 'pr' | 'round' | 'phase' | 'waiting' | 'busy' | 'line'>

/** One task: its name, PR and round; then one word of state and what a
 *  person has to do. Folded, only the dot stays, and the words move to the
 *  tooltip. */
function Row({ task: t, selected, onSelect, title }:
  { task: RowTask; selected: boolean; onSelect: (key: string) => void; title?: string }) {
  const tip = `${t.name}${t.pr ? ` #${t.pr}` : ''}${t.round ? ` R${t.round}` : ''} — ${t.line}`
  return (
    <button type="button" onClick={() => onSelect(t.key)} aria-current={selected} title={title ?? tip}
      className={cn('mb-0.5 w-full rounded-md px-2.5 py-1.5 text-left hover:bg-sidebar-accent max-[1280px]:grid max-[1280px]:h-8 max-[1280px]:place-items-center max-[1280px]:px-0',
        selected && 'bg-sidebar-accent')}>
      <span className="flex items-center gap-2">
        <span aria-hidden className={cn('size-2 shrink-0 rounded-full', t.waiting ? 'bg-wait' : DOT[t.phase],
          t.busy && 'animate-pulse')} />
        <span className={cn('min-w-0 truncate font-mono text-[12px]', WIDE)}>{t.name}</span>
        {(t.pr || t.round > 0) && (
          <span className={cn('ml-auto shrink-0 font-mono text-[10.5px] text-muted-foreground', WIDE)}>
            {t.pr ? `#${t.pr}` : ''}{t.round > 0 ? ` R${t.round}` : ''}
          </span>
        )}
      </span>
      <span className={cn('mt-0.5 block truncate pl-4 font-mono text-[10.5px]', t.waiting ? 'text-wait' : 'text-muted-foreground', WIDE)}>
        {t.line}
      </span>
      <span className={cn('sr-only', NARROW)}>{tip}</span>
    </button>
  )
}
