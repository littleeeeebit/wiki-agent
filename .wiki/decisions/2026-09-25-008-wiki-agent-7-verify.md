---
scope: project
severity: preference
triggers: []
domain: ''
title: "test: Step 7 verification — Run the entire process in the window with translation off and on, then fix the caught landmines"
pr: 8
merged: 2026-09-25
branch: "wiki-agent-7-verify"
---

# test: Step 7 verification — Run the entire process in the window with translation off and on, then fix the caught landmines

What. Step 7 of the wiki-agent plan. The plan is `docs/plans/wiki-agent-7-verify.md`.

Why. I ran the process of attaching to the actual Tauri window via CDP (WebView2 remote debugging) to create a task tree → wiki query → open citation → draft task → write approval → terminal → cleanup once with translation off and once with it on. - Off side: `/api/translate` 0 cases, monthly usage remains the same. Agent response is in original English - On side: Agent response is in Korean overlay, usage $0.051265 → $0.051502 - In both cases, there is no file before approval, and it only exists in the task tree after approval. Closing the window also shuts down the server - `path:line` citations that came without backticks were missing from the draft's evidence and clickable citations - The citation drawer has a fixed width, so the query side was crushed to 76px …

Source. PR #8 · `wiki-agent-7-verify`
