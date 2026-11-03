# Agent commands — fewer refusals, faster hooks, one research trigger

Purpose. What a coding agent does is create, read, edit and write files, run
tests and browser scenarios, and sometimes search the web. The hooks around
those commands grew one refusal at a time while cutting cost, and the agent
spent turns retrying instead of working. This plan keeps the rules that guard
something real, turns the rest into automatic fixes, makes the hooks cheap to
run, and replaces the review loop's research branches with one automatic rule.

## What the audit found (2026-11-03)

PreToolUse denials in the last three weeks of Claude transcripts (450 files):

| Rule | Denials | Hook |
| --- | --- | --- |
| Rewriting an existing file (`> file`, script writes, whole-file `Write`) | ~630 | `tool/edit_as_diff.py` |
| pytest output piped into `tail`/`grep` | ~560 | `tool/pytest_output.py` via `deny.py` |
| `sed -i` | 185 | `enforce.deny` in `operator/edit-files-as-diffs.md` |
| Markdown emphasis | ~130 | `tool/markdown_emphasis.py` |
| `powershell` (5.1) | ~130 | `enforce.deny` in `operator/run-inside-this-session.md` |
| Effect check | ~125, almost all before #280 | `tool/guard.py` |
| English progress description | ~90 | `tool/english_progress.py` |

Hook latency, best of three, one hook at a time: 170–310 ms each, seven per
tool call. Profiling `memory_words.py` puts ~100 ms in interpreter start,
~120–300 ms in the `git rev-parse` that `hook.py` spawns to find the project
(`workspace.sessions.checkout`), and ~30–40 ms in imports and the check itself.
Interpretation is not the cost; process creation is.

## Decisions

- Scope: Claude Code and Codex both.
- Shells: general work stays where it is (Git Bash for Claude, PowerShell for
  Codex); Windows-only work (registry, services, scheduled tasks, processes)
  uses `pwsh` 7. Nothing needs Windows PowerShell 5.1.
- Handling: a rule that can repair a command repairs it and lets it run; only
  what is hard to undo is refused.
- Rewriting files: refuse only shell or script rewrites of git-tracked files.
  Logs, generated files, gitignored paths and memory pass. Whole-file `Write`
  on a tracked file stays refused.
- pytest pipes: the hook inserts `2>&1 | tee <temp log>` before the filter and
  runs it. Checked 2026-11-03: both hosts rewrite input. Claude applies
  `updatedInput` with no decision and still runs its own approval rules on the
  new command; Codex takes it only with `allow`.
- `sed -i`: allowed when one command applies the same substitution to three or
  more files; a single file goes through Edit.
- `powershell`: rewritten to `pwsh -NoProfile`.
- Emphasis and English progress: unchanged, still refused.
- Effect check: behaviour unchanged.
- Hook speed: no `git` subprocess (read `.git` the way `guard_grant.layout`
  does) and one process for every PreToolUse check. Results must not change.
- Review research: the A-A, A-B-A and round-7 branches go. One rule: the same
  P0/P1 finding id raised a fourth time in a pull request starts research with
  web tools automatically, limits 10 minutes, 40 tool calls, 200k tokens
  (adjustable in settings). A repeat after a researched fix researches once
  more; a third time stops for the person.
- Browser scenarios: Playwright scripts.
- Tools: `fd` and `jq` installed with winget at setup, and installed when found
  missing while the app is in use.
- Guide: one short wiki rule page on which command to use for each kind of
  work, so the agent starts right instead of being refused.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Hook speed, same results | `hook.py` finds the project without `git`; one PreToolUse process runs every check; replay of three weeks of recorded tool calls through old and new gives identical decisions and reasons; latency measured before and after | Done — project lookup agrees on 1,585 folders; replay 0 differ on 5,000 unmanaged and 7,932 app-managed calls; 200-call latency median 1,063 ms → 241 ms, p90 2,255 ms → 503 ms |
| 2 | Rule changes | Automatic repair for pytest pipes and `powershell`, narrowed rewrite rule, batch `sed -i`, Codex parity | Done — of 3,200 refused calls in three weeks, 881 now run repaired (810 pytest pipes, 71 `powershell`) and 475 pass (400 untracked rewrites, 75 `sed -i` on scratch or batches); every refusal left was already there behind the first; both hosts checked live; merged as #284 (968d398) |
| 3 | Research rule | `tool/main/recovery.py` reduced to the fourth-occurrence trigger with default limits | Not started |
| 4 | Tools and guide | `fd`/`jq` install at setup and on demand, Playwright as the scenario runner, the command guide page | Not started |

Step 1 ships alone so that "results unchanged" stays provable: the later steps
change results on purpose.
