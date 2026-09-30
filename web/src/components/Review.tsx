import { useState } from 'react'
import { Btn } from '@/components/Modal'
import * as api from '@/lib/api'
import { PROFILE_LABEL, type Spec } from '@/lib/api'
import { LOOPING } from '@/lib/tasks'
import { cn } from '@/lib/utils'

/** The review tab: the loop of the selected task's spec — its rounds, why it
 *  stopped, and the buttons that act on it. Which spec and PR it is, the
 *  pane's header says. */
export function Review({ spec, onChanged }: { spec: Spec | null; onChanged: () => void }) {
  const [working, setWorking] = useState('')
  const [fault, setFault] = useState('')
  const [note, setNote] = useState('')
  const [file, setFile] = useState<{ title: string; text: string } | null>(null)

  if (!spec) return <p className="p-5 text-[13.5px] text-faint">명세가 없는 작업이다. 리뷰 루프는 명세의 PR 에서 돈다.</p>
  if (!spec.pr) return <p className="p-5 text-[13.5px] text-faint">아직 PR 이 없다. 작업이 끝나 PR 이 서면 여기서 라운드가 돈다.</p>

  async function act(what: string, fn: () => Promise<unknown>) {
    setFault('')
    setWorking(what)
    try {
      await fn()
      setNote('')
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
    } finally {
      setWorking('')
      onChanged()
    }
  }

  async function open(n: number, what: 'order' | 'result') {
    try {
      const { path, text } = await api.roundFile(spec!.id, n, what)
      setFile({ title: path, text })
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
    }
  }

  const reason = spec.stopped?.reason ?? ''
  const rounds = spec.rounds ?? []
  const checked = spec.validation?.round
  const final = spec.validation?.final
  const running = spec.validation?.phase === 'final_running'
  const proven = !running && spec.unproven === ''
  return (
    <div className="h-full overflow-y-auto px-5 py-4 text-[12.5px]">
      <div className="flex items-center gap-2">
        <a href={spec.pr.url} target="_blank" rel="noreferrer" className="font-mono text-[10.5px] text-primary hover:underline">
          GitHub 에서 PR #{spec.pr.number} 열기
        </a>
        {LOOPING.test(spec.state) && (
          <Btn tone="danger" className="ml-auto" disabled={!!working} onClick={() => act('halt', () => api.haltSpec(spec.id))}
            title="이 루프를 멈춘다. [계속] 으로 잇는다">
            멈춤
          </Btn>
        )}
      </div>

      {spec.state === '멈춤' && spec.stopped && (
        <div className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 p-3">
          <div className="text-destructive">멈춤 — {spec.stopped.reason}</div>
          {spec.stopped.detail && <div className="mt-0.5 text-muted-foreground">{spec.stopped.detail}</div>}
          {reason === '검토하지 않은 base 에 머지됨' ? (
            <div className="mt-2 space-y-1.5">
              <div className="flex gap-1.5">
                <Btn disabled={!!working} onClick={() => act('accept', () => api.settleSpec(spec.id, 'accept'))}>받아들임</Btn>
                <Btn disabled={!!working} onClick={() => act('reopen', () => api.settleSpec(spec.id, 'reopen'))}>다시 PR</Btn>
              </div>
              <p className="text-faint">
                `{spec.merge?.base}` 의 머지는 그대로다. 되돌리려면 GitHub 에서. [다시 PR] 은 원래 base 로 새 PR 을 올린다.
              </p>
            </div>
          ) : (
            <div className="mt-2 space-y-1.5">
              {reason === '반론' && (
                <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2}
                  placeholder="그 발견에 정한 것 — 다음 라운드 지시에 들어간다"
                  className="w-full rounded-md border border-border bg-background px-2 py-1 text-[13.5px]" />
              )}
              <Btn tone="primary" disabled={!!working || (reason === '반론' && !note.trim())}
                onClick={() => act('resume', () => api.resumeSpec(spec.id, note.trim()))}>
                {working === 'resume' ? '…' : reason === '라운드 상한' ? '계속 (+4 라운드)' : '계속'}
              </Btn>
            </div>
          )}
        </div>
      )}

      {/^PR #\d+$/.test(spec.state) && spec.fault && (
        <div className="mt-3 rounded-md border border-border p-3">
          <div className="text-muted-foreground">PR 은 올라갔는데 리뷰에 들어가지 못했다 — {spec.fault}</div>
          {spec.plan_commit === 'asked' ? (
            <div className="mt-2">계획 행 커밋이 아직이다 — 에이전트 탭에서 행을 고쳐 커밋하게 하면 리뷰로 간다</div>
          ) : (
            <Btn tone="primary" className="mt-2" disabled={!!working}
              onClick={() => act('review', () => api.startLoops([spec.pr!.number]).then(({ results }) => {
                if (results[0]?.error) throw new Error(results[0].error)
              }))}>
              {working === 'review' ? '…' : '리뷰 시작'}
            </Btn>
          )}
        </div>
      )}

      {(checked || final || running) && (
        <div className="mt-3 space-y-0.5 text-muted-foreground">
          {checked && (
            <div>
              라운드 확인 · {checked.selection === 'mapped' ? '변경이 닿는 것만' : '전체'} ·{' '}
              <span className={checked.ok ? 'text-primary' : 'text-destructive'}>{checked.ok ? '통과' : '실패'}</span>
              <span className="ml-1 font-mono text-[10.5px] text-faint">{checked.head.slice(0, 7)}</span>
            </div>
          )}
          <div>
            최종 게이트 ·{' '}
            {running ? '도는 중 — 끝나기 전에는 머지하지 않는다'
              : !final ? '아직 — 리뷰가 허용한 커밋에서 돈다'
                : !final.ok ? <span className="text-destructive">실패 — {final.reason}</span>
                  : spec.unproven ? `지난 통과는 이제 안 선다 — ${spec.unproven}`
                    : <span className="text-primary">통과</span>}
            {final && !running && <span className="ml-1 font-mono text-[10.5px] text-faint">{final.head.slice(0, 7)}</span>}
          </div>
        </div>
      )}

      {spec.state === '머지 가능' && spec.approved && (
        <div className="mt-3 rounded-md border border-st-ready/50 p-3">
          <div className="flex items-center gap-2">
            {proven ? (
              <Btn tone="primary" disabled={!!working} onClick={() => act('merge', () => api.mergeSpec(spec.id, spec.approved!))}>
                {working === 'merge' ? '머지하는 중…' : '머지 ▸'}
              </Btn>
            ) : (
              // The server refuses the merge and sends the spec back to run only the final gate.
              <Btn disabled={!!working || running}
                onClick={() => act('final', () => api.mergeSpec(spec.id, spec.approved!).catch(() => undefined))}>
                {working === 'final' ? '…' : '최종 게이트 돌리기'}
              </Btn>
            )}
            <span className="font-mono text-[10.5px] text-faint">squash · {spec.approved.slice(0, 7)} 에 묶인다</span>
          </div>
          {spec.p2_comment && (
            <>
              <div className="mt-2 text-faint">머지할 때 PR 에 달 코멘트</div>
              <pre className="mt-1 whitespace-pre-wrap rounded bg-secondary p-2 text-[12px]">{spec.p2_comment}</pre>
            </>
          )}
        </div>
      )}

      {spec.state === '머지 대기' && (
        <p className="mt-3 text-muted-foreground">
          GitHub 이 들고 있다 — {spec.queued || '대기열'}. 실제로 머지되면 정리하고, 대기에서 빠지면 멈춘다.
        </p>
      )}

      {spec.cleanup && spec.cleanup.length > 0 && (
        <ul className="mt-3 list-disc pl-5 text-muted-foreground">
          {spec.cleanup.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      )}

      <table className="mt-4 w-full border-collapse text-left">
        <thead className="font-heading text-[11px] font-semibold text-faint">
          <tr><th className="py-1">라운드</th><th>판정</th><th>P0·P1·P2</th><th>기준 · 리뷰어</th><th>게이트</th><th /></tr>
        </thead>
        <tbody>
          {rounds.length === 0 && <tr><td colSpan={6} className="py-1 text-faint">아직 라운드가 없다</td></tr>}
          {rounds.map((r, i) => (
            <tr key={i} className={cn('border-t border-border', r.stale && 'text-faint line-through')}>
              <td className="py-1 font-mono">R{r.n}{r.stale && ' (버림)'}</td>
              <td className={r.verdict === 'allow' ? 'text-primary' : 'text-destructive'}>
                {r.verdict === 'allow' ? '머지 허용' : '머지 불가'}
              </td>
              <td className="font-mono">
                {r.findings.P0}·{r.findings.P1}·{r.findings.P2}
                {r.identity !== 'full' && r.findings.P0 + r.findings.P1 + r.findings.P2 > 0 && (
                  <span className="ml-1 font-sans text-faint" title="finding-meta 가 없어 발견에 id 가 없다 — 반복으로 세지 않는다">id 없음</span>
                )}
              </td>
              <td title={r.reviewer?.session_id ? `리뷰 세션 ${r.reviewer.session_id}` : undefined}>
                {r.profile ? PROFILE_LABEL[r.profile] : '—'}
                {r.reviewer?.model && (
                  <span className="ml-1 font-mono text-[10.5px] text-faint">
                    {r.reviewer.model.replace(/^codex:/, '')}{r.reviewer.effort ? ` · ${r.reviewer.effort}` : ''}
                  </span>
                )}
              </td>
              <td>{r.gate?.ok == null ? '—' : r.gate.ok ? '통과' : '실패'}</td>
              <td className="text-right">
                {!r.stale && (
                  <>
                    <button type="button" className="text-primary hover:underline" onClick={() => open(r.n, 'order')}>지시</button>
                    <span className="mx-1 text-faint">·</span>
                    <button type="button" className="text-primary hover:underline" onClick={() => open(r.n, 'result')}>결과</button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {fault && <p role="alert" className="mt-2 text-destructive">{fault}</p>}
      {file && (
        <div className="mt-3">
          <div className="flex items-center gap-2 font-mono text-[10.5px] text-faint">
            <span className="truncate">{file.title}</span>
            <button type="button" className="ml-auto" onClick={() => setFile(null)}>닫기</button>
          </div>
          <pre className="mt-1 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-secondary p-2 text-[12px]">{file.text}</pre>
        </div>
      )}
    </div>
  )
}
