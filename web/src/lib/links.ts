import { invoke } from '@tauri-apps/api/core'

/** File citations stay in the app; web links use the system browser in Tauri. */
export function fileLink(href: string): { path: string; line: number } | null {
  if (!href || href.startsWith('#') || /^(?:https?|mailto|tel):/i.test(href)) return null
  let path: string
  try { path = decodeURIComponent(href.replace(/^file:\/\//i, '')).replace(/\\/g, '/') }
  catch { return null }
  if (/^\/[a-z]:\//i.test(path)) path = path.slice(1)
  if (/^[a-z][a-z\d+.-]*:/i.test(path) && !/^[a-z]:\//i.test(path)) return null
  const at = path.match(/(?::(\d+)(?:[-–]\d+)?|#L(\d+)(?:-L?\d+)?)$/)
  if (at) path = path.slice(0, -at[0].length)
  return /\.[\w-]+$/.test(path) ? { path, line: Number(at?.[1] ?? at?.[2] ?? 1) } : null
}

export async function openWebLink(url: string): Promise<void> {
  await invoke('open_url', { url })
}
