import { useState } from 'react'
import { Btn, Modal } from '@/components/Modal'
import { Toolbar } from '@/components/Toolbar'
import * as api from '@/lib/api'
import type { Options, PlanRole, Spec } from '@/lib/api'

const ROLES: { key: 'planner' | 'reviser' | 'reviewer'; label: string; note: string }[] = [
  { key: 'planner', label: '계획자', note: '조사하고 새 계획 폴더의 초안을 쓴다. 읽기 전용, 웹 검색만' },
  { key: 'reviser', label: '수정자', note: '리뷰 뒤 문서를 고친다. 서버가 계획 폴더 안에만 쓴다' },
  { key: 'reviewer', label: '리뷰어', note: '따로 연 읽기 전용 세션. 계획자와 같은 세션은 되지 않는다' },
]

const field = 'w-full rounded-md border border-border bg-background px-2 py-1.5 text-[12.5px]'

/** `[계획]`: a goal, three roles and the limits, all submitted before anything
 *  starts. The server makes the plan's worktree and spec and runs the planner;
 *  nothing is implemented or merged after the review allows it. */
export function PlanStart({ options, onClose, onStarted }: {
  options: Options | null
  onClose: () => void
  onStarted: (spec: Spec) => void
}) {
  // One key per form: a second press sends the same key and gets the same plan.
  const [key] = useState(() => crypto.randomUUID())
  const [goal, setGoal] = useState('')
  const [context, setContext] = useState('')
  const [slug, setSlug] = useState('')
  const [stages, setStages] = useState('')
  const [roles, setRoles] = useState<Record<string, PlanRole>>(
    { planner: { model: '', effort: '' }, reviser: { model: '', effort: '' }, reviewer: { model: '', effort: '' } })
  // No defaults: an unlimited or zero allowance never starts.
  const [minutes, setMinutes] = useState('')
  const [calls, setCalls] = useState('')
  const [tokens, setTokens] = useState('')
  const [working, setWorking] = useState(false)
  const [fault, setFault] = useState('')

  const limits = { seconds: Number(minutes) * 60, calls: Number(calls), tokens: Number(tokens) }
  const positive = Object.values(limits).every((v) => Number.isFinite(v) && v > 0)
  const ready = goal.trim() !== '' && positive && !working

  const submit = async () => {
    setFault('')
    setWorking(true)
    try {
      onStarted(await api.startPlan({
        request_id: key, goal, context, slug, stages: stages ? Number(stages) : null,
        roles: { planner: roles.planner, reviser: roles.reviser, reviewer: roles.reviewer },
        limits: { ...limits, calls: Math.floor(limits.calls), tokens: Math.floor(limits.tokens) },
      }))
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
      setWorking(false)
    }
  }

  return (
    <Modal title="계획 세우기" wide locked={working} onClose={onClose} foot={(
      <>
        <Btn disabled={working} onClick={onClose}>취소</Btn>
        <Btn tone="primary" disabled={!ready} onClick={() => void submit()}>{working ? '시작하는 중…' : '계획 시작 ▸'}</Btn>
      </>
    )}>
      <div className="space-y-3">
        <label className="block space-y-1">
          <span className="font-heading text-[11px] font-semibold text-faint">목표</span>
          <textarea className={field} rows={2} value={goal} onChange={(e) => setGoal(e.target.value)} />
        </label>
        <label className="block space-y-1">
          <span className="font-heading text-[11px] font-semibold text-faint">맥락 — 진행 상황, 막힌 것, 꼭 볼 문서</span>
          <textarea className={field} rows={3} value={context} onChange={(e) => setContext(e.target.value)} />
        </label>
        <div className="flex gap-3">
          <label className="block flex-1 space-y-1">
            <span className="font-heading text-[11px] font-semibold text-faint">폴더 이름 (docs/plans/…)</span>
            <input className={`${field} font-mono`} placeholder="비우면 자동" value={slug}
              onChange={(e) => setSlug(e.target.value)} />
          </label>
          <label className="block w-28 space-y-1">
            <span className="font-heading text-[11px] font-semibold text-faint">단계 수</span>
            <input className={field} type="number" min={1} max={10} placeholder="계획자가" value={stages}
              onChange={(e) => setStages(e.target.value)} />
          </label>
        </div>
        <div className="space-y-2">
          {ROLES.map((r) => (
            <div key={r.key} className="flex items-center gap-3">
              <div className="w-40 shrink-0">
                <div className="font-heading text-[11px] font-semibold">{r.label}</div>
                <div className="text-[10.5px] text-faint">{r.note}</div>
              </div>
              <Toolbar value={roles[r.key]} options={options} busy={working}
                onChange={(next) => setRoles((prev) => ({ ...prev, [r.key]: next }))} />
            </div>
          ))}
        </div>
        <div className="flex gap-3">
          {([['시간 (분)', minutes, setMinutes], ['호출 (턴)', calls, setCalls], ['토큰', tokens, setTokens]] as const)
            .map(([label, value, set]) => (
              <label key={label} className="block flex-1 space-y-1">
                <span className="font-heading text-[11px] font-semibold text-faint">{label}</span>
                <input className={field} type="number" min={1} required value={value}
                  onChange={(e) => set(e.target.value)} />
              </label>
            ))}
        </div>
        <ul className="space-y-0.5 text-[11.5px] text-muted-foreground">
          <li>· 호출은 계획자에게 보낸 턴 수다. 한 턴 안에서 호스트가 부른 모델 호출까지 세지는 못해 엄격한 공급자 상한이 아니다</li>
          <li>· 토큰은 답이 끝난 뒤에 보고된다. 마지막 턴이 한도를 넘을 수 있고, 넘으면 실패로 적고 더 보내지 않는다</li>
          <li>· 사용량을 알리지 않는 호스트는 0 으로 치지 않고 멈춘다. 시간 한도에 닿으면 도는 턴을 끊는다</li>
          <li>· 적어도 단계 수 + 2 턴이 든다 (조사, 개요). 답을 못 읽으면 한 번, 검사 실패면 한 번 더 든다</li>
        </ul>
        {fault && <p role="alert" className="whitespace-pre-wrap text-destructive">{fault}</p>}
      </div>
    </Modal>
  )
}

