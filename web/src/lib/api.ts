// Every point of contact with the backend. Nothing else calls fetch.

export type Channel = {
  id: string
  label: string
  blurb: string
  live: boolean
  repo: string
  remote: string
  model: string
  model_name: string
  effort: string
}

/** A repository's own documents, as `repo_graph.picture` drew them. */
export type DocNode = {
  id: string
  kind: 'doc' | 'page' | 'module' | 'decision'
  title: string
  chars: number
  severity?: string
  triggers?: string[]
  injected?: boolean
}

/** A hub rule in `graph.build`'s shape — only the fields the map reads. */
export type RuleNode = {
  id: string
  label: string
  scope: string
  severity: string
  headline: string
  rule: string
  chars: number
  injected: boolean
  triggers: string[]
  layer: number
  status: Record<string, string>
}

/** One repository's map in two layers (`GET /api/graph?repo=`). */
export type MapData = {
  repo: string
  /** The repository is the hub: its hub layer is `graph.json` unchanged. */
  hub: boolean
  /** The hub's name, for reading a rule page's file. */
  wiki: string
  layers: {
    repo: { nodes: DocNode[]; edges: { a: string; b: string }[] }
    hub: { nodes: RuleNode[]; edges: { a: string; b: string; kind: 'link' | 'co' }[];
      ladder: { n: number; title: string }[] }
  }
  metrics: { pages: number; orphans: number; lint: number }
}

/** A repository and how far the wiki is attached to it (`tool/main/connect.py`). */
export type Project = {
  id: string
  path: string
  wired: boolean
  state: '연결 완료' | '일부' | '미연결'
  missing: string[]
  notes: string[]
  probing: boolean
  survey?: SurveyProgress | null
}
export type SurveyProgress = {
  sid?: string
  state?: string
  turn?: number
  turns?: number
  label?: string
  tokens?: number
  limit?: number
  why?: string
  reason?: string
}
export type Choice = { id: string; label: string; note: string }
export type Options = {
  projects: Project[]
  models: (Choice & { efforts?: Choice[]; is_default?: boolean })[]
  efforts: Choice[]
  codex_error: string
}

export type Turn = {
  ts: number
  /** `result`: a spec's outcome told back into the `next` conversation. */
  role: 'user' | 'assistant' | 'result'
  text: string
  /** What the person said, when the CLI was sent more — the gathered
   *  materials, or the results in front of it. */
  said?: string
  propose?: boolean
  blocks?: Block[]
  spec?: string
  source?: string
  error?: string
  verification?: Verification
  simple_text?: string
  simple_error?: string
  simple_meta?: { ms?: number; cost_usd?: number }
  ms?: number
  cost_usd?: number
  model?: string
  session_id?: string
  tokens?: Tokens
  /** The `knowledge.Run` this turn belongs to (stage 9). */
  run_id?: string
  cancelled?: boolean
}

export type Tokens = {
  in?: number
  out?: number
  cache_read?: number
  cache_write?: number
  reasoning?: number
}

/** How an answer was checked before it was published (stage 7 of
 *  `docs/plans/jev/`): only what the server's `verified-answer/1` says, never
 *  guessed from the text. An answer without one was not checked. */
export type Verification = {
  status: 'complete' | 'partial' | 'abstained' | 'verification_unavailable' | 'unverified'
  verified: boolean
  degraded: boolean
  /** The host model, not Jev, settled part of it where Jev was not confident: never shown as verified. */
  host_checked?: boolean
  reason: string | null
  missing_requirements: { id: string; text: string }[]
  conflicts: { claim_id: string; evidence: string[] }[]
  citations: { cite: string }[]
}

export type Ev = {
  kind: 'hits' | 'delta' | 'tool' | 'done' | 'error' | 'blocks' | 'step' | 'cancelled'
    | 'simple_start' | 'simple_delta' | 'simple_done' | 'simple_error'
  text: string
  /** Every event of a run carries its run and its place in it. */
  run_id?: string
  seq?: number
  /** A `step`: where the run is — `retrieve`, `expand`, `verify`, `publish`… */
  stage?: string
  status?: string
  code?: string
  pages?: string[]
  blocks?: Block[]
  verification?: Verification
  ms?: number
  error?: boolean
  session_id?: string
  model?: string
  cost_usd?: number
  tokens?: Tokens
}

export type Kind = '교정' | '재입력' | '부분수행' | '되돌림'
export const KINDS: Kind[] = ['교정', '재입력', '부분수행', '되돌림']

