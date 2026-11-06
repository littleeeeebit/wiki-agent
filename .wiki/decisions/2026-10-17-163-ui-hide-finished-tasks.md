---
scope: project
severity: preference
triggers: []
domain: ''
title: "web: Hide merged tasks and stalled tasks from the rail"
pr: 163
merged: 2026-10-17
branch: "ui/hide-finished-tasks"
---

# web: Hide merged tasks and stalled tasks from the rail

What. Merged tasks are not visible anywhere on the rail. I removed the `끝난 것` column, and merged tasks that are left to be cleaned up (`정리 대기`) are also not visible. - Hide specifications that are `작업 중` but have no PR and whose branches are not checked out anywhere. Nothing can be run for these specifications (currently 12). - Only screen files were modified: `web/src/lib/tasks.ts`, `web/src/components/TaskRail.tsx`. Specification files are not deleted. …

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #163 · `ui/hide-finished-tasks`
