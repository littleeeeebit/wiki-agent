// A question's run on screen (stage 9 of `docs/plans/jev/`): where it is
// while it runs, and afterwards what it found, what it published and why.
// Every state is a word or a mark as well as a colour.

import { useEffect, useRef, useState } from 'react'
import { Btn } from '@/components/Modal'
import * as api from '@/lib/api'
import type { RunEvidence, RunSummary } from '@/lib/api'
import { LANE, OUTCOME, PHASES, STAGE, SUPPORT, note, phaseOf } from '@/lib/run'

/** Search → graph expansion → answer → publish, with the stage and stop. */
export function RunProgress({ stage, runId, onStop }: {
  stage?: string; runId?: string; onStop: (runId: string) => void
}) {
  const [stopping, setStopping] = useState(false)
  const now = phaseOf(stage)
  const at = now ? PHASES.indexOf(now) : -1
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <ol aria-label="진행" className="flex flex-wrap items-center gap-1 font-mono text-[10.5px]">
        {PHASES.map((p, i) => (
          <li key={p} aria-current={i === at ? 'step' : undefined}
            className={i === at ? 'text-primary' : i < at ? 'text-muted-foreground' : 'text-faint'}>
            {i > 0 && <span aria-hidden className="mr-1 text-faint">→</span>}
            <span aria-hidden>{i < at ? '✓' : i === at ? '●' : '○'}</span> {p}
            <span className="sr-only">{i < at ? ' 지남' : i === at ? ' 지금' : ' 남음'}</span>
          </li>
        ))}
      </ol>
      <span role="status" className="text-[12.5px] text-muted-foreground">
        {stopping ? '멈추는 중…' : (stage && STAGE[stage]) || '시작하는 중…'}
      </span>
      {runId && (
        <Btn tone="ghost" disabled={stopping} onClick={() => {
          setStopping(true)
          onStop(runId)
        }}>멈춤</Btn>
      )}
    </div>
  )
}

/** What a finished run found and published, read on demand from its summary. */
export function RunDetails({ runId, onPeek, onMapRun }: {
  runId: string; onPeek: (path: string, line: number) => void; onMapRun: (run: RunSummary) => void
}) {
  const [run, setRun] = useState<RunSummary | null>(null)
  const [fault, setFault] = useState('')
  const asked = useRef(false)
  const timer = useRef(0)
  // Leaving the view stops the asking.
  useEffect(() => () => window.clearTimeout(timer.current), [])
  // The answer is out before the run ends (the plain explanation is still
  // being written), so a run not done yet is asked again until it is.
  const load = () => {
    if (asked.current) return
    asked.current = true
    setFault('')
    const once = () => api.getRun(runId)
      .then((r) => (r.done && r.outcome ? setRun(r) : void (timer.current = window.setTimeout(once, 1500))))
      .catch((err) => {
        asked.current = false   // a failure is not final: opening it again asks again
        setFault(String(err instanceof Error ? err.message : err))
      })
    void once()
  }
  const paths = run?.graph?.paths.length ?? 0
  return (
    <details onToggle={(e) => (e.currentTarget.open ? load() : undefined)} className="text-[12.5px]">
      <summary className="min-h-7 cursor-pointer text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
        근거와 판단 보기
      </summary>
      <div className="mt-2 space-y-3 border-l-2 border-border pl-3">
        {fault && <p role="alert" className="text-destructive">실행 기록을 못 읽었다 — {fault}{' '}
          <button type="button" className="text-primary underline-offset-2 hover:underline" onClick={load}>다시 읽기</button></p>}
        {!run && !fault && <p className="text-faint">읽는 중…</p>}
        {run && (
          <>
            <p>
              <span className="font-heading text-[11px] font-semibold text-faint">결과 </span>
              {OUTCOME[run.outcome] ?? run.outcome}
              {run.retrieval?.fallback && ' · 기본 검색으로 대신함'}
              {run.settings && <span className="font-mono text-[10.5px] text-faint"> · Jev {run.settings.mode}</span>}
            </p>
            {(run.notes?.length ?? 0) > 0 && (
              <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
                {run.notes!.map((n, i) => <li key={i}>{note(n)}</li>)}
              </ul>
            )}
            {(run.evidence?.length ?? 0) > 0 && (
              <div>
                <h4 className="mb-1 font-heading text-[11px] font-semibold text-faint">근거 {run.evidence!.length}</h4>
                <ul className="space-y-1.5">
                  {run.evidence!.map((e) => <Evidence key={e.chunk_id} e={e} onPeek={onPeek} />)}
                </ul>
              </div>
            )}
            {run.repository_state?.text && (
              <details>
                <summary className="min-h-7 cursor-pointer text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
                  저장소 관찰 스냅숏
                  {run.repository_state.observed_at && (
                    <time dateTime={run.repository_state.observed_at} className="ml-2 font-mono text-[10.5px] text-faint">
                      {new Date(run.repository_state.observed_at).toLocaleString()}
                    </time>
                  )}
                </summary>
                <pre tabIndex={0} aria-label="저장소 관찰 스냅숏"
                  className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-[12px] text-muted-foreground">
                  {run.repository_state.text}
                </pre>
              </details>
            )}
            {(run.claims?.length ?? 0) > 0 && (
              <p className="text-muted-foreground">
                주장 {run.claims!.length} · 실음 {run.claims!.filter((c) => c.state === 'accepted').length}
                {' · '}뺌 {run.claims!.filter((c) => c.state !== 'accepted').length}
                {run.claims!.some((c) => c.reason) && ` (${[...new Set(run.claims!.map((c) => c.reason).filter(Boolean))].join(', ')})`}
              </p>
            )}
            {paths > 0 && (
              <Btn className="h-auto min-h-7 whitespace-normal py-1 text-left" onClick={() => onMapRun(run)}>
                지도에서 경로 보기 · 경로 {paths}{run.graph!.discarded ? ` · 버림 ${run.graph!.discarded}` : ''}
              </Btn>
            )}
          </>
        )}
      </div>
    </details>
  )
}

