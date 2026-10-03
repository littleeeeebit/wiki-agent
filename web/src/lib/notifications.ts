import { invoke } from '@tauri-apps/api/core'

/** Use the desktop plugin explicitly: its permission is independent of WebView permission. */
export async function requestNotifications(): Promise<boolean> {
  if ('__TAURI_INTERNALS__' in window) {
    if (await invoke<boolean | null>('plugin:notification|is_permission_granted')) return true
    return await invoke<string>('plugin:notification|request_permission') === 'granted'
  }
  if (!('Notification' in window)) return false
  return (Notification.permission === 'default' ? await Notification.requestPermission() : Notification.permission) === 'granted'
}

export async function notify(title: string, body: string): Promise<void> {
  try {
    if ('__TAURI_INTERNALS__' in window) {
      if (await requestNotifications()) await invoke('plugin:notification|notify', { options: { title, body } })
      return
    }
    if (!('Notification' in window) || Notification.permission !== 'granted') return
    const registration = 'serviceWorker' in navigator ? await navigator.serviceWorker.getRegistration() : undefined
    if (registration) await registration.showNotification(title, { body })
    else new Notification(title, { body })
  } catch (error) {
    window.dispatchEvent(new CustomEvent('notification-failed', { detail: String(error) }))
  }
}
