import { useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import * as api from '@/lib/api'
import type { Spec } from '@/lib/api'

/** The human requirements document the server renders from the saved record, read when opened.
 *  Only a record with requirements has one; links and images stay text. The caller keys it by the
 *  record's revision and requirements version: any revision mounts a new one, so a document or a
 *  reply still in flight from the old revision lands on the retired instance and is never shown. */
export function SpecViews({ spec }: { spec: Spec }) {
  const [text, setText] = useState('')
  const [fault, setFault] = useState('')
  if (!spec.requirements?.length) return null
  const open = (event: React.SyntheticEvent<HTMLDetailsElement>) => {
    if (event.currentTarget.open && !text) {
      api.specViews(spec.id).then((v) => setText(v.human), (err) => setFault(String(err instanceof Error ? err.message : err)))
    }
  }
  return <details className="mt-2 text-[12.5px]" onToggle={open}>
    <summary className="cursor-pointer text-primary">요구사항 문서 · 버전 {spec.spec_version}</summary>
    {fault && <p role="alert" className="mt-1 text-destructive">{fault}</p>}
    {text && <div aria-label="요구사항 문서" className="question-preview mt-2 rounded-md bg-secondary p-3 text-[13px] leading-relaxed">
      <Markdown remarkPlugins={[remarkGfm]} components={{ a: ({ children }) => <span>{children}</span>,
        img: ({ alt }) => <span>{alt}</span> }}>{text}</Markdown>
    </div>}
  </details>
}
