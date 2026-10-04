import { useEffect, useId, useRef, useState } from 'react'
import * as api from '@/lib/api'

/** The app's repository is fixed by the server, independent of project selection. */
export function Architecture({ theme }: { theme: 'dark' | 'light' }) {
  const [data, setData] = useState<api.ArchitectureData | null>(null)
  const [selected, setSelected] = useState('overall-architecture')
  const [fit, setFit] = useState(true)
  const [error, setError] = useState('')
  const [picture, setPicture] = useState({ key: '', svg: '', error: '' })
  const generation = useRef(0)
  const renderGeneration = useRef(0)
  const id = useId().replace(/[^a-zA-Z0-9]/g, '')
  const node = data?.nodes.find((item) => item.path === selected) ?? data?.nodes[0]
  const pictureKey = `${theme}:${node?.diagram ?? ''}`
  const svg = picture.key === pictureKey ? picture.svg : ''
  const renderError = picture.key === pictureKey ? picture.error : ''
  useEffect(() => {
    const ticket = ++generation.current
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const next = await api.getArchitecture()
        if (ticket === generation.current) {
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
  }, [])
  useEffect(() => {
    const ticket = ++renderGeneration.current
    if (!node?.diagram) return
    const draw = async () => {
      try {
        const { default: mermaid } = await import('mermaid')
        if (ticket !== renderGeneration.current) return
        const dark = theme === 'dark'
        mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'base',
          flowchart: { htmlLabels: false, useMaxWidth: false },
          themeVariables: { fontFamily: 'IBM Plex Sans KR, system-ui, sans-serif', fontSize: '12px',
            primaryColor: dark ? '#232830' : '#ecebe6', primaryTextColor: dark ? '#eceae5' : '#16181d',
            primaryBorderColor: dark ? '#a8aeb6' : '#4a5058', lineColor: dark ? '#a8aeb6' : '#4a5058',
            edgeLabelBackground: dark ? '#1c1f25' : '#ffffff' } })
        const result = await mermaid.render(`architecture${id}g${ticket}`, node.diagram)
        if (ticket === renderGeneration.current) setPicture({ key: pictureKey, svg: result.svg, error: '' })
      } catch (err) {
        if (ticket === renderGeneration.current) setPicture({ key: pictureKey, svg: '', error: err instanceof Error ? err.message : String(err) })
      }
    }
    void draw()
    return () => { renderGeneration.current = ticket + 1 }
  }, [node?.diagram, theme, id, pictureKey])
  return <section aria-label="앱 구조" className="flex h-full min-h-0 flex-col">
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-4 py-3">
      <label className="min-w-0 flex-1 text-[12.5px]">구조 보기
        <select aria-label="구조 보기" value={node?.path ?? selected} onChange={(event) => setSelected(event.target.value)}
          className="mt-1 block w-full rounded-md border border-border bg-card px-2 py-1 text-[12.5px]">
          {data?.nodes.map((item) => <option key={item.path} value={item.path}>{item.path.replace('overall-architecture', '전체 구조')}</option>)}
        </select>
      </label>
      <span className="font-mono text-[10.5px] text-muted-foreground">.omm · {data?.files ?? '…'}개 소스 · 자동 갱신 5초</span>
    </div>
    <div className="min-h-0 flex-1 space-y-4 overflow-auto p-4">
      {error && <p role="status" className="text-[12.5px] text-destructive">{error}{data && ' · 마지막으로 읽은 구조를 표시한다'}</p>}
      {!data && !error && <p className="text-[12.5px] text-muted-foreground">구조 읽는 중…</p>}
      {renderError && <p role="status" className="text-[12.5px] text-destructive">도표를 표시하지 못했다 — {renderError}</p>}
      {node?.diagram && !svg && !renderError && <p className="text-[12.5px] text-muted-foreground">도표 그리는 중…</p>}
      {svg && <>
        <button type="button" aria-pressed={fit} onClick={() => setFit((value) => !value)}
          className="rounded-md border border-border px-2 py-1 text-[12.5px] text-muted-foreground"
          title="도표 전체를 폭에 맞추거나 원래 크기로 펼쳐 스크롤한다">{fit ? '원래 크기로 보기' : '도표 전체 맞추기'}</button>
        <div className={`architecture-diagram overflow-auto rounded-md border border-border bg-card p-3 [&_svg]:h-auto ${fit ? '[&_svg]:max-w-full' : '[&_svg]:max-w-none'}`} tabIndex={0}
          role="img" aria-label={`${node?.path} 구조 도표`} dangerouslySetInnerHTML={{ __html: svg }} />
      </>}
      <pre className="whitespace-pre-wrap break-all font-mono text-[12px] leading-relaxed text-muted-foreground">{node?.description}</pre>
      {([['context', '맥락'], ['constraint', '제약'], ['concern', '유의점'], ['todo', '할 일'], ['note', '메모']] as const)
        .map(([field, label]) => node?.[field] && <details key={field}>
          <summary className="cursor-pointer text-[12.5px]">{label}</summary>
          <p className="mt-2 max-w-prose whitespace-pre-wrap break-words text-[13.5px] leading-relaxed text-muted-foreground">{node[field]}</p>
        </details>)}
      {node?.diagram && <details><summary className="cursor-pointer text-[12.5px]">Mermaid 원본</summary>
        <pre className="mt-2 overflow-auto font-mono text-[12px]">{node.diagram}</pre>
      </details>}
    </div>
  </section>
}