const RUNNING = ['collect', 'research', 'outline', 'stages', 'validate', 'publish']

/** A plan spec's run: where it is, what it spent against what was allowed,
 *  the questions waiting on the person, and resume or cancel. */
export function PlanStatus({ spec, onChanged }: { spec: Spec; onChanged: () => void }) {
  const p = spec.planning!
  const [chosen, setChosen] = useState<Record<string, string>>({})
  const [working, setWorking] = useState(false)
  const [fault, setFault] = useState('')

  const act = async (fn: () => Promise<unknown>) => {
    setFault('')
    setWorking(true)
    try {
      await fn()
      onChanged()
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
    } finally {
      setWorking(false)
    }
  }
  const role = (r: PlanRole) => `${r.model || '기본'}${r.effort ? ` · ${r.effort}` : ''}`
  const answered = p.questions.every((q) => chosen[q.id]?.trim())

  return (
    <div className="space-y-2">
      <div className="font-heading text-[11px] font-semibold text-faint">계획</div>
      <div>
        {api.PLAN_PHASE[p.phase]}
        {p.stopped && <span className="text-destructive"> — {api.PLAN_STOP[p.stopped.reason] ?? p.stopped.reason}
          {p.stopped.detail && <span className="block whitespace-pre-wrap text-[11.5px]">{p.stopped.detail}</span>}</span>}
      </div>
      <div className="text-[11.5px] text-muted-foreground">
        시간 {Math.round(p.spent.seconds)}/{p.limits.seconds}초 · 턴 {p.spent.calls}/{p.limits.calls}
        · 토큰 {p.spent.tokens.toLocaleString()}/{p.limits.tokens.toLocaleString()} · 도구 {p.spent.tools}
        {p.overrun && <span className="text-destructive"> · 토큰 한도를 넘었다 ({p.overrun.tokens.toLocaleString()})</span>}
        {p.spent.unknown && <span className="text-destructive"> · 사용량을 모르는 턴이 있다</span>}
      </div>
      <div className="text-[11.5px] text-muted-foreground">
        계획자 {role(p.roles.planner)} · 수정자 {role(p.roles.reviser)} · 리뷰어 {role(p.roles.reviewer)}
      </div>
      {p.phase === 'clarify' && (
        <div className="space-y-2 rounded-md border border-border p-2">
          {p.questions.map((q) => (
            <fieldset key={q.id} className="space-y-1">
              <legend className="font-semibold">{q.question}</legend>
              {q.options.map((o) => (
                <label key={o.label} className="flex items-baseline gap-1.5">
                  <input type="radio" name={`${spec.id}-${q.id}`} checked={chosen[q.id] === o.label}
                    onChange={() => setChosen((prev) => ({ ...prev, [q.id]: o.label }))} />
                  <span>{o.label}{o.note && <span className="ml-1 text-faint">— {o.note}</span>}</span>
                </label>
              ))}
            </fieldset>
          ))}
          <Btn tone="primary" disabled={working || !answered} onClick={() => void act(() => api.answerPlan(
            spec.id, p.questions_rev, p.questions.map((q) => ({ id: q.id, choice: chosen[q.id] }))))}>
            답하고 이어 가기
          </Btn>
        </div>
      )}
      {p.source_manifest && (
        <ul className="space-y-0.5 text-[11.5px]">
          {p.source_manifest.map((s) => (
            <li key={s.id}>· {s.id} {s.kind === 'web'
              ? <a className="underline" href={s.url} target="_blank" rel="noreferrer">{s.title}</a>
              : <code className="font-mono">{s.url}</code>}</li>
          ))}
        </ul>
      )}
      {p.publication.pr && <a className="underline" href={p.publication.pr.url} target="_blank" rel="noreferrer">
        PR #{p.publication.pr.number}</a>}
      <div className="flex gap-2">
        {p.phase === 'stopped' && <Btn tone="primary" disabled={working} onClick={() => void act(() => api.resumePlan(spec.id))}>재개</Btn>}
        {(p.phase === 'clarify' || RUNNING.includes(p.phase)) && (
          <Btn tone="danger" disabled={working} onClick={() => void act(() => api.cancelPlan(spec.id))}>취소</Btn>
        )}
      </div>
      {fault && <p role="alert" className="text-destructive">{fault}</p>}
    </div>
  )
}
