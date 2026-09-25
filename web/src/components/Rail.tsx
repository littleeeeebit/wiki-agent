import { useState } from 'react'
import { Picker } from '@/components/Toolbar'
import type { Item } from '@/components/Toolbar'
import type { Options, Switch, Worktree } from '@/lib/api'
import { cn } from '@/lib/utils'

type Props = {
  repo: string
  options: Options | null
  projectBusy: boolean
  rows: Worktree[]
  /** Worktrees whose agent is waiting on an approval. */
  waiting: Set<string>
  /** The state of the spec that owns a worktree, by its path. */
  specs: Record<string, string>
  selected: string
  view: 'query' | 'map'
  sw: Switch | null
  theme: 'dark' | 'light'
  onProject: (repo: string) => void
  onSelect: (path: string) => void
  onMake: (task: string) => Promise<void>
  onRemove: (path: string) => Promise<void>
  onView: (view: 'query' | 'map') => void
  onSwitch: (on: boolean) => void
  onTheme: (theme: 'dark' | 'light') => void
}

// What `workspace` accepts as a task name: a branch and a folder at once.
const TASK = /^[a-z0-9][a-z0-9-]{0,63}$/

/** The project, its worktrees, and what the whole app shares. */
export function Rail(props: Props) {
  const { repo, options, projectBusy, rows, waiting, selected, view, sw, theme } = props
  const [task, setTask] = useState('')
  const [fault, setFault] = useState('')
  const [working, setWorking] = useState('')

  const projects: Item[] = options?.projects.map((p) => ({ value: p.id, label: p.id,
    note: p.wired ? '위키 붙음' : undefined })) ?? []

  async function act(key: string, fn: () => Promise<void>) {
    setFault('')
    setWorking(key)
    try {
      await fn()
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
    } finally {
      setWorking('')
    }
  }

  const usage = sw?.usage
  return (
    <aside className="flex h-full min-h-0 flex-col border-r border-sidebar-border bg-sidebar">
      <div className="px-4 pt-4 pb-3">
        <div className="font-heading text-[15px] font-semibold leading-tight">wiki-agent</div>
        <div className="mt-0.5 text-[12.5px] text-faint">위키에 묻고, 그 근거로 일을 시킨다</div>
      </div>

      <div className="px-3 pb-3">
        <Picker label="프로젝트" width="w-full" mono stacked items={projects} value={repo}
          disabled={projectBusy || !options} onPick={(v) => v && v !== repo && props.onProject(v)} />
      </div>

      <div className="border-t border-sidebar-border px-4 pt-3 pb-1 font-heading text-[11px] font-semibold text-faint">작업트리</div>
      <nav aria-label="작업트리" className="min-h-0 flex-1 overflow-y-auto px-2">
        {rows.length === 0 && <p className="px-2 py-1 text-[12.5px] text-faint">아직 없다. 아래에서 만든다.</p>}
        {rows.map((r) => (
          <div key={r.path} className={cn('group mb-0.5 rounded-md', r.path === selected && 'bg-sidebar-accent')}>
            <button
              type="button"
              onClick={() => props.onSelect(r.path)}
              aria-current={r.path === selected}
              className="w-full rounded-md px-2.5 py-1.5 text-left text-[12.5px] hover:bg-sidebar-accent"
            >
              <div className="flex items-center gap-1.5">
                <span
                  className={cn('size-1.5 shrink-0 rounded-full',
                    waiting.has(r.path) ? 'bg-wait' : r.busy ? 'animate-pulse bg-primary' : r.live ? 'bg-primary' : 'bg-border')}
                  title={waiting.has(r.path) ? '쓰기 허용을 기다린다' : r.busy ? '에이전트가 돌고 있다' : r.live ? '세션이 살아 있다' : '세션 없음'}
                />
                <span className="truncate font-mono text-[12px]">{r.name}</span>
                {props.specs[r.path] && (
                  <span className="ml-auto shrink-0 text-[10.5px] text-muted-foreground">{props.specs[r.path]}</span>
                )}
              </div>
              <div className="mt-0.5 flex gap-1.5 pl-3 font-mono text-[10.5px] text-muted-foreground">
                {r.branch !== r.name && <span className="truncate font-mono">{r.branch}</span>}
                {r.dirty && <span>변경</span>}
                {r.merged && !r.dirty && <span className="text-primary" title="브랜치의 변경이 모두 원본 HEAD 에 있다. 지워도 잃는 것이 없다">HEAD 에 다 있음</span>}
              </div>
            </button>
            {r.merged && !r.dirty && !r.busy && (
              <button
                type="button"
                disabled={!!working}
                onClick={() => act(r.path, () => props.onRemove(r.path))}
                className="mb-1.5 ml-5 rounded border border-sidebar-border px-1.5 py-0.5 text-[12.5px] text-muted-foreground hover:bg-sidebar-accent disabled:opacity-40"
                title="작업트리와 브랜치를 지운다. 브랜치의 변경은 원본 HEAD 에 다 있다"
              >
                {working === r.path ? '정리하는 중…' : '정리'}
              </button>
            )}
          </div>
        ))}
      </nav>

      <form
        className="border-t border-sidebar-border p-3"
        onSubmit={(e) => {
          e.preventDefault()
          const name = task.trim()
          if (!TASK.test(name)) return setFault('작업 이름은 소문자·숫자·- 만, 64자까지')
          void act('make', async () => {
            await props.onMake(name)
            setTask('')
          })
        }}
      >
        <label className="font-heading text-[11px] font-semibold text-faint" htmlFor="task">새 작업</label>
        <div className="mt-1 flex gap-1.5">
          <input
            id="task"
            value={task}
            onChange={(e) => setTask(e.target.value.toLowerCase())}
            placeholder="fix-login"
            spellCheck={false}
            className="min-w-0 flex-1 rounded-md border border-sidebar-border bg-background px-2 py-1 font-mono text-[12px]"
          />
          <button type="submit" disabled={!task || !!working}
            className="rounded-md border border-sidebar-border px-2 py-1 text-[12.5px] hover:bg-sidebar-accent disabled:opacity-40">
            {working === 'make' ? '…' : '만들기'}
          </button>
        </div>
        {fault && <p role="alert" className="mt-1.5 text-[12.5px] text-destructive">{fault}</p>}
      </form>

      <div className="space-y-2 border-t border-sidebar-border p-3">
        <button
          type="button"
          aria-pressed={view === 'map'}
          onClick={() => props.onView(view === 'map' ? 'query' : 'map')}
          className={cn('w-full rounded-md px-2.5 py-1.5 text-left text-[12.5px] hover:bg-sidebar-accent',
            view === 'map' && 'bg-sidebar-accent')}
        >
          {view === 'map' ? '← 위키 질의로' : '위키 지도'}
        </button>
        <label className="flex items-center justify-between gap-2 px-2.5 text-[12.5px]">
          <span>
            <span>한국어 번역</span>
            {usage && (
              <span className="ml-1.5 font-mono text-[10.5px] text-faint">
                {usage.usd == null ? '?' : `$${usage.usd.toFixed(2)}`} / ${usage.limit.toFixed(0)}
              </span>
            )}
          </span>
          <input
            type="checkbox"
            role="switch"
            checked={sw?.translate ?? false}
            disabled={!sw}
            onChange={(e) => props.onSwitch(e.target.checked)}
            className="size-4 accent-primary"
          />
        </label>
        <label className="flex items-center justify-between gap-2 px-2.5 text-[12.5px]">
          <span>어두운 화면</span>
          <input
            type="checkbox"
            role="switch"
            checked={theme === 'dark'}
            onChange={(e) => props.onTheme(e.target.checked ? 'dark' : 'light')}
            className="size-4 accent-primary"
          />
        </label>
      </div>
    </aside>
  )
}
