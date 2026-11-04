# Stage 1 — audit every read of the selected project

Purpose. Before the selection stops being global, know every place that reads
it, and make each one that runs outside a request name its repository.
Otherwise stage 2 turns a silent global read into a read of whichever
repository a window picked last.

## What reads the selection today

Counted on 2026-11-04 at `2280c3c`. `tool/main` calls `query.project()` or
`query.current_repo()` 60 times. 50 of the calls sit directly in a route
handler, where the request's `X-Project` answers them in stage 2. The other
10 sit in functions outside a route:

| Reader | Reached from | After stage 2 |
| --- | --- | --- |
| `loop.mine`, `planning.begun`, `work.ours` (its `current_repo()` default) | Route handlers only | Answered by the route's `X-Project`; no change |
| `query.current_repo`, `session_key`, `config`, `session`, `remember`, `recall`, `hits_for` | Routes, and the question's thread `query.ask`, started with `contextvars.copy_context()`. Its hold refuses a switch until it ends, so today it cannot see one | The copied context carries the request's `X-Project`. Stage 2 lets another project switch during a question, so these must read the copied project, never a global |

`app.demo`, a command-line self check with no server, also reaches
`query.session`.

Threads that write without a request already name their repository:

- `specs.told`, called from `loop.poll` and work turns, passes `repo` to `query.remember`.
- The Jev shadow's `record` in `query.shadow` passes `repo` to `prepare` and `remember`.
- `loop.Loop.repo` and `maintenance.indexes(path)` carry paths.

`refactor.charted` and its `moved` stop no longer exist. They went with the
refactor purpose modes (#229–#231), and no refactor worker reads the
selection now.

## Work

1. A test walks `tool/main` with `ast`. It fails when a function outside a
   route calls `project()` or `current_repo()` and is not on the reviewed list
   (`test_main.py` `SELECTION_READERS`). A nested function counts as its own
   reader, since a route may start it on a thread. Done.
2. A test pins that the shadow's record keeps the turn's repository when a
   switch lands while Jev decides
   (`test_the_jev_shadow_records_into_its_turns_repository_after_a_switch`).
   It fails if `record` drops `repo=`. Done.
3. Dropped: `refactor.charted` is gone, and `work.ours` and `loop.mine` are
   reached only from routes, so they have no worker repository to pass.

## Done when

- The `ast` test passes with an allow list that holds only route helpers and
  the question thread's helpers. Met.
- No refactor worker reads the selection. Met, by the `ast` test.
- The existing switch tests (`test_main.py`
  `test_a_switch_waits_for_a_short_request_and_not_for_a_turn`,
  `test_a_turn_is_reachable_after_the_project_moves`) still pass. Met.
