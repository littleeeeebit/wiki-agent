---
scope: project
severity: preference
triggers: []
domain: ''
title: "Synchronize architecture description according to the merge flow"
pr: 76
merged: 2026-10-05
branch: "fix/pr75-architecture-sync"
---

# Synchronize architecture description according to the merge flow

What. Correct the outdated merge flow description of `.omm` confirmed after merging PR #75. The existing description mentioned automatic refresh and automatic merge after merge, which have already been removed. It has been corrected to the sequence of explicit merge request, pre-maintenance of PR checkout, re-review of the changed head and final gate, and checking GitHub results and local cleanup according to the current implementation.

Why. `omm validate`: 4 perspectives valid (1 existing recommendation for state-transitions node count) - `python tool/lint.py --repo .`: No findings - `python tool/repo_lint.py --repo .`: No findings - Modified files have no UTF-8 BOM Independent review: After 1st P1 (outdated diagram/memo) correction, no P0/P1/P2 in 2nd, `머지 허용` (`adbb7c3`). Full gate of the same approved head: `1,560 passed, 2 skipped, 1 warning` (exit code 0). Two intermittent failures occurred in the first run, but each and the corresponding file bundle re-runs passed, and the full re-run also passed.

Source. PR #76 · `fix/pr75-architecture-sync`
