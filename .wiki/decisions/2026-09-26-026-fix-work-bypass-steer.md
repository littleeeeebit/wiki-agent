---
scope: project
severity: preference
triggers: []
domain: ''
title: "fix: work agent defect five — bypass, interruption, hook display, choice question, deletion of circular work tree"
pr: 26
merged: 2026-09-26
branch: "fix/work-bypass-steer"
---

# fix: work agent defects five — bypass, interruption, hook display, choice question, deleting running work tree

What. Five defects of the work agent found while using it directly. It keeps asking for permission (bypass doesn't work) → Settings · General "Do not ask for work permission" (default on, save immediately, from next turn). If turned on, Claude is `bypassPermissions`, Codex is `danger-full-access` + `never` cannot talk to the running agent → The input window remains open while running, and the sent message enters in that turn. …

Why. bypass is user selection (setting switch, default on). If turned on, the CLI does not ask, so the refusal to write outside the work tree also disappears. Claude's `permissions.deny` remains, even in bypass it leaves `--permission-prompt-tool stdio` — if removed, the CLI removes `AskUserQuestion` entirely (measured). Claude's interruption knows when the CLI accepts it via `--replay-user-messages`. Messages entered after the last step are included in this turn, even if the CLI runs one more turn. It does not leak into the next turn …

Source. PR #26 · `fix/work-bypass-steer`
