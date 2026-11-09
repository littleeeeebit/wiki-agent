import { useState } from 'react'
import { Btn, Modal } from '@/components/Modal'
import * as api from '@/lib/api'
import type { HubPlan, RewirePlan } from '@/lib/api'

/** [모두 다시 연결]: `setup_agents.py --global --project <each>` from the screen
 *  (`tool/main/wiring.py`). Every line it writes is shown before it writes. `act`
 *  is the project list's: it shows a failure and lists the rows again after. */
export function Rewire({ working, disabled, act }: {
  working: string; disabled: boolean; act: (key: string, fn: () => Promise<void>) => Promise<void>
}) {
  const [plan, setPlan] = useState<RewirePlan | null>(null)
  // The hub's own refusal is shown by `HubMove`; this is everything else that stops the write.
  const blocked = plan && [plan.refused, ...plan.repos.map((r) => r.refused)].filter(Boolean).join('\n')
  const confirm = () => {
    if (!plan) return
    const { digest } = plan
    setPlan(null)
    void act('rewire', async () => {
      // A write that failed after others landed: name both, so the person sees what is half done.
      const done = await api.rewire(digest)
      if (!done.ok) throw new Error(`일부만 다시 연결했다.\n쓴 것:\n${done.written.join('\n') || '(없음)'}\n실패:\n${done.failed.join('\n')}`)
    })
  }
  return (
    <>
      <Btn className="ml-auto font-sans" disabled={disabled} onClick={() => void act('rewire', async () => setPlan(await api.rewirePlan()))}
        title="setup_agents.py --global --project <연결된 저장소마다> 와 같다. 바뀔 줄을 먼저 보여 준다">
        {working === 'rewire' ? '다시 연결하는 중…' : '모두 다시 연결'}
      </Btn>
      {plan && (
        <Modal wide title="모두 다시 연결" onClose={() => setPlan(null)} foot={(
          <>
            <Btn onClick={() => setPlan(null)}>닫기</Btn>
            <Btn tone="primary" disabled={!!blocked || !!plan.hub.refused} onClick={confirm}>확인</Btn>
          </>
        )}>
          <div className="font-sans">
            {blocked && <p className="mb-2 whitespace-pre-wrap text-destructive">{blocked}</p>}
            <HubMove hub={plan.hub} />
            <section className="mb-3">
              <div className="mb-1 font-heading text-[11px] font-semibold text-faint">저장소마다 옛 훅 정리·보호 규칙 적용</div>
              <ul className="space-y-1 font-mono text-[12px]">
                {plan.repos.map((r) => (
                  <li key={r.name}>
                    {r.name} <span className="text-faint">{r.unwire.length ? '' : '— 바꿀 것 없음'}</span>
                    {r.unwire.map((u) => <div key={u} className="pl-3 text-muted-foreground">{u}</div>)}
                {r.refused && <div className="whitespace-pre-wrap pl-3 text-destructive">{r.refused}</div>}
                  </li>
                ))}
              </ul>
            </section>
            {!plan.hub.needed && plan.repos.every((r) => !r.unwire.length) && (
              <p className="text-muted-foreground">쓸 것이 없다 — 이 기계의 배선은 이미 이 허브를 부른다.</p>
            )}
          </div>
        </Modal>
      )}
    </>
  )
}

/** The hub move both confirmations show: every user-level line it writes, or why it cannot. */
export function HubMove({ hub }: { hub: HubPlan }) {
  if (hub.refused) return <p className="mb-2 whitespace-pre-wrap text-destructive">허브를 옮길 수 없다 — {hub.refused}</p>
  if (!hub.needed) return null
  return (
    <section className="mb-3">
      <div className="mb-1 font-heading text-[11px] font-semibold text-faint">허브 이전 — 이 기계에 한 번. 사용자 단위 설정이 이렇게 바뀐다</div>
      <ul className="space-y-0.5 font-mono text-[12px]">
        {hub.lines.map((l, i) => <li key={i}><span className="text-faint">{l.file}</span> {l.change}</li>)}
        {hub.links.map((l) => (
          <li key={l.link}>
            <span className="text-faint">{l.link}</span>{' '}
            {l.to ? `→ ${l.to}` : `그대로 둔다 — 이 허브에 같은 이름의 스킬이 없다 (${l.from})`}
          </li>
        ))}
        {hub.trust && <li>Codex: 이 허브의 hook.py 를 부르는 위키 훅을 신뢰로 기록한다 (--trust-codex)</li>}
      </ul>
    </section>
  )
}
