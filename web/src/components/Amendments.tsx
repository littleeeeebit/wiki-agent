import { useState } from 'react'
import { Btn } from '@/components/Modal'
import { DiffLines } from '@/components/LiveChanges'
import * as api from '@/lib/api'
import type { Spec } from '@/lib/api'
import { useLoaded } from '@/lib/loaded'

/** An existing test file the card changed outside its contract: a spec-update cannot reach the contract, so the
 *  person reads the diff and declares the changed definitions by name. The card then goes on by itself. */
type Amendment = { head: string; digest: string; files: { file: string; names: string[]; blob?: string; diff: string }[] }

export function Amendments({ spec, onDone }: { spec: Spec; onDone: () => void }) {
  const url = `/api/refactors/specs/${spec.id}/amendments`
  const [seen, fault, setFault, reload] = useLoaded<Amendment>(url, spec.repo, '바뀐 테스트 파일', spec.fault)
  const [working, setWorking] = useState(false)
  const ready = seen?.files.some((f) => f.names.length > 0 || f.blob)
  return <div className="mt-2 space-y-1.5 text-[12.5px]">
    {seen && !seen.files.length && <p className="text-faint">승인할 테스트 변경이 없다.</p>}
    {seen?.files.map((f) => <div key={f.file}>
      <div className="font-mono text-[11.5px]">{f.file}{f.blob ? <span className="text-faint"> · 내용 그대로 승인</span>
        : f.names.length > 0 ? <span className="text-faint"> · 선언: {f.names.join(', ')}</span>
        : <span className="text-destructive"> · 이름 있는 정의 밖이 바뀌어 선언할 수 없다</span>}</div>
      <DiffLines lines={f.diff.split('\n')} label={`${f.file} 의 시작 커밋과의 차이`}
        className="mt-1 max-h-80 rounded-md border border-border bg-secondary p-2" />
    </div>)}
    {ready && <Btn tone="primary" disabled={working} title="이 변경을 명세의 테스트 계약에 선언하고 카드를 이어 간다" onClick={async () => {
      setFault('')
      setWorking(true)
      try {
        await api.post(url, { head: seen!.head, digest: seen!.digest }, 'POST', spec.repo).then((r) => api.json<Spec>(r, '계약 변경 승인'))
        onDone()
      } catch (err) { setFault(String(err instanceof Error ? err.message : err)); reload() } finally { setWorking(false) }
    }}>{working ? '…' : '테스트 계약 변경 승인'}</Btn>}
    {fault && <p role="alert" className="whitespace-pre-wrap text-destructive">{fault}</p>}
    {fault && !seen && <Btn onClick={() => { setFault(''); reload() }}>다시 읽기</Btn>}
  </div>
}
