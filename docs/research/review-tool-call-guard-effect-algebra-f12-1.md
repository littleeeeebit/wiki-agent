# Review research — F12, cycle 1

Draft research before the fix of review round 3 of PR #272, written 2026-11-02. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

PowerShell keeps one current location per runspace, and every construct that runs statements in the same process shares it: groups and script blocks (`& {}`, `. {}`, `(…)`), subexpressions (`$()`, `@()`, including inside expandable strings, here-strings and redirection targets) and every stage of a pipeline. The invariant: each relative path is judged from the location PowerShell actually has when that path is resolved. That includes the rest of the same command, whose arguments are evaluated before the cmdlet resolves its path, and every statement after it. The guard breaks this. It tracks the location only in `reduce`'s top-level statement loop (tool/guard.py:512-549). The nested evaluators `reduce` (groups, guard.py:608) and `code()` (Word.subs, guard.py:563) return only an effect set, so a location change inside them is lost and a later write outside the roots is placed inside the checkout.

Trigger: repeat, rounds 2, 3.

- [P1] [P1] tool/guard.py:563 — PowerShell subexpressions discard location changes

## Failed fixes

- Round 2 (fix in 1f77357): added a text check at tool/guard.py:615-617 that sets `group_moved` when a group token list (`it[2]`) directly contains a LOCATION_CMDS word, and returns {None} as the next location. It did not hold because it detects one kind of container by looking at its text, instead of having the nested evaluation report the state it leaves behind. A `Set-Location` inside `$()`/`@()` lives in `Word.subs` and never appears as a group token, so `Write-Output $(Set-Location ../outside); Set-Content target.txt x` passes (the round-3 finding). For the same reason (state dropped outside the one checked shape), the check also does not cover: (a) a pipeline stage, because reduce ignores `moved` whenever the statement is piped or piped into (guard.py:539), a bash subshell rule applied to PowerShell, so `Set-Location ../outside | Out-Null; Set-Content target.txt x` and `Get-Item ../outside | Set-Location; …` should pass, the latter also because guard_windows.py:142-144 returns ctx.cwds when Set-Location has no path argument although -Path accepts pipeline input; (b) the command that holds the group: `unknown` is used only as the location for the next statement, while this command's own run_argv still uses the old ctx.cwds (guard.py:651-663), so `Set-Content target.txt (Set-Location ../outside)` likely passes. The regression test `F12-in-process-group-cd` (tool/test_guard.py:417) exercises only the one shape the check matches.

## Sources

### S1 — Set-Location (Microsoft.PowerShell.Management) - PowerShell | Microsoft Learn

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/set-location
- Retrieved: 2026-11-02
- Locator: Notes; -Path parameter properties; Inputs
- Fragment: PowerShell supports multiple runspaces per process. Each runspace has its own current directory. This is not the same as [System.Environment]::CurrentDirectory. ... -Path ... Position: 0 ... Value from pipeline: True ... Value from pipeline by property name: True. ... -LiteralPath ... Aliases: PSPath, LP ... Value from pipeline: False ... Value from pipeline by property name: True. ... You can pipe a string that contains a path, but not a literal path, to this cmdlet.
- Applicability: Location belongs to the runspace, so any in-process construct can change it for what follows. Set-Location can take its target from the pipeline, so `… | Set-Location` with no argument still moves (guard_windows.py:142-144 treats it as no move).
- Counterevidence: It describes runspaces, not scopes. It does not say outright that a change inside `& {}` persists.
- Validation: The pwsh experiment shows Get-Location changed after `& { Set-Location .. }`, after `$(Set-Location ..)` and after `Get-Item .. | Set-Location`.

### S2 — about_Scopes - PowerShell | Microsoft Learn

- URL: https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_scopes
- Retrieved: 2026-11-02
- Locator: Long description; Scope rules
- Fragment: PowerShell protects access to variables, aliases, functions, and PowerShell drives (PSDrives) by limiting where they can be read and changed. ... When you start PowerShell, the host (pwsh.exe) creates a PowerShell runspace. Host processes can have multiple runspaces. Each runspace has its own session state and scope containers.
- Applicability: The current location is not among the items scopes isolate, so the child scope that `&` creates does not undo a location change for the caller.
- Counterevidence: PSDrives are scoped, so a location on a drive created inside the child scope may differ. The page never mentions location at all, so this is an argument from omission.
- Validation: Same experiment: `& { Set-Location .. }` leaves the outer location changed.

### S3 — about_Operators - PowerShell | Microsoft Learn

