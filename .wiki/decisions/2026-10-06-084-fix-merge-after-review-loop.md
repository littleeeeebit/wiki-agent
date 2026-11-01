---
scope: project
severity: preference
triggers: []
domain: ''
title: "Fixing two defects where merges were canceled or failed after the review loop ended"
pr: 84
merged: 2026-10-06
branch: "fix-merge-after-review-loop"
---

# Fixing two defects where merges were canceled or failed after the review loop ended

What. Fixing two P0 issues where a merge would not succeed if `[머지]` was pressed after the review loop ended with `머지 가능`. Both actually occurred in `project-codeit-mid/deploy-gcp-vm-ssh-tunnel` (PR #22), and the cause was identified through saved specification logs and `raw/errors.jsonl`.

Why. When merge is pressed, a preparation commit (wiki index + `.omm` structure document written by the model) is uploaded to the PR, and that commit is reviewed again. - A reviewer rejected an `.omm` sentence written by the model as P1 (R4, R6 — `.omm/*/description.md`). A revision turn fixed the document, and the next round approved it. - However, `requested_merge` canceled the request because it only merged if the preparation commit saved at the time of clicking and the approved commit were exactly the same. Since `.omm` was newly generated every time it was pressed, the same thing repeated. - Now, if the approved commit is a descendant of the preparation commit, and the files changed in between are ones that the preparation stage can change itself (Markdown, `.omm/`, `.wiki/corpus.json`·`.wiki/graph. …

Source. PR #84 · `fix-merge-after-review-loop`
