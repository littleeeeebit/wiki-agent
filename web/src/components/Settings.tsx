import { useCallback, useEffect, useState } from 'react'
import type { Dispatch, ReactNode, SetStateAction } from 'react'
import { Btn, Modal } from '@/components/Modal'
import { MobileSettings } from '@/components/MobileAccess'
import * as api from '@/lib/api'
import { FAMILY } from '@/lib/run'
import { notify, requestNotifications } from '@/lib/notifications'
import type { Hub, Jev, JevLimits, JevMode, LoopSettings, Options, Probe, SurveySettings, Switch } from '@/lib/api'

type Props = {
  sw: Switch | null
  theme: 'dark' | 'light'
  options: Options | null
  loop: LoopSettings | null
  onSwitch: (mode: api.TranslationMode) => void
  onTheme: (theme: 'dark' | 'light') => void
  onLoop: (s: LoopSettings) => Promise<void>
  onClose: () => void
}

const field = 'h-7 rounded-md border border-input bg-background px-2 font-mono text-[12px]'

/** Everything the whole window shares, in four parts: general, connection,
 *  questions (Jev), review. What a part edits is saved by that part's own button. */
export function Settings({ sw, theme, options, loop, onSwitch, onTheme, onLoop, onClose }: Props) {
  const [survey, setSurvey] = useState<SurveySettings | null>(null)
  const [savedSurvey, setSavedSurvey] = useState<SurveySettings | null>(null)
  const [structure, setStructure] = useState<api.StructureSettings | null>(null)
  const [savedStructure, setSavedStructure] = useState<api.StructureSettings | null>(null)
  const [hub, setHub] = useState<Hub | null>(null)
  const [rounds, setRounds] = useState(0)
  const [seats, setSeats] = useState(0)
  const [working, setWorking] = useState('')
  const [fault, setFault] = useState('')
  const [notification, setNotification] = useState('')

  useEffect(() => {
    api.getConnect()
      .then(({ settings, hub }) => {
        setSurvey(settings)
        setSavedSurvey(settings)
        setHub(hub)
      })
      .catch((err) => setFault(String(err instanceof Error ? err.message : err)))
    api.getStructureSettings()
      .then((s) => { setStructure(s); setSavedStructure(s) })
      .catch((err) => setFault(String(err instanceof Error ? err.message : err)))
  }, [])

  // The form follows the server's values: opened before they arrive, a form
  // seeded with guesses would show them and [저장] would write them.
  useEffect(() => {
    if (!loop) return
    setRounds(loop.rounds)
    setSeats(loop.concurrent)
  }, [loop])

  async function act(key: string, fn: () => Promise<void>) {
    setFault('')
    setWorking(key)
    try {
      await fn()
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
    } finally {
      setWorking('')
    }
  }

  const loopEdited = !!loop && (rounds !== loop.rounds || seats !== loop.concurrent)
  const surveyEdited = JSON.stringify(survey) !== JSON.stringify(savedSurvey)
  const structureEdited = JSON.stringify(structure) !== JSON.stringify(savedStructure)
  // Jev's part keeps its own form and hands up how to save it while it differs from the server's.
  const [jevSave, setJevSave] = useState<(() => Promise<void>) | null>(null)
  const edited = loopEdited || surveyEdited || structureEdited || !!jevSave
  // One [저장] for the window: each part that differs goes to its own endpoint.
  const save = () => act('save', async () => {
    if (survey && surveyEdited) {
      const saved = await api.setSurveySettings(survey)
      setSurvey(saved); setSavedSurvey(saved)
    }
    if (loop && loopEdited) await onLoop({ ...loop, rounds, concurrent: seats })
    if (structure && structureEdited) {
      const saved = await api.setStructureSettings(structure)
      setStructure(saved); setSavedStructure(saved)
    }
    await jevSave?.()
  })
  const usage = sw?.usage
  const models = options?.models.filter((m) => m.id) ?? []
  // A model's own efforts, as the toolbar reads them; a model without a list
  // takes the CLI's five.
  const efforts = (id: string) => options?.models.find((m) => m.id === id)?.efforts ?? options?.efforts ?? []
  const effortPicker =(id: string, value: string, set: (v: string) => void) => (
    <select value={value} aria-label="추론 강도" onChange={(e) => set(e.target.value)} className={`${field} w-24 font-sans`}>
      {!efforts(id).some((e) => e.id === value) && <option value={value}>{value}</option>}
      {efforts(id).map((e) => <option key={e.id} value={e.id}>{e.label}</option>)}
    </select>
  )

  return (
    <Modal title="설정" onClose={onClose} foot={
      <Btn tone="primary" disabled={!edited || working === 'save'} onClick={save}>{working === 'save' ? '저장하는 중…' : '저장'}</Btn>}>
      <div className="space-y-5">
        <Part title="일반">
          <Row label="한국어 번역" note={usage ? `이번 달 ${usage.usd == null ? '?' : `$${usage.usd.toFixed(2)}`} / $${usage.limit.toFixed(0)}` : undefined}>
            <select aria-label="한국어 번역 모드" className={`${field} font-sans`}
              value={sw?.mode ?? (sw?.translate ? 'full' : 'off')} disabled={!sw}
              onChange={(e) => onSwitch(e.target.value as api.TranslationMode)}>
              <option value="off">끄기</option>
              <option value="full">전체 활성화</option>
              <option value="partial">일부 활성화</option>
            </select>
          </Row>
          <Row label="어두운 화면" note="이 기계에만 기억한다">
            <input type="checkbox" role="switch" className="size-4 accent-primary" checked={theme === 'dark'}
              onChange={(e) => onTheme(e.target.checked ? 'dark' : 'light')} />
          </Row>
        </Part>

        <MobileSettings />

        <Part title="연결">
          <Row label="허브" note={hub ? (hub.refused ? `옮길 수 없다 — ${hub.refused}`
            : hub.needed ? '옛 허브를 가리킨다 · 첫 [연결] 때 옮긴다' : undefined) : '읽는 중…'}>
            <span className="font-mono text-[12px]">{hub?.name ?? ''}</span>
          </Row>
          {survey && (
            <>
              <Row label="연결 조사 토큰 한도">
                <input type="number" min={10000} step={100000} value={survey.survey_tokens} className={`${field} w-32`}
                  onChange={(e) => setSurvey({ ...survey, survey_tokens: Number(e.target.value) })} />
              </Row>
              <Row label="연결 조사 시간 한도 (분)">
                <input type="number" min={1} value={survey.survey_minutes} className={`${field} w-20`}
                  onChange={(e) => setSurvey({ ...survey, survey_minutes: Number(e.target.value) })} />
              </Row>
              <Row label="연결 조사 모델">
                <span className="flex gap-1.5">
                  <select value={survey.survey_model} className={`${field} w-32 font-sans`}
                    onChange={(e) => setSurvey({ ...survey, survey_model: e.target.value, survey_effort: '' })}>
                    {!models.some((m) => m.id === survey.survey_model) && (
                      <option value={survey.survey_model}>{survey.survey_model || 'CLI 기본'}</option>
                    )}
                    {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </select>
                  {effortPicker(survey.survey_model, survey.survey_effort,
                    (v) => setSurvey({ ...survey, survey_effort: v }))}
                </span>
              </Row>
            </>
          )}
        </Part>

        <Part title="알림">
          <Row label="질문 · 완료 · 실패" note="연결된 앱의 시스템 알림으로 받는다">
            <Btn onClick={() => act('notifications', async () => {
              const granted = await requestNotifications()
              setNotification(granted ? '알림 허용됨' : '시스템 설정에서 알림을 허용해라')
              if (granted) await notify('wiki-agent 알림 확인', '질문, 완료와 실패 알림을 받을 수 있다.')
            })}>알림 확인</Btn>
          </Row>
          {notification && <p role="status">{notification}</p>}
        </Part>
        <JevPart onEdited={setJevSave} />

        <Part title="리뷰">
          {!loop ? <p className="text-faint">읽는 중…</p> : (<>
          <Row label="라운드 상한" note="넘으면 멈추고 [계속] 을 기다린다">
            <input type="number" min={1} max={50} value={rounds} className={`${field} w-20`}
              onChange={(e) => setRounds(Number(e.target.value))} />
          </Row>
          <Row label="동시 실행" note="한꺼번에 도는 루프 수">
            <input type="number" min={1} max={10} value={seats} className={`${field} w-20`}
              onChange={(e) => setSeats(Number(e.target.value))} />
          </Row>
          </>)}
          {structure && (<>
          <Row label="구조 문서 새로 고침 모델" note="머지 때 .omm 갱신 · 고르지 않으면 건너뛴다">
            <span className="flex gap-1.5">
              <select value={structure.omm_model} aria-label="구조 문서 새로 고침 모델" className={`${field} w-32 font-sans`}
                onChange={(e) => setStructure({ omm_model: e.target.value, omm_effort: '' })}>
                <option value="">고르지 않음</option>
                {structure.omm_model && !models.some((m) => m.id === structure.omm_model) &&
                  <option value={structure.omm_model}>{structure.omm_model}</option>}
                {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
              {structure.omm_model && effortPicker(structure.omm_model, structure.omm_effort,
                (v) => setStructure({ ...structure, omm_effort: v }))}
            </span>
          </Row>
          </>)}
        </Part>

        {fault && <p role="alert" className="whitespace-pre-wrap text-destructive">{fault}</p>}
      </div>
    </Modal>
  )
}

function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2.5">
      <h3 className="font-heading text-[11px] font-semibold text-faint">{title}</h3>
      {children}
    </section>
  )
}

