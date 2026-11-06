---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Prevent the gate from stopping with a 2 due to CRLF in the committed decision log."
pr: 223
merged: 2026-10-23
branch: "fix/gate-diff-check-skips-harvested-records"
---

# Prevent the gate from stopping with a 2 due to CRLF in the committed decision log.

What. The full refactoring plan for ai-companion-voice-agent (plan-32916e65, PR #248) stopped at `멈춤 — 게이트 · 게이트가 두 번 연속 실패했다 — 게이트가 2 로 끝났다\` during review.

Why. If the change path is not mapped to a check, the gate is deferred to a single `git diff --check <merge-base> HEAD\` (`specs.selected\`). Commit 06e5f45e (`docs: add decision records for #225 to #246\`) merged into the branch contained 20 decision logs harvested by the start hook. In that repository's decision logs, 220 out of 236 are CRLF (a remnant from when core.autocrlf was enabled). `git diff --check\` treats the CR at the end of the line as trailing whitespace and returns an exit code of 2. Therefore, the branch that committed the logs failed for the same reason every time. Logs harvested in #220 (specs. …

Source. PR #223 · `fix/gate-diff-check-skips-harvested-records`
