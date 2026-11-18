import { createContext, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Choices } from '@/components/Blocks'
import { Btn } from '@/components/Modal'
import { Board } from '@/components/OneshotBoard'
import { Track } from '@/components/OneshotTrack'
import { api, STOP, type Brief, type Decision, type Gate, type Handset, type Palette,
  type Models, type PhoneRound, type RunSummary, type SpecView, type Talk, type Tool, kindLabel, ROLE, modelName, toolText,
} from '@/lib/oneshot'
import { cn } from '@/lib/utils'

/** Which model each part of the run uses, fixed at its start. */
export function ModelsList({ models, className }: { models?: Models; className?: string }) {
  if (!models) return <p title="「다음 작업」 창에서 고른 모델을 쓴다" className={cn('text-[12px] text-faint', className)}>모델을 고르기 전에 시작한 실행</p>
  return (
    <dl className={cn('grid grid-cols-[auto_auto] gap-x-2.5 gap-y-0.5 text-[12px] text-faint', className)}>
      {(Object.keys(ROLE) as (keyof Models)[]).map((k) => [
        <dt key={`${k}-t`}>{ROLE[k]}</dt>, <dd key={`${k}-d`} className="text-muted-foreground">{modelName(k, models[k])}</dd>,
      ])}
      <dt>화면 비평</dt><dd className="text-muted-foreground">opus · high (고정)</dd>
    </dl>
  )
}

// -- The stage's layouts (`DESIGN.md`, "Oneshot window") ---------------------------------

/** The stage's title, badge and hint, which the window hands down and the layout puts at its top-left. */
export const Lead = createContext<ReactNode>(null)

/** The work on the left and what it makes or rests on beside it, each scrolling on its own;
 *  under 1100px the side goes below the work and the window scrolls as one. */
export function Split({ label, side, sideLabel, children }: { label: string; side: ReactNode; sideLabel: string; children: ReactNode }) {
  const lead = useContext(Lead)
  return (
    <section aria-label={label} className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_34rem] max-[1500px]:grid-cols-[minmax(0,1fr)_24rem]
      max-[1100px]:flex max-[1100px]:flex-none max-[1100px]:flex-col">
      <div className="flex min-h-0 flex-col overflow-y-auto max-[1100px]:shrink-0 max-[1100px]:overflow-visible">
        {lead && <div className="shrink-0 px-10 pt-6 max-[1500px]:px-7 max-[560px]:px-4">{lead}</div>}
        <Lead.Provider value={null}>{children}</Lead.Provider>
      </div>
      <aside aria-label={sideLabel} className="flex min-h-0 flex-col gap-4 overflow-y-auto border-l border-border bg-card px-7 pt-6 pb-0
        max-[1100px]:shrink-0 max-[1100px]:overflow-visible max-[1100px]:border-t max-[1100px]:border-l-0 max-[560px]:px-4">{side}</aside>
    </section>
  )
}

/** One column under the stage's title, with the title's left edge. */
export function Top({ wide, children }: { wide?: boolean; children: ReactNode }) {
  return <div className={cn('flex w-full flex-col gap-5 px-10 pt-6 pb-8 max-[1500px]:px-7 max-[560px]:px-4', !wide && 'max-w-[60rem]')}>{useContext(Lead)}{children}</div>
}

/** A side or card's label. */
export function Title({ children }: { children: ReactNode }) {
  return <h3 className="flex justify-between gap-2 font-heading text-[12px] font-semibold tracking-wide text-faint">{children}</h3>
}

/** The button that sends a decision: larger than the row buttons, and amber, since the run waits on it. */
export const GO = 'h-9 justify-center px-4 text-[13px] font-medium'

/** The decision, held at the bottom of the side while what it decides on scrolls. */
export function Decide({ children }: { children: ReactNode }) {
  return <div className="sticky bottom-0 -mx-7 mt-auto flex flex-col gap-2 border-t border-border bg-card px-7 py-4 max-[560px]:-mx-4 max-[560px]:px-4">{children}</div>
}

export function Fault({ text }: { text: string }) {
  return text ? <p role="alert" className="whitespace-pre-wrap rounded-md bg-destructive/10 px-3 py-2 text-destructive">{text}</p> : null
}

/** A run that stopped: the reason first, and `[재개]`. */
export function Halt({ title, detail, children }: { title: string; detail: string; children: ReactNode }) {
  return (
    <section role="alert" className="flex flex-col gap-3 rounded-lg border border-wait/40 bg-wait/10 px-5 py-4">
      <p className="font-heading text-[15px] font-semibold">{title}</p>
      <p className="max-w-[44em] whitespace-pre-wrap">{detail}</p>
      <div className="flex flex-wrap gap-2">{children}</div>
    </section>
  )
}

/** A run past S1 (`docs/plans/oneshot/5-screen-pr.md`): the S2 conversation beside the requirements it
 *  gathered, the three gate sheets beside their decisions, the feature board beside the first screen it
 *  makes, and the summary. The server renders every sheet and owns every rule; this view only shows,
 *  folds and collects decisions. */

const error = (err: unknown) => String(err instanceof Error ? err.message : err)

