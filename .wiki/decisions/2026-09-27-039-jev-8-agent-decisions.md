---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: Jev stage 8 — agent decisions at server-owned points"
pr: 39
merged: 2026-09-27
branch: "jev-8-agent-decisions"
---

# feat: Jev stage 8 — agent decisions at server-owned points

What. Stage 8 of `docs/plans/jev/`: Jev chooses among code-owned operations at three decision points the server owns, and in active mode the choice changes what runs. The `. …

Why. Contract (`tool/main/decisions.py`): `ActionProposal` (`action-proposal/1`) with every field the plan names; a closed operation enum; candidates built by code; one `action` Choice per decision through the stage 6 `request`/`decide`/`checked` (new Choice kind, provisional confidence 0.6 / margin 0.2, own prompt version and artifact). …

Source. PR #39 · `jev-8-agent-decisions`
