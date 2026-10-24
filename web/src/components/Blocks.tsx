import { useState } from 'react'
import { Questions } from '@/components/Questions'
import { PlanStatus } from '@/components/Plan'
import { locks, RefactorCheck, REFACTOR_MODE, rolesText, SurveyDetail, surveyStarted } from '@/components/Refactor'
import type { RefactorMode } from '@/components/Refactor'
import { Btn } from '@/components/Modal'
import type { Choice } from '@/components/Toolbar'
import * as api from '@/lib/api'
import type { Block, ChoiceQuestion, Spec } from '@/lib/api'
import { useOverlay } from '@/lib/overlay'

export type BlockProps = {
  specs: Spec[]
  korean: boolean
  busy: boolean
  /** Send the next thing said in this conversation. */
  onSay: (text: string) => void
  /** Read the spec list again: a card changed. */
  onSpecs: () => void
  /** `choice` is a survey spec's fixed work model; otherwise the empty agent pane's. */
  onStart: (id: string, choice?: Choice) => Promise<void>
  refactorMode?: RefactorMode
  refactorGeneration?: number
}

/** The named blocks at the end of a `next` answer, drawn as what they ask for. */
export function Blocks({ blocks, ...props }: BlockProps & { blocks: Block[] }) {
  return (
    <div className="space-y-3">
      {blocks.map((b, i) => {
        if (b.error !== undefined) return <Broken key={i} name={b.name} error={b.error} {...props} />
        if (b.name === 'candidates' && 'value' in b) return <Candidates key={i} list={b.value} {...props} />
        if (b.name === 'choices' && 'value' in b) return <Choices key={i} value={b.value} {...props} />
        if (b.name === 'spec' && 'id' in b) return <SpecCard key={i} id={b.id} {...props} />
        return null
      })}
    </div>
  )
}

const box = 'not-prose rounded-md border border-border bg-card p-3'

function Broken({ name, error, busy, onSay }: { name: string; error: string } & BlockProps) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-2 text-[12.5px] text-destructive">
      <span>`{name}` 블록이 깨졌다 — {error}</span>
      <Btn disabled={busy} onClick={() => onSay(`The \`${name}\` block failed: ${error}. Emit it again.`)}>
        다시 요청
      </Btn>
    </div>
  )
}

