import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Btn, Modal } from '@/components/Modal'
import * as api from '@/lib/api'
import type { Hub, LoopSettings, Options, SurveySettings, Switch } from '@/lib/api'

type Props = {
  sw: Switch | null
  theme: 'dark' | 'light'
  options: Options | null
  loop: LoopSettings | null
  onSwitch: (on: boolean) => void
  onTheme: (theme: 'dark' | 'light') => void
  onLoop: (s: LoopSettings) => Promise<void>
  onClose: () => void
  onProjects: () => void
}

const field = 'h-7 rounded-md border border-input bg-background px-2 font-mono text-[12px]'

/** Everything the whole window shares, in three parts: general, connection,
 *  review. What a part edits is saved by that part's own button. */
export function Settings({ sw, theme, options, loop, onSwitch, onTheme, onLoop, onClose, onProjects }: Props) {
  const [survey, setSurvey] = useState<SurveySettings | null>(null)
  const [savedSurvey, setSavedSurvey] = useState<SurveySettings | null>(null)
  const [hub, setHub] = useState<Hub | null>(null)
  const [bypass, setBypass] = useState<boolean | null>(null)
  const [rounds, setRounds] = useState(0)
  const [seats, setSeats] = useState(0)
  const [model, setModel] = useState('')
  const [effort, setEffort] = useState('')
  const [working, setWorking] = useState('')
  const [fault, setFault] = useState('')

  useEffect(() => {
    api.getConnect()
      .then(({ settings, hub }) => {
        setSurvey(settings)
        setSavedSurvey(settings)
        setHub(hub)
      })
      .catch((err) => setFault(String(err instanceof Error ? err.message : err)))
    api.getWorkSettings()
      .then((s) => setBypass(s.bypass))
      .catch((err) => setFault(String(err instanceof Error ? err.message : err)))
  }, [])

  // The form follows the server's values: opened before they arrive, a form
  // seeded with guesses would show them and [저장] would write them.
  useEffect(() => {
    if (!loop) return
    setRounds(loop.rounds)
    setSeats(loop.concurrent)
    setModel(loop.review_model)
    setEffort(loop.review_effort)
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

  const loopEdited = !!loop && (rounds !== loop.rounds || seats !== loop.concurrent || model !== loop.review_model
    || effort !== loop.review_effort)
  const surveyEdited = JSON.stringify(survey) !== JSON.stringify(savedSurvey)
  const usage = sw?.usage
  const models = options?.models.filter((m) => m.id) ?? []
  // A model's own efforts, as the toolbar reads them; a model without a list
  // takes the CLI's five.
  const efforts = (id: string) => options?.models.find((m) => m.id === id)?.efforts ?? options?.efforts ?? []
  // The review's empty model is Codex's default, not the Claude CLI's.
  const reviewing = (id: string) => id || options?.models.find((m) => m.is_default)?.id || ''
  const effortPicker =(id: string, value: string, set: (v: string) => void) => (
    <select value={value} aria-label="추론 강도" onChange={(e) => set(e.target.value)} className={`${field} w-24 font-sans`}>
      {!efforts(id).some((e) => e.id === value) && <option value={value}>{value}</option>}
      {efforts(id).map((e) => <option key={e.id} value={e.id}>{e.label}</option>)}
    </select>
  )

  return (
    <Modal title="설정" onClose={onClose}>
      <div className="space-y-5">
        <Part title="일반">
          <Row label="한국어 번역" note={usage ? `이번 달 ${usage.usd == null ? '?' : `$${usage.usd.toFixed(2)}`} / $${usage.limit.toFixed(0)}` : undefined}>
            <input type="checkbox" role="switch" className="size-4 accent-primary" checked={sw?.translate ?? false}
              disabled={!sw} onChange={(e) => onSwitch(e.target.checked)} />
          </Row>
          <Row label="작업 권한 묻지 않기"
            note="켜면 작업 에이전트가 작업트리에서 묻지 않고 쓰고 실행한다(bypass). 다음 턴부터 적용된다">
            <input type="checkbox" role="switch" className="size-4 accent-primary" checked={bypass ?? false}
              disabled={bypass === null || working === 'bypass'}
              onChange={(e) => {
                const on = e.target.checked
                void act('bypass', async () => setBypass((await api.setWorkSettings({ bypass: on })).bypass))
              }} />
          </Row>
          <Row label="어두운 화면" note="이 기계에만 기억한다">
            <input type="checkbox" role="switch" className="size-4 accent-primary" checked={theme === 'dark'}
              onChange={(e) => onTheme(e.target.checked ? 'dark' : 'light')} />
          </Row>
        </Part>

        <Part title="연결">
          <Row label="허브" note={hub ? (hub.refused ? `옮길 수 없다 — ${hub.refused}`
            : hub.needed ? '이 기계의 hook 과 스킬 링크가 아직 옛 허브를 가리킨다. 첫 [연결] 때 바뀔 줄을 보여 주고 옮긴다'
              : '이 기계의 hook 과 스킬 링크가 이 허브를 가리킨다') : '읽는 중…'}>
            <span className="font-mono text-[12px]">{hub?.name ?? ''}</span>
          </Row>
          <Row label="저장소마다 [연결]" note="위키가 붙었는지 보고 붙인다">
            <Btn onClick={onProjects}>프로젝트 목록 ▸</Btn>
          </Row>
          {survey && (
            <>
              <Row label="[연결] 이 전수조사까지" note="켜면 견적을 보여 주고 확인받는다">
                <input type="checkbox" role="switch" className="size-4 accent-primary" checked={survey.survey}
                  onChange={(e) => setSurvey({ ...survey, survey: e.target.checked })} />
              </Row>
              <Row label="토큰 한도">
                <input type="number" min={10000} step={100000} value={survey.survey_tokens} className={`${field} w-32`}
                  onChange={(e) => setSurvey({ ...survey, survey_tokens: Number(e.target.value) })} />
              </Row>
              <Row label="시간 한도 (분)">
                <input type="number" min={1} value={survey.survey_minutes} className={`${field} w-20`}
                  onChange={(e) => setSurvey({ ...survey, survey_minutes: Number(e.target.value) })} />
              </Row>
              <Row label="조사 모델">
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
              <Save edited={surveyEdited} busy={working === 'survey'} onSave={() => act('survey', async () => {
                const saved = await api.setSurveySettings(survey)
                setSurvey(saved)
                setSavedSurvey(saved)
              })} />
            </>
          )}
        </Part>

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
          <Row label="리뷰 모델">
            <span className="flex gap-1.5">
              <select value={model} className={`${field} w-32 font-sans`} onChange={(e) => {
                // The effort goes with the model: one the new model does not take is dropped.
                setModel(e.target.value)
                if (!efforts(reviewing(e.target.value)).some((x) => x.id === effort)) setEffort('')
              }}>
                <option value="">Codex 기본</option>
                {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
              {effortPicker(reviewing(model), effort, setEffort)}
            </span>
          </Row>
          <Save edited={loopEdited} busy={working === 'loop'}
            onSave={() => act('loop', () => onLoop({ rounds, concurrent: seats, review_model: model, review_effort: effort }))} />
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

function Save({ edited, busy, onSave }: { edited: boolean; busy: boolean; onSave: () => void }) {
  return (
    <div className="flex justify-end">
      <Btn tone="primary" disabled={!edited || busy} onClick={onSave}>{busy ? '저장하는 중…' : '저장'}</Btn>
    </div>
  )
}
