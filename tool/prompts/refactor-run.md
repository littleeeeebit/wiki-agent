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
untested and name it in your answer. Change no other file. Commit, then
end the answer with a fenced block tagged `refactor-tests` holding JSON:
`{"tests": [every repository-relative test file that pins this behaviour,
existing or new], "test_argv": [the command that runs exactly those tests
from the repository root, as an argument list]}`. Do not emit `done-report`
in that answer. The server checks that only test files changed and that the
tests pass on the unchanged code, freezes them, and then asks you for the
refactoring. Those frozen tests run again in the final command.

In `rework` with a `measure`, the server runs its command on the task's start
commit and on your final commit, back to back in this worktree, the same
number of times, after the gate passes. Keep the command working on both
commits; you do not measure for the pull request yourself.
