import { get, json, post, type ChoiceQuestion } from '@/lib/api'

/** The oneshot run's data past S1 and its requests (`tool/main/oneshot_spec.py` `view`). */

export type Decision = { approve?: boolean; edit?: string }
export type Feature = { id: string; name: string; class: Freq; why: string; endpoints: string[] }
export type Freq = 'every' | 'sometimes' | 'rarely' | 'never'
export type Palette = { id: string; name: string; colors: Record<string, string> }
export type Gate = { round: number; decisions: Record<string, Decision>; marks?: Record<string, string>; palette?: string }
export type Talk = { role: 'owner' | 'agent'; text: string; choices?: ChoiceQuestion | { questions: ChoiceQuestion[] } | null }
export type SpecSummary = {
  repo: string; stage: string; working: string | null; waits: boolean
  stopped: { reason: string; detail: string } | null
}
export type Reading = { round: number; seconds: number | null; opened: string[]; edited: string[] }
export type RunSummary = {
  sentence: string; pr: { number: number; url: string; branch: string } | null; critiques: number; looks: number
  gates: { gate: string; rounds: number; edited: string[]; reading: Reading[] }[]
  spend: { usd?: number; seconds?: number; calls?: number; unknown?: number }
}
export type SpecView = SpecSummary & {
  oneshot: {
    sentence: string; talk?: Talk[]; attempts?: number; critiques?: unknown[]
    r1?: Gate; r2?: Gate; r3?: Gate; front?: { tokens: { palettes: Palette[] } }
    reading?: Record<string, { round: number; seconds: number | null; opened: string[]; edited: string[] }[]>
  }
  sheet?: string; items?: string[]
  board?: { features: Feature[]; first_screen: string | null; frequency: Record<string, Freq> }
  front_sheet?: string; front_items?: string[]
  r3_sheet?: string; r3_items?: string[]
  summary?: RunSummary
}

export const base = (repo: string) => `/api/oneshot/spec/${repo}`
const answer = (r: Response) => json<SpecView>(r, '원샷 명세')
export const oneshotSpecs = () => get('/api/oneshot-specs').then((r) => json<{ specs: SpecSummary[] }>(r, '원샷 명세 목록'))
export const api = {
  view: (repo: string) => get(base(repo)).then(answer),
  say: (repo: string, text: string) => post(`${base(repo)}/say`, { text }).then(answer),
  resume: (repo: string) => post(`${base(repo)}/resume`, {}).then(answer),
  board: (repo: string, frequency: Record<string, Freq>, first_screen: string) =>
    post(`${base(repo)}/board`, { frequency, first_screen }).then(answer),
  decide: (repo: string, gate: string, body: object) => post(`${base(repo)}/${gate}`, body).then(answer),
}

// The stages in order. `talk` and `gate` wait for the owner; the rest run on their own.
export const STAGES: { id: string; label: string; kind: 'talk' | 'gate' | 'auto' }[] = [
  { id: 'spec', label: '요구 대화', kind: 'talk' }, { id: 'design', label: '백엔드 설계', kind: 'auto' },
  { id: 'r1', label: 'R1 백엔드 설계', kind: 'gate' }, { id: 'tests', label: '계약 테스트', kind: 'auto' },
  { id: 'build', label: '백엔드 빌드', kind: 'auto' }, { id: 'review', label: '백엔드 리뷰', kind: 'auto' },
  { id: 'split', label: '기능 나누기', kind: 'auto' }, { id: 'board', label: '기능 보드', kind: 'talk' },
  { id: 'frontend', label: '화면 설계', kind: 'auto' }, { id: 'r2', label: 'R2 화면 설계', kind: 'gate' },
  { id: 'build-front', label: '프런트엔드 빌드', kind: 'auto' }, { id: 'critique', label: '화면 비평', kind: 'auto' },
  { id: 'r3', label: 'R3 완성 화면', kind: 'gate' }, { id: 'pr', label: '풀 리퀘스트', kind: 'auto' },
  { id: 'done', label: '끝', kind: 'auto' },
]
export const stageLabel = (id: string) => STAGES.find((s) => s.id === id)?.label ?? id
export const STOP: Record<string, string> = {
  budget: '한도에 닿았다', block: '모델 답을 쓸 수 없었다', restart: '서버가 다시 켜져 멈췄다', tests: '계약 테스트를 쓰지 못했다',
  gate: '빌드가 검사를 통과하지 못했다', review: '백엔드 리뷰가 두 번 거절했다', critique: '화면 비평이 멈췄다',
}
export const COLUMNS: { id: Freq; label: string }[] = [
  { id: 'every', label: '늘 쓴다' }, { id: 'sometimes', label: '가끔 쓴다' },
  { id: 'rarely', label: '드물게 쓴다' }, { id: 'never', label: '화면에 없다' },
]
