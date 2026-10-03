import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { MobileAccess } from '@/components/MobileAccess'
import { openWebLink } from '@/lib/links'

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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MobileAccess><App /></MobileAccess>
  </StrictMode>,
)
