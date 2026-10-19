# Stage 1 — audit every read of the selected project

Purpose. Before the selection stops being global, know every place that reads
it, and make each one that runs outside a request name its repository.
Otherwise stage 2 turns a silent global read into a read of whichever
repository a window picked last.

## What reads the selection today

`query.project()` and `query.current_repo()` are read at about 54 places in
`tool/main`. They fall into three kinds.

| Kind | Where | After stage 2 |
| --- | --- | --- |
| Route handler | `specs.py`, `work.py`, `planning.py`, `refactor_api.py`, `verification.py`, `architecture.py`, `suite.py`, `improvements.py`, most of `query.py` | Answered by the request's `X-Project`; no change beyond stage 2 |
| Thread started by a request | `query.py` `ask` (started with `contextvars.copy_context()`), `query.py` `record` (Jev shadow), `query.remember` and `recall` when called from them | Must carry the request's context or an explicit repository; `record` is started without a copied context today |
| Worker or loop thread | `refactor.charted` (reads `current_repo()` and stops a run as `moved`, then calls `planning.begun`), `work.ours` and `loop.mine` defaults | Pass the worker's or loop's repository; a default from the selection is removed |

`loop.poll`, the review loops and merge maintenance already pass repository
paths explicitly (`loop.Loop.repo`, `maintenance.indexes(path)`). The audit
confirms that and pins it with a test rather than assuming it.

## Work

1. List every call with its enclosing function and whether a request context
   reaches it. A test walks `tool/main` with `ast` and fails on a call to
   `project()` or `current_repo()` from a function that is not a route, unless
   the function is on a short, reviewed allow list.
2. Give `query.record` the copied context, as `ask` already has.
3. Make `refactor.charted` and its `planning.begun` call use the worker's
   repository. Remove the `moved` stop.
4. Make `work.ours(path, repo)` and `loop.mine` take the repository from their
   caller; drop the `current_repo()` default.

## Done when

- The `ast` test passes with an allow list that holds only route helpers.
- A refactor run continues when another project is selected mid-run.
- Existing tests for switching (`test_main.py`
  `test_a_switch_waits_for_a_short_request_and_not_for_a_turn`,
  `test_a_turn_is_reachable_after_the_project_moves`) still pass.
