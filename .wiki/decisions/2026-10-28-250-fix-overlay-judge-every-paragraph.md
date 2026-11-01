---
scope: project
severity: preference
triggers: []
domain: ''
title: "Filters out translations where the negation is correct but the meaning is inverted in verification answer translation."
pr: 250
merged: 2026-10-28
branch: "fix/overlay-judge-every-paragraph"
---

# Filters out translations where the negation is correct but the meaning is inverted in verification answer translation.

What. `checked`: Translations where numbers/identifiers do not match regardless of how they are read are rejected immediately as before. All remaining translations are judged by Gemini. - Group into pairs per request, and send requests simultaneously. - If the judgment is "changed", delete that translation from the cache, translate it one more time, and judge it again. - If a judgment cannot be received (limit/no key/timeout), show only translations that passed the strict check as before. …

Why. Even after #249, verification answer translations were displayed as is if the numbers were the same and the negation was present or absent in both sides. Therefore, translations with inverted meanings were not caught. `fails` → `통과한다`, `missing` → `있다`, `disable` → `켠다` double negation (`not uncommon` → `거의 없다`) translations with swapped cause and effect (`A caused B` → `B가 A를 일으켰다`) After creating and inserting 30 such inverted translations, all 30 were displayed on the screen.

Source. PR #250 · `fix/overlay-judge-every-paragraph`
