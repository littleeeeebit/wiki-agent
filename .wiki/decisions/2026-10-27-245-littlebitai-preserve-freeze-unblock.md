---
scope: project
severity: preference
triggers: []
domain: ''
title: "Phase 1 behavior pinning tests are pinned even when accompanied by specification changes, and test-only dependencies are accepted."
pr: 245
merged: 2026-10-27
branch: "LittleBitAI/preserve-freeze-unblock"
---

# Phase 1 behavior pinning tests are pinned even when accompanied by specification changes, and test-only dependencies are accepted.

What. In Phase 1 (preserve), if the worker submits the behavior pinning test list (`refactor-tests`) and the specification change (`spec-update`) in the same response, the server now checks and pins the test list first. Previously, it processed the specification change, requested a completion report again, and ended the turn, causing the test list to be unread and discarded (`tool/main/specs.py`). …

Why. The Phase 1 card `preserve-repository-boundaries-1f9c4293` for ai-companion-voice-agent is stuck. The worker committed 28 tests (`d2563f3d`) and submitted the test list and specification change (version 2) in the same response. The server only processed the specification change and requested a completion report, and the worker subsequently only replied that they were "waiting for the server's pinning confirmation." With the server waiting for the list and the worker waiting for the server, the card is stuck at `작업 중` without failing or progressing. The same commit includes `react-test-renderer` required for front-end tests added to `devDependencies`. …

Source. PR #245 · `LittleBitAI/preserve-freeze-unblock`
