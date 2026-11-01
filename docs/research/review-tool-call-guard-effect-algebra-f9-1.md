# Review research — F9, cycle 1

Draft research before the fix of review round 2 of PR #272, written 2026-11-02. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

A block declared in the shared wiki (Bash(git reset --hard*), Bash(git clean -fdx*)) must hold for every shell call the PreToolUse chain evaluates, whatever the host's payload shape, at the top level and inside nested shells. The effect guard allows `git reset --hard` inside the checkout, so the declared deny in tool/deny.py is the only thing that stops it. A Codex exec_command payload {cmd: "bash -c 'git reset --hard'"} reaches deny.verdict as an empty command and passes.

Trigger: repeat, rounds 1, 2.

- [P1] [P1] tool/deny.py:18 — Native Codex command payloads skip the repaired nested declarations

## Failed fixes

- The F10 commit added guard.nested_bash_commands so deny.py also checks nested shell strings. The sink recorded whole nested scripts, so `bash -c 'echo ready; git reset --hard'` passed. That was the round 1 finding.
- c9770fe changed guard.reduce (tool/guard.py:500-505) to send each simple command to the sink, and added the compound regression at tool/test_guard.py:331. This holds for Bash/PowerShell payloads keyed `command`. It never touched how deny.verdict pulls the command out of the payload (tool/deny.py:16-25). That code reads only tool_input.command, does not strip the `functions.` prefix, always uses the posix dialect for non-PowerShell tools, and turns argv lists into a str that nested_bash_commands rejects. Every regression sends {"command": ...}, so the round 2 finding (exec_command/cmd) went untested.

## Sources

### S1 — CWE-180: Incorrect Behavior Order: Validate Before Canonicalize

- URL: https://cwe.mitre.org/data/definitions/180.html
- Retrieved: 2026-11-02
- Locator: Description; Potential Mitigations (Input Validation)
- Fragment: "The product validates input before it is canonicalized." Mitigation: "Inputs should be decoded and canonicalized to the application's current internal representation before being validated"; "Make sure that the application does not decode the same input twice"; "Such errors could be used to bypass allowlist validation schemes by introducing dangerous inputs after they have been checked."
- Applicability: deny.py validates tool_input.command before normalizing the payload the way guard.py does (cmd fallback, prefix, dialect, argv), so the check runs on a non-canonical projection.
- Counterevidence: CWE-180 is about string encodings. Applying it to payload-field normalization is an analogy, not its stated scope.
- Validation: After the fix, deny.py and guard.py get their (tool, command, dialect) from one function, and the parametrized payload test passes for every shape.

### S2 — SEI CERT Oracle Coding Standard for Java, IDS01-J: Normalize strings before validating them

- URL: https://wiki.sei.cmu.edu/confluence/x/IkRGBQ
- Retrieved: 2026-11-02
- Locator: rule text, as quoted in the web search result (page not fetched)
- Fragment: "When implemented, this form of validation must be performed only after normalizing the input." The noncompliant example validates with a blacklist regex before NFKC normalization; the compliant one normalizes first.
- Applicability: Same ordering rule: the declared-deny blacklist has to run after the payload is normalized to the guard's canonical command.
- Counterevidence: It is about Unicode normalization in Java; the payload case is an analogy.
- Validation: Same experiment as S1.

### S3 — safe-chains src/targets/codex.rs

- URL: https://docs.rs/crate/safe-chains/latest/source/src/targets/codex.rs
- Retrieved: 2026-11-02
- Locator: struct ToolInput; CodexHookFormat::parse_input
- Fragment: ToolInput has the single field `command: String`; parse_input uses `command: tool_input.command`; for PreToolUse, `if let Some(name) = &envelope.tool_name && name != "Bash"` fails parsing (tool_name is optional).
- Applicability: Evidence that current Codex PreToolUse hooks may send only Bash/command, which narrows how often exec_command/cmd actually reaches the hook.
- Counterevidence: It is a third-party parser that may simply reject shapes it doesn't support; the repo's own guard and host_boundary accept cmd.
- Validation: Log a real Codex PreToolUse stdin in this repo's runtime to see which shape arrives.

### S4 — Codex CLI Hooks: Complete Guide to Events, Policy Engines and Production Patterns

- URL: https://codex.danielvaughan.com/2026/04/15/codex-cli-hooks-complete-guide-events-policy-patterns/
- Retrieved: 2026-11-02
- Locator: PreToolUse payload example (from the search result summary; page not fetched)
- Fragment: Search summary: the PreToolUse example puts the shell command string under tool_input.command, and the guide says the tool name is always "Bash" in Codex CLI. A separate clash test maps a Codex tool named "shell" to "Bash", which suggests raw names may vary.
- Applicability: Counterevidence on how often the exec_command/cmd shape occurs.
- Counterevidence: It is a third-party blog post, and the clash mapping shows raw tool names do vary.
- Validation: Same as S3.

