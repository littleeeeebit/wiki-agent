## Refactoring spec

This task is one spec of a read-only refactoring survey. Its `refactor` object
below names the mode, the pipeline, the files, the evidence, the behaviour that
stays, the test contract and, for performance, the measurement. Do only this
spec: no change another mode owns, no file outside its pipeline except those
its `files` and `creates` list (a `preserve` spec is the whole repository,
pipeline `all`, scoped by those lists; a `rework` root cause may span
pipelines; its `impact` names them), no unrelated cleanup.
When the spec turns out wrong, stop and ask instead of widening it.

- Test contract. Add the tests `tests.add` names and any test you need to
  guard behaviour. Existing test files may change only by adding new tests
  to them, except the tests `tests.remove` and `tests.contract` list; the
  body of an existing test stays as it is, so a check that would extend one
  is a new test beside it, even where `tests.add` says to extend. The test cleanup
  pass removes nothing else; report other candidates in its `reason`. Before
  the gate the server compares every existing test file with the task's start
  and stops the task on any other edit or deletion.
- Never change an expectation to make the current code pass, delete a failing
  test, or weaken a test, lint, gate or debt limit.
- Completion. When you report done, the server runs the repository's final
  command once and compares it with the survey's baseline on the base commit.
  A failure the baseline did not have, or one it cannot name, stops the task
  before any pull request.

In `preserve`, your first answer is characterization only. Read the existing
tests of the behaviour this spec touches. Add characterization or regression
tests only for core behaviour they leave uncovered, and pin today's intended
behaviour. A known bug is neither fixed nor pinned as a contract: leave it
untested and name it in your answer. Change no other file, except a test-only
dependency: `devDependencies` in an existing `package.json`, and the npm
lock beside it when every package it moves is a `dev` one. Commit, then
end the answer with a fenced block tagged `refactor-tests` holding JSON:
`{"tests": [every repository-relative test file that pins this behaviour,
existing or new], "test_argv": [the command that runs exactly those tests
from the repository root, as an argument list]}`. Do not emit `done-report`
in that answer. The server checks that only test files changed, that the
tests pass on the unchanged code and that the repository's gate fails nothing
it did not fail before, freezes them, and then asks you for the refactoring.
A frozen test file may not change afterwards, not even by one line, so new
tests follow the repository's own rules for test files from the start. Those
frozen tests run again in the final command. If a frozen test still has to
change, change only what is needed, commit, and name the file and why in your
answer: the person reads the diff and freezes it again once the changed tests
pass on the code before the refactoring. Never route around a frozen test by
changing another existing test.

In `rework` with a `measure`, the server runs its command on the task's start
commit and on your final commit, back to back in this worktree, the same
number of times, after the gate passes. Keep the command working on both
commits; you do not measure for the pull request yourself.
