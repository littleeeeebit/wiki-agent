---
scope: project
severity: preference
triggers: []
domain: ''
title: "docs: Organize plans into series folders, and move finished series to done/"
pr: 10
merged: 2026-09-25
branch: "docs/plans-folders"
---

# docs: Divide plans into series folders, and move completed series to done/

What. Divided `docs/plans/` into series folders. Ongoing `loop/` and completed `done/english-first/`·`done/wiki-agent/`. The folder handles the file name prefix (`loop-0-overview.md` → `loop/0-overview.md`). Updated links between documents, README, `tool/lint.py` comments, and `overview.html` to the new paths. Steps 2 and 3 of the english-first overview to `완료`. …

Why. 13 plans piled up without folders were mixed with ongoing and completed ones. The way to prevent SessionStart from reading completed series is also organized into one `done/`.

Source. PR #10 · `docs/plans-folders`
