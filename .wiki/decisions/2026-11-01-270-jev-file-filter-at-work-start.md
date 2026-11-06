---
scope: project
severity: preference
triggers: []
domain: ''
title: "Include only the necessary files in the first message when starting a task and provide only the paths for the rest."
pr: 270
merged: 2026-11-01
branch: "jev-file-filter-at-work-start"
---

# Include only the necessary files in the first message when starting a task and provide only the paths for the rest.

What. When starting a task, the code collects candidates for files to read. The order is files mentioned as evidence in the specification, files found by searching the repository index with the goal utterance, and files modified in previous merge tasks that used the same files as evidence, with a maximum of 40 files that actually exist in the repository at the start time. - Location: `tool/main/file_filter.py`'s `candidates`, `searched`, `merged`, `Tree` - Request to the judgment model Jev once per candidate...

Why. When a human clicks start on a task card, the server sends the first message to the coding agent. Until now, this first message contained only the task specification and a single 'start' utterance, and the agent found and opened the files to read on its own. The agent spent time and increased input size by finding and opening the same files one by one in the first turn. Including all candidates would consume tokens for irrelevant files.

Source. PR #270 · `jev-file-filter-at-work-start`
