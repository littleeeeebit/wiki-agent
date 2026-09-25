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

/** The policy graph: `graph.json` exactly as `tool/graph.py` produced it.
 *
 *  The shape is not restated here. A node has a dozen or so fields, and
 *  copying them into a type means editing two places every time the Python
 *  side adds one. The drawing code only needs to know the fields it uses. */
export type GraphData = {
  ns: string
  nodes: { id: string; injected: boolean; chars: number }[]
  projects: { key: string; short: string; deny: number; corpus: number; note: string; status: Record<string, string> }[]
  ladder: { n: number; title: string; color: string }[]
  load: { max: number; median: number; hits: number }
  corpus: number
  cap: number
}

export type Project = { id: string; path: string; wired: boolean }
export type Choice = { id: string; label: string; note: string }
export type Options = {
  projects: Project[]
  models: (Choice & { efforts?: Choice[] })[]
  efforts: Choice[]
  codex_error: string
}

export type Turn = {
  ts: number
  role: 'user' | 'assistant'
  text: string
  source?: string
  error?: string
  simple_text?: string
  simple_error?: string
  simple_meta?: { ms?: number; cost_usd?: number }
  ms?: number
  cost_usd?: number
  model?: string
  session_id?: string
  tokens?: Tokens
}

export type Tokens = {
  in?: number
  out?: number
  cache_read?: number
  cache_write?: number
}

export type Ev = {
  kind: 'hits' | 'delta' | 'tool' | 'done' | 'error' | 'simple_start' | 'simple_delta' | 'simple_done' | 'simple_error'
  text: string
  pages?: string[]
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

async function scoped(url: string): Promise<Record<string, string>> {
  if (!url.startsWith('/api/channels')) await claimedOnce
  return claimed ? { 'X-Project': encodeURIComponent(claimed) } : {}
}

/** The project the server is on, when it refused this screen's. Announced as
 *  a window event so the one place that follows the server hears it. */
function moved(res: Response) {
  const to = res.headers.get('X-Project-Moved')
  if (to) window.dispatchEvent(new CustomEvent('project-moved', { detail: decodeURIComponent(to) }))
}

const get = async (url: string) => fetch(url, { headers: await scoped(url) })

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

const post = async (url: string, body?: unknown) =>
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await scoped(url)) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })

export const getChannels = () =>
  get('/api/channels').then((r) => json<Channel[]>(r, '채널 목록'))

export const getGraph = () =>
  get('/api/graph').then((r) => json<GraphData>(r, '위키 지도'))

export const getOptions = () =>
  get('/api/options').then((r) => json<Options>(r, '고를 것'))

export const getLog = (id: string, legacy = false) =>
  get(`/api/log/${id}?legacy=${legacy}`).then((r) => json<Turn[]>(r, '기록'))

export const reset = (id: string) => post(`/api/reset/${id}`).then((r) => json(r, '문맥 지우기'))

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

/** Ask the wiki under one focus and hand back the events in order. */
export async function say(id: string, text: string, onEvent: (ev: Ev) => void): Promise<void> {
  await events(await post(`/api/say/${id}`, { text }), onEvent, (t): Ev => ({ kind: 'error', text: t }))
}

/** The instruction draft that carries an answer — or a retro candidate — to a
 *  worktree's agent. The last section is left for a person. */
export const draft = (body: { question: string; answer?: string; hits?: string[]; target?: 'wiki' | 'claude_md' }) =>
  post('/api/draft', body).then((r) => json<{ text: string }>(r, '작업 초안'))

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
export const makeWorktree = (task: string) =>
  post('/api/worktrees', { task }).then((r) => json<{ path: string }>(r, '작업트리 만들기'))
export const removeWorktree = (path: string) =>
  post('/api/worktrees/remove', { path }).then((r) => json<{ text: string }>(r, '작업트리 정리'))

/** One event of a worktree's agent. `session_id` is the program's own id for
 *  the session, fixed for its life — what tells a late event from a current
 *  one, and what an approval answer has to name. */
export type WorkEv = {
  kind: 'delta' | 'tool' | 'approval' | 'done' | 'error'
  text: string
  meta: {
    id?: string
    tool?: string
    input?: Record<string, unknown>
    ms?: number
    session_id?: string
    model?: string
    cost_usd?: number
    tokens?: Tokens
  }
  session_id: string
  parent_id: string | null
}

export type WorkTurn = {
  role: 'user' | 'assistant'
  text: string
  error?: string
  tools?: string[]
  ms?: number
  cost_usd?: number
  model?: string
}

export const workLog = (path: string) =>
  get(`/api/work/log?${new URLSearchParams({ path })}`).then((r) =>
    json<{ rows: WorkTurn[]; session_id: string; busy: boolean }>(r, '작업 기록'),
  )
export const workReset = (path: string) =>
  post('/api/work/reset', { path }).then((r) => json(r, '작업 문맥 비우기'))
export const workAnswer = (body: { path: string; session_id: string; id: string; allow: boolean }) =>
  post('/api/work/answer', body).then((r) => json(r, '승인'))

export async function workSay(
  body: { path: string; text: string; model: string; effort: string },
  onEvent: (ev: WorkEv) => void,
): Promise<void> {
  await events(await post('/api/work/say', body), onEvent,
    (t): WorkEv => ({ kind: 'error', text: t, meta: {}, session_id: '', parent_id: null }))
}
