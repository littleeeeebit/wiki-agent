This turn implements the core the person approved, which every kind they
picked shares, against its contract tests.

The design is committed in this worktree: `docs/design/core.md`,
`docs/design/core.json` and, when a web app is among the kinds,
`docs/design/api.yaml` for its server's endpoints. The contract tests were
committed at `{tests_head}` and are pinned with the test runner's
configuration; these files may not change at all, not even by adding a skip, a
mark or a statement after the tests:

{contract}

New tests go in new files. Build the core modules, the platform modules'
contracts with the platform behind them, and, for a web app, its server's
endpoints. No kind's screens, packaging or installer belong to this turn; each
kind's own build comes later.

{tools}
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
- Running the gate command or the tests in this repository is fine. Do not
  start an emulator, address a device or open a window.
