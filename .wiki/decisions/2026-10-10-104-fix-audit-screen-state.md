---
scope: project
severity: contract
triggers: ["화면", "vision", "프레임", "스크린", "캡처", "공유"]
domain: vision
title: "External audit follow-up: Maintain terminal shell upon theme change, retry viewing evidence"
pr: 104
merged: 2026-10-10
branch: "fix/audit-screen-state"
---

# External audit follow-up: Maintain terminal shell upon theme change, retry viewing evidence

What. Remaining items from the external audit (2026-10-10), specifically screen bundles (U1·U2). Follow-up to #101 and #103.

Why. U1: The effect that creates the PTY was bound to `[cwd, theme]`, so a new shell opened after `pty_close` when changing the theme. Running commands and output disappear. In the pre-fix build, I verified 2 instances of `pty_open` and 1 instance of `pty_close` using the validation script. Additional defect discovered in U1: React executes child effects before parent effects, but the theme class is changed in the parent (App) effect. Therefore, before the fix, the new shell was rendered with the previous theme's palette (the background remained at `rgb(28, 31, 37)`). I made it read the palette one frame later. …

Source. PR #104 · `fix/audit-screen-state`
