import Markdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Candidates } from '@/components/Candidates'
import { fileLink } from '@/lib/links'

// Inline code that looks like a citation: `tool/main/app.py:118`, `docs/x.md`, a
// git commit identifier.
const FILE = /^([\w./-]+\.(?:py|md|ts|tsx|js|json|toml|ya?ml|cmd|txt|css|html|jsonl))(?::(\d+)(?:[-–]\d+)?)?$/
const SHA = /^[0-9a-f]{7,40}$/
// The same citation written bare. The prompt asks for inline code, but haiku
// also writes `craft/x.md:16` without the backticks — a line number is what
// tells it from a file merely named. `query.CITE` counts it for the draft.
const BARE = /((?<![\w./-])[\w./-]+\.(?:py|md|ts|tsx|js|json|toml|ya?ml|cmd|txt|css|html|jsonl):\d+(?:[-–]\d+)?)/

type Node = { type: string; value?: string; children?: Node[] }

/** Bare citations in the parsed text become inline code. On the tree, not the
 *  string: a link's destination and every kind of code block are not text
 *  nodes, so they stay as written. A link's own text is left alone too. */
function citations(node: Node) {
  if (!node.children || node.type === 'link' || node.type === 'linkReference') return
  node.children = node.children.flatMap((child) => {
    if (child.type !== 'text') {
      citations(child)
      return [child]
    }
    // Split on the one capture group: odd parts are the citations.
    return (child.value ?? '').split(BARE)
      .map((value, i) => ({ type: i % 2 ? 'inlineCode' : 'text', value }))
      .filter((part) => part.value)
  })
}
const remarkCitations = () => citations

export type AnswerProps = {
  text: string
  korean: boolean
  remote: string
  onPeek: (path: string, line: number) => void
  onDecide?: (candidate: string, target: 'wiki' | 'claude_md' | 'drop') => Promise<string>
}

/** The answer body. Tables and code blocks arrive often, and gfm holds those.
 *
 *  Links open in a new tab. Almost every link in an answer is a commit
 *  permalink, and the conversation must not disappear while the person is
 *  off comparing against the original.
 *
 *  Inline code shaped like a path or a SHA becomes clickable — "all the way
 *  to the original". A path opens that line in the side drawer; a SHA goes to
 *  GitHub. */
export function Answer({ text, korean, remote, onPeek, onDecide }: AnswerProps) {
  return (
    <div className="prose-answer">
      <Markdown
        remarkPlugins={[remarkGfm, remarkCitations]}
        urlTransform={(href) => fileLink(href) ? href : defaultUrlTransform(href)}
        components={{
          a: ({ href, children }) => {
            const file = fileLink(href ?? '')
            return file ? <button type="button" className="cite" onClick={() => onPeek(file.path, file.line)}>
              {children}
            </button> : <a href={href} target="_blank" rel="noreferrer">{children}</a>
          },
          table: ({ children }) => (
            <div className="overflow-x-auto">
              <table>{children}</table>
            </div>
          ),
          code: ({ className, children }) => {
            const raw = String(children ?? '').replace(/\n$/, '')

            // A ```retro-candidates fence: the retro's candidates, which get
            // buttons. `korean` goes in because the translator protects a
            // fence byte for byte, so the overlay on the answer never reaches
            // inside this one — and these are the lines a person decides on.
            if (className === 'language-retro-candidates') {
              return onDecide ? (
                <Candidates raw={raw} korean={korean} onDecide={onDecide} />
              ) : (
                <code className={className}>{raw}</code>
              )
            }
            // Every other fence stands as it is.
            if (className) return <code className={className}>{children}</code>

            const linked = fileLink(raw)
            const file = raw.match(FILE)
            if (linked && !file) return <button type="button" className="cite"
              onClick={() => onPeek(linked.path, linked.line)}>{raw}</button>
            if (file) {
              const line = file[2] ? Number(file[2]) : 1
              return (
                <button
                  type="button"
                  onClick={() => onPeek(file[1], line)}
                  className="cite"
                  title="이 자리를 본다"
                >
                  {raw}
                </button>
              )
            }
            if (remote && SHA.test(raw)) {
              return (
                <a href={`${remote}/commit/${raw}`} target="_blank" rel="noreferrer" className="cite">
                  {raw}
                </a>
              )
            }
            return <code>{children}</code>
          },
        }}
      >
        {text}
      </Markdown>
    </div>
  )
}
