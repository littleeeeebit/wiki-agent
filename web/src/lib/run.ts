// How a question's run (stage 9 of `docs/plans/jev/`) reads on screen. The
// server says what happened as codes; the words are here, once.

import type { RunSummary, Support } from '@/lib/api'

export const FAMILY: Record<string, string> = {
  hub: '허브 규칙', documents: '저장소 문서', memory: '메모리', papers: '논문', research: '조사 기록',
}

/** The four phases a person follows, and which server stages each covers. */
export const PHASES = ['검색', '그래프 확장', '검증', '게시'] as const
export type Phase = (typeof PHASES)[number]
const PHASE_OF: Record<string, Phase> = {
  route: '검색', retrieve: '검색', grade: '검색', assess: '검색', repair_retrieval: '검색', retrieved: '검색',
  ready: '검색', partial: '검색', exhausted: '검색', unavailable: '검색',
  expand: '그래프 확장',
  draft: '검증', verify: '검증', repair: '검증',
  publish: '게시', answer: '게시', explain: '게시',
}
export const phaseOf = (stage?: string): Phase | null => (stage ? PHASE_OF[stage] ?? null : null)

/** What a stage is doing, as a status sentence. */
export const STAGE: Record<string, string> = {
  route: '어디를 찾을지 정하는 중', retrieve: '근거를 찾는 중', expand: '그래프를 따라 넓히는 중',
  grade: '찾은 근거를 가리는 중', assess: '질문을 다 덮는지 보는 중', repair_retrieval: '빠진 것을 다시 찾는 중',
  retrieved: '근거를 모았다', ready: '근거를 모았다', partial: '근거를 일부만 모았다',
  exhausted: '한도 안에서 더 찾지 못했다', unavailable: '판단을 못 해 기본 검색으로 간다',
  draft: '근거로 답을 쓰는 중', verify: '주장마다 근거와 대조하는 중', repair: '어긋난 주장을 고쳐 쓰는 중',
  publish: '확인된 것만 싣는 중', answer: '기본 모드로 답하는 중', explain: '쉬운 설명을 쓰는 중',
}

/** How a run ended — every outcome in words, never only a colour. */
export const OUTCOME: Record<string, string> = {
  complete: '검증됨', partial: '부분 답', abstained: '답 보류', verification_unavailable: '검증 불가',
  unverified: '분석 (미검증)',
  answered: '답함 (검증 없음)', cancelled: '멈춤', failed: '실패', interrupted: '끊김 — 서버가 내려갔다',
}

export const SUPPORT: Record<Support, { mark: string; label: string }> = {
  supported: { mark: '✓', label: '뒷받침함' },
  unverified: { mark: '?', label: '인용했으나 확인 안 됨' },
  conflict: { mark: '≠', label: '다른 근거와 충돌' },
  untrusted: { mark: '!', label: '지시문이 섞여 믿지 않음' },
  not_cited: { mark: '·', label: '찾았으나 인용 안 함' },
}

export const LANE: Record<string, string> = { rrf: '검색', graph: '그래프', direct: '대화', dense: '의미 검색', bm25: '낱말 검색' }

export const ORIGIN: Record<string, string> = {
  deterministic: '문서 구조에서 확정', extracted: '문장에서 추출', observed: '사용 기록에서 관찰',
}

type Note = NonNullable<RunSummary['notes']>[number]

/** One note code in words. */
export function note(n: Note): string {
  switch (n.code) {
    case 'mode_off': return 'Jev 가 꺼져 있어 기본 검색으로 답했다'
    case 'mode_shadow': return 'Jev 는 그림자 모드 — 판단만 기록하고 답은 기본대로 했다'
    case 'settings_problem': return `Jev 설정을 쓸 수 없다 (${String(n.detail ?? '')})`
    case 'sources_disabled': return `설정에서 끈 곳은 찾지 않았다: ${((n.sources as string[]) ?? []).map((s) => FAMILY[s] ?? s).join(', ')}`
    case 'fallback': return `판단을 못 해 기본 검색으로 대신했다${n.reason ? ` (${n.reason})` : ''}`
    case 'source_unavailable': return `찾는 곳 하나를 읽지 못했다 (${String(n.detail ?? '')})`
    case 'truncated': return `한도에 닿아 후보 일부를 잘랐다 (${((n.limits as string[]) ?? []).join(', ')})`
    case 'not_normalized': return `영어로 옮기지 못한 조각 ${String(n.chunks)}개는 빼고 판단했다`
    case 'no_evidence': return '근거를 하나도 찾지 못했다'
    case 'evidence_missing': return `근거를 못 찾은 부분: ${String(n.text || n.requirement)}`
    case 'threshold_not_met': return `확신이 기준에 못 미친 주장 ${String(n.claims)}개는 싣지 않았다`
    case 'verification_unavailable': return `근거 대조를 하지 못했다${n.reason ? ` (${n.reason})` : ''}`
    case 'cancelled': return '멈춤을 눌러 멈췄다. 게시한 것이 없다'
    case 'failed': return `실패했다${n.reason ? ` (${n.reason})` : ''}`
    default: return n.code
  }
}
