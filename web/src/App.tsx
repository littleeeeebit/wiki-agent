import { useCallback, useEffect, useState } from 'react'
import { Agent } from '@/components/Agent'
import type { Choice } from '@/components/Toolbar'
import { Peek } from '@/components/Peek'
import { Query } from '@/components/Query'
import { Rail } from '@/components/Rail'
import { Terminal } from '@/components/Terminal'
import { WikiMap } from '@/components/WikiMap'
import * as api from '@/lib/api'
import type { Channel, Options, Peek as PeekData, Switch, Worktree } from '@/lib/api'
import { useWork } from '@/lib/work'

type Theme = 'dark' | 'light'

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
  const [selected, setSelected] = useState('')
  const [view, setView] = useState<'query' | 'map'>('query')
  const [seed, setSeed] = useState<{ text: string } | null>(null)
  const [queryBusy, setQueryBusy] = useState(false)
  const [choice, setChoice] = useState<Choice>({ model: '', effort: '' })
  const [peek, setPeek] = useState<{ data: PeekData | null; error?: string } | null>(null)
  // Dark unless the person chose light. Remembered per machine, not per server.
  const [theme, setTheme] = useState<Theme>(() => stored('theme', 'dark'))
  const work = useWork()
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

  const refresh = useCallback(() => {
    api.getWorktrees()
      .then(({ rows }) => {
        setRows(rows)
        setSelected((path) => (rows.some((r) => r.path === path) ? path : ''))
      })
      .catch((err) => setFault(String(err)))
  }, [])

  useEffect(() => {
    Promise.all([api.getChannels(), api.getSwitch()])
      .then(([list, now]) => {
        setChannels(list)
        setSw(now)
      })
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
    refresh()
    window.addEventListener('focus', refresh)
    return () => window.removeEventListener('focus', refresh)
  }, [repo, refresh])

  useEffect(() => {
    if (selected) work.load(selected)
  }, [selected, work])

  const project = useCallback(async (next: string) => {
    const wiki = channels.find((c) => c.id === 'wiki') ?? channels[0]
    if (!wiki) return
    setFault('')
    try {
      await api.setConfig(wiki.id, { repo: next, model: wiki.model, effort: wiki.effort })
      setChannels(await api.getChannels())
      setSelected('')
    } catch (err) {
      setFault(String(err))
    }
  }, [channels])

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

  const showPeek = useCallback(async (path: string, line: number) => {
    if (!selected) return
    setPeek({ data: null })
    try {
      setPeek({ data: await api.peek(selected, path, line) })
    } catch (err) {
      setPeek({ data: null, error: String(err) })
    }
  }, [selected])

  const row = rows.find((r) => r.path === selected)
  const waiting = new Set(Object.entries(work.turns).filter(([, turns]) => turns.some((t) => t.pending
    && t.steps.some((s) => s.kind === 'approval' && s.answer === undefined))).map(([path]) => path))
  const on = sw?.translate ?? false

  return (
    <div className="grid h-screen grid-cols-[15rem_minmax(0,1fr)_minmax(0,1fr)] overflow-hidden">
      <Rail
        repo={repo}
        options={options}
        // The server refuses the switch too; this keeps the picker from offering it.
        projectBusy={queryBusy || rows.some((r) => r.busy)
          || Object.values(work.turns).some((turns) => turns.at(-1)?.pending)}
        // The server's `busy` is as old as the last listing; a turn this
        // window is streaming is known here first.
        rows={rows.map((r) => ({ ...r, busy: r.busy || Boolean(work.turns[r.path]?.at(-1)?.pending) }))}
        waiting={waiting}
        selected={selected}
        view={view}
        sw={sw}
        theme={theme}
        onProject={project}
        onSelect={setSelected}
        onMake={async (task) => {
          const { path } = await api.makeWorktree(task)
          refresh()
          setSelected(path)
        }}
        onRemove={async (path) => {
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
          ) : (
            <Query
              channels={channels}
              options={options}
              on={on}
              onChannels={setChannels}
              onBusy={setQueryBusy}
              onDraft={(text) => setSeed({ text })}
            />
          )}
        </div>
      </main>

      <div className="flex min-h-0 min-w-0 flex-col">
        <div className="flex min-h-0 flex-[3]">
          <div className="min-w-0 flex-1">
            <Agent
              row={row}
              turns={(selected && work.turns[selected]) || []}
              options={options}
              choice={choice}
              on={on}
              seed={seed}
              onChoice={setChoice}
              onSend={order}
              onAnswer={(turn, id, allow) => work.answer(selected, turn, id, allow)}
              onReset={() => work.reset(selected).catch((err) => setFault(String(err)))}
              onPeek={showPeek}
            />
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