function Row({ label, note, children }: { label: string; note?: string; children: ReactNode }) {
  return (
    <label className="flex items-center justify-between gap-4">
      <span className="min-w-0">
        <span className="block">{label}</span>
        {note && <span className="block text-[12.5px] text-faint">{note}</span>}
      </span>
      <span className="shrink-0">{children}</span>
    </label>
  )
}

// Where each Jev setting came from, as a person reads it.
const SOURCE: Record<Jev['mode_source'], string> = {
  app: '이 화면에서 정함', file: '.env 파일', environment: '환경 변수', legacy: '옛 설정 이름', default: '기본값',
}
// The mode this screen saved, if it saved one: active even where a canary makes this checkout shadow,
// so saving the form here never drops it.
const saved = (j: Jev): JevMode | null => j.mode_source === 'app' ? (j.canary ? 'active' : j.mode) : null
const HEALTH: Record<Jev['health'], string> ={ configured: '설정됨', disabled: '꺼짐', unavailable: '쓸 수 없음' }
const PROBED: Record<Probe['health'], string> = { reachable: '응답함', auth_failed: '키가 거절됨', unavailable: '닿지 않음' }
const MODES: { id: JevMode; label: string; note: string }[] = [
  { id: 'off', label: '끔', note: '기본 검색과 답만' },
  { id: 'shadow', label: '그림자', note: '뒤에서 판단만 기록하고 답은 기본대로' },
  { id: 'active', label: '켬', note: '판단이 검색을 이끌고, 에이전트가 근거를 종합해 답한다 (문장별 검증 없음)' },
]
const LIMIT: { id: keyof JevLimits; label: string; min: number; max: number; step: number }[] = [
  { id: 'seconds', label: '시간 (초)', min: 5, max: 120, step: 5 },
  { id: 'calls', label: '판단 호출', min: 1, max: 20, step: 1 },
  { id: 'candidates', label: '후보 수', min: 1, max: 200, step: 10 },
]

