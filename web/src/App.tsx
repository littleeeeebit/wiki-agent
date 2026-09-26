import { useCallback, useEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { Agent } from '@/components/Agent'
import type { Choice } from '@/components/Toolbar'
import { Btn } from '@/components/Modal'
import { Peek } from '@/components/Peek'
import { Query } from '@/components/Query'
import type { Seed } from '@/components/Query'
import { Projects } from '@/components/Projects'
import { RepoMap } from '@/components/RepoMap'
import { Review } from '@/components/Review'
import { Settings } from '@/components/Settings'
import { SpecSummary } from '@/components/SpecSummary'
import { TaskRail } from '@/components/TaskRail'
import type { View } from '@/components/TaskRail'
import { Terminal, closed } from '@/components/Terminal'
import * as api from '@/lib/api'
import type { Channel, LoopRow, LoopSettings, Options, Peek as PeekData, Pr, Spec, Switch, Worktree } from '@/lib/api'
import { LOOPING, elsewhere, phase, tasks as taskList } from '@/lib/tasks'
import { cn } from '@/lib/utils'
import { useWork } from '@/lib/work'

type Theme = 'dark' | 'light'
type Tab = 'agent' | 'review' | 'terminal'
// What `[+ 새 작업]` puts in the next-task box: a spec in one go, the worktree
// made by its [시작]. The person writes the task after it.
const NEW_TASK = '이것 하나를 바로 명세로 만들어라. 되묻지 말고 빼는 것과 완료 조건은 네가 정해라.\n할 일: '

const TABS: { id: Tab; label: string }[] = [{ id: 'agent', label: '에이전트' }, { id: 'review', label: '리뷰' }, { id: 'terminal', label: '터미널' }]

// The state word's colour on the right pane's header: the rail's dot, in text.
const TONE: Record<string, string> = {
  draft: 'text-st-draft', work: 'text-st-work', review: 'text-st-review', ready: 'text-st-ready',
  queued: 'text-st-queued', stop: 'text-st-stop', done: 'text-faint', none: 'text-faint',
}

/** An OS notification, only while the window is not in front. Without the
 *  person's leave the rail's mark is all there is. */
function notify(title: string, body: string) {
  if (document.hasFocus() || !('Notification' in window) || Notification.permission !== 'granted') return
  try {
    new Notification(title, { body })
  } catch {
    // A webview without notifications: the rail still shows it.
  }
}

function stored<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T | null) ?? fallback
  } catch {
    return fallback
  }
}

/** One window in three columns: the project's tasks on the left, the wiki —
 *  its conversation, its map, or every project — in the middle, and the
 *  selected task's agent, review and shell on the right. */
