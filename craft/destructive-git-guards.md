---
scope: craft
severity: contract
repeat: rule
triggers: ["reset\\s+--hard", "git\\s+clean", "checkout\\s+--", "git\\s+restore", "되돌려|되돌리", "작업.{0,4}(날아|잃|사라)"]
slots: []
enforce:
  deny: ["Bash(git reset --hard*)", "Bash(git clean -fdx*)"]
sources: []
sources_withheld: true
conflicts_with: []
links: [edit-files-as-diffs]
---

# Block the git that cannot be undone

Rule. `git reset --hard` and `git clean -fdx` are blocked. If something has to
be rolled back, make a commit and roll back on top of it. Where that is not
enough, the user types it themselves. `git checkout -- <path>` and
`git restore <path>` are the same family, held by a precondition instead of a
block: a revert loop only runs on a committed tree, so check the target paths
with `git status --porcelain` first and do not start if they are dirty.

Why. Both delete uncommitted work without asking. What sets them apart from
the other destructive git commands is that there is no way back at all —
reflog cannot restore a working tree.

## `git checkout -- <path>` is the same family — and is not on the deny list

So is `git restore <path>`. All three delete an uncommitted working tree
without asking, and reflog cannot bring it back.

## Held by a precondition, not by a block

`checkout -- <path>` does not go into `deny`. Putting it there blocks the
legitimate use along with the rest — the revert loop itself reverts with that
command.

The precondition instead:

> A revert loop only runs on a committed tree. At the start, check the target
> paths with `git status --porcelain`, and do not start if they are dirty.

Then the only thing a revert can restore is what was just committed, so there
is nothing to lose. This is the page's first sentence — *make a commit and
roll back on top of it* — applied to the loop, not a new rule. What was
missing was which commands that sentence covers.

Where this catches is predictable. In a repository that asks for a mutation
check on every unit of implementation, the loop runs at the end of every unit.

So the wiki has a second door besides repetition. Something that already
stands as a check or a block in even one place, and still makes sense when
carried to another project, comes in. See "census cannot see a rule that
works" in `ENFORCEMENT.md`.

## `git stash` is not here

It follows the project, so it lives in that repository's own documents and is
not brought in here.
