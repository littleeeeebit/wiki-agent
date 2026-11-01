---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: token savings step 8 — compact-before-idle page and --compact-window"
pr: 22
merged: 2026-09-25
branch: "token-diet-8-compact"
---

# feat: Token savings step 8 — compact-before-idle page and --compact-window

What. Step 8 of the token diet, the last of four stacked PRs. It is based on #21 (step 7), and will be rebased onto `main` and retargeted once #21 merges.

Why. `setup_agents.py --compact-window <tokens>` sets the automatic compact threshold in two places: - Claude's `autoCompactWindow` in `~/.claude/settings.json`. - `model_auto_compact_token_limit` at the top level of every Codex home's `config.toml`. That includes Orca's per-account homes, via `apply.user_files`. - Nothing else is touched. …

Source. PR #22 · `token-diet-8-compact`
