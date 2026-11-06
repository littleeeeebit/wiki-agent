---
scope: project
severity: preference
triggers: []
domain: ''
title: "Reliability PR 8: Repeated findings require research before the next fix (2-cycle limit)"
pr: 132
merged: 2026-10-12
branch: "feat/reliability-8-recovery"
---

# Reliability PR 8: Repeated findings require research before the next fix (2-cycle limit)

What. This is an implementation of `docs/plans/reliability/8-recovery.md`. If the same critical finding (P0/P1) recurs, research must be conducted before submitting the next fix. That research is a read-only turn within the limit, and web search must be included. The research page is included in the same PR as the fix. There is a limit of two research/fix cycles per finding, after which a human decides.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #132 · `feat/reliability-8-recovery`
