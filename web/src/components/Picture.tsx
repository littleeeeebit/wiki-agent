import { useContext, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Zoom } from '@/components/Modal'
import { InLink, PictureRoot } from '@/lib/picture'

/** A Markdown image in an agent's answer or question, such as the before and
 *  after of a design it asks to approve. A local path is drawn through
 *  `/api/image`; a web address, or a path with no checkout to read it from,
 *  stays its alt text, so an answer never fetches from outside. A click
 *  enlarges it in place rather than opening a browser window — except inside
 *  a link, where it is the link's content and opens the link. */
export function Picture({ src, alt, fallback }: { src?: string; alt?: string; fallback?: ReactNode }) {
  const repo = useContext(PictureRoot)
  const linked = useContext(InLink)
  const [broken, setBroken] = useState(false)
  const [zoomed, setZoomed] = useState(false)
  const local = !!src && !/^(https?|data|blob):/i.test(src) && !src.startsWith('//')
  // With no alt, the file's name: a picture that cannot be drawn never leaves its link with nothing to show.
  if (!repo || !local || broken) return fallback ?? <span>{alt || src?.split(/[\\/]/).pop() || '그림'}</span>
  const url = `/api/image?${new URLSearchParams({ repo, path: src })}`
  const image = <img src={url} alt={alt ?? ''} loading="lazy" onError={() => setBroken(true)}
    className="max-h-[480px] w-full rounded-md border border-border bg-background object-contain" />
  return (
    // Half the width less the space Markdown leaves between two images, so a before and an after compare side by
    // side; a narrow pane wraps them.
    <span className="my-2 inline-block w-[calc(50%-0.5rem)] min-w-[200px] max-w-full align-top">
      {linked ? image : <button type="button" onClick={(e) => { e.stopPropagation(); setZoomed(true) }} title="크게 본다"
        aria-label={alt ? `${alt} 크게 보기` : '크게 보기'} className="block w-full cursor-zoom-in rounded-md">{image}</button>}
      {/* Seen, not read: the image's alt, or the zoom button's label, already names it. */}
      {alt && <span aria-hidden="true" className="mt-1 block text-[12px] text-muted-foreground">{alt}</span>}
      {/* On the body, not in the paragraph: a dialog is flow content, and an enclosing link must not own its clicks. */}
      {zoomed && createPortal(<Zoom src={url} alt={alt} close={() => setZoomed(false)} />, document.body)}
    </span>
  )
}
