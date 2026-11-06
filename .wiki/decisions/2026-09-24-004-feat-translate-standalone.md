---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: translate has one public entry point and a monthly cost limit"
pr: 4
merged: 2026-09-24
branch: "feat/translate-standalone"
---

# feat: translate has one public entry point and a monthly cost limit

What. Stage 3 — `docs/plans/wiki-agent-3-translate.md`.

Why. The public entry point is the `__all__` of `tool/translate/__init__.py` — `translate`, `usage`, `glossary`, `KO_EN`, `EN_KO`. Delete `ko_to_en`·`en_to_ko` - `translate(texts, direction, deadline)` — no default value. Remove the default timeout of 60 seconds (`TIMEOUT`), and the screen side (`/api/translate` of `chat`, `mirror`) exceeds its own 60 seconds - monthly cost limit - accumulate the `usageMetadata` × `gemini-3.1-flash-lite` fee of the response (input $0.25, output $1.50 / 1M tokens) in the `spend(month, usd)` of cache sqlite …

Source. PR #4 · `feat/translate-standalone`