- URL: https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_operators
- Retrieved: 2026-11-02
- Locator: Subexpression operator $( ); Array subexpression operator @( ); Call operator &; Pipeline operator |
- Fragment: Subexpression operator $( ): Returns the result of one or more statements. ... Use this when you want to use an expression within another expression. ... Array subexpression operator @( ): Returns the result of one or more statements as an array. ... Call operator &: Runs a command, script, or scriptblock. ... The call operator executes in a child scope. ... Pipeline operator |: Sends ("pipes") the output of the command that precedes it to the command that follows it.
- Applicability: `$()`/`@()` run full statements inline, which is the Word.subs path the round-2 check misses. `&` creates only a child scope, not a new runspace or process.
- Counterevidence: It does not state the evaluation order of arguments relative to the cmdlet's path resolution, and does not describe pipeline stages' effect on location.
- Validation: `Write-Output $(Set-Location ..); (Get-Location).Path` shows the parent directory.

### S4 — Pester 4.1.1 In.ps1 - PowerShell Gallery

- URL: https://www.powershellgallery.com/packages/Pester/4.1.1/Content/Functions%5CIn.ps1
- Retrieved: 2026-11-02
- Locator: In function description (seen as a search-result summary, not fetched)
- Fragment: once the script block has been executed, the location will be reset to the location the script was in prior to calling In
- Applicability: A widely used module restores the location by hand after running a script block, which implies a script block's Set-Location otherwise persists for the caller.
- Counterevidence: Indirect: a workaround, not a specification. Seen only through a search summary.
- Validation: Same experiment as S2.

### S5 — tool/guard.py (reduce, code, words_effects, simple)

- URL: tool/guard.py
- Retrieved: 2026-11-02
- Locator: lines 512-556, 559-569, 599-663
- Fragment: def reduce(toks, ctx, src='') -> set ... if moved is not None and not piped_in and nxt not in ('|', '|&'): pending = moved ... def code(...) -> set: return reduce(lex(text, d), ctx.deeper(d, fresh, **kw), text) ... for d, text in w.subs: out |= code(text, d, ctx, fresh=False) ... if ctx.d == 'ps' and any(c[0] == 'w' and not c[1].why and c[1].text.lower() in LOCATION_CMDS for c in it[2]): group_moved = True ... unknown = frozenset({None}) if group_moved else None ... more, moved = run_argv(words, ctx); return eff | more, unknown if moved is None else moved
- Applicability: Shows the effects-only return type of the nested evaluators, the text check limited to group tokens, the bash-style pipeline drop applied to PowerShell, and that the command's own run_argv uses the old location even when the check fires.
- Counterevidence: Read only, not executed in this session.
- Validation: New guard tests for the subexpression, pipeline and same-command forms fail before the fix and pass after it.

### S6 — tool/guard_windows.py ps_cmdlet set-location

- URL: tool/guard_windows.py
- Retrieved: 2026-11-02
- Locator: lines 142-146
- Fragment: if canon in ('set-location', 'push-location'): w = one(*PATH) or (pos[0] if pos else None); return set(), moved_to(w, ctx) if w is not None else ctx.cwds
- Applicability: Set-Location with no path argument (pipeline input) is treated as no move.
- Counterevidence: Without piped input, `Set-Location` with no path goes to home, so ctx.cwds is wrong in that case too, but it fails closed only if home is outside the roots.
- Validation: `Get-Item ../outside | Set-Location; Set-Content target.txt x` denied after the fix.

## Claims

- C1 (source; S1, S2): The current location is per-runspace state, not per-scope state.
- C2 (interpretation; S2, S3, S4): A Set-Location inside `& { }`, `. { }` or `$()`/`@()` changes the location seen by later statements in the same command line, because these run in the same runspace and location is not among the scoped items.
- C3 (source; S3): `$()` and `@()` run full statements inline inside another expression.
- C4 (interpretation; S1, S6): Set-Location takes -Path from the pipeline, and the guard treats a Set-Location with no path argument as no move, so `… | Set-Location` escapes location tracking.
- C5 (source; S5): The guard's nested evaluators (`reduce` for groups, `code` for Word.subs) return only effects, so any location change inside them is lost unless found by a separate text check, and the round-2 check looks only at group tokens.
- C6 (interpretation; S5, S1): For PowerShell, `reduce` ignores a location change from any pipeline stage, a bash subshell rule that does not match PowerShell's single runspace.
- C7 (interpretation; S5): Even when the round-2 check fires, the command that holds the group still resolves its own paths from the old location, so `Set-Content target.txt (Set-Location ../outside)` is likely misjudged.

## Core theory

Microsoft's Set-Location documentation says the working location belongs to the runspace: 'PowerShell supports multiple runspaces per process. Each runspace has its own current directory.' about_Scopes lists what scopes isolate: 'variables, aliases, functions, and PowerShell drives (PSDrives)'. The current location is not in that list, so the child scope created by the call operator ('The call operator executes in a child scope') does not undo a location change. Subexpressions `$()`/`@()` 'return the result of one or more statements' inline, in the same runspace. In static-analysis terms, a sound transfer function has to map input state to output state for every construct that runs in the same environment. Dropping the output state of a sub-evaluation loses a mutation the later code sees.

## Applicability

