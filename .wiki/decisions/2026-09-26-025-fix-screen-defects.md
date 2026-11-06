---
scope: project
severity: contract
triggers: ["화면", "vision", "프레임", "스크린", "캡처", "공유"]
domain: vision
title: "fix: screen defect five — [connection] position, effort of work model, new work, context clearing and memory"
pr: 25
merged: 2026-09-26
branch: "fix/screen-defects"
---

# fix: Screen defects five — [Connect] position, effort of task model, new task, clearing context and memory

What. Five defects found by the user during inspection. [Connect] is not visible → If the current project is `미연결`·`일부` under the project selection in the rail, a line "wiki disconnected · [Connect]". "All projects" as "Project · Connect". [Project list ▸] in the connection section of the settings modal. When the connection changes, the rail also reloads. No effort when implementing → The model/inference intensity of the agent tab stands even when there is no task tree ("task model"). The [Start] of the specification runs on this. …

Why. Write the raw utterance first — there is nothing to lose even if the summary fails. Failure is notified as `fault` and the utterance is cleared. The purpose of memory is to ensure the next session does not ask again and does not overturn what has been decided. Therefore, `decisions` is only what the human agreed to, suggestions without answers are `open`, `why` is only the evidence given by the utterance (no guessing), `.wiki/memory/` is like `.wiki/*` outside of git (user selection — exclusive to this machine). Therefore, it was added separately to the search list (`git ls-files`). The memory body is in English — it is text read by the agent. Korean names remain as they are. `agent.oneshot` is a generalization of `explain`. The new session shape is the same.

Source. PR #25 · `fix/screen-defects`