/** Jev: whether it is reachable, how a question uses it, and what a question
 *  may search. The key is never shown or edited here — only whether one exists. */
function JevPart({ onEdited }: { onEdited: Dispatch<SetStateAction<(() => Promise<void>) | null>> }) {
  const [jev, setJev] = useState<Jev | null>(null)
  const [mode, setMode] = useState<JevMode>('off')
  const [off, setOff] = useState<string[]>([])
  const [limits, setLimits] = useState<JevLimits | null>(null)
  const [probe, setProbe] = useState<Probe | null>(null)
  const [working, setWorking] = useState('')
  const [fault, setFault] = useState('')

  const seed = useCallback((j: Jev) => {
    setJev(j)
    setMode(saved(j) ?? j.mode)
    setOff(j.disabled_sources)
    setLimits(j.limits)
  }, [])
  useEffect(() => {
    api.getJev().then(seed).catch((err) => setFault(String(err instanceof Error ? err.message : err)))
  }, [seed])

  async function act(key: string, fn: () => Promise<void>) {
    setFault('')
    setWorking(key)
    try {
      await fn()
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
    } finally {
      setWorking('')
    }
  }

  const edited = !!jev && !!limits && (mode !== (saved(jev) ?? jev.mode)
    || JSON.stringify([...off].sort()) !== JSON.stringify([...jev.disabled_sources].sort())
    || JSON.stringify(limits) !== JSON.stringify(jev.limits))
  // Saved by the window's one [저장]. A mode left as the file sets it keeps following the file.
  useEffect(() => {
    const store = async () => seed(await api.setJev({
      mode: jev && saved(jev) === null && mode === jev.mode ? null : mode, disabled_sources: off, limits: limits! }))
    onEdited(edited ? () => store : null)
  }, [edited, jev, mode, off, limits, onEdited, seed])

  if (!jev || !limits) {
    return <Part title="질문 (Jev)"><p className="text-faint">{fault || '읽는 중…'}</p></Part>
  }
  return (
    <Part title="질문 (Jev)">
      <Row label="상태" note={jev.problem ? `${HEALTH[jev.health]} — ${jev.problem}`
        : `모델 ${jev.model} · 키 ${jev.key ? `있음 (${jev.key_source})` : '없음'}`}>
        <span className="flex items-center gap-2">
          <span role="status" className={`font-mono text-[10.5px] ${jev.health === 'unavailable' ? 'text-destructive' : 'text-faint'}`}>
            {HEALTH[jev.health]}{probe && ` · ${PROBED[probe.health]}${probe.category ? ` (${probe.category})` : ''}`}
          </span>
          <Btn disabled={working === 'probe' || !jev.key} onClick={() => act('probe', async () => setProbe(await api.probeJev()))}>
            {working === 'probe' ? '시험하는 중…' : '연결 시험'}
          </Btn>
        </span>
      </Row>
      <fieldset className="space-y-1.5">
        <legend className="mb-1">모드 <span className="text-[12.5px] text-faint">· 지금 {jev.mode} ({SOURCE[jev.mode_source]})
          {jev.active_projects.length > 0 && ` · 켬은 지정한 체크아웃 ${jev.active_projects.length}곳에서만`}</span></legend>
        <div role="radiogroup" aria-label="Jev 모드" className="grid grid-cols-2 gap-1.5">
          {MODES.map((m) => (
            <label key={m.label} className={`flex min-h-9 cursor-pointer items-start gap-2 rounded-md border px-2 py-1.5 ${
              mode === m.id ? 'border-primary' : 'border-border'}`}>
              <input type="radio" name="jev-mode" className="mt-0.5 accent-primary" checked={mode === m.id}
                onChange={() => setMode(m.id)} />
              <span className="min-w-0">
                <span className="block text-[12.5px]">{m.label}{mode === m.id && ' ✓'}</span>
                <span className="block text-[12.5px] text-faint">{m.note}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend className="mb-1">찾는 곳 <span className="text-[12.5px] text-faint">· 끈 곳은 질문이 찾지 않는다</span></legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {Object.keys(FAMILY).map((f) => (
            <label key={f} className="flex min-h-7 items-center gap-1.5 text-[12.5px]">
              <input type="checkbox" className="size-4 accent-primary" checked={!off.includes(f)}
                onChange={(e) => setOff(e.target.checked ? off.filter((x) => x !== f) : [...off, f])} />
              {FAMILY[f]}
            </label>
          ))}
        </div>
      </fieldset>
      {LIMIT.map((l) => (
        <Row key={l.id} label={`한 질문의 ${l.label}`} note={`${l.min}–${l.max}`}>
          <input type="number" min={l.min} max={l.max} step={l.step} value={limits[l.id]} className={`${field} w-20`}
            onChange={(e) => setLimits({ ...limits, [l.id]: Number(e.target.value) })} />
        </Row>
      ))}
      {fault && <p role="alert" className="whitespace-pre-wrap text-destructive">{fault}</p>}
    </Part>
  )
}
