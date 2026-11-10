This turn implements the backend the person approved, against its contract tests.

The design is committed in this worktree: `docs/design/backend.md` and
`docs/design/api.yaml`. The contract tests were committed at `{tests_head}`
and are pinned with the test tool's configuration; these files may not change
at all, not even by adding a skip, a mark or a statement after the tests:

{contract}

New tests go in new files.

## When this turn ends

The server checks, in order:

1. The `done-report` covers every item of the spec's `done`, each passing.
2. Every pinned file is unchanged since `{tests_head}`.
3. The gate `{gate}` passes on a clean, committed HEAD.
4. In a run of the pinned test command, every case pinned at `{tests_head}`,
   by its class and name, runs and passes. Skipped or absent counts as
   failing, whatever the gate said, and a test of the same name elsewhere
   does not stand in for it.

A refusal comes back with the reason, at most three times in all.

## In this turn

- Commit everything and leave nothing uncommitted, then end with the
  `done-report`. No `test-cleanup` block is needed in this stage.
- Do not push, open a pull request, or ask the person anything: nobody answers
  until the next review. Decide within the approved design.
- Running the gate command or the tests in this repository is fine.