export type Peek = {
  path: string
  start: number
  line: number
  total: number
  lines: string[]
}

// The project the screen shows, sent with every request. The server's
// selection is one for all windows; a screen that missed another window's
// switch is refused (409) instead of writing into the other project, and the
// refusal names where the server is, so the screen can follow.
let claimed = ''
// Until the first channel list names the project, a request would go out
// with no `X-Project` and land in whichever project the server is on. So
// everything but that list waits for it; the server refuses a write without it.
let known: () => void = () => {}
const claimedOnce = new Promise<void>((resolve) => (known = resolve))

export const claim = (name: string) => {
  claimed = name
  if (name) known()
}

async function scoped(url: string, owner?: string): Promise<Record<string, string>> {
  if (!url.startsWith('/api/channels')) await claimedOnce
  const repo = owner ?? claimed
  return repo ? { 'X-Project': encodeURIComponent(repo) } : {}
}

/** The project the server is on, when it refused this screen's. Announced as
 *  a window event so the one place that follows the server hears it. */
function moved(res: Response) {
  const to = res.headers.get('X-Project-Moved')
  if (to) window.dispatchEvent(new CustomEvent('project-moved', { detail: decodeURIComponent(to) }))
}

const get = async (url: string, owner?: string) => fetch(url, { headers: await scoped(url, owner) })

async function json<T>(res: Response, what: string): Promise<T> {
  if (!res.ok) {
    moved(res)
    let detail = ''
    try {
      detail = (await res.json()).detail ?? ''
    } catch {
      // With a body that is not JSON, the status code is all there is to say.
    }
    throw new Error(detail || `${what} — 서버가 ${res.status} 로 답했다`)
  }
  return res.json()
}

const post = async (url: string, body?: unknown, method = 'POST', owner?: string) =>
  fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json', ...(await scoped(url, owner)) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })

export const getChannels = () =>
  get('/api/channels').then((r) => json<Channel[]>(r, '채널 목록'))

export const getGraph = (repo: string) =>
  get(`/api/graph?${new URLSearchParams({ repo })}`).then((r) => json<MapData>(r, '지도'))

export const getOptions = () =>
  get('/api/options').then((r) => json<Options>(r, '고를 것'))

export const getLog = (id: string, legacy = false) =>
  get(`/api/log/${id}?legacy=${legacy}`).then((r) => json<Turn[]>(r, '기록'))

/** What a clear does with the conversation: kept as a transcript and memory
 *  pair in the repository's `.wiki/memory/`, or deleted from the record. */
export type Keep = 'memory' | 'delete'
export type Kept = { ok: boolean; raw?: string; memory?: string; fault?: string }

export const reset = (id: string, keep: Keep) =>
  post(`/api/reset/${id}`, { keep }).then((r) => json<Kept>(r, '문맥 지우기'))

/** Every channel shares the project; conversations are kept per project and
 *  per channel. */
export const setConfig = (id: string, cfg: { repo: string; model: string; effort: string }) =>
  post(`/api/config/${id}`, cfg).then((r) => json<{ kept: boolean; switched: boolean; repo: string }>(r, '설정'))

/** Name one category at the moment it went wrong, in the census's format. */
export const mark = (
  id: string,
  body: { kind: Kind; user_text: string; assistant_text: string; session_id?: string },
) => post(`/api/mark/${id}`, body).then((r) => json<{ ok: boolean; total: number }>(r, '표시'))

/** The place a cited `path:line` points at. */
export const peek = (repo: string, path: string, line: number) =>
  get(
    `/api/file?${new URLSearchParams({ repo, path, line: String(line), around: '25' })}`,
  ).then((r) => json<Peek>(r, '파일'))

/** Render what a screen is about to show. Failure returns the original.
 *
 *  It calls the phase-one translator directly. Identifiers, paths, links and
 *  config values are lifted out before the request, so there is nothing for
 *  this side to mask. */
export const render = (texts: string[], direction: 'en->ko' | 'ko->en' = 'en->ko') =>
  post('/api/translate', { texts, direction }).then((r) =>
    json<{ texts: string[] }>(r, '번역'),
  )

/** A verified answer's rendering. A translation that changed a number or an
 *  identifier comes back as the original, with a status saying so. */
export const renderChecked = (texts: string[]) =>
  post('/api/translate', { texts, direction: 'en->ko', checked: true }).then((r) =>
    json<{ texts: string[]; statuses?: string[]; off?: boolean }>(r, '번역'),
  )

