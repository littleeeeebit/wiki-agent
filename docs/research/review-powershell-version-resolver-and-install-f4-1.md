# Review research — F4, cycle 1

Draft research before the fix of review round 4 of PR #307, written 2026-11-07. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F4 concerns incomplete inspection of nested PowerShell commands. The invariant is that a command explicitly targeting Windows PowerShell must be checked against that child's capabilities, independently of the parent shell's version, while quoted data remains data. The repository establishes this invariant, but its current extraction can omit executable portions of a child command. [C1, C4]

Trigger: recurrence, rounds 1, 2, 3, 4.

- [P1] [P1] tool/shell_lint.py:43 — multi-backtick CommonMark inline code is not catalogue-checked / when documentation uses a valid double-backtick span / uncatalogued PowerShell instructions pass lint

## Failed fixes

- The initial nested-command handling distinguishes quoted data from child code and checks explicit Windows PowerShell children even under a PowerShell 7 parent. The current tests preserve these cases, but they do not establish that the complete child command is reconstructed. This is a repository-based reconstruction, not a verified account of a particular review round. [C1, C4]
- The grouped-target correction recognizes quoted executable names wrapped in parentheses. Its tests vary target grouping and parent version, but each child command body is supplied as one argument. Recognition of more executable spellings does not repair incomplete argument consumption. [C2, C4]
- The encoded-command correction decodes Base64 as UTF-16LE, rejects unreadable payloads, and recursively inspects decoded commands. Its tests cover encoding variants and nesting. The ordinary quoted -Command branch still extracts only the first quoted span, leaving the separate argument-reconstruction problem intact. [C3, C4]

## Sources

### S1 — PowerShell resolver and nested-command inspection

- URL: tool/common/shell.py
- Retrieved: 2026-11-07
- Locator: nested_commands, classic_seven_only, seven_only, launch; lines 102-154 and 258-272
- Fragment: def classic_seven_only(text: str) -> bool:
    """An explicit Windows PowerShell child stays on 5.1 even under a 7 host."""
    return any(program == "powershell" and seven_only(body) or classic_seven_only(body)
               for program, body in nested_commands(text))
- Applicability: Defines child-specific capability checking and the launch boundary.
- Counterevidence: Recursive checking exists already; completeness depends on the bodies yielded by nested_commands().
- Validation: Equivalent child programs must yield equivalent capability decisions under parent versions 5 and 7.

### S2 — Grouped targets and independent child-version regressions

- URL: tool/test_shell_round_two.py
- Retrieved: 2026-11-07
- Locator: test_grouped_literal_call_targets_cannot_bypass_ps5_capabilities; test_classic_child_capabilities_are_enforced_independently_of_parent_version; test_grouped_safe_child_and_literal_shell_text_remain_allowed; lines 69-96
- Fragment: @pytest.mark.parametrize("body", ["'Get-Error'", '"Test-Json"', "Get-Error", "@'\nGet-Error\n'@"])
def test_classic_child_capabilities_are_enforced_independently_of_parent_version(ps5, monkeypatch, major, depth, body):
- Applicability: Shows the tested grouped-target correction and parent/child version distinction.
- Counterevidence: The body parameter contains one body argument; it does not cover a command assembled from multiple arguments.
- Validation: Preserve these cases and add split-argument equivalents.

### S3 — Encoded child-command regressions

- URL: tool/test_shell_round_three.py
- Retrieved: 2026-11-07
- Locator: Encoded-command tests; lines 41-92
- Fragment: command = f"{target} -{option} {quote}{body}{quote}"
assert shell.seven_only(command) == ("Get-Error",)
- Applicability: Records coverage for encoded payloads, option variants, malformed input, and recursive nesting.
- Counterevidence: Encoded payload coverage does not demonstrate complete reconstruction of ordinary -Command arguments.
- Validation: Keep encoded and malformed-payload regressions while changing ordinary invocation extraction.

### S4 — Windows PowerShell command-line interface

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe?view=powershell-5.1
- Retrieved: 2026-11-07
- Locator: -Command section, especially lines 83-94
- Fragment: "If the value of Command is a string" ... "all arguments following it are interpreted as part of the command to execute."
- Applicability: Limits the argument-tail rule to string-valued -Command; the same section separately describes ScriptBlock and standard-input forms.
- Counterevidence: The documentation sentence names pwsh despite appearing on the powershell.exe page; confirm behavior on the actual Windows PowerShell executable.
- Validation: Compare the split-argument reproducer with its single-argument equivalent on Windows PowerShell.