export function OneshotSpec({ repo, onView }: { repo: string; onView?: (view: SpecView) => void }) {
  const [view, setView] = useState<SpecView | null>(null)
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { api.view(repo).then(setView).catch((err) => setFault(error(err))) }, [repo])
  useEffect(() => { if (view) onView?.(view) }, [view, onView])
  const working = !!view?.working
  useEffect(() => {
    if (!working) return
    const timer = window.setInterval(() => { api.view(repo).then(setView).catch((err) => setFault(error(err))) }, 1500)
    return () => window.clearInterval(timer)
  }, [repo, working])

  /** Whether the server took it: a refused answer stays in its box to be sent again. */
  async function act(fn: () => Promise<SpecView>) {
    setFault('')
    setBusy(true)
    try {
      setView(await fn())
      return true
    } catch (err) {
      setFault(error(err))
      return false
    } finally {
      setBusy(false)
    }
  }

  if (!view) return <Top><p role="status" className="text-muted-foreground">{fault || '읽는 중…'}</p></Top>
  const o = view.oneshot
  const android = ['android', 'desktop'].includes(o.intent?.kind ?? '')   // the six parts (`oneshot_profile.parts`)
  const idle = !view.working && !view.stopped
  // A `kind` stop waits for this kind's next stage to ship: no resume reaches it.
  const resume = !['kind', 'rekind'].includes(view.stopped?.reason ?? '') && (
    <Btn tone="wait" className={GO} disabled={busy} onClick={() => void act(() => api.resume(repo))}>재개</Btn>)
  const faulted = (fault || !!view.phone_owned?.length) && (
    <div className="flex flex-col gap-2 px-10 pt-4 max-[1500px]:px-7 max-[560px]:px-4">
      <Fault text={fault} />
      {/* Shown on the run, whatever its stage, until the phone is listed again and the cleanup finishes. */}
      {view.phone_owned?.map((r) => r.state === 'unsure' ? (
        <p key={`${r.serial}-${r.packages.join()}`} role="status" className="rounded-md bg-wait/10 px-3 py-2">
          휴대폰 <span className="font-mono">{r.serial}</span> 에 이 실행의 앱이 있지만, 이 실행이 설치를 마쳤는지 확인하지 못해 지우지
          않았다: {(r.unsure ?? []).join(', ')}. 필요 없으면 휴대폰에서 직접 지우고 [휴대폰 찾기] 를 누른다.
        </p>
      ) : (
        <p key={`${r.serial}-${r.packages.join()}`} role="status" className="rounded-md bg-wait/10 px-3 py-2">
          휴대폰 <span className="font-mono">{r.serial}</span> 에 이 실행이 설치한 앱이 남아 있다: {(r.left.length ? r.left : r.packages).join(', ')}.
          그 휴대폰을 연결하고 [휴대폰 찾기] 를 누르면 지운다.
        </p>
      ))}
      {/* Any stage: a passed round's cleanup can be left pending at R3, where no phone prompt stands. */}
      {!!view.phone_owned?.length && (
        <Btn className="self-start" disabled={busy}
          onClick={() => void act(async () => { await api.phones(repo); return api.view(repo) })}>휴대폰 찾기</Btn>
      )}
    </div>
  )
  if (view.stage === 'spec') {
    return <>{faulted}<Conversation talk={o.talk ?? []} requirements={view.requirements ?? []} stack={o.stack ?? []}
      android={android} kinds={o.intent?.kinds ?? [o.intent?.kind ?? 'webapp']}
      halt={view.stopped && <Halt title={STOP[view.stopped.reason] ?? view.stopped.reason} detail={view.stopped.detail}>
        {resume}</Halt>}
      busy={busy || !idle} send={(text) => act(() => api.say(repo, text))}
      confirm={(part, choice, tools) => act(() => api.confirmBuild(repo, part, choice, tools))} /></>
  }
  if (view.stopped) {
    return (
      <Track view={view} halt={<>
        <Fault text={fault} />
        {view.phone_owned?.length ? faulted : null}
        <Halt title={STOP[view.stopped.reason] ?? view.stopped.reason} detail={view.stopped.detail}>{resume}</Halt>
      </>} />
    )
  }
  return (
    <>
      {faulted}
      {idle && view.stage === 'r1' && view.sheet && o.r1 && (
        <Sheet key={`r1-${o.r1.round}-${view.brief?.source}`} gate="r1" text={view.sheet} items={view.items ?? []}
          state={o.r1} busy={busy} brief={view.brief}
          submit={(body) => act(() => api.decide(repo, 'r1', { ...body, source: view.brief?.source }))} />
      )}
      {idle && view.stage === 'board' && view.board && (
        <Board name={view.repo} board={view.board} busy={busy} submit={(f, line) => act(() => api.board(repo, f, line))} />
      )}
      {idle && view.stage === 'r2' && view.front_sheet && o.r2 && (
        <Sheet key={`r2-${o.r2.round}-${view.front_brief?.source}`} gate="r2" text={view.front_sheet}
          items={view.front_items ?? []} state={o.r2} busy={busy} palettes={o.front?.tokens.palettes ?? []}
          brief={view.front_brief}
          reopen={() => act(() => api.decide(repo, 'r2', { reopen: true, source: view.front_brief?.source }))}
          submit={(body) => act(() => api.decide(repo, 'r2', { ...body, source: view.front_brief?.source }))} />
      )}
      {idle && view.stage === 'phone' && (
        <PhonePick repo={repo} round={o.phone} busy={busy} act={act} />
      )}
      {idle && view.stage === 'r3' && view.r3_sheet && o.r3 && (
        <Sheet key={`r3-${o.r3.round}-${view.r3_brief?.source}`} gate="r3" text={view.r3_sheet}
          items={view.r3_items ?? []} state={o.r3} busy={busy} brief={view.r3_brief}
          submit={(body) => act(() => api.decide(repo, 'r3', { ...body, source: view.r3_brief?.source }))} />
      )}
      {view.stage === 'done' && view.summary && <Finished summary={view.summary} />}
      {view.working && view.stage !== 'done' && <Track view={view} halt={view.working === 'phone' && (
        <section aria-label="휴대폰 회차" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-5 py-4">
          <p>휴대폰 <span className="font-mono">{o.phone?.serial}</span> 에서 화면과 시나리오를 검사하는 중이다. 끝나면 설치한 앱을 지운다.</p>
          <Btn disabled={busy} onClick={() => void act(() => api.stopPhone(repo))}>멈춤</Btn>
        </section>
      )} />}
    </>
  )
}

