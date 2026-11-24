import { get, json, post, type ChoiceQuestion } from '@/lib/api'

/** The oneshot run's data past S1 and its requests (`tool/main/oneshot_spec.py` `view`). */

export type Decision = { approve?: boolean; edit?: string }
// A web app's feature names its served endpoints; an Android app's names its core parts.
export type Feature = { id: string; name: string; class: Freq; why: string; endpoints?: string[]; parts?: string[]; kinds?: string[] }
export type Freq = 'every' | 'sometimes' | 'rarely' | 'never'
/** What the owner gives an outside call (`oneshot_needs.shown`): a secret's value never comes back, only `given`. */
export type Need = { id: string; what: string; kind: 'secret' | 'account' | 'tool' | 'file'; env?: string; calls: string[]; given: boolean; leaked?: boolean; unsure?: boolean; launch?: boolean; started?: string[] }
/** A program a PC app's stack runs on this PC, and how a CI runner gets it (`oneshot_profile.tools_of`). */
export type Tool = { name: string; setup?: string | { run: string } | { uses: string; with?: Record<string, string> } }
/** A tool with its whole setup, as the owner confirms it: the setup's kind named, then its value, an action's
 *  inputs included, each a string the server checked (`oneshot_profile.tools_of`), quoted so an empty or spaced
 *  value shows as it is. A command reading `preinstalled` never looks like a preinstalled tool. */
