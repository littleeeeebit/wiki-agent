---
scope: project
severity: contract
triggers: ["화면", "vision", "프레임", "스크린", "캡처", "공유"]
domain: vision
title: "The monthly translation limit is set in dollars; 0 means unlimited, and an incorrectly entered limit appears as an error on the screen."
pr: 262
merged: 2026-10-30
branch: "LittleBitAI/translate-limit-unlimited"
---

# The monthly translation limit is set in dollars; 0 means unlimited, and an incorrectly entered limit appears as an error on the screen.

What. `tool/translate/__init__.py` - `MONTHLY_USD = 5.0` The default value has been removed. - `limit()` is `float | None`. None, empty value, or `0` means no limit (`0.0`), and a number 0 or greater is that dollar amount. Values that are not amounts, such as `$10`, `10,00`, `-1`, `nan`, and `inf`, are `None`. ...

Why. As this month's translation usage reached $10.08, exceeding `.env`'s `TRANSLATE_MONTHLY_USD=10`, both the screen and hook's Korean↔English translations reverted to the original text (`translate._outcomes` status `limit`). Only English remained on the screen without any notice. If no limit was entered, the $5 hardcoded in the code was used, and `0` or unreadable values meant "translation off".

Source. PR #262 · `LittleBitAI/translate-limit-unlimited`