// -- The phone round before R3 -------------------------------------------------------------

/** The prompt to connect the owner's phone: the phones the run's own ADB server lists, one picked by its
 *  serial, and whether a passing app stays on it. The last round's outcome and any unfinished cleanup sit
 *  beside it. The server owns every rule; a refused pick comes back as the fault. */
function PhonePick({ repo, round, busy, act }: {
  repo: string; round?: PhoneRound; busy: boolean; act: (fn: () => Promise<SpecView>) => Promise<boolean>
}) {
  const [found, setFound] = useState<{ devices: Handset[] } | null>(null)
  const [looking, setLooking] = useState(false)
  const [fault, setFault] = useState('')
  const [serial, setSerial] = useState(round?.serial ?? '')
  const [keep, setKeep] = useState(!!round?.keep)
  async function look() {
    setFault('')
    setLooking(true)
    try {
      setFound(await api.phones(repo))
      await act(() => api.view(repo))   // a cleanup the listing finished leaves the run's notice
    } catch (err) { setFault(error(err)) } finally { setLooking(false) }
  }
  const ready = found?.devices.filter((d) => d.state === 'device') ?? []
  const high = round?.findings?.filter((f) => f.severity === 'high') ?? []
  return (
    <Split label="휴대폰 회차" sideLabel="연결된 휴대폰" side={<>
      <Title><span>연결된 휴대폰</span><span className="font-mono">{found ? ready.length : ''}</span></Title>
      <Btn disabled={looking || busy} onClick={() => void look()}>{looking ? '찾는 중…' : found ? '다시 찾기' : '휴대폰 찾기'}</Btn>
      <Fault text={fault} />
      {found && !found.devices.length && (
        <p className="text-muted-foreground">휴대폰이 보이지 않는다. USB 디버깅을 켜고 연결했는지 본다. 다른 ADB 서버(Android Studio 같은)가
          휴대폰을 잡고 있으면 이 실행의 서버에는 보이지 않으니, 그 서버를 닫고 다시 찾는다.</p>
      )}
      {!!found?.devices.length && (
        <fieldset className="flex flex-col gap-1.5">
          <legend className="sr-only">검사할 휴대폰</legend>
          {found.devices.map((d) => (
            <label key={d.serial} className={cn('flex items-baseline gap-2.5 rounded-md border border-border px-3 py-2.5',
              d.state !== 'device' && 'text-muted-foreground')}>
              <input type="radio" name="phone" value={d.serial} checked={serial === d.serial} disabled={d.state !== 'device'}
                onChange={() => setSerial(d.serial)} />
              <span className="flex min-w-0 flex-col">
                <span>{d.model || '이름 없는 기기'}</span>
                <span className="font-mono text-[12px] text-faint">{d.serial}</span>
                {d.state === 'unauthorized' && <span className="text-[12.5px]">휴대폰 화면에서 USB 디버깅을 허용한다</span>}
                {d.state !== 'device' && d.state !== 'unauthorized' && <span className="text-[12.5px]">{d.state}</span>}
              </span>
            </label>
          ))}
        </fieldset>
      )}
      <label className="flex items-baseline gap-2.5">
        <input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />
        <span>통과하면 앱을 휴대폰에 남긴다</span>
      </label>
      <Decide>
        <Btn tone="wait" className={GO} disabled={busy || looking || !ready.some((d) => d.serial === serial)}
          onClick={() => void act(() => api.phone(repo, serial, keep))}>이 휴대폰에서 검사</Btn>
      </Decide>
    </>}>
      <div className="flex max-w-[44rem] flex-col gap-4 px-10 py-6 max-[1500px]:px-7 max-[560px]:px-4">
        <p>에뮬레이터에서 두 번 깨끗하게 통과한 앱을 휴대폰에서 한 번 더 본다. 휴대폰을 USB 로 연결하고, 개발자 옵션에서 USB
          디버깅을 켜고, 잠금을 푼 채로 둔다.</p>
        <p className="text-muted-foreground">이 실행만의 패키지 이름으로 설치하고, 모든 화면과 시나리오, 첫 화면의 프레임 시간과 메모리를 잰
          뒤 지운다. 고른 휴대폰 말고는 아무 기기에도 명령을 보내지 않고, 다른 앱이나 데이터는 건드리지 않는다.</p>
        {round?.detail && <p role="alert" className="rounded-md bg-wait/10 px-3 py-2">{round.detail}</p>}
        {high.length > 0 && (
          <section aria-label="지난 휴대폰 회차" className="flex flex-col gap-2">
            <Title><span>지난 회차에서 고친 것</span><span className="font-mono">{high.length}</span></Title>
            <ul className="flex flex-col gap-1.5">
              {high.map((f, i) => <li key={i}><span className="font-mono text-[12px] text-faint">{f.screen} {f.state}</span> {f.what}</li>)}
            </ul>
          </section>
        )}
      </div>
    </Split>
  )
}

