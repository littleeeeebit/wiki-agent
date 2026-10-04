import { useEffect, useState } from 'react'
import { Answer } from '@/components/Answer'
import { Btn } from '@/components/Modal'
import * as api from '@/lib/api'
import type { SuiteCell } from '@/lib/api'
import { useParagraphOverlay } from '@/lib/overlay'
import { cn } from '@/lib/utils'

const KIND = { work: '작업', review: '리뷰', planning: '계획', query: '대화' }
const STATUS = { running: '실행 중', waiting: '응답 대기', stopping: '멈추는 중', completed: '완료',
  failed: '실패', stopped: '멈춤', interrupted: '중단 · 완료 기록 없음' }
const ACTIVE = new Set<SuiteCell['status']>(['running', 'waiting', 'stopping'])

export function Suite({ repo, korean, onPeek, onOpen }: {
  repo: string; korean: boolean; onPeek: (path: string, line: number) => void; onOpen: (cell: SuiteCell) => void
}) {
  const [data, setData] = useState<{ rows: SuiteCell[]; history_limit: number } | null>(null)
  const [fault, setFault] = useState('')
  const [filter, setFilter] = useState('all')
  useEffect(() => {
    let stopped = false
    let timer: number | undefined
    const read = async () => {
      try {
        const next = await api.getSuite(repo)
        if (!stopped && next.repo === repo) { setData(next); setFault('') }
      } catch (err) {
        if (!stopped) setFault(String(err instanceof Error ? err.message : err))
      } finally {
        if (!stopped) timer = window.setTimeout(read, 2000)
      }
    }
    void read()
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [repo])
  const rows = (data?.rows ?? []).filter((r) => filter === 'all' || (filter === 'active'
    ? ACTIVE.has(r.status) : filter === 'failed' ? r.status === 'failed' || r.status === 'interrupted'
    : r.status === filter))
  return <section aria-label="스위트 · 셀 실행 목록" className="flex h-full min-h-0 flex-col">
    <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-5 py-3">
      <h2 className="font-heading text-[14px] font-semibold">셀 실행 목록</h2>
      <span className="text-[12.5px] text-muted-foreground">실행 중 {(data?.rows ?? []).filter((r) => ACTIVE.has(r.status)).length}개</span>
      <select aria-label="실행 상태" value={filter} onChange={(e) => setFilter(e.target.value)}
        className="ml-auto min-h-11 max-w-full rounded-md border border-border bg-card px-2 text-[12.5px]">
        <option value="all">모든 실행</option><option value="active">실행 중 · 응답 대기</option>
        <option value="completed">완료</option><option value="failed">실패 · 중단</option><option value="stopped">멈춤</option>
      </select>
      <p className="w-full text-[12.5px] text-muted-foreground">선택한 저장소의 실행과 최근 {data?.history_limit ?? 100}개 기록 · 2초마다 갱신</p>
    </header>
    {fault && <p role="alert" className="px-5 py-3 text-[12.5px] text-destructive">{fault}</p>}
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3">
      {!data && !fault && <p role="status" className="text-[13.5px] text-muted-foreground">실행 목록을 읽는 중…</p>}
      {data && !rows.length && <p className="text-[13.5px] text-muted-foreground">표시할 셀 실행이 없다.</p>}
      <ul className="space-y-2">{rows.map((cell) => <Cell key={cell.id} cell={cell} korean={korean} onPeek={onPeek} onOpen={onOpen} />)}</ul>
    </div>
  </section>
}

function Cell({ cell, korean, onPeek, onOpen }: {
  cell: SuiteCell; korean: boolean; onPeek: (path: string, line: number) => void; onOpen: (cell: SuiteCell) => void
}) {
  const [open, setOpen] = useState(false)
  const started = cell.started_at ? new Date(cell.started_at * 1000).toLocaleString('ko-KR') : '시간 기록 없음'
  const duration = cell.started_at && cell.finished_at ? Math.max(0, Math.round(cell.finished_at - cell.started_at)) : null
  return <li className="min-w-0 rounded-md border border-border bg-card">
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="min-h-11 cursor-pointer px-3 py-3 text-[12.5px] focus-visible:outline-2 focus-visible:outline-primary">
        <span className="font-heading font-semibold">{KIND[cell.kind]} · {cell.target}{cell.pr ? ` · PR #${cell.pr}` : ''}</span>
        <span className={cn('ml-2', cell.status === 'failed' ? 'text-destructive' : cell.status === 'waiting'
          ? 'text-wait' : 'text-muted-foreground')}>{STATUS[cell.status]}</span>
        <span className="mt-1 block break-all text-[12.5px] text-muted-foreground">{cell.model || '모델 기록 없음'}</span>
        <span className="mt-1 block font-mono text-[10.5px] text-muted-foreground">{started}{duration !== null ? ` · ${duration}초` : ''}</span>
      </summary>
      {open && <Detail cell={cell} korean={korean} onPeek={onPeek} onOpen={onOpen} />}
    </details>
  </li>
}

function Detail({ cell, korean, onPeek, onOpen }: {
  cell: SuiteCell; korean: boolean; onPeek: (path: string, line: number) => void; onOpen: (cell: SuiteCell) => void
}) {
  const text = useParagraphOverlay(cell.text, korean)
  return <div className="min-w-0 space-y-3 border-t border-border px-3 py-3">
    {(cell.task || cell.path) && <Btn className="min-h-11" onClick={() => onOpen(cell)}>
      {cell.kind === 'review' ? '리뷰에서 열기' : '에이전트에서 열기'}
    </Btn>}
    {cell.cell && <p className="break-all font-mono text-[10.5px] text-muted-foreground">셀 {cell.cell}</p>}
    {cell.prompt && <details><summary className="cursor-pointer py-2 text-[12.5px]">실행 지시</summary>
      <pre className="whitespace-pre-wrap break-words font-mono text-[12px]">{cell.prompt}</pre></details>}
    {cell.steps.length > 0 && <ol className="space-y-1" aria-label="실행 단계">{cell.steps.map((step, i) =>
      <li key={i} className="whitespace-pre-wrap break-words font-mono text-[12px] text-muted-foreground">
        {step.text}{step.answer ? ` · ${step.answer}` : ''}
      </li>)}</ol>}
    {cell.error && <p className="whitespace-pre-wrap break-words text-[12.5px] text-destructive">{cell.error}</p>}
    {cell.text && <Answer text={text} korean={korean} remote="" onPeek={onPeek} />}
  </div>
}
