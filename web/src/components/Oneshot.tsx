import { useEffect, useState } from 'react'
import { Btn } from '@/components/Modal'
import { Fault, GO, Halt, Split, Title, Top } from '@/components/OneshotSpec'
import { Toolbar } from '@/components/Toolbar'
import { browseFolder, get, getLoopSettings, getOptions, json, post, type Options } from '@/lib/api'
import { remote } from '@/lib/mobile'
import { KINDS, kindLabel, ROLE, type Models } from '@/lib/oneshot'
import { cn } from '@/lib/utils'

type OneshotField = {
  options?: string[]; enabled?: string[]; text?: boolean; asked?: boolean; default: string
}
export type OneshotRun = {
  id: string
  state: 'intake' | 'environment' | 'bootstrap' | 'spec'
  working: string | null
  stopped: { reason: 'block' | 'folder' | 'github' | 'connect' | 'restart' | 'kind' | 'kinds'; detail: string; state: string } | null
  sentence: string
  models?: Models
  picked: string
  intent: { restated: string; kind: string; kinds?: string[]; name: string; users: string; first_screen: string
    unsupported?: boolean } | null
  profile: Record<string, string | number> | null
  proposed: Record<string, { value: string | number; why: string }>
  identity: { path: string; name: string; owner: string } | null
  steps: Record<string, number>
  spec: { repo: string; id: string } | null
  errors: string[]
  warnings: string[]
  schema: Record<string, OneshotField>
  steps_order: string[]
  devices?: Record<string, { label: string; width: number; height: number; dpi: number }>
  toolchain?: Toolchain
}
type Toolchain = {
  sdk: string
  pieces: { key: string; label: string; path: string; version: string }[]
  missing: string[]
  job: { state: 'idle' | 'running' | 'done' | 'failed'; step: string; error: string; log: string[] }
}

const answer = (r: Response) => json<OneshotRun>(r, '원샷')
export const api = {
  browseFolder,
  oneshotRuns: () => get('/api/oneshot').then((r) => json<{ runs: OneshotRun[] }>(r, '원샷 목록')),
  oneshotRun: (id: string) => get(`/api/oneshot/${id}`).then(answer),
  startOneshot: (sentence: string, path: string, models: Models, kinds: string[]) =>
    post('/api/oneshot', { sentence, path, models, kinds }).then(answer),
  answerIntent: (id: string, body: { confirm: boolean; edit?: string; name?: string }) =>
    post(`/api/oneshot/${id}/intent`, body).then(answer),
  saveProfile: (id: string, values: Record<string, string | number>, confirm: boolean) =>
    post(`/api/oneshot/${id}/profile`, { values, confirm }).then(answer),
  resumeOneshot: (id: string, adopt = false) => post(`/api/oneshot/${id}/resume`, { adopt }).then(answer),
  toolchain: () => get('/api/oneshot-android/toolchain').then((r) => json<Toolchain>(r, 'Android 도구')),
  installToolchain: () => post('/api/oneshot-android/toolchain/install', {}).then((r) => json<Toolchain>(r, 'Android 도구 설치')),
}

/** A oneshot run's S1 parts (`docs/plans/oneshot/2-bootstrap.md`), shown in the oneshot window:
 *  the intake form, the restatement beside the owner's sentence, the
 *  environment card and the bootstrap's progress. Every stop shows its reason
 *  and `[재개]`. The server owns every rule; these parts only show and ask. */

const LABEL: Record<string, string> = {
  os_now: '지금 쓰는 OS', os_later: '다른 OS 에서도', auth: '로그인',
  visibility: 'GitHub 공개 범위', license: '라이선스', ci: 'CI',
  ui_language: '화면 언어', docs_language: '문서 언어', responsive: '화면 크기', device: '기준 기기', llm: 'LLM 사용',
  github_owner: 'GitHub 소유자',
}
const STEP: Record<string, string> = {
  recheck: '이름 다시 확인', folder: '폴더', files: '기본 파일', commit: '첫 커밋', register: '앱에 연결',
  remote: 'GitHub 저장소', origin: 'origin 연결', publish: 'main push', spec: '명세 작업 공간',
}
export const STOP: Record<string, string> = {
  block: '모델 답을 쓸 수 없었다', folder: '폴더나 이름 문제', github: 'GitHub 문제', connect: '연결을 확인해야 한다',
  restart: '서버가 다시 켜져 멈췄다', kind: '이 종류는 여기까지', kinds: '이 실행은 이어 갈 수 없다',
}
const WORKING: Record<string, string> = {
  intake: '하고 싶은 것을 다시 정리하는 중…', profile: '환경 카드를 채우는 중…', bootstrap: '저장소를 만드는 중…',
}