/** The stack's parts as S2 asks them (`oneshot_profile.PARTS`, `ANDROID_PARTS`). */
const PART: Record<string, string> = {
  backend: '백엔드', frontend: '화면', styling: '스타일', data_store: '저장소', tests: '테스트', deploy: '배포',
}
const ANDROID_PART: Record<string, string> = {
  language: '언어', ui: '화면', core: '핵심 로직', data_store: '저장소', tests: '테스트', build: '빌드',
}
/** Several kinds' parts (`oneshot_profile.SHARED_PARTS`, `OWN_PARTS`): the shared ones, then `<kind>.<part>`. */
const OWN_PARTS: Record<string, string[]> = {
  webapp: ['backend', 'frontend', 'styling', 'deploy'], android: ['ui', 'build'], desktop: ['ui', 'build'],
}
const partsOf = (kinds: string[], android: boolean): [string, string][] => kinds.length > 1
  ? [...['core', 'data_store', 'tests'].map((p): [string, string] => [p, ANDROID_PART[p]]),
     ...kinds.flatMap((k) => (OWN_PARTS[k] ?? []).map((p): [string, string] =>
       [`${k}.${p}`, `${kindLabel(k)} ${PART[p] ?? ANDROID_PART[p] ?? p}`]))]
  : Object.entries(android ? ANDROID_PART : PART)

/** The S2 conversation filling the height with its answer box held at the bottom, beside the requirements and
 *  the stack it has gathered so far. */
function Conversation({ talk, requirements, stack, android, kinds, halt, busy, send, confirm }: {
  talk: Talk[]; requirements: NonNullable<SpecView['requirements']>; stack: NonNullable<SpecView['oneshot']['stack']>
  android: boolean; kinds: string[]; halt: ReactNode; busy: boolean; send: (text: string) => Promise<boolean>
  confirm: (part: string, choice: string, tools: Tool[]) => Promise<boolean>
}) {
  const parts = partsOf(kinds, android)
  const [text, setText] = useState('')
  const thread = useRef<HTMLDivElement>(null)
  useEffect(() => { thread.current?.scrollTo({ top: thread.current.scrollHeight }) }, [talk.length])
  // Cleared only once the server took it, and only if nothing new was typed meanwhile.
  const submit = async (sent: string) => { if (await send(sent)) setText((now) => (now === sent ? '' : now)) }
  const open = stack.filter((s) => s.status !== 'confirmed').length
  // A question still waits for its answer: the build's own button is never taken as that answer.
  const asking = talk.at(-1)?.role === 'agent' && !!talk.at(-1)?.choices
  return (
    <Split label="요구 대화" sideLabel="모인 요구" side={<>
      <Title><span>모인 요구</span><span className="font-mono">{requirements.length}</span></Title>
      {requirements.length ? (
        <ol className="flex flex-col">
          {requirements.map((r) => (
            <li key={r.id} className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-2 border-b border-border py-2.5 last:border-b-0">
              <span className="font-mono text-[12px] text-faint">{r.id}</span>
              <span>{r.text}{r.kinds && kinds.length > 1 && (
                <span className="ml-1.5 text-[12px] text-muted-foreground">— {r.kinds.map(kindLabel).join(' · ')}만</span>)}</span>
            </li>
          ))}
        </ol>
      ) : <p className="text-muted-foreground">아직 없다. 질문에 답하면 여기에 쌓인다.</p>}
      <Title><span>스택</span><span className="font-mono">{open ? `${open}개 남음` : ''}</span></Title>
      <dl className="grid grid-cols-[minmax(5rem,max-content)_minmax(0,1fr)] gap-x-3 gap-y-2 pb-6">
        {parts.map(([p, label]) => {
          const s = stack.find((x) => x.part === p)
          return [
            <dt key={`${p}-t`} className="text-muted-foreground">{label}</dt>,
            <dd key={`${p}-d`} title={s?.why} className={cn(!s || s.status !== 'confirmed' ? 'text-faint' : '')}>
              {/* The choice as stored, spaces and line breaks kept: a PC app's build is confirmed exactly as shown. */}
              {s ? <><span className="whitespace-pre-wrap break-words">{s.choice}</span>{s.status === 'confirmed' ? '' : ' (아직 묻는 중)'}</>
                : '아직 안 정함'}
              {/* A PC app's build names the tools the owner confirms with it, each with how CI gets it. Only its own
                  button confirms it, exactly as shown (`oneshot_spec.confirm_build`). */}
              {/* Each setup as stored: spaces and line breaks kept, so two setups never look alike. */}
              {s?.tools && <ul aria-label="도구" className="mt-1 flex flex-col gap-0.5 text-[12px] text-muted-foreground">
                {s.tools.map((t) => <li key={t.name} className="whitespace-pre-wrap break-all font-mono">{toolText(t)}</li>)}
              </ul>}
              {s?.tools && s.status !== 'confirmed' && (asking
                ? <span className="mt-1.5 block text-[12px] text-muted-foreground">질문에 먼저 답하면 확정할 수 있다</span>
                : <Btn className="mt-1.5" disabled={busy} onClick={() => void confirm(s.part, s.choice, s.tools!)}>이 빌드와 도구로 확정</Btn>
              )}
            </dd>,
          ]
        })}
      </dl>
      <p className="pb-6 text-[12.5px] text-muted-foreground">스택이 다 정해지고 물을 것이 없으면 대화가 끝나고 {android || kinds.length > 1 ? '핵심 설계' : '백엔드 설계'}로 넘어간다.</p>
    </>}>
      <div className="flex min-h-0 flex-1 flex-col max-[1100px]:min-h-[34rem]">
        <div ref={thread} className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-10 py-5 max-[1500px]:px-7 max-[560px]:px-4">
          {halt}
          {talk.map((t, i) => (
            <div key={i} className={cn('flex max-w-[44rem] flex-col gap-1', t.role === 'owner' && 'items-end self-end', t.choices && 'max-w-[60rem]')}>
              <span className="text-[12px] text-faint">{t.role === 'agent' ? '에이전트' : '나'}</span>
              <div className={cn('whitespace-pre-wrap rounded-xl px-4 py-3', t.role === 'agent' ? 'border border-border bg-card' : 'bg-secondary')}>
                {t.text}
                {/* The question itself rides in `choices`, cut from the text: only the last turn's can be answered. */}
                {t.choices && <Choices value={t.choices} korean={false} busy={busy || i !== talk.length - 1} onSay={(a) => void send(a)} />}
              </div>
            </div>
          ))}
          {busy && !halt && <p role="status" className="text-muted-foreground">에이전트가 답을 쓰는 중…</p>}
        </div>
        <form className="flex shrink-0 items-end gap-3 border-t border-border bg-card px-10 py-3.5 max-[1500px]:px-7 max-[560px]:px-4"
          onSubmit={(e) => { e.preventDefault(); void submit(text) }}>
          <textarea aria-label="답" value={text} onChange={(e) => setText(e.target.value)} rows={2}
            placeholder="질문에 답하거나 요구를 더 적는다"
            className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-foreground max-[560px]:text-base" />
          <Btn type="submit" tone="primary" disabled={busy || !text.trim()}>보내기</Btn>
        </form>
      </div>
    </Split>
  )
}

