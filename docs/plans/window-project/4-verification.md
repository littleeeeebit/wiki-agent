# Stage 4 — two windows, two repositories, one live run

Purpose. Show on the real app that two repositories can be worked at once and
nothing crosses between them.

## Scenario

On the built desktop app, with two attached repositories (wiki-agent and
rfg_rag):

1. Window A on wiki-agent, window B (opened with the new window command) on
   rfg_rag.
2. In A, start a task and let its turn run. In B, start a task and let its
   turn run at the same time.
3. In B, start a review loop on an open pull request.
4. While both run, pick a third repository in A and come back.
5. Ask the wiki focus in both windows at the same time.

## What must hold

- No 409 in either window's network log, and no `project-moved` event.
- Each window's task, loop and conversation records carry its own repository
  (`raw/chat/<cid>.jsonl` `repo`, `raw/specs/<repo>/`).
- The turn in A and the loop in B finish; neither session was closed by the
  other window's switch.
- The restart question, asked from either window, names the work of both.
  Asked from B while A has only a wiki answer being generated, it still
  warns, and cancelling leaves A's answer running.
- With a terminal open in each window, closing A ends A's shell; B's shell,
  B's loop and A's server-side work go on.

## Evidence

A short record in this plan's step table: the app's commit, the two
repositories, the network log excerpt showing no 409, and the record paths
checked. A failing step stops here and goes back to the stage that owns it.

## Run (2026-11-04)

The release app built from `335f8ff` (#291), started with WebView2's remote
debugging port and driven through Playwright over CDP, so no OS input was
used. A wrapper on each page's `fetch` recorded every request's status and
`X-Project`, and a listener recorded `project-moved`.

Changes from the scenario, each the person's call:

- Window B ran on `wiki-agent-refactor-scratch`, not rfg_rag. rfg_rag offered
  only its in-progress `wiki-bootstrap` task, whose conversation the check
  would have written into. B's turn ran in a local worktree made for it
  (`stage4-probe`) and removed after.
- No review loop was started. Two live loops of other projects
  (ai-companion-voice-agent and ai-generation) were restarted with the app and
  ran through the whole check: R5 to R3 of the next cycle, and R7 to R9.
- The restart question was checked through the last window's close, which
  calls the same `leave`. `window.confirm` was replaced to record the question
  and answer cancel, so the native dialog itself was not seen.
- The person closed the first window by hand early on. Window B and the
  server went on, and a new window was opened from B as A.

| Step | What held |
| --- | --- |
| Open | B, opened by 새 창, started on the last pick (wiki-agent). B moved to rfg_rag and later to the scratch project; A stayed on wiki-agent throughout. Both windows were `main-2` and `main-3`, not `main`. |
| Terminals | A terminal in each window opened in its worktree, so the capability's `main-*` pattern lets their IPC through. Two shells under the app. |
| Turns | A turn in each window ran at the same time. `/api/loops` named them `wiki-agent` and `wiki-agent-refactor-scratch`. A's answered `fix/repo-graph-canonical-name`. A switched to ai-coding-wiki and back while they ran; neither was closed. |
| Wiki | A question in both windows at once. `/api/loops` counted 2 answers being written. `raw/chat/wiki.jsonl` holds each window's rows (`retrieval`, `draft`, `assistant`) under its own repository, and each answer describes that repository. |
| Close B | Asked nothing and closed. B's shell ended (two shells to one); A's shell still answered and both loops went on. |
| Close A, last | Asked `도는 작업 1개, 리뷰 루프 2개, 만드는 답 1개가 멈춘다. …` while A's wiki answer was being written. Cancelled: the window stayed and the answer finished. |
| Network | No 409 and no `project-moved` in either window's log over the whole run (about 2,000 requests). |

Found on the way, outside this plan's change:

- The rail's other-project rows open the worktree, but an instruction sent
  there is refused with 404: `/api/work/say` checks the path against the
  window's project (`work.ours`), and the screen sends no owner for it. The
  same was true with one selection for the server. A follow-up can send the
  row's project as the owner, as review loops and merges already do.
