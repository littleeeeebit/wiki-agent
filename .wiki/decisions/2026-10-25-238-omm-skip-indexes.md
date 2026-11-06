---
scope: project
severity: preference
triggers: []
domain: ''
title: "Exclude the wiki index JSON from the diff of the structure document refresh."
pr: 238
merged: 2026-10-25
branch: "omm-skip-indexes"
---

# Exclude the wiki index JSON from the diff of the structure document refresh.

What. Exclude `.wiki/corpus.json` and `.wiki/graph.json` from both the changed file list and the diff. They are merely indices describing the document and are unrelated to the code structure. - PRs where only the index has changed do not invoke the model.

Why. The structure document refresh during a merge (#237) sends PR diffs of up to 60,000 characters to the model. In ai-companion-voice-agent #257, that diff was 212,772 characters, most of which were `.wiki/corpus.json`·`.wiki/graph.json` and research documents that the merge preparation rewrites every time. As a result, the actual code changes were pushed to the back and truncated.

Source. PR #238 · `omm-skip-indexes`
