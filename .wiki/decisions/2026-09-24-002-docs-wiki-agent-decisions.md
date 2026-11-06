---
scope: project
severity: preference
triggers: []
domain: ''
title: "docs: wiki-agent Phase 1 decision and Phase 2 sub-plan"
pr: 2
merged: 2026-09-24
branch: "docs/wiki-agent-decisions"
---

# docs: wiki-agent Phase 1 decision and Phase 2 sub-plan

What. `docs/plans/wiki-agent-0-overview.md` — Reflect Phase 1 (direction confirmation) decision - Tauri + Python sidecar, complete replacement of Orca - Agent is event stream, terminal is xterm.js + Rust `portable-pty` (Phase 6) - Each pipeline has `tool/` subfolder, main is `tool/` root → `tool/main/` in Phase 6 - All writes require screen approval, task tree is `.. …

Why. `tool/main/` is not being built now. Just moving `chat.py` only changes the path, and it will be rewritten in Phase 6 - `tool/common/` is only allowed by rule and is not being built now. There are no modules shared by two pipelines yet (`wikilib` belongs to `wiki`) 🤖 Generated with [Claude Code](https://claude.com/claude-code)

Source. PR #2 · `docs/wiki-agent-decisions`