/** For more than one request holds. Order and count survive intact.
 *
 *  The splitting lives here alone. Split at each call site and changing the
 *  limit means editing as many places as there are screens. */
const BATCH = 40

export async function renderAll(
  texts: string[],
  direction: 'en->ko' | 'ko->en' = 'en->ko',
): Promise<string[]> {
  const out: string[] = []
  for (let i = 0; i < texts.length; i += BATCH) {
    const { texts: done } = await render(texts.slice(i, i + BATCH), direction)
    out.push(...done)
  }
  return out
}

/** Read a `text/event-stream` body and hand back each event in order.
 *
 *  It is SSE without `EventSource`, because `EventSource` is GET only and an
 *  utterance has to travel in the body. A reply that is not a stream becomes
 *  one error event carrying the server's reason. */
async function events<E>(res: Response, onEvent: (ev: E) => void, error: (text: string) => E): Promise<void> {
  if (!res.ok || !res.body) {
    moved(res)
    let detail = ''
    try {
      detail = (await res.json()).detail ?? ''
    } catch {
      // The status code is all there is to say.
    }
    onEvent(error(detail || `서버가 ${res.status} 로 답했다`))
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // In SSE a blank line ends one event. The last piece has not ended yet,
    // so it is carried over.
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const line = chunk.split('\n').find((l) => l.startsWith('data: '))
      if (!line) continue
      try {
        onEvent(JSON.parse(line.slice(6)))
      } catch {
        // Half a JSON object is dropped. The next event is coming.
      }
    }
  }
}

/** Who a hub document is written for (`search.sources.AUDIENCES`). */
export type Audience = 'product' | 'hooks' | 'jev'

/** Ask the wiki under one focus and hand back the events in order.
 *  `propose`: the server gathers the materials for candidates (`next` only).
 *  `audiences`: narrow retrieval to those documents; null searches every audience. */
export async function say(id: string, text: string, onEvent: (ev: Ev) => void, propose = false,
  audiences: Audience[] | null = null): Promise<void> {
  await events(await post(`/api/say/${id}`, { text, propose, audiences }), onEvent,
    (t): Ev => ({ kind: 'error', text: t }))
}

// -- Jev and a question's run (stage 9 of `docs/plans/jev/`) -----------------

export type JevMode = 'off' | 'shadow' | 'active'
export type JevLimits = { seconds: number; calls: number; candidates: number }
/** The Jev settings as the server reads them now. `key` is only whether one exists. */
export type Jev = {
  mode: JevMode
  mode_source: 'app' | 'file' | 'environment' | 'legacy' | 'default'
  model: string
  key: boolean
  key_source: string
  health: 'disabled' | 'configured' | 'unavailable'
  problem: string | null
  file: string
  disabled_sources: string[]
  limits: JevLimits
  /** Checkouts active mode is limited to; empty: every checkout. */
  active_projects: string[]
  /** Active where the mode came from, shadow here: this checkout is not among `active_projects`. */
  canary: boolean
}
export type Probe = { health: 'reachable' | 'auth_failed' | 'unavailable'; category: string; expected: boolean | null;
  elapsed_ms?: number | null }
export type KnowledgeStatus = {
  jev: Jev
  families: string[]
  sources: { held: string[]; searched: string[] }
  generation: unknown
  runs: { run_id: string; focus: string; stage: string; seq: number }[]
}

export type PathStep = { node: string; node_kind: string; edge_id?: string; kind?: string; reverse?: boolean
  origin?: string; confidence?: number }
export type GraphPath = { lane: string; seed: string; to: string; hops: number; status: string; steps: PathStep[] }
export type Support = 'supported' | 'unverified' | 'conflict' | 'untrusted' | 'not_cited'
export type RunEvidence = {
  chunk_id: string
  kind: string
  revision: string
  locator: { path?: string; line?: number; start_line?: number } & Record<string, unknown>
  language?: string
  text_en?: string
  lane?: string
  relevance?: number | null
  cite: string
  support: Support
  /** The file now: still the revision the run read, or no longer — then only
   *  the run's snapshot of it remains. `null` for evidence that is not a file. */
  now?: 'same' | 'changed' | 'missing' | 'unreadable' | null
}
/** `run-summary/1`: what one question found and published, and why it ended so. */
export type RunSummary = {
  run_id: string
  done: boolean
  focus: string
  outcome: string
  reason: string | null
  settings?: Jev
  retrieval?: { status: string; reason: string | null; fallback: boolean; sources: string[] | null } | null
  evidence?: RunEvidence[]
  graph?: {
    seeds: string[]
    paths: GraphPath[]
    bridges: string[]
    discarded: number
    nodes: Record<string, { kind: string; label: string; type: string }>
    edges: Record<string, { kind: string; directed: boolean; origin: string; confidence: number
      spans: { locator?: { path?: string; start_line?: number }; quote?: string | null }[] }>
    detail?: string
  }
  claims?: { claim_id: string; kind: string; state: string; reason: string | null }[]
  notes?: { code: string; reason?: string; [k: string]: unknown }[]
}