### S5 — tool/deny.py

- URL: tool/deny.py
- Retrieved: 2026-11-02
- Locator: lines 16-25
- Fragment: given = payload.get("tool_input") or {}; tool = payload.get("tool_name"); command = str(given.get("command") or "").strip(); inner = guard.nested_bash_commands(command, "ps" if tool == "PowerShell" else "posix") if tool in guard.SHELL_TOOLS else []
- Applicability: The divergent reader: command only, no prefix strip, no host, argv lists turned into str.
- Counterevidence: none given
- Validation: After the fix it calls the shared normalizer.

### S6 — tool/guard.py

- URL: tool/guard.py
- Retrieved: 2026-11-02
- Locator: lines 500-505, 754-756, 771-772, 784-786, 814
- Fragment: sink appends each simple command's words; derive: `if isinstance(command, list): return {READ} | run_argv(...)`; nested_bash_commands: `if not isinstance(command, str): return []`; call_effects: `command = given.get("command", given.get("cmd", ""))`, `windows_codex = host == "codex" and ctx.windows`; verdict: `.removeprefix("functions.")`
- Applicability: The canonical form the effect guard evaluates, and the round 1 fix that held only for the command field.
- Counterevidence: none given
- Validation: The shared normalizer is extracted from these lines.

### S7 — tool/codex_pretool.py and tool/host_boundary.py

- URL: tool/codex_pretool.py
- Retrieved: 2026-11-02
- Locator: codex_pretool.py:18; host_boundary.py:29-33
- Fragment: denied = host_boundary.verdict(payload) or deny.verdict(payload) or guard.verdict(payload, host="codex"); host_boundary: command = str(given.get("command") or given.get("cmd") or given.get("code") or "")
- Applicability: Shows three readers of one payload in one chain, with deny getting no host.
- Counterevidence: none given
- Validation: The experiment runs through this chain.

### S8 — tool/test_guard.py

- URL: tool/test_guard.py
- Retrieved: 2026-11-02
- Locator: test_declared_git_blocks_hold_inside_nested_shells, lines 312-334
- Fragment: answer = deny.verdict({"tool_name": tool, "tool_input": {"command": command}}) — tool is only Bash or PowerShell
- Applicability: The regression pins one payload shape, so the round 2 divergence went untested.
- Counterevidence: none given
- Validation: Replaced or extended by the parametrized payload-shape test.

## Claims

- C1 (source; S5, S6, S7): deny.py, guard.py and host_boundary.py each extract the shell command from the same Codex payload with different rules, and deny.py reads only tool_input.command.
- C2 (source; S1, S2): A deny check has to run on the canonical form of the input that will be acted on; checking a non-canonical projection lets dangerous input through.
- C3 (interpretation; S5, S6, S8): The round 1 fix held for the parse depth it changed but did not touch payload normalization, and its regression only used the command field. That is why the same invariant broke again through cmd. The prefix, dialect and argv-list gaps are likely the next repeats.
- C4 (interpretation; S3, S4): Current Codex releases may send only Bash/command, so the exec_command/cmd bypass may be rare in practice, but the guard already accepts that shape and the deny has to match it.

## Core theory

Validate-before-canonicalize (CWE-180): checks have to run on input that is already decoded and canonicalized to the application's internal representation, or dangerous input can bypass them. CERT IDS01-J: validation must happen only after normalizing. Here the canonical form is the (tool, command, dialect) that the effect guard evaluates and the host executes. The declared deny validates a different, unnormalized projection.

## Applicability

tool/codex_pretool.py:18 chains host_boundary.verdict, deny.verdict and guard.verdict(host="codex") on the same payload. guard.py normalizes: tool strips `functions.` (814), command = command or cmd (784), dialect ps for Codex on Windows (785-786), list commands parsed as argv by run_argv (754-755). deny.py does none of this (16-25), and codex_pretool does not pass it host. host_boundary.py:33 reads command|cmd|code but does not strip the prefix. The fix belongs in one shared normalizer in guard.py that deny.py calls, plus argv support in nested_bash_commands through run_argv with the sink, so every caller goes through one place.

## Hypothesis

