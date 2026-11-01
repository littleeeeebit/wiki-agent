---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: The wiki has a public entry point, and lint prevents bypasses running via import tool.X"
pr: 5
merged: 2026-09-24
branch: "feat/wiki-standalone"
---

# feat: lint prevents the bypass where the wiki has a public gate and runs via import tool.X

What. Step 4 — `docs/plans/wiki-agent-4-wiki.md`.

Why. The public gate is the `__all__` of the `tool/wiki/__init__.py`. Two groups - Query: `pages`, `match_pages`, `render_parts`, `rule_index`, `source_map`, `label`, `budget`, `RULE_BUDGET`, `REPO_BUDGET` - Page format: `WIKI`, `SCOPES`, `front_matter`, `metadata_errors`, `links_of`, `resolve`, `hub_pages`, `project_pages`, `INJECTABLE`, `SLOT`, `adapter_path`, `slots_for` - Nine root modules (`apply`, `chat`, `graph`, `inject`, `lint`, `rep …

Source. PR #5 · `feat/wiki-standalone`
