import { useState } from 'react'
import { Btn } from '@/components/Modal'
import { Decide, GO, Split, Title } from '@/components/OneshotSpec'
import { api, type PcPlan, type PcResult, type SpecView } from '@/lib/oneshot'

/** What the owner's desktop round will start and read, for the owner's one-time approval: the checks, the app and
 *  each fixture, and each capture with what it reads. A screen-region capture runs only when the owner allows it;
 *  its check otherwise stays pending. The server checks the approval against the run again. */
export function PcApprove({ repo, view, plan, busy, act }: {
  repo: string; view: SpecView; plan: PcPlan; busy: boolean; act: (fn: () => Promise<SpecView>) => Promise<boolean>
}) {
  const [surface, setSurface] = useState(false)
  const given = view.pc_approved
  const last = view.oneshot.pc?.last?.results
  const owed = new Map((view.oneshot.owed ?? []).map((x) => [x.id, x]))
  const surfaced = plan.captures.some((c) => c.kind === 'surface')
  return (
    <Split label="주인 화면 회차" sideLabel="승인" side={<>
      {/* An approval that still stands here is a round that never started: approved again, it starts. */}
      <Title><span>{given ? '회차가 시작되지 않았다' : '이 회차를 승인한다'}</span><span className="font-mono">{plan.checks.length}</span></Title>
      {surfaced && (
        <label className="flex items-baseline gap-2.5">
          <input type="checkbox" checked={surface} onChange={(e) => setSurface(e.target.checked)} />
          <span>고정 창이 덮은 화면 영역을 읽어도 된다 (회차와 앱 모두)</span>
        </label>
      )}
      <Decide>
        <Btn tone="wait" className={GO} disabled={busy || !plan.checks.length}
          onClick={() => void act(() => api.approvePc(repo, plan, surface))}>{given ? '다시 승인' : '승인'}</Btn>
      </Decide>
    </>}>
      <div className="flex max-w-[44rem] flex-col gap-4 px-10 py-6 max-[1500px]:px-7 max-[560px]:px-4">
        <p>소유 데스크톱에서는 볼 수 없는 캡처 검사를 내 화면에서 한 번 돌린다. 아래에 적힌 것만 띄우고 읽으며, 승인은 이 빌드의 한
          회차에만 쓴다. 서버가 다시 켜지거나 빌드가 바뀌면 다시 승인한다.</p>
        <p className="text-muted-foreground">승인하면 바로 내 화면 왼쪽 위에 고정 창과 앱이 뜬다. 회차는 창을 앞으로 가져오거나 키를
          보내지 않는다. 끝날 때까지 그 영역을 덮지 않는다.</p>
        {last && <PcResults title="지난 회차" results={last} />}
        <section aria-label="돌릴 검사" className="flex flex-col gap-2">
          <Title><span>돌릴 검사</span><span className="font-mono">{plan.checks.length}</span></Title>
          <ul className="flex flex-col gap-1.5">
            {plan.checks.map((id) => (
              <li key={id}><span className="font-mono text-[12px] text-faint">{id} {owed.get(id)?.requirement}</span> {owed.get(id)?.check}</li>
            ))}
          </ul>
        </section>
        <section aria-label="띄울 것" className="flex flex-col gap-2">
          <Title><span>띄울 것</span></Title>
          <ul className="flex flex-col gap-1.5 font-mono text-[12.5px]">{plan.starts.map((s) => <li key={s}>{s}</li>)}</ul>
        </section>
        {plan.captures.length > 0 && (
          <section aria-label="읽을 것" className="flex flex-col gap-2">
            <Title><span>찍고 읽을 것</span></Title>
            <ul className="flex flex-col gap-1.5">
              {plan.captures.map((c) => (
                <li key={`${c.check}-${c.by}`}><span className="font-mono text-[12px] text-faint">{c.check}</span>{' '}
                  {c.by === 'app' ? '앱이 읽는 ' : '회차가 찍는 '}{c.reads}
                  {c.kind === 'surface' && <span className="text-muted-foreground"> — 그 영역에 뜬 알림도 함께 찍힌다</span>}</li>
              ))}
            </ul>
          </section>
        )}
        {!!view.pc_unrun?.length && (
          <p className="text-muted-foreground">{view.pc_unrun.join(', ')} 는 이 회차에서 돌리지 않아 남는다. 남은 검사가 있으면 R3 는 열리지
            않는다.</p>
        )}
        {view.oneshot.pc?.detail && <p role="alert" className="rounded-md bg-wait/10 px-3 py-2">{view.oneshot.pc.detail}</p>}
      </div>
    </Split>
  )
}

/** Each check's result in a round on the owner's desktop: the last one at `pc`, a passed one beside R3's hold. */
export function PcResults({ title, results }: { title: string; results: Record<string, PcResult> }) {
  return (
    <section aria-label={title} className="flex flex-col gap-2">
      <Title><span>{title}</span></Title>
      <ul className="flex flex-col gap-1.5">
        {Object.entries(results).map(([id, r]) => (
          <li key={id}><span className="font-mono text-[12px] text-faint">{id}</span>{' '}
            {{ passed: '통과', failed: '실패', pending: '남음', judge: '판정 전' }[r.status]}
            {r.why && <span className="text-muted-foreground"> — {r.why}</span>}</li>
        ))}
      </ul>
    </section>
  )
}
