---
scope: project
severity: contract
triggers: ["런처", "게이트", "postgres", "마이그레이션", "ci"]
domain: infra
title: "fix: remove duplication between wiki answer rejection gate and body sources"
pr: 59
merged: 2026-10-01
branch: "fix/wiki-answer-synthesis"
---

# fix: remove duplication between wiki answer rejection gate and body sources

What. In progress status questions, list evidence for every sentence and modify the common posting path that converted search uncertainty into answer rejection. The next task, wiki, and retrospective all use the existing comprehensive answer path to interpret completed, in-progress, and remaining tasks, and no exceptions are added for specific projects or question expressions.

Why. Keep sources and verification status in the existing 'View Evidence and Judgment' and remove internal citation marks and verification guidance from the body. Insufficient search, inability to judge, and call limits do not block general answers, and unknown citation IDs are left only for diagnostics. Comprehensive answers are not promoted to independently verified facts but are recorded as `unverified`, and the boundaries for explicit claim verification and task approval are maintained. Option, specification, and retrospective candidate blocks are also maintained. Isolated CLI answer sessions use only Read, Glob, and Grep. The server delivers the current checkout and the merge status of recent local commits and PRs via defined read commands. Limits are set to 10 seconds per command, 12,000 characters of output, and a maximum of 20 commits/PRs; if tools or GitHub cannot be used, the answer is provided with only those observations missing. …

Source. PR #59 · `fix/wiki-answer-synthesis`
