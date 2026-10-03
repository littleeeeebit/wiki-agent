import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { MobileAccess } from '@/components/MobileAccess'

// In the window, the WebView's own menu — save as, print — is a web page's,
// not this program's. It stays where it does something: a text box, selected
// text, the terminal. The browser keeps its menu everywhere.
if ('__TAURI_INTERNALS__' in window) {
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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MobileAccess><App /></MobileAccess>
  </StrictMode>,
)
