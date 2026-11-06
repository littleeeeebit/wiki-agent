---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Ensure the final gate does not read harvested decision records as uncommitted changes."
pr: 235
merged: 2026-10-25
branch: "final-gate-harvested-records"
---

# Ensure the final gate does not read harvested decision records as uncommitted changes.

What. `verify.identity` does not count untracked `.wiki/decisions/` records as changes. Since it lists files one by one with `--untracked-files=all`, other files cannot hide behind a folder line. - Keep only one path constant `verify.HARVESTED`, and `specs.HARVESTED` uses it. - `hook_row` has not been changed. `workspace. …

Why. ai-companion-voice-agent #257(`finalizer-candidate-exclusions`) is stuck at merge step 8 "Prepare commit independent review/final gate". > Stopped — Local verification preparation · Final verification results cannot be used because there are uncommitted changes. 7 decision records (`.wiki/decisions/2026-10-25-*.md`) harvested in PRs #250~#256, where the start hook (`tool/sync.py`) was merged into the shared checkout, remained untracked. These records are for a human to collect and commit. `specs.status` (round gate/dirty judgment) and `verification.checkout_proven` already excluded these records. The final gate's `verify. …

Source. PR #235 · `final-gate-harvested-records`
