import { useEffect, useRef, useState } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import '@xterm/xterm/css/xterm.css'
import { useParagraphOverlay } from '@/lib/overlay'

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
  const terminal = useRef<XTerm | null>(null)
  const [fault, setFault] = useState('')
  const [selection, setSelection] = useState('')
  const [approved, setApproved] = useState<{ cwd: string; theme: string; text: string } | null>(null)
  const confirmed = approved?.cwd === cwd && approved.theme === theme ? approved.text : ''
  const shown = useParagraphOverlay(confirmed, on)

  useEffect(() => {
    if (!shell || !cwd || !box.current) return
    setFault('')
    setSelection('')
    setApproved(null)
    const term = new XTerm({ fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 12.5,
      cursorBlink: true, theme: colors(), scrollback: 5000, convertEol: true })
    terminal.current = term
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(box.current)
    if (box.current.clientWidth && box.current.clientHeight) fit.fit()

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
      if (!box.current?.clientWidth || !box.current.clientHeight) return
      fit.fit()
      if (id) void invoke('pty_resize', { id, cols: term.cols, rows: term.rows })
    })
    resized.observe(box.current)

    return () => {
      alive = false
      terminal.current = null
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
          <div ref={box} className="min-h-0 flex-1 overflow-hidden px-2 py-1" />
          {on && <section aria-label="터미널 출력 번역" className="max-h-[40%] shrink-0 overflow-y-auto border-t border-border px-4 py-2">
            <p className="mb-1 text-[11px] text-faint">터미널 출력은 자동 전송하지 않는다. 번역할 출력을 선택한 뒤 비밀값·개인정보를 지우고 확인한다.</p>
            <button type="button" className="text-[12px] text-primary" onClick={() => {
              setSelection(terminal.current?.getSelection() ?? '')
              setApproved(null)
            }}>선택한 출력 가져오기</button>
            {selection && <>
              <label className="mt-2 block text-[12px]">외부 번역 서비스로 보낼 내용
                <textarea value={selection} rows={3} onChange={(e) => setSelection(e.target.value)}
                  className="mt-1 w-full rounded border border-border bg-background p-2 font-mono text-[12px]" />
              </label>
              <button type="button" className="text-[12px] text-primary" onClick={() => setApproved({ cwd, theme, text: selection })}>
                확인한 내용 번역</button>
            </>}
            {confirmed && <p className="mt-2 whitespace-pre-wrap break-words text-[12.5px] leading-relaxed">{shown}</p>}
          </section>}
        </>
      )}
    </section>
  )
}
