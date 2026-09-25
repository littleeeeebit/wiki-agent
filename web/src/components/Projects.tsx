import { useCallback, useEffect, useState } from 'react'
import * as api from '@/lib/api'
import type { ConnectPlan, Project, SurveySettings } from '@/lib/api'
import { cn } from '@/lib/utils'

// Temporary, as stage 5 of the loop plan says: stage 6 rebuilds this as the
// middle pane's project list. What it shows is read by a person, in Korean.

const DOT: Record<Project['state'], string> = { '연결 완료': 'bg-primary', 일부: 'bg-wait', 미연결: 'bg-border' }

const kilo = (n: number) => (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : `${Math.round(n / 1000)}k`)
const minutes = (s: number) => `${Math.max(1, Math.round(s / 60))}분`

/** Every repository in the workspace, its connection, `[연결]` and `[다시 시험]`. */
export function Projects() {
  const [rows, setRows] = useState<Project[]>([])
  const [settings, setSettings] = useState<SurveySettings | null>(null)
  const [fault, setFault] = useState('')
  const [working, setWorking] = useState('')
  const [asking, setAsking] = useState<{ name: string; plan: ConnectPlan } | null>(null)

  const read = useCallback(() => {
    api.getConnect()
      .then(({ rows, settings }) => {
        setRows(rows)
        setSettings((now) => now ?? settings)
      })
      .catch((err) => setFault(String(err instanceof Error ? err.message : err)))
  }, [])

  // Read on focus — a handover left waiting is tried again by this read — and
  // whenever the server says a connection changed.
  useEffect(() => {
    read()
    window.addEventListener('focus', read)
    window.addEventListener('connect-changed', read)
    return () => {
      window.removeEventListener('focus', read)
      window.removeEventListener('connect-changed', read)
    }
  }, [read])

  async function act(key: string, fn: () => Promise<void>) {
    setFault('')
    setWorking(key)
    try {
      await fn()
    } catch (err) {
      setFault(String(err instanceof Error ? err.message : err))
    } finally {
      setWorking('')
      read()
    }
  }

  // The plan first. With nothing to confirm — no hub move, no survey — it
  // connects at once; otherwise the confirmation shows every line.
  const begin = (name: string) => act(name, async () => {
    const plan = await api.connectPlan(name)
    if (!plan.hub.needed && !plan.survey) {
      await api.connect(name, { hub: '', survey: false })
    } else {
      setAsking({ name, plan })
    }
  })

  const confirm = (survey: boolean) => {
    if (!asking) return
    const { name, plan } = asking
    setAsking(null)
    void act(name, () => api.connect(name, { hub: plan.hub.needed ? plan.hub.digest : '', survey }).then(() => {}))
  }

  return (
    <div className="h-full overflow-y-auto px-5 py-4">
      <div className="mb-3 font-heading text-[14px] font-semibold">프로젝트</div>

      {settings && (
        <form
          className="mb-4 flex flex-wrap items-end gap-3 rounded-md border border-border bg-card px-3 py-2 text-[12.5px]"
          onSubmit={(e) => {
            e.preventDefault()
            void act('settings', async () => setSettings(await api.setSurveySettings(settings)))
          }}
        >
          <label className="flex items-center gap-1.5">
            <input type="checkbox" className="accent-primary" checked={settings.survey}
              onChange={(e) => setSettings({ ...settings, survey: e.target.checked })} />
            [연결] 이 전수조사까지 한다
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-faint">토큰 한도</span>
            <input type="number" min={10000} step={100000} value={settings.survey_tokens}
              className="w-28 rounded border border-input bg-background px-1.5 py-0.5 font-mono"
              onChange={(e) => setSettings({ ...settings, survey_tokens: Number(e.target.value) })} />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-faint">시간 한도 (분)</span>
            <input type="number" min={1} value={settings.survey_minutes}
              className="w-20 rounded border border-input bg-background px-1.5 py-0.5 font-mono"
              onChange={(e) => setSettings({ ...settings, survey_minutes: Number(e.target.value) })} />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-faint">조사 모델</span>
            <input value={settings.survey_model}
              className="w-28 rounded border border-input bg-background px-1.5 py-0.5 font-mono"
              onChange={(e) => setSettings({ ...settings, survey_model: e.target.value })} />
          </label>
          <button type="submit" disabled={!!working}
            className="rounded-md border border-border px-2 py-1 hover:bg-secondary disabled:opacity-40">
            {working === 'settings' ? '저장하는 중…' : '저장'}
          </button>
        </form>
      )}

      {fault && <div role="alert" className="mb-3 whitespace-pre-wrap text-[12.5px] text-destructive">{fault}</div>}

      <ul className="space-y-1.5">
        {rows.map((r) => (
          <li key={r.id} className="rounded-md border border-border px-3 py-2 text-[12.5px]">
            <div className="flex items-center gap-2">
              <span className={cn('size-2 shrink-0 rounded-full', DOT[r.state])} />
              <span className="font-mono">{r.id}</span>
              <span className="text-muted-foreground">{r.state}</span>
              {r.probing && <span className="animate-pulse text-faint">시험 중…</span>}
              <span className="ml-auto flex gap-1.5">
                {r.state !== '연결 완료' && (
                  <button type="button" disabled={!!working || r.probing} onClick={() => void begin(r.id)}
                    className="rounded border border-primary px-2 py-0.5 text-primary disabled:opacity-40">
                    {working === r.id ? '연결하는 중…' : '연결'}
                  </button>
                )}
                {r.state !== '미연결' && (
                  <button type="button" disabled={!!working || r.probing}
                    onClick={() => void act(`probe:${r.id}`, () => api.reprobe(r.id).then(() => {}))}
                    className="rounded border border-border px-2 py-0.5 hover:bg-secondary disabled:opacity-40"
                    title="Claude 와 Codex 의 실제 세션을 한 턴씩 돌린다. 모델 두 턴의 비용이 든다">
                    다시 시험
                  </button>
                )}
              </span>
            </div>
            {r.missing.length > 0 && (
              <ul className="mt-1 pl-4 text-[12px] text-wait">
                {r.missing.map((m) => <li key={m} className="whitespace-pre-wrap">· {m}</li>)}
              </ul>
            )}
            {r.notes.length > 0 && <div className="mt-0.5 pl-4 text-[12px] text-faint">{r.notes.join(' · ')}</div>}
            {r.survey?.state && (
              <div className="mt-0.5 pl-4 text-[12px] text-muted-foreground">
                조사 {r.survey.state}
                {r.survey.turns ? ` · 턴 ${r.survey.turn}/${r.survey.turns}` : ''}
                {r.survey.label ? ` · ${r.survey.label}` : ''}
                {r.survey.tokens !== undefined ? ` · ${kilo(r.survey.tokens)} 토큰` : ''}
                {r.survey.why ? ` · ${r.survey.why}에서 멈춤` : ''}
                {r.survey.reason ? ` · ${r.survey.reason}` : ''}
              </div>
            )}
          </li>
        ))}
      </ul>

      {asking && (
        <div role="dialog" aria-modal="true" aria-label="연결 확인"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setAsking(null)}>
          <div className="max-h-[85vh] w-[40rem] max-w-[calc(100vw-2rem)] overflow-y-auto rounded-lg border border-border bg-card p-4 text-[12.5px]"
            onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 font-heading text-[13px] font-semibold">{asking.name} 연결</div>

            {asking.plan.hub.refused && (
              <p className="mb-2 whitespace-pre-wrap text-destructive">허브를 옮길 수 없다 — {asking.plan.hub.refused}</p>
            )}
            {asking.plan.hub.needed && !asking.plan.hub.refused && (
              <section className="mb-3">
                <div className="mb-1 font-semibold">허브 이전 — 이 기계에 한 번. 사용자 단위 설정이 이렇게 바뀐다</div>
                <ul className="space-y-0.5 font-mono text-[11.5px]">
                  {asking.plan.hub.lines.map((l, i) => <li key={i}><span className="text-faint">{l.file}</span> {l.change}</li>)}
                  {asking.plan.hub.links.map((l) => (
                    <li key={l.link}>
                      <span className="text-faint">{l.link}</span>{' '}
                      {l.to ? `→ ${l.to}` : `그대로 둔다 — 이 허브에 같은 이름의 스킬이 없다 (${l.from})`}
                    </li>
                  ))}
                  {asking.plan.hub.trust && <li>Codex: 이 허브의 hook.py 를 부르는 위키 훅을 신뢰로 기록한다 (--trust-codex)</li>}
                </ul>
              </section>
            )}
            {asking.plan.adapter && (
              <section className="mb-3">
                <div className="mb-1 font-semibold">원본 체크아웃에 쓰는 .wiki/adapter.toml</div>
                <ul className="font-mono text-[11.5px]">
                  {Object.entries(asking.plan.adapter).map(([k, v]) => <li key={k}>{k} = {v || <span className="text-wait">채워야 함</span>}</li>)}
                </ul>
              </section>
            )}
            {asking.plan.unwire.length > 0 && (
              <section className="mb-3">
                <div className="mb-1 font-semibold">옛 프로젝트 단위 hook</div>
                <ul className="font-mono text-[11.5px]">{asking.plan.unwire.map((u) => <li key={u}>{u}</li>)}</ul>
              </section>
            )}
            {asking.plan.survey && (
              <section className="mb-3">
                <div className="mb-1 font-semibold">전수조사 견적</div>
                <p>
                  파일 {asking.plan.survey.files}개 · 코드 {kilo(asking.plan.survey.code)}B · 문서 {kilo(asking.plan.survey.docs)}B
                  · 커밋 {asking.plan.survey.commits} · 머지된 PR {asking.plan.survey.prs} · 모듈 {asking.plan.survey.modules} · 턴 {asking.plan.survey.turns}
                </p>
                <p className="mt-0.5">
                  예상 {kilo(asking.plan.survey.tokens)} 토큰, {minutes(asking.plan.survey.seconds)}
                  {' '}(한도 {kilo(asking.plan.survey.limit.tokens)} 토큰, {minutes(asking.plan.survey.limit.seconds)})
                </p>
                {asking.plan.survey.over && <p className="mt-0.5 text-wait">한도에서 멈춘다 — 모듈 페이지 일부만</p>}
              </section>
            )}

            <div className="mt-3 flex justify-end gap-1.5">
              <button type="button" className="rounded-md border border-border px-2 py-1" onClick={() => setAsking(null)}>닫기</button>
              {asking.plan.survey && (
                <button type="button" disabled={!!asking.plan.hub.refused}
                  className="rounded-md border border-border px-2 py-1 disabled:opacity-40" onClick={() => confirm(false)}>
                  조사 없이 연결
                </button>
              )}
              <button type="button" disabled={!!asking.plan.hub.refused}
                className="rounded-md border border-primary px-2 py-1 text-primary disabled:opacity-40"
                onClick={() => confirm(!!asking.plan.survey)}>
                {asking.plan.survey ? '확인 — 조사까지' : '확인'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