// -- The gate sheet ---------------------------------------------------------------------

/** The sheet's Markdown split at its `<details>` blocks: `[text, null]` is shown, `[text, summary]` folds. */
function parts(text: string): [string, string | null][] {
  const out: [string, string | null][] = []
  const fold = /<details><summary>([\s\S]*?)<\/summary>([\s\S]*?)<\/details>/g
  let at = 0
  for (const m of text.matchAll(fold)) {
    out.push([text.slice(at, m.index), null], [m[2], m[1]])
    at = m.index + m[0].length
  }
  out.push([text.slice(at), null])
  return out.filter(([t]) => t.trim())
}
const named = (text: string, id: string) => new RegExp(`(^|[^\\w-])${id}(?![\\w-])`).test(text)

function Mermaid({ code }: { code: string }) {
  const [svg, setSvg] = useState('')
  const id = `sheet${useId().replace(/[^\w]/g, '')}`
  useEffect(() => {
    let live = true
    import('mermaid').then(async ({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'base', htmlLabels: false })
      const result = await mermaid.render(id, code)
      if (live) setSvg(result.svg)
    }).catch(() => live && setSvg(''))
    return () => { live = false }
  }, [code, id])
  return svg ? <div className="overflow-x-auto" dangerouslySetInnerHTML={{ __html: svg }} /> : <pre><code>{code}</code></pre>
}

function Md({ text, zoom }: { text: string; zoom: (src: string) => void }) {
  // Made once: a new `img` each render is a new component, which replaces the thumbnail holding the focus.
  const components = useMemo<Components>(() => ({
        img: ({ src, alt }) => (
          <button type="button" className="inline-block" onClick={() => zoom(String(src))} title="크게 본다">
            <img src={String(src)} alt={alt ?? ''} loading="lazy" className="max-h-64 rounded border border-border" />
          </button>
        ),
        table: ({ children }) => <div className="overflow-x-auto"><table>{children}</table></div>,
        code: ({ className, children }) => className === 'language-mermaid'
          ? <Mermaid code={String(children ?? '').replace(/\n$/, '')} /> : <code className={className}>{children}</code>,
      }), [zoom])
  return (
    <div className="prose-answer">
      <Markdown remarkPlugins={[remarkGfm]} components={components}>{text}</Markdown>
    </div>
  )
}

/** The brief's sections before its confirmation lines; the rest follow them (`oneshot_brief.SECTIONS`). */
const BEFORE_LINES = ['한 줄', '쓰는 장면', '물어보신 것', '돌아가는 순서', '화면', '기능이 놓인 자리']
const FULL_SHEET = '전체 설계 시트'

