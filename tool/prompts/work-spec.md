Task: carry out the spec below in this worktree. The spec was settled with the
person in a separate conversation; it is the whole assignment.

## Rules

- Work only inside this worktree. Do not do anything listed under `out`.
- Commit what you change, following this repository's commit message
  conventions. Leave nothing uncommitted when you report done.
- Do not push and do not open a pull request. The server does both after it
  checks your work.
- Every write and every command outside reading is approved by a person. Ask
  for what you need; do not route around a refusal.
- To say you are done, end the answer with a `done-report` block: a fenced
  code block whose info string is `done-report`, holding a JSON list with one
  entry per item of `done`, in order:

  ```done-report
  [{"item": "the done item as written", "pass": true, "evidence": "the command you ran, then the last line of its output"}]
  ```

- If any item did not pass, do not say you are done and do not emit the
  block. Say what is blocking and ask.
- The server runs the first `done` item, the repository's gate, again in this
  worktree before it believes the report.

## The spec
