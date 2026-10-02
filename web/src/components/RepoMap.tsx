// The map of the rail's project. It follows the project: that repository's
// own documents are the default layer, the hub's rules as they stand there
// are the other. For the hub itself the rule layer is `graph.json` unchanged.
// The old landing page's title and number cards are gone; one line of
// measures stays.

import { useEffect, useMemo, useRef, useState } from 'react'
import { Answer } from '@/components/Answer'
import { Btn } from '@/components/Modal'
import { mountGraph } from '@/graph/engine'
import type { Dot, Handle, Line } from '@/graph/engine'
import '@/graph/map.css'
import * as api from '@/lib/api'
import type { DocNode, MapData, RunSummary } from '@/lib/api'
import { useParagraphOverlay } from '@/lib/overlay'
import { LANE, ORIGIN, OUTCOME, SUPPORT } from '@/lib/run'
import { cn } from '@/lib/utils'

type Layer = 'repo' | 'hub' | 'both' | 'run'
const LAYERS: { id: Layer; label: string }[] = [
  { id: 'repo', label: '이 저장소 문서' }, { id: 'hub', label: '허브 규칙' }, { id: 'both', label: '둘 다' },
]

const KIND: Record<DocNode['kind'], string> = { doc: '문서', page: '지식 페이지', module: '모듈 페이지', decision: '결정 기록' }
// What a rule's `status` says about this repository.
const REACH: Record<string, string> = { on: '붙음', partial: '일부만', none: '안 붙음', prose: '산문뿐', foreign: '다른 저장소' }

/** A node, with where its file is and what the panel says of it. */
type Entry = { dot: Dot; repo: string; path: string; line?: number; title: string; facts: [string, string][] }

/** One relation a run walked, as recorded: `a` → `b` in the graph's own direction. */
type Walked = { id: string; a: string; b: string; kind: string; directed: boolean; origin: string
  confidence: number | null; spans: { locator?: { path?: string; start_line?: number } }[] }

const ROLE = { seed: '시작점 — 검색이 찾은 곳', bridge: '다리 — 그래프로 새로 닿은 곳', passed: '지나감' }

/** The run's own layer: only the nodes and relations its recorded paths
 *  went through, never the graph around them. */
function walkedEntries(run: RunSummary, repo: string): { list: Entry[]; lines: Line[]; walked: Walked[]
  label: (id: string) => string } {
  const g = run.graph!
  const evidence = new Map((run.evidence ?? []).map((e) => [e.chunk_id, e]))
  const label = (id: string) => evidence.get(id)?.cite ?? g.nodes[id]?.label ?? id.slice(0, 10)
  const walked = new Map<string, Walked>()
  const kinds = new Map<string, string>()
  for (const p of g.paths) {
    p.steps.forEach((s, i) => {
      kinds.set(s.node, s.node_kind)
      if (!s.edge_id || i === 0) return
      const prev = p.steps[i - 1].node
      const detail = g.edges[s.edge_id]
      walked.set(s.edge_id, { id: s.edge_id, a: s.reverse ? s.node : prev, b: s.reverse ? prev : s.node,
        kind: detail?.kind ?? s.kind ?? '?', directed: detail?.directed ?? true, origin: detail?.origin ?? s.origin ?? '?',
        confidence: detail?.confidence ?? s.confidence ?? null, spans: detail?.spans ?? [] })
    })
  }
  const list: Entry[] = [...kinds].map(([id, kind]) => {
    const e = evidence.get(id)
    const role = g.seeds.includes(id) ? 'seed' : g.bridges.includes(id) ? 'bridge' : 'passed'
    const cited = e?.support === 'supported'
    return {
      dot: { id, label: label(id), r: role === 'seed' ? 10 : 7, fill: role === 'passed' ? 'var(--map-doc)' : 'var(--map-page)',
        stroke: cited ? 'var(--foreground)' : role === 'passed' ? 'var(--map-doc)' : 'var(--map-page)',
        alpha: role === 'passed' ? 0.45 : 0.9, dashed: role === 'bridge', thick: cited },
      repo, path: e?.locator.path ?? '', line: Number(e?.locator.start_line ?? 1), title: label(id),
      facts: [
        ['역할', ROLE[role]],
        ['종류', g.nodes[id]?.kind ?? kind],
        ...(e ? [['근거', `${SUPPORT[e.support].mark} ${SUPPORT[e.support].label}`] as [string, string],
          ['찾은 길', LANE[e.lane ?? ''] ?? e.lane ?? '—'] as [string, string]] : []),
      ],
    }
  })
  const lines = [...walked.values()].map((w) => ({ a: w.a, b: w.b, dashed: false, weight: 0, directed: w.directed }))
  return { list, lines, walked: [...walked.values()], label }
}

