import { useCallback, useEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { Agent } from '@/components/Agent'
import type { Choice } from '@/components/Toolbar'
import { Peek } from '@/components/Peek'
import { Query } from '@/components/Query'
import { Rail } from '@/components/Rail'
import type { Other, View } from '@/components/Rail'
import { Projects } from '@/components/Projects'
import { Review } from '@/components/Review'
import { Terminal, closed } from '@/components/Terminal'
import { WikiMap } from '@/components/WikiMap'
import * as api from '@/lib/api'
import type { Channel, LoopRow, LoopSettings, Options, Peek as PeekData, Pr, Spec, Switch, Worktree } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useWork } from '@/lib/work'

type Theme = 'dark' | 'light'

const LOOPING = /^(리뷰 대기|리뷰 R\d+|고치는 중 R\d+)$/

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

/** One window: the worktrees on the left, the wiki query in the middle, the
 *  selected worktree's agent and shell on the right. */
export default function App() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [options, setOptions] = useState<Options | null>(null)
  const [fault, setFault] = useState('')
  const [sw, setSw] = useState<Switch | null>(null)
  const [rows, setRows] = useState<Worktree[]>([])
  const [specs, setSpecs] = useState<Spec[]>([])
  const [selected, setSelected] = useState('')
  const [view, setView] = useState<View>('query')
  const [seed, setSeed] = useState<{ text: string } | null>(null)
  const [queryBusy, setQueryBusy] = useState(false)
  const [choice, setChoice] = useState<Choice>({ model: '', effort: '' })
  const [peek, setPeek] = useState<{ data: PeekData | null; error?: string } | null>(null)
  // Dark unless the person chose light. Remembered per machine, not per server.
  const [theme, setTheme] = useState<Theme>(() => stored('theme', 'dark'))
  const [prs, setPrs] = useState<Pr[]>([])
  const [loopRows, setLoopRows] = useState<LoopRow[]>([])
  const [turnsElsewhere, setTurnsElsewhere] = useState<{ path: string; repo: string }[]>([])
  const [loopSettings, setLoopSettings] = useState<LoopSettings | null>(null)
  const [tab, setTab] = useState<'agent' | 'review'>('agent')
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
  const [making, setMaking] = useState(false)

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
  const elsewhere = useRef(new Set<string>())

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
        setSelected((path) => (rows.some((r) => r.path === path) || elsewhere.current.has(path) ? path : ''))
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
      .then(({ project, rows }) => project === expected.current && setPrs(rows))
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
  }, [repo, attach, readSpecs, readLoops, readPrs])

  useEffect(() => {
    if (selected) work.load(selected)
  }, [selected, work])

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
    const path = selected
    await work.send(path, text, choice)
    refresh()
    api.getSwitch().then(setSw).catch(() => {})
  }, [selected, choice, work, refresh])

  // `[시작]`: the server makes the worktree and starts its first turn; the
  // screen selects it, and loading it attaches to that turn.
  const start = useCallback(async (id: string) => {
    const { path } = await api.startSpec(id, choice)
    readSpecs()
    refresh()
    setSelected(path)
  }, [choice, readSpecs, refresh])

  const showPeek = useCallback(async (path: string, line: number) => {
    if (!selected) return
    setPeek({ data: null })
    try {
      setPeek({ data: await api.peek(selected, path, line) })
    } catch (err) {
      setPeek({ data: null, error: String(err) })
    }
  }, [selected])

  const others: Other[] = [
    ...loopRows.filter((l) => l.repo !== repo && l.worktree).map((l) => ({
      path: l.worktree!, repo: l.repo, label: `${l.pr ? `#${l.pr} ` : ''}${l.round ? `R${l.round} ` : ''}${l.state}` })),
    ...turnsElsewhere.filter((t) => t.repo !== repo && !loopRows.some((l) => l.worktree === t.path))
      .map((t) => ({ ...t, label: '도는 중' })),
  ]
  const otherPaths = new Set(others.map((o) => o.path))
  useEffect(() => {
    elsewhere.current = new Set(otherPaths)
  })
  // A worktree of another project has no row in this project's list; the
  // pane still shows it, and a new instruction there is refused by the server.
  const row = rows.find((r) => r.path === selected) ?? (otherPaths.has(selected)
    ? { path: selected, name: selected.split(/[\\/]/).pop() ?? '', branch: '', dirty: false, merged: false,
        live: true, busy: false }
    : undefined)
  const owning = specs.find((s) => s.worktree === selected)
  const waiting = new Set([
    ...Object.entries(work.turns).filter(([, turns]) => turns.some((t) => t.pending
      && t.steps.some((s) => s.kind === 'approval' && s.answer === undefined))).map(([path]) => path),
    ...loopRows.filter((l) => l.waiting && l.worktree).map((l) => l.worktree!),
  ])
  const on = sw?.translate ?? false

  return (
    <div className="grid h-screen grid-cols-[15rem_minmax(0,1fr)_minmax(0,1fr)] overflow-hidden">
      <Rail
        repo={repo}
        options={options}
        // The server refuses the switch too; this keeps the picker from offering it.
        projectBusy={queryBusy || making || rows.some((r) => r.busy)
          || Object.values(work.turns).some((turns) => turns.at(-1)?.pending)}
        // The server's `busy` is as old as the last listing; a turn this
        // window is streaming is known here first.
        rows={rows.map((r) => ({ ...r, busy: r.busy || Boolean(work.turns[r.path]?.at(-1)?.pending) }))}
        waiting={waiting}
        specs={Object.fromEntries(specs.filter((s) => s.worktree).map((s) => [s.worktree, {
          state: s.state, pr: s.pr?.number ?? null, round: (s.rounds ?? []).filter((r) => !r.stale).length }]))}
        prs={prs}
        others={others}
        loopSettings={loopSettings}
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
        onLoopSettings={async (s) => setLoopSettings(await api.setLoopSettings(s))}
        selected={selected}
        view={view}
        sw={sw}
        theme={theme}
        onProject={project}
        onSelect={setSelected}
        onMake={async (task) => {
          setMaking(true)
          try {
            const { path } = await api.makeWorktree(task)
            refresh()
            setSelected(path)
          } finally {
            setMaking(false)
          }
        }}
        onRemove={async (path) => {
          // A terminal's shell may stand in that folder, and Windows will not
          // delete a directory a process stands in. Rendered now so a selected
          // one starts closing; then every close there is waited on — one
          // started a moment ago by selecting elsewhere counts too.
          if (path === selected) flushSync(() => setSelected(''))
          await closed(path)
          await api.removeWorktree(path)
          // The same task name makes the same path again; its turns must not
          // come back with it.
          work.forget(path)
          refresh()
        }}
        onView={setView}
        onSwitch={flip}
        onTheme={setTheme}
      />

      <main className="flex min-h-0 min-w-0 flex-col border-r border-border">
        {fault && (
          <div role="alert" className="border-b border-destructive/30 bg-destructive/10 px-5 py-2 text-[12.5px] text-destructive">
            {fault}
          </div>
        )}
        <div className="min-h-0 flex-1">
          {view === 'map' ? (
            <div className="h-full overflow-auto"><WikiMap on={on} /></div>
          ) : view === 'projects' ? (
            <Projects />
          ) : (
            <Query
              channels={channels}
              options={options}
              on={on}
              onChannels={accept}
              onBusy={setQueryBusy}
              onDraft={(text) => setSeed({ text })}
              specs={specs}
              onSpecs={readSpecs}
              onStart={start}
            />
          )}
        </div>
      </main>

      <div className="flex min-h-0 min-w-0 flex-col">
        <div className="flex min-h-0 flex-[3]">
          <div className="flex min-w-0 flex-1 flex-col">
            {owning?.pr && (
              <div role="tablist" className="flex gap-1 border-b border-border bg-card px-3 pt-1.5">
                {(['agent', 'review'] as const).map((t) => (
                  <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                    className={cn('flex items-center gap-1.5 rounded-t-md px-2.5 py-1 text-[12.5px]',
                      tab === t ? 'bg-background font-semibold' : 'text-muted-foreground hover:bg-secondary')}>
                    {t === 'agent' ? '에이전트' : `리뷰 #${owning.pr!.number}`}
                    {t === 'agent' && waiting.has(selected) && <span className="size-1.5 rounded-full bg-wait" title="승인을 기다린다" />}
                  </button>
                ))}
              </div>
            )}
            <div className="min-h-0 flex-1">
            {owning?.pr && tab === 'review' ? (
              <Review spec={owning} onChanged={() => {
                readSpecs()
                readPrs()
                readLoops()
                refresh()
              }} />
            ) : (
            <Agent
              row={row}
              turns={(selected && work.turns[selected]) || []}
              options={options}
              choice={choice}
              on={on}
              seed={seed}
              onChoice={setChoice}
              onSend={order}
              onAnswer={(turn, id, allow, scope) => work.answer(selected, turn, id, allow, scope)}
              onStop={(turn) => work.stop(selected, turn).catch((err) => setFault(String(err)))}
              rules={(selected && work.rules[selected]?.list) || []}
              onClearRules={() => work.clearRules(selected).catch((err) => setFault(String(err)))}
              onReset={() => work.reset(selected).catch((err) => setFault(String(err)))}
              onPeek={showPeek}
            />
            )}
            </div>
          </div>
          {peek && <Peek data={peek.data} error={peek.error} onClose={() => setPeek(null)} />}
        </div>
        <div className="min-h-0 flex-[2] border-t border-border">
          <Terminal cwd={selected} theme={theme} />
        </div>
      </div>
    </div>
  )
}