export const getJev = () => get('/api/jev').then((r) => json<Jev>(r, 'Jev 설정'))
export const probeJev = () => post('/api/jev/probe').then((r) => json<Probe>(r, 'Jev 연결 시험'))
export const setJev = (body: { mode: JevMode | null; disabled_sources: string[]; limits: JevLimits }) =>
  post('/api/jev/settings', body).then((r) => json<Jev>(r, 'Jev 설정'))
export const getKnowledge = () => get('/api/knowledge/status').then((r) => json<KnowledgeStatus>(r, '질문 상태'))
export const getRun = (id: string) => get(`/api/knowledge/runs/${id}`).then((r) => json<RunSummary>(r, '실행'))
export const cancelRun = (id: string) =>
  post(`/api/knowledge/runs/${id}/cancel`).then((r) => json<{ ok: boolean; done: boolean }>(r, '멈춤'))
/** Reattach to a run after the last `seq` seen: its rest, tailed while it runs. */
export async function runEvents(id: string, after: number, onEvent: (ev: Ev) => void): Promise<void> {
  await events(await get(`/api/knowledge/runs/${id}/events?after=${after}`), onEvent,
    (t): Ev => ({ kind: 'error', text: t }))
}

// -- Task specs ---------------------------------------------------------------

/** A named block the `next` focus ends an answer with, as the server checked
 *  it. A `spec` block arrives as the id of the card it made, one per spec. */
export type Block =
  | { name: 'candidates'; value: { title: string; why?: string; source?: string }[]; error?: undefined }
  | { name: 'choices'; value: { question: string; options: { label: string; note?: string }[]; multi?: boolean };
      error?: undefined }
  | { name: 'spec'; id: string; error?: undefined }
  | { name: string; error: string }

/** One review round as the loop recorded it. A `stale` one read a head or
 *  base that moved before it ended, and is not counted. */
export type Round = {
  n: number
  head: string
  base: string
  findings: { P0: number; P1: number; P2: number }
  verdict: 'allow' | 'deny'
  stale?: boolean
  gate?: { ok: boolean | null; cmd: string | null; head: string | null }
  disposition?: { finding: string; id?: string | null; action: string; evidence?: string }[] | null
  /** The criteria this round was judged by — a plan reaching past its root is `mixed`. Absent before PR 6. */
  profile?: ReviewProfile
  profile_version?: number
  /** `limited`: findings without a `finding-meta` block, so without ids. */
  identity?: 'full' | 'limited'
  /** The review cell that gave this verdict, as it ran then. */
  reviewer?: { model: string | null; effort: string | null; session_id: string | null; cell: string; tools: string }
  /** The findings with the server's ids; absent on a stale round and before PR 6. */
  items?: { id: string | null; grade: string; head: string; component?: string; possible?: string | null;
    disposition?: string | null }[]
}

export type ReviewProfile = 'plan' | 'code' | 'mixed'

/** What the server ran on a head. `round` is the checks a change maps to,
 *  run every round; `final` is the full gate, once, on the head a review
 *  allowed. Only a passing `final` on the approved head lets `[머지]` through. */
export type Validation = {
  version: number
  round: {
    head: string; base_oid: string; commands: string[]; selection: 'mapped' | 'full'; ok: boolean; finished_at: number
  } | null
  final: {
    head: string; base_oid: string; command: string; environment_digest: string; ok: boolean; code: number | null
    reason: string; finished_at: number | null
  } | null
  phase: 'final_running' | null
}

/** How a person reads a review profile. */
export const PROFILE_LABEL: Record<ReviewProfile, string> = { plan: '계획', code: '코드', mixed: '계획+코드' }

