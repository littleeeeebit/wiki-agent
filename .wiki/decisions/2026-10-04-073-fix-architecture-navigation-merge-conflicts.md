---
scope: project
severity: preference
triggers: []
domain: ''
title: "App structure navigation, Korean display improvement, and merge conflict recovery"
pr: 73
merged: 2026-10-04
branch: "fix/architecture-navigation-merge-conflicts"
---

# App structure navigation, Korean display improvement, and merge conflict recovery

What. Architecture boxes open stored subgraphs or leaf descriptions. Wheel zoom and right-click drag operate on the fitted view, and the reset at the top left returns to that view. Graph labels, perspective names, and descriptions use a Korean overlay, but node IDs, source paths, and Mermaid source remain as they are.

Why. Merge approval now checks if the reviewed head can be merged with the latest base tip. If a conflict occurs, one integrated recovery per base tip is triggered, followed by a new check and review. Failed auto-merges stop iterative retries and retain a visible reason. The viewer renders static SVG and bypassed the Korean overlay. The merge gate checked the merge base, which can remain unchanged when `main` proceeds with conflicting edits. The local log recorded 45 identical auto-merge failure attempts for project-codeit-mid PR #14. Diagnostics, sources, and limits are recorded in `docs/research/architecture-navigation-merge-conflicts.md`. …

Source. PR #73 · `fix/architecture-navigation-merge-conflicts`
