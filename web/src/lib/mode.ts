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

/** A run handing over to the next one (a start answered, a bootstrap finished): it opens when the oneshot
 *  window shows, and otherwise only becomes the run the window reopens, so it never pulls maintenance away. */
export const follow = (run: string) => {
  if (read().mode === 'oneshot') go('oneshot', run)
  else last = run
}

/** Keeps the items of a list answer that have the fields the screens read, so one bad item drops alone. */
export const rows = <T>(list: unknown, fields: string[]): T[] => (Array.isArray(list) ? list : [])
  .filter((x) => !!x && typeof x === 'object' && fields.every((f) => typeof (x as Record<string, unknown>)[f] === 'string')) as T[]

export function useRoute(): Route {
  const [route, setRoute] = useState(read)
  useEffect(() => {
    const on = () => setRoute(read())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}

/** A run that is gone (its spec deleted): neither the fragment nor the window's memory opens it again. */
export const forget = (run: string) => {
  const shown = /^#oneshot\/(.+)$/.exec(location.hash)
  if (shown && decodeURIComponent(shown[1]) === run) {
    last = ''
    location.hash = 'oneshot'
  } else if (last === run) last = ''
}

/** Every run past S1, read again whenever the server saves one or the feed resyncs. */
export function useSpecs(): SpecSummary[] {
  return useSpecList().specs
}

/** The runs past S1, and the repos that a later successful read no longer lists: deleted, not merely
 *  unread. A failed read keeps the list it had, so a dropped connection never looks like a deletion. */
export function useSpecList(): { specs: SpecSummary[]; removed: string[] } {
  const [list, setList] = useState<{ specs: SpecSummary[]; removed: string[] }>({ specs: [], removed: [] })
  useEffect(() => {
    // The switch sits on maintenance's rail, which a bad answer here must not take down. Only the latest
    // read counts, and only an answer that carries a list: a late older answer, or one without `specs`,
    // would otherwise read as every run it lacks having been deleted.
    let seq = 0
    const again = () => {
      const mine = ++seq
      oneshotSpecs().then((d) => {
        if (mine !== seq || !Array.isArray(d?.specs)) return
        const specs = rows<SpecSummary>(d.specs, ['repo', 'stage'])
        const now = new Set(specs.map((s) => s.repo))
        setList((was) => ({
          specs,
          removed: [...new Set([...was.removed, ...was.specs.map((s) => s.repo)])].filter((r) => !now.has(r)),
        }))
      }).catch(() => {})
    }
    again()
    // A worker moving a run on (an automatic stage reaching a gate) saves its spec, which the feed announces;
    // a feed that reconnects or resyncs announces no single spec, so the list reads again then too.
    const on = ['focus', 'spec-changed', 'server-resync']
    on.forEach((name) => window.addEventListener(name, again))
    return () => on.forEach((name) => window.removeEventListener(name, again))
  }, [])
  return list
}
