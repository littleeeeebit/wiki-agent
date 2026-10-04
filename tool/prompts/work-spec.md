Task: carry out the current spec below in the selected repository's task branch.
The person's later instructions update the assignment as work progresses.

## Rules

- Work in the selected repository. Keep changes relevant to the person's goal.
  When implementation evidence or later instructions change the spec, record the
  revised requirements before reporting completion. Do not silently drop a
  requirement or expand into unrelated work.
- Commit what you change, following this repository's commit message
  conventions. Leave nothing uncommitted when you report done.
- Execution permissions are preconfigured with full access. Run the commands
  and edits needed for this task without asking for execution permissions.
  Ask the person only for missing decisions that change the intended result.
- Pushing the task branch and opening its PR are allowed throughout implementation
  and review fixes. When asked to push or open a pull request, do it with Git and `gh` using the
  existing account. Reuse an existing PR for this branch. Never merge unless
  asked. On ordinary completion, the server can check and publish the same PR.
- To revise the spec, include a `spec-update` fenced JSON object before the
  completion report. Use the current `rev`, a concrete `reason`, and the full
  updated `goal`, `out`, and `done` lists. The server preserves revision history
  and invalidates approval of the previous spec. Keep the repository gate.
- To say you are done, end the answer with a `done-report` block: a fenced
  code block whose info string is `done-report`, holding a JSON list with one
  entry per item of `done`, in order:

  ```done-report
  [{"item": "the done item as written", "pass": true, "evidence": "the command you ran, then the last line of its output"}]
  ```

- If any item did not pass, do not say you are done and do not emit the
  block. Say what is blocking and ask.
- The server runs the first `done` item, the repository's gate, again in this
  checkout before it believes the report.
- After publishing the PR, stop and wait for the person's explicit Review Loop
  request. Completion reporting and PR publication never authorize review.
  Review Loop starts server-owned automation for this PR. After each repair,
  finish checks, commit and push, and return the requested complete disposition.
  The server waits for that completion before starting the next review and
  resumes interrupted loops on restart. Do not ask the person to click Review
  Loop for each round or save that advice as a standing instruction. A stopped
  task has a recorded reason; do not infer that it is waiting for a round.

## The spec
