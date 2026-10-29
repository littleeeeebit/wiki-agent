import { useState } from 'react'
import { Btn } from '@/components/Modal'
import * as api from '@/lib/api'
import type { Spec } from '@/lib/api'

/** An existing test file the card changed outside its contract: a spec-update cannot reach the contract, so the
 *  person reads the diff and declares the changed definitions by name. The card then goes on by itself. */
type Amendment = { head: string; digest: string; files: { file: string; names: string[]; diff: string }[] }

export function Amendments({ spec, onDone }: { spec: Spec; onDone: () => void }) {
  const [seen, setSeen] = useState<Amendment | null>(null)
  const [working, setWorking] = useState('')
  const [fault, setFault] = useState('')
  const url = `/api/refactors/specs/${spec.id}/amendments`
  async function run(what: string, fn: () => Promise<unknown>) {
    setFault('')
    setWorking(what)
    try { await fn() } catch (err) { setFault(String(err instanceof Error ? err.message : err)) } finally { setWorking('') }
  }
  const close = <Btn tone="ghost" disabled={!!working} onClick={() => setSeen(null)}>닫기</Btn>
  const ready = seen?.files.some((f) => f.names.length > 0)
  return <div className="mt-2 space-y-1.5 text-[12.5px]">
    {!seen ? <Btn disabled={!!working} onClick={() => run('look', async () =>
      setSeen(await api.get(url, spec.repo).then((r) => api.json<Amendment>(r, '바뀐 테스트 파일'))))}>
      {working === 'look' ? '…' : '테스트 계약 변경 승인'}
    </Btn> : !seen.files.length ? <div className="flex items-center gap-1.5">
      <p className="text-faint">승인할 테스트 변경이 없다.</p>{close}
    </div> : <>
      <p className="text-muted-foreground">
        계약 밖에서 바뀐 기존 테스트 파일 {seen.files.length}개. 시작 커밋과의 차이다. 바뀐 정의만 바꿔도 되는지 읽고 승인하면
        그 정의들을 이름으로 명세의 테스트 계약에 선언하고, 카드는 마지막 완료 보고부터 이어서 진행한다. 같은 파일의 다른 곳을 고치면 여전히 거부한다.
      </p>
      {seen.files.map((f) => <div key={f.file}>
        <div className="font-mono text-[11.5px]">{f.file}{f.names.length > 0
          ? <span className="text-faint"> · 선언할 정의: {f.names.join(', ')}</span>
          : <span className="text-destructive"> · 이름 있는 정의 밖이 바뀌어 선언할 수 없다</span>}</div>
        <pre aria-label={`${f.file} 의 시작 커밋과의 차이`} className="mt-1 max-h-80 overflow-auto rounded-md border border-border bg-secondary p-2 font-mono text-[11.5px]">{f.diff}</pre>
      </div>)}
      <div className="flex gap-1.5">
        {ready && <Btn tone="primary" disabled={!!working} onClick={() => run('approve', async () => {
          await api.post(url, { head: seen.head, digest: seen.digest }, 'POST', spec.repo).then((r) => api.json<Spec>(r, '계약 변경 승인'))
          setSeen(null)
          onDone()
        })}>{working === 'approve' ? '…' : '승인하고 정의를 계약에 선언'}</Btn>}
        {close}
      </div>
    </>}
    {fault && <p role="alert" className="whitespace-pre-wrap text-destructive">{fault}</p>}
  </div>
}
