import { useState } from 'react'
import { Btn } from '@/components/Modal'
import * as api from '@/lib/api'
import type { Spec } from '@/lib/api'

/** A test file the card deleted that its contract names only by test: the worker cannot declare the file,
 *  so the person reads what it held and declares it removed whole. The card then goes on by itself. */
type Removal = { head: string; digest: string; files: { file: string; named: string[]; text: string }[] }

export function Removals({ spec, onDone }: { spec: Spec; onDone: () => void }) {
  const [seen, setSeen] = useState<Removal | null>(null)
  const [working, setWorking] = useState('')
  const [fault, setFault] = useState('')
  const url = `/api/refactors/specs/${spec.id}/removals`
  async function run(what: string, fn: () => Promise<unknown>) {
    setFault('')
    setWorking(what)
    try { await fn() } catch (err) { setFault(String(err instanceof Error ? err.message : err)) } finally { setWorking('') }
  }
  const close = <Btn tone="ghost" disabled={!!working} onClick={() => setSeen(null)}>닫기</Btn>
  return <div className="mt-2 space-y-1.5 text-[12.5px]">
    {!seen ? <Btn disabled={!!working} onClick={() => run('look', async () =>
      setSeen(await api.get(url, spec.repo).then((r) => api.json<Removal>(r, '삭제한 테스트 파일'))))}>
      {working === 'look' ? '…' : '테스트 파일 삭제 승인'}
    </Btn> : !seen.files.length ? <div className="flex items-center gap-1.5">
      <p className="text-faint">승인할 테스트 파일 삭제가 없다.</p>{close}
    </div> : <>
      <p className="text-muted-foreground">
        지운 테스트 파일 {seen.files.length}개. 시작 커밋에 있던 내용이다. 지워도 되는 테스트만 들어 있는지 읽고 승인하면
        명세의 테스트 계약에 파일 단위 삭제로 선언하고, 카드는 마지막 완료 보고부터 이어서 진행한다.
      </p>
      {seen.files.map((f) => <div key={f.file}>
        <div className="font-mono text-[11.5px]">{f.file}{f.named.length > 0 && <span className="text-faint"> · 명세가 이름으로 선언한 테스트: {f.named.join(', ')}</span>}</div>
        <pre aria-label={`${f.file} 의 시작 커밋 내용`} className="mt-1 max-h-80 overflow-auto rounded-md border border-border bg-secondary p-2 font-mono text-[11.5px]">{f.text}</pre>
      </div>)}
      <div className="flex gap-1.5">
        <Btn tone="primary" disabled={!!working} onClick={() => run('approve', async () => {
          await api.post(url, { head: seen.head, digest: seen.digest }, 'POST', spec.repo).then((r) => api.json<Spec>(r, '삭제 승인'))
          setSeen(null)
          onDone()
        })}>{working === 'approve' ? '…' : '승인하고 파일 단위로 선언'}</Btn>
        {close}
      </div>
    </>}
    {fault && <p role="alert" className="whitespace-pre-wrap text-destructive">{fault}</p>}
  </div>
}