// A decision record's file name leads with its date and number; the label drops them.
const stem = (path: string) => path.split('/').pop()!.replace(/\.md$/, '').replace(/^\d{4}-\d\d-\d\d-\d+-/, '')

function entries(data: MapData, layer: Layer): { list: Entry[]; lines: Line[] } {
  const list: Entry[] = []
  const lines: Line[] = []
  if (layer !== 'hub') {
    const docs = data.layers.repo.nodes
    const most = Math.max(...docs.map((d) => d.chars), 1)
    for (const d of docs) {
      const tone = d.kind === 'page' && d.injected ? 'page' : d.kind === 'page' ? 'doc' : d.kind
      list.push({
        dot: { id: d.id, label: stem(d.id), r: d.kind === 'decision' ? 4 : 5 + 10 * Math.sqrt(d.chars / most),
          fill: `var(--map-${tone})`, stroke: `var(--map-${tone})`, alpha: d.injected ? 0.9 : 0.55,
          dashed: d.kind === 'module', thick: d.severity === 'landmine' },
        repo: data.repo, path: d.id, title: d.title,
        facts: [
          ['종류', KIND[d.kind]],
          ...(d.severity ? [['등급', d.severity] as [string, string]] : []),
          ...(d.triggers?.length ? [['트리거', d.triggers.join(' · ')] as [string, string]] : []),
          ['주입', d.kind === 'module' ? '안 한다 — 모듈 페이지는 읽기만' : d.injected ? `${d.chars.toLocaleString()}자` : '안 실림'],
        ],
      })
    }
    lines.push(...data.layers.repo.edges.map((e) => ({ a: e.a, b: e.b, dashed: false, weight: 0, directed: true })))
  }
  if (layer !== 'repo') {
    const rules = data.layers.hub.nodes
    const most = Math.max(...rules.map((n) => (n.injected ? n.chars : 0)), 1)
    const key = data.hub ? 'all' : data.repo
    for (const n of rules) {
      const reach = data.hub ? 'on' : n.status[data.repo]
      const away = reach === 'none' || reach === 'foreign'
      const hubPage = n.scope === 'operator' || n.scope === 'craft'
      list.push({
        dot: { id: n.id, label: n.label, r: n.injected ? 10 + 16 * Math.sqrt(n.chars / most) : 7,
          fill: `var(--map-l${n.layer})`, stroke: n.severity === 'landmine' ? 'var(--foreground)' : `var(--map-l${n.layer})`,
          alpha: away ? 0.12 : n.injected ? 0.9 : 0.42, dashed: away || n.severity === 'preference',
          thick: n.severity === 'landmine' },
        repo: hubPage ? data.wiki : n.scope, path: hubPage ? `${n.id}.md` : `.wiki/${n.label}.md`,
        title: n.headline,
        facts: [
          ['범위', n.scope], ['등급', n.severity],
          ['층', `${n.layer} ${data.layers.hub.ladder.find((l) => l.n === n.layer)?.title ?? ''}`],
          ['트리거', n.triggers.length ? n.triggers.join(' · ') : '없음'],
          ['주입', n.injected ? `${n.chars.toLocaleString()}자` : '안 실림'],
          ...(data.hub ? [] : [['이 저장소', REACH[reach] ?? reach] as [string, string]]),
        ],
      })
    }
    for (const e of data.layers.hub.edges as { a: string; b: string; kind: string; by?: Record<string, number> }[]) {
      const weight = e.kind === 'co' ? e.by?.[key] ?? 0 : 0
      if (e.kind === 'link' || weight > 0) lines.push({ a: e.a, b: e.b, dashed: e.kind === 'co', weight, directed: false })
    }
  }
  return { list, lines }
}

