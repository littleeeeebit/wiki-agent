---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: token savings step 7 — keep-alive (keep the cache of a Claude cell that rests for one hour alive)"
pr: 21
merged: 2026-09-25
branch: "token-diet-7-keepalive"
---

# feat: Token diet step 7 — keep-alive (keeps the cache of a Claude session that has been idle for an hour alive)

What. Step 7 of the token diet, third of four stacked PRs. Based on #20 (step 6); rebased onto `main` and retargeted once #20 merges.

Why. A Claude session left idle for an hour loses its prompt cache, and writes its whole context again when the person comes back. The search daemon pings such a session at 55 minutes, at most `keep_alive` times per human utterance. - `tool/keepalive.py` handles SessionStart, Stop and SessionEnd in one command; the event comes from the payload. `inject.py` does the same for each utterance. …

Source. PR #21 · `token-diet-7-keepalive`
