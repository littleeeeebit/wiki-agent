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
2. The tests collect: write `from app.main import app` inside each test
   body, never at module level and never in a fixture, so collection succeeds
   while the application does not exist yet.
3. Every planned test runs and fails, in its own body, for one reason only:
   `from app.main import app` raises `ModuleNotFoundError` because `app`
   does not exist yet. A planned test that passes now checks nothing, a
   skipped one checks nothing either, an error in a fixture is not a failing
   test, and a test that fails for any other reason, an import from another
   module included, is refused.

The server runs pytest with plugin autoloading off and `PYTEST_ADDOPTS`
empty, so a plugin the tests need must be named in the configuration. When
all three hold, the server commits the test files itself and pins them with
the pytest configuration: from then on these files, any `conftest.py`, the
`pytest.ini` and the `[tool.pytest]` table cannot change. Make them right now.

## In this turn

- Write no `done-report` and no `test-cleanup` block. Committing is optional;
  the server commits what you leave.
- Do not push, open a pull request, or ask the person anything: nobody answers
  until the next review. Decide within the approved design.
- Running the gate command or the tests in this repository is fine.
