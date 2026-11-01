---
scope: project
severity: preference
triggers: []
domain: ''
title: "refactor: divide the pipeline into folders, and lint blocks imports between folders"
pr: 3
merged: 2026-09-24
branch: "refactor/pipeline-boundary"
---

# refactor: divide the pipeline into folders, and lint blocks imports between folders

What. Step 2 — `docs/plans/wiki-agent-2-boundary.md`. Re-uploaded to `main` after merging #2.

Why. | Now | After moving | | --- | --- | | `tool/translate.py` | `tool/translate/__init__.py` (`import translate` as is, CLI is `python tool/translate --check`) | | `tool/sessions.py` | `tool/workspace/sessions.py` | | `tool/chat_session.py`, `tool/chat_local.py` | `tool/agent/` | | `tool/wikilib.py` | `tool/wiki/wikilib.py` | | Matching/rendering of `tool/inject.py` | `tool/wiki/match.py` | - `inject. …

Source. PR #3 · `refactor/pipeline-boundary`
