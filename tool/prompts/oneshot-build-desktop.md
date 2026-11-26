This turn implements the Windows program's core the person approved, against
its contract tests.

The design is committed in this worktree: `docs/design/core.md` and
`docs/design/core.json`. The contract tests were committed at `{tests_head}`
and are pinned with the test runner's configuration; these files may not
change at all, not even by adding a skip, a mark or a statement after the
tests:

{contract}

New tests go in new files. Screens, packaging and the installer are not part
of this turn: build the core modules, and the platform modules' contracts
with the Windows APIs behind them.

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

The server runs each declared tool from the path it found for this stage.
A refusal comes back with the reason, at most three times in all.

## In this turn

- Add no fake, mock, stub or stand-in of an outside call, and no stand-in
  beyond the device platform a thin platform module wraps. The outside calls go to
  the real services with what the owner gave for them:

{needs}

  Your shell and the server's gate both have each secret's environment
  variable, so a test that needs one runs and must pass when you run it too —
  except a secret the list marks as the gate's alone: it is not in your shell,
  so its test is left to the gate, which runs it live.
  Never print a secret's value, and never write it into a file, a commit or
  your answer. The server refuses a turn whose answer, commits since the
  design commit or uncommitted files hold one, naming only the need.
  The screen check starts the PC app with each secret's variable set, as the
  gate does, and the app reads it there. A screen whose text holds a secret is
  refused, so a secret never reaches the screen.
- Commit everything and leave nothing uncommitted, then end with the
  `done-report`. No `test-cleanup` block is needed in this stage.
- Do not push or open a pull request. Decide within the approved design; ask
  the owner only a decision that is theirs, as the end of this message says.
- Running the gate command or the tests in this repository is fine. Do not
  start the program or open a window.
- Build and test with the gate command's own package selection, features and profile
  each time: every other flag set builds its own copy of the dependencies, and
  the next build starts from that copy instead of what was just built. Never
  clean the build cache (`cargo clean`, deleting `target`, `gradle clean`):
  a warm cache rebuilds only the modules a fix changed (the server bounds Cargo's incremental cache).
