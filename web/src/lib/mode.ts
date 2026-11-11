import { useEffect, useState } from 'react'
import { oneshotSpecs, type SpecSummary } from '@/lib/oneshot'

/** Which window shows: maintenance (`#` or none) or the oneshot window (`#oneshot`, `#oneshot/<run>`).
 *  The fragment holds it, so the browser's back and a second tab of `#oneshot` both work. A run is
 *  `spec:<repo>` past S1, `run:<id>` before it, `new` for the start form. */
export type Route = { mode: 'main' | 'oneshot'; run: string }

let last = ''   // the run last shown: going back to the oneshot window opens it again

const read = (): Route => {
  const m = /^#oneshot(?:\/(.+))?$/.exec(location.hash)
  if (m?.[1]) last = decodeURIComponent(m[1])
  return m ? { mode: 'oneshot', run: m[1] ? last : '' } : { mode: 'main', run: '' }
}

export const go = (mode: Route['mode'], run = '') => {
  if (mode === 'oneshot') last = run || last
  location.hash = mode === 'main' ? '' : last ? `oneshot/${encodeURIComponent(last)}` : 'oneshot'
}

export function useRoute(): Route {
  const [route, setRoute] = useState(read)
  useEffect(() => {
    const on = () => setRoute(read())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}

/** Every run past S1, read again whenever the server saves one or the feed resyncs. */
export function useSpecs(): SpecSummary[] {
  const [specs, setSpecs] = useState<SpecSummary[]>([])
  useEffect(() => {
    // A list or nothing: the switch sits on maintenance's rail, which a bad answer here must not take down.
    const again = () => { oneshotSpecs().then((d) => setSpecs(Array.isArray(d?.specs) ? d.specs : [])).catch(() => setSpecs([])) }
    again()
    // A worker moving a run on (an automatic stage reaching a gate) saves its spec, which the feed announces;
    // a feed that reconnects or resyncs announces no single spec, so the list reads again then too.
    const on = ['focus', 'spec-changed', 'server-resync']
    on.forEach((name) => window.addEventListener(name, again))
    return () => on.forEach((name) => window.removeEventListener(name, again))
  }, [])
  return specs
}
