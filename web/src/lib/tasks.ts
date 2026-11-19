import { PLAN_PHASE, PLAN_STOP } from '@/lib/api'
import type { LoopRow, Spec, TurnRow, Worktree } from '@/lib/api'

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

/** The state a person reads. A stopped spec whose worktree runs a turn — an answer to the agent's question, an
 *  instruction to go on — is being worked on: its stop still stands on the server until that turn ends. */
export const shownState = (state: string, running: boolean) => state === '멈춤' && running ? '작업 중' : state

/** The rail's order: what a person has to do, what runs, what waits to be
 *  started. Finished work has no place on it. */
export type Group = 'act' | 'run' | 'idle'

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
  /** A gate running for this task: shown on its own row, never as a row of its own. */
  gate: Spec['gating']
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
 *  A merged spec is never shown, nor one left `작업 중` with no PR whose
 *  branch is no longer checked out anywhere: nothing can run it, and those
 *  are work that landed through another branch. The spec files stay. */
export function tasks(specs: Spec[], rows: Worktree[], approvals: (path: string) => number,
  busy: (path: string) => boolean, turning: (path: string, spec: Spec) => boolean): Task[] {
  const byPath = new Map(rows.map((r) => [r.path, r]))
  const owned = new Set<string>()
  const out: Task[] = []
  for (const s of specs) {
    // A refactoring card waits in the refactoring focus until it starts; an
    // earlier workflow's controller card never does. Its PR steps were ordinary tasks.
    if (s.refactor && (!s.refactor.survey || s.state === '정리됨')) continue
    const found = s.worktree ? byPath.get(s.worktree) ?? null : null
    const row = found && (s.workspace_mode !== 'branch' || found.branch === (s.branch ?? s.id)) ? found : null
    if (row) owned.add(row.path)
    const running = row ? busy(row.path) : false
    // A turn, not `busy`: a worktree hold (a context reset) runs nothing and leaves the stop to the person.
    const state = shownState(s.state, !!row && turning(row.path, s))
    const p = phase(state)
    if (p === 'done' || (p === 'work' && !row && !s.pr)) continue
    const asks = row ? approvals(row.path) : 0
    const waiting = asks > 0 || Boolean(row && s.waiting)
    const merging = !!s.merge_progress && ['running', 'waiting_review', 'queued'].includes(s.merge_progress.state)
    // A plan still being drafted says its phase; its questions and its stop wait on the person.
    const plan = s.state === '작업 중' ? s.planning : null
    const planAct = plan?.phase === 'clarify' || plan?.phase === 'stopped'
    const gate = merging ? null : s.gating ?? null
    // A running gate takes the state word's place; the row shows it with its elapsed time.
    const parts = gate ? [] : [merging ? s.merge_progress!.stage : plan ? `계획 · ${PLAN_PHASE[plan.phase]}` : word(state)]
    if (plan?.stopped) parts.push(PLAN_STOP[plan.stopped.reason] ?? plan.stopped.reason)
    if (waiting) parts.push(asks ? `승인 ${asks}` : '승인 대기')
    if (p === 'ready' && !merging) parts.push('머지를 누른다')
    if (p === 'stop' && s.stopped) parts.push(s.stopped.reason)
    if (row?.refreezing) parts.push('바꾸기 전 코드에서 고정 테스트 확인 중')
    if (p === 'queued') parts.push(s.queued || '대기열')
    out.push({
      key: row?.path ?? `spec:${s.id}`, path: row?.path ?? null, spec: s, name: s.id,
      pr: s.pr?.number ?? null, round: (s.rounds ?? []).filter((r) => !r.stale).length,
      phase: p,
      group: waiting || planAct || p === 'ready' && !merging || p === 'stop' ? 'act'
        : p === 'draft' && !running ? 'idle' : 'run',
      line: parts.join(' · '), gate, waiting, busy: running || merging || !!gate, live: row?.live ?? false, row,
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
      group: asks ? 'act' : running ? 'run' : 'idle', line: parts.join(' · '), gate: null, waiting: asks > 0,
      busy: running, live: r.live, row: r })
  }
  return out
}

const owns = (l: LoopRow, t: TurnRow) => t.spec?.repo === l.repo && t.spec?.id === l.id

/** A task of another project that the rail still shows: its loop or its turn, with the
 *  approvals `approvals` already knows for its path. */
export function elsewhere(loops: LoopRow[], turns: TurnRow[], repo: string, approvals: (path: string) => number) {
  return [
    // Rows are keyed by task, not checkout: several tasks share a primary checkout. `turn` is the row's own
    // running turn; the pane steers no other (`Agent`'s `foreign`).
    ...loops.filter((l) => l.repo !== repo && l.worktree).map((l) => {
      // The loop's own turn, by its spec, is not listed apart: its model and its running show on this row.
      const turn = turns.find((t) => t.path === l.worktree && owns(l, t))
      const asks = turn ? approvals(l.worktree!) : 0   // a shared checkout's approvals are the running task's
      const state = shownState(l.state, !!turn)
      return {
        key: `loop:${l.repo}/${l.id}`, turn: turn?.turn,
        path: l.worktree!, repo: l.repo, spec: { repo: l.repo, id: l.id } as TurnRow['spec'],
        name: l.id, pr: l.pr, round: l.round, phase: phase(state), busy: !!turn,
        cell: turn?.cell ?? l.cell ?? undefined,
        waiting: (!!turn && l.waiting) || asks > 0,   // the server's flag is the checkout's: the running task's
        line: [word(state), asks ? `승인 ${asks}` : turn && l.waiting && '승인 대기', state === '멈춤' && l.stopped?.reason,
          l.state === '머지 대기' && (l.queued || '대기열')].filter(Boolean).join(' · '),
      }
    }),
    ...turns.filter((t) => t.repo !== repo && !loops.some((l) => l.worktree === t.path && owns(l, t))).map((t) => {
      const asks = approvals(t.path)
      return {
        key: `turn:${t.path}`, turn: t.turn,
        path: t.path, repo: t.repo, spec: t.spec, name: t.path.split(/[\\/]/).pop() ?? '', pr: null, round: 0,
        phase: 'work' as Phase, busy: true, cell: t.cell, waiting: asks > 0, line: asks ? `도는 중 · 승인 ${asks}` : '도는 중',
      }
    }),
  ]
}

export type Elsewhere = ReturnType<typeof elsewhere>[number]
