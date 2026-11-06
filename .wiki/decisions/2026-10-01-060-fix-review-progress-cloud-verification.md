---
scope: project
severity: preference
triggers: []
domain: ''
title: "Fix: Restore independent review/progress translation and Cloud verification permissions"
pr: 60
merged: 2026-10-01
branch: "fix/review-progress-cloud-verification"
---

# Fix: Restore independent review/progress translation and Cloud verification permissions

What. Branch: fix/review-progress-cloud-verification Final commit: 6060779a7f5f5313482c6e841ec6ee1ac49bb361

Why. Restored progress translation and line breaks for agents/reviews, and separated review models/sessions/event streams from implementation cells. Also fixed additional rounds for external/Cloud implementations and execution status display. Cloud reviews can execute commands and create files within restricted verification worktrees and artifact folders. Standard reviews remain read-only. Verified execution, file creation, temporary files, and resuming the same session without repeated permission requests. Terminal output is not automatically sent to external translation. Secrets and personal information in selected output must be removed in the preview and explicitly confirmed before transmission. If the verification environment, data scope, or browser settings change in the same PR commit, the review process is reopened with the new settings, but compatible conversations are continued. Implementation: GPT-6.1-Sol. …

Source. PR #60 · `fix/review-progress-cloud-verification`
