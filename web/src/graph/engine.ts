// The map's layout and drawing. The physics came across from `graph_view.py`
// unchanged; what a node looks like now comes from the caller, so one engine
// draws a repository's documents and the hub's rules. The side panel is
// React's (`RepoMap.tsx`), not this file's.

/** One node as drawn. Colours are CSS values, tokens included (`var(--x)`). */
export type Dot = {
  id: string
  label: string
  r: number
  fill: string
  stroke: string
  /** Fill opacity: a rule that does not reach this repository fades. */
  alpha: number
  dashed: boolean
  thick: boolean
}

/** `dashed` is a measured pairing (carried in one turn), drawn by `weight`.
 *  `directed` says `a` points at `b`; the hub's links are stored with their
 *  ends sorted, so they carry no direction. The engine does not read it. */
export type Line = { a: string; b: string; dashed: boolean; weight: number; directed: boolean }

export type Handle = {
  stop: () => void
  select: (id: string | null) => void
  find: (text: string) => void
  reset: () => void
}

type Body = Dot & { x: number; y: number; vx: number; vy: number; fx: number | null; fy: number | null }

const NS = 'http://www.w3.org/2000/svg'

/** Lay out and draw into `svg`, which must hold nothing React owns. */
export function mountGraph(svg: SVGSVGElement, dots: Dot[], lines: Line[],
  onSelect: (id: string | null) => void): Handle {
  let stopped = false
  const W = svg.clientWidth || 900
  const H = svg.clientHeight || 560
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`)
  svg.textContent = ''
  const view = document.createElementNS(NS, 'g')
  svg.appendChild(view)

  const nodes: Body[] = dots.map((d, i) => {
    const a = (i / Math.max(dots.length, 1)) * Math.PI * 2
    return { ...d, x: W / 2 + Math.cos(a) * W * 0.3, y: H / 2 + Math.sin(a) * H * 0.3,
      vx: 0, vy: 0, fx: null, fy: null }
  })
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const edges = lines.filter((l) => byId.has(l.a) && byId.has(l.b) && l.a !== l.b)
  const maxW = Math.max(...edges.map((l) => l.weight), 1)
  // Repulsion falls with the crowd, or a repository of four hundred
  // documents pushes everything against the walls.
  const push = 30000 * Math.min(1, 40 / Math.max(nodes.length, 1))

  /* --- The forces, kept running and alive the way Obsidian's are ----------- */
  const sim = { alpha: 1, running: false }
  function tick() {
    for (const a of nodes) {
      for (const b of nodes) {
        if (a === b) continue
        const dx = a.x - b.x, dy = a.y - b.y
        const d2 = dx * dx + dy * dy || 0.01
        // Unbounded, repulsion throws every unlinked document against the walls.
        if (d2 > 240 * 240) continue
        const d = Math.sqrt(d2)
        const f = Math.min(push / d2, 40)
        a.vx += (dx / d) * f; a.vy += (dy / d) * f
      }
    }
    for (const l of edges) {
      const a = byId.get(l.a)!, b = byId.get(l.b)!
      const dx = b.x - a.x, dy = b.y - a.y
      const d = Math.hypot(dx, dy) || 0.01
      const rest = l.dashed ? 125 : 165
      const k = l.dashed ? 0.006 + 0.016 * (l.weight / maxW) : 0.013
      const f = (d - rest) * k
      a.vx += (dx / d) * f; a.vy += (dy / d) * f
      b.vx -= (dx / d) * f; b.vy -= (dy / d) * f
    }
    for (const n of nodes) {
      if (n.fx !== null && n.fy !== null) { n.x = n.fx; n.y = n.fy; n.vx = n.vy = 0; continue }
      n.vx += (W / 2 - n.x) * 0.0018; n.vy += (H / 2 - n.y) * 0.0026
      n.x += (n.vx *= 0.8) * sim.alpha
      n.y += (n.vy *= 0.8) * sim.alpha
      // The label is wider than the dot; clamped by the dot alone it runs off the board.
      const half = Math.max(n.r, n.label.length * 3.4) + 8
      n.x = Math.max(half, Math.min(W - half, n.x))
      n.y = Math.max(n.r + 20, Math.min(H - n.r - 24, n.y))
    }
  }
  function loop() {
    if (stopped) { sim.running = false; return }
    for (let i = 0; i < 2; i++) tick()
    sim.alpha *= 0.986
    place()
    if (sim.alpha > 0.004) requestAnimationFrame(loop)
    else sim.running = false
  }
  function kick(a = 0.55) {
    sim.alpha = Math.max(sim.alpha, a)
    if (!sim.running) { sim.running = true; requestAnimationFrame(loop) }
  }

  /* --- Drawing ------------------------------------------------------------- */
  const make = <K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number> = {}) => {
    const el = document.createElementNS(NS, tag)
    for (const k in attrs) el.setAttribute(k, String(attrs[k]))
    return el
  }
  const gEdges = make('g'), gNodes = make('g')
  view.appendChild(gEdges); view.appendChild(gNodes)

  const edgeEls = edges.map((l) => {
    const el = make('line', {
      'stroke-width': l.dashed ? 1 + 2.6 * (l.weight / maxW) : 1.2,
      'stroke-dasharray': l.dashed ? '4 4' : 'none',
    })
    el.classList.add('edge')
    gEdges.appendChild(el)
    return { el, l }
  })

  let dragged = false
  let selected: string | null = null
  let hovered: string | null = null
  let query = ''

  const nodeEls = nodes.map((n) => {
    const g = make('g', { class: 'node', tabindex: 0, role: 'button', 'aria-label': n.label })
    const circle = make('circle', { r: n.r, 'stroke-width': n.thick ? 2.5 : 1.5,
      'stroke-dasharray': n.dashed ? '3 3' : 'none', 'fill-opacity': n.alpha })
    // Through `style`: `var()` in a presentation attribute is not read.
    circle.style.fill = n.fill
    circle.style.stroke = n.stroke
    const label = make('text', { 'text-anchor': 'middle' })
    label.textContent = n.label
    g.appendChild(circle); g.appendChild(label)
    gNodes.appendChild(g)
    g.addEventListener('click', (e) => { if (!dragged) onSelect(n.id); e.stopPropagation() })
    g.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(n.id) }
    })
    g.addEventListener('pointerenter', () => { hovered = n.id; paint() })
    g.addEventListener('pointerleave', () => { hovered = null; paint() })
    g.addEventListener('pointerdown', (e) => startDrag(e, n))
    return { g, circle, label, n }
  })

  function place() {
    for (const { circle, label, n } of nodeEls) {
      circle.setAttribute('cx', String(n.x)); circle.setAttribute('cy', String(n.y))
      label.setAttribute('x', String(n.x)); label.setAttribute('y', String(n.y + n.r + 13))
    }
    for (const { el, l } of edgeEls) {
      const a = byId.get(l.a)!, b = byId.get(l.b)!
      el.setAttribute('x1', String(a.x)); el.setAttribute('y1', String(a.y))
      el.setAttribute('x2', String(b.x)); el.setAttribute('y2', String(b.y))
    }
  }

  const near = new Map<string, Set<string>>(nodes.map((n) => [n.id, new Set([n.id])]))
  for (const { a, b } of edges) { near.get(a)!.add(b); near.get(b)!.add(a) }
  const hidden = (n: Body) => !!query && !(n.id + ' ' + n.label).toLowerCase().includes(query)

  function paint() {
    const focus = hovered || selected
    const keep = focus ? near.get(focus) : null
    for (const { g, n } of nodeEls) {
      const gone = hidden(n)
      g.classList.toggle('off', gone)
      g.classList.toggle('sel', n.id === selected)
      g.classList.toggle('dim', !gone && !!keep && !keep.has(n.id))
    }
    for (const { el, l } of edgeEls) {
      const gone = hidden(byId.get(l.a)!) || hidden(byId.get(l.b)!)
      el.classList.toggle('off', gone)
      el.classList.toggle('dim', !gone && !!keep && !(keep.has(l.a) && keep.has(l.b)))
    }
  }
  // Nodes drift under a still cursor and an `enter` arrives without its
  // `leave`; leaving the whole board clears the highlight unconditionally.
  svg.onpointerleave = () => { hovered = null; paint() }

  /* --- Drag, zoom, pan ----------------------------------------------------- */
  let tx = 0, ty = 0, k = 1
  const applyView = () => view.setAttribute('transform', `translate(${tx} ${ty}) scale(${k})`)
  function pointer(e: PointerEvent | WheelEvent) {
    const r = svg.getBoundingClientRect()
    return [((e.clientX - r.left) / r.width * W - tx) / k, ((e.clientY - r.top) / r.height * H - ty) / k]
  }
  function startDrag(e: PointerEvent, n: Body) {
    e.stopPropagation(); dragged = false
    const move = (ev: PointerEvent) => {
      dragged = true
      const [x, y] = pointer(ev)
      n.fx = x; n.fy = y; kick(0.35)
    }
    const up = () => {
      n.fx = n.fy = null
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setTimeout(() => { dragged = false }, 0)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }
  svg.onpointerdown = (e) => {
    if ((e.target as Element).closest('.node')) return
    svg.classList.add('grabbing')
    const sx = e.clientX, sy = e.clientY, ox = tx, oy = ty
    const r = svg.getBoundingClientRect()
    let moved = false
    const move = (ev: PointerEvent) => {
      moved = true
      tx = ox + (ev.clientX - sx) / r.width * W
      ty = oy + (ev.clientY - sy) / r.height * H
      applyView()
    }
    const up = () => {
      svg.classList.remove('grabbing')
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      // A click on the empty board closes the panel; a pan does not.
      if (!moved) onSelect(null)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }
  svg.onwheel = (e) => {
    e.preventDefault()
    const [px, py] = pointer(e)
    const next = Math.max(0.3, Math.min(3.2, k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)))
    tx += px * (k - next); ty += py * (k - next)
    k = next; applyView()
  }

  place(); paint(); kick(1)
  return {
    stop: () => { stopped = true },
    select: (id) => { selected = id; paint() },
    find: (text) => { query = text.trim().toLowerCase(); paint() },
    reset: () => {
      tx = ty = 0; k = 1; applyView()
      nodes.forEach((n) => { n.fx = n.fy = null })
      kick(1)
    },
  }
}
