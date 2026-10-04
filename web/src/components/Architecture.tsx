import { useEffect, useId, useRef, useState } from 'react'
import * as api from '@/lib/api'
import { paragraphs, useOverlay } from '@/lib/overlay'
import { ArchitectureDiagram } from './ArchitectureDiagram'

// Translate human labels before layout; identifiers and source-path lines stay literal.
const labelPattern = /"([^"\n]*)"|\|([^|"\n]+)\|/g
const labelText = (label: string) => label.split(/\\n|<br\s*\/?\s*>/i)[0]
const pathTitle = (path: string) => path.split('/').map((part) => part === 'overall-architecture' ? '전체 구조' : part.replaceAll('-', ' ')).join(' / ')

/** Requests stay bound to this repository, including while a write is pending. */
export function Architecture({ repo, theme, korean }: { repo: string; theme: 'dark' | 'light'; korean: boolean }) {
  const [data, setData] = useState<api.ArchitectureData | null>(null)
  const [selected, setSelected] = useState('overall-architecture')
  const [error, setError] = useState('')
  const [adding, setAdding] = useState(false)
  const [picture, setPicture] = useState({ key: '', svg: '', error: '' })
  const generation = useRef(0)
  const renderGeneration = useRef(0)
  const id = useId().replace(/[^a-zA-Z0-9]/g, '')
  const node = data?.nodes.find((item) => item.path === selected) ?? data?.nodes.find((item) => item.diagram) ?? data?.nodes[0]
  const fields = ['description', 'context', 'constraint', 'concern', 'todo', 'note'] as const
  const labels = Array.from((node?.diagram ?? '').matchAll(labelPattern), (match) => labelText(match[1] ?? match[2]))
  const titles = data?.nodes.map((item) => pathTitle(item.path)) ?? []
  const prose = fields.flatMap((field) => paragraphs(node?.[field] ?? ''))
  const original = [...labels, ...titles, ...prose]
  const translated = useOverlay(original, korean)
  const shown = new Map(original.map((text, index) => [text, translated[index]]))
  const diagram = (node?.diagram ?? '').replace(labelPattern, (whole, quoted: string | undefined, edge: string | undefined) => {
    const label = quoted ?? edge ?? ''
    const first = labelText(label)
    const replacement = shown.get(first) ?? first
    if (replacement === first) return whole
    const safe = replacement.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('|', '&#124;').replace(/\r?\n/g, ' ')
    return quoted !== undefined ? `"${safe}${label.slice(first.length)}"` : `|"${safe}"|`
  })
  const showProse = (text: string) => paragraphs(text).map((part) => shown.get(part) ?? part).join('\n\n')
  const pictureKey = `${theme}:${diagram}`
  const svg = picture.key === pictureKey ? picture.svg : ''
  const renderError = picture.key === pictureKey ? picture.error : ''
  useEffect(() => {
    const ticket = ++generation.current
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const next = await api.getArchitecture(repo)
        if (ticket === generation.current && next.repo === repo) {
          setData((old) => old?.revision === next.revision ? old : next)
          setError('')
        }
      } catch (err) {
        if (ticket === generation.current) setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (ticket === generation.current) timer = setTimeout(poll, 5000)
      }
    }
    void poll()
    return () => { generation.current = ticket + 1; clearTimeout(timer) }
  }, [repo])
  const add = async () => {
    const ticket = generation.current
    setAdding(true)
    setError('')
    try {
      const next = await api.addArchitecture(repo)
      if (ticket === generation.current && next.repo === repo) setData(next)
    } catch (err) {
      if (ticket === generation.current) setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (ticket === generation.current) setAdding(false)
    }
  }
  useEffect(() => {
    const ticket = ++renderGeneration.current
    if (!node?.diagram) return
    const draw = async () => {
      try {
        const { default: mermaid } = await import('mermaid')
        if (ticket !== renderGeneration.current) return
        const dark = theme === 'dark'
        mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'base', htmlLabels: false,
          flowchart: { htmlLabels: false, useMaxWidth: false },
          themeVariables: { fontFamily: 'IBM Plex Sans KR, system-ui, sans-serif', fontSize: '12px',
            primaryColor: dark ? '#232830' : '#ecebe6', primaryTextColor: dark ? '#eceae5' : '#16181d',
            primaryBorderColor: dark ? '#a8aeb6' : '#4a5058', lineColor: dark ? '#a8aeb6' : '#4a5058',
            edgeLabelBackground: dark ? '#1c1f25' : '#ffffff' } })
        const result = await mermaid.render(`architecture${id}g${ticket}`, diagram)
        if (ticket === renderGeneration.current) setPicture({ key: pictureKey, svg: result.svg, error: '' })
      } catch (err) {
        if (ticket === renderGeneration.current) setPicture({ key: pictureKey, svg: '', error: err instanceof Error ? err.message : String(err) })
      }
    }
    void draw()
    return () => { renderGeneration.current = ticket + 1 }
  }, [node?.diagram, diagram, theme, id, pictureKey])
  return <section aria-label="앱 구조" className="flex h-full min-h-0 flex-col">
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-4 py-3">
      <label className="min-w-0 flex-1 text-[12.5px]">{repo} · 구조 보기
        <select aria-label="구조 보기" value={node?.path ?? selected} onChange={(event) => setSelected(event.target.value)}
          disabled={!data?.nodes.length}
          className="mt-1 block w-full rounded-md border border-border bg-card px-2 py-1 text-[12.5px]">
          {data?.nodes.map((item) => <option key={item.path} value={item.path}>{shown.get(pathTitle(item.path)) ?? pathTitle(item.path)}</option>)}
        </select>
      </label>
      {data?.installed && <span className="font-mono text-[10.5px] text-muted-foreground">.omm · PR 머지 후 갱신</span>}
    </div>
    <div className="min-h-0 flex-1 space-y-4 overflow-auto p-4">
      {node && <nav aria-label="구조 경로" className="flex flex-wrap gap-2 text-[12.5px]">
        <button type="button" className="min-h-9 rounded-md border border-border px-2" onClick={() => setSelected('overall-architecture')}>전체 구조</button>
        {node.path.split('/').slice(0, -1).map((_, index, parts) => {
          const parent = parts.slice(0, index + 1).join('/')
          return parent !== 'overall-architecture' && data?.nodes.some((item) => item.path === parent) && <button key={parent} type="button"
            className="min-h-9 rounded-md border border-border px-2" onClick={() => setSelected(parent)}>{shown.get(pathTitle(parent)) ?? pathTitle(parent)}</button>
        })}
        <span className="self-center text-muted-foreground">{shown.get(pathTitle(node.path)) ?? pathTitle(node.path)}</span>
      </nav>}
      {error && <p role="status" className="text-[12.5px] text-destructive">{error}{data && ' · 마지막으로 읽은 구조를 표시한다'}</p>}
      {!data && !error && <p className="text-[12.5px] text-muted-foreground">구조 읽는 중…</p>}
      {data && !data.installed && <div className="space-y-3">
        <p className="text-[12.5px] text-muted-foreground">{repo}에 .omm 구조 문서가 없다. 추가하면 모델이 실행 흐름을 분석한다. 이후 PR 머지 후 갱신한다.</p>
        <button type="button" disabled={adding} onClick={() => void add()}
          className="min-h-11 rounded-md border border-border bg-secondary px-3 text-[12.5px] disabled:opacity-50">
          {adding ? '.omm 추가 중…' : '.omm 추가'}</button>
      </div>}
      {data?.installed && !data.nodes.length && <p className="text-[12.5px] text-muted-foreground">.omm에 표시할 구조 문서가 없다.</p>}
      {renderError && <div className="space-y-2">
        <p role="status" className="text-[12.5px] text-destructive">도표를 표시하지 못했다 — {renderError}</p>
        <button type="button" onClick={() => window.location.reload()}
          className="min-h-11 rounded-md border border-border px-3 text-[12.5px]">화면 새로고침</button>
      </div>}
      {node?.diagram && !svg && !renderError && <p className="text-[12.5px] text-muted-foreground">도표 그리는 중…</p>}
      {svg && node && <ArchitectureDiagram key={`${repo}:${node.path}:${pictureKey}`} svg={svg} path={node.path}
        childPaths={data?.nodes.filter((item) => item.path.startsWith(`${node.path}/`) && item.path.split('/').length === node.path.split('/').length + 1).map((item) => item.path) ?? []}
        navigate={setSelected} />}
      <p className="max-w-prose whitespace-pre-wrap break-words text-[13.5px] leading-relaxed text-muted-foreground">{showProse(node?.description ?? '')}</p>
      {([['context', '맥락'], ['constraint', '제약'], ['concern', '유의점'], ['todo', '할 일'], ['note', '메모']] as const)
        .map(([field, label]) => node?.[field] && <details key={field}>
          <summary className="cursor-pointer text-[12.5px]">{label}</summary>
          <p className="mt-2 max-w-prose whitespace-pre-wrap break-words text-[13.5px] leading-relaxed text-muted-foreground">{showProse(node[field])}</p>
        </details>)}
      {node?.diagram && <details><summary className="cursor-pointer text-[12.5px]">Mermaid 원본</summary>
        <pre className="mt-2 overflow-auto font-mono text-[12px]">{node.diagram}</pre>
      </details>}
    </div>
  </section>
}
