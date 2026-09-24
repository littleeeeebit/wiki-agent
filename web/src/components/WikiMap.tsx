// The wiki map. What `wiki.html` used to do, and this is where it lives now.
//
// The prose did not come across. The old page carried an explanation of the
// ladder, a comparison with Obsidian and a how-to, and all three were copies
// of README, SCHEMA and ENFORCEMENT — one of them, the how-to, had already
// gone stale and described lint as looking at six places. What is left here
// is the measured values and nothing else.

import { useEffect, useRef, useState } from 'react'

import { mountGraph } from '@/graph/force'
import '@/graph/graph.css'
import { getGraph, renderAll, type GraphData } from '@/lib/api'

/** The two fields on a node that may be rendered. Nothing else is touched:
 *  `id` is the slug and the graph's key, and links and `graph.json` find each
 *  other by that exact string. */
type Wordy = { headline?: string; rule?: string }

/** `on` is the app's translation switch. Off, the map asks for nothing, whatever
 *  its own button says — the switch promises no request leaves. */
export function WikiMap({ on }: { on: boolean }) {
  const [data, setData] = useState<GraphData | null>(null)
  const [error, setError] = useState('')
  const [asked, setKorean] = useState(false)
  const korean = on && asked
  const [said, setSaid] = useState<GraphData | null>(null)
  const [fault, setFault] = useState('')
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getGraph()
      .then((graph) => {
        setData(graph)
        // Once the pages are English the default a person wants is Korean.
        // Before that it would only spend requests rendering Korean as
        // Korean. The data decides the default, not a setting.
        const words = (graph.nodes as (typeof graph.nodes[number] & Wordy)[])
          .map((n) => n.headline ?? '')
          .filter(Boolean)
        setKorean(words.length > 0 && !words.some((w) => /[가-힣]/.test(w)))
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  // Headline and the one rule line. Identifiers, links and config values stay.
  useEffect(() => {
    if (!korean || !data || said) return
    let stale = false
    const nodes = data.nodes as (typeof data.nodes[number] & Wordy)[]
    const source = nodes.flatMap((n) => [n.headline ?? '', n.rule ?? ''])
    renderAll(source)
      .then((texts) => {
        if (stale) return
        setSaid({
          ...data,
          nodes: nodes.map((n, i) => ({
            ...n,
            headline: texts[i * 2] || n.headline,
            rule: texts[i * 2 + 1] || n.rule,
          })),
        })
      })
      .catch(() => !stale && setFault('번역이 실패했다 — 원문으로 그린다'))
    return () => {
      stale = true
    }
  }, [korean, data, said])

  const shown = korean && said ? said : data

  // The drawing side is imperative and owns its root outright, so it mounts
  // only once data exists and the layout loop is stopped on the way out.
  // Unstopped, it keeps running after the screen is gone.
  useEffect(() => {
    if (!shown || !host.current) return
    return mountGraph(host.current, shown)
  }, [shown])

  if (error) return <p className="wikimap-note">지도를 못 읽었다 — {error}</p>
  if (!data || !shown) return <p className="wikimap-note">지도를 읽는 중…</p>

  const injected = data.nodes.filter((n) => n.injected).length
  const budget = data.cap ? `예산 ${data.cap.toLocaleString()}자` : '예산 없음'

  return (
    <div className="wikimap" ref={host}>
      <div className="wrap">
        <header>
          <p className="eyebrow">ai-coding-agent-wiki</p>
          <h1>측정해서 쓰고,<br />어기면 막는 위키</h1>
          <div className="meters">
            <div className="meter">
              <span className="v">{data.nodes.length}</span>
              <span className="k">페이지</span>
            </div>
            <div className="meter">
              <span className="v">{injected}</span>
              <span className="k">주입되는 규칙</span>
            </div>
            <div className="meter">
              <span className="v">{data.load.max.toLocaleString()}</span>
              <span className="k">한 턴 최대 (자)</span>
              <span className="n">중앙 {data.load.median.toLocaleString()} · {budget}</span>
            </div>
            <div className="meter">
              <span className="v">{data.corpus.toLocaleString()}</span>
              <span className="k">측정한 발화</span>
              <span className="n">그중 {data.load.hits.toLocaleString()}건에 실린다</span>
            </div>
          </div>
        </header>

        <h2>그래프</h2>
        <p className="lede">
          노드를 눌러 규칙을 펴고, 끌어서 옮기고, 휠로 확대한다. 크기는 그 규칙이 한
          턴에 싣는 글자수 — 페이지가 느는 것 자체가 비용이다. 색은 가장 세게
          강제되는 층이고, 사다리의 번호 순서와는 다르다.
        </p>

        <p className="lede">
          <button
            type="button"
            onClick={() => setKorean((on) => !on)}
            aria-pressed={korean}
            disabled={!on}
            className="rounded border border-border px-2 py-0.5 text-[12.5px] disabled:opacity-40"
            title={on ? '제목과 규칙 한 줄만 옮긴다. 슬러그와 설정 값은 그대로다' : '번역 스위치가 꺼져 있다'}
          >
            {korean ? (said ? '한국어' : '옮기는 중') : '원문'}
          </button>
          {fault && <span className="ml-2 text-[12px] text-destructive">{fault}</span>}
        </p>

        <div className="controls">
          <div className="tabs">
            <button className="tab" data-project="all" aria-pressed="true">전체</button>
            {data.projects.map((p) => (
              <button key={p.key} className="tab" data-project={p.key} aria-pressed="false">
                {p.short}
              </button>
            ))}
          </div>
          <input id="search" type="search" placeholder="규칙 찾기" aria-label="규칙 찾기" />
          <button id="reset" className="ghost">되돌리기</button>
        </div>

        <div className="stage">
          <svg id="canvas" viewBox="0 0 900 560" role="img" aria-label="위키 규칙 그래프">
            <g id="view" />
          </svg>
          {/* 여기 안쪽은 그리는 쪽이 쓴다. React 가 자식을 갖지 않아야 안 부딪힌다. */}
          <aside id="panel" />
        </div>

        <div className="legend">
          {data.ladder.map((r) => (
            <button key={r.n} className="chip" data-layer={r.n} aria-pressed="true">
              <i className="swatch" style={{ background: r.color }} />
              {r.n}층 {r.title}
            </button>
          ))}
          <span className="static"><i className="solidline" />링크</span>
          <span className="static"><i className="dashline" />함께 실림</span>
        </div>

        <h2>프로젝트마다 무엇이 붙어 있나</h2>
        <p className="lede">
          위키가 무엇을 선언했는지가 아니라, 그쪽 <code>.claude/settings.json</code> 이
          실제로 무엇을 갖고 있는지를 읽은 값이다. 탭을 바꾸면 안 붙은 규칙이 비어 보인다.
        </p>
        <div className="scroll">
          <table>
            <thead>
              <tr><th>저장소</th><th>붙은 규칙</th><th>deny</th><th>코퍼스</th><th /></tr>
            </thead>
            <tbody>
              {data.projects.length === 0 && (
                <tr><td colSpan={5}>아직 붙인 저장소가 없다. <code>--project</code> 로 준다.</td></tr>
              )}
              {data.projects.map((p) => (
                <tr key={p.key}>
                  <td className="mono">{p.short}</td>
                  <td className="num">
                    {Object.values(p.status).filter((v) => v === 'on').length}
                  </td>
                  <td className="num">{p.deny}</td>
                  <td className="num">{p.corpus.toLocaleString()}</td>
                  <td>{p.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