export type Spec = {
  id: string
  repo: string
  rev: number
  goal: string
  out: string[]
  done: string[]
  grounds: { pages: string[]; files: string[]; rules: string[] }
  decisions: { what: string; why: string; rejected: string }[]
  /** The review criteria the spec asked for; the server reads an old spec as `code`. */
  review_profile: ReviewProfile
  review_profile_version: number
  artifact_root: string | null
  source: { focus: string; turn: number; plan: { path: string; row: string } | null }
  state: string
  /** Why a `멈춤` stopped: one of the stage 4 plan's table. */
  stopped: { reason: string; detail: string } | null
  worktree: string | null
  pr: { number: number; url: string; base?: string; head?: string; branch?: string } | null
  report: { item: string; pass: boolean; evidence?: string }[] | null
  gate: { ok: boolean; reason: string; cmd: string; tail: string } | null
  fault: string | null
  /** A plan row's commit still owed after the PR went up: no round before it. */
  plan_commit?: 'asked' | 'pushed' | null
  missing: string[]
  rounds?: Round[]
  /** The head the last counted round allowed: what `[머지]` is bound to. */
  approved?: string | null
  validation?: Validation
  /** In `머지 가능`, why the final gate does not stand for `approved` now —
   *  the server's `specs.proven`, never judged here. Empty only when `[머지]`
   *  may go; `null` in every other state. */
  unproven?: string | null
  /** The work cell waits on a person's approval. Not a state. */
  waiting?: boolean
  p2?: string[]
  p2_comment?: string
  extra?: number
  merge?: { commit: string; base: string } | null
  cleanup?: string[]
  /** Who holds a `머지 대기`: "대기열" or "자동 머지 — 검사 대기". */
  queued?: string | null
  implementation_environment?: 'local' | 'claude-cloud'
  local_verification?: LocalVerification
  /** A plan the Plan action drafts: its phase, budget and hand-off (reliability PR 7). */
  planning?: Planning | null
}

export const getSpecs = () =>
  get('/api/specs').then((r) => json<{ project: string; gate: string; specs: Spec[] }>(r, '명세'))
export const saveSpec = (id: string, body: { rev: number; goal: string; out: string[]; done: string[]; slug: string }) =>
  post(`/api/specs/${id}`, body, 'PUT').then((r) => json<Spec>(r, '명세 저장'))
export const startSpec = (id: string, choice: { model: string; effort: string }) =>
  post(`/api/specs/${id}/start`, choice).then((r) => json<{ path: string; turn: string }>(r, '시작'))
export const dropSpec = (id: string) => post(`/api/specs/${id}/drop`).then((r) => json(r, '버리기'))

// -- The Plan action -----------------------------------------------------------

export type PlanRole = { model: string; effort: string }
export type PlanLimits = { seconds: number; calls: number; tokens: number }
export type PlanPhase = 'collect' | 'clarify' | 'research' | 'outline' | 'stages' | 'validate' | 'publish'
  | 'handoff' | 'stopped'
export type Planning = {
  version: number
  phase: PlanPhase
  artifact_root: string
  roles: { planner: PlanRole; reviser: PlanRole; reviewer: PlanRole }
  limits: PlanLimits
  /** `calls` counts turns sent to the planner; `unknown`: a turn came back without usage, so the spend is a lower bound. */
  spent: PlanLimits & { tools: number; unknown: boolean }
  /** Usage reported after an answer crossed the token ceiling: a failed ceiling check. */
  overrun?: { tokens: number; limit: number } | null
  questions: { id: string; question: string; options: { label: string; note: string }[] }[]
  questions_rev: number
  answers: { id: string; question: string; choice: string }[] | null
  source_manifest: { id: string; title: string; url: string; kind: 'web' | 'local' }[] | null
  artifact_manifest: { path: string }[] | null
  publication: { head: string | null; pr: { number: number; url: string } | null }
  stopped: { reason: string; detail: string; phase: PlanPhase } | null
}

export const PLAN_PHASE: Record<PlanPhase, string> = {
  collect: '자료 모으는 중', clarify: '답 기다림', research: '조사 중', outline: '개요 쓰는 중', stages: '단계 쓰는 중',
  validate: '검사 중', publish: '올리는 중', handoff: '리뷰로 넘김', stopped: '멈춤',
}
export const PLAN_STOP: Record<string, string> = {
  cancelled: '취소', restart: '서버 재시작', deadline: '시간 한도', calls: '호출 한도', tokens: '토큰 한도',
  budget_unknown: '사용량을 모름', web_unavailable: '웹 검색 없음', format: '답 형식', invalid: '검사 실패',
  root_exists: '폴더가 이미 있음', worktree_moved: '작업트리가 움직임', publish_failed: '올리기 실패',
  host: '호스트 오류', broken: '내부 오류',
}

