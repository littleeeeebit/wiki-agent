---
name: after-merge
description: >-
  Clean up after a pull request is merged — return to the default branch, pull,
  delete the merged local and remote branches, stop any servers this session
  started, clear scratch directories, and run the wiki lint. Use when the user
  says "머지했다", "merge 완료", "브랜치 정리", or otherwise reports that a PR landed.
---

# Cleanup after a merge

The rule behind this skill is `operator/after-merge-cleanup`. Why it works this
way is on that page; only how to do it is written here. That page also carries
this repository's server stop command (`server_stop`) and scratch directories
(`scratch_dirs`) — a skill is the same file in every repository, so those are
read off the injected page.

Do all of it. Do not wait to be told one step at a time.

## Steps

### 1. Confirm the merge

```bash
git fetch origin --prune
gh pr view <N> --json state,mergedAt,mergeCommit
```

If it has not merged, stop and say so. Cleaning up something unmerged loses work.

### 2. Return to the default branch

```bash
git checkout main && git pull --ff-only
```

`--ff-only`, so no merge commit appears quietly. Do not switch a working folder
that has uncommitted changes or an independent review session in it. A request
to merge includes the cleanup and does not wait for a second approval.

### 3. Delete the local branch — check before deleting

`git branch -d` refuses a squash-merged branch, because its tip is not an
ancestor of `main`. Before forcing it with `-D`, check that the content really
landed.

```bash
git rev-parse <branch>^{tree}          # these two
git rev-parse <merge-commit-of-that-PR>^{tree}
git diff --stat <branch> <merge-commit-of-that-PR>
```

Same, then `-D`. Different, then review the conflict resolution or follow-up
commits, preserve them as a bundle, and decide. Differing from the latest main
is not by itself evidence that it is unmerged. For a branch attached to a
worktree, finish preserving assets below first, then remove the worktree and
delete it. Remove an Orca worktree with Orca.

### 4. Remote branch

The merge usually deleted it already. Check with `git branch -r` after
`git fetch --prune`, and delete it if it is still there.

### 5. Stop the servers

Stop everything this session started, with the page's `server_stop`. Check by
port — it is more certain than the process list. Only what this session
started: another project's server on the same machine looks identical by name
and by timestamp, so tell them apart by command line
(`operator/run-inside-this-session`).

### 6. Clear the scratch directories

The directories are the page's `scratch_dirs`. Check the absolute path of what
is being deleted, and what the links point at, first. In a merged worktree,
check for uncommitted changes first, then preserve audio assets, runtime data,
live and review evidence, and shared dependencies outside the worktree when
they are needed. A junction loses the
link only, never the target. Leave an independent review session the user
opened.

### 7. Check the wiki

```bash
python tool/lint.py --repo <this repo>       # the hub wiki's structure
python tool/repo_lint.py --repo <this repo>  # this repo's knowledge
```

What to do with a finding is in the wiki's `MAINTENANCE.md`.

A finding does not stop the cleanup. Go through step 8 and report them together.

### 8. Report the state

Query local branches, remote branches and worktrees again, and report values
you checked: the branch list, server state, worktree cleanliness. Do not write
"cleaned up" — write what was deleted, what is left and why it was kept.
Include any lint findings.

## If something blocks

If one step blocks, do the rest, and name what blocked and why. Do not narrow
the scope quietly.
