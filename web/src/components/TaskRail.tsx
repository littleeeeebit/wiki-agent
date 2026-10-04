import { useEffect, useState } from 'react'
import type { MouseEvent } from 'react'
import { FolderGit2, Plus, Repeat, Settings as Gear } from 'lucide-react'
import { Btn, Modal } from '@/components/Modal'
import { Picker } from '@/components/Toolbar'
import type { Item } from '@/components/Toolbar'
import type { Options, Pr } from '@/lib/api'
import type { Elsewhere, Group, Phase, Task } from '@/lib/tasks'
import { cn } from '@/lib/utils'
import { ProviderUsage } from '@/components/ProviderUsage'

/** What the middle pane shows. */
export type View = 'chat' | 'map' | 'architecture' | 'projects'

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
  onLoop: (prs: number[], environment: 'local' | 'claude-cloud' | 'external', repo: string) => Promise<void>
  onSettings: () => void
  onNew: () => void
  /** Delete a worktree even while it runs: what runs is stopped first. */
  onRemove: (path: string) => void
}

type Target = { path: string; name: string; shared: boolean }

const DOT: Record<Phase, string> = {
  draft: 'bg-st-draft', work: 'bg-st-work', review: 'bg-st-review', ready: 'bg-st-ready',
  queued: 'bg-st-queued', stop: 'bg-st-stop', done: 'bg-border', none: 'bg-border',
}
const GROUPS: { id: Exclude<Group, 'done'>; label: string }[] = [
  { id: 'act', label: '할 것' }, { id: 'run', label: '도는 것' }, { id: 'idle', label: '정리됨' },
]

// Below 1280px the rail folds to its icons and dots (`max-[1280px]:`).
const WIDE = 'rail-expanded max-[1280px]:hidden'
const NARROW = 'rail-folded min-[1280px]:hidden'

/** The project, the window's settings and review loop, and the project's
 *  tasks in the order a person reads them: what they have to do, what runs,
 *  what waits to be started, what is finished. */
