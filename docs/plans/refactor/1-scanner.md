# Stage 1 — scanner and ratchet

Measure debt the same way in any repository, and make every final gate refuse
to let it grow.

## Work

`tool/debt.py` measures tracked and new code files (an extension allowlist,
symlinks and undecodable files skipped):

| Metric | Meaning |
| --- | --- |
| `lines` | Physical lines |
| `dup` | Lines inside a block of six significant lines that appears elsewhere; blank, punctuation-only, import and comment lines are not significant |
| `block` | Longest indented body under a header ending in `:` or `{`, containers excluded — an approximate function length |
| `churn` | Commits touching the file in the last 180 days |

`scan` ranks by `(lines + dup) × (1 + churn)`. `init` writes
`.wiki/ratchet.json` with an entry for every file over its cap or holding a
duplicate. `check` fails when a file exceeds its entry, or, without one,
exceeds 800 lines (1,500 for test files) or holds any duplicate. Against the
merge base it also fails a raised entry without a new `reason`, and raised caps
or added `exclude` globs without a new top-level `reason`. A malformed file
fails. `tighten` lowers entries to current numbers, drops entries the caps now
cover and entries for deleted files, and never raises.

## Contracts

- `specs.required` appends `debt.command(spec base)` to the adapter gate, so
  the final gate, its digest and `proven` all include the ratchet. A repository
  without `.wiki/ratchet.json` passes. The base is passed only when it is a
  plain branch name; anything else is left out rather than quoted into a shell.
- `maintenance.prepare` runs `tighten` at Merge and commits a changed ratchet
  with the indexes (`git add -f`, since `.wiki/` is often ignored).
- The hub adopted its baseline in this PR; `.wiki/.gitignore` admits the file.

## Errors

Changing the gate command invalidates final results recorded before this
change once ("the command or environment changed"); the next final gate run
re-proves them. An unreadable base skips only the raise check and says so in
the output.

## Tests

`tool/test_debt.py`: duplicate detection and its noise filters, block length,
every check rule including reasons and a malformed file, tighten only lowering,
and the gate command running the check through a shell with a hostile base
refused.

## Rollback

Revert the `specs.required` line to drop enforcement; `tighten` and the
committed baselines are inert without it.
