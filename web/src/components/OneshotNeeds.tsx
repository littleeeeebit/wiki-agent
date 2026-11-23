import { useState } from 'react'
import { Btn } from '@/components/Modal'
import type { Need } from '@/lib/oneshot'

const KIND: Record<Need['kind'], string> = { secret: '비밀 값', account: '계정', tool: '프로그램', file: '파일' }
const HINT: Partial<Record<Need['kind'], string>> = { secret: '값', tool: '프로그램 이름 또는 경로', file: '파일의 절대 경로' }

/** The needs whose value a build turn left in its answer, commits or files (`oneshot_needs.marked`), and apart from
 *  them those a scan that could not finish carried (`unsure`), shown at any stage, running or stopped: a refusal
 *  names each by its `[id]`, its variable or its row, and here it is found. */
export function Leaked({ needs }: { needs: Need[] }) {
  const rows = needs.map((n, i) => ({ n, row: i + 1 }))
  const lists = [['값이 결과에 들어 있는 항목:', rows.filter(({ n }) => n.leaked)],
    ['검사를 끝내지 못해 확인을 기다리는 항목:', rows.filter(({ n }) => n.unsure)]] as const
  if (!lists.some(([, hit]) => hit.length)) return null
  return (
    <section role="alert" aria-label="결과에 들어 있을 수 있는 비밀 값" className="flex flex-col gap-1 rounded-md bg-destructive/10 px-3 py-2">
      {lists.map(([title, hit]) => hit.length > 0 && (
        <div key={title} className="flex flex-col gap-0.5">
          <p>{title}</p>
          <ul className="flex flex-col gap-0.5">
            {hit.map(({ n, row }) => (
              <li key={n.id}>시트의 {row}번째 항목 · <span className="font-mono">{n.id}</span> {n.what}{n.env && <> <code>{n.env}</code></>}</li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  )
}

/** What the owner gives the outside calls (`tool/main/oneshot_needs.py`). At R1 each need takes its value and says
 *  whether it is given; R1 approval waits for all of them. A secret goes to Windows Credential Manager and never
 *  comes back to the screen. At any stage, a secret Credential Manager still holds that no need of today's design
 *  names can be removed. The caller keys this on the sheet's `source`, so a draft never outlives its sheet. */
export function Needs({ needs, kept, editable, busy, give, takeBack, forget }: {
  needs: Need[]; kept: string[]; editable: boolean; busy: boolean
  give: (id: string, body: { value?: string; confirmed?: boolean }) => Promise<boolean>
  takeBack: (id: string) => Promise<boolean>; forget: (env: string) => Promise<boolean>
}) {
  const named = new Set(needs.map((n) => n.env).filter(Boolean))
  const loose = kept.filter((env) => !named.has(env))
  if (!(editable && needs.length) && !loose.length) return null
  const missing = needs.filter((n) => !n.given).length
  return (
    <section aria-label="주인이 줄 것" className="flex flex-col gap-3 rounded-md border border-border px-4 py-3">
      {editable && needs.length > 0 && <>
        <h2 className="text-[15px] font-semibold">주인이 줄 것</h2>
        <p className="text-muted-foreground">
          바깥 호출이 실제로 쓰는 것이다. 모두 주어져야 R1 을 승인할 수 있다.{' '}
          {missing ? `${missing}개가 아직 없다.` : '모두 주어졌다.'} 비밀 값은 이 PC 의 Windows 자격 증명 관리자에만 들어간다.
        </p>
        <ul className="flex flex-col gap-2">
          {needs.map((n) => <Row key={`${n.id}-${n.kind}-${n.env ?? ''}-${n.what}`} n={n} busy={busy} give={give} takeBack={takeBack} />)}
        </ul>
      </>}
      {loose.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <p className="text-muted-foreground">지금 설계가 쓰지 않는데 자격 증명 관리자에 남아 있는 이 저장소의 비밀 값:</p>
          {loose.map((env) => (
            <div key={env} className="flex items-center gap-2">
              <code>{env}</code>
              <Btn tone="danger" disabled={busy} aria-label={`${env} 지우기`} onClick={() => void forget(env)}>지우기</Btn>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

function Row({ n, busy, give, takeBack }: {
  n: Need; busy: boolean
  give: (id: string, body: { value?: string; confirmed?: boolean }) => Promise<boolean>; takeBack: (id: string) => Promise<boolean>
}) {
  const [value, setValue] = useState('')
  const label = `${n.id} ${n.what}`
  async function send() {
    if (await give(n.id, n.kind === 'account' ? { confirmed: true } : { value })) setValue('')
  }
  return (
    <li className="flex flex-col gap-1.5 rounded-md bg-secondary/40 px-3 py-2">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-mono">{n.id}</span>
        <span className="font-medium">{n.what}</span>
        <span className="text-muted-foreground">
          {KIND[n.kind]}{n.env && <> <code>{n.env}</code></>} · {n.calls.join(', ')}
        </span>
        <span role="status" className={n.given ? 'text-st-ready' : 'text-wait'}>{n.given ? '주어짐' : '없음'}</span>
        {n.leaked && <span role="alert" className="text-destructive">값이 결과에 들어 있다</span>}
        {n.unsure && <span role="status" className="text-wait">검사를 끝내지 못해 확인을 기다린다</span>}
        {n.launch && <span role="status" className="text-wait">에이전트를 띄울 때 서버가 쓰는 이름이라 에이전트에게는 주지 않는다</span>}
        {n.started && <span role="status" className="text-wait">화면 검사가 앱을 띄울 때 쓰는 이름이라 화면 검사의 앱에는 주지 않는다</span>}
      </div>
      <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); void send() }}>
        {n.kind !== 'account' && (
          <input aria-label={`${label} ${HINT[n.kind]}`} value={value} onChange={(e) => setValue(e.target.value)}
            type={n.kind === 'secret' ? 'password' : 'text'} autoComplete="off" spellCheck={false}
            placeholder={n.given && n.kind === 'secret' ? '바꾸려면 새 값을 넣는다' : HINT[n.kind]}
            className="min-w-0 flex-1 rounded-md border border-input bg-background px-2 py-1 text-foreground max-[560px]:text-base" />
        )}
        {n.kind === 'account'
          ? !n.given && <Btn type="submit" tone="primary" disabled={busy} aria-label={`${label} 계정이 있다`}>계정이 있다</Btn>
          : <Btn type="submit" tone="primary" disabled={busy || !value.trim()} aria-label={`${label} ${n.kind === 'tool' ? '찾기' : '넣기'}`}>
              {n.kind === 'tool' ? '찾기' : '넣기'}</Btn>}
        {n.given && <Btn tone="ghost" disabled={busy} aria-label={`${label} 지우기`} onClick={() => void takeBack(n.id)}>지우기</Btn>}
      </form>
    </li>
  )
}
