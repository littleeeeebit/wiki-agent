## Asking the person (Codex in wiki-agent)

`request_user_input` is turned off in this session. You ask the person with a
`choices` block, and the screen draws it as option buttons. A question in
prose has no buttons, so never ask in prose.

### When you must ask

Stop and ask before you act or write a spec when any of these holds. Do not
pick for the person and report it afterwards.

- Money: a paid call, a budget, a cap, a quota.
- Irreversible or outward-facing: deleting, migrating stored data, pushing,
  publishing, sending.
- The request reads two ways and the two readings produce different work.
- Two or more approaches each carry a real cost: quality against speed, scope,
  dependencies, data formats, defaults the person will live with.
- Scope: what is in, what stays out, what counts as done, when the
  conversation has not settled it.

Do not ask when the default is obvious or the conversation already settled it.
Decide, say what you decided in one line, and carry on. Finish whatever does
not depend on the answer first, then ask.

### How dense

Match the density of a careful human consultant, not a yes/no prompt.

- Gather every independent decision that is open now into one block, one
  chapter per decision: one to five questions. Ask dependent follow-ups after
  the answers come back.
- Each question gets two to four meaningful options. Never invent an option
  only to fill the list.
- The recommended option goes first, and its label ends with `(Recommended)`.
- `header`: at most 12 characters. `label`: one to five words.
- `note`: two or three sentences. Say what happens if this is picked, what it
  costs (time, money, scope, risk) and when to pick it. Use concrete numbers,
  paths and names from the repository.
- `preview`: when options differ in shape (layouts, schemas, commands, file
  trees), a short Markdown or fenced-text sketch. Never executable HTML.
- `multi`: `true` only when picking several options together makes sense.
- Write question prose in English; the screen mirrors it in Korean. Keep code
  and paths as they are.

### Format

Put the block at the very end of the answer, after the prose, as a fenced code
block whose info string is `choices` and whose content is JSON. Then end the
turn and wait. The person's answer arrives as the next message, one line per
chapter (`header: label`). Do not repeat the block's content in the prose.

```choices
{"questions": [{"header": "Storage", "question": "Where should the embeddings live?", "options": [{"label": "pgvector (Recommended)", "note": "One PostgreSQL database holds rows and vectors, so backups and joins stay simple. Needs the extension on the server; about 111 MiB at 1,536 dimensions for 19k chunks.", "preview": "chunks(id, doc_id, body, embedding vector(1536))"}, {"label": "Keep the SQLite index", "note": "No migration and nothing new to run. Approximate search and concurrent writes stay unavailable."}], "multi": false}]}
```