export function TaskRail(props: Props) {
  const { repo, options, view, tasks, selected, prs } = props
  const [fault, setFault] = useState('')
  const [looping, setLooping] = useState(false)
  const [toolsOpen, setToolsOpen] = useState(false)
  const [environment, setEnvironment] = useState<'local' | 'claude-cloud' | 'external'>('local')
  // A choice belongs to the project it was made in. When the window follows
  // another project the modal is gone with it: the same numbers there are
  // other pull requests, and [시작] would loop them.
  const [choice, setChoice] = useState<{ repo: string; numbers: number[] } | null>(null)
  const picking = choice?.repo === repo ? choice.numbers : null
  const setPicking = (next: number[] | null | ((now: number[] | null) => number[])) =>
    setChoice((was) => {
      const now = was?.repo === repo ? was.numbers : null
      const numbers = typeof next === 'function' ? next(now) : next
      return numbers ? { repo, numbers } : null
    })
  const pickable = prs.filter((p) => p.pickable)
  const done = tasks.filter((t) => t.group === 'done')
  // Right-click on a task with a worktree: a menu at the pointer, then a confirm.
  const [menu, setMenu] = useState<(Target & { x: number; y: number }) | null>(null)
  const [doomed, setDoomed] = useState<Target | null>(null)
  useEffect(() => {
    if (!menu) return
    const close = () => setMenu(null)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && close()
    window.addEventListener('click', close)
    window.addEventListener('blur', close)
    window.addEventListener('keydown', esc)
    return () => {
      window.removeEventListener('click', close)
      window.removeEventListener('blur', close)
      window.removeEventListener('keydown', esc)
    }
  }, [menu])
  const onMenu = (t: Task) => (t.spec || (t.path && !t.row?.primary) ? (e: MouseEvent) => {
    e.preventDefault()
    setMenu({ path: t.spec ? `spec:${t.spec.id}` : t.path!, name: t.name,
      shared: !t.path || t.spec?.workspace_mode === 'branch' || !!t.row?.primary, x: e.clientX, y: e.clientY })
  } : undefined)

  const projects: Item[] = options?.projects.map((p) => ({ value: p.id, label: p.id,
    note: p.state === '미연결' ? undefined : p.state })) ?? []
  // The current project's state when the wiki is not wholly attached: the
  // rail says so and offers [연결] right there.
  const state = options?.projects.find((p) => p.id === repo)?.state
  const unwired = state && state !== '연결 완료' ? state : ''

  async function loop(numbers: number[]) {
    setFault('')
    setLooping(true)
    try {
      await props.onLoop(numbers, environment, repo)
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
        <div className="rail-primary space-y-2">
        <Picker label="프로젝트" hideLabel width="w-full" mono items={projects} value={repo}
          disabled={props.projectBusy || !options} onPick={(v) => v && v !== repo && props.onProject(v)} />
        <button type="button" onClick={props.onNew}
          aria-label="새 작업" className="rail-new-task flex h-7 w-full items-center justify-center gap-1.5 rounded-md border border-sidebar-border text-[12.5px] hover:bg-sidebar-accent"
          title="다음 작업 대화에 한 줄 명세 틀을 넣는다. 명세의 [시작] 이 작업트리를 만든다">
          <Plus className="size-3.5" /> <span className="rail-new-task-label">새 작업</span>
        </button>
        </div>
        {unwired && (
          <div className="flex items-center gap-2 text-[12.5px]">
            <span aria-hidden className={cn('size-2 shrink-0 rounded-full', unwired === '일부' ? 'bg-wait' : 'bg-border')} />
            <span className="min-w-0 flex-1 truncate text-muted-foreground">위키 {unwired}</span>
            <Btn tone="primary" onClick={() => props.onView('projects')}>연결</Btn>
          </div>
        )}
        <button type="button" className="mobile-only hidden w-full items-center justify-between rounded-md px-2 text-[14px] text-muted-foreground"
          aria-expanded={toolsOpen} aria-controls="rail-tools" onClick={() => setToolsOpen((open) => !open)}>
          프로젝트 · 리뷰 <span aria-hidden>{toolsOpen ? '−' : '+'}</span>
        </button>
        <div id="rail-tools" data-open={toolsOpen} className="grid grid-cols-2 gap-1.5">
          <button type="button" aria-pressed={view === 'projects'}
            onClick={() => props.onView(view === 'projects' ? 'chat' : 'projects')}
            className={cn('h-7 rounded-md border border-sidebar-border px-2 text-[12.5px] hover:bg-sidebar-accent',
              view === 'projects' && 'bg-sidebar-accent')}
            title="저장소마다 위키가 붙었는지, [연결]">
            프로젝트 · 연결
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
        <button type="button" aria-label="새 작업" title="새 작업" onClick={props.onNew}
          className="grid size-8 place-items-center rounded-md hover:bg-sidebar-accent">
          <Plus className="size-4" />
        </button>
        <button type="button" aria-pressed={view === 'projects'} aria-label="프로젝트 · 연결"
          title={`프로젝트 · 연결 — 지금 ${repo}${unwired ? ` (위키 ${unwired})` : ''}`}
          onClick={() => props.onView(view === 'projects' ? 'chat' : 'projects')}
          className={cn('relative grid size-8 place-items-center rounded-md hover:bg-sidebar-accent', view === 'projects' && 'bg-sidebar-accent')}>
          <FolderGit2 className="size-4" />
          {unwired && <span aria-hidden className="absolute top-1 right-1 size-1.5 rounded-full bg-wait" />}
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
            아직 작업이 없다. [새 작업] 으로 명세를 정하고 [시작] 한다.
          </p>
        )}
        {GROUPS.map((g) => {
          const list = tasks.filter((t) => t.group === g.id)
          return list.length > 0 && (
            <div key={g.id} className="mb-2">
              <Label>{g.label}</Label>
              {list.map((t) => <Row key={t.key} task={t} selected={t.key === selected} onSelect={props.onSelect}
                onMenu={onMenu(t)} />)}
            </div>
          )
        })}
        {done.length > 0 && (
          <details className="mb-2">
            <summary className="cursor-pointer list-none rounded-md px-2 py-1 font-heading text-[11px] font-semibold text-faint hover:bg-sidebar-accent max-[1280px]:text-center">
              <span className={WIDE}>끝난 것 {done.length}</span>
              <span className={NARROW}>{done.length}</span>
            </summary>
            {done.map((t) => <Row key={t.key} task={t} selected={t.key === selected} onSelect={props.onSelect}
              onMenu={onMenu(t)} />)}
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

      <ProviderUsage />

      {menu && (
        <div role="menu" style={{ left: menu.x, top: menu.y }}
          className="fixed z-50 min-w-28 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md">
          <button type="button" role="menuitem" autoFocus onClick={() => setDoomed(menu)}
            className="w-full rounded px-2 py-1 text-left text-[12.5px] text-destructive hover:bg-secondary">
            삭제
          </button>
        </div>
      )}

      {doomed && (
        <Modal title="작업 삭제" onClose={() => setDoomed(null)} foot={(
          <>
            <Btn onClick={() => setDoomed(null)}>닫기</Btn>
            <Btn tone="danger" onClick={() => {
              const path = doomed.path
              setDoomed(null)
              props.onRemove(path)
            }}>
              삭제
            </Btn>
          </>
        )}>
          {doomed.shared ? <p><span className="font-mono text-[12.5px]">{doomed.name}</span> 작업을 목록에서 지우고 실행을 멈춘다.
            명세는 보관하며 저장소, 브랜치, 코드 변경과 PR은 남긴다.</p> : <p>
            <span className="font-mono text-[12.5px]">{doomed.name}</span> 작업트리를 지운다. 도는 에이전트와 리뷰 루프는
            멈추고, 커밋하지 않은 변경은 사라진다. 머지 전 작업이면 목록에서도 빠진다. 브랜치와 올린 PR 은 남긴다.
          </p>}
        </Modal>
      )}

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
          <label className="mb-3 flex flex-wrap items-center gap-2 text-[12.5px]">
            구현 환경
            <select aria-label="구현 환경" value={environment}
              onChange={(e) => setEnvironment(e.target.value as 'local' | 'claude-cloud' | 'external')}
              className="h-7 min-w-0 rounded-md border border-border bg-background px-2">
              <option value="local">로컬 구현 · 기존 리뷰</option>
              <option value="claude-cloud">Claude Code Cloud · 로컬 검증 후 리뷰</option>
              <option value="external">다른 환경 · 리뷰만, 수정은 외부에서</option>
            </select>
          </label>
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
function Row({ task: t, selected, onSelect, onMenu, title }: {
  task: RowTask; selected: boolean; onSelect: (key: string) => void; onMenu?: (e: MouseEvent) => void; title?: string
}) {
  const tip = `${t.name}${t.pr ? ` #${t.pr}` : ''}${t.round ? ` R${t.round}` : ''} — ${t.line}`
  return (
    <button type="button" onClick={() => onSelect(t.key)} onContextMenu={onMenu} aria-current={selected}
      title={title ?? tip}
      className={cn('mb-0.5 w-full rounded-md px-2.5 py-1.5 text-left hover:bg-sidebar-accent max-[1280px]:grid max-[1280px]:h-8 max-[1280px]:place-items-center max-[1280px]:px-0',
        selected && 'bg-sidebar-accent')}>
      <span className="flex items-center gap-2">
        <span aria-hidden className={cn('size-2 shrink-0 rounded-full', t.waiting ? 'bg-wait' : DOT[t.phase],
          t.busy && 'animate-pulse')} />
        <span className={cn('task-name min-w-0 truncate font-mono text-[12px]', WIDE)}>{t.name}</span>
        {(t.pr || t.round > 0) && (
          <span className={cn('ml-auto shrink-0 font-mono text-[10.5px] text-muted-foreground', WIDE)}>
            {t.pr ? `#${t.pr}` : ''}{t.round > 0 ? ` R${t.round}` : ''}
          </span>
        )}
      </span>
      <span className={cn('task-state mt-0.5 block truncate pl-4 font-mono text-[10.5px]', t.waiting ? 'text-wait' : 'text-muted-foreground', WIDE)}>
        {t.line}
      </span>
      <span className={cn('sr-only', NARROW)}>{tip}</span>
    </button>
  )
}
