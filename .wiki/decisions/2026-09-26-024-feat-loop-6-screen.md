---
scope: project
severity: contract
triggers: ["화면", "vision", "프레임", "스크린", "캡처", "공유"]
domain: vision
title: "feat: loop stage 6 — work-centered 3-stage screen and map per repository"
pr: 24
merged: 2026-09-26
branch: "feat/loop-6-screen"
---

# feat: loop stage 6 — task-centric 3-pane screen and map per repository

What. loop stage 6. We removed the screens temporarily attached to the old framework in stages 3–5 and rebuilt the task (specification)-centric 3-pane structure in the order of `design-pass`. The map follows the project of the rail and draws the document graph of that repository. Removed: "New Task" input field and `POST /api/worktrees`, "→ Task" draft and `/api/draft`·`WRITERS`. Work starts only with [Start] of the specification or the review loop. Map server: `repo_graph. …

Why. The map does not call `repo_graph.write` — the server is not used for original checkout. Before and after the test calls, it checks the modification times of `git status` and `graph.json`. The hub layer does not load `project` pages that do not apply to that repository — rules of other repositories must not be mixed into the map of this repository. "Review Loop (1)" also goes through the selection modal — the loop uses review rounds. Opening it directly because there is only one is incurring a cost without confirmation. Among `1.1fr`, context clearing is an icon — at 1280px, the conversation header (three focus, two models, context clearing) needs 574px to stand in one line, but it was 520px at the same width …

Source. PR #24 · `feat/loop-6-screen`
