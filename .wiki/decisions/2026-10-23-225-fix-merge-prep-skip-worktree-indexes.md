---
scope: project
severity: preference
triggers: []
domain: ''
title: "Ensure merge preparation does not stop or lose modifications in files with skip-worktree or assume-unchanged flags."
pr: 225
merged: 2026-10-23
branch: "fix/merge-prep-skip-worktree-indexes"
---

# Ensure merge preparation does not stop or lose modifications in files with skip-worktree or assume-unchanged flags.

What. After allowing the merge in ai-companion-voice-agent `plan-32916e65` and clicking merge, the merge preparation stopped. The error message was "merge preparation failed — The following paths and/or pathspecs matched paths that exist".

Why. The tracked files `.wiki/corpus.json` and `.wiki/graph.json` of that checkout had the skip-worktree flag attached (from `git ls-files -v` to `S`). git considers items with this flag as "outside the sparse-checkout scope" and rejects `git add -f`. The same message is reproduced exactly even in an empty repository. This flag hides changes from status checks. assume-unchanged is the same. Therefore, the modifications by the person who hid them and the ratchet·`.omm` artifacts have not been visible until now. I could not find who attached the flag in the remaining records. The wiki-agent code does not attach this flag.

Source. PR #225 · `fix/merge-prep-skip-worktree-indexes`
