import { useEffect, useState } from 'react'
import { Btn, Modal } from '@/components/Modal'
import { Toolbar } from '@/components/Toolbar'
import { browseFolder, get, getLoopSettings, getOptions, json, post, type Options } from '@/lib/api'
import { remote } from '@/lib/mobile'
import { ModelsLine, OneshotSpec } from '@/components/OneshotSpec'
import { ROLE, oneshotSpecs, stageLabel, type Models, type SpecSummary } from '@/lib/oneshot'

type OneshotField = {
  options?: string[]; enabled?: string[]; text?: boolean; asked?: boolean; default: string
}
type OneshotRun = {
  id: string
  state: 'intake' | 'environment' | 'bootstrap' | 'spec'
  working: string | null
  stopped: { reason: 'block' | 'folder' | 'github' | 'connect' | 'restart'; detail: string; state: string } | null
  sentence: string
  models?: Models
  picked: string
  intent: { restated: string; kind: string; name: string; users: string; first_screen: string; unsupported?: boolean } | null
  profile: Record<string, string | number> | null
  proposed: Record<string, { value: string | number; why: string }>
  identity: { path: string; name: string; owner: string } | null
  steps: Record<string, number>
  spec: { repo: string; id: string } | null
  errors: string[]
  warnings: string[]
  schema: Record<string, OneshotField>
  steps_order: string[]
}

const answer = (r: Response) => json<OneshotRun>(r, '원샷')
const api = {
  browseFolder,
  oneshotRuns: () => get('/api/oneshot').then((r) => json<{ runs: OneshotRun[] }>(r, '원샷 목록')),
  oneshotRun: (id: string) => get(`/api/oneshot/${id}`).then(answer),
  startOneshot: (sentence: string, path: string, models: Models) =>
    post('/api/oneshot', { sentence, path, models }).then(answer),
  answerIntent: (id: string, body: { confirm: boolean; edit?: string; name?: string }) =>
    post(`/api/oneshot/${id}/intent`, body).then(answer),
  saveProfile: (id: string, values: Record<string, string | number>, confirm: boolean) =>
    post(`/api/oneshot/${id}/profile`, { values, confirm }).then(answer),
  resumeOneshot: (id: string, adopt = false) => post(`/api/oneshot/${id}/resume`, { adopt }).then(answer),
}

/** The oneshot run's minimum screen (`docs/plans/oneshot/2-bootstrap.md`):
 *  the intake form, the restatement beside the owner's sentence, the
 *  environment card and the bootstrap's progress. Every stop shows its reason
 *  and `[재개]`. The server owns every rule; this screen only shows and asks. */

const LABEL: Record<string, string> = {
  os_now: '지금 쓰는 OS', os_later: '다른 OS 에서도', auth: '로그인',
  visibility: 'GitHub 공개 범위', license: '라이선스', ci: 'CI',
  ui_language: '화면 언어', docs_language: '문서 언어', responsive: '화면 크기', llm: 'LLM 사용',
  github_owner: 'GitHub 소유자',
}
const STEP: Record<string, string> = {
  recheck: '이름 다시 확인', folder: '폴더', files: '기본 파일', commit: '첫 커밋', register: '앱에 연결',
  remote: 'GitHub 저장소', origin: 'origin 연결', publish: 'main push', spec: '명세 작업 공간',
}
const STOP: Record<string, string> = {
  block: '모델 답을 쓸 수 없었다', folder: '폴더나 이름 문제', github: 'GitHub 문제', connect: '연결을 확인해야 한다',
  restart: '서버가 다시 켜져 멈췄다',
}
const WORKING: Record<string, string> = {
  intake: '하고 싶은 것을 다시 정리하는 중…', profile: '환경 카드를 채우는 중…', bootstrap: '저장소를 만드는 중…',
}

const error = (err: unknown) => String(err instanceof Error ? err.message : err)

