---
scope: project
severity: preference
triggers: []
domain: ''
title: "Fixes the issue where entire paragraphs remained in English because the correct Korean translation of the verification answer was rejected."
pr: 249
merged: 2026-10-28
branch: "fix/overlay-paragraph-english"
---

# Fixes the issue where entire paragraphs remained in English because the correct Korean translation of the verification answer was rejected.

What. If the presence of negation is the same on both sides, the Gemini judgment is skipped. Double negation or inversions like `missing` → `있다` are still not caught, as before. - Increased the file size ratchet (`tool/translate/__init__.py` 1409 lines, `web/src/lib/api.ts` 1026 lines). The reason is written in `.wiki/ratchet.json`.

Why. In the 'next task' conversation of ai-generation, some paragraphs of the verification answer were not translated into Korean and remained in English. The translation itself was successful, but the pre-display check rejected the correct translation as "meaning changed," reverting the entire paragraph to the original English. I reproduced this with 30 scenarios mixing actual records and synthetic paragraphs; before the fix, English paragraphs remained in 16 out of 30 answers. - Number units: `486 M` → `4억 8,600만`, `1.2 billion` → `12억`, `Oct 28` → `10월 28일`, `2026-09-07` → `9월 7일`, `a dozen` → `12개`, `a tenth` → `10분의 1`. Now numbers are compared by value. …

Source. PR #249 · `fix/overlay-paragraph-english`
