# Step 15 — A fix rebuilds only what it changed

The overall design is in the [overview](0-overview.md). A build turn that
fixes one thing should compile that one thing, not the whole program again.

## Why this step exists

The `vtuber-ko-dub` run's work-turn log
(`raw/work/vtuber-ko-dub-worktrees/oneshot-initial.jsonl`) and its worktree's
`target` show what each fix cost.

| What happened | What the record shows |
| --- | --- |
| Every build after a one-line change compiled the whole `vtuber-core` crate | The server ran every agent with `CARGO_INCREMENTAL=0` (#366). 36 recorded builds of the crate took 30 to 60 seconds each |
| The same crate was built under many flag sets | `nextest -p vtuber-core` (523 times), `build --tests`, `build`, `clippy --all-targets`, `check --tests`, `nextest --workspace` and more. Each flag set compiled the changed crate again |
| Switching flag sets rebuilt the dependencies | `target/debug/deps` held 41 builds of `vtuber_core` and 25 of each of `tokio`, `hyper` and `axum`: `-p vtuber-core` and `--workspace` unify features differently. The folder is 16 GB with no incremental cache at all |
| Why incremental compilation was off | Cargo never prunes its incremental cache, and each flag set keeps its own; a stopped run's `target` once reached 24 GB |

## Design

- The server no longer sets `CARGO_INCREMENTAL=0`. A fix recompiles only the
  parts of a crate it changed, as Cargo does by default; an owner's own
  setting still wins.
- At the end of every work turn, while the turn still holds its worktree,
  `common.build_cache.pruned` removes the least recently used incremental
  caches until the rest fit in 6 GB. It finds every Cargo build folder in the
  worktree (`.cargo-lock` beside `incremental`) whatever its target folder is
  called, one inside another included, up to eight folders down, through real
  folders only. It touches only a build folder whose Cargo lock it takes, so no
  Cargo build runs there meanwhile, whoever started it. One a build holds stays
  whole for the next turn's end, but its size counts toward the bound. Nothing
  is deleted through a link or outside the worktree. The open lock file pins
  the path down to the build folder before that path is checked against the
  root the trim began on. Each folder below is removed under its own pin, a
  file the trim creates and the system deletes when its handle closes. Only
  what really went counts toward the bound. This relies on Windows keeping a
  folder with an open file where it is; the oneshot builds on Windows, and
  elsewhere nothing is trimmed.
- Gradle's caches are not bounded here; the prompts only ask the agent to
  keep them warm.
- Every build prompt tells the agent to build and test with the gate
  command's own package selection, features and profile each time, and never
  to clean the build cache.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 15a | Incremental compilation on, the cache bounded at each turn's end, the build prompts' flag line | In this pull request |
