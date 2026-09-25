import { useState } from 'react'
import * as api from '@/lib/api'
import type { Spec } from '@/lib/api'
import { cn } from '@/lib/utils'

// A temporary screen for stage 4 of the loop plan. Stage 6 rebuilds it.

const LOOPING = /^(리뷰 대기|리뷰 R\d+|고치는 중 R\d+)$/
const small = 'rounded border border-border bg-background px-2 py-0.5 text-[12.5px] hover:bg-secondary disabled:opacity-40'

/** The review tab: the loop of the spec that owns the selected worktree —
 *  its rounds, why it stopped, and the buttons that act on it. */
export function Review({ spec, onChanged }: { spec: Spec | undefined; onChanged: () => void }) {
  const [working, setWorking] = useState('')
  const [fault, setFault] = useState('')
  const [note, setNote] = useState('')
  const [file, setFile] = useState<{ title: string; text: string } | null>(null)

  if (!spec) return <p className="p-5 text-[12.5px] text-faint">이 작업트리에는 명세가 없다.</p>

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
  return (
    <div className="h-full overflow-y-auto p-5 text-[12.5px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono">{spec.id}</span>
        {spec.pr && <a href={spec.pr.url} target="_blank" rel="noreferrer" className="font-mono text-primary">#{spec.pr.number}</a>}
        <span className="rounded bg-secondary px-1.5 py-0.5">{spec.state}</span>
        {spec.waiting && <span className="rounded bg-wait px-1.5 py-0.5 text-wait-foreground">승인 대기</span>}
        {LOOPING.test(spec.state) && (
          <button type="button" disabled={!!working} className={cn(small, 'ml-auto border-destructive/40 text-destructive')}
            onClick={() => act('halt', () => api.haltSpec(spec.id))}>
            멈춤
          </button>
        )}
      </div>
      <p className="mt-1.5 text-[13.5px]">{spec.goal}</p>

      {spec.state === '멈춤' && spec.stopped && (
        <div className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 p-3">
          <div className="text-destructive">멈춤 — {spec.stopped.reason}</div>
          {spec.stopped.detail && <div className="mt-0.5 text-muted-foreground">{spec.stopped.detail}</div>}
          {reason === '검토하지 않은 base 에 머지됨' ? (
            <div className="mt-2 space-y-1.5">
              <div className="flex gap-1.5">
                <button type="button" disabled={!!working} className={small}
                  onClick={() => act('accept', () => api.settleSpec(spec.id, 'accept'))}>받아들임</button>
                <button type="button" disabled={!!working} className={small}
                  onClick={() => act('reopen', () => api.settleSpec(spec.id, 'reopen'))}>다시 PR</button>
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
                  className="w-full rounded-md border border-border bg-background px-2 py-1 text-[13px]" />
              )}
              <button type="button" disabled={!!working || (reason === '반론' && !note.trim())} className={small}
                onClick={() => act('resume', () => api.resumeSpec(spec.id, note.trim()))}>
                {working === 'resume' ? '…' : reason === '라운드 상한' ? '계속 (+4 라운드)' : '계속'}
              </button>
            </div>
          )}
        </div>
      )}

      {spec.state === '머지 가능' && spec.approved && (
        <div className="mt-3 rounded-md border border-primary/40 p-3">
          <div className="flex items-center gap-2">
            <button type="button" disabled={!!working} className={cn(small, 'border-primary text-primary')}
              onClick={() => act('merge', () => api.mergeSpec(spec.id, spec.approved!))}>
              {working === 'merge' ? '머지하는 중…' : '머지 ▸'}
            </button>
            <span className="font-mono text-[11px] text-faint">squash · {spec.approved.slice(0, 7)} 에 묶인다</span>
          </div>
          {spec.p2_comment && (
            <>
              <div className="mt-2 text-faint">머지할 때 PR 에 달 코멘트</div>
              <pre className="mt-1 whitespace-pre-wrap rounded bg-secondary p-2 text-[12px]">{spec.p2_comment}</pre>
            </>
          )}
        </div>
      )}

      {spec.cleanup && spec.cleanup.length > 0 && (
        <ul className="mt-3 list-disc pl-5 text-muted-foreground">
          {spec.cleanup.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      )}

      <table className="mt-4 w-full border-collapse text-left">
        <thead className="text-[11px] text-faint">
          <tr><th className="py-1">라운드</th><th>판정</th><th>P0·P1·P2</th><th>게이트</th><th /></tr>
        </thead>
        <tbody>
          {rounds.length === 0 && <tr><td colSpan={5} className="py-1 text-faint">아직 라운드가 없다</td></tr>}
          {rounds.map((r, i) => (
            <tr key={i} className={cn('border-t border-border', r.stale && 'text-faint line-through')}>
              <td className="py-1 font-mono">R{r.n}{r.stale && ' (버림)'}</td>
              <td className={r.verdict === 'allow' ? 'text-primary' : 'text-destructive'}>
                {r.verdict === 'allow' ? '머지 허용' : '머지 불가'}
              </td>
              <td className="font-mono">{r.findings.P0}·{r.findings.P1}·{r.findings.P2}</td>
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
          <div className="flex items-center gap-2 font-mono text-[11px] text-faint">
            <span className="truncate">{file.title}</span>
            <button type="button" className="ml-auto" onClick={() => setFile(null)}>닫기</button>
          </div>
          <pre className="mt-1 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-secondary p-2 text-[12px]">{file.text}</pre>
        </div>
      )}
    </div>
  )
}