export function Oneshot({ onClose, repo: picked = '' }: { onClose: () => void; repo?: string }) {
  const [run, setRun] = useState<OneshotRun | null>(null)
  const [repo, setRepo] = useState(picked)
  const [open, setOpen] = useState<OneshotRun[]>([])
  const [later, setLater] = useState<SpecSummary[]>([])
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.oneshotRuns().then((d) => setOpen(d.runs)).catch((err) => setFault(error(err)))
    oneshotSpecs().then((d) => setLater(d.specs)).catch((err) => setFault(error(err)))
  }, [])

  // While a stage runs on the server, read the run until it settles.
  const id = run?.id
  const working = !!run?.working
  useEffect(() => {
    if (!id || !working) return
    const timer = window.setInterval(() => {
      api.oneshotRun(id).then(setRun).catch((err) => setFault(error(err)))
    }, 1500)
    return () => window.clearInterval(timer)
  }, [id, working])

  async function act(fn: () => Promise<OneshotRun>) {
    setFault('')
    setBusy(true)
    try {
      setRun(await fn())
    } catch (err) {
      setFault(error(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="새 프로젝트 — 원샷" wide locked={busy} onClose={onClose}>
      <div className="flex flex-col gap-4">
        {fault && <p role="alert" className="whitespace-pre-wrap rounded-md bg-destructive/10 px-3 py-2 text-destructive">{fault}</p>}
        {repo && <OneshotSpec repo={repo} />}
        {!repo && !run && <Intake open={open} later={later} busy={busy} act={act} pick={setRun} pickSpec={setRepo} />}
        {!repo && run && <Body run={run} busy={busy} act={act} />}
      </div>
    </Modal>
  )
}

/** The runs past S1, a row above the project list: each opens in the oneshot window, marked when it waits. */
export function OneshotRail({ open }: { open: (repo: string) => void }) {
  const [runs, setRuns] = useState<SpecSummary[]>([])
  useEffect(() => {
    const read = () => { oneshotSpecs().then((d) => setRuns(d.specs.filter((s) => s.stage !== 'done'))).catch(() => setRuns([])) }
    read()
    // A worker moving a run on (an automatic stage reaching a gate) saves its spec, which the feed announces;
    // a feed that reconnects or resyncs announces no single spec, so the rail reads again then too.
    const on = ['focus', 'spec-changed', 'server-resync']
    on.forEach((name) => window.addEventListener(name, read))
    return () => on.forEach((name) => window.removeEventListener(name, read))
  }, [])
  if (!runs.length) return null
  return (
    <nav aria-label="원샷 실행" className="flex flex-wrap gap-1.5 border-b border-border px-5 py-2 text-[12.5px]">
      {runs.map((s) => (
        <button key={s.repo} type="button" onClick={() => open(s.repo)}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 hover:bg-secondary">
          {s.waits && <span aria-label="결정을 기다린다" className="size-2 rounded-full bg-wait" />}
          <span className="font-mono">{s.repo}</span>
          <span className="text-muted-foreground">{s.stopped ? '멈춤' : stageLabel(s.stage)}</span>
        </button>
      ))}
    </nav>
  )
}

type Act = (fn: () => Promise<OneshotRun>) => Promise<void>

function Intake({ open, later, busy, act, pick, pickSpec }: {
  open: OneshotRun[]; later: SpecSummary[]; busy: boolean; act: Act; pick: (run: OneshotRun) => void
  pickSpec: (repo: string) => void
}) {
  const [sentence, setSentence] = useState('')
  const [path, setPath] = useState('')
  const [typing, setTyping] = useState(remote)
  const [options, setOptions] = useState<Options | null>(null)
  const none = { model: '', effort: '' }
  const [models, setModels] = useState<Models>({ spec: none, build: none, review: none })
  // The review starts on the Review Loop's own choice, so the run fixes a named model, not "whatever it is then".
  useEffect(() => {
    getOptions().then(setOptions).catch(() => setOptions(null))
    getLoopSettings().then((s) => setModels((m) => m.review.model || m.review.effort ? m
      : { ...m, review: { model: s.review_model ?? '', effort: s.review_effort ?? '' } }))
      .catch(() => {})
  }, [])
  const codex = options?.models.find((m) => m.id.startsWith('codex:') && m.is_default)
  const reviewOptions = options && { ...options, models: [
    { id: '', label: '리뷰 설정 따름', note: '시작할 때 리뷰 루프 설정의 모델로 정한다', efforts: codex?.efforts ?? [] },
    ...options.models.filter((m) => m.id),
  ] }
  const HINT = { spec: '요구 대화, 백엔드·화면 설계', build: '코드를 쓰는 작업 세션, PR 뒤의 수정', review: 'PR 전 리뷰, PR 뒤 리뷰 루프' }

  const find = async () => {
    const found = await api.browseFolder().catch(() => null)
    if (found === null) setTyping(true)
    else if (found) setPath(found)
  }

  return (
    <>
      {(open.length > 0 || later.length > 0) && (
        <section className="flex flex-col gap-1.5">
          <h3 className="font-semibold">끝나지 않은 실행</h3>
          {later.filter((s) => s.stage !== 'done').map((s) => (
            <button key={s.repo} type="button" onClick={() => pickSpec(s.repo)}
              className="truncate rounded-md border border-border px-3 py-2 text-left hover:bg-secondary">
              {s.waits && <span aria-label="결정을 기다린다" className="mr-1.5 inline-block size-2 rounded-full bg-wait" />}
              {s.repo}
              <span className="ml-2 text-muted-foreground">{s.stopped ? `멈춤 — ${s.stopped.reason}` : stageLabel(s.stage)}</span>
            </button>
          ))}
          {open.map((r) => (
            <button key={r.id} type="button" onClick={() => pick(r)}
              className="truncate rounded-md border border-border px-3 py-2 text-left hover:bg-secondary">
              {r.identity?.name ?? r.intent?.name ?? r.sentence}
              <span className="ml-2 text-muted-foreground">{r.stopped ? `멈춤 — ${STOP[r.stopped.reason] ?? r.stopped.reason}` : r.state}</span>
            </button>
          ))}
        </section>
      )}
      <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); void act(() => api.startOneshot(sentence, path, models)) }}>
        <label className="flex flex-col gap-1.5">
          <span className="font-semibold">무엇을 만들고 싶은가</span>
          <textarea value={sentence} onChange={(e) => setSentence(e.target.value)} rows={4} maxLength={2000}
            placeholder="한두 문장이면 된다. 예: 동네 독서 모임의 책 투표와 모임 일정을 한곳에서 관리하고 싶다"
            className="rounded-md border border-input bg-background px-2 py-1.5 text-foreground" />
        </label>
        <div className="flex flex-col gap-1.5">
          <span className="font-semibold">폴더</span>
          {typing ? (
            <input aria-label="비어 있는 폴더 경로" value={path} onChange={(e) => setPath(e.target.value)}
              placeholder="비어 있는 폴더 경로 — 비우면 작업 공간에 새로 만든다"
              className="h-7 rounded-md border border-input bg-background px-2 font-mono text-[12px] text-foreground" />
          ) : (
            <div className="flex items-center gap-2">
              <Btn onClick={() => void find()} disabled={busy}>빈 폴더 고르기</Btn>
              <span className="truncate font-mono text-[12px] text-muted-foreground">{path || '고르지 않으면 작업 공간에 새 폴더를 만든다'}</span>
              {path && <Btn tone="ghost" onClick={() => setPath('')}>지우기</Btn>}
            </div>
          )}
        </div>
        <fieldset className="flex flex-col gap-1.5">
          <legend className="font-semibold">모델 — 이 실행이 끝날 때까지 바뀌지 않는다</legend>
          {(Object.keys(ROLE) as (keyof Models)[]).map((k) => (
            <div key={k} role="group" aria-label={`${ROLE[k]} 모델`} className="flex flex-wrap items-center gap-2">
              <span className="w-12 font-medium">{ROLE[k]}</span>
              <Toolbar options={k === 'review' ? reviewOptions : options} value={models[k]} busy={busy}
                onChange={(c) => setModels((m) => ({ ...m, [k]: { model: c.model, effort: c.effort } }))} />
              <span className="text-[12px] text-muted-foreground">{HINT[k]}</span>
            </div>
          ))}
          <p className="text-[12px] text-muted-foreground">화면 비평은 이미지를 읽어야 해서 opus · high 로 고정이다.</p>
        </fieldset>
        <div className="flex justify-end">
          <Btn type="submit" tone="primary" disabled={busy || !sentence.trim()}>{busy ? '시작하는 중…' : '시작'}</Btn>
        </div>
      </form>
    </>
  )
}