const error = (err: unknown) => String(err instanceof Error ? err.message : err)

export type Act = (fn: () => Promise<OneshotRun>) => Promise<void>

/** One request at a time: what the server answers goes to `done`, a refusal to `fault`. */
export function useAct(done: (run: OneshotRun) => void) {
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState(false)
  const act: Act = async (fn) => {
    setFault('')
    setBusy(true)
    try {
      done(await fn())
    } catch (err) {
      setFault(error(err))
    } finally {
      setBusy(false)
    }
  }
  return { fault, setFault, busy, act }
}

export function Intake({ busy, act }: { busy: boolean; act: Act }) {
  const [sentence, setSentence] = useState('')
  const [path, setPath] = useState('')
  const [kinds, setKinds] = useState<string[]>([])   // nothing preselected: the owner picks every kind
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
      <form className="flex flex-col gap-5" onSubmit={(e) => { e.preventDefault(); void act(() => api.startOneshot(sentence, path, models, kinds)) }}>
        <label className="flex flex-col gap-1.5">
          <span className="font-semibold">무엇을 만들고 싶은가</span>
          <textarea value={sentence} onChange={(e) => setSentence(e.target.value)} rows={4} maxLength={2000}
            placeholder="한두 문장이면 된다. 예: 동네 독서 모임의 책 투표와 모임 일정을 한곳에서 관리하고 싶다"
            className={`${field} py-2 text-[15px]`} />
        </label>
        <fieldset className="flex flex-col gap-1.5">
          <legend className="font-semibold">어떤 형태로 만드나 — 여러 개를 골라도 된다</legend>
          <div className="flex flex-wrap gap-x-5 gap-y-2">
            {KINDS.map((k) => (
              <label key={k.id} className={cn('flex items-center gap-2', k.soon && 'text-muted-foreground')}>
                <input type="checkbox" checked={kinds.includes(k.id)} disabled={busy || !!k.soon}
                  onChange={(e) => setKinds((ks) => e.target.checked ? [...ks, k.id] : ks.filter((x) => x !== k.id))} />
                <span className="font-medium">{k.label}</span>
                <span className="text-[12px] text-muted-foreground">{k.soon ?? k.note}</span>
              </label>
            ))}
          </div>
          {kinds.length > 1 && (
            <p className="text-[12px] text-muted-foreground">여러 형태는 핵심을 함께 쓰는 저장소 하나로 만들고, 풀 리퀘스트 하나에 형태마다의 README 와 배포 파일을 담는다.</p>
          )}
        </fieldset>
        <div className="flex flex-col gap-1.5">
          <span className="font-semibold">폴더</span>
          {typing ? (
            <input aria-label="비어 있는 폴더 경로" value={path} onChange={(e) => setPath(e.target.value)}
              placeholder="비어 있는 폴더 경로 — 비우면 작업 공간에 새로 만든다"
              className={`${field} h-9 font-mono text-[12.5px]`} />
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
          <Btn type="submit" tone="primary" disabled={busy || !sentence.trim() || !kinds.length}>{busy ? '시작하는 중…' : '시작'}</Btn>
        </div>
      </form>
    </>
  )
}

/** A run before S2: its stop, what runs, or what it asks, each in its own layout. Past the bootstrap the
 *  window opens its spec instead. */
export function Body({ run, busy, act, fault }: { run: OneshotRun; busy: boolean; act: Act; fault: string }) {
  if (run.stopped || run.working) {
    return (
      <Top>
        <Fault text={fault} />
        {run.stopped ? <Stopped run={run} busy={busy} act={act} />
          : <p role="status" className="text-muted-foreground">{WORKING[run.working!] ?? run.working}</p>}
        {run.state === 'bootstrap' && <Steps run={run} />}
      </Top>
    )
  }
  if (run.state === 'intake' && run.intent) return <Restated run={run} busy={busy} act={act} fault={fault} />
  if (run.state === 'environment' && run.profile) return <Card run={run} busy={busy} act={act} fault={fault} />
  if (run.state === 'bootstrap') {
    return (
      <Split label="저장소 만들기" sideLabel="만들어지는 것" side={<>
        <Title><span>만들어지는 것</span></Title>
        <Facts run={run} />
      </>}>
        <Top><Fault text={fault} /><Steps run={run} /></Top>
      </Split>
    )
  }
  return <Top><Fault text={fault} /></Top>
}

function Stopped({ run, busy, act }: { run: OneshotRun; busy: boolean; act: Act }) {
  const s = run.stopped!
  return (
    <Halt title={STOP[s.reason] ?? s.reason} detail={s.detail}>
      {/* A card an earlier version stopped at `kind`: its resume opens the card again for the owner's confirm.
          A run stopped at `kinds` has no resume: the owner starts a new run. */}
      {s.reason !== 'kinds' && (
        <Btn tone="wait" className={GO} disabled={busy} onClick={() => void act(() => api.resumeOneshot(run.id))}>
          {s.reason === 'kind' ? '카드로 돌아가기' : '재개'}
        </Btn>
      )}
      {s.reason === 'github' && (
        <Btn disabled={busy} onClick={() => void act(() => api.resumeOneshot(run.id, true))}
          title="GitHub 에 이미 있는 같은 이름의 빈 저장소를 이 실행의 것으로 쓴다. 커밋이 하나라도 있으면 서버가 거절한다">
          이어 쓰기
        </Btn>
      )}
    </Halt>
  )
}

/** Where the run will land: the repository, its folder and who can see it. */
function Facts({ run, draft }: { run: OneshotRun; draft?: Record<string, string | number> }) {
  const v = draft ?? run.profile ?? {}
  const owner = String(v.github_owner || run.identity?.owner || '')
  const visibility = v.visibility === 'public' ? '공개' : v.visibility === 'private' ? '비공개' : '정하지 않음'
  return (
    <dl className="grid grid-cols-[6rem_minmax(0,1fr)] gap-x-3 gap-y-2.5">
      <dt className="text-muted-foreground">GitHub</dt><dd className="break-all font-mono text-[12.5px]">{owner}/{run.identity?.name}</dd>
      <dt className="text-muted-foreground">공개 범위</dt><dd>{visibility}</dd>
      <dt className="text-muted-foreground">로컬 폴더</dt><dd className="break-all font-mono text-[12.5px]">{run.identity?.path}</dd>
      <dt className="text-muted-foreground">첫 커밋</dt><dd>main 에 push 한다</dd>
    </dl>
  )
}

const field = 'rounded-md border border-input bg-background px-3 text-foreground max-[560px]:text-base'

/** The owner's sentence in large type beside what the agent understood of it. */
function Restated({ run, busy, act, fault }: { run: OneshotRun; busy: boolean; act: Act; fault: string }) {
  const intent = run.intent!
  const [name, setName] = useState(intent.name)
  const [edit, setEdit] = useState('')
  return (
    <Top wide>
      <Fault text={fault} />
      <div className="grid max-w-[80rem] grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] items-start gap-12 max-[1100px]:grid-cols-1 max-[1100px]:gap-6">
        <section aria-label="내가 쓴 말" className="flex flex-col gap-3">
          <Title><span>내가 쓴 말</span></Title>
          <p className="whitespace-pre-wrap break-keep font-heading text-[22px] leading-snug text-balance max-[560px]:text-[18px]">{run.sentence}</p>
        </section>
        <section aria-label="에이전트가 이해한 것" className="flex flex-col gap-4 rounded-xl border border-border bg-card p-6 max-[560px]:p-4">
          <Title><span>에이전트가 이해한 것</span></Title>
          <p className="whitespace-pre-wrap">{intent.restated}</p>
          <dl className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-3 gap-y-2">
            <dt className="text-muted-foreground">쓰는 사람</dt><dd>{intent.users}</dd>
            <dt className="text-muted-foreground">첫 화면</dt><dd>{intent.first_screen}</dd>
            <dt className="text-muted-foreground">형태</dt>
            <dd>{(intent.kinds ?? [intent.kind]).map(kindLabel).join(' · ')} <span className="text-muted-foreground">— 내가 고른 것</span></dd>
          </dl>
          {intent.unsupported && (
            <p className="rounded-md bg-wait/10 px-3 py-2">
              <span className="font-mono">{intent.kind}</span> 는 이 버전에서 만들 수 없다 — 지금은 웹앱과 Android 앱만. 새 실행을 시작해 형태를 골라라.
            </p>
          )}
          <label className="flex flex-col gap-1.5">
            <span className="text-muted-foreground">저장소 이름</span>
            <input value={run.picked ? run.picked.split(/[\\/]/).pop() : name} disabled={!!run.picked}
              onChange={(e) => setName(e.target.value)}
              title={run.picked ? '고른 폴더의 이름을 쓴다' : undefined}
              className={`${field} h-9 font-mono text-[12.5px]`} />
          </label>
          <textarea aria-label="고칠 곳" value={edit} onChange={(e) => setEdit(e.target.value)} rows={2}
            placeholder="틀린 곳이 있으면 여기에 적고 [고쳐 달라]" className={`${field} py-2`} />
          <div className="flex justify-end gap-2">
            <Btn className={GO} disabled={busy || !edit.trim()} onClick={() => void act(() => api.answerIntent(run.id, { confirm: false, edit }))}>고쳐 달라</Btn>
            <Btn tone="wait" className={GO} disabled={busy || !!intent.unsupported}
              onClick={() => void act(() => api.answerIntent(run.id, { confirm: true, name }))}>맞다</Btn>
          </div>
        </section>
      </div>
    </Top>
  )
}

/** The environment card beside what confirming it makes outside: a repository, a folder, a push. */
function Card({ run, busy, act, fault }: { run: OneshotRun; busy: boolean; act: Act; fault: string }) {
  const [draft, setDraft] = useState<Record<string, string | number>>(run.profile!)
  return (
    <Top wide>
      <Fault text={fault} />
      <div className="grid max-w-[80rem] grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] items-start gap-10 max-[1100px]:grid-cols-1 max-[1100px]:gap-6">
      <section aria-label="환경 카드" className="flex flex-col gap-5 rounded-xl border border-border bg-card p-6 max-[560px]:p-4">
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 max-[560px]:grid-cols-1">
        {Object.entries(run.schema).map(([key, field]) => {
          const why = run.proposed[key]?.why
          const value = draft[key] ?? ''
          const set = (v: string) => setDraft({ ...draft, [key]: v })
          return (
            <label key={key} className="flex flex-col gap-1">
              <span className="text-muted-foreground">{LABEL[key] ?? key}{field.asked && ' — 직접 정한다'}</span>
              {field.options ? (
                <select value={String(value)} onChange={(e) => set(e.target.value)}
                  className="h-9 rounded-md border border-input bg-background px-2 font-mono text-[12.5px] text-foreground max-[560px]:text-base">
                  {value === '' && <option value="">고르기</option>}
                  {field.options.map((o) => (
                    <option key={o} value={o} disabled={!!field.enabled && !field.enabled.includes(o)}>
                      {key === 'device' && run.devices?.[o]
                        ? `${run.devices[o].label} — ${run.devices[o].width}×${run.devices[o].height}, ${run.devices[o].dpi} dpi`
                        : o}{field.enabled && !field.enabled.includes(o) ? ' (아직 없음)' : ''}
                    </option>
                  ))}
                </select>
              ) : (
                <input type="text" value={String(value)}
                  onChange={(e) => set(e.target.value)}
                  className="h-9 rounded-md border border-input bg-background px-3 font-mono text-[12.5px] text-foreground max-[560px]:text-base" />
              )}
              {why && <span className="text-[12px] text-faint">{why}</span>}
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
      <div className="flex justify-end gap-2">
        <Btn className={GO} disabled={busy} onClick={() => void act(() => api.saveProfile(run.id, draft, false))}
          title="저장하고 문제를 다시 확인한다">저장</Btn>
        <Btn tone="wait" className={GO} disabled={busy} onClick={() => void act(() => api.saveProfile(run.id, draft, true))}>{run.toolchain ? '확정' : '확정하고 만들기'}</Btn>
      </div>
      </section>
      {run.toolchain ? <Tools initial={run.toolchain} busy={busy} /> : (
        <section aria-label="확정하면 바깥에 남는 것" className="flex flex-col gap-4 rounded-xl border border-border p-6 max-[560px]:p-4">
          <Title><span>확정하면 바깥에 이렇게 남는다</span></Title>
          <Facts run={run} draft={draft} />
        </section>
      )}
      </div>
    </Top>
  )
}

/** An `android` card's toolchain: each piece found or missing, and the owner's one install action. */
function Tools({ initial, busy }: { initial: Toolchain; busy: boolean }) {
  const [tools, setTools] = useState(initial)
  const [fault, setFault] = useState('')
  const running = tools.job.state === 'running'
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => { api.toolchain().then(setTools).catch(() => {}) }, 3000)
    return () => clearInterval(timer)
  }, [running])
  const press = () => {
    setFault('')
    api.installToolchain().then(setTools).catch((err) => setFault(error(err)))
  }
  return (
    <section aria-label="Android 도구" className="flex flex-col gap-4 rounded-xl border border-border p-6 max-[560px]:p-4">
      <Title><span>Android 도구</span></Title>
      <ul className="flex flex-col gap-1.5">
        {tools.pieces.map((p) => (
          <li key={p.key} className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-x-2">
            <span aria-hidden className={p.path ? 'text-progress' : 'text-destructive'}>{p.path ? '✓' : '×'}</span>
            <span>{p.label}{p.version && <span className="text-muted-foreground"> {p.version}</span>}
              {!p.path && <span className="text-muted-foreground"> — 없음</span>}</span>
          </li>
        ))}
      </ul>
      {running && <p role="status" className="text-muted-foreground">{tools.job.step || '설치하는 중…'}</p>}
      {tools.job.state === 'failed' && <p className="text-destructive">{tools.job.error}</p>}
      <Fault text={fault} />
      {tools.missing.length > 0 && (
        <>
          <p className="text-[12.5px] text-muted-foreground">
            [도구 설치]를 누르면 JDK 17 과 Android SDK(플랫폼 도구, 에뮬레이터, 플랫폼, 빌드 도구, 시스템 이미지)를
            {' '}<span className="break-all font-mono">{tools.sdk}</span> 옆에 수 GB 내려받는다. 다른 폴더나 PATH 는
            바꾸지 않는다. 누르는 것은 Android SDK 라이선스에 동의하는 것이다.
          </p>
          <div className="flex justify-end">
            <Btn tone="wait" className={GO} disabled={busy || running} onClick={press}>{running ? '설치하는 중…' : '도구 설치'}</Btn>
          </div>
        </>
      )}
    </section>
  )
}

function Steps({ run }: { run: OneshotRun }) {
  const next = run.steps_order.find((s) => !run.steps[s])
  return (
    <ol aria-label="저장소 만들기 단계" className="flex flex-col">
      {run.steps_order.map((s) => (
        <li key={s} aria-current={s === next ? 'step' : undefined}
          className={cn('grid grid-cols-[1.5rem_minmax(0,1fr)] border-b border-border py-2.5 last:border-b-0',
            run.steps[s] ? '' : s === next ? 'font-semibold' : 'text-muted-foreground')}>
          <span aria-hidden className={run.steps[s] ? 'text-progress' : s === next ? 'text-progress' : 'text-faint'}>
            {run.steps[s] ? '✓' : s === next ? '→' : '·'}</span>
          <span>{STEP[s] ?? s}</span>
        </li>
      ))}
    </ol>
  )
}
