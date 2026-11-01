---
scope: project
severity: preference
triggers: []
domain: ''
title: "Fixing the remaining three P2 items from the refactoring series review"
pr: 83
merged: 2026-10-06
branch: "fix/refactor-review-leftovers"
---

# Fixing the remaining three P2 items from the refactoring series review

What. Fixing the three P2 items left as non-blocking for merge in the refactoring series (#77–#82) review in a single PR.

Why. #77 Debt Scanner(`tool/debt.py`): It failed to recognize multi-line imports with comments after the opening line, such as `from x import ( # 주석` or `import { // 주석`, as imports, counting the subsequent name lines as duplicate code. Now, it strips trailing comments when determining the opening line. - #78 Runner command execution(`tool/refactor_profile.py`): On Windows, commands were started and then added to a job, allowing grandchild processes created in between to escape the job and survive timeouts or cancellations. Now, commands are started in a suspended state, added to the job, and then resumed. If resuming fails, the entire job is cleaned up and an error is raised. …

Source. PR #83 · `fix/refactor-review-leftovers`
