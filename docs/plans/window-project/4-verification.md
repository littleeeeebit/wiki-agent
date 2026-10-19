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

## Evidence

A short record in this plan's step table: the app's commit, the two
repositories, the network log excerpt showing no 409, and the record paths
checked. A failing step stops here and goes back to the stage that owns it.
