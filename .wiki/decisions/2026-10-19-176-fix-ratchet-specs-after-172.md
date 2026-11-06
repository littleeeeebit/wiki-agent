---
scope: project
severity: preference
triggers: []
domain: ''
title: "#172 Restore main: specs.py ratchet and native temp tests"
pr: 176
merged: 2026-10-19
branch: "fix/ratchet-specs-after-172"
---

# #172 Restore main: specs.py ratchet and native temp tests

What. Raise the `.wiki/ratchet.json` threshold of `tool/main/specs.py` from 1,489 to 1,497 lines and state the reason.

Why. #172 (own temporary folder for every Windows gate) increased specs.py to 1,497 lines but was merged without raising the ratchet threshold, causing all PRs on top of main (#174, #175) to be blocked by `tool/debt.py check` in CI. Owner decision (2026-10-19): Raise the threshold in a separate PR. Reducing specs.py is not in the scope of this PR.

Source. PR #176 · `fix/ratchet-specs-after-172`
