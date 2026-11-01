---
scope: project
severity: contract
triggers: ["화면", "vision", "프레임", "스크린", "캡처", "공유"]
domain: vision
title: "The screen and repository selection do not stop even while progress bars, such as model downloads, are running."
pr: 256
merged: 2026-10-29
branch: "fix/command-output-flood"
---

# The screen and repository selection do not stop even while progress bars, such as model downloads, are running.

What. `tool/agent/chat_events.py` `merged()` — Groups the output of the same command into one event (latest 4000 characters) every 0.5 seconds. If another event arrives, it sends out the collected output first to maintain order. `ChatSession._say` Hooks into one place and applies to all task turns, loops, reviews, and conversation focuses. - `web/src/lib/work.ts` — Subsequent output of the same command is appended to the previous step rather than a new step (latest 4000 characters). …

Why. When a command that continuously draws a progress bar, such as an HF model download, runs, the app screen becomes unresponsive, making it impossible to select other repositories. Codex sends fragments of command output (`item/commandExecution/outputDelta`) every time they are written, and the server passes them as individual events. Among the recorded turns, there are several with 10,000 to 30,000 steps, most of which are fragments like `"\r\n"` and progress bars. The screen copied the entire step array and redrew all steps for every event. If a progress bar continues for tens of minutes, the main thread becomes tied up in re-rendering.

Source. PR #256 · `fix/command-output-flood`