export type PlanRequest = {
  /** Made once per form: a second press of the same form is the same plan. */
  request_id: string
  goal: string
  context: string
  slug: string
  stages: number | null
  roles: { planner: PlanRole; reviser: PlanRole; reviewer: PlanRole }
  limits: PlanLimits
}

export const startPlan = (body: PlanRequest) => post('/api/plans', body).then((r) => json<Spec>(r, '계획'))
export const answerPlan = (id: string, revision: number, answers: { id: string; choice: string }[]) =>
  post(`/api/plans/${id}/answers`, { revision, answers }).then((r) => json<Spec>(r, '답'))
export const resumePlan = (id: string) => post(`/api/plans/${id}/resume`).then((r) => json<Spec>(r, '재개'))
export const cancelPlan = (id: string) => post(`/api/plans/${id}/cancel`).then((r) => json<Spec>(r, '취소'))

// -- The review loop -----------------------------------------------------------

export type Pr = {
  number: number
  title: string
  branch: string
  head: string
  url: string
  fork: boolean
  spec: string | null
  state: string | null
  /** Why it cannot go into a loop from the list; empty when it can. */
  why: string
  pickable: boolean
}

/** A spec's place in the loop, whichever project it is in. */
export type LoopRow = {
  repo: string
  id: string
  state: string
  stopped: { reason: string; detail: string } | null
  pr: number | null
  round: number
  worktree: string | null
  waiting: boolean
  queued?: string | null
}

export type LoopSettings = { rounds: number; concurrent: number; review_model: string; review_effort: string }

export const getPrs = () =>
  get('/api/prs').then((r) => json<{ project: string; rows: Pr[]; error?: string }>(r, 'PR 목록'))
export const startLoops = (prs: number[], implementation_environment: 'local' | 'claude-cloud' = 'local', owner?: string) =>
  post('/api/loops', { prs, implementation_environment }, 'POST', owner).then((r) =>
    json<{ results: { number: number; id?: string; error?: string }[] }>(r, '리뷰 루프'))
export const getLoops = () =>
  get('/api/loops').then((r) => json<{ loops: LoopRow[]; turns: { path: string; repo: string }[] }>(r, '루프'))
export const mergeSpec = (id: string, head: string, owner?: string) =>
  post(`/api/specs/${id}/merge`, { head }, 'POST', owner).then((r) => json<Spec>(r, '머지'))
export const settleSpec = (id: string, choice: 'accept' | 'reopen', owner?: string) =>
  post(`/api/specs/${id}/settle`, { choice }, 'POST', owner).then((r) => json<Spec>(r, '끝내기'))
export const resumeSpec = (id: string, note = '', owner?: string) =>
  post(`/api/specs/${id}/resume`, { note }, 'POST', owner).then((r) => json<Spec>(r, '계속'))
export const haltSpec = (id: string, owner?: string) => post(`/api/specs/${id}/halt`, undefined, 'POST', owner).then((r) => json<Spec>(r, '멈춤'))
export const roundFile = (id: string, n: number, what: 'order' | 'result', owner?: string) =>
  get(`/api/specs/${id}/rounds/${n}?what=${what}`, owner).then((r) => json<{ path: string; text: string }>(r, '라운드 파일'))
export const getLoopSettings = () => get('/api/loop/settings').then((r) => json<LoopSettings>(r, '루프 설정'))
export const setLoopSettings = (body: LoopSettings) =>
  post('/api/loop/settings', body).then((r) => json<LoopSettings>(r, '루프 설정'))

export type LocalVerification = {
  version: number
  state: 'waiting_environment' | 'waiting_review' | 'running' | 'runtime_passed' | 'verified'
    | 'waiting_cloud' | 'reanalysis' | 'unstable' | 'interrupted'
  head: string
  reason: string
  document_only?: boolean
  needs_research?: boolean
  flows?: { id: string; title: string; kind: string; ok: boolean; reason: string; command: string;
    executed_head: string; reuse_reason: string; log?: string; evidence: unknown; finished_at: number | null; attempts?: unknown[] }[]
  failures?: Record<string, string[]>
  published?: { head: string; state: string; id: number | null } | null
}

export type VerificationConfig = {
  repo: string
  settings: Record<string, unknown>
  manifest: { version: number; contracts: string[]; flows: { id: string; title: string; command: string; kind: string;
    environments: string[] }[] } | null
  manifest_digest: string
  problem: string
}