export function Sheet({ gate, text, items, state, busy, submit, palettes, reopen, brief }: {
  gate: 'r1' | 'r2' | 'r3'; text: string; items: string[]; state: Gate; busy: boolean
  submit: (body: object) => Promise<unknown>; palettes?: Palette[]; reopen?: () => Promise<unknown>; brief?: Brief
}) {
  const [decisions, setDecisions] = useState<Record<string, Decision>>(state.decisions)
  const [lines, setLines] = useState<Record<string, Decision>>({})
  // An edit saved before a redesign turn that failed is still the owner's: sent again until they change it.
  const [touched, setTouched] = useState<string[]>(() => items.filter((i) => !!state.decisions[i]?.edit?.trim()))
  const [palette, setPalette] = useState(state.palette ?? '')
  const [opened, setOpened] = useState<string[]>([])
  const [zoom, setZoom] = useState('')
  const since = useRef(0)
  useEffect(() => { since.current = Date.now() }, [])
  const sections = parts(text)
  const shown = sections.filter(([, s]) => s === null).map(([t]) => t).join('\n')
  // An item the shown text names sits with it; the rest belong to the folded sections.
  const folded = items.filter((i) => !named(shown, i))
  // On a brief, each confirmation line is decided and every other item is approved with it, unless the owner
  // decided it on the full sheet below: only those items are sent, each still needing a valid decision.
  const keys = brief?.lines ? Object.keys(brief.lines) : null
  const undecided = keys
    ? [...keys.filter((k) => !decided(lines[k])), ...touched.filter((i) => !decided(decisions[i]))]
    : items.filter((i) => !decided(decisions[i]))
  const needPalette = !!palettes?.length && !palette
  const set = (id: string, d: Decision) => {
    setDecisions((all) => ({ ...all, [id]: d }))
    setTouched((t) => (t.includes(id) ? t : [...t, id]))
  }
  const approveAll = (ids: string[]) => ids.forEach((i) => set(i, { approve: true }))
  const reading = () => ({ seconds: (Date.now() - since.current) / 1000, opened })
  const open = (name: string) => setOpened((o) => (o.includes(name) ? o : [...o, name]))
  const fullSheet = sections.map(([t, summary], n) => summary === null ? <Md key={n} text={t} zoom={setZoom} /> : (
    <details key={n} className="my-3 rounded-md border border-border px-4 py-2"
      onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) open(summary) }}>
      <summary className="cursor-pointer font-semibold">{summary}</summary>
      <Md text={t} zoom={setZoom} />
    </details>
  ))
  const sheetItems = <>
    <Items title="펼쳐진 항목" ids={items.filter((i) => !folded.includes(i))} decisions={decisions} marks={state.marks} set={set} />
    {folded.length > 0 && (
      <Items title="접힌 부분의 항목" ids={folded} decisions={decisions} marks={state.marks} set={set}
        approveAll={() => approveAll(folded)} />
    )}
  </>
  const picker = palettes && palettes.length > 0 && (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-2 font-semibold">색 배합 하나</legend>
      {palettes.map((p) => (
        <label key={p.id} className={cn('flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2', palette === p.id ? 'border-primary' : 'border-border')}>
          <input type="radio" name="palette" value={p.id} checked={palette === p.id} onChange={() => setPalette(p.id)} />
          {Object.values(p.colors).map((c) => <span key={c} className="size-4 rounded-sm border border-border" style={{ background: c }} />)}
          <span className="font-mono text-[12px]">{p.id}</span> {p.name}
        </label>
      ))}
    </fieldset>
  )
  const reopener = reopen && (
    <Btn disabled={busy} onClick={() => void reopen()} title="기능의 자리가 틀렸다: 화면 설계를 버리고 기능 보드로 돌아간다">기능 보드 다시 열기</Btn>
  )

  if (keys && brief) {
    // Every item no line covers is approved with the brief: none at R2, where each screen has its line.
    const covered = new Set(Object.values(brief.lines ?? {}).flat())
    const others = items.filter((i) => !covered.has(i)).length
    const ready = undecided.length ? `정하지 않은 것 ${undecided.length}개` : needPalette ? '색 배합을 골라라'
      : others ? `제출하면 나머지 항목 ${others}개는 요약대로 승인한다` : '모두 정했다'
    // The pictures the server gives a line or a section (R2's screens, R3's findings and its before and after).
    const shots = (key: string) => !!brief.pictures?.[key]?.length && (
      <span className="mt-1.5 flex flex-wrap gap-2">
        {brief.pictures[key].map((src, n) => (
          <button key={`${src}-${n}`} type="button" onClick={() => setZoom(src)} title="크게 본다">
            <img src={src} alt={brief.pictures![key].length > 1 ? `${key} 사진 ${n + 1}` : `${key} 화면`} loading="lazy"
              className="max-h-64 rounded border border-border" />
          </button>
        ))}
      </span>
    )
    const said = (names: string[]) => brief.sections?.filter((s) => names.includes(s.name)).map((s) => (
      <section key={s.name} aria-label={s.name} className="flex flex-col gap-1.5">
        <h3 className="font-heading text-[15px] font-semibold">{s.name}</h3>
        <p className="whitespace-pre-wrap">{s.text}</p>
        {shots(s.name)}
      </section>
    ))
    const after = brief.sections?.map((s) => s.name).filter((n) => !BEFORE_LINES.includes(n)) ?? []
    return (
      <Split label={`${gate.toUpperCase()} 요약`} sideLabel="결정" side={<>
        {picker}
        <Title><span>확인해 주세요</span><span className="font-mono">{keys.length - keys.filter((k) => !decided(lines[k])).length}/{keys.length}</span></Title>
        {brief.confirm?.map((c) => (
          <Item key={c.key} id={c.key} text={c.text} line d={lines[c.key]}
            set={(id, d) => setLines((all) => ({ ...all, [id]: d }))} />
        ))}
        {!keys.length && <p className="text-muted-foreground">따로 확인할 줄이 없다.</p>}
        <details className="flex flex-col gap-2" onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) open('전체 시트의 항목') }}>
          <summary className="cursor-pointer text-muted-foreground">전체 시트의 항목 {items.length}개 — 하나씩 고칠 수도 있다</summary>
          <div className="flex flex-col gap-3 pt-2">{sheetItems}</div>
        </details>
        <Decide>
          <span role="status" className="text-muted-foreground">{ready}</span>
          <div className="flex flex-wrap justify-end gap-2">
            {reopener}
            <Btn tone="wait" className={GO} disabled={busy || undecided.length > 0 || needPalette}
              onClick={() => void submit({ decisions: clean(decisions, touched), lines: clean(lines, keys), rest: true,
                palette: palette || undefined, reading: reading() })}>
              요약대로 제출
            </Btn>
          </div>
        </Decide>
      </>}>
        <div className="flex w-full max-w-[60rem] flex-col gap-5 px-10 py-6 max-[1500px]:px-7 max-[560px]:px-4">
          {said(BEFORE_LINES)}
          {keys.length > 0 && (
            <section aria-label="확인해 주세요 줄" className="flex flex-col gap-1.5 rounded-md bg-wait/10 px-4 py-3">
              <h3 className="font-heading text-[15px] font-semibold">확인해 주세요</h3>
              <ul className="flex list-disc flex-col gap-1 pl-5">
                {brief.confirm?.map((c) => (
                  <li key={c.key}>{c.text} <span className="font-mono text-[12px] text-faint">({c.key})</span>
                    {shots(c.key)}
                  </li>
                ))}
              </ul>
              <p className="text-[12.5px] text-muted-foreground">에이전트가 혼자 정한 것이다. 오른쪽에서 한 줄씩 승인하거나 고친다.</p>
            </section>
          )}
          {said(after)}
          <details className="rounded-md border border-border px-4 py-2" onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) open(FULL_SHEET) }}>
            <summary className="cursor-pointer font-semibold">{FULL_SHEET}</summary>
            {fullSheet}
          </details>
        </div>
        {zoom && <Zoom src={zoom} close={() => setZoom('')} />}
      </Split>
    )
  }

  // The sheet reads wide on the left; every decision on it sits beside, with [제출] held at the bottom.
  return (
    <Split label={`${gate.toUpperCase()} 시트`} sideLabel="결정" side={<>
      <Title><span>결정</span><span className="font-mono">{items.length - undecided.length}/{items.length}</span></Title>
      {picker}
      {sheetItems}
      <Decide>
        <span role="status" className="text-muted-foreground">
          {undecided.length ? `정하지 않은 항목 ${undecided.length}개` : needPalette ? '색 배합을 골라라' : '모두 정했다'}
        </span>
        <div className="flex flex-wrap justify-end gap-2">
          {reopener}
          <Btn tone="wait" className={GO} disabled={busy || undecided.length > 0 || needPalette}
            onClick={() => void submit({ decisions: clean(decisions, items), palette: palette || undefined,
              reading: reading() })}>
            제출
          </Btn>
        </div>
      </Decide>
    </>}>
      <div className="w-full max-w-[60rem] px-10 py-6 max-[1500px]:px-7 max-[560px]:px-4">
        {brief?.failed && <p role="status" className="mb-4 rounded-md bg-wait/10 px-3 py-2">요약을 쓰지 못해 전체 시트로 정한다 — {brief.failed}</p>}
        {fullSheet}
      </div>
      {zoom && <Zoom src={zoom} close={() => setZoom('')} />}
    </Split>
  )
}

