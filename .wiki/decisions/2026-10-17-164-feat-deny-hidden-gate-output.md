---
scope: project
severity: preference
triggers: []
domain: ''
title: "deny: Reject test execution piped to hide progress"
pr: 164
merged: 2026-10-17
branch: "feat/deny-hidden-gate-output"
---

# deny: Reject test execution piped to hide progress

What. If you pass the output of `pytest` (including `-m pytest`) to another command like `| tail`, `| grep`, or `| head`, the PreToolUse hook (`tool/deny.py`) will reject it. This is because passing it that way hides all progress until execution finishes. - The rejection message instructs to run it again without piping. If it takes a long time, you can use `run_in_background` or leave it as `| tee <로그 파일>`. - `| tee` is allowed. …

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #164 · `feat/deny-hidden-gate-output`