export const getVerificationConfig = (repo: string, sid: string) =>
  get(`/api/verification/config?sid=${encodeURIComponent(sid)}`, repo).then((r) => json<VerificationConfig>(r, '로컬 검증 설정'))
export const saveVerificationConfig = (repo: string, sid: string, body: unknown) =>
  post(`/api/verification/config?sid=${encodeURIComponent(sid)}`, body, 'PUT', repo)
    .then((r) => json<VerificationConfig>(r, '로컬 검증 설정 저장'))
export const configureVerificationProtection = (repo: string, base: string) =>
  post('/api/verification/protection', { base }, 'POST', repo).then((r) => json(r, 'GitHub 필수 검사 설정'))

// -- Connecting a repository --------------------------------------------------

export type SurveySettings = {
  survey: boolean; survey_tokens: number; survey_minutes: number; survey_model: string; survey_effort: string
}
export type HubPlan = {
  needed: boolean
  refused: string
  lines: { file: string; change: string }[]
  links: { link: string; from: string; to: string | null }[]
  trust: boolean
  digest: string
}
export type Estimate = {
  files: number
  code: number
  docs: number
  commits: number
  prs: number
  modules: number
  turns: number
  tokens: number
  seconds: number
  limit: { tokens: number; seconds: number }
  over: boolean
}
export type ConnectPlan = { hub: HubPlan; adapter: Record<string, string> | null; unwire: string[]; survey: Estimate | null }

/** Whether this machine's hooks and skill links point at this hub yet. */
export type Hub = { name: string; needed: boolean; refused: string }

export const getConnect = () =>
  get('/api/connect').then((r) => json<{ rows: Project[]; settings: SurveySettings; hub: Hub }>(r, '프로젝트 목록'))
export const connectPlan = (name: string) =>
  get(`/api/connect/${encodeURIComponent(name)}/plan`).then((r) => json<ConnectPlan>(r, '연결 계획'))
export const connect = (name: string, body: { hub: string; survey: boolean }) =>
  post(`/api/connect/${encodeURIComponent(name)}`, body).then((r) => json<{ ok: boolean }>(r, '연결'))
export const reprobe = (name: string) =>
  post(`/api/connect/${encodeURIComponent(name)}/probe`).then((r) => json<{ ok: boolean }>(r, '다시 시험'))
export const setSurveySettings = (body: SurveySettings) =>
  post('/api/connect/settings', body).then((r) => json<SurveySettings>(r, '조사 설정'))

/** What the server changed on its own: a spec moved, it started a turn, or a
 *  repository's connection changed. */
export type FeedEv =
  | ({ kind: 'spec'; seq: number } & LoopRow)
  | { kind: 'turn'; seq: number; path: string; turn: string; session_id: string }
  | { kind: 'connect'; seq: number; repo: string }

/** Tail the server's own changes until `signal` aborts or the stream drops. */
export async function loopEvents(onEvent: (ev: FeedEv) => void, signal: AbortSignal): Promise<void> {
  const url = '/api/loops/events'
  const res = await fetch(url, { headers: await scoped(url), signal })
  await events<FeedEv | null>(res, (ev) => ev && onEvent(ev), () => null)
}

// -- The translation switch ---------------------------------------------------

export type Usage = { month: string; usd: number | null; limit: number }
export type Switch = { translate: boolean; usage: Usage }

export const getSwitch = () => get('/api/switch').then((r) => json<Switch>(r, '번역 스위치'))
export const setSwitch = (on: boolean) =>
  post('/api/switch', { translate: on }).then((r) => json<Switch>(r, '번역 스위치'))

// -- Worktrees and their agents ----------------------------------------------

export type Worktree = {
  path: string
  name: string
  branch: string
  dirty: boolean
  merged: boolean
  live: boolean
  busy: boolean
}

export const getWorktrees = () =>
  get('/api/worktrees').then((r) => json<{ project: string; repo: string; rows: Worktree[] }>(r, '작업트리'))
/** `force`: stop what runs there and drop uncommitted changes — a person's delete. */
export const removeWorktree = (path: string, force = false) =>
  post('/api/worktrees/remove', { path, force }).then((r) => json<{ text: string }>(r, '작업트리 정리'))

/** Who answered an approval: a person, a session rule, or the server itself
 *  refusing a write outside the worktree or anything a read session asks. */