/** `on` is the app's translation switch: the panel's preview is overlaid
 *  only while it is on. `onAsk` puts the path in the wiki focus's box. */
export function RepoMap({ repo, on, run, onRunClose, onAsk }: {
  repo: string; on: boolean; run: RunSummary | null; onRunClose: () => void; onAsk: (path: string) => void
}) {
  const [data, setData] = useState<MapData | null>(null)
  const [fault, setFault] = useState('')
  const [layer, setLayer] = useState<Layer>('repo')
  const [picked, setPicked] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const board = useRef<SVGSVGElement>(null)
  const handle = useRef<Handle | null>(null)

  useEffect(() => {
    if (!repo) return
    let stale = false
    setData(null)
    setFault('')
    setPicked(null)
    api.getGraph(repo)
      .then((d) => {
        if (stale) return
        setData(d)
        setLayer((now) => (now === 'run' ? now : d.hub ? 'hub' : 'repo'))
      })
      .catch((err) => !stale && setFault(String(err instanceof Error ? err.message : err)))
    return () => {
      stale = true
    }
  }, [repo])

  // A run handed over opens on its own layer; one taken away leaves it.
  const hub = data?.hub ?? false
  useEffect(() => {
    setPicked(null)
    setLayer((now) => (run ? 'run' : now === 'run' ? (hub ? 'hub' : 'repo') : now))
  }, [run, hub])
  const path = useMemo(() => (run?.graph ? walkedEntries(run, repo) : null), [run, repo])
  const drawn = useMemo(() => (layer === 'run' ? path : data ? entries(data, layer) : null), [data, layer, path])

  // The drawing side owns the board outright, and is stopped on the way out;
  // unstopped, its layout loop outlives the screen.
  useEffect(() => {
    if (!drawn || !board.current) return
    const h = mountGraph(board.current, drawn.list.map((e) => e.dot), drawn.lines, setPicked)
    handle.current = h
    return () => {
      h.stop()
      handle.current = null
    }
  }, [drawn])
  useEffect(() => handle.current?.select(picked), [picked, drawn])
  useEffect(() => handle.current?.find(query), [query, drawn])

  const entry = drawn?.list.find((e) => e.dot.id === picked) ?? null
  const title = (id: string) => drawn?.list.find((e) => e.dot.id === id)?.dot.label ?? id

  return (
    <section aria-label="지도" className="flex h-full min-h-0 flex-col">
      <div className="map-toolbar flex h-11 shrink-0 items-center gap-3 border-b border-border bg-card px-5">
        <div role="radiogroup" aria-label="층" className="flex h-7 shrink-0 rounded-md border border-border p-0.5">
          {[...LAYERS, ...(path ? [{ id: 'run' as Layer, label: '이번 질문 경로' }] : [])].map((l) => (
            <button key={l.id} type="button" role="radio" aria-checked={layer === l.id} onClick={() => setLayer(l.id)}
              className={cn('rounded-[4px] px-2 text-[12.5px]',
                layer === l.id ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:text-foreground')}>
              {l.label}
            </button>
          ))}
        </div>
        <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="찾기" aria-label="노드 찾기"
          className="h-7 w-28 min-w-0 rounded-md border border-input bg-background px-2 text-[12.5px]" />
        {drawn && <Btn tone="ghost" onClick={() => handle.current?.reset()}>되돌리기</Btn>}
        {data && layer !== 'run' && (
          <p className="ml-auto flex shrink-0 gap-3 font-mono text-[10.5px] text-faint">
            <span title="이 저장소의 문서·지식 페이지·모듈·결정 기록">페이지 {data.metrics.pages}</span>
            <span title="아무 문서도 가리키지 않는 문서">고아 {data.metrics.orphans}</span>
            <span title="repo_lint 의 발견">경고 {data.metrics.lint}</span>
          </p>
        )}
      </div>

      <div className="relative min-h-0 flex-1">
        {fault && <p role="alert" className="p-5 text-[12.5px] text-destructive">지도를 못 읽었다 — {fault}</p>}
        {!drawn && !fault && <p className="p-5 text-[13.5px] text-faint">지도를 그리는 중…</p>}
        <svg ref={board} className={cn('map-board size-full', !drawn && 'hidden')} role="img"
          aria-label={layer === 'run' ? `${repo} — 이번 질문이 지나간 경로` : `${repo} 지도`} />
        {layer === 'run' && path && run && !entry && (
          <RunPanel run={run} label={path.label} onPick={setPicked} onClose={onRunClose} />
        )}
        {entry && layer === 'run' && path && (
          <Panel key={entry.dot.id} entry={entry} on={on} onAsk={onAsk} onPick={setPicked} title={path.label}
            into={[]} out={[]} both={[]}
            walked={path.walked.filter((w) => w.a === entry.dot.id || w.b === entry.dot.id)} />
        )}
        {entry && drawn && layer !== 'run' && (() => {
          // Links only: a dashed line is a measured pairing, not a reference.
          const links = drawn.lines.filter((l) => !l.dashed)
          const id = entry.dot.id
          return (
            <Panel key={id} entry={entry} on={on} onAsk={onAsk} onPick={setPicked} title={title}
              into={links.filter((l) => l.directed && l.b === id).map((l) => l.a)}
              out={links.filter((l) => l.directed && l.a === id).map((l) => l.b)}
              both={links.filter((l) => !l.directed && (l.a === id || l.b === id)).map((l) => (l.a === id ? l.b : l.a))} />
          )
        })()}
      </div>
    </section>
  )
}

/** The picked node: its facts, what points at it and what it points at, the
 *  top of its body, and the way to ask about it. */
function Panel({ entry, on, onAsk, onPick, title, into, out, both, walked }: {
  entry: Entry; on: boolean; onAsk: (path: string) => void; onPick: (id: string | null) => void
  title: (id: string) => string; into: string[]; out: string[]; both: string[]; walked?: Walked[]
}) {
  const [body, setBody] = useState<string | null>(null)
  const [fault, setFault] = useState('')
  useEffect(() => {
    if (!entry.path) {
      setFault('파일에 묶인 노드가 아니다')
      return
    }
    api.peek(entry.repo, entry.path, entry.line ?? 1)
      .then((p) => setBody(p.lines.join('\n').replace(/^---\n[\s\S]*?\n---\n/, '')))
      .catch((err) => setFault(String(err instanceof Error ? err.message : err)))
  }, [entry.repo, entry.path, entry.line])
  const shown = useParagraphOverlay(body ?? '', on && body !== null)

  const links = (label: string, ids: string[]) => ids.length > 0 && (
    <div>
      <div className="mb-1 font-heading text-[11px] font-semibold text-faint">{label} {ids.length}</div>
      <ul className="space-y-0.5">
        {ids.map((id) => (
          <li key={id}>
            <button type="button" onClick={() => onPick(id)}
              className="max-w-full truncate font-mono text-[12px] text-primary hover:underline">{title(id)}</button>
          </li>
        ))}
      </ul>
    </div>
  )

  return (
    <aside aria-label="고른 문서" className="absolute inset-y-0 right-0 flex w-80 max-w-full flex-col border-l border-border bg-card">
      <div className="flex shrink-0 items-start gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <h3 className="font-heading text-[14px] font-semibold leading-snug">{entry.title}</h3>
          <p className="mt-0.5 truncate font-mono text-[10.5px] text-faint">{entry.path}</p>
        </div>
        <Btn tone="ghost" onClick={() => onPick(null)} aria-label="닫기">닫기</Btn>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-3 text-[12.5px]">
        <dl className="grid grid-cols-[4.5rem_1fr] gap-x-2 gap-y-1">
          {entry.facts.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-faint">{k}</dt>
              <dd className="min-w-0 break-words">{v}</dd>
            </div>
          ))}
        </dl>
        {links('들어오는 링크', into)}
        {links('나가는 링크', out)}
        {links('이어진 규칙', both)}
        {walked && walked.length > 0 && (
          <div>
            <div className="mb-1 font-heading text-[11px] font-semibold text-faint">지나간 관계 {walked.length}</div>
            <ul className="space-y-2">
              {walked.map((w) => {
                const other = w.a === entry.dot.id ? w.b : w.a
                const arrow = !w.directed ? '—' : w.a === entry.dot.id ? '→' : '←'
                return (
                  <li key={w.id}>
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[12px]">{w.kind}</span>
                      <span role="img" aria-label={arrow === '—' ? '방향 없음' : arrow === '→' ? '나감' : '들어옴'}>{arrow}</span>
                      <button type="button" onClick={() => onPick(other)}
                        className="min-w-0 truncate font-mono text-[12px] text-primary hover:underline">{title(other)}</button>
                    </div>
                    <div className="font-mono text-[10.5px] text-faint">
                      {ORIGIN[w.origin] ?? w.origin}{w.confidence != null && ` · 확신 ${w.confidence.toFixed(2)}`}
                      {w.spans.length > 0 && ` · 출처 ${w.spans.map((x) => `${x.locator?.path ?? '?'}:${x.locator?.start_line ?? '?'}`).join(', ')}`}
                    </div>
                  </li>
                )
              })}
            </ul>
          </div>
        )}
        <div>
          <div className="mb-1 font-heading text-[11px] font-semibold text-faint">본문 앞부분</div>
          {fault ? <p className="text-destructive">{fault}</p>
            : body === null ? <p className="text-faint">읽는 중…</p>
              : <Answer text={shown} korean={on} remote="" onPeek={() => {}} />}
        </div>
      </div>
      <div className="shrink-0 border-t border-border px-4 py-3">
        <Btn tone="primary" className="w-full justify-center" disabled={!entry.path} onClick={() => onAsk(entry.path)}>이 문서에 대해 묻기</Btn>
      </div>
    </aside>
  )
}

