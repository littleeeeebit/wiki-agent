---
scope: project
severity: preference
triggers: []
domain: ''
title: "Automatic recovery of review loop progress and addition of suite execution list"
pr: 71
merged: 2026-10-04
branch: "fix/review-loop-continuity-suite"
---

# Automatic recovery of review loop progress and addition of suite execution list

What. Fix the flow where the next round would start before the review modification was finished, or subsequent execution would be cut off after cell termination. The next review starts after the evidence, inspection, and commit posting for each modification report/discovery are completed, and interrupted active loops are automatically recovered by server restart and the poller.

Why. Continue with approved subsequent tasks and pending instructions even if completion recording or notification issuance fails. Different discoveries at the same location must each have a processing report. - Lock server ownership and block new executions upon termination. Wait until the review driver, task callback, and plan handover are actually finished, and review handovers approved during termination are automatically resumed by the next server. Also prevent read failures during Windows specification file replacement. - Protect termination from cancellation until the HTTP producer and monitor are cleaned up when a mobile connection is terminated. Verify with early connection termination regression tests and iterative tests. - Query task, review, plan, conversation cells, and recent execution lists for repositories selected in the suite. Provide status filters, detailed views, and task connections. - `. …

Source. PR #71 · `fix/review-loop-continuity-suite`
