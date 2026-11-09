This turn writes the contract tests of a new backend, and nothing else.

The person approved the backend design. It is committed in this worktree:
`docs/design/backend.md` (the sheet they read) and `docs/design/api.yaml`
(the OpenAPI document). No backend code exists yet, and none may be written in
this turn.

## What to write

One test for each planned test below, with exactly its `test_name` as the
function name, in files under `tests/`. The application will be the FastAPI
object `app` in `app/main.py`; the tests reach it as `from app.main import
app`, and the build will put it exactly there. Each test checks its `then` through the
endpoints it depends on, as `api.yaml` describes them. Add `pytest` and what the
tests need (for example the HTTP test client) to `pyproject.toml`, and create
the environment the gate command uses, so the gate can collect the tests.

```json
{tests}
```

The gate command is `{gate}`.

## What the server checks when this turn ends

1. Only test files (`tests/`, `test_*.py`, `*_test.py`, `conftest.py`) and the
   test configuration (`pyproject.toml`, `uv.lock`, `pytest.ini`) changed since
   the design commit.
2. The tests collect: import the application inside a fixture or the test
   body, never at module level, so collection succeeds while the application
   does not exist yet.
3. Every planned test runs and fails, because nothing is implemented. A planned
   test that passes now checks nothing, and a skipped one checks nothing either.

When all three hold, the server commits the tests itself and pins them: from
then on these files cannot change. Make them right now.

## In this turn

- Write no `done-report` and no `test-cleanup` block. Committing is optional;
  the server commits what you leave.
- Do not push, open a pull request, or ask the person anything: nobody answers
  until the next review. Decide within the approved design.
- Running the gate command or the tests in this repository is fine.
