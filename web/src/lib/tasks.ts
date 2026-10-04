import { PLAN_PHASE, PLAN_STOP } from '@/lib/api'
import type { LoopRow, Spec, Worktree } from '@/lib/api'

/** The review loop's own states: while in one of these, the server runs it. */
export const LOOPING = /^(리뷰 대기|리뷰 R\d+|고치는 중 R\d+)$/

/** A spec's place in the loop, in six colours (`--st-*`), plus none. */
export type Phase = 'draft' | 'work' | 'review' | 'ready' | 'queued' | 'stop' | 'done' | 'none'

export function phase(state: string | undefined): Phase {
  if (!state) return 'none'
  if (state === '정리됨') return 'draft'
  if (state === '작업 중') return 'work'
  if (state === '머지 가능') return 'ready'
  if (state === '머지 대기') return 'queued'
  if (state === '머지됨') return 'done'
  if (state === '멈춤') return 'stop'
  return 'review'   // PR #n, 리뷰 대기, 리뷰 Rn, 고치는 중 Rn
}

/** The rail's order: what a person has to do, what runs, what waits to be
 *  started, and what is finished. */
export type Group = 'act' | 'run' | 'idle' | 'done'

export type Task = {
  /** The worktree's path, or `spec:<id>` for a spec not started yet. */
  key: string
  path: string | null
  spec: Spec | null
  name: string
  pr: number | null
  round: number
  phase: Phase
  group: Group
  /** The second line: one word of state, then what a person has to do. */
  line: string
  waiting: boolean
  busy: boolean
  live: boolean
  row: Worktree | null
}

/** One word for a state. The PR and the round are on the first line already. */
function word(state: string): string {
  if (state === '정리됨') return '시작 대기'
  if (/^리뷰 R\d+$/.test(state)) return '리뷰 중'
  if (/^고치는 중 R\d+$/.test(state)) return '고치는 중'
  if (/^PR #\d+$/.test(state)) return 'PR 올림'
  return state
}

/** The project's tasks: one per spec and one per worktree without a spec.
 *  A finished spec whose worktree was removed is gone from the rail. */
export function tasks(specs: Spec[], rows: Worktree[], approvals: (path: string) => number,
  busy: (path: string) => boolean): Task[] {
  const byPath = new Map(rows.map((r) => [r.path, r]))
  const owned = new Set<string>()
  const out: Task[] = []
  for (const s of specs) {
    const found = s.worktree ? byPath.get(s.worktree) ?? null : null
    const row = found && (s.workspace_mode !== 'branch' || found.branch === (s.branch ?? s.id)) ? found : null
    if (row) owned.add(row.path)
    const p = phase(s.state)
    if (p === 'done' && (s.cleanup_complete || (s.cleanup_complete === undefined && !row))) continue
    const cleanupPending = p === 'done' && s.cleanup_complete === false
    const asks = row ? approvals(row.path) : 0
    const waiting = asks > 0 || Boolean(row && s.waiting)
    const running = row ? busy(row.path) : false
    // A plan still being drafted says its phase; its questions and its stop wait on the person.
    const plan = s.state === '작업 중' ? s.planning : null
    const planAct = plan?.phase === 'clarify' || plan?.phase === 'stopped'
    const parts = [cleanupPending ? '정리 대기' : plan ? `계획 · ${PLAN_PHASE[plan.phase]}` : word(s.state)]
    if (plan?.stopped) parts.push(PLAN_STOP[plan.stopped.reason] ?? plan.stopped.reason)
    if (waiting) parts.push(asks ? `승인 ${asks}` : '승인 대기')
    if (p === 'ready') parts.push('머지를 누른다')
    if (p === 'stop' && s.stopped) parts.push(s.stopped.reason)
    if (p === 'queued') parts.push(s.queued || '대기열')
    out.push({
      key: row?.path ?? `spec:${s.id}`, path: row?.path ?? null, spec: s, name: s.id,
      pr: s.pr?.number ?? null, round: (s.rounds ?? []).filter((r) => !r.stale).length,
      phase: p,
      group: waiting || planAct || cleanupPending || p === 'ready' || p === 'stop' ? 'act'
        : p === 'done' ? 'done' : p === 'draft' && !running ? 'idle' : 'run',
      line: parts.join(' · '), waiting, busy: running, live: row?.live ?? false, row,
    })
  }
  for (const r of rows) {
    if (owned.has(r.path)) continue
    if (r.primary && !r.busy && !r.live) continue
    const asks = approvals(r.path)
    const running = busy(r.path)
    const parts = ['명세 없음']
    if (asks) parts.push(`승인 ${asks}`)
    else if (r.dirty) parts.push('변경 있음')
    else if (r.merged) parts.push('HEAD 에 다 있음')
    out.push({ key: r.path, path: r.path, spec: null, name: r.name, pr: null, round: 0, phase: 'none',
      group: asks ? 'act' : running ? 'run' : 'idle', line: parts.join(' · '), waiting: asks > 0,
      busy: running, live: r.live, row: r })
  }
  return out
}

/** A task of another project that the rail still shows: its loop or its turn. */
export function elsewhere(loops: LoopRow[], turns: { path: string; repo: string }[], repo: string) {
  return [
    ...loops.filter((l) => l.repo !== repo && l.worktree).map((l) => ({
      path: l.worktree!, repo: l.repo, name: l.id, pr: l.pr, round: l.round, phase: phase(l.state),
      waiting: l.waiting,
      line: [word(l.state), l.waiting && '승인 대기', l.stopped?.reason, l.state === '머지 대기' && (l.queued || '대기열')]
        .filter(Boolean).join(' · '),
    })),
    ...turns.filter((t) => t.repo !== repo && !loops.some((l) => l.worktree === t.path)).map((t) => ({
      path: t.path, repo: t.repo, name: t.path.split(/[\\/]/).pop() ?? '', pr: null, round: 0,
      phase: 'work' as Phase, waiting: false, line: '도는 중',
    })),
  ]
}

export type Elsewhere = ReturnType<typeof elsewhere>[number]
