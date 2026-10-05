# Stage 6 — hub scope

Let the hub refactor itself without touching the checkout it runs from.

## Work

- Hub scope runs only in separate worktrees, like any task; the running
  server's checkout is never edited. After merge the person restarts the app.
- Records are namespaced by scope and repository identity, as self-improvement
  records are. A hub result never becomes a shared rule by itself.
- First use on the hub's own code is left out. The owner decided that this
  series builds the feature only, and applying it is a later, separate run.

## Tests

Hub run refuses the original checkout; records of hub and project runs do not
mix.

## As built

- `refactor.switched` gives each hub step, the characterization step
  included, a linked worktree at `worktree_home(repo)/<spec id>`. The step
  branches from the previous step's branch, and the server's checkout keeps
  its branch and HEAD. Hub specs omit `workspace_mode`, as linked tasks
  always have, so merge cleanup removes each tree.
- The runner is driven from that worktree. Its gate comes from the original
  checkout (`refactor_profile.prepare(gate=...)`), because the adapter that
  names it is per-machine wiring Git does not carry.
- Full mode is refused for the hub: the planner forks the checkout the server
  runs from. Cleanup and module restructure are open.
- Runner records sit under `raw/refactor/hub/`; project runs use
  `raw/refactor/project/`. The tab warns that a merged hub step needs an app
  restart.

Tests: `test_the_hub_refactors_in_linked_worktrees_and_never_switches_its_own_checkout`
in `tool/test_refactor.py`.

## Rollback

Disable hub scope; project scope is unaffected.
