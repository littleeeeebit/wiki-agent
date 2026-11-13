import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { MobileAccess } from '@/components/MobileAccess'
import { OneshotWindow } from '@/components/OneshotWindow'
import { useRoute } from '@/lib/mode'
import { openWebLink } from '@/lib/links'
import { reportError } from '@/lib/api'
import { OnboardingGate } from '@/components/Onboarding'

// Bound repeated failures locally; reporting must never create another rejection.
let errorReports = 0
const logError = (error: unknown) => {
  if (errorReports++ >= 20) return
  void reportError(error instanceof Error ? error.message : String(error),
    error instanceof Error ? error.stack : '').catch(() => {})
}
window.addEventListener('error', (event) => logError(event.error || event.message))
window.addEventListener('unhandledrejection', (event) => logError(event.reason))

// In the window, the WebView's own menu — save as, print — is a web page's,
// not this program's. It stays where it does something: a text box, selected
// text, the terminal. The browser keeps its menu everywhere.
if ('__TAURI_INTERNALS__' in window) {
  const open = (e: MouseEvent) => {
    const link = e.target instanceof Element ? e.target.closest<HTMLAnchorElement>('a[href]') : null
    if (!link || !/^https?:\/\//i.test(link.getAttribute('href') ?? '') || e.button > 1) return
    e.preventDefault()
    void openWebLink(link.href).catch(() => window.alert('링크를 열지 못했다. 기본 브라우저 설정을 확인해라'))
  }
  window.addEventListener('click', open)
  window.addEventListener('auxclick', open)
  window.addEventListener('contextmenu', (e) => {
    const at = e.target instanceof Element ? e.target : null
    if (at?.closest('input, textarea, [contenteditable="true"], .xterm') || String(window.getSelection())) return
    e.preventDefault()
  })
}

// The worker keeps only a connection-help screen. Conversations and API
// responses remain on the desktop and are never put in browser caches.
if (!('__TAURI_INTERNALS__' in window) && 'serviceWorker' in navigator) {
  void navigator.serviceWorker.register('/sw.js').catch(() => {})
}

/** Maintenance and the oneshot window. The oneshot window mounts when first opened, and from then on
 *  both stay mounted: a stream or a draft in one survives a look at the other. */
function Windows() {
  const { mode } = useRoute()
  const [opened, setOpened] = useState(mode === 'oneshot')
  if (mode === 'oneshot' && !opened) setOpened(true)
  return (
    <>
      <div hidden={mode !== 'main'}><App /></div>
      {opened && <div hidden={mode !== 'oneshot'}><OneshotWindow /></div>}
    </>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MobileAccess><OnboardingGate><Windows /></OnboardingGate></MobileAccess>
  </StrictMode>,
)