function Evidence({ e, onPeek }: { e: RunEvidence; onPeek: (path: string, line: number) => void }) {
  const [english, setEnglish] = useState(false)
  const s = SUPPORT[e.support]
  const path = e.locator.path
  const line = Number(e.locator.start_line ?? e.locator.line ?? 1)
  return (
    <li>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
        <span className={e.support === 'supported' ? 'text-primary' : 'text-muted-foreground'}>
          <span aria-hidden>{s.mark}</span> {s.label}
        </span>
        {path && e.now !== 'missing' ? (
          <button type="button" title="이 자리를 본다" onClick={() => onPeek(path, line)}
            className="min-w-0 break-all text-left font-mono text-[12px] text-primary underline decoration-dotted underline-offset-2">
            {e.cite}
          </button>
        ) : <span className="min-w-0 break-all font-mono text-[12px]">{e.cite}</span>}
        <span className="font-mono text-[10.5px] text-faint">
          {e.kind}{e.lane ? ` · ${LANE[e.lane] ?? e.lane}` : ''} · rev {e.revision.slice(0, 8)}
        </span>
        {e.text_en && (
          <button type="button" aria-pressed={english} onClick={() => setEnglish(!english)}
            className="min-h-7 rounded px-1 text-muted-foreground underline underline-offset-2 hover:text-foreground">
            {english ? '영어 원문 닫기' : '판단에 쓴 영어 보기'}
          </button>
        )}
      </div>
      {e.now && e.now !== 'same' && (
        <p role="status" className="mt-0.5 text-muted-foreground">
          {e.now === 'changed' ? '파일이 그 뒤 바뀌었다 — 누르면 지금 파일이 열린다. 이 실행이 본 판은 ‘판단에 쓴 영어’ 스냅숏뿐이다'
            : e.now === 'missing' ? '파일이 지워졌다 — 이 실행이 본 판은 ‘판단에 쓴 영어’ 스냅숏뿐이다'
              : '파일을 읽지 못한다 — 이 실행이 본 판은 ‘판단에 쓴 영어’ 스냅숏뿐이다'}
        </p>
      )}
      {english && <p className="mt-1 whitespace-pre-wrap font-mono text-[12px] text-muted-foreground">{e.text_en}</p>}
    </li>
  )
}
