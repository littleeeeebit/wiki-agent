/** The desktop remains the only owner of execution. Remote requests reuse its
 *  HTTP routes over WebSocket because temporary tunnels buffer SSE. */
export let remote = false
export type MobileLayout = 'portrait' | 'landscape'
let preferred: MobileLayout = 'portrait'
try { if (localStorage.getItem('mobile-layout') === 'landscape') preferred = 'landscape' } catch { /* Storage may be blocked. */ }
const compact = window.matchMedia('(max-width: 1100px)')
const applyLayout = () => {
  document.documentElement.dataset.mobileLayout = remote ? preferred : compact.matches ? 'portrait' : 'desktop'
}
// Only local windows follow viewport width. A paired phone keeps its chosen mode.
compact.addEventListener('change', applyLayout)
export const mobileLayout = () => preferred
export const setMobileLayout = (value: MobileLayout) => {
  preferred = value
  try { localStorage.setItem('mobile-layout', value) } catch { /* Keep the choice for this session. */ }
  applyLayout()
}
export const setRemote = (value: boolean) => { remote = value; applyLayout() }

export type MobileStatus = {
  local: boolean; paired?: boolean; enabled?: boolean; starting?: boolean; origin?: string; error?: string
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
