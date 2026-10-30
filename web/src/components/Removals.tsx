import { useState } from 'react'
import { Btn } from '@/components/Modal'
import * as api from '@/lib/api'
import type { Spec } from '@/lib/api'
import { useLoaded } from '@/lib/loaded'

/** A test file the card deleted that its contract names only by test: the worker cannot declare the file,
 *  so the person reads what it held and declares it removed whole. The card then goes on by itself. */
type Removal = { head: string; digest: string; files: { file: string; named: string[]; text: string }[] }

export function Removals({ spec, onDone }: { spec: Spec; onDone: () => void }) {
  const url = `/api/refactors/specs/${spec.id}/removals`
  const [seen, fault, setFault] = useLoaded<Removal>(url, spec.repo, '삭제한 테스트 파일', spec.fault)
  const [working, setWorking] = useState(false)
  return <div className="mt-2 space-y-1.5 text-[12.5px]">
    {seen && !seen.files.length && <p className="text-faint">승인할 테스트 파일 삭제가 없다.</p>}
    {seen?.files.map((f) => <div key={f.file}>
      <div className="font-mono text-[11.5px]">{f.file} · 지움{f.named.length > 0 && <span className="text-faint"> · 명세가 이름으로 선언한 테스트: {f.named.join(', ')}</span>}</div>
      <pre aria-label={`${f.file} 의 시작 커밋 내용`} className="mt-1 max-h-80 overflow-auto rounded-md border border-border bg-secondary p-2 font-mono text-[11.5px]">{f.text}</pre>
    </div>)}
    {!!seen?.files.length && <Btn tone="primary" disabled={working} title="파일 단위 삭제로 명세의 테스트 계약에 선언하고 카드를 이어 간다" onClick={async () => {
      setFault('')
      setWorking(true)
      try {
        await api.post(url, { head: seen.head, digest: seen.digest }, 'POST', spec.repo).then((r) => api.json<Spec>(r, '삭제 승인'))
        onDone()
      } catch (err) { setFault(String(err instanceof Error ? err.message : err)) } finally { setWorking(false) }
    }}>{working ? '…' : '테스트 파일 삭제 승인'}</Btn>}
    {fault && <p role="alert" className="whitespace-pre-wrap text-destructive">{fault}</p>}
  </div>
}