All in-process nested evaluation in PowerShell goes through two places: `simple()`'s group branch (tool/guard.py:607-617, calling `reduce`) and `words_effects()` (guard.py:559-569, calling `code(..., fresh=False)` for every Word.subs, used for argument words and redirection targets). `dollar()` (guard.py:160-165) stores `$(...)`/`@(...)` as subs, `dquote`/`scan` do the same for strings and here-strings, and `lex` turns `(`, `{`, `@(` into groups through `commands()`. Pipeline handling is in `reduce` at guard.py:539. Set-Location without a path is in guard_windows.py:142-144. The fresh=True callers (guard_models.py:402,408,418,460; guard.py:686) start a new process or are POSIX and must keep dropping location. POSIX `eval`/`source`/`.` are already top (guard_models.py:797), and POSIX `$()`/`(…)`/pipeline stages are subshells, so this fix applies only to `ctx.d == 'ps'`.

## Hypothesis

The evaluator's interface is wrong for PowerShell. Location is state that any in-process sub-evaluation can change, but `reduce`/`code` return only effects. Any fix that rediscovers the change by looking at the syntax of one container (group tokens, then perhaps `$()` text, then pipelines) will keep missing the next container. The fix that holds makes in-process evaluation return its resulting location: (1) `reduce` and `code(fresh=False)` return `(effects, cwds_out)`; real process boundaries (`code(fresh=True)`: pwsh -c, bash -c, env commands) drop it. (2) For PowerShell, `simple()` carries the location through each nested evaluation (groups, then each word's subs, then redirection targets) in source order, judges the command's own paths from the resulting location, and returns it as `moved` when the command itself does not move. Marking it unknown ({None}) whenever anything changed is enough and keeps it fail-closed. (3) For PowerShell, `reduce` stops ignoring `moved` for pipeline stages, and `Set-Location`/`Push-Location` with piped input and no path return {None}. (4) Remove the text check at guard.py:615-617.

## Counterevidence

- No fetched source says outright that a Set-Location inside `& { }` or `$()` persists after it. This rests on 'each runspace has its own current directory' together with location being absent from about_Scopes' list of scoped items. Pester's `In` helper restoring the location by hand is indirect support.
- about_Scopes lists PSDrives as scoped. A location on a PSDrive created inside the child scope could behave differently. That is an edge case, not the filesystem case here.
- The same-command ordering (arguments, including `(…)` and `$()`, are evaluated before the cmdlet resolves its relative path) is my reading of how parameter binding works, not tested. If it is wrong, step (2)'s same-command part is extra caution, not a soundness gap.
- The pipeline case assumes a Set-Location stage really changes the runspace location for statements after the pipeline. The docs only say stages run object by object in the same session. To be confirmed by experiment.

## Alternatives

- Extend the text check to search `Word.subs` text for location commands as well. Rejected: same failure mode. It misses pipelines, the same-command case and any container added later. It is the kind of patch that already failed once.
- Ride a marker effect such as ('moved', None) on the existing set return, check it in `simple()`, and strip it in `code(fresh=True)` and `derive()`. Smaller diff, no signature change. Kept as the fallback: a missed strip leaks an unknown effect kind into `collapse`/judging, or a nested shell's location into the caller.
- Treat every PowerShell command that holds any group or subexpression as moving to an unknown location. Simple and sound, but it would deny common forms like `"$(Get-Date)"; Set-Content f x`, which the read-only allow tests (test_guard.py:406-410) suggest the project wants kept.

## Experiment

In pwsh, from a scratch directory with a sibling directory: run `& { Set-Location .. }; (Get-Location).Path`, `Write-Output $(Set-Location ..); (Get-Location).Path`, `Set-Location .. | Out-Null; (Get-Location).Path`, `Get-Item .. | Set-Location; (Get-Location).Path`, and `Set-Content t.txt (Set-Location ..)` followed by checking which t.txt exists. Each location that changed confirms the hypothesis for that construct. Then add each as an OUTSIDE_FORMS-style guard test that must be denied, with `"$(Get-Date)"; Set-Content target.txt x` staying allowed, and `pwsh -c 'Set-Location ..'; Set-Content target.txt x` plus bash `$(cd ..); echo x > target.txt` and `cd .. | cat; echo x > target.txt` still judged at the original location. Run only `python -m pytest -q tool/test_guard.py` and `python -m ruff check tool laya`.

## Result

pending — the fix records what it observed here, with the tests and the commit.

## Unresolved limits

Nothing was run in this session (no shell), so every 'passes today' claim is from reading the code. That includes the pipeline and same-command forms, which the reviews did not raise. Statement-level control flow inside PowerShell (`if`/`try`/`foreach` bodies, functions that run Set-Location only when called) was not traced beyond confirming that groups are flat token slices (`closing()`/`commands()`), so a nested `Set-Location` inside a group is currently seen. Other location changes that bypass Set-Location (`$ExecutionContext.SessionState.Path.SetLocation`) go through `member_effect` and are top unless allow-listed. That was checked only against SAFE_MEMBERS in passing.
