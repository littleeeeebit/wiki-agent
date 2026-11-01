---
scope: project
severity: contract
triggers: ["지연", "latency", "텔레메트리", "턴 조립", "api 조립"]
domain: api
title: "feat: token savings ② — within a session, the rules page is provided in full only once, and turns exceeding the host limit only include the rules paragraph"
pr: 18
merged: 2026-09-25
branch: "token-diet-2-dedup"
---

# feat: token savings ② — within a session, rule pages are sent in full only once; turns exceeding the host limit only include the rule paragraph

What. Plan: `ai-coding-agent-wiki-public/docs/plans/token-diet-4-clone.md`, bundle ② (dedup, compact reset, page declarations, the two page shrinks). Rebuilt in this repository's package layout, not pasted.

Why. `hook.py` passes `--host` to `inject.py`. The host decides the ceiling (`wiki.LIMIT`); with no host nothing changes. - `inject.recall` reads this session's trajectory rows back (`trajectory.session_rows`). A page that declares `repeat: rule` and went out whole on a turn under the ceiling goes out as its rule paragraph afterwards. …

Source. PR #18 · `token-diet-2-dedup`