function Body({ run, busy, act }: { run: OneshotRun; busy: boolean; act: Act }) {
  if (run.state === 'spec') {
    return (
      <>
        <p role="status">
          저장소 <span className="font-mono">{run.spec?.repo}</span> 를 만들고 main 에 올렸다. 명세는 <span className="font-mono">{run.spec?.id}</span> 브랜치의
          작업 공간에서 이어진다.
        </p>
        {run.spec && <OneshotSpec repo={run.spec.repo} />}
      </>
    )
  }
  return (
    <>
      <ModelsLine models={run.models} />
      {run.stopped && <Stopped run={run} busy={busy} act={act} />}
      {run.working && <p role="status" className="text-muted-foreground">{WORKING[run.working] ?? run.working}</p>}
      {run.state === 'intake' && run.intent && !run.working && !run.stopped && <Restated run={run} busy={busy} act={act} />}
      {run.state === 'environment' && run.profile && !run.working && !run.stopped && <Card run={run} busy={busy} act={act} />}
      {run.state === 'bootstrap' && <Steps run={run} />}
    </>
  )
}

function Stopped({ run, busy, act }: { run: OneshotRun; busy: boolean; act: Act }) {
  const s = run.stopped!
  return (
    <section role="alert" className="flex flex-col gap-2 rounded-md border border-wait/40 bg-wait/10 px-3 py-2">
      <p className="font-semibold">멈췄다 — {STOP[s.reason] ?? s.reason}</p>
      <p className="whitespace-pre-wrap">{s.detail}</p>
      <div className="flex justify-end gap-2">
        {s.reason === 'github' && (
          <Btn disabled={busy} onClick={() => void act(() => api.resumeOneshot(run.id, true))}
            title="GitHub 에 이미 있는 같은 이름의 빈 저장소를 이 실행의 것으로 쓴다. 커밋이 하나라도 있으면 서버가 거절한다">
            이어 쓰기
          </Btn>
        )}
        <Btn tone="primary" disabled={busy} onClick={() => void act(() => api.resumeOneshot(run.id))}>재개</Btn>
      </div>
    </section>
  )
}