### S5 — PowerShell parsing and native argument transmission

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parsing?view=powershell-5.1
- Retrieved: 2026-11-07
- Locator: Argument mode; Passing arguments to native commands; lines 80-103 and 186-188
- Fragment: When running native commands from PowerShell, the arguments are first parsed by PowerShell.
- Applicability: Explains why original quote spans alone do not specify the complete child invocation.
- Counterevidence: Argument-mode exceptions and native transmission details require runtime validation of concrete examples.
- Validation: Confirm actual child behavior alongside static classification, under the supported Windows shells.

### S6 — CommandAst.GetCommandName static-resolution limits

- URL: https://learn.microsoft.com/en-us/dotnet/api/system.management.automation.language.commandast.getcommandname?view=powershellsdk-7.4.0
- Retrieved: 2026-11-07
- Locator: Definition and examples; lines 57-60
- Fragment: This command name may not be known statically, in which case null is returned.
- Applicability: Requires an unresolved state when an invocation target depends on runtime evaluation.
- Counterevidence: This limitation does not prevent reliable recognition of supported literal targets.
- Validation: Test literal and computed targets separately; never convert an unresolved target into evidence of compatibility.

### S7 — Existing argument-aware shell model

- URL: tool/guard_models.py
- Retrieved: 2026-11-07
- Locator: joined and m_pwsh; lines 460-501
- Fragment: if p.startswith("encoded"):
    return top("an encoded command"), None
if p == "command":
    rest = args[i + 1:]
    if not rest or [r.text for r in rest] == ["-"]:
        return from_stdin("ps", ctx, name), None
    return joined(rest, "ps", ctx), None
- Applicability: Provides an existing model that consumes the complete command argument tail, with explicit stdin and encoded cases.
- Counterevidence: This is an effects model, and encoded input is classified as unknown rather than decoded.
- Validation: Reuse only validated parsing behavior; preserve resolver-specific decoding and capability checks.

### S8 — Effect guard's treatment of unknown commands

- URL: tool/guard.py
- Retrieved: 2026-11-07
- Locator: Module contract; lines 13-15
- Fragment: Open by default (`guard_grant.outside`): a write or delete outside the write
roots, a protected file or a push to an unknown host is denied; `top` and
`spawn` pass.
- Applicability: Shows why the guard's allow decision cannot prove PowerShell version compatibility.
- Counterevidence: Its lexer and argument representation may still be reusable independently of the effects policy.
- Validation: Ensure unresolved compatibility input has an explicit outcome independent of permissive effect evaluation.

### S9 — PreToolUse compatibility refusal boundary

- URL: tool/command_repair.py
- Retrieved: 2026-11-07
- Locator: refusal; lines 43-64
- Fragment: if shell.classic_seven_only(command):
    raise ValueError(f"Windows PowerShell에는 7 전용 명령을 넘길 수 없다. '{shell.INSTALL_CONTROL}'에서 확인하세요.")
if shell.chosen().major >= 7:
    return None
- Applicability: Both hosts depend on classic_seven_only() before the parent-version shortcut.
- Counterevidence: Executable repair can change unquoted legacy invocations, so tests must also preserve an explicit quoted Windows PowerShell target.
- Validation: Exercise both host refusal paths with quoted targets under selected parent versions 5 and 7.

## Claims

- C1 (source; S1): The resolver recursively checks explicit Windows PowerShell child bodies independently of the selected parent version.
- C2 (interpretation; S2): The grouped-target regression file covers parent versions and safe literal data, but its child-body cases use a single argument.
- C3 (source; S3): Encoded-command regressions cover option and quoting variants, malformed payloads, and nested encoded children.
- C4 (interpretation; S1, S4, S5): The quoted-body branch extracts one quoted span and does not consume subsequent arguments; this predicts incomplete inspection of a child command assembled from several arguments.
- C5 (source; S4): The command-line documentation says that arguments following string-valued -Command form part of the executed command; ScriptBlock and stdin forms have separate conditions.
- C6 (interpretation; S5): PowerShell parses arguments before passing them to a native executable, so runtime transmission must be considered when reconstructing a nested invocation.
- C7 (source; S6): An official CommandAst can have a statically unknown command name; using an AST does not resolve every runtime-dependent target.
- C8 (interpretation; S7, S8): The existing effects model joins the -Command argument tail, but its unknown encoded-command result and permissive guard policy cannot establish version compatibility.
- C9 (source; S1, S9): The PreToolUse refusal path shares classic_seven_only() with the launch boundary and checks it before accepting a PowerShell 7 parent.