export type AnsweredBy = 'person' | 'session' | 'outside' | 'read'

/** One event of a worktree's agent. `session_id` is the program's own id for
 *  the session, fixed for its life — what tells a late event from a current
 *  one, and what an approval answer has to name. `turn` and `seq` place it in
 *  the server's buffer of that turn, which a reattaching screen reads from. */
export type WorkEv = {
  kind: 'delta' | 'tool' | 'said' | 'hook' | 'approval' | 'answered' | 'done' | 'error'
  text: string
  meta: {
    /** A hook's: which event, and what it put into context. */
    event?: string
    context?: string
    /** A question's answers, one per question. */
    answers?: string[]
    id?: string
    tool?: string
    input?: Record<string, unknown>
    answer?: 'allow' | 'deny'
    allow?: boolean
    by?: AnsweredBy
    session?: boolean
    ms?: number
    session_id?: string
    model?: string
    cost_usd?: number
    tokens?: Tokens
  }
  session_id: string
  parent_id: string | null
  turn?: string
  seq?: number
  /** When the server took it, in epoch seconds. */
  ts?: number
}

export type WorkStep =
  | { kind: 'tool' | 'said' | 'hook'; text: string }
  | { kind: 'approval'; tool: string; text: string; answer: 'allow' | 'deny' | 'none'; by: AnsweredBy;
      answers?: string[] }

export type WorkTurn = {
  role: 'user' | 'assistant'
  text: string
  error?: string
  steps?: WorkStep[]
  /** How many steps came before the answer. */
  answered?: number
  ms?: number
  cost_usd?: number
  model?: string
  tokens?: Tokens
}

/** An "allow for this session": a file tool by name, or one exact command. */
export type Rule = { kind: 'file' | 'command'; tool: string; command?: string; cwd?: string }

export type Running = { turn: string; session_id: string; seq: number }

export const workLog = (path: string) =>
  get(`/api/work/log?${new URLSearchParams({ path })}`).then((r) =>
    json<{ rows: WorkTurn[]; session_id: string; busy: boolean; running: Running | null; rules: Rule[];
      queued: string | null }>(r, '작업 기록'),
  )
export const workReset = (path: string, keep: Keep) =>
  post('/api/work/reset', { path, keep }).then((r) => json<Kept>(r, '작업 문맥 비우기'))
export const workAnswer = (body: {
  path: string; session_id: string; id: string; allow: boolean; scope: 'once' | 'session'; answers?: string[]
}) => post('/api/work/answer', body).then((r) => json(r, '승인'))
export const workStop = (path: string, turn: string) =>
  post('/api/work/stop', { path, turn }).then((r) => json(r, '멈춤'))
export const workSteer = (path: string, turn: string, text: string) =>
  post('/api/work/steer', { path, turn, text }).then((r) => json(r, '끼어들기'))
/** The next instruction, sent by the server once this turn's run lets go. */
export const workQueue = (body: { path: string; turn: string; text: string; model: string; effort: string }) =>
  post('/api/work/queue', body).then((r) => json(r, '대기'))
export const workUnqueue = (path: string) => post('/api/work/unqueue', { path }).then((r) => json(r, '대기 취소'))
export type WorkSettings = { bypass: boolean }
export const getWorkSettings = () => get('/api/work/settings').then((r) => json<WorkSettings>(r, '작업 설정'))
export const setWorkSettings = (body: WorkSettings) =>
  post('/api/work/settings', body).then((r) => json<WorkSettings>(r, '작업 설정'))
export const clearRules = (path: string, session_id: string) =>
  post('/api/work/rules/clear', { path, session_id }).then((r) => json(r, '세션 허용 해제'))

const workError = (t: string): WorkEv => ({ kind: 'error', text: t, meta: {}, session_id: '', parent_id: null })

export async function workSay(
  body: { path: string; text: string; model: string; effort: string },
  onEvent: (ev: WorkEv) => void,
): Promise<void> {
  await events(await post('/api/work/say', body), onEvent, workError)
}

/** Reattach to a turn after the last `seq` seen. `gone` when the server holds
 *  another turn by now (410): the record is the thing to read then. */
export async function workEvents(
  path: string, turn: string, after: number, onEvent: (ev: WorkEv) => void,
): Promise<'gone' | void> {
  const res = await get(`/api/work/events?${new URLSearchParams({ path, turn, after: String(after) })}`)
  if (res.status === 410) return 'gone'
  await events(res, onEvent, workError)
}