export default function App() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [options, setOptions] = useState<Options | null>(null)
  const [fault, setFault] = useState('')
  const [sw, setSw] = useState<Switch | null>(null)
  const [rows, setRows] = useState<Worktree[]>([])
  const [specs, setSpecs] = useState<Spec[]>([])
  // A task's key: its worktree's path, or `spec:<id>` before it has one.
  const [selected, setSelected] = useState('')
  const [view, setView] = useState<View>('chat')
  // The map is drawn once it is first opened, and kept after.
  const [mapped, setMapped] = useState(false)
  const [seed, setSeed] = useState<Seed | null>(null)
  const [setting, setSetting] = useState(false)
  const [queryBusy, setQueryBusy] = useState(false)
  const [choice, setChoice] = useState<Choice>({ model: '', effort: '' })
  const [peek, setPeek] = useState<{ data: PeekData | null; error?: string } | null>(null)
  // Dark unless the person chose light. Remembered per machine, not per server.
  const [theme, setTheme] = useState<Theme>(() => stored('theme', 'dark'))
  // The list carries the project it was read for, and is shown only under
  // that project: right after a switch the old one still stands here.
  const [prs, setPrs] = useState<{ project: string; rows: Pr[] }>({ project: '', rows: [] })
  const [loopRows, setLoopRows] = useState<LoopRow[]>([])
  const [turnsElsewhere, setTurnsElsewhere] = useState<{ path: string; repo: string }[]>([])
  const [loopSettings, setLoopSettings] = useState<LoopSettings | null>(null)
  const [tab, setTab] = useState<Tab>('agent')
  const work = useWork()
  const { attach } = work
  const repo = channels[0]?.repo ?? ''

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    document.documentElement.classList.toggle('light', theme === 'light')
    try {
      localStorage.setItem('theme', theme)
    } catch {
      // A blocked store only means the choice is made again next time.
    }
  }, [theme])

  // Only the newest listing lands. One for the previous project could come
  // back after the new project's and fill its rail with the old worktrees.
  const listing = useRef(0)
  // The project the screen shows, set only by `follow` (and the first load).
  // Every list from the server is judged against it by what it says, not by
  // when it was asked: a channel list for another project is dropped, and so
  // is a worktree list, which instead makes the screen follow. Late answers
  // come from several places — the first load, a switch, the query pane after
  // each answer — and one of them once turned the screen back a project.
  const expected = useRef('')

  const accept = useCallback((list: Channel[]) => {
    const of = list[0]?.repo ?? ''
    if (expected.current && of !== expected.current) return
    expected.current = of
    api.claim(of)
    setChannels(list)
  }, [])

  /** The screen moves to the server's project `now`, however it learned of it
   *  — its own switch, or a list showing that another window switched. The
   *  old project's worktrees and selection go at that moment, not when the
   *  new list arrives: until then, or if it never does, they stood under the
   *  new name and could be picked. The channels are read again after. */
  const follow = useCallback((now: string) => {
    expected.current = now
    api.claim(now)
    listing.current++
    setRows([])
    setSpecs([])
    setSelected('')
    setChannels((list) => list.map((c) => ({ ...c, repo: now })))
    return api.getChannels().then(accept)
  }, [accept])

  // The server refused a request because this screen shows another project
  // than it is on. Whatever asked, the screen follows here.
  useEffect(() => {
    const onMoved = (e: Event) => {
      const to = (e as CustomEvent<string>).detail
      if (to && to !== expected.current) follow(to).catch(() => {})
    }
    window.addEventListener('project-moved', onMoved)
    return () => window.removeEventListener('project-moved', onMoved)
  }, [follow])

  // Worktrees of other projects the rail lists — a loop, a running turn. One
  // of them stays selected when this project's list does not have it.
  const away = useRef(new Set<string>())

  const refresh = useCallback(() => {
    const mine = ++listing.current
    api.getWorktrees()
      .then(({ project, rows }) => {
        if (mine !== listing.current) return
        if (project !== expected.current) {
          follow(project).catch(() => {})
          return
        }
        setRows(rows)
        setSelected((key) => (key.startsWith('spec:') || rows.some((r) => r.path === key) || away.current.has(key)
          ? key : ''))
      })
      .catch((err) => mine === listing.current && setFault(String(err)))
  }, [follow])

  // Read when the worktrees are, and after a turn ends: a turn is where a
  // spec's pull request goes up. Merged on GitHub is noticed by this read.
  const readSpecs = useCallback(() => {
    api.getSpecs()
      .then(({ project, specs }) => project === expected.current && setSpecs(specs))
      .catch(() => {})
  }, [])

  // The pull requests the loop button counts, and every project's loops for
  // the rail's other-projects group.
  const readPrs = useCallback(() => {
    api.getPrs()
      .then(({ project, rows }) => project === expected.current && setPrs({ project, rows }))
      .catch(() => {})
  }, [])
  const readLoops = useCallback(() => {
    api.getLoops()
      .then(({ loops, turns }) => {
        setLoopRows(loops)
        setTurnsElsewhere(turns)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    // The list first: it names the project, and every other request waits
    // for that (`api.claim`).
    api.getChannels()
      .then((list) => {
        accept(list)
        return api.getSwitch()
      })
      .then(setSw)
      .catch(() => setFault('서버가 안 뜬 것 같다 — tool\\app.cmd, 또는 python tool/main'))
    // Apart, because listing Codex's models starts Codex. The screen does not
    // wait on that; only the pickers do.
    api.getOptions().then(setOptions).catch((err) => setFault(String(err)))
    // The projects' connection states ride on the options; the rail shows
    // the current one's, so a connection read again reads them again.
    const reread = () => api.getOptions().then(setOptions).catch(() => {})
    window.addEventListener('connect-changed', reread)
    return () => window.removeEventListener('connect-changed', reread)
  }, [])

  // The project decides which worktrees exist. Whatever else changes them —
  // an agent writing, a merge elsewhere — is picked up when the window comes
  // back into focus.
  useEffect(() => {
    if (!repo) return
    const both = () => {
      refresh()
      readSpecs()
      readPrs()
      readLoops()
    }
    both()
    api.getLoopSettings().then(setLoopSettings).catch(() => {})
    window.addEventListener('focus', both)
    return () => window.removeEventListener('focus', both)
  }, [repo, refresh, readSpecs, readPrs, readLoops])

  // What the server changes by itself — a loop moving a spec, a turn it
  // started — arrives on one stream. The lists are read again shortly after
  // (a burst of changes is one read), a window showing the worktree of a
  // server-started turn attaches to it, and a loop that comes to wait on an
  // approval, or a merge into an unreviewed base, notifies once.
  useEffect(() => {
    if (!repo) return
    const stop = new AbortController()
    const seen = new Map<string, LoopRow>()
    let timer: number | undefined
    const soon = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        refresh()
        readSpecs()
        readLoops()
        readPrs()
      }, 800)
    }
    const on = (ev: api.FeedEv) => {
      if (ev.kind === 'turn') {
        attach(ev.path)
        return
      }
      if (ev.kind === 'connect') {
        window.dispatchEvent(new Event('connect-changed'))
        return
      }
      const key = `${ev.repo}/${ev.id}`
      const before = seen.get(key)
      seen.set(key, ev)
      const name = `${ev.repo} · ${ev.id}${ev.pr ? ` #${ev.pr}` : ''}`
      if (ev.waiting && !before?.waiting && LOOPING.test(ev.state)) notify('리뷰 루프가 승인을 기다린다', name)
      if (ev.stopped?.reason === '검토하지 않은 base 에 머지됨' && before?.state !== '멈춤') {
        notify('검토하지 않은 base 에 머지됐다', name)
      }
      soon()
    }
    void (async () => {
      while (!stop.signal.aborted) {
        try {
          await api.loopEvents(on, stop.signal)
        } catch {
          // Dropped or aborted; tried again below unless aborted.
        }
        if (!stop.signal.aborted) await new Promise((r) => window.setTimeout(r, 2000))
      }
    })()
    return () => {
      stop.abort()
      window.clearTimeout(timer)
    }
  }, [repo, attach, refresh, readSpecs, readLoops, readPrs])

  const path = selected.startsWith('spec:') ? '' : selected
  useEffect(() => {
    if (path) work.load(path)
  }, [path, work])

  // Closing the app window takes the server down, and every running turn with
  // it. A browser tab is not asked: closing it leaves the server and the turns.
  const running = Object.values(work.turns).filter((turns) => turns.at(-1)?.pending).length
  const runningNow = useRef(running)
  useEffect(() => {
    if (running < runningNow.current) readSpecs()
    runningNow.current = running
  }, [running, readSpecs])
  // A loop dies with the server too; it is counted with the turns.
  const loopsNow = useRef(0)
  useEffect(() => {
    loopsNow.current = loopRows.filter((l) => LOOPING.test(l.state)).length
  }, [loopRows])
  useEffect(() => {
    if (!('__TAURI_INTERNALS__' in window)) return
    let off: (() => void) | undefined
    let gone = false
    import('@tauri-apps/api/window').then(({ getCurrentWindow }) =>
      getCurrentWindow().onCloseRequested((event) => {
        const n = runningNow.current
        const loops = loopsNow.current
        const what = [n && `도는 작업 ${n}개`, loops && `리뷰 루프 ${loops}개`].filter(Boolean).join('와 ')
        if (what && !window.confirm(`${what}가 멈춘다. 다시 띄우면 루프는 [계속] 으로 잇는다. 닫을까?`)) {
          event.preventDefault()
        }
      }),
    ).then((unlisten) => {
      if (gone) unlisten()
      else off = unlisten
    })
    return () => {
      gone = true
      off?.()
    }
  }, [])

  const project = useCallback(async (next: string) => {
    const wiki = channels.find((c) => c.id === 'wiki') ?? channels[0]
    if (!wiki) return
    setFault('')
    try {
      const { repo: now } = await api.setConfig(wiki.id, { repo: next, model: wiki.model, effort: wiki.effort })
      // The server has switched; the screen follows from this answer, not
      // from the next request — if that one failed, the picker stayed on the
      // old project while every API answered for the new one.
      await follow(now)
    } catch (err) {
      setFault(String(err))
    }
  }, [channels, follow])

  const flip = useCallback(async (on: boolean) => {
    try {
      setSw(await api.setSwitch(on))
    } catch (err) {
      setFault(String(err))
    }
  }, [])

  const order = useCallback(async (text: string) => {
    await work.send(path, text, choice)
    refresh()
    api.getSwitch().then(setSw).catch(() => {})
  }, [path, choice, work, refresh])

  // `[시작]`: the server makes the worktree and starts its first turn; the
  // screen selects it, and loading it attaches to that turn.
  const start = useCallback(async (id: string) => {
    const { path } = await api.startSpec(id, choice)
    readSpecs()
    refresh()
    setSelected(path)
    setTab('agent')
  }, [choice, readSpecs, refresh])

  const showPeek = useCallback(async (file: string, line: number) => {
    if (!path) return
    setPeek({ data: null })
    try {
      setPeek({ data: await api.peek(path, file, line) })
    } catch (err) {
      setPeek({ data: null, error: String(err) })
    }
  }, [path])

  const remove = useCallback(async (target: string) => {
    setFault('')
    try {
      // A terminal's shell may stand in that folder, and Windows will not
      // delete a directory a process stands in. Rendered now so a selected
      // one starts closing; then every close there is waited on — one
      // started a moment ago by selecting elsewhere counts too.
      if (target === selected) flushSync(() => setSelected(''))
      await closed(target)
      await api.removeWorktree(target)
      // The same task name makes the same path again; its turns must not
      // come back with it.
      work.forget(target)
      refresh()
      readSpecs()
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
    }
  }, [selected, work, refresh, readSpecs])

  // The rail leaves a terminal's changes to the next read: when the person
  // leaves the terminal tab, the worktree list is read again.
  const pick = useCallback((next: Tab) => {
    if (tab === 'terminal' && next !== 'terminal') refresh()
    setTab(next)
  }, [tab, refresh])

  const others = elsewhere(loopRows, turnsElsewhere, repo)
  const otherPaths = new Set(others.map((o) => o.path))
  useEffect(() => {
    away.current = new Set(otherPaths)
  })
  // Open approvals by worktree, as this window's streams know them.
  const asks = (p: string) => (work.turns[p] ?? []).filter((t) => t.pending)
    .flatMap((t) => t.steps).filter((s) => s.kind === 'approval' && s.answer === undefined).length
  // The server's `busy` is as old as the last listing; a turn this window is
  // streaming is known here first.
  const busy = (p: string) => Boolean(rows.find((r) => r.path === p)?.busy || work.turns[p]?.at(-1)?.pending)
  const list = taskList(specs, rows, asks, busy)
  const task = list.find((t) => t.key === selected)
  const other = others.find((o) => o.path === selected)
  // A worktree of another project has no row in this project's list; the
  // pane still shows it, and a new instruction there is refused by the server.
  const row = task?.row ?? (other
    ? { path: selected, name: other.name, branch: '', dirty: false, merged: false, live: true, busy: false }
    : undefined)
  const spec = task?.spec ?? null
  const waiting = (task?.waiting || other?.waiting) ?? false
  const on = sw?.translate ?? false
  const tidy = row && task?.row && row.merged && !row.dirty && !task.busy

  const middle = view === 'projects' ? '프로젝트 · 연결' : repo
  return (
    <div className="grid h-screen grid-cols-[15rem_minmax(0,1.1fr)_minmax(0,1fr)] overflow-hidden max-[1280px]:grid-cols-[3.25rem_minmax(0,1.1fr)_minmax(0,1fr)]">
      <TaskRail
        repo={repo}
        options={options}
        // The server refuses the switch too; this keeps the picker from offering it.
        projectBusy={queryBusy || rows.some((r) => r.busy)
          || Object.values(work.turns).some((turns) => turns.at(-1)?.pending)}
        view={view}
        tasks={list}
        others={others}
        selected={selected}
        prs={prs.project === repo ? prs.rows : []}
        onProject={project}
        onView={(v) => {
          if (v === 'map') setMapped(true)
          setView(v)
        }}
        onSelect={setSelected}
        onSettings={() => setSetting(true)}
        onNew={() => {
          setView('chat')
          setSeed({ focus: 'next', text: NEW_TASK })
        }}
        onLoop={async (numbers) => {
          // Asked here, on a click: a browser grants it only to a gesture.
          if ('Notification' in window && Notification.permission === 'default') void Notification.requestPermission()
          const { results } = await api.startLoops(numbers)
          readPrs()
          readSpecs()
          refresh()
          const failed = results.filter((r) => r.error)
          if (failed.length) throw new Error(failed.map((r) => `#${r.number} — ${r.error}`).join(' · '))
        }}
      />

      <main className="flex min-h-0 min-w-0 flex-col border-r border-border">
        <header className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-border bg-card px-5">
          <h1 className="min-w-0 truncate font-mono text-[12px] text-muted-foreground">{middle}</h1>
          {view === 'projects' ? (
            <Btn tone="ghost" onClick={() => setView('chat')}>← 돌아가기</Btn>
          ) : (
            <div role="tablist" aria-label="가운데" className="flex h-7 shrink-0 rounded-md border border-border p-0.5">
              {(['chat', 'map'] as const).map((v) => (
                <button key={v} type="button" role="tab" aria-selected={view === v}
                  onClick={() => {
                    if (v === 'map') setMapped(true)
                    setView(v)
                  }}
                  className={cn('rounded-[4px] px-2.5 text-[12.5px]',
                    view === v ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:text-foreground')}>
                  {v === 'chat' ? '대화' : '지도'}
                </button>
              ))}
            </div>
          )}
        </header>
        {fault && (
          <div role="alert" className="flex items-start gap-2 border-b border-destructive/30 bg-destructive/10 px-5 py-2 text-[12.5px] text-destructive">
            <span className="min-w-0 flex-1 whitespace-pre-wrap">{fault}</span>
            <button type="button" className="shrink-0 hover:underline" onClick={() => setFault('')}>닫기</button>
          </div>
        )}
        {/* The conversation and the map stay mounted: an answer streaming in
            one focus is not cut by a look at the map, nor the map redrawn. */}
        <div className={cn('min-h-0 flex-1', view !== 'chat' && 'hidden')}>
          <Query
            channels={channels}
            options={options}
            on={on}
            seed={seed}
            onChannels={accept}
            onBusy={setQueryBusy}
            specs={specs}
            onSpecs={readSpecs}
            onStart={start}
          />
        </div>
        {mapped && (
          <div className={cn('min-h-0 flex-1', view !== 'map' && 'hidden')}>
            <RepoMap repo={repo} on={on} onAsk={(file) => {
              setSeed({ focus: 'wiki', text: `\`${file}\` ` })
              setView('chat')
            }} />
          </div>
        )}
        {view === 'projects' && <div className="min-h-0 flex-1"><Projects current={repo} /></div>}
      </main>

      <section aria-label="작업" className="flex min-h-0 min-w-0 flex-col">
        <header className="flex h-11 shrink-0 items-center gap-3 border-b border-border bg-card px-5">
          <h2 className="min-w-0 truncate font-heading text-[14px] font-semibold">
            {task?.name ?? other?.name ?? '작업'}
          </h2>
          {(task || other) && (
            <span className="flex shrink-0 gap-1.5 font-mono text-[10.5px]">
              {(task?.pr ?? other?.pr) && <span className="text-muted-foreground">#{task?.pr ?? other?.pr}</span>}
              {(task?.round || other?.round) ? <span className="text-muted-foreground">R{task?.round || other?.round}</span> : null}
              <span className={TONE[spec ? phase(spec.state) : 'none']}>{spec?.state ?? (other ? other.line : '명세 없음')}</span>
            </span>
          )}
          {!task && !other && <span className="truncate text-[12.5px] text-faint">왼쪽에서 작업을 고른다</span>}
          {tidy && (
            <Btn className="ml-auto" onClick={() => void remove(row!.path)}
              title="작업트리와 브랜치를 지운다. 브랜치의 변경은 원본 HEAD 에 다 있다">
              작업트리 정리
            </Btn>
          )}
        </header>
        {task && <SpecSummary key={spec?.id ?? task.key} spec={spec} onStart={start} />}
        <div role="tablist" aria-label="작업 면" className="flex h-9 shrink-0 items-end gap-1 border-b border-border px-3">
          {TABS.map((t) => (
            <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => pick(t.id)}
              className={cn('-mb-px flex h-8 items-center gap-1.5 border-b-2 px-2.5 text-[12.5px]',
                tab === t.id ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground')}>
              {t.label}
              {t.id === 'agent' && waiting && <span className="size-1.5 rounded-full bg-wait" title="승인을 기다린다" />}
            </button>
          ))}
        </div>
        <div className="flex min-h-0 flex-1">
          <div className="min-w-0 flex-1">
            {tab === 'agent' && (
              <Agent
                key={path}
                row={row}
                turns={(path && work.turns[path]) || []}
                options={options}
                choice={choice}
                on={on}
                onChoice={setChoice}
                onSend={order}
                onAnswer={(turn, id, allow, scope) => work.answer(path, turn, id, allow, scope)}
                onStop={(turn) => work.stop(path, turn).catch((err) => setFault(String(err)))}
                rules={(path && work.rules[path]?.list) || []}
                onClearRules={() => work.clearRules(path).catch((err) => setFault(String(err)))}
                onReset={(keep) => work.reset(path, keep)}
                onPeek={showPeek}
              />
            )}
            {tab === 'review' && (
              <Review key={spec?.id} spec={spec} onChanged={() => {
                readSpecs()
                readPrs()
                readLoops()
                refresh()
              }} />
            )}
            {/* Kept mounted: leaving the tab must not end the shell. */}
            <div className={cn('h-full', tab !== 'terminal' && 'hidden')}>
              <Terminal cwd={path} theme={theme} />
            </div>
          </div>
          {peek && <Peek data={peek.data} error={peek.error} onClose={() => setPeek(null)} />}
        </div>
      </section>

      {setting && (
        <Settings sw={sw} theme={theme} options={options} loop={loopSettings} onSwitch={flip} onTheme={setTheme}
          onLoop={async (s) => setLoopSettings(await api.setLoopSettings(s))} onClose={() => setSetting(false)}
          onProjects={() => {
            setSetting(false)
            setView('projects')
          }} />
      )}
    </div>
  )
}