/** Shown translated, sent as written: the agent reads its own words back. */
function Candidates({ list, korean, busy, onSay }: { list: { title: string; why?: string; source?: string }[] } & BlockProps) {
  const shown = useOverlay(list.flatMap((c) => [c.title, c.why ?? '']), korean)
  return (
    <div className={box}>
      <div className="mb-2 font-heading text-[11px] text-faint">후보 — 하나를 고르면 되묻는다</div>
      <div className="space-y-1.5">
        {list.map((c, i) => (
          <button key={i} type="button" disabled={busy} onClick={() => onSay(c.title)}
            className="block w-full rounded-md border border-border px-2.5 py-1.5 text-left hover:bg-secondary disabled:opacity-40">
            <div className="text-[13.5px] leading-snug">{shown[i * 2]}</div>
            {(c.why || c.source) && (
              <div className="mt-0.5 text-[12.5px] text-muted-foreground">
                {shown[i * 2 + 1]}
                {c.source && <span className="ml-1.5 font-mono text-[10.5px] text-faint">{c.source}</span>}
              </div>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}

export function Choices({ value, korean, busy, onSay }:
  { value: ChoiceQuestion | { questions: ChoiceQuestion[] } } & Pick<BlockProps, 'korean' | 'busy' | 'onSay'>) {
  const questions = 'questions' in value ? value.questions : [value]
  return <div className={box}>
    <Questions questions={questions.map((q) => ({ question: q.question, header: q.header,
      multiSelect: q.multi, options: q.options.map((o) => ({ label: o.label, description: o.note, preview: o.preview })) }))}
      korean={korean} disabled={busy}
      onSubmit={(answers) => onSay(questions.length === 1 ? answers[0]
        : questions.map((q, i) => `${q.header || q.question}: ${answers[i]}`).join('\n'))} />
  </div>
}

const lines = (text: string) => text.split('\n').map((l) => l.trim()).filter(Boolean)

/** A spec card. Goal, what stays out and the done items are edited here; the
 *  grounds and the decisions only through the conversation. The gate line is
 *  the server's and stays locked. Once started, it only reports. */
function SpecCard({ id, specs, ...props }: { id: string } & BlockProps) {
  // A rename gives the card a new id; it keeps following its own.
  const [current, setCurrent] = useState(id)
  const spec = specs.find((s) => s.id === current)
  if (!spec) return <p className="text-[12.5px] text-faint">명세 `{current}` 는 이제 없다 — 버렸거나 이름을 바꿨다.</p>
  if (spec.refactor && !spec.refactor.survey) return <Legacy spec={spec} {...props} />
  if (spec.state !== '정리됨') return <Started spec={spec} {...props} />
  // A new version of the spec starts what is typed over.
  return <SpecForm key={`${spec.id}:${spec.rev}`} spec={spec} specs={specs} onRenamed={setCurrent} {...props} />
}

/** A card of the earlier refactoring workflow: it no longer starts; it can be dropped. */
function Legacy({ spec, korean, onSpecs }: { spec: Spec } & Pick<BlockProps, 'korean' | 'onSpecs'>) {
  const [fault, setFault] = useState('')
  return <div className={box}>
    <Head spec={spec} />
    <SpecDetails spec={spec} korean={korean} />
    <p className="mt-2 text-[12.5px] text-muted-foreground">이전 방식의 리팩터링 명세다. 시작할 수 없다 — 목적 모드를 골라 다시 조사한다.</p>
    {spec.state === '정리됨' && <Btn tone="ghost" className="mt-2" onClick={() => api.dropSpec(spec.id)
      .then(onSpecs, (err) => setFault(String(err instanceof Error ? err.message : err)))}>버리기</Btn>}
    {fault && <p role="alert" className="mt-1.5 text-[12.5px] text-destructive">{fault}</p>}
  </div>
}

function Head({ spec }: { spec: Spec }) {
  return (
    <div className="mb-2 flex items-center gap-2 font-heading text-[11px] text-faint">
      <span>명세</span>
      {spec.refactor?.mode && <span>{REFACTOR_MODE[spec.refactor.mode] ?? spec.refactor.mode}</span>}
      <span className="font-mono">{spec.id}</span>
      {spec.rev > 1 && <span>판 {spec.rev}</span>}
      <span className="ml-auto font-mono text-[10.5px] font-normal text-muted-foreground">{spec.state}</span>
    </div>
  )
}

function Started({ spec, korean, busy, onSpecs, onStart }: { spec: Spec } & Pick<BlockProps, 'korean' | 'busy' | 'onSpecs' | 'onStart'>) {
  const [editing, setEditing] = useState(false)
  const [fault, setFault] = useState('')
  return (
    <div className={box}>
      <Head spec={spec} />
      <SpecDetails spec={spec} korean={korean} />
      {spec.refactor?.survey && <RefactorCheck spec={spec} />}
      {spec.planning && <PlanStatus spec={spec} korean={korean} onChanged={onSpecs} />}
      {!busy && !/^(머지됨|머지 대기|리뷰 대기|리뷰 R\d+|고치는 중 R\d+)$/.test(spec.state) &&
        <Btn onClick={() => setEditing((open) => !open)}>{editing ? '편집 닫기' : '명세 수정'}</Btn>}
      {editing && <SpecForm key={`${spec.id}:${spec.rev}`} spec={spec} korean={korean} busy={busy}
        onSpecs={onSpecs} onStart={onStart} onRenamed={() => setEditing(false)} />}
      {spec.workspace_mode === 'branch' && !busy && <Btn onClick={async () => {
        try { await onStart(spec.id) } catch (err) { setFault(String(err)) }
      }}>브랜치 열기</Btn>}
      {fault && <p role="alert" className="text-destructive">{fault}</p>}
      <div className="mt-1 flex flex-wrap gap-x-3 font-mono text-[10.5px] text-muted-foreground">
        {spec.worktree && <span>{spec.workspace_mode === 'branch'
          ? `브랜치 ${spec.branch ?? spec.id}` : `작업트리 ${spec.worktree.split(/[\\/]/).pop()}`}</span>}
        {spec.pr && <a href={spec.pr.url} target="_blank" rel="noreferrer" className="text-primary">PR #{spec.pr.number}</a>}
      </div>
      {spec.gate && !spec.gate.ok && (
        <div className="mt-2 text-[12.5px] text-destructive">
          판정 실패 — {spec.gate.reason}
          {spec.gate.tail && <pre className="mt-1 max-h-40 overflow-auto rounded bg-secondary p-2 font-mono text-[12px] text-foreground">{spec.gate.tail}</pre>}
        </div>
      )}
      {spec.fault && <p className="mt-1 text-[12.5px] text-destructive">{spec.fault}</p>}
    </div>
  )
}

/** Mirror requirements without changing their stored or submitted originals. */
function SpecDetails({ spec, korean }: { spec: Spec; korean: boolean }) {
  const originals = [spec.goal, ...spec.out, ...spec.done,
    ...spec.decisions.flatMap((d) => [d.what, d.why ?? '', d.rejected ?? '']),
    ...(spec.report ?? []).map((r) => r.item)].filter((text) => text !== spec.done[0])
  const translated = useOverlay(originals, korean)
  const overlay = new Map(originals.map((text, i) => [text, translated[i]]))
  const shown = (text: string) => overlay.get(text) ?? text
  const list = (title: string, items: string[]) => items.length > 0 && <div className="mt-2">
    <div className="font-heading text-[11px] text-faint">{title}</div>
    <ul className="mt-1 space-y-1 text-[13.5px] leading-relaxed">{items.map((text, i) => <li key={i}>· {text}</li>)}</ul>
  </div>
  return <div className="break-words">
    <p className="text-[13.5px] leading-relaxed">{shown(spec.goal)}</p>
    <details className="mt-2 text-[12.5px]">
      <summary className="cursor-pointer text-primary">범위 · 완료 조건 보기</summary>
      {list('빼는 것', spec.out.map(shown))}
      {list('완료 조건', spec.done.map(shown))}
      {list('결정', spec.decisions.map((d) => `${shown(d.what)}${d.why ? ` — ${shown(d.why)}` : ''}${d.rejected ? ` (버린 것: ${shown(d.rejected)})` : ''}`))}
      {list('작업 셀의 보고', (spec.report ?? []).map((r) => `${r.pass ? '통과' : '실패'} · ${shown(r.item)}`))}
      {!!spec.revisions?.length && <div className="mt-2">변경 이력 · 현재 판 {spec.rev}</div>}
    </details>
  </div>
}

export function SpecForm({ spec, specs = [], busy, onSpecs, onStart, onRenamed, korean = false, refactorMode, refactorGeneration }:
  { spec: Spec; specs?: Spec[]; onRenamed: (id: string) => void; korean?: boolean; refactorMode?: RefactorMode; refactorGeneration?: number
  } & Pick<BlockProps, 'busy' | 'onSpecs' | 'onStart'>) {
  // A survey spec is refined in the conversation, never typed over, and runs on the roles its survey fixed.
  const surveyed = !!spec.refactor?.survey
  const roles = spec.refactor?.roles
  const locked = locks(spec, specs, refactorMode, refactorGeneration)
  const [goal, setGoal] = useState(spec.goal)
  const [out, setOut] = useState(spec.out.join('\n'))
  const [done, setDone] = useState(spec.done.slice(1).join('\n'))
  const [slug, setSlug] = useState(spec.id)
  const [working, setWorking] = useState('')
  const [fault, setFault] = useState('')
  const [editing, setEditing] = useState(spec.state !== '정리됨')

  const edited = goal !== spec.goal || out !== spec.out.join('\n') || done !== spec.done.slice(1).join('\n')
    || slug !== spec.id

  async function act(what: string, fn: () => Promise<unknown>) {
    setFault('')
    setWorking(what)
    try {
      await fn()
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
    } finally {
      setWorking('')
      onSpecs()
    }
  }

  const field = 'w-full rounded-md border border-border bg-background px-2 py-1 text-[12.5px]'
  return (
    <div className={box}>
      <Head spec={spec} />
      <SpecDetails spec={spec} korean={korean} />
      {surveyed && <SurveyDetail spec={spec} korean={korean} />}
      {!surveyed && <Btn className="mt-2" onClick={() => setEditing((open) => !open)}>{editing ? '원문 편집 닫기' : '원문 수정'}</Btn>}
      {editing && <div className="mt-2 space-y-2 text-[12.5px]">
        <label className="block">
          <span className="text-faint">이름</span>
          <input value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} spellCheck={false}
            disabled={spec.state !== '정리됨'}
            className={`${field} font-mono`} />
        </label>
        <label className="block">
          <span className="text-faint">목표</span>
          <input value={goal} onChange={(e) => setGoal(e.target.value)} className={field} />
        </label>
        <label className="block">
          <span className="text-faint">빼는 것 — 한 줄에 하나</span>
          <textarea value={out} onChange={(e) => setOut(e.target.value)} rows={2} className={field} />
        </label>
        <div>
          <span className="text-faint">완료 조건 — 한 줄에 하나</span>
          <div className="rounded-t-md border border-b-0 border-border bg-secondary px-2 py-1 font-mono text-[12px]"
            title="게이트. 서버가 늘 첫 항목으로 넣고, 작업트리에서 다시 돌린다">
            🔒 {spec.done[0]}
          </div>
          <textarea value={done} onChange={(e) => setDone(e.target.value)} rows={3} className={`${field} rounded-t-none`} />
        </div>
        {(spec.grounds.pages.length + spec.grounds.files.length + spec.grounds.rules.length) > 0 && (
          <div>
            <span className="text-faint">근거</span>
            <ul className="font-mono text-[12px]">
              {spec.grounds.pages.map((g) => <li key={`p${g}`}>{g}</li>)}
              {spec.grounds.files.map((g) => (
                <li key={`f${g}`}>{g}{spec.missing.includes(g) && <span className="ml-1.5 text-destructive">없는 경로</span>}</li>
              ))}
              {spec.grounds.rules.map((g) => <li key={`r${g}`}>{g}</li>)}
            </ul>
          </div>
        )}
        {spec.decisions.length > 0 && (
          <div>
            <span className="text-faint">결정</span>
            <ul className="space-y-0.5">
              {spec.decisions.map((d, i) => (
                <li key={i}>
                  {d.what}
                  {d.why && <span className="text-muted-foreground"> — {d.why}</span>}
                  {d.rejected && <span className="text-faint"> (버린 것: {d.rejected})</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {spec.source.plan && (
          <div className="font-mono text-[10.5px] text-faint">계획 행 · {spec.source.plan.path} #{spec.source.plan.row}</div>
        )}
      </div>}
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {!surveyed && <Btn disabled={!edited || !!working}
          onClick={() => act('save', async () => {
            const saved = await api.saveSpec(spec.id, { rev: spec.rev, goal, out: lines(out), done: lines(done), slug })
            onRenamed(saved.id)
          })}>
          {working === 'save' ? '…' : '저장'}
        </Btn>}
        {roles && <span aria-label="고정 모델" title="리팩터링을 시작할 때 고른 모델 — 이 명세의 모든 세션이 쓴다"
          className="basis-full text-[12.5px] text-muted-foreground">{rolesText(roles)}</span>}
        {spec.state === '정리됨' && <Btn tone="primary" disabled={edited || busy || !!working || !!locked.length}
          title={edited ? '고친 것을 먼저 저장한다' : surveyed
            ? '작업트리에서 이 명세 하나를 실행한다 — 리뷰·검증 뒤 PR 까지, 머지는 직접' : '저장소에 작업 브랜치를 만들고 첫 턴을 보낸다'}
          onClick={() => act('start', () => roles ? onStart(spec.id, roles.worker) : onStart(spec.id))}>
          {working === 'start' ? '시작하는 중…' : '시작 ▸'}
        </Btn>}
        {surveyed && spec.state === '정리됨' && <Btn disabled={busy || !!working || !roles}
          title="이 명세의 파이프라인을 지금 코드에서 다시 읽는다 — 같은 명세가 고쳐지거나, 더는 맞지 않으면 치워진다"
          onClick={() => act('recheck', () => api.post(`/api/refactors/specs/${spec.id}/recheck`,
            { request_id: crypto.randomUUID(), mode: spec.refactor!.mode }, 'POST', spec.repo)
            .then((r) => api.json(r, '다시 확인')).then(surveyStarted))}>
          {working === 'recheck' ? '…' : '다시 확인'}
        </Btn>}
        {spec.state === '정리됨' && <Btn tone="ghost" disabled={!!working} className="ml-auto" onClick={() => act('drop', () => api.dropSpec(spec.id))}>
          버리기
        </Btn>}
      </div>
      {!!locked.length && <ul role="status" aria-label="시작할 수 없는 이유" className="mt-2 text-[12.5px] text-wait">
        {locked.map((why) => <li key={why}>· {why}</li>)}</ul>}
      {fault && <p role="alert" className="mt-1.5 text-[12.5px] text-destructive">{fault}</p>}
    </div>
  )
}
