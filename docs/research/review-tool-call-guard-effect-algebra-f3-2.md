# Review research — F3, cycle 2

Draft research before the fix of review round 3 of PR #272, written 2026-11-02. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F3: every PowerShell member invocation has to reach the shared member gate (`guard.member_effect`), however it is written, so that a member that writes, deletes or moves a file produces a write/delete effect or `top`. Round 3 shows the ForEach-Object channel can still skip the gate: `Get-Item ../outside/target.txt | ForEach-Object -MemberName:Delete` is valid PowerShell parameter binding, but `foreach_member` (tool/guard_windows.py:210-239) compares the whole `membername:delete` word against the `membername` prefix, skips it, finds no member, and returns no effect. FileInfo.Delete then runs outside the write roots. The invariant broken is that the guard must read the command's parameters the way PowerShell binds them, or treat whatever it cannot read as undecidable.

Trigger: repeat, rounds 2, 3.

- [P1] [P1] tool/guard_windows.py:225 — Colon-bound member parameters skip the shared member gate

## Failed fixes

- Round 1 (eb6794a to c9770fe) removed `copyto` from SAFE_METHODS. That closed one method name in one syntax, the `.Name(` lexer site. The allow-list was still keyed by name, and every non-`.Name(` channel was outside the check, so round 2 got in through `ForEach-Object -MemberName Delete`.
- Round 2 (c9770fe to 1f77357) added the right single gate, `member_effect`, and routed the new channels into it. For ForEach-Object/Where-Object/%/foreach it added `foreach_member`, a second parameter reader written by hand that allows by default. (1) It recognises a member only as `-<prefix of membername>` followed by a separate word, so the colon-bound `-MemberName:Delete` / `-m:Delete` is skipped (round 3). (2) Any other dash word is assumed to be a switch that takes no value, so a value-taking parameter (common parameters -OutVariable/-ov, -PipelineVariable/-pv, -ErrorVariable/-ev, -WarningVariable/-wv, -InformationVariable/-iv, -OutBuffer/-ob, and ForEach-Object's own -ArgumentList/-Args and -InputObject) leaves its value to be read as the 'first positional = member name'. `% -ov name Delete` therefore judges `name`, which is in SAFE_MEMBERS, and never sees `Delete`. (3) `if any(w.why == 'group' for w in args): return set()` assumes that any group is the script-block form. But `simple()` turns every group into Word('(...)', 'group') whatever its opener (tool/guard.py:610). So `% ('Delete')`, `% -MemberName ('Delete')`, `% -MemberName:('Delete')`, `ForEach-Object -InputObject (Get-Item ../outside/x) -MemberName Delete` and `% -MemberName MoveTo -ArgumentList ('../outside/t.md')` all return no effect. The gate was correct; the binder that feeds it drops spellings, and when it is unsure it says 'no member' instead of 'undecidable'.

## Sources

### S1 — ForEach-Object (Microsoft.PowerShell.Core) - PowerShell 7.6

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/foreach-object
- Retrieved: 2026-11-02
- Locator: Syntax: ScriptBlockSet, PropertyAndMethodSet; Parameters -InputObject, -MemberName, -ArgumentList, -Process; CommonParameters; Example 7
- Fragment: ScriptBlockSet: 'ForEach-Object [-Process] <ScriptBlock[]> [-InputObject <PSObject>] [-Begin <ScriptBlock>] [-End <ScriptBlock>] [-RemainingScripts <ScriptBlock[]>] [-WhatIf] [-Confirm] [<CommonParameters>]'. PropertyAndMethodSet: 'ForEach-Object [-MemberName] <String> [-InputObject <PSObject>] [-ArgumentList <Object[]>] [-WhatIf] [-Confirm] [<CommonParameters>]'. -MemberName: 'Specifies the name of the member property to get or the member method to call. The members must be instance members, not static members. Wildcard characters are permitted, but work only if the resulting string resolves to a unique value.' Position 0, Type String. -ArgumentList: 'Specifies an array of arguments to a method call.' Aliases: Args; Position: Named; Value from remaining arguments: False. -InputObject: 'When you use the InputObject parameter with ForEach-Object, instead of piping command results to ForEach-Object, the InputObject value is treated as a single object.' Example 7: 'foreach Split "."' ('omits the names of the MemberName and ArgumentList parameters, which are optional'). 'This cmdlet supports the common parameters: -Debug, -ErrorAction, -ErrorVariable, -InformationAction, -InformationVariable, -OutBuffer, -OutVariable, -PipelineVariable, -ProgressAction, -Verbose, -WarningAction, and -WarningVariable.'
- Applicability: This is the parameter table a guard reader must agree with: the member slot may be filled by name or by position; -InputObject and -ArgumentList take values (including groups); the common parameters are accepted. foreach_member models none of these except a separate-word -MemberName.
- Counterevidence: The -ArgumentList table (Named, not from remaining arguments) contradicts Example 7, which binds a positional value; Process and MemberName both sit at position 0 and are told apart by type, which a static reader cannot see for a group.
- Validation: The -InputObject, -ArgumentList-with-group and positional-group cases in the experiment are denied after the fix.

### S2 — about_Parameters - PowerShell 7.6

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parameters
- Retrieved: 2026-11-02
- Locator: Long description, opening paragraph
- Fragment: '-<parameter_name> <parameter_value>' / '-<parameter_name>:<parameter_value>'. 'The name of the parameter is preceded by a hyphen (-), which signals to PowerShell that the word following the hyphen is a parameter name. The parameter name and value can be separated by a space or a colon character. Some parameters do not require or accept a parameter value. Other parameters require a value, but do not require the parameter name in the command.'
- Applicability: The colon-bound form is the documented equivalent of the space form; foreach_member (tool/guard_windows.py:224-226) handles only the space form.
- Counterevidence: The page does not spell out prefix abbreviation, and it says nothing about Unicode dashes.
- Validation: `% -MemberName:Delete` and `% -m:Delete` are denied after the fix; `% -MemberName:Name` stays allowed.

### S3 — about_CommonParameters - PowerShell 7.6

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_commonparameters
- Retrieved: 2026-11-02
- Locator: Long description alias list; -OutVariable, -PipelineVariable, -ErrorVariable, -OutBuffer sections
- Fragment: Aliases: 'ErrorVariable (ev)', 'InformationVariable (iv)', 'OutVariable (ov)', 'OutBuffer (ob)', 'PipelineVariable (pv)', 'WarningVariable (wv)'. -OutVariable: 'Stores output objects from the command in the specified variable' (Type: String, Aliases: ov, Position: Named). -PipelineVariable: 'Valid values are strings, the same as for any variable names.' (Type: String, Aliases: pv). -OutBuffer: Type Int32, Aliases ob. 'You can use the common parameters with any cmdlet, but they might not have an effect on all cmdlets.'
- Applicability: These parameters take a value in the next word. foreach_member skips the dash word only, so the value (for example `name`) lands in the member slot and the real member after it is never judged.
- Counterevidence: The value must happen to be a SAFE_MEMBERS name for the bypass to be silent; any other value makes foreach_member return top.
- Validation: `% -OutVariable name Delete` and `% -pv name Delete` are denied after the fix.

### S4 — Language-Theoretic Security - Wikipedia

- URL: https://en.wikipedia.org/wiki/Language-Theoretic_Security
- Retrieved: 2026-11-02
- Locator: Parser differentials; Injection attacks; Recognizer pattern
- Fragment: 'it is possible to verify that two implementations parse the same input language consistently' and this is 'advantageous because it shows no parser differential exists between the two implementations'; of CVE-2013-4787, if the two parsers were equivalent 'the vulnerability could not have existed'. Injection attacks 'are a special case of parser differentials'. The recognizer pattern requires that 'a program's input is fully recognized by a parser before the program acts upon that input.'
- Applicability: The guard (the checker) and PowerShell's binder (the consumer) read ForEach-Object arguments differently. Where they differ, the guard has to fail closed: an argument list it does not fully recognise must not be passed as 'no effect'.
- Counterevidence: This is a secondary summary; it does not discuss a sound over-approximation (a checker that is stricter than the consumer), which is what the fix aims for rather than exact equivalence.
- Validation: After the fix, every argument shape foreach_member does not recognise comes out top, which the deny cases check.

### S5 — PowerShell error in a script but not on the console: the string is missing the terminator (wiert.me)

- URL: https://wiert.me/2021/09/29/powershell-error-in-a-script-but-not-on-the-console-the-string-is-missing-the-terminator/
- Retrieved: 2026-11-02
- Locator: search-result summary (page not fetched)
- Fragment: 'when using Unicode on the console, it does not matter if you have a minus sign (-), en-dash (–), em-dash (—) or horizontal bar (―) as dash character. You can see this in tokenizer.cs at function NextToken and CharTraits.cs at function IsChar.' The same post: when saving to a non-Unicode file, it does matter. A PSScriptAnalyzer issue recommends ASCII hyphen-minus because 'Unicode dashes might cause parsing errors in earlier versions of PowerShell'.
- Applicability: A further way the guard's parameter readers may disagree with PowerShell's. foreach_member already returns top for it by accident; ps_args would read `–Path` as positional (outside F3).
- Counterevidence: Not Microsoft documentation; only the search summary was read; behaviour may differ between Windows PowerShell 5.1 and PowerShell 7 and with file encoding.
- Validation: A `% –MemberName Delete` case stays denied after the fix; the ps_args question is left to a separate finding.

### S6 — tool/guard_windows.py at 1f77357 (foreach_member, ps_args, module docstring)

- URL: tool/guard_windows.py
- Retrieved: 2026-11-02
- Locator: lines 1-9 docstring; 103-125 ps_args; 173-174; 210-239 foreach_member
- Fragment: Docstring: 'a parameter it does not know may or may not have taken the next word, and then every positional word counts as a possible target.' ps_args: `k, colon, v = t[1:].partition(':')`, `full = next((p for p in PS_VALUES if p.startswith(k)), None)`. foreach_member: `if any(w.why == 'group' for w in args): return set()`; `if t.startswith('-'): k = t[1:].lower(); if k and 'membername'.startswith(k) and i + 1 < len(args): members.append(args[i + 1]); i += 2; continue` / `i += 1; continue`; `members.append(w)  # the first positional word is the member name`; `break`.
- Applicability: This is the reader that disagrees with PowerShell: there is no colon split, unknown dash words are treated as switches, and any group anywhere means no effect. It also contradicts the module's own stated principle, which ps_args follows.
- Counterevidence: ps_args's global PS_VALUES prefix table resolves `-m` to `method`, so reusing it unchanged would introduce a different disagreement.
- Validation: The fix replaces this loop with fail-closed shape recognition; the new MEMBER_FORMS cases pass.

### S7 — tool/guard.py at 1f77357 (commands, simple, member_effect)

- URL: tool/guard.py
- Retrieved: 2026-11-02
- Locator: line 415 commands(); lines 606-610 simple(); 572-593 member_effect
- Fragment: commands(): `cur.append(('g', val, toks[i + 1:j]))` (val is the opener: '(', '{', '@{', '@('). simple(): `if it[0] == 'g': inner = reduce(it[2], ...); eff |= inner; words.append(Word('(...)', 'group'))`. member_effect: `return set() if name in SAFE_MEMBERS else top(f'method {method}')`.
- Applicability: The opener is known in commands() and discarded in simple(), so foreach_member cannot tell a script block `{}` from an expression `()`. member_effect itself is correct once it is given the name.
- Counterevidence: Keeping the opener changes the shape of the group Word, which other callers that check `words[1].text == '(...)'` (simple() line 652) rely on; the change must keep the existing text for '('.
- Validation: `% ('Delete')` is denied while `% { $_.Name }` stays allowed.

### S8 — tool/test_guard.py at 1f77357 (MEMBER_FORMS and controls)

- URL: tool/test_guard.py
- Retrieved: 2026-11-02
- Locator: lines 381-410
- Fragment: 'foreach-object-membername': 'Get-Item ../outside/target.txt | ForEach-Object -MemberName Delete', 'foreach-abbrev': 'gi ../outside/target.txt | foreach -m Delete', 'member-with-arguments': "gi README.md | ForEach-Object -MemberName CopyTo -ArgumentList '../outside/t.md'"; allowed controls include 'Get-ChildItem | % Name' and 'Get-ChildItem | ForEach-Object { $_.Name }'.
- Applicability: Every ForEach-Object case uses the space-separated form with a literal, ungrouped value and no other parameters, so the tests check only the spellings the reader handles.
- Counterevidence: none given
- Validation: The experiment's cases are added here.

## Claims

- C1 (source; S2): PowerShell accepts `-Name:Value` as the equivalent of `-Name Value`, so `-MemberName:Delete` binds Delete to MemberName.
- C2 (source; S1): ForEach-Object's member form takes the member name by name or at position 0, and also accepts -InputObject, -ArgumentList (alias Args) and the common parameters.
- C3 (source; S3): Several common parameters accepted by every cmdlet take a value in the next word: OutVariable/ov, PipelineVariable/pv, ErrorVariable/ev, WarningVariable/wv, InformationVariable/iv (String) and OutBuffer/ob (Int32).
- C4 (interpretation; S1, S2, S3, S6, S7): foreach_member disagrees with PowerShell's binder in at least three ways, and each disagreement comes out as 'no effect': it has no colon split, it treats value-taking parameters as switches so their value fills the member slot, and it takes any `(...)` group as the script-block form.
- C5 (source; S4): A checker that parses input differently from the consumer is a parser differential, and the checker approves something other than what runs; input should be fully recognised before it is acted on.
- C6 (interpretation; S6, S7, S8, S4): Rounds 2 and 3 both stem from foreach_member allowing what it does not recognise. Patching only the colon form leaves the other disagreements open. Recognising two exact shapes and returning top for everything else is the fix that converges.
- C7 (source; S5): PowerShell's tokenizer reportedly accepts en dash, em dash and horizontal bar as the parameter dash, which would be a further disagreement for the guard's parameter readers.

## Core theory

Language-theoretic security calls the case where two parsers interpret the same input differently a parser differential, and treats it as a source of vulnerabilities in its own right: a checker that reads the input differently from the program that acts on it approves something other than what runs. Its mitigations are to show that both implementations parse the same input language, and the 'recognizer pattern': input is fully recognised before anything acts on it, so later logic only ever sees valid input. Here the consumer is PowerShell's parameter binder. about_Parameters states that 'The parameter name and value can be separated by a space or a colon character'. ForEach-Object's PropertyAndMethodSet takes `[-MemberName] <String>` at position 0, `[-InputObject <PSObject>]` and `[-ArgumentList <Object[]>]` (alias Args), and supports the common parameters, several of which take a value (OutVariable/ov, PipelineVariable/pv, ErrorVariable/ev, WarningVariable/wv, InformationVariable/iv: String; OutBuffer/ob: Int32). A guard reader that does not apply those rules disagrees with the binder. A security check should be built so that any disagreement comes out as 'undecidable'; this one comes out as 'safe'.

## Applicability

tool/guard_windows.py:210-239 `foreach_member`: line 217 `if any(w.why == 'group' ...) return set()` (groups taken for script blocks); lines 224-231 (a dash word is taken as -MemberName only by prefix and with a separate value; everything else is skipped as a switch); line 232 (the first non-dash word is the member). tool/guard.py:606-610 `simple()` appends Word('(...)', 'group') for every opener, which discards the `{` vs `(` distinction that `commands()` (tool/guard.py:415) keeps. tool/guard_windows.py:1-9, the module docstring, already states the fail-safe principle ('a parameter it does not know may or may not have taken the next word, and then every positional word counts as a possible target'), and `ps_args` (103-125) follows it with its `ambiguous` flag and colon split; `foreach_member` follows neither. `ps_args` cannot simply be called instead: it resolves prefixes against a single global PS_VALUES table, where `-m` resolves to `method`, not `membername`, while PowerShell resolves prefixes per cmdlet. The fix belongs in `foreach_member`, plus a one-line change in `simple()` to keep the group's opener. tool/test_guard.py:384-410 MEMBER_FORMS / controls is where the cases belong.

## Hypothesis

The member gate is sound. The fault is in `foreach_member`, which reads ForEach-Object's parameters differently from PowerShell's binder and, wherever the two disagree, returns 'no effect' instead of `top`. Round 3's colon form is one of at least three ways they disagree; the other two are unknown value-taking parameters read as switches, and `(...)` groups read as script blocks. A fix that only splits `-MemberName:Value` leaves the other two open, which is the pattern that produced rounds 1 to 3. The fix that converges is a fail-closed reader. `foreach_member` returns no effect only for argument lists it fully explains, and `top` for anything else. Two shapes are explained: (a) script-block form, where every argument is a `{}` script block, optionally under -Process/-Begin/-End/-RemainingScripts/-Parallel, plus known switches and literal -ThrottleLimit/-TimeoutSeconds; (b) member form, where the member is `-MemberName`/`-m` with its value as the next word or after a colon, or the first positional word, is a literal with no `why`, and is followed only by literal positional arguments or literal -ArgumentList/-Args values. That member goes to `member_effect('', name, False)`, with a wildcard name giving `top`. Any other parameter (InputObject, common parameters, an unknown or ambiguous prefix), any `(...)`/`$(...)`/`@(...)` group, or any variable gives `top`. For (a), `simple()` has to keep the group's opener (commands() already stores it as `val` in ('g', val, toks)), so that `{` can be told apart from `(`. Where-Object should not share this reader unchanged: its simplified syntax has a different parameter table (-Property, -Value and comparison switches such as -EQ).

## Counterevidence

- Some forms are already `top` by accident and need no change: a Unicode-dash parameter (`% –MemberName Delete`) falls through to the member slot as `–membername`, which is not in SAFE_MEMBERS. A quoted member (`% 'Delete'`) and a variable member (`% $m`, why='variable') also come out `top`.
- The -OutVariable/-PipelineVariable/-ArgumentList bypasses only work when the parameter's value happens to be in SAFE_MEMBERS (name, length, count, keys, ...). That narrows them but does not remove them; `name` is a normal variable name.
- For `-ArgumentList` the ForEach-Object page lists Position: Named and 'Value from remaining arguments: False', yet Example 7 `foreach Split '.'` binds a positional '.' to ArgumentList. The docs contradict each other. This does not change the judgement, because the member name decides the effect and its arguments do not.
- MoveTo/CopyTo with a relative argument resolve against the process working directory (cycle 1, about_Methods). The destination may therefore fall inside the root, but MoveTo still deletes the source outside the root.
- No code was run at 1f77357; which forms are allowed today is predicted from reading foreach_member and simple().

## Alternatives

- Split `-name:value` in foreach_member only, which is what round 3 literally asks. Rejected as the first step: it leaves `% -ov name Delete`, `% ('Delete')`, `-InputObject (gi x) -MemberName Delete` and `-MemberName MoveTo -ArgumentList ('../outside/t.md')` open, which is how the previous rounds failed.
- Call `ps_args` from foreach_member. Rejected as-is: it resolves prefixes against the global PS_VALUES table (`-m` becomes `method`) and has no `membername` entry. A per-cmdlet parameter table passed to a shared normaliser would work but is more code than the fail-closed shape check.
- Model PowerShell's binder fully: parameter sets, per-cmdlet unique-prefix resolution, aliases, every common parameter, delay-bind. That would be more precise, but it is large and is itself a new place for the two parsers to disagree. The fail-closed shapes cost only precision on rare forms (`-InputObject`, common parameters on `%`), which agents seldom write.
- Make every ForEach-Object call that is not purely script blocks `top`. Sound and smaller still, but it denies `gci | % Name` and `'a.b' | % Split '.'`, which the existing controls keep allowed.

## Experiment

Add the following cases to MEMBER_FORMS (tool/test_guard.py:384); each must be denied ('판단할 수 없는'): `gi ../outside/target.txt | ForEach-Object -MemberName:Delete`; `gi ../outside/target.txt | % -m:Delete`; `gi ../outside/target.txt | % ('Delete')`; `gi ../outside/target.txt | % -MemberName ('Delete')`; `ForEach-Object -InputObject (Get-Item ../outside/target.txt) -MemberName Delete`; `gi README.md | % -MemberName MoveTo -ArgumentList ('../outside/t.md')`; `gi ../outside/target.txt | % -OutVariable name Delete`; `gi ../outside/target.txt | % -pv name Delete`; `gi ../outside/target.txt | % -ArgumentList name MoveTo`. Add these to the allowed controls: `Get-ChildItem | % -MemberName:Name`, `Get-ChildItem | % -Process { $_.Name }`, `1..2 | % -Begin {} -Process { $_ }`, `'a.b' | % Split '.'`. Run them against 1f77357 first, with only the affected selection (`python -m pytest -q tool/test_guard.py -k member`). The hypothesis predicts that every new deny case is allowed today. If any is already denied, the differential is narrower than stated and the fix can be smaller. After the fix: all deny cases denied, controls and the existing MEMBER_FORMS unchanged, and `python -m ruff check tool laya` clean.

## Result

pending — the fix records what it observed here, with the tests and the commit.

## Unresolved limits

No code was run, so which forms are allowed today is predicted from reading `foreach_member` and `simple()`. That PowerShell accepts en dash, em dash and horizontal bar as the parameter dash rests on a blog post and a PSScriptAnalyzer issue, not on Microsoft documentation. If it is true, `ps_args` has the same kind of differential outside F3 (`Remove-Item –Path ../outside/x` would read `–Path` as a positional path inside the root); that should be raised as its own finding, not folded into this fix. Where-Object currently shares foreach_member; its simplified syntax only reads a property, and its parameter table was not fetched, so how it should be read is left open. The ForEach-Object page contradicts itself on whether -ArgumentList takes remaining positional arguments. The LangSec theory is cited from a Wikipedia summary; the primary paper (Sassaman et al., 2011) was not fetched, and a Dartmouth PDF on parser-differential root causes was downloaded but could not be read.
