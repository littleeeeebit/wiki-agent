import { useContext, useState } from 'react'
import { PictureRoot } from '@/lib/picture'

/** A Markdown image in an agent's answer or question, such as the before and
 *  after of a design it asks to approve. A local path is drawn through
 *  `/api/image`; a web address, or a path with no checkout to read it from,
 *  stays its alt text, so an answer never fetches from outside. */
export function Picture({ src, alt }: { src?: string; alt?: string }) {
  const repo = useContext(PictureRoot)
  const [broken, setBroken] = useState(false)
  const local = !!src && !/^(https?|data|blob):/i.test(src) && !src.startsWith('//')
  if (!repo || !local || broken) return <span>{alt}</span>
  const url = `/api/image?${new URLSearchParams({ repo, path: src })}`
  return (
    // Half the width less the space Markdown leaves between two images, so a before and an after compare side by
    // side; a narrow pane wraps them.
    <a href={url} target="_blank" rel="noreferrer" className="my-2 inline-block w-[calc(50%-0.5rem)] min-w-[200px] max-w-full align-top">
      <img src={url} alt={alt ?? ''} loading="lazy" onError={() => setBroken(true)}
        className="max-h-[480px] w-full rounded-md border border-border bg-background object-contain" />
      {alt && <span className="mt-1 block text-[12px] text-muted-foreground">{alt}</span>}
    </a>
  )
}
