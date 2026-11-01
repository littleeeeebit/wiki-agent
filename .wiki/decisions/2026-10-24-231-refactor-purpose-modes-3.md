---
scope: project
severity: contract
triggers: ["화면", "vision", "프레임", "스크린", "캡처", "공유"]
domain: vision
title: "Refactoring purpose mode 3/3 — screen, delete previous method, modify actual verification"
pr: 231
merged: 2026-10-24
branch: "refactor-purpose-modes-3"
---

# Refactoring purpose mode 3/3 — screen, delete previous method, modify actual verification

What. Although investigation and execution were added to the server in PRs 1 and 2, the screen still showed the previous cleanup, reconstruction, and full-scale methods. Running it from the actual repository to the screen from end to end revealed several issues where the investigation stopped midway or the specification became poor.

Why. The screen was unaware of the new investigation record. The issues revealed in the actual repository were as follows: The practice repository that ran to the end did not have `origin/HEAD`, so the final command failed with `invalid refspec`, and that failure was incorrectly reported as "verification commit is different." The conda line starting with `ERROR` was caught as a baseline failure. The glob path written on the map was rejected as "not in the baseline commit." If the server is restarted, the mode and generation are initialized, locking the cards already created. If the hook makes the model speak more, the block remained in the previous message, not the last message. If one key exceeded 40 characters in the integration turn, 23 pipeline investigations were discarded there. Changed mode …

Source. PR #231 · `refactor-purpose-modes-3`
