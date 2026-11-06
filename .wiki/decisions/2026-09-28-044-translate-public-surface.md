---
scope: project
severity: preference
triggers: ["지연", "latency", "텔레메트리", "턴 조립", "api 조립"]
domain: api
title: "fix: translate api_key and HANGUL_WORD in the public list"
pr: 44
merged: 2026-09-28
branch: "translate-public-surface"
---

# fix: translate api_key and HANGUL_WORD in the public list

What. Fix 2 items found by `lint.pipeline_surface`. `tool/main/knowledge.py` was using a name outside of `translate`'s `__all__`. `HANGUL_WORD` — #42 (Allow Korean names within English claims) `api_key` — #43 (Mask all credentials held by the process) Add two names to `__all__`. There is no change in behavior.

Why. (the PR body has no reason section; the grounds for this decision were not recorded)

Source. PR #44 · `translate-public-surface`
