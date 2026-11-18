import { useEffect, useRef, useState } from 'react'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { Settings } from '@/components/Settings'
import * as api from '@/lib/api'
import { settingsChanged } from '@/lib/desktop'
import { leave } from '@/lib/leave'

/** A machine-local window, with no project or task stream mounted. */
export function SettingsWindow({ onClose }: { onClose?: () => void }) {
  const [sw, setSw] = useState<api.Switch | null>(null)
  const [options, setOptions] = useState<api.Options | null>(null)
  const [loop, setLoop] = useState<api.LoopSettings | null>(null)
  const [theme, setTheme] = useState<'dark' | 'light'>(() => localStorage.getItem('theme') === 'light' ? 'light' : 'dark')
  const [fault, setFault] = useState('')
  const generation = useRef(0)
  useEffect(() => {
    const mine = ++generation.current
    const watching = new AbortController()
    // The options read again while the server fetches the first Codex model list (`watchOptions`).
    const options = new Promise<api.Options>((first, failed) => {
      api.watchOptions((o) => { first(o); if (mine === generation.current) setOptions(o) }, watching.signal).catch(failed)
    })
    Promise.all([api.getSwitch(), options, api.getLoopSettings()]).then(([s, , l]) => {   // the options set themselves
      if (mine === generation.current) { setSw(s); setLoop(l) }
    }, (e) => { if (mine === generation.current) setFault(String(e)) })
    return () => { generation.current = mine + 1; watching.abort() }
  }, [])
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    document.documentElement.classList.toggle('light', theme === 'light')
    localStorage.setItem('theme', theme)
    settingsChanged()
  }, [theme])
  return <>{fault && <p role="alert" className="p-3 text-destructive">{fault}</p>}
    <Settings sw={sw} theme={theme} options={options} loop={loop}
      onTheme={setTheme} onSwitch={(mode) => {
        const mine = generation.current
        void api.setSwitch(mode).then((s) => { if (mine === generation.current) { setSw(s); settingsChanged() } },
          (e) => { if (mine === generation.current) setFault(String(e)) })
      }} onLoop={async (value) => {
        const mine = generation.current
        const next = await api.setLoopSettings(value)
        if (mine === generation.current) setLoop(next)
        settingsChanged()
      }} onClose={onClose ?? (() => { void leave(true).then((allowed) => {
        if (allowed) { if ('__TAURI_INTERNALS__' in window) void getCurrentWindow().destroy(); else window.close() }
      }) })} />
  </>
}
