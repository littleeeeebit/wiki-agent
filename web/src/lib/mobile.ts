/** The desktop remains the only owner of execution. Remote requests reuse its
 *  HTTP routes over WebSocket because temporary tunnels buffer SSE. */
export let remote = false
const compact = window.matchMedia('(max-width: 1100px)')
let chosenMode: 'portrait' | 'landscape' = 'portrait'
const applyLayout = () => {
  const root = document.documentElement
  root.dataset.mobileClient = remote ? 'remote' : 'local'
  root.dataset.mobileLayout = remote ? chosenMode : compact.matches ? 'portrait' : 'desktop'
}
// Only an explicit APK menu choice sets the mobile mode. Never infer it from
// viewport shape, orientation sensors, auto-rotate or the keyboard's height.
window.addEventListener('mobile-screen-mode', (event) => {
  const mode = (event as CustomEvent<unknown>).detail
  if (!remote || (mode !== 'portrait' && mode !== 'landscape')) return
  chosenMode = mode
  applyLayout()
})
compact.addEventListener('change', applyLayout)
export const setRemote = (value: boolean) => {
  remote = value
  const nativeMode = document.documentElement.dataset.nativeMode
  if (remote && (nativeMode === 'portrait' || nativeMode === 'landscape')) chosenMode = nativeMode
  applyLayout()
}

export type MobileStatus = {
  local: boolean; paired?: boolean; enabled?: boolean; starting?: boolean; origin?: string; error?: string; apk_available?: boolean
}

type InstallPrompt = Event & {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

let installPrompt: InstallPrompt | null = null
let installedHere = false
const standalone = window.matchMedia('(display-mode: standalone)')
const installChanged = () => window.dispatchEvent(new Event('mobile-install-changed'))

window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault()
  installPrompt = event as InstallPrompt
  installChanged()
})
window.addEventListener('appinstalled', () => { installPrompt = null; installedHere = true; installChanged() })
standalone.addEventListener('change', installChanged)

export type InstallState = 'installed' | 'ready' | 'manual'
export const installState = (): InstallState => installedHere || standalone.matches
  || Boolean((navigator as Navigator & { standalone?: boolean }).standalone)
  ? 'installed' : installPrompt ? 'ready' : 'manual'

export async function installApp(): Promise<void> {
  if (!installPrompt) return
  const prompt = installPrompt
  installPrompt = null
  await prompt.prompt()
  installedHere = (await prompt.userChoice).outcome === 'accepted'
  installChanged()
}

export async function status(): Promise<MobileStatus> {
  const res = await fetch('/api/mobile/status', { cache: 'no-store', signal: AbortSignal.timeout(8000) })
  if (!res.ok) throw new Error('PC에 연결하지 못했습니다')
  return res.json()
}

/** Return a native Response so the existing JSON and SSE readers stay shared. */
export function request(url: string, init: RequestInit = {}): Promise<Response> {
  if (!remote) return fetch(url, init)
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`wss://${location.host}/api/mobile/request`)
    ws.binaryType = 'arraybuffer'
    let started = false
    let cancelled = false
    let stream: ReadableStreamDefaultController<Uint8Array>
    const body = new ReadableStream<Uint8Array>({ start(controller) { stream = controller },
      cancel() { cancelled = true; ws.close() } })
    const abort = () => {
      const error = new DOMException('Aborted', 'AbortError')
      if (!cancelled) { cancelled = true; stream.error(error) }
      reject(error)
      ws.close()
    }
    if (init.signal?.aborted) { abort(); return }
    init.signal?.addEventListener('abort', abort, { once: true })
    ws.onopen = () => {
      if (cancelled) { ws.close(); return }
      ws.send(JSON.stringify({ url, method: init.method ?? 'GET', body: init.body ?? '',
        project: new Headers(init.headers).get('X-Project') ?? '' }))
    }
    ws.onmessage = ({ data }) => {
      if (cancelled) return
      if (typeof data === 'string') {
        const { status, headers } = JSON.parse(data) as { status: number; headers: [string, string][] }
        started = true
        if (status === 401) window.dispatchEvent(new Event('mobile-unauthorized'))
        resolve(new Response([204, 304].includes(status) ? null : body, { status, headers }))
      } else stream.enqueue(new Uint8Array(data))
    }
    ws.onclose = ({ code }) => {
      init.signal?.removeEventListener('abort', abort)
      if (code === 1008) window.dispatchEvent(new Event('mobile-unauthorized'))
      if (cancelled) return
      if (code === 1000 && started) stream.close()
      else {
        window.dispatchEvent(new Event('mobile-disconnected'))
        const error = new Error('PC 연결이 끊겼습니다. 다시 연결하세요')
        stream.error(error)
        reject(error)
      }
    }
    ws.onerror = () => {
      window.dispatchEvent(new Event('mobile-disconnected'))
      reject(new Error('PC에 연결하지 못했습니다'))
    }
  })
}