The underlying cause is three separate readers of one payload, not the nested parse. host_boundary.py, deny.py and guard.py each extract tool name, command field, dialect and argv form on their own, and they disagree. A declaration checked in deny.py therefore sees a different string, or none, from what guard.py evaluates and the host runs. Each fix aligned one projection (parse depth) while another (payload field) stayed divergent. The `functions.` prefix, the Codex-on-Windows dialect and the argv-list `shell` form are further divergences of the same kind, not yet raised.

## Counterevidence

- Public third-party sources (safe-chains codex.rs; Daniel Vaughan's Codex hooks guide) describe Codex PreToolUse payloads as tool_name "Bash" with tool_input.command only. The exec_command/cmd shape may come from older Codex versions or another call path, so the bypass may be narrower in practice than the review implies.
- The repository itself treats exec_command/cmd as real: guard.py:784, host_boundary.py:33 and test_host_boundary.py:23,37 all handle it, and markdown_emphasis.py:20 records Codex sending `functions.`-prefixed names. The invariant has to hold for the shapes the guard already accepts, whatever the current Codex release sends.

## Alternatives

- One-line fix: read `given.get("command") or given.get("cmd")` in deny.py:18. It fixes the named case but leaves the prefix, dialect and argv-list divergences open, which is the same pattern that produced this repeat. Not first.
- Move declared-deny matching into guard.verdict. That would put it behind the WIKI_AGENT_MANAGED gate (guard.py:812) and behind grant() returning None, and deny.py has to work for unmanaged user-level installs (apply.py:476, 636). Rejected.
- Have the effect guard treat `git reset --hard` as an effect outside the grant. That duplicates the declared rule in code and doesn't make other declarations hold under Codex. Rejected.

## Experiment

Run one parametrized test through codex_pretool.verdict with WIKI_AGENT_MANAGED unset (so only deny.py can block) and apply.declared monkeypatched to the two Git blocks. Payloads: Bash/{command}, exec_command/{cmd}, functions.exec_command/{cmd}, shell_command/{command}, shell/{command: ["bash","-lc","git reset --hard"]}, local_shell/{command: ["git","reset","--hard"]}, each also with the inner command `echo ready; git reset --hard`. Assert the reason contains the declared-rule text `공유 위키의 차단 규칙`, and that the same shapes carrying `git status` return None. Before the fix, every non-`command` row should fail; after it, all should pass. A second assertion: for each payload, the shared normalizer gives deny.py the same command string that guard.call_effects evaluates.

## Result

The hypothesis held: the cause was three readers of one payload disagreeing, not the nested parse.
Against the pre-fix tree (`c9770fe`), `deny.verdict` read only `tool_input.command` with a fixed
posix dialect and no `functions.` stripping or argv-list handling, so a Codex `exec_command` payload
`{cmd: "bash -c 'git reset --hard'"}` reached it as an empty command and passed the declared block,
while `guard.call_effects` (reading `cmd`) permitted the checkout write.

The fix adds one shared normalizer, `guard.shell_command(payload, host)`, returning
`(tool, command, dialect)` read the one way — `command` or `cmd`, a string or an argv list, a
`functions.`-stripped tool name, PowerShell on a Codex-on-Windows shell. Both `deny.verdict` and
`guard.call_effects` now take the command from it (validate-after-canonicalize, CWE-180), and
`guard.nested_bash_commands` accepts an argv list. The declared `Bash(...)` patterns are fnmatched
against the top-level command for every shell tool (not only `Bash`) and against each nested command;
the direct tool-name deny still matches the raw (unstripped) tool name so its reason names the tool
as written.

Tests (in `tool/test_guard.py`): `test_declared_blocks_hold_across_every_payload_shape` drives
`deny.verdict` with the declared git blocks monkeypatched and six payload shapes — `Bash/command`,
`exec_command/cmd` wrapping `bash -c`, `functions.exec_command/cmd`, `shell_command/command` with a
compound `echo ready; git reset --hard`, `shell/command` as the argv list `["bash","-lc","git reset
--hard"]`, and `local_shell/command` as `["git","reset","--hard"]` — asserting each is blocked with
the shared-wiki reason and that the same shapes carrying `git status` are not;
`test_declared_git_blocks_hold_inside_nested_shells` (F10) still passes unchanged. The existing
`test_codex_hooks` suite (which exercises the raw-tool-name deny for `request_user_input_async` and
`functions.request_user_input_async`) also passes.

## Unresolved limits

I did not confirm which payload shapes the current Codex release sends; the public sources disagree with the repo. On Windows Codex, I don't know whether parsing nested text with the ps dialect instead of posix changes which inner commands the sink records. That needs the experiment to run on Windows. The `functions.` prefix, dialect and argv-list gaps were found by reading the code, not by running payloads.
