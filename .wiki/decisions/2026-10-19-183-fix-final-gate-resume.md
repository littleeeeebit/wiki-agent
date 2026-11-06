---
scope: project
severity: preference
triggers: []
domain: ''
title: "Make [Continue] run the unfinished final check one more time"
pr: 183
merged: 2026-10-19
branch: "fix/final-gate-resume"
---

# Make [Continue] run the unfinished final check one more time

What. Added `again` to `verify.final`. If the local receipt is `incomplete` (timeout/manual stop) or `running` (leftover from server death), leave the previous attempt in `history` and run it one more time. `running` is read within the repository-specific lock, so it cannot be a live execution. - The `finalized` of the loop passes `again` only when the previous final result of the same head is `pending`. …

Why. In rfg_rag #72 r4, the local final check exceeded the 19-minute limit and ended in `incomplete`. This record remained in the receipt for the same commit/base, so every time [Continue] was pressed, it did not rerun the check and stopped again at `기존 로컬 최종 검사: incomplete — 검증 실행 시간 제한을 넘었다. 자동 재실행하지 않습니다`. The loop could not move unless a person deleted the receipt file. The same code (2db6b04) passed 1484 tests in 9 minutes and 49 seconds, and the tests before and after the stopping point passed individually in 13 seconds. It appears to have stopped on the execution environment side, and it could not be confirmed what stopped it as there is no record.

Source. PR #183 · `fix/final-gate-resume`
