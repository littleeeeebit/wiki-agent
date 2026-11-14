import { invoke } from '@tauri-apps/api/core'
import { remote } from '@/lib/mobile'

export async function openPreferences(page: 'settings' | 'updates') {
  if (remote && page === 'settings') window.dispatchEvent(new Event('preferences-open'))
  else if ('__TAURI_INTERNALS__' in window) await invoke('preferences_window', { page })
  else {
    const popup = window.open(`/?page=${page}`, `wiki-agent-${page}`, 'popup,width=1040,height=760')
    if (!popup) throw new Error('새 창을 허용한 뒤 다시 시도하세요')
  }
}

export const settingsChanged = () => {
  localStorage.setItem('settings-changed', String(Date.now()))
  window.dispatchEvent(new Event('preferences-saved'))
}