export const toolText = (t: Tool) => `${t.name} — ${t.setup === undefined ? 'CI 설치 방법 없음'
  : typeof t.setup === 'string' ? 'CI 이미지에 있음'
  : 'run' in t.setup ? `명령: ${t.setup.run}`
  : `액션: ${[t.setup.uses, ...Object.entries(t.setup.with ?? {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`)].join(', ')}`}`
export type Palette = { id: string; name: string; colors: Record<string, string> }
export type Gate = { round: number; decisions: Record<string, Decision>; marks?: Record<string, string>; palette?: string }
/** A review's brief for the owner (`oneshot_brief.shown`): always the gate's `source`, which every submission carries;
 *  the brief's sections and confirmation lines (`lines`: key → the items it covers) when one was written for this
 *  gate, else why the review shows its full sheet. */
export type Brief = {
  source: string; sections?: { name: string; text: string }[]; confirm?: { key: string; text: string }[]
  lines?: Record<string, string[]>; failed?: string
  /** R2 and R3: pictures by confirmation line, or by section (R3's `화면`: the first screen before and after). */
  pictures?: Record<string, string[]>
}
export type Talk ={ role: 'owner' | 'agent'; text: string; choices?: ChoiceQuestion | { questions: ChoiceQuestion[] } | null }
/** The three models a run fixes at its start (`tool/main/oneshot.py` `Models`); a run from before them has none. */
export type Models = Record<'spec' | 'build' | 'review', { model: string; effort: string }>
export const ROLE = { spec: '명세', build: '구현', review: '리뷰' } as const
/** An empty model is the role's default: Claude's CLI for writing, the Review Loop settings for review. */
export const modelName = (role: keyof Models, m: { model: string; effort: string }) =>
  `${m.model || (role === 'review' ? '리뷰 설정 따름' : 'Claude 기본')}${m.effort ? ` · ${m.effort}` : ''}`
export type SpecSummary = {
  repo: string; stage: string; working: string | null; waits: boolean; kind?: string; kinds?: string[]
  /** `counted`: the critiques a cap stop reached (`oneshot_critique`). */
  stopped: { reason: string; detail: string; counted?: number } | null
}
export type Reading = { round: number; seconds: number | null; opened: string[]; edited: string[] }
export type RunSummary = {
  sentence: string; pr: { number: number; url: string; branch: string } | null; critiques: number; looks: number
  gates: { gate: string; rounds: number; edited: string[]; reading: Reading[] }[]
  spend: { usd?: number; seconds?: number; calls?: number; unknown?: number }
  /** An Android app's debug APK on a draft prerelease (`oneshot_pr.released`), or why it is not there. */
  apk?: { url?: string; name?: string; error?: string } | null
  /** Each outside call, the gate runs that reached it, and the planned tests CI skips for its secret. */
  calls?: Call[]
  /** Whether the product repository has CI, which skips the tests that need a secret. */
  ci?: boolean
}

/** One outside call on the card (`oneshot_calls.summary`). */
export type Call = { id: string; target: string; runs: number; tests: string[] }
/** One backend review verdict on a HEAD (`oneshot_review.decided`). */
/** One backend review; `added` is the reviewed diff's added lines, `focus` `narrow` when the round read what
 * changed since the last one because the repairs did not converge (`oneshot_review.ruled`). */
export type Review = {
  verdict: 'approve' | 'refuse'; head: string; ts?: number; added?: number; focus?: 'full' | 'narrow'
  findings: { location: string; severity: string; reason: string; evidence?: string; area?: string; device?: unknown }[]
}
/** One look at the built screens (`oneshot_critique.judged`); `clean` has no high finding. */
export type Critique = {
  look: number; head: string; clean: boolean; ts?: number; kind?: string
  findings: { route: string; viewport: string; state: string; what: string; severity: string }[]
}
/** One phone the run's own ADB server lists (`android_phone.listed`). */
export type Handset = { serial: string; state: string; model: string }
/** What a phone round installed and whether it is gone again (`oneshot_phone.recorded`). */
export type Owned = {
  serial: string; model: string; packages: string[]; state: string; left: string[]; unsure?: string[]; dump?: boolean
}

/** What the card says of one record a phone round may have left (`oneshot_phone.owning`): the apps still owed and
 * the hierarchy dump still owed, each whenever it is, so one never hides the other. */
export function ownedNotice(r: Owned): string {
  const apps = r.state === 'unsure'
    ? `이 실행의 앱이 있지만, 이 실행이 설치를 마쳤는지 확인하지 못해 지우지 않았다: ${(r.unsure ?? []).join(', ')}. `
      + '필요 없으면 휴대폰에서 직접 지우고 [휴대폰 찾기] 를 누른다.'
    : ['owning', 'pending'].includes(r.state)
      ? `이 실행이 설치한 앱이 남아 있다: ${(r.left.length ? r.left : r.packages).join(', ')}. `
        + '그 휴대폰을 연결하고 [휴대폰 찾기] 를 누르면 지운다.'
      : ''
  const dump = r.dump
    ? '화면 검사가 읽은 화면 계층 파일이 남아 있다. 비밀 값을 담았을 수 있다. 그 휴대폰을 연결하고 [휴대폰 찾기] 를 누르면 지운다.'
    : ''
  return [apps, dump].filter(Boolean).join(' ')
}
/** The phone round before R3 (`oneshot_phone`): `waiting` for the owner's pick, then its last outcome. */
export type PhoneRound = {
  status: string; detail?: string; serial?: string; keep?: boolean; round?: number; model?: string
  findings?: { screen: string; state: string; what: string; severity: string }[]
  timing?: { frames: number | null; janky_percent: number | null; p50_ms: number | null; p90_ms: number | null }
  memory_kb?: number | null
}
/** What the owner approves for the owner's desktop round (`oneshot_pc.plan`), and the approval it records. */
export type PcPlan = {
  attempt: string; head: string; build: string; freeze: string; checks: string[]; starts: string[]
  captures: { check: string; by: 'round' | 'app'; kind: 'window' | 'surface'; reads: string }[]
}
export type PcApproval = PcPlan & { surface: boolean; ts: number }
/** One check's result in the owner's desktop round (`oneshot_pc.one`, `judged`). */
export type PcResult = { status: 'passed' | 'failed' | 'pending' | 'judge'; why?: string }
/** The owner's desktop round before R3 (`oneshot_pc`): `waiting` for the owner's approval, `running` once approved,
 *  then `passed`, or `waiting` again with the last round's outcome (`last`). */
export type PcRound = {
  status: string; detail?: string; approval?: PcApproval
  /** The approval the running round used up as it started. */
  ran?: PcApproval
  last?: { status: string; detail?: string; results?: Record<string, PcResult> } | null
  /** A round that passed: each check's result, kept beside the R3 hold. */
  results?: Record<string, PcResult>
}
/** One step the owner takes, in one window (`oneshot_measure.step_problem`); the build agent writes them. */
export type Step = { window: string; do: string; see: string; url?: string; copy?: string }
export type MeasureInput = { id: string; what: string; kind: 'secret' | 'text'; env: string; guide: Step[]; given: boolean }
export type Metric = {
  id: string; what: string; unit: string; requirements: string[]; calls: string[]
  pass: { op: string; value: number | boolean }
}
/** One real measurement (`oneshot_measure.measure`): `owner` waits for the owner's verdict, recorded in `owner`. */
export type MeasureRun = {
  n: number; head: string; ts: number; status: 'owner' | 'failed' | 'input' | 'stale'; code?: number | null
  metrics?: (Metric & { value: number | boolean | null; ok: boolean })[]; notes?: string; error?: string
  input_error?: { input: string; why: string }; clips?: string[]; tail?: string
  owner?: { approve: boolean; note: string; ts: number }; comment_error?: string
}
/** The measurement stage (`oneshot_measure.shown`): the plan the build agent committed, or why it does not hold. */
export type MeasureView = {
  plan: { serve: { command: string; health: string }; measure: { command: string; minutes: number }; inputs: MeasureInput[]
          during: Step[]; metrics: Metric[]; clips: boolean } | null
  error: string; runs: MeasureRun[]; pr: { number: number; url: string } | null; head: string
}
/** An area the builder and the review could not both meet (`oneshot_review.conflicted`). */
export type Conflict = {
  key: string; area: string; why: 'replaced' | 'answered'
  findings: { location: string; severity: string; reason: string; evidence?: string; breaks?: string[] }[]
  answers: { round: number; location: string; evidence: string; test: string; breaks: string[] }[]
}
export type Keep = 'review' | 'build' | 'owner'
/** The turn running now (`oneshot.turned`, `oneshot_build.send`): a work turn carries `turn`. */
export type Inflight = { role: string; codex: boolean; sent: number; seen?: number; turn?: string }
export type SpecView = SpecSummary & {
  /** Where the run's work turns go: the screen follows them as maintenance does. */
  worktree?: string
  /** The server's caps on a stage's tries. */
  limits?: { attempts: number; refusals: number; critiques: number }
  oneshot: {
    sentence: string; models?: Models; talk?: Talk[]; attempts?: number; critiques?: Critique[]
    intent?: { kind?: string; kinds?: string[] }
    /** How many `critiques` came before the count against the cap restarted (a resume, an R3 fix). */
    critique_base?: number
    /** Several kinds: where a kind's own count restarted after its cap stop. */
    critique_bases?: Record<string, number>
    inflight?: Inflight | null; reviews?: Review[]; refused?: number; cases?: string[][]
    spend?: { usd?: number; seconds?: number; calls?: number }
    /** The gate runs that reached each outside call (`oneshot_calls.reached`), by call id. */
    calls?: Record<string, number>; design?: { outside?: { id: string; target: string }[] }
    stack?: { part: string; choice: string; why: string; status: string; tools?: Tool[] }[]
    r1?: Gate; r2?: Gate; r3?: Gate; front?: { tokens: { palettes: Palette[] } }; phone?: PhoneRound; pc?: PcRound
    owed?: { id: string; kind: string; source?: string; requirement: string; check: string }[]
    reading?: Record<string, { round: number; seconds: number | null; opened: string[]; edited: string[] }[]>
    conflicts?: Conflict[] | null; rulings?: Record<string, { area: string; keep: Keep; note: string; round: number }>
  }
  measure?: MeasureView
  requirements?: { id: string; text: string; status: string; kinds?: string[] }[]
  sheet?: string; items?: string[]; brief?: Brief
  /** The design's needs, and every `env` Credential Manager holds a secret of this repository under. */
  needs?: Need[]; secrets_kept?: string[]
  // Several kinds: `kinds` is set, and `first_screen` holds one line for each kind.
  board?: { features: Feature[]; first_screen: string | Record<string, string> | null; frequency: Record<string, Freq>
            kinds?: { id: string; name: string }[] }
  front_sheet?: string; front_items?: string[]; front_brief?: Brief
  r3_sheet?: string; r3_items?: string[]; r3_brief?: Brief
  summary?: RunSummary
  /** A phone round's cleanup that has not finished: shown until that phone is listed again. */
  phone_owned?: Owned[]
  /** At `pc`: what the owner approves, the approval while it stands, and the checks that stay pending. */
  pc_plan?: PcPlan; pc_approved?: PcApproval | null; pc_unrun?: string[]
}

/** Whether the stage's own screen shows (`OneshotMeasure` `Owner`), its stop included: a stop at the measurement (a
 *  wrong input, the owner's stop, a restart) shows above its steps, and [실측 시작] starts it again with no [재개]
 *  first. Any other stop shows as every stage's does. */
export const owns = (v: SpecView) => v.stage === 'measure' && !!v.measure
  && (!v.stopped || ['measure', 'measure-input', 'restart'].includes(v.stopped.reason))
  || v.stage === 'conflict' && !v.working && !v.stopped

export const base = (repo: string) => `/api/oneshot/spec/${repo}`
const answer = (r: Response) => json<SpecView>(r, '원샷 명세')
export const oneshotSpecs = () => get('/api/oneshot-specs').then((r) => json<{ specs: SpecSummary[] }>(r, '원샷 명세 목록'))
export const api = {
  view: (repo: string) => get(base(repo)).then(answer),
  say: (repo: string, text: string) => post(`${base(repo)}/say`, { text }).then(answer),
  // A PC app's build part, confirmed exactly as offered (`oneshot_spec.confirm_build`).
  confirmBuild: (repo: string, part: string, choice: string, tools: Tool[]) =>
    post(`${base(repo)}/build`, { part, choice, tools }).then(answer),
  resume: (repo: string) => post(`${base(repo)}/resume`, {}).then(answer),
  board: (repo: string, frequency: Record<string, Freq>, first_screen: string | Record<string, string>) =>
    post(`${base(repo)}/board`, { frequency, first_screen }).then(answer),
  decide: (repo: string, gate: string, body: object) => post(`${base(repo)}/${gate}`, body).then(answer),
  // A need given or taken back on the R1 sheet the owner sees (`source`); a kept secret removed at any stage.
  give: (repo: string, id: string, body: { value?: string; confirmed?: boolean }, source = '') =>
    post(`${base(repo)}/needs/${id}`, { ...body, source }).then(answer),
  takeBack: (repo: string, id: string, source = '') =>
    post(`${base(repo)}/needs/${id}?source=${encodeURIComponent(source)}`, undefined, 'DELETE').then(answer),
  forget: (repo: string, env: string) => post(`${base(repo)}/secrets/${env}`, undefined, 'DELETE').then(answer),
  phones: (repo: string) => get(`${base(repo)}/phone/devices`)
    .then((r) => json<{ devices: Handset[]; owned: Owned[] }>(r, '연결된 휴대폰')),
  phone: (repo: string, serial: string, keep: boolean) => post(`${base(repo)}/phone`, { serial, keep }).then(answer),
  stopPhone: (repo: string) => post(`${base(repo)}/phone/stop`, {}).then(answer),
  // The owner's one-time approval of the round exactly as `plan` showed it, whole (`oneshot_pc.approve`).
  approvePc: (repo: string, plan: PcPlan, surface: boolean) =>
    post(`${base(repo)}/pc/approve`, { plan, surface }).then(answer),
  stopPc: (repo: string) => post(`${base(repo)}/pc/stop`, {}).then(answer),
  // The real measurement (`oneshot_measure`): an input the owner got by following its steps, then the run.
  // Each names the input as shown (`env`, `kind`): a plan rebuilt since answers 409, never a value in the wrong box.
  measureGive: (repo: string, head: string, x: MeasureInput, value: string) =>
    post(`${base(repo)}/measure/inputs/${x.id}`, { value, head, env: x.env, kind: x.kind }).then(answer),
  measureTakeBack: (repo: string, head: string, x: MeasureInput) => post(
    `${base(repo)}/measure/inputs/${x.id}?${new URLSearchParams({ head, env: x.env, kind: x.kind })}`, undefined, 'DELETE')
    .then(answer),
  measureStart: (repo: string, head: string) => post(`${base(repo)}/measure/start`, { head }).then(answer),
  measureStop: (repo: string) => post(`${base(repo)}/measure/stop`, {}).then(answer),
  measureDecide: (repo: string, run: MeasureRun, approve: boolean, note: string) =>
    post(`${base(repo)}/measure/decide`, { head: run.head, n: run.n, approve, note }).then(answer),
  clip: (repo: string, n: number, name: string) => `${base(repo)}/measure/${n}/clips/${encodeURIComponent(name)}`,
  rule: (repo: string, head: string, rulings: Record<string, { keep: Keep; note: string }>) =>
    post(`${base(repo)}/conflict`, { head, rulings }).then(answer),
}

// The stages in order. `talk` and `gate` wait for the owner; the rest run on their own.
export const STAGES: { id: string; label: string; kind: 'talk' | 'gate' | 'auto' }[] = [
  { id: 'spec', label: '요구 대화', kind: 'talk' }, { id: 'design', label: '백엔드 설계', kind: 'auto' },
  { id: 'r1', label: 'R1 백엔드 설계', kind: 'gate' }, { id: 'tests', label: '계약 테스트', kind: 'auto' },
  { id: 'build', label: '백엔드 빌드', kind: 'auto' }, { id: 'measure', label: '진짜 환경 실측', kind: 'gate' },
  { id: 'review', label: '백엔드 리뷰', kind: 'auto' }, { id: 'conflict', label: '주인 결정', kind: 'gate' },
  { id: 'split', label: '기능 나누기', kind: 'auto' }, { id: 'board', label: '기능 보드', kind: 'talk' },
  { id: 'frontend', label: '화면 설계', kind: 'auto' }, { id: 'r2', label: 'R2 화면 설계', kind: 'gate' },
  { id: 'build-front', label: '프런트엔드 빌드', kind: 'auto' }, { id: 'critique', label: '화면 비평', kind: 'auto' },
  // An Android app's only (`stagesOf`): one round on the owner's phone before R3.
  { id: 'phone', label: '휴대폰 회차', kind: 'gate' },
  // A PC app's only: its `owner` checks on the owner's own desktop before R3.
  { id: 'pc', label: '주인 화면 회차', kind: 'gate' },
  { id: 'r3', label: 'R3 완성 화면', kind: 'gate' }, { id: 'pr', label: '풀 리퀘스트', kind: 'auto' },
  { id: 'done', label: '끝', kind: 'auto' },
]
// The stages a core runs through, as its sheet names it (`oneshot_sheet.core_sheet`): a web app alone has a backend.
const CORE_LABELS: Record<string, string> = { design: '핵심 설계', r1: 'R1 핵심 설계', build: '핵심 빌드', review: '핵심 리뷰' }
const cored = (kinds: readonly string[]) => kinds.length > 0 && !(kinds.length === 1 && kinds[0] === 'webapp')
export const stageLabel = (id: string, kinds: readonly string[] = []) =>
  (cored(kinds) && CORE_LABELS[id]) || (STAGES.find((s) => s.id === id)?.label ?? id)
/** The stages a run of these kinds passes through, each under its name for them: the phone round is a run's with
 *  an Android app among them, the owner's desktop round one's with a PC app, and a core's stages are named for
 *  the core. */
export const stagesOf = <T extends { id: string; label: string }>(stages: readonly T[], kinds: readonly string[] = []) =>
  stages.filter((s) => (s.id !== 'phone' || kinds.includes('android')) && (s.id !== 'pc' || kinds.includes('desktop')))
    .map((s) => ({ ...s, label: (cored(kinds) && CORE_LABELS[s.id]) || s.label }))
export const STOP: Record<string, string> = {
  block: '모델 답을 쓸 수 없었다', restart: '서버가 다시 켜져 멈췄다', tests: '계약 테스트를 쓰지 못했다',
  gate: '빌드가 검사를 통과하지 못했다', review: '백엔드 리뷰가 라운드 상한까지 거절했다', critique: '화면 비평이 멈췄다',
  kind: '이 종류는 여기까지', rekind: '고른 종류로는 만들 수 없는 요구', toolchain: '쓰기로 한 도구가 이 PC 에 없다',
  measure: '실측이 멈췄다', 'measure-input': '실측에 넣은 값이 틀렸다', fake: '고정한 계약 테스트에 가짜가 있다',
}
/** The kinds the owner picks on the start screen (`tool/main/oneshot.py` `Start.kinds`); `soon` ones are shown off,
 *  and a `host` one only once the server says this host builds it (`oneshot_profile.HOST_ONLY`). */
export const KINDS: { id: string; label: string; note: string; soon?: string; host?: boolean }[] = [
  { id: 'webapp', label: '웹앱', note: '브라우저에서 쓴다' },
  { id: 'android', label: 'Android 앱', note: '휴대폰에 설치한다' },
  { id: 'desktop', label: 'PC 앱', note: 'Windows 에 설치한다', host: true },
]
export const kindLabel = (id: string) => KINDS.find((k) => k.id === id)?.label ?? id
export const COLUMNS: { id: Freq; label: string }[] = [
  { id: 'every', label: '늘 쓴다' }, { id: 'sometimes', label: '가끔 쓴다' },
  { id: 'rarely', label: '드물게 쓴다' }, { id: 'never', label: '화면에 없다' },
]