function Restated({ run, busy, act }: { run: OneshotRun; busy: boolean; act: Act }) {
  const intent = run.intent!
  const [name, setName] = useState(intent.name)
  const [edit, setEdit] = useState('')
  return (
    <section className="flex flex-col gap-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <h3 className="font-semibold">내가 쓴 말</h3>
          <p className="whitespace-pre-wrap text-muted-foreground">{run.sentence}</p>
        </div>
        <div className="flex flex-col gap-1">
          <h3 className="font-semibold">에이전트가 이해한 것</h3>
          <p className="whitespace-pre-wrap">{intent.restated}</p>
          <p><span className="text-muted-foreground">쓰는 사람</span> {intent.users}</p>
          <p><span className="text-muted-foreground">첫 화면</span> {intent.first_screen}</p>
          <p><span className="text-muted-foreground">종류</span> <span className="font-mono">{intent.kind}</span></p>
        </div>
      </div>
      {intent.unsupported && (
        <p className="rounded-md bg-wait/10 px-3 py-2">
          <span className="font-mono">{intent.kind}</span> 는 이 버전에서 만들 수 없다 — 지금은 웹앱만. 웹앱으로 만들고 싶다면 아래에 그렇게 고쳐 적어라.
        </p>
      )}
      <label className="flex items-center gap-2">
        <span className="shrink-0 text-muted-foreground">저장소 이름</span>
        <input value={run.picked ? run.picked.split(/[\\/]/).pop() : name} disabled={!!run.picked}
          onChange={(e) => setName(e.target.value)}
          title={run.picked ? '고른 폴더의 이름을 쓴다' : undefined}
          className="h-7 flex-1 rounded-md border border-input bg-background px-2 font-mono text-[12px] text-foreground" />
      </label>
      <textarea value={edit} onChange={(e) => setEdit(e.target.value)} rows={2}
        placeholder="틀린 곳이 있으면 여기에 적고 [고쳐 달라]"
        className="rounded-md border border-input bg-background px-2 py-1.5 text-foreground" />
      <div className="flex justify-end gap-2">
        <Btn disabled={busy || !edit.trim()} onClick={() => void act(() => api.answerIntent(run.id, { confirm: false, edit }))}>고쳐 달라</Btn>
        <Btn tone="primary" disabled={busy || !!intent.unsupported}
          onClick={() => void act(() => api.answerIntent(run.id, { confirm: true, name }))}>맞다</Btn>
      </div>
    </section>
  )
}