/** The run's paths as a list, in the order they were walked: the same
 *  record the board draws, readable and reachable without a pointer. */
function RunPanel({ run, label, onPick, onClose }: {
  run: RunSummary; label: (id: string) => string; onPick: (id: string) => void; onClose: () => void
}) {
  const g = run.graph!
  const cited = (run.evidence ?? []).filter((e) => e.support === 'supported').length
  return (
    <aside aria-label="이번 질문 경로" className="absolute inset-y-0 right-0 flex w-80 max-w-full flex-col border-l border-border bg-card">
      <div className="flex shrink-0 items-start gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <h3 className="font-heading text-[14px] font-semibold leading-snug">이번 질문 경로</h3>
          <p className="mt-0.5 font-mono text-[10.5px] text-faint">
            {OUTCOME[run.outcome] ?? run.outcome} · 시작점 {g.seeds.length} · 다리 {g.bridges.length} · 인용 {cited}
            {g.discarded ? ` · 버림 ${g.discarded}` : ''}
          </p>
        </div>
        <Btn tone="ghost" onClick={onClose}>경로 닫기</Btn>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-3 text-[12.5px]">
        <p className="text-faint">
          큰 점 — 시작점 · 점선 — 다리 · 진한 테두리 — 답이 인용한 근거. 기록된 순회 그대로이고, 둘레의 그래프는 그리지 않는다.
          {g.detail === 'unavailable' && ' 그래프를 읽지 못해 관계의 출처는 빠졌다.'}
        </p>
        <ol className="space-y-3">
          {g.paths.map((p, i) => (
            <li key={i}>
              <div className="mb-1 font-heading text-[11px] font-semibold text-faint">경로 {i + 1} · {p.hops}홉 · {p.status}</div>
              <ol className="space-y-0.5">
                {p.steps.map((x, j) => (
                  <li key={j} className="flex items-center gap-1.5">
                    {j > 0 && <span className="font-mono text-[10.5px] text-faint">{x.reverse ? '←' : '→'} {x.kind}</span>}
                    <button type="button" onClick={() => onPick(x.node)}
                      className="min-w-0 truncate font-mono text-[12px] text-primary hover:underline">{label(x.node)}</button>
                  </li>
                ))}
              </ol>
            </li>
          ))}
        </ol>
      </div>
    </aside>
  )
}
