import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
