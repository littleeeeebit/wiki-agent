import { useEffect, useState } from 'react'
import { Btn } from '@/components/Modal'
import * as api from '@/lib/api'
import type { PowerShellStatus } from '@/lib/api'
import { openWebLink } from '@/lib/links'
import { remote } from '@/lib/mobile'

export function PowerShell({ status, read, disabled }: {
  status?: PowerShellStatus; read: () => void; disabled: boolean
}) {
  const [starting, setStarting] = useState(false)
  const [fault, setFault] = useState('')
  useEffect(() => {
    if (!status?.install.running) return
    const timer = window.setInterval(read, 1000)
    return () => window.clearInterval(timer)
  }, [status?.install.running, read])

  function download(url: string) {
    if ('__TAURI_INTERNALS__' in window) void openWebLink(url).catch((err) => setFault(String(err)))
    else window.open(url, '_blank', 'noopener,noreferrer')
  }

  async function install() {
    if (!status) return
    setFault('')
    if (!status.winget) { download(status.download); return }
    setStarting(true)
    try {
      const result = await api.installPowerShell()
      if (result.download) { setFault(result.reason ?? ''); download(result.download) }
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
    } finally {
      setStarting(false)
      read()
    }
  }

  if (!status?.windows) return null
  return (
    <section aria-label="PowerShell" className="shrink-0 border-b border-border px-5 py-3 text-[12.5px]">
      <div className="flex items-center gap-3">
        <span>PowerShell {status.version ?? '없음'}</span>
        {status.needs_install && <Btn disabled={remote || disabled || starting || status.install.running}
          onClick={() => void install()}>{status.install.running ? 'PowerShell 7 설치 중…' : 'PowerShell 7 설치'}</Btn>}
      </div>
      {status.needs_install && !status.winget && (
        <p className="mt-1 text-muted-foreground">winget이 없어 공식 다운로드 페이지를 연다.</p>
      )}
      {status.install.output && <pre role="log" aria-live="polite"
        className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px]">{status.install.output}</pre>}
      {fault && <p role="alert" className="mt-1 whitespace-pre-wrap text-destructive">{fault}</p>}
    </section>
  )
}
