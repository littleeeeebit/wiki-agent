import { get, json, post, type ChoiceQuestion } from '@/lib/api'

/** The oneshot run's data past S1 and its requests (`tool/main/oneshot_spec.py` `view`). */

export type Decision = { approve?: boolean; edit?: string }
// A web app's feature names its served endpoints; an Android app's names its core parts.
export type Feature = { id: string; name: string; class: Freq; why: string; endpoints?: string[]; parts?: string[]; kinds?: string[] }
export type Freq = 'every' | 'sometimes' | 'rarely' | 'never'
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
}
/** One backend review verdict on a HEAD (`oneshot_review.decided`). */
export type Review = {
  verdict: 'approve' | 'refuse'; head: string; ts?: number
  findings: { location: string; severity: string; reason: string; evidence?: string }[]
}
/** One look at the built screens (`oneshot_critique.judged`); `clean` has no high finding. */
export type Critique = {
  look: number; head: string; clean: boolean; ts?: number; kind?: string
  findings: { route: string; viewport: string; state: string; what: string; severity: string }[]
}
/** One phone the run's own ADB server lists (`android_phone.listed`). */
export type Handset = { serial: string; state: string; model: string }
/** What a phone round installed and whether it is gone again (`oneshot_phone.recorded`). */
export type Owned = { serial: string; model: string; packages: string[]; state: string; left: string[]; unsure?: string[] }
/** The phone round before R3 (`oneshot_phone`): `waiting` for the owner's pick, then its last outcome. */
export type PhoneRound = {
  status: string; detail?: string; serial?: string; keep?: boolean; round?: number; model?: string
  findings?: { screen: string; state: string; what: string; severity: string }[]
  timing?: { frames: number | null; janky_percent: number | null; p50_ms: number | null; p90_ms: number | null }
  memory_kb?: number | null
}
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
    stack?: { part: string; choice: string; why: string; status: string }[]
    r1?: Gate; r2?: Gate; r3?: Gate; front?: { tokens: { palettes: Palette[] } }; phone?: PhoneRound
    reading?: Record<string, { round: number; seconds: number | null; opened: string[]; edited: string[] }[]>
  }
  requirements?: { id: string; text: string; status: string; kinds?: string[] }[]
  sheet?: string; items?: string[]; brief?: Brief
  // Several kinds: `kinds` is set, and `first_screen` holds one line for each kind.
  board?: { features: Feature[]; first_screen: string | Record<string, string> | null; frequency: Record<string, Freq>
            kinds?: { id: string; name: string }[] }
  front_sheet?: string; front_items?: string[]; front_brief?: Brief
  r3_sheet?: string; r3_items?: string[]; r3_brief?: Brief
  summary?: RunSummary
  /** A phone round's cleanup that has not finished: shown until that phone is listed again. */
  phone_owned?: Owned[]
}

export const base = (repo: string) => `/api/oneshot/spec/${repo}`
const answer = (r: Response) => json<SpecView>(r, '원샷 명세')
export const oneshotSpecs = () => get('/api/oneshot-specs').then((r) => json<{ specs: SpecSummary[] }>(r, '원샷 명세 목록'))
export const api = {
  view: (repo: string) => get(base(repo)).then(answer),
  say: (repo: string, text: string) => post(`${base(repo)}/say`, { text }).then(answer),
  resume: (repo: string) => post(`${base(repo)}/resume`, {}).then(answer),
  board: (repo: string, frequency: Record<string, Freq>, first_screen: string | Record<string, string>) =>
    post(`${base(repo)}/board`, { frequency, first_screen }).then(answer),
  decide: (repo: string, gate: string, body: object) => post(`${base(repo)}/${gate}`, body).then(answer),
  phones: (repo: string) => get(`${base(repo)}/phone/devices`)
    .then((r) => json<{ devices: Handset[]; owned: Owned[] }>(r, '연결된 휴대폰')),
  phone: (repo: string, serial: string, keep: boolean) => post(`${base(repo)}/phone`, { serial, keep }).then(answer),
  stopPhone: (repo: string) => post(`${base(repo)}/phone/stop`, {}).then(answer),
}

// The stages in order. `talk` and `gate` wait for the owner; the rest run on their own.
export const STAGES: { id: string; label: string; kind: 'talk' | 'gate' | 'auto' }[] = [
  { id: 'spec', label: '요구 대화', kind: 'talk' }, { id: 'design', label: '백엔드 설계', kind: 'auto' },
  { id: 'r1', label: 'R1 백엔드 설계', kind: 'gate' }, { id: 'tests', label: '계약 테스트', kind: 'auto' },
  { id: 'build', label: '백엔드 빌드', kind: 'auto' }, { id: 'review', label: '백엔드 리뷰', kind: 'auto' },
  { id: 'split', label: '기능 나누기', kind: 'auto' }, { id: 'board', label: '기능 보드', kind: 'talk' },
  { id: 'frontend', label: '화면 설계', kind: 'auto' }, { id: 'r2', label: 'R2 화면 설계', kind: 'gate' },
  { id: 'build-front', label: '프런트엔드 빌드', kind: 'auto' }, { id: 'critique', label: '화면 비평', kind: 'auto' },
  // An Android app's only (`stagesOf`): one round on the owner's phone before R3.
  { id: 'phone', label: '휴대폰 회차', kind: 'gate' }, { id: 'r3', label: 'R3 완성 화면', kind: 'gate' }, { id: 'pr', label: '풀 리퀘스트', kind: 'auto' },
  { id: 'done', label: '끝', kind: 'auto' },
]
export const stageLabel = (id: string) => STAGES.find((s) => s.id === id)?.label ?? id
/** The stages a run of these kinds passes through: the phone round is a run's with an Android app among them. */
export const stagesOf = <T extends { id: string }>(stages: readonly T[], kinds: readonly string[] = []) =>
  stages.filter((s) => s.id !== 'phone' || kinds.includes('android'))
export const STOP: Record<string, string> = {
  block: '모델 답을 쓸 수 없었다', restart: '서버가 다시 켜져 멈췄다', tests: '계약 테스트를 쓰지 못했다',
  gate: '빌드가 검사를 통과하지 못했다', review: '백엔드 리뷰가 라운드 상한까지 거절했다', critique: '화면 비평이 멈췄다',
  kind: '이 종류는 여기까지', rekind: '고른 종류로는 만들 수 없는 요구',
}
/** The kinds the owner picks on the start screen (`tool/main/oneshot.py` `Start.kinds`); `soon` ones are shown off. */
export const KINDS: { id: string; label: string; note: string; soon?: string }[] = [
  { id: 'webapp', label: '웹앱', note: '브라우저에서 쓴다' },
  { id: 'android', label: 'Android 앱', note: '휴대폰에 설치한다' },
  { id: 'desktop', label: 'PC 앱', note: 'Windows 에 설치한다', soon: '계획 9e 에서 열린다' },
]
export const kindLabel = (id: string) => KINDS.find((k) => k.id === id)?.label ?? id
export const COLUMNS: { id: Freq; label: string }[] = [
  { id: 'every', label: '늘 쓴다' }, { id: 'sometimes', label: '가끔 쓴다' },
  { id: 'rarely', label: '드물게 쓴다' }, { id: 'never', label: '화면에 없다' },
]