/** A picture enlarged in its own modal dialog above the oneshot window: Escape, the backdrop or
 *  [닫기] close only it, and the focus goes back to the thumbnail that opened it. React passes a
 *  dialog's `close` and `cancel` up its tree, so they stop here before the window's own close. */
function Zoom({ src, close }: { src: string; close: () => void }) {
  const box = useRef<HTMLDialogElement>(null)
  const opener = useRef<Element | null>(null)
  useEffect(() => {
    if (!box.current || box.current.open) return
    opener.current = document.activeElement
    box.current.showModal()
  }, [])
  return (
    <dialog ref={box} aria-label="화면 사진" onCancel={(e) => e.stopPropagation()}
      onClose={(e) => { e.stopPropagation(); close(); (opener.current as HTMLElement | null)?.focus() }}
      onClick={(e) => { e.stopPropagation(); if (e.target === box.current) box.current.close() }}
      className="m-auto max-h-[95dvh] max-w-[95vw] bg-transparent p-0 backdrop:bg-black/70">
      <div className="flex flex-col items-end gap-2">
        <Btn autoFocus onClick={() => box.current?.close()}>닫기</Btn>
        <img src={src} alt="" className="max-h-[85dvh] max-w-full" />
      </div>
    </dialog>
  )
}

const decided = (d?: Decision) => !!d && (d.approve === true || !!d.edit?.trim())
const clean = (all: Record<string, Decision>, items: string[]) =>
  Object.fromEntries(items.map((i) => [i, all[i]?.edit?.trim() ? { edit: all[i].edit!.trim() } : { approve: true }]))

