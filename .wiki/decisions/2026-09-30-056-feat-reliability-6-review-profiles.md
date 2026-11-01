---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: Separation of plan/code review criteria and server-provided discovery id — Reliability PR 6"
pr: 56
merged: 2026-09-30
branch: "feat/reliability-6-review-profiles"
---

# feat: Separation of plan/code review criteria and server-provided discovery id — Reliability PR 6

What. Reliability PR 6. Within a single review loop, separated plan, code, and mixed review criteria, and attached a stable id provided by the server to discoveries. There is no new state machine.

Why. Specification profile (`tool/main/specs.py`): The specification block can specify `review_profile` (plan · code · mixed) and `artifact_root`, which the server validates. plan must have `artifact_root`. Old specifications and PRs fetched from GitHub are read as code. `.md` does not infer plan solely from the extension. - Actual profile (`loop.effective`): If it is a plan but the changed file is outside the folder, is not Markdown, or the list of changes cannot be read, it is broadened to mixed. Code never passes solely based on plan criteria. - Criteria file: `review-round. …

Source. PR #56 · `feat/reliability-6-review-profiles`
