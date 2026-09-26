# Step 7 — Verification

The relationship between the overall design and the steps is in [Overview](0-overview.md)].

Goal. Run everything built up to step 6 from start to finish once, to see if the process of one person asking the wiki in one window and using the answer to assign tasks to the agent in the worktree works both with translation turned off and on. Fix anything that gets stuck while running in this step.

## How it was run

It was run in an actual Tauri window, not a browser, because the terminal is only in the window. I launched it with `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<포트>`, attached with Playwright's `connect_over_cdp`, clicked, read, and took screenshots. The model for both the query and the agent is haiku.

One process is as follows:

1. Set the translation switch
2. Create a new worktree
3. Ask the wiki
4. Open the `file:line` citation of the answer
5. Create a draft with "→ Task" and send it to the agent with "To-do" written
6. Approve in the write approval card
7. Check if the file was created only in the worktree
8. Read the result in the terminal of the same worktree
9. Clean up the worktree

Translation requests counted `/api/translate` calls wrapping the page's `fetch`, and costs read `translate.usage()` before and after.

## Results

| Check | Result |
| --- | --- |
| `pytest tool/` | 298 passed. Two new tests at the end of step 6 |
| `python tool/lint.py --check` | Exit 0 |
| `ruff check tool/` | Passed |
| `npm run build` | Passed (including `tsc -b`) |
| `cargo build --release` | Passed |
| Process with translation off | When the switch was turned off, `raw/chat/main.json` was `false`. Two queries, draft, approval, and agent answer resulted in 0 `/api/translate`, monthly usage remained the same ($0.050876). The agent's answer is in the original English. There is no file before approval, and it exists only in the worktree after approval. The terminal opens in PowerShell in that worktree and `git status`/Korean output is exchanged |
| When relaunching the window | It returns with the switch turned off |
| Process with translation on | Same sequence. The agent's English answer appears in Korean, and the monthly usage increased from $0.051265 → $0.051502. There is no `notes/` in the original checkout |
| Closing the window | The server (`python tool/main`) also went down every time |

## Things that got stuck while running

I fixed four things. Three had tests, and each was red in the code before fixing. The width of the citation drawer was measured in the window.

| What | Cause | Fixed |
| --- | --- | --- |
| The draft lacked an "evidence" section and could not click the answer's citation | The prompt says to write citations as inline code, but haiku wrote them like `craft/destructive-git-guards.md:16 — …` without backticks. Both places only looked inside backticks | Citations with line numbers are also counted as citations. The draft is `query.CITE`, and the screen only changes the top citation to inline code in the text node parsed by the remark plugin of `Answer.tsx` — link destinations, link text, and code blocks are not text nodes. `test_a_draft_carries_the_grounds…` |
| When opening the citation drawer, the query side was crushed to 76px, making the text stand vertically | The drawer was `w-[34rem]` at `shrink-0`. In a 1480px window, the query side is 620px | Set drawer width to `min(34rem, 55%)`. In the same window, conversation 279px, drawer 340px |
| When translation was turned on, the Korean answer changed to English | `worth_translating` sent an Eng→Kor request even if there was only one Latin character. Gemini, having received a Korean answer mixed with paths and names, returned it in English, which remained in the cache | Only send for Eng→Kor when Latin words outnumber Korean words in the prose remaining after removing inline code, file/module names (tokens with dots), backslash paths, and underscore identifiers. Incorrectly entered cache entries are no longer searched. `test_korean_with_a_few_identifiers_is_not_sent_to_korean` |
| When cleaning up the selected worktree, `Permission denied`, the branch remained | The terminal shell of that worktree was standing inside the folder. Windows cannot delete a folder where a process is standing. git had already forgotten the worktree, so it disappeared from the list, and there was no way on the screen to delete the branch. It cannot be recreated with the same task name | The screen waits for all terminal closures started in that folder before deleting the worktree — `pty_close` only answers after the shell ends, and also closes shells that are opening. `workspace.remove` does not stop with failure if git has forgotten the worktree, but finishes processing the branch and notifies of the remaining folder. `test_a_folder_held_by_a_shell_still_loses_its_branch`. I saw the folder, branch, and list all disappear by cleaning up while the terminal was open in the window and while the shell was opening |

## Things noted without fixing

| What | Why keep it |
| --- | --- |
| Query answers come in Korean instead of English | `chat-answer.md` says to write in English, but haiku answered in Korean all four times. Since it no longer sends even when translation is on, the screen is correct. The overlay just has nothing to do. I will look separately at whether the prompt and user-level instructions conflict |
| Text inside quotation marks is also translated | The agent's `Wrote "gate ok"` appeared as `"게이트 ok"`. The translator only respects inside backticks. If it also respected quotation marks, English sentences quoted with quotation marks would not be translated |
| `wiki-agent` one word is translated as `위키-에이전트` | It could be put in the glossary, but if the glossary changes, the entire cache becomes invalid. I will do it when fixing the glossary for other reasons |
| Changes in the terminal are not immediately visible in the worktree list | The list is re-read when the window receives focus. The terminal inside the window does not move focus. There is nothing wrong except that the cleanup button appears late |

## Review

| Round | Result |
| --- | --- |
| 1 | Three P1s, all reproduced. Changed top citations from strings, also changed link destinations and `~~~`/indented code blocks — only changed in text nodes of the parsed tree. Translation judgment was Latin words vs. Korean syllables, so Korean with many paths was sent, and English with long Korean names was skipped — measured word-for-word excluding code and paths. Terminal closing just went out before deletion and did not wait for completion — it waits |
| 2 | Two P1s, all reproduced. Did not wait for the closing of shells that were closing because a different worktree was just selected or shells split by theme switching — collected closures by folder and waited for all of that folder's when deleting. Did not see English connected by slashes like `Pass/Fail` as a path and did not translate — slashes alone are not counted as paths |
| 3 | No new discoveries, merge approved |