function Card({ run, busy, act }: { run: OneshotRun; busy: boolean; act: Act }) {
  const [draft, setDraft] = useState<Record<string, string | number>>(run.profile!)
  const owner = String(draft.github_owner || run.identity?.owner || '')
  const visibility = draft.visibility === 'public' ? '공개' : draft.visibility === 'private' ? '비공개' : '(공개 범위 미정)'
  return (
    <section className="flex flex-col gap-3">
      <div className="grid gap-x-4 gap-y-2 sm:grid-cols-2">
        {Object.entries(run.schema).map(([key, field]) => {
          const why = run.proposed[key]?.why
          const value = draft[key] ?? ''
          const set = (v: string) => setDraft({ ...draft, [key]: v })
          return (
            <label key={key} className="flex flex-col gap-0.5">
              <span className="text-muted-foreground">{LABEL[key] ?? key}{field.asked && ' — 직접 정한다'}</span>
              {field.options ? (
                <select value={String(value)} onChange={(e) => set(e.target.value)}
                  className="h-7 rounded-md border border-input bg-background px-1.5 font-mono text-[12px] text-foreground">
                  {value === '' && <option value="">고르기</option>}
                  {field.options.map((o) => (
                    <option key={o} value={o} disabled={!!field.enabled && !field.enabled.includes(o)}>
                      {o}{field.enabled && !field.enabled.includes(o) ? ' (아직 없음)' : ''}
                    </option>
                  ))}
                </select>
              ) : (
                <input type="text" value={String(value)}
                  onChange={(e) => set(e.target.value)}
                  className="h-7 rounded-md border border-input bg-background px-2 font-mono text-[12px] text-foreground" />
              )}
              {why && <span className="text-[11.5px] text-faint">{why}</span>}
            </label>
          )
        })}
      </div>
      {(run.errors.length > 0 || run.warnings.length > 0) && (
        <ul className="flex flex-col gap-1">
          {run.errors.map((e) => <li key={e} className="text-destructive">{e}</li>)}
          {run.warnings.map((w) => <li key={w} className="text-wait">{w}</li>)}
        </ul>
      )}
      <p className="rounded-md border border-border px-3 py-2">
        확정하면 바깥에 이렇게 남는다: GitHub 에 <span className="font-mono">{owner}/{run.identity?.name}</span> 저장소를 {visibility}로
        만들고 첫 커밋을 main 에 push 한다. 로컬 폴더는 <span className="font-mono">{run.identity?.path}</span>.
      </p>
      <div className="flex justify-end gap-2">
        <Btn disabled={busy} onClick={() => void act(() => api.saveProfile(run.id, draft, false))}
          title="저장하고 문제를 다시 확인한다">저장</Btn>
        <Btn tone="primary" disabled={busy} onClick={() => void act(() => api.saveProfile(run.id, draft, true))}>확정하고 만들기</Btn>
      </div>
    </section>
  )
}

function Steps({ run }: { run: OneshotRun }) {
  const next = run.steps_order.find((s) => !run.steps[s])
  return (
    <ol className="flex flex-col gap-1">
      {run.steps_order.map((s) => (
        <li key={s} className={run.steps[s] ? '' : s === next ? 'font-semibold' : 'text-muted-foreground'}>
          {run.steps[s] ? '✓' : s === next ? '→' : '·'} {STEP[s] ?? s}
        </li>
      ))}
    </ol>
  )
}
