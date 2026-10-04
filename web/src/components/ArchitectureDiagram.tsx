import { useEffect, useMemo, useRef, useState } from 'react'

type View = { scale: number; x: number; y: number }
const fitted: View = { scale: 1, x: 0, y: 0 }

/** The fitted drawing is the minimum zoom; navigation only targets saved children. */
export function ArchitectureDiagram({ svg, path, childPaths, navigate }: {
  svg: string; path: string; childPaths: string[]; navigate: (path: string) => void
}) {
  const canvas = useRef<HTMLDivElement>(null)
  const [view, setView] = useState(fitted)
  const drag = useRef<{ id: number; x: number; y: number } | null>(null)
  const clamp = (next: View): View => {
    const box = canvas.current?.getBoundingClientRect()
    if (!box || next.scale <= 1) return fitted
    return { scale: next.scale,
      x: Math.max(box.width * (1 - next.scale), Math.min(0, next.x)),
      y: Math.max(box.height * (1 - next.scale), Math.min(0, next.y)) }
  }
  const zoom = (factor: number, x?: number, y?: number) => {
    const box = canvas.current?.getBoundingClientRect()
    if (!box) return
    const px = x ?? box.width / 2, py = y ?? box.height / 2
    setView((old) => {
      const scale = Math.max(1, Math.min(8, old.scale * factor))
      return clamp({ scale, x: px - (px - old.x) * scale / old.scale,
        y: py - (py - old.y) * scale / old.scale })
    })
  }
  useEffect(() => {
    const element = canvas.current
    if (!element) return
    const wheel = (event: WheelEvent) => {
      if (!event.deltaY) return
      event.preventDefault()
      const box = element.getBoundingClientRect()
      zoom(Math.exp(-Math.max(-100, Math.min(100, event.deltaY)) * .002),
        event.clientX - box.left, event.clientY - box.top)
    }
    element.addEventListener('wheel', wheel, { passive: false })
    const resize = new ResizeObserver(() => setView((old) => clamp(old)))
    resize.observe(element)
    return () => { element.removeEventListener('wheel', wheel); resize.disconnect() }
  }, [])
  const markup = useMemo(() => {
    // Mermaid may include HTML labels inside foreignObject; parse as browser markup.
    const template = document.createElement('template')
    template.innerHTML = svg
    const drawing = template.content.querySelector('svg')
    if (!drawing) return svg
    drawing.setAttribute('width', '100%')
    drawing.setAttribute('height', '100%')
    drawing.style.maxWidth = 'none'
    drawing.style.height = '100%'
    drawing.setAttribute('preserveAspectRatio', 'xMidYMid meet')
    drawing.querySelectorAll<SVGGElement>('g.node, g.cluster').forEach((element) => {
      const id = element.id.match(/(?:^|-)flowchart-(.+)-\d+$/)?.[1] ?? element.id
      const target = childPaths.find((child) => child === `${path}/${id}`)
      if (!target) return
      element.dataset.target = target
      element.setAttribute('role', 'button')
      element.setAttribute('tabindex', '0')
      element.setAttribute('aria-label', `${element.textContent?.trim()} · 세부 구조 보기`)
      element.style.cursor = 'pointer'
    })
    return drawing.outerHTML
  }, [svg, path, childPaths])
  const open = (target: EventTarget | null) => {
    const child = target instanceof Element ? target.closest<SVGGElement>('[data-target]')?.dataset.target : ''
    if (child) navigate(child)
  }
  return <div className="space-y-2">
    <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
      <button type="button" onClick={() => setView(fitted)} className="min-h-9 rounded-md border border-border px-2">원래 크기로 보기</button>
      <button type="button" aria-label="구조 확대" onClick={() => zoom(1.25)} disabled={view.scale === 8}
        className="min-h-9 min-w-9 rounded-md border border-border disabled:opacity-50">+</button>
      <button type="button" aria-label="구조 축소" onClick={() => zoom(.8)} disabled={view.scale === 1}
        className="min-h-9 min-w-9 rounded-md border border-border disabled:opacity-50">−</button>
      <span className="text-muted-foreground">{Math.round(view.scale * 100)}% · 휠로 확대 · 우클릭 드래그로 이동</span>
    </div>
    <div ref={canvas} className="architecture-diagram relative h-[min(55vh,420px)] min-h-64 overflow-hidden rounded-md border border-border bg-card select-none"
      tabIndex={0} role="group" aria-label={`${path} 구조 도표`} data-scale={view.scale} data-x={view.x} data-y={view.y}
      onContextMenu={(event) => event.preventDefault()}
      onClick={(event) => open(event.target)}
      onKeyDown={(event) => {
        if ((event.key === 'Enter' || event.key === ' ') && event.target !== event.currentTarget) {
          event.preventDefault(); open(event.target)
        } else if (event.key === '+' || event.key === '=') { event.preventDefault(); zoom(1.25) }
        else if (event.key === '-') { event.preventDefault(); zoom(.8) }
        else if (event.key === 'Home') { event.preventDefault(); setView(fitted) }
        else if (event.key.startsWith('Arrow')) {
          event.preventDefault()
          setView((old) => clamp({ ...old, x: old.x + (event.key === 'ArrowLeft' ? 30 : event.key === 'ArrowRight' ? -30 : 0),
            y: old.y + (event.key === 'ArrowUp' ? 30 : event.key === 'ArrowDown' ? -30 : 0) }))
        }
      }}
      onPointerDown={(event) => {
        if (event.button !== 2 || view.scale === 1) return
        event.preventDefault()
        event.currentTarget.setPointerCapture(event.pointerId)
        drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY }
      }}
      onPointerMove={(event) => {
        const start = drag.current
        if (!start || start.id !== event.pointerId) return
        const dx = event.clientX - start.x, dy = event.clientY - start.y
        drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY }
        setView((old) => clamp({ ...old, x: old.x + dx, y: old.y + dy }))
      }}
      onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }}
      onLostPointerCapture={() => { drag.current = null }}>
      <div className="absolute inset-0 p-3" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`, transformOrigin: '0 0' }}
        dangerouslySetInnerHTML={{ __html: markup }} />
    </div>
  </div>
}