## Core theory

PowerShell first interprets arguments in the calling shell, and string-valued -Command consumes the following arguments as command text. Consequently, inspection must reconstruct the invocation before recursively classifying its program. A real AST helps distinguish syntax roles, but it does not statically resolve every computed command name. [C5, C6, C7]

## Applicability

Replace the single-span child-body extraction in tool/common/shell.py with an invocation representation containing the executable, argument sequence, command mode, reconstructed body, and an explicit unresolved state. Preserve the existing encoded-command decoder and recursive child-version checks. Use the same representation at launch() and command_repair.refusal(), so their decisions concern the command actually supplied to the child. The existing guard already joins the argument tail for -Command and is useful implementation evidence, but its permissive treatment of unknown effects cannot itself establish compatibility. Parser reuse needs focused validation rather than wholesale substitution. [C1, C4, C8, C9]

## Hypothesis

The recurring cause is a mismatch between source spans and invocation semantics. nested_commands() treats the first quoted span after -Command as the complete child program. For string-valued -Command, subsequent arguments also contribute to the executed command. A concrete candidate is powershell -Command 'Write-Output ok;' 'Get-Error': inspection follows only the benign first argument, although the child is expected to execute the following Get-Error statement too. This prediction follows from code and documentation; it has not been executed here. [C4, C5]

## Counterevidence

- The current implementation already handles important distinctions: grouped literal targets, child versions independent of parent versions, nested encoded commands, and benign quoted text. The hypothesis concerns remaining invocation reconstruction, not a complete absence of recursive inspection. [C1, C2, C3]
- The proposed multi-argument reproducer has not been run. Native argument transmission can affect the child's received command, so Windows execution must confirm the predicted behavior. [C6]
- Even an official AST can return an unknown command name for computed invocations. Parser adoption alone cannot prove universal compatibility. [C7]

## Alternatives

- Extend the wrapper regex with another spelling or quoting exception. This may repair a particular example, but does not address the demonstrated first-span-versus-argument-tail mismatch. [C4, C5]
- Reuse guard_models.m_pwsh() unchanged. Its argument-tail handling is relevant, but its encoded-command handling returns an unknown effect, and the guard documents that unknown effects pass. Extract and validate syntax handling rather than treating the effect verdict as compatibility proof. [C8]
- Reject every nested PowerShell invocation. This would close the identified inspection gap but would also reject safe nested invocations explicitly covered by existing tests. Prefer complete handling of supported literal forms, with an explicit policy for unresolved forms. [C1, C2, C7]

## Experiment

On Windows, first compare powershell -NoProfile -NonInteractive -Command 'Write-Output ok;' 'Get-Error' with the single-argument equivalent powershell -NoProfile -NonInteractive -Command 'Write-Output ok; Get-Error'. Capture output and exit status to confirm whether the split form executes the second statement. Separately record nested_commands(), seven_only(), classic_seven_only(), launch('gate', ...), and both hosts' refusal decisions for both forms with mocked selected parent versions 5 and 7. Repeat with & 'powershell.exe' to keep executable rewriting from obscuring the child-version test. Before the fix, the hypothesis predicts that extraction omits the second quoted argument; after the fix, both equivalent forms must receive the same incompatible-child decision. Retain safe quoted-text, grouped-target, encoded-command, and malformed-encoding controls. Refute or revise the hypothesis if the real child does not execute the reconstructed tail. These checks are proposed, not executed. [C1, C2, C3, C4, C5, C6, C9]

## Result

pending — the fix records what it observed here, with the tests and the commit.

## Unresolved limits

The prior answer and F4 round transcripts are not present in the available context, and no tracked round-history file was located. Exact round wording, commit chronology, and whether this candidate is the latest reported reproducer remain unverified. Earlier corrections are inferred from current code and regression files. No files were changed and no tests or shell commands were run. This is draft research for the next fix and review.
