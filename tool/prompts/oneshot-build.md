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

- Add no fake, mock, stub or stand-in of anything. The outside calls go to
  the real services with what the owner gave for them:

{needs}

  Your shell and the server's gate both have each secret's environment
  variable, so a test that needs one runs and must pass when you run it too —
  except a secret the list marks as the gate's alone: it is not in your shell,
  so its test is left to the gate, which runs it live.
  Never print a secret's value, and never write it into a file, a commit or
  your answer. The server refuses a turn whose answer, commits since the
  design commit or uncommitted files hold one, naming only the need.
- Commit everything and leave nothing uncommitted, then end with the
  `done-report`. No `test-cleanup` block is needed in this stage.
- Do not push or open a pull request. Decide within the approved design; ask
  the owner only a decision that is theirs, as the end of this message says.
- Running the gate command or the tests in this repository is fine.
- Build and test with the gate command's own package selection, features and profile
  each time: every other flag set builds its own copy of the dependencies, and
  the next build starts from that copy instead of what was just built. Never
  clean the build cache (`cargo clean`, deleting `target`, `gradle clean`):
  a warm cache rebuilds only the modules a fix changed (the server bounds Cargo's incremental cache).
