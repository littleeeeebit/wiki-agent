---
scope: project
severity: preference
triggers: []
domain: ''
title: "Rerunning a failed final check after fixing only the environment"
pr: 185
merged: 2026-10-19
branch: "fix/gate-env-retry"
---

# Rerunning a failed final check after fixing only the environment

What. If a modification turn ends without changing the head, the final check is rerun once at that head (`repaired`). This is because causes outside the code (such as the environment) may have been fixed. - If that re-check also fails, it stops with `게이트` as it does now. If a human clicks [Continue] (`continued`), it reruns the failure of that head once more with `retry_reason`. The previous attempt remains in receipt `history`. - Restart recovery leaves `recovered`. …

Why. In rfg_rag #73, the final check stopped with `기존 로컬 최종 검사: failed — 게이트가 1 로 끝났다. 자동 재실행하지 않습니다`, and the same error occurred even after clicking [Continue]. The gate command `.venv\Scripts\python` could not start because there was no `.venv` in the worktree. It ended in 0.3 seconds with cp949 "The system cannot find the path specified", and no tests were run. The work cell of the modification turn fixed the environment of the reranker worktree by connecting it to `.venv`. There was no commit because there were no code changes. The loop stopped with `최종 게이트 실패, 고친 커밋이 없다`. Clicking [Continue] read the `failed` receipt of the same head and stopped again. …

Source. PR #185 · `fix/gate-env-retry`
