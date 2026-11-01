---
scope: project
severity: preference
triggers: []
domain: ''
title: "docs: Documents in English — docs/ and plans, and rules"
pr: 30
merged: 2026-09-26
branch: "docs/english-first-docs"
---

# docs: Documents in English — docs/ and plans, and rules

What. Translate documents into English, and change the rules themselves. `docs/` everything (including plans), `DESIGN.md`, and `tool/hook-diagnostics.md` have been translated into English. Humans read the cited original text via a translation overlay. This is a PR built on top of #29.

Why. Rule change — Questions asked to the wiki are translated into English, but `docs/` was in Korean, so it appeared less in searches. I changed `operator/english-progress` to "All documents are in English, humans read via overlay." Things that humans read as-is (hook rejection, `systemMessage`, UI labels, PRs) remain in Korean (Discarded: the existing rule that `docs/` remains in Korean). I made the code that reads plans accept English — `## Steps`, status `Done`/`Complete`, plan row `Done — PR #n`. Old Korean plans (`단계`/`완료`) are also read as-is. Since the translator mixes "complete" as `Done` and `Complete`, it accepts both ...

Source. PR #30 · `docs/english-first-docs`
