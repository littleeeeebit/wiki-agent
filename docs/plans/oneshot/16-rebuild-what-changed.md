# Step 16 — A fix rebuilds only what it changed

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

- On Windows the server no longer sets `CARGO_INCREMENTAL=0`. A fix
  recompiles only the parts of a crate it changed, as Cargo does by default.
  Elsewhere nothing bounds the cache, so it stays off there; an owner's own
  setting wins either way.
- At the end of every work turn, while the turn still holds its worktree,
  `common.build_cache.pruned` removes the least recently used incremental
  caches, by their newest file, until the rest fit in 6 GB. It finds every Cargo build folder in the
  worktree (`.cargo-lock` beside `incremental`) whatever its target folder is
  called, one inside another included, up to eight folders down, through real
  folders only. It touches only a build folder whose Cargo lock it takes, so no
  Cargo build runs there meanwhile, whoever started it. One a build holds stays
  whole and unread for the next turn's end, and the count is logged as partial.
  No cache is measured or deleted through a link or outside the worktree. The open lock file pins
  the path down to the build folder before that path is checked against the
  root the trim began on. Each folder below is read and removed under its own
  pin, a file the trim creates and the system deletes when its handle closes,
  held while its children are read. The caches are counted once, every file
  by its identity, a file linked more than once counted once. Only counted
  files a removal itself freed come off that count: each it removed, through
  that file's own handle so the file removed is the one looked at, as its last
  link, in whichever cache that was. The handle opens the entry itself, never
  what a link there points at, and anything but a plain file is refused before
  it is marked for deletion. A file another handle still holds is not yet
  freed, so it is not credited, and only the system's own not-found answer
  shows a file gone. If a mark cannot be set or checked it is taken back; if
  it cannot be taken back either, the trim stops there. A cache that moved, would not go, or
  could not be read stays counted, and a removal that fails partway keeps what
  went and leaves the next its turn, so what is left is an estimate that the
  next turn's end counts again.
  Discovery is a provisional scan for names: it pins nothing, since a pin
  changes its folder's time and a build script may watch a source folder's
  time, so it may list a folder a junction swapped in meanwhile. Every name it
  finds is checked under its lock's pin before any cache is counted or
  deleted. This relies on Windows keeping a
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
| 16a | Incremental compilation on, the cache bounded at each turn's end, the build prompts' flag line | In this pull request |
