import { useEffect, useState } from 'react'
import * as api from '@/lib/api'

/** What an approval card shows, read when it appears, when `again` changes and on `reload`.
 *  A read leaves `fault` alone: an approval's refusal stays beside what is read after it. */
export function useLoaded<T>(url: string, repo: string, what: string, again?: unknown) {
  const [data, setData] = useState<T | null>(null)
  const [fault, setFault] = useState('')
  const [reads, setReads] = useState(0)
  useEffect(() => {
    let live = true
    api.get(url, repo).then((r) => api.json<T>(r, what)).then((d) => { if (live) setData(d) },
      (err) => { if (live) setFault(String(err instanceof Error ? err.message : err)) })
    return () => { live = false }
  }, [url, repo, what, again, reads])
  return [data, fault, setFault, () => setReads((n) => n + 1)] as const
}
