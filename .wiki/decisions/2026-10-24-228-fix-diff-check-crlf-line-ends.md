---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Prevents the merge preparation commit from stopping at the gate with \"ended with 2\" due to CRLF line endings."
pr: 228
merged: 2026-10-24
branch: "fix/diff-check-crlf-line-ends"
---

# Prevents the merge preparation commit from stopping at the gate with "ended with 2" due to CRLF line endings.

What. While merging ai-companion-voice-agent `plan-32916e65`, it stopped with "Stopped — gate / gate failed twice in a row — gate ended with 2".

Why. Because the change path of this PR is not connected to the check, the gate ran with one default check `git diff --check <merge-base> HEAD` (#223). ai-companion is a repository that has originally committed `.wiki/graph.json`·`.wiki/corpus.json`·`.omm/` with CRLF (all 1178 lines of graph.json in both 13c11f0·17597ac are CRLF). The merge preparation kept those line endings as they were and rewrote them as CRLF. `git diff --check` treats `\r` of line endings as trailing whitespace by default. Therefore, an error occurred on every changed line and it failed with exit code 2. The same scope `git -c core. …

Source. PR #228 · `fix/diff-check-crlf-line-ends`
