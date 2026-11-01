---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "gate: Make the gate use its own temporary folder on Windows"
pr: 172
merged: 2026-10-18
branch: "fix/gate-own-temp"
---

# gate: Make the gate use its own temporary folder on Windows

What. The gate (`specs.gate`) run by the app changes `TMPDIR`, `TEMP`, and `TMP` to `<TEMP>\wiki-agent-gate` on Windows and executes. This folder is used only by the app's gate.

Why. rfg_rag #64 The gate ended with 1 even though all 1330 tests passed (`판정 실패`). pytest cleans up `%TEMP%\pytest-of-dasdk` when it finishes. However, the Codex sandbox ran pytest with a different Windows account using the same username and left a `pytest-current` link in this folder. That link could only be read by `OWNER RIGHTS`, so the app account's pytest died with `PermissionError: [WinError 5]`.

Source. PR #172 · `fix/gate-own-temp`
