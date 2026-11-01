---
scope: project
severity: preference
triggers: []
domain: ''
title: "feat: Jev Phase 1 — Execution settings, judgment transmission, baseline"
pr: 32
merged: 2026-09-26
branch: "jev-1-runtime-baseline"
---

# feat: Jev Phase 1 — Execution settings, judgment transmission, baseline

What. Implements Phase 1 of `docs/plans/jev/1-runtime-baseline.md`. Enables actual requests to Jev using the key registered in `.env`, allows the app and CLI to read the same settings, and leaves a reproducible baseline before changing the search.

Why. Settings: `tool/common/settings.py` is the only code that reads `.env`, and the translation also uses this. The order is file entries, environment variables, and default values; empty values also override, duplicates are won by the first entry, and shell expressions and variable substitutions are not executed. - Judgment transmission: Moved the prototype's request code to `tool/decision/`. Settings are read anew for each request and keys are not copied to the process environment, and the state only shows the presence of the key. It is `WIKI_JEV_MODE=off|shadow|active`, and if the key exists, the default value is shadow, if not, it is off, and the existing `WIKI_JEV=on` is active. …

Source. PR #32 · `jev-1-runtime-baseline`
