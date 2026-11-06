---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: Langfuse trace for every question execution"
pr: 43
merged: 2026-09-28
branch: "langfuse-tracing"
---

# feat: Langfuse trace for every question execution

What. Save the execution of a single question (`knowledge.Run`) as one trace in the self-hosted Langfuse. This is to read "why it was rejected" from the record at that time when an answer is withheld. If you try to reproduce it by running it again, it might not be reproducible. SDK v4 (OpenTelemetry), `tool/main/tracing.py`. Since the server is v4, it does not receive traces with the old `/api/public/ingestion`. `langfuse==4.15.6` fixed. …

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #43 · `langfuse-tracing`
