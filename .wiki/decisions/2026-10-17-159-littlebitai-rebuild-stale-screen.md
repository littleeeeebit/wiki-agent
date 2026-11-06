---
scope: project
severity: contract
triggers: ["화면", "vision", "프레임", "스크린", "캡처", "공유"]
domain: vision
title: "Rebuild when starting an old screen build"
pr: 159
merged: 2026-10-17
branch: "LittleBitAI/rebuild-stale-screen"
---

# Rebuild when starting an old screen build

What. Fixes a P0 defect where Codex agent options appeared as JSON text instead of buttons, and the turn appeared to have ended.

Why. After #157 was merged, the original checkout received new code via `git checkout main`·`git pull` in a different session. This path does not run `npm run build` (it only runs the update button inside the app). The server restarted with the new Python, so Codex produced `choices` blocks according to the new instructions. However, `web/dist` was the October 14th build and lacked the `choicesOf` parser. Therefore, the blocks appeared as raw JSON.

Source. PR #159 · `LittleBitAI/rebuild-stale-screen`
