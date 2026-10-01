import { useEffect, useRef, useState } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import type { IMarker } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import '@xterm/xterm/css/xterm.css'
import { useOverlay } from '@/lib/overlay'

/** The Tauri shell holds the terminals; a browser tab has none to offer. */
const shell = '__TAURI_INTERNALS__' in window

/** Every close started in a folder, until its shell has exited. Removing a
 *  worktree waits on all of them — the one just deselected, and the ones a
 *  theme change replaced — as Windows will not delete a folder a shell still
 *  stands in. */
const closing = new Map<string, Promise<unknown>>()
export const closed = (cwd: string) => closing.get(cwd) ?? Promise.resolve()

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
export function Terminal({ cwd, theme, on }: { cwd: string; theme: string; on: boolean }) {
  const box = useRef<HTMLDivElement>(null)
  const [fault, setFault] = useState('')
  const [output, setOutput] = useState<string[]>([])
  const shown = useOverlay(output, on)

  useEffect(() => {
    if (!shell || !cwd || !box.current) return
    setFault('')
    setOutput([])
    const term = new XTerm({ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 12.5,
      cursorBlink: true, theme: colors(), scrollback: 5000, convertEol: true })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(box.current)
    if (box.current.clientWidth && box.current.clientHeight) fit.fit()

    let id = 0
    let alive = true
    const early = new Map<number, Uint8Array[]>()
    const stops: (() => void)[] = []
    const inputs = new Set<IMarker>()
    let timer: number | undefined
    // Read xterm's parsed screen: ANSI cursor moves and split UTF-8 bytes have
    // already been handled. The mirror never changes the interactive terminal.
    const parsed = term.onWriteParsed(() => {
      if (timer !== undefined) return
      timer = window.setTimeout(() => {
        timer = undefined
        if (!alive) return
        const buffer = term.buffer.active
        const inputRows = new Set([...inputs].map((marker) => marker.line))
        const lines: string[] = []
        let input = false
        // ponytail: last 80 parsed rows, not a transcript; persist PTY output if full history is needed.
        for (let i = Math.max(0, buffer.length - 80); i < buffer.length; i++) {
          const line = buffer.getLine(i)
          const text = line?.translateToString(true) ?? ''
          input = inputRows.has(i) || /^(?:PS\s+|[^\s]+@[^\s]+.*[$#]|[$>]\s)/.test(text)
            || (!!line?.isWrapped && input)
          if (input) continue
          if (line?.isWrapped && lines.length) lines[lines.length - 1] += text
          else if (text.trim() || lines.length) lines.push(text)
        }
        while (lines.length && !lines.at(-1)?.trim()) lines.pop()
        setOutput(lines)
      }, 600)
    })
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

    const typed = term.onData((data) => {
      if (!id) return
      const pieces = data.split(/\r\n|\r|\n/)
      const count = Math.max(1, pieces.length - (pieces.at(-1) === '' ? 1 : 0))
      for (let i = 0; i < count; i++) {
        const row = term.buffer.active.baseY + term.buffer.active.cursorY + i
        if ([...inputs].some((marker) => marker.line === row)) continue
        const marker = term.registerMarker(i)
        if (marker) { inputs.add(marker); marker.onDispose(() => inputs.delete(marker)) }
      }
      void invoke('pty_write', { id, data })
    })
    const resized = new ResizeObserver(() => {
      if (!box.current?.clientWidth || !box.current.clientHeight) return
      fit.fit()
      if (id) void invoke('pty_resize', { id, cols: term.cols, rows: term.rows })
    })
    resized.observe(box.current)

    return () => {
      alive = false
      window.clearTimeout(timer)
      parsed.dispose()
      resized.disconnect()
      typed.dispose()
      stops.forEach((stop) => stop())
      // Through `opened`, not `id`: a shell still opening closes too.
      const close = opened.then((got) => invoke('pty_close', { id: got })).catch(() => undefined)
      closing.set(cwd, Promise.all([closing.get(cwd), close]))
      term.dispose()
    }
  }, [cwd, theme])

  return (
    <section aria-label="터미널" className="flex h-full min-h-0 flex-col bg-card">
      {(fault || cwd) && (
        <div className="flex h-8 shrink-0 items-center border-b border-border px-4">
          {fault
            ? <span role="alert" className="truncate text-[12.5px] text-destructive">{fault}</span>
            : <span className="truncate font-mono text-[10.5px] text-faint">{cwd}</span>}
        </div>
      )}
      {!shell ? (
        <p className="p-5 text-[13.5px] text-faint">터미널은 앱 창(tool\app.cmd)에서만 열린다. 브라우저 탭에는 셸이 없다.</p>
      ) : !cwd ? (
        <p className="p-5 text-[13.5px] text-faint">작업트리가 있는 작업을 고르면 그 안에서 셸이 열린다.</p>
      ) : (
        <>
          <div ref={box} className="min-h-0 flex-1 px-2 py-1" />
          {on && output.length > 0 && <section aria-label="터미널 출력 번역" className="max-h-[40%] shrink-0 overflow-y-auto border-t border-border px-4 py-2">
            <p className="mb-1 text-[11px] text-faint">출력 번역 · 입력과 명령은 위 터미널에서 확인한다</p>
            {shown.map((text, i) => <p key={i} title={output[i]} className="whitespace-pre-wrap break-words text-[12.5px] leading-relaxed">{text || '\u00a0'}</p>)}
          </section>}
        </>
      )}
    </section>
  )
}
