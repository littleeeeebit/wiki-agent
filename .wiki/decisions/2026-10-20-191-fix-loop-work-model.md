---
scope: project
severity: contract
triggers: ["화면", "vision", "프레임", "스크린", "캡처", "공유"]
domain: vision
title: "Run the revision turn of the review loop with the work model selected on the screen"
pr: 191
merged: 2026-10-20
branch: "fix/loop-work-model"
---

# Run the revision turn of the review loop with the work model selected on the screen

What. The review loop button sends the work model (agent slot) selected on the screen together, and the server fills that value into the specification where the work model is empty. It does not change specifications that already have a work model assigned.

Why. rfg_rag #74·#75·#76: The work model was selected as `gpt-6.1-sol`, but `claude-opus-5-5[1m]` was recorded in the revision turn after the review loop finished. This is not a display error. The model in the work cell record (`raw/work/rfg_rag-worktrees/*.jsonl`) is the value returned by the CLI when it finished the turn, and all three PRs actually ran the revision turn with the Claude default model. Cause: The specification (`minimal()`) created by importing a GitHub PR as is does not have a work model (`cell`). Since the revision turn of the loop only reads `spec["cell"]`, it becomes the Claude default model if the value is empty. Specifications started as a task in the app (#61~#67) include the model selected by `startSpec` and `codex:gpt-6. …

Source. PR #191 · `fix/loop-work-model`
