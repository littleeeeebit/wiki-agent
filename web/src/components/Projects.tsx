import { useCallback, useEffect, useState } from 'react'
import { Btn, Modal } from '@/components/Modal'
import * as api from '@/lib/api'
import type { ConnectPlan, Project } from '@/lib/api'
import { cn } from '@/lib/utils'

// `일부` is a person's to finish, so it takes the one warm colour.
const DOT: Record<Project['state'], string> = { '연결 완료': 'bg-primary', 일부: 'bg-wait', 미연결: 'bg-border' }

const kilo = (n: number) => (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : `${Math.round(n / 1000)}k`)
const minutes = (s: number) => `${Math.max(1, Math.round(s / 60))}분`

/** The middle pane's project list: every repository in the workspace, how far
 *  the wiki is attached to it, `[연결]` and `[다시 시험]`. The survey's own
 *  settings are the settings modal's. */
export function Projects({ current }: { current: string }) {
  const [rows, setRows] = useState<Project[]>([])
  const [fault, setFault] = useState('')
  const [working, setWorking] = useState('')
  const [asking, setAsking] = useState<{ name: string; plan: ConnectPlan } | null>(null)

  const read = useCallback(() => {
    api.getConnect()
      .then(({ rows }) => setRows(rows))
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

  const count = (s: Project['state']) => rows.filter((r) => r.state === s).length

  return (
    <section aria-label="모든 프로젝트" className="flex h-full min-h-0 flex-col">
      <div className="flex h-11 shrink-0 items-center gap-3 border-b border-border bg-card px-5 font-mono text-[10.5px] text-faint">
        <span>연결 완료 {count('연결 완료')}</span>
        <span>일부 {count('일부')}</span>
        <span>미연결 {count('미연결')}</span>
      </div>
      {fault && (
        <div role="alert" className="whitespace-pre-wrap border-b border-destructive/30 bg-destructive/10 px-5 py-2 text-[12.5px] text-destructive">
          {fault}
        </div>
      )}
      <ul className="min-h-0 flex-1 divide-y divide-border overflow-y-auto">
        {rows.map((r) => (
          <li key={r.id} className={cn('px-5 py-3 text-[12.5px]', r.id === current && 'bg-secondary/40')}>
            <div className="flex items-center gap-2">
              <span className={cn('size-2 shrink-0 rounded-full', DOT[r.state])} />
              <span className="min-w-0 truncate font-mono text-[12px]">{r.id}</span>
              <span className="shrink-0 text-muted-foreground">{r.state}</span>
              {r.probing && <span className="shrink-0 animate-pulse text-faint">시험 중…</span>}
              <span className="ml-auto flex shrink-0 gap-1.5">
                {r.state !== '미연결' && (
                  <Btn disabled={!!working || r.probing}
                    onClick={() => void act(`probe:${r.id}`, () => api.reprobe(r.id).then(() => {}))}
                    title="Claude 와 Codex 의 실제 세션을 한 턴씩 돌린다. 모델 두 턴의 비용이 든다">
                    다시 시험
                  </Btn>
                )}
                {r.state !== '연결 완료' && (
                  <Btn tone="primary" disabled={!!working || r.probing} onClick={() => void begin(r.id)}>
                    {working === r.id ? '연결하는 중…' : '연결'}
                  </Btn>
                )}
              </span>
            </div>
            {r.missing.length > 0 && (
              <ul className="mt-1.5 space-y-0.5 pl-4 text-[12.5px] text-muted-foreground">
                {r.missing.map((m) => <li key={m} className="whitespace-pre-wrap">· {m}</li>)}
              </ul>
            )}
            {r.notes.length > 0 && <div className="mt-1 pl-4 text-[12.5px] text-faint">{r.notes.join(' · ')}</div>}
            {r.survey?.state && (
              <div className="mt-1 pl-4 font-mono text-[10.5px] text-muted-foreground">
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
        <Modal wide title={`${asking.name} 연결`} onClose={() => setAsking(null)} foot={(
          <>
            <Btn onClick={() => setAsking(null)}>닫기</Btn>
            {asking.plan.survey && (
              <Btn disabled={!!asking.plan.hub.refused} onClick={() => confirm(false)}>조사 없이 연결</Btn>
            )}
            <Btn tone="primary" disabled={!!asking.plan.hub.refused} onClick={() => confirm(!!asking.plan.survey)}>
              {asking.plan.survey ? '확인 — 조사까지' : '확인'}
            </Btn>
          </>
        )}>
          <div>
            {asking.plan.hub.refused && (
              <p className="mb-2 whitespace-pre-wrap text-destructive">허브를 옮길 수 없다 — {asking.plan.hub.refused}</p>
            )}
            {asking.plan.hub.needed && !asking.plan.hub.refused && (
              <section className="mb-3">
                <div className="mb-1 font-heading text-[11px] font-semibold text-faint">허브 이전 — 이 기계에 한 번. 사용자 단위 설정이 이렇게 바뀐다</div>
                <ul className="space-y-0.5 font-mono text-[12px]">
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
                <div className="mb-1 font-heading text-[11px] font-semibold text-faint">원본 체크아웃에 쓰는 .wiki/adapter.toml</div>
                <ul className="font-mono text-[12px]">
                  {Object.entries(asking.plan.adapter).map(([k, v]) => <li key={k}>{k} = {v || <span className="text-wait">채워야 함</span>}</li>)}
                </ul>
              </section>
            )}
            {asking.plan.unwire.length > 0 && (
              <section className="mb-3">
                <div className="mb-1 font-heading text-[11px] font-semibold text-faint">옛 프로젝트 단위 hook</div>
                <ul className="font-mono text-[12px]">{asking.plan.unwire.map((u) => <li key={u}>{u}</li>)}</ul>
              </section>
            )}
            {asking.plan.survey && (
              <section className="mb-3">
                <div className="mb-1 font-heading text-[11px] font-semibold text-faint">전수조사 견적</div>
                <p>
                  파일 {asking.plan.survey.files}개 · 코드 {kilo(asking.plan.survey.code)}B · 문서 {kilo(asking.plan.survey.docs)}B
                  · 커밋 {asking.plan.survey.commits} · 머지된 PR {asking.plan.survey.prs} · 모듈 {asking.plan.survey.modules} · 턴 {asking.plan.survey.turns}
                </p>
                <p className="mt-0.5">
                  예상 {kilo(asking.plan.survey.tokens)} 토큰, {minutes(asking.plan.survey.seconds)}
                  {' '}(한도 {kilo(asking.plan.survey.limit.tokens)} 토큰, {minutes(asking.plan.survey.limit.seconds)})
                </p>
                {asking.plan.survey.over && <p className="mt-0.5 text-muted-foreground">한도에서 멈춘다 — 모듈 페이지 일부만</p>}
              </section>
            )}
          </div>
        </Modal>
      )}
    </section>
  )
}
