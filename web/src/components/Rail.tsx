import { useState } from 'react'
import { Picker } from '@/components/Toolbar'
import type { Item } from '@/components/Toolbar'
import type { LoopSettings, Options, Pr, Switch, Worktree } from '@/lib/api'
import { cn } from '@/lib/utils'

/** What the rail shows of the spec that owns a worktree. */
export type Badge = { state: string; pr: number | null; round: number }

/** A running turn or loop of a project other than the selected one. */
export type Other = { path: string; repo: string; label: string }

/** What the middle pane shows. */
export type View = 'query' | 'map' | 'projects'

type Props = {
  repo: string
  options: Options | null
  projectBusy: boolean
  rows: Worktree[]
  /** Worktrees whose agent is waiting on an approval. */
  waiting: Set<string>
  /** The spec that owns a worktree, by its path. */
  specs: Record<string, Badge>
  prs: Pr[]
  others: Other[]
  loopSettings: LoopSettings | null
  selected: string
  view: View
  sw: Switch | null
  theme: 'dark' | 'light'
  onProject: (repo: string) => void
  onSelect: (path: string) => void
  onMake: (task: string) => Promise<void>
  onRemove: (path: string) => Promise<void>
  onLoop: (prs: number[]) => Promise<void>
  onLoopSettings: (s: LoopSettings) => Promise<void>
  onView: (view: View) => void
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
  const [picking, setPicking] = useState<number[] | null>(null)
  const pickable = props.prs.filter((p) => p.pickable)

  const projects: Item[] = options?.projects.map((p) => ({ value: p.id, label: p.id,
    note: p.state === '미연결' ? undefined : p.state })) ?? []

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
        <div className="flex items-end gap-1.5">
          <div className="min-w-0 flex-1">
            <Picker label="프로젝트" width="w-full" mono stacked items={projects} value={repo}
              disabled={projectBusy || !options} onPick={(v) => v && v !== repo && props.onProject(v)} />
          </div>
          <button type="button" aria-pressed={view === 'projects'}
            onClick={() => props.onView(view === 'projects' ? 'query' : 'projects')}
            className={cn('shrink-0 rounded-md border border-sidebar-border px-2 py-1.5 text-[12.5px] hover:bg-sidebar-accent',
              view === 'projects' && 'bg-sidebar-accent')}
            title="저장소마다 위키가 붙었는지, [연결]">
            목록
          </button>
        </div>
        <button
          type="button"
          disabled={pickable.length === 0 || !!working}
          onClick={() => (pickable.length === 1
            ? act('loop', () => props.onLoop([pickable[0].number]))
            : setPicking(pickable.map((p) => p.number)))}
          className="mt-2 w-full rounded-md border border-sidebar-border px-2.5 py-1.5 text-left text-[12.5px] hover:bg-sidebar-accent disabled:opacity-40"
          title="명세 없는 PR 과 멈춘 루프를 리뷰 루프에 넣는다"
        >
          {working === 'loop' ? '루프를 여는 중…' : `리뷰 루프 (${pickable.length})`}
        </button>
      </div>

      {picking && (
        <div role="dialog" aria-modal="true" aria-label="리뷰 루프에 넣을 PR"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setPicking(null)}>
          <div className="w-[28rem] max-w-[calc(100vw-2rem)] rounded-lg border border-border bg-card p-4"
            onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 font-heading text-[13px] font-semibold">리뷰 루프에 넣을 PR</div>
            <div className="max-h-80 space-y-1 overflow-y-auto">
              {props.prs.map((p) => (
                <label key={p.number} className={cn('flex items-start gap-2 text-[12.5px]', !p.pickable && 'opacity-50')}>
                  <input type="checkbox" className="mt-0.5 accent-primary" disabled={!p.pickable}
                    checked={picking.includes(p.number)}
                    onChange={(e) => setPicking((now) => (e.target.checked
                      ? [...(now ?? []), p.number] : (now ?? []).filter((n) => n !== p.number)))} />
                  <span className="min-w-0">
                    <span className="font-mono">#{p.number}</span> {p.title}
                    {p.why && <span className="block text-[11.5px] text-faint">{p.why}</span>}
                  </span>
                </label>
              ))}
            </div>
            <div className="mt-3 flex justify-end gap-1.5">
              <button type="button" className="rounded-md border border-border px-2 py-1 text-[12.5px]"
                onClick={() => setPicking(null)}>닫기</button>
              <button type="button" disabled={picking.length === 0}
                className="rounded-md border border-primary px-2 py-1 text-[12.5px] text-primary disabled:opacity-40"
                onClick={() => {
                  const chosen = picking
                  setPicking(null)
                  void act('loop', () => props.onLoop(chosen))
                }}>
                시작 ({picking.length})
              </button>
            </div>
          </div>
        </div>
      )}

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
                  <span className="ml-auto flex shrink-0 gap-1 font-mono text-[10.5px] text-muted-foreground">
                    {props.specs[r.path].pr && <span>#{props.specs[r.path].pr}</span>}
                    {props.specs[r.path].round > 0 && <span>R{props.specs[r.path].round}</span>}
                    <span className="font-sans">{props.specs[r.path].state}</span>
                  </span>
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
        {props.others.length > 0 && (
          <>
            <div className="px-2 pt-3 pb-1 font-heading text-[11px] font-semibold text-faint">다른 프로젝트</div>
            {props.others.map((o) => (
              <button
                key={o.path}
                type="button"
                onClick={() => props.onSelect(o.path)}
                aria-current={o.path === selected}
                title="프로젝트를 바꾸지 않고 그 작업트리를 연다"
                className={cn('mb-0.5 flex w-full items-center gap-1.5 rounded-md px-2.5 py-1.5 text-left text-[12.5px] hover:bg-sidebar-accent',
                  o.path === selected && 'bg-sidebar-accent')}
              >
                <span className={cn('size-1.5 shrink-0 rounded-full', waiting.has(o.path) ? 'bg-wait' : 'bg-primary')} />
                <span className="truncate font-mono text-[12px]">{o.repo}/{o.path.split(/[\\/]/).pop()}</span>
                <span className="ml-auto shrink-0 text-[10.5px] text-muted-foreground">{o.label}</span>
              </button>
            ))}
          </>
        )}
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
        {props.loopSettings && (
          <LoopFields key={JSON.stringify(props.loopSettings)} value={props.loopSettings} options={options}
            onSave={(s) => act('settings', () => props.onLoopSettings(s))} />
        )}
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

/** The loop's three settings. Temporary: stage 6's settings modal takes them. */
function LoopFields({ value, options, onSave }:
  { value: LoopSettings; options: Options | null; onSave: (s: LoopSettings) => void }) {
  const [rounds, setRounds] = useState(String(value.rounds))
  const [concurrent, setConcurrent] = useState(String(value.concurrent))
  const [model, setModel] = useState(value.review_model)
  const edited = rounds !== String(value.rounds) || concurrent !== String(value.concurrent) || model !== value.review_model
  const field = 'w-12 rounded border border-sidebar-border bg-background px-1 py-0.5 font-mono text-[12px]'
  return (
    <div className="space-y-1 px-2.5 text-[12.5px]">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor="loop-rounds">라운드 상한</label>
        <input id="loop-rounds" inputMode="numeric" value={rounds} onChange={(e) => setRounds(e.target.value)} className={field} />
      </div>
      <div className="flex items-center justify-between gap-2">
        <label htmlFor="loop-seats">동시 실행</label>
        <input id="loop-seats" inputMode="numeric" value={concurrent} onChange={(e) => setConcurrent(e.target.value)} className={field} />
      </div>
      <div className="flex items-center justify-between gap-2">
        <label htmlFor="loop-model">리뷰 모델</label>
        <select id="loop-model" value={model} onChange={(e) => setModel(e.target.value)}
          className="min-w-0 max-w-32 rounded border border-sidebar-border bg-background px-1 py-0.5 text-[12px]">
          <option value="">Codex 기본</option>
          {options?.models.filter((m) => m.id).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
        </select>
      </div>
      {edited && (
        <button type="button" className="w-full rounded border border-sidebar-border py-0.5 hover:bg-sidebar-accent"
          onClick={() => onSave({ rounds: Number(rounds), concurrent: Number(concurrent), review_model: model })}>
          루프 설정 저장
        </button>
      )}
    </div>
  )
}
