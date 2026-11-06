---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "Checking that the working tree is clean does not stop the gate, transition, or merge because of the decision logs left by the hook."
pr: 220
merged: 2026-10-23
branch: "fix/gate-ignores-harvested-decision-records"
---

# Checking that the working tree is clean does not stop the gate, transition, or merge because of the decision logs left by the hook.

What. `specs.status` / `specs.dirty` Consolidate all "is the working tree clean" checks for the server into one. Except for the untracked files under `.wiki/decisions/`, all other changes are blocked as is. - List untracked files one by one (`--untracked-files=all`). This is because if Git collapses the entirely untracked `.wiki/` into a single line, other files next to the log might be hidden. …

Why. #219 The plan `plan-32916e65` for ai-companion-voice-agent after the merge was posted as PR #248, but the review loop stopped with "Stopped — Gate · The gate failed twice in a row — Uncommitted changes". This is due to 20 untracked decision logs used by the session start hook (`tool/sync.py`) for checkout. Since that repository collects logs manually and commits them (216 tracked), the untracked logs in between are in a normal state. The same logs likely caused a stop during the refactoring stage transition and the check immediately before the merge.

Source. PR #220 · `fix/gate-ignores-harvested-decision-records`