function Items({ title, ids, decisions, marks, set, approveAll }: {
  title: string; ids: string[]; decisions: Record<string, Decision>; marks?: Record<string, string>
  set: (id: string, d: Decision) => void; approveAll?: () => void
}) {
  // What needs a decision again comes first and open; what stays approved sits collapsed below. The split is
  // the sheet's as it opened (each round mounts its own), so deciding an item here never folds it away mid-edit.
  const [again] = useState(() => ids.filter((i) => !decided(decisions[i]) || marks?.[i]))
  if (!ids.length) return null
  const settled = ids.filter((i) => !again.includes(i))
  return (
    <section aria-label={title} className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <h4 className="font-semibold">{title}</h4>
        {approveAll && <Btn tone="ghost" onClick={approveAll}>모두 승인 ({ids.length})</Btn>}
      </div>
      {again.map((i) => <Item key={i} id={i} d={decisions[i]} mark={marks?.[i]} set={set} />)}
      {settled.length > 0 && (
        <details>
          <summary className="cursor-pointer text-muted-foreground">승인한 항목 {settled.length}개</summary>
          {settled.map((i) => <Item key={i} id={i} d={decisions[i]} set={set} />)}
        </details>
      )}
    </section>
  )
}

/** One decision: an item of the sheet, or with `line` one of the brief's confirmation lines and its `text`. */
function Item({ id, d, mark, set, text, line }: {
  id: string; d?: Decision; mark?: string; set: (id: string, d: Decision) => void; text?: string; line?: boolean
}) {
  const editing = d?.edit !== undefined
  const name = line ? `확인 줄 ${id}` : id
  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-border bg-background px-3 py-2"
      {...(line ? { 'data-line': id } : { 'data-item': id })}>
      <div className="flex items-center gap-2">
        <span className="font-mono">{id}</span>
        {mark && <span className="rounded bg-wait/15 px-1 text-[11px] text-wait">다시 볼 것</span>}
        <span className="flex-1" />
        <Btn tone={d?.approve ? 'primary' : 'plain'} aria-pressed={!!d?.approve} aria-label={`${name} 승인`}
          onClick={() => set(id, { approve: true })}>승인</Btn>
        <Btn tone={editing ? 'wait' : 'plain'} aria-pressed={editing} aria-label={`${name} 고침`}
          onClick={() => set(id, { edit: d?.edit ?? '' })}>고침</Btn>
      </div>
      {text && <p>{text}</p>}
      {editing && (
        <textarea aria-label={`${name} 고칠 내용`} value={d?.edit ?? ''} rows={2} onChange={(e) => set(id, { edit: e.target.value })}
          className="rounded-md border border-input bg-background px-2 py-1 text-foreground" />
      )}
    </div>
  )
}

// -- The finished run -------------------------------------------------------------------

const GATE_NAMES: Record<string, string> = { r1: 'R1 백엔드 설계', r2: 'R2 화면 설계', r3: 'R3 완성 화면' }

/** The one summary a finished run keeps: what was asked, each review and how it was read, what it cost. */
function Finished({ summary }: { summary: RunSummary }) {
  const s = summary.spend
  const hours = ((s.seconds ?? 0) / 3600).toFixed(1)
  return (
    <Top wide>
      <section aria-label="실행 요약" className="flex max-w-[72rem] flex-col gap-6">
        <dl className="grid grid-cols-4 gap-px overflow-hidden rounded-xl border border-border bg-border max-[1100px]:grid-cols-2">
          {([
            ['풀 리퀘스트', summary.pr
              ? <a key="pr" href={summary.pr.url} target="_blank" rel="noreferrer" className="underline underline-offset-4">#{summary.pr.number}</a> : '없음'],
            ['걸린 시간', `${hours}시간`],
            [s.unknown ? `비용 (추정 ${s.unknown}턴 포함)` : '비용', `$${(s.usd ?? 0).toFixed(2)}`],
            ['턴', String(s.calls ?? 0)],
          ] as [string, ReactNode][]).map(([k, v]) => (
            <div key={k} className="flex flex-col-reverse gap-1 bg-card px-[18px] py-4">
              <dt className="text-[12.5px] text-faint">{k}</dt>
              <dd className="font-heading text-[26px] font-semibold tabular-nums">{v}</dd>
            </div>
          ))}
        </dl>
        {summary.apk && (summary.apk.url
          ? <p>디버그 APK: <a href={summary.apk.url} target="_blank" rel="noreferrer" className="underline underline-offset-4">{summary.apk.name}</a> — 초안 프리릴리스</p>
          : <p className="text-faint">디버그 APK 를 Release 에 올리지 못했다 — {summary.apk.error}</p>)}
        <div className="overflow-x-auto">
          <table className="w-full text-left [&_td]:border-t [&_td]:border-border [&_td]:py-2.5 [&_td]:pr-6 [&_th]:pb-2 [&_th]:pr-6">
            <thead><tr className="text-[12.5px] text-faint"><th>검토</th><th>회차</th><th>고친 항목</th><th>읽은 시간</th><th>펼친 부분</th></tr></thead>
            <tbody className="tabular-nums">
              {summary.gates.map((g) => (
                <tr key={g.gate}>
                  <td>{GATE_NAMES[g.gate] ?? g.gate}</td><td>{g.rounds}</td><td>{g.edited.length}</td>
                  <td>{Math.round(g.reading.reduce((n, r) => n + (r.seconds ?? 0), 0) / 60)}분</td>
                  <td>{[...new Set(g.reading.flatMap((r) => r.opened))].join(', ') || '없음'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-[12.5px] text-muted-foreground">화면 찍기 {summary.looks}회 · 화면 비평 {summary.critiques}회</p>
      </section>
    </Top>
  )
}
