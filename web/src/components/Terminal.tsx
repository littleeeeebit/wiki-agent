import { useEffect, useRef, useState } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import '@xterm/xterm/css/xterm.css'

/** The Tauri shell holds the terminals; a browser tab has none to offer. */
const shell = '__TAURI_INTERNALS__' in window

/** Settles once the last terminal closed has exited. Removing a worktree waits
 *  on it: Windows will not delete a folder a shell still stands in. */
let closing: Promise<unknown> = Promise.resolve()
export const closed = () => closing

function colors() {
  const css = getComputedStyle(document.documentElement)
  const v = (name: string) => css.getPropertyValue(name).trim()
  return { background: v('--card'), foreground: v('--foreground'), cursor: v('--primary'),
    selectionBackground: v('--accent') }
}

/** A shell in the selected worktree, for the person. Python never sees it.
 *
 *  Output arrives as bytes: a chunk can end halfway through a Korean
 *  character, and xterm.js joins the halves only when it is given bytes.
 *  The listener is attached before the shell is opened and holds what
 *  arrives until it knows its id, or the first prompt is lost. */
export function Terminal({ cwd, theme }: { cwd: string; theme: string }) {
  const box = useRef<HTMLDivElement>(null)
  const [fault, setFault] = useState('')

  useEffect(() => {
    if (!shell || !cwd || !box.current) return
    setFault('')
    const term = new XTerm({ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 12.5,
      cursorBlink: true, theme: colors(), scrollback: 5000 })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(box.current)
    fit.fit()

    let id = 0
    let alive = true
    const early = new Map<number, Uint8Array[]>()
    const stops: (() => void)[] = []
    const ready = Promise.all([
      listen<[number, number[]]>('pty-out', ({ payload: [from, bytes] }) => {
        const chunk = new Uint8Array(bytes)
        if (from === id) term.write(chunk)
        else if (!id) early.set(from, [...(early.get(from) ?? []), chunk])
      }),
      listen<number>('pty-exit', ({ payload }) => {
        if (payload === id) term.write('\r\n\x1b[2m[셸이 끝났다]\x1b[0m\r\n')
      }),
    ]).then((unlisten) => stops.push(...unlisten))

    const opened = ready.then(() => invoke<number>('pty_open', { cwd, cols: term.cols, rows: term.rows }))
    opened
      .then((got) => {
        if (!alive) return
        id = got
        for (const chunk of early.get(got) ?? []) term.write(chunk)
        early.clear()
      })
      .catch((err) => alive && setFault(String(err)))

    const typed = term.onData((data) => id && void invoke('pty_write', { id, data }))
    const resized = new ResizeObserver(() => {
      fit.fit()
      if (id) void invoke('pty_resize', { id, cols: term.cols, rows: term.rows })
    })
    resized.observe(box.current)

    return () => {
      alive = false
      resized.disconnect()
      typed.dispose()
      stops.forEach((stop) => stop())
      // Through `opened`, not `id`: a shell still opening closes too.
      closing = opened.then((got) => invoke('pty_close', { id: got })).catch(() => undefined)
      term.dispose()
    }
  }, [cwd, theme])

  return (
    <section aria-label="터미널" className="flex h-full min-h-0 flex-col bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-1.5">
        <span className="font-heading text-[11px] font-semibold text-muted-foreground">터미널</span>
        {fault
          ? <span role="alert" className="truncate pl-3 text-[12.5px] text-destructive">{fault}</span>
          : <span className="truncate pl-3 font-mono text-[10.5px] text-faint">{cwd}</span>}
      </div>
      {!shell ? (
        <p className="p-4 text-[12.5px] text-faint">터미널은 앱 창(tool\app.cmd)에서만 열린다. 브라우저 탭에는 셸이 없다.</p>
      ) : !cwd ? (
        <p className="p-4 text-[12.5px] text-faint">작업트리를 고르면 그 안에서 셸이 열린다.</p>
      ) : (
        <div ref={box} className="min-h-0 flex-1 px-2 py-1" />
      )}
    </section>
  )
}
