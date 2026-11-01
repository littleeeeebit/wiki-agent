---
scope: project
severity: preference
triggers: []
domain: ''
title: "Enable project selection even during tasks, and add project plans per window"
pr: 178
merged: 2026-10-19
branch: "fix/project-picker-lock"
---

# Enable project selection even during tasks, and add project plans per window

What. The top-left project selector was disabled if anything was running in the current project. This applied if even one task tree turn was running or an instruction was pending. Therefore, it was impossible to switch to another repository while working on this repository.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #178 · `fix/project-picker-lock`
