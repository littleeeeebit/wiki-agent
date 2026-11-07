# Review research — F3, cycle 2

Draft research before the fix of review round 10 of PR #307, written 2026-11-07. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F3 now concerns command identity, beyond the earlier executable/data distinction. With PowerShell 7 selected, `function powershell { Write-Output helper }; powershell` contains a genuine command invocation that resolves to a function. The repair instead predicts `function powershell { Write-Output helper }; pwsh -NoProfile`. Executable modernization must preserve locally defined command behavior and must not infer an external executable solely from a literal command name. This conclusion is a static trace, not an executed reproduction. [C3, C4, C5, C10]

Trigger: recurrence, rounds 1, 2, 3, 6, 10.

- [P1] [P1] tool/command_repair.py:33 — a same-named PowerShell function is rewritten as an external executable / when `function powershell { Write-Output helper }; powershell` runs with PowerShell 7 selected / the hook launches `pwsh` instead of calling the function

## Failed fixes

- After round 1 at e2e98be, the repair excluded grouped path data and required arguments before inserting `&`. That protected the reported assignment but skipped standalone quoted legacy checks, allowing string output to substitute for check execution. [C1, C12]
- After round 2 at 0fb8ffa, standalone legacy positions were admitted again. Argument classification still relied on expression-operator exclusions, leaving PowerShell 7 null-coalescing and ternary expressions vulnerable to conversion into execution. [C1]
- After round 3 at 3304b40, PowerShell 7 expression operators were excluded. This addressed that expression family without establishing every enclosing syntax role. Subsequent shared discovery work admitted assignment and hashtable-value commands but also misclassified command-like hashtable keys, producing round 6. [C1]
- After round 6 at dd6048c, the context check was moved ahead of generic command-boundary acceptance. Current tests distinguish exact key and command extents and exercise preservation through both host verdicts. That correction addresses the key defect, but command-position correctness does not establish whether a command resolves to a function or an external executable. [C2, C8]
- Intervening F6 fixes added literal-variable binding, declared-alias resolution and alias-removal transitions. They protected an alias named powershell, but left an unaliased name as `literal` without considering functions. Earlier research explicitly retained function shadowing as unproved. Round 10 therefore exposes an omitted resolution category rather than evidence that the corrected hashtable ordering failed again. [C3, C9, C10]

## Sources

### S1 — Shared invocation discovery and child-shell inspection

- URL: tool/common/shell.py
- Retrieved: 2026-11-07
- Locator: Invocation and invocations, lines 104–180; nested_commands, lines 183–203; invoked, lines 245–268
- Fragment: `return Invocation(program, start, cursor, "bound" if bound else "literal", words[0])`; `program, aliased = aliases.resolve(item.name, start)`; `if aliased: item = replace(item, program=program, kind="alias" if program is not None else "unresolved")`. nested_commands raises for kind `unresolved` and inspects programs powershell/pwsh. invoked separately repairs legacy gate positions.
- Applicability: Shows where readable spelling becomes a program classification and where that classification affects replacement and capability inspection.
- Counterevidence: Explicit unresolved, bound, alias and scriptblock kinds already provide some separation; discovery is not wholly name-only.
- Validation: Require distinct local-function and external decisions while preserving target extents, valid helper acceptance and genuine child discovery.

### S2 — PowerShell executable replacement

- URL: tool/command_repair.py
- Retrieved: 2026-11-07
- Locator: _pwsh, lines 24–37; refusal, lines 52–74
- Fragment: `if os.name == "nt" and shell.chosen().major < 7: return command`; replacement requires `match.kind == "literal" and match.program == "powershell"` and an exact powershell/powershell.exe source spelling. It inserts `pwsh` or `pwsh -NoProfile`.
- Applicability: The final function call satisfies the current replacement condition despite not identifying the external Windows PowerShell executable.
- Counterevidence: Selected Windows version below 7, alias kinds and path-qualified source targets do not satisfy this rewrite branch.
- Validation: Under selected version 7, preserve the function call and still modernize supported external-command controls.

### S3 — Declared alias state

- URL: tool/common/powershell_aliases.py
- Retrieved: 2026-11-07
- Locator: Aliases.resolve, lines 24–37; define, lines 39–112
- Fragment: `if current not in self.values: break`; resolve then returns the normalized name and aliased flag. define recognizes alias setters, alias-provider operations and removals; provider paths not beginning with `alias:` are excluded.
- Applicability: An absent alias entry leaves powershell as its own literal program name. Function declarations and Function: provider bindings are not tracked.
- Counterevidence: Alias chains, uncertain aliases, removal transitions and some scope conditions are explicitly modeled.
- Validation: Check alias precedence over functions, aliases targeting functions and alias removal revealing a function rather than assuming an external executable.

### S4 — Current command-position classifier

- URL: tool/common/powershell_syntax.py
- Retrieved: 2026-11-07
- Locator: command_position, lines 77–106
- Fragment: `if stack and stack[-1][1]:` handles the hashtable frame and returns its value-context classification before `if re.search(boundary + end, prefix, re.I): return True`.
- Applicability: Confirms that the previous immediate ordering defect has been corrected in the current source.
- Counterevidence: This routine classifies syntax position, not runtime function or executable identity.
- Validation: Retain hashtable-role regressions while adding binding checks after discovery.

### S5 — F3 cycle 1 research and recorded correction

- URL: docs/research/review-powershell-version-resolver-and-install-f3-1.md
- Retrieved: 2026-11-07
- Locator: Failed fixes, lines 13–18; Result and Unresolved limits, lines 213–219
- Fragment: "After round 1 at e2e98be, the reported repair excluded grouped data and required a following argument before inserting `&`." The page records standalone restoration, PS7 operator exclusions and the later key-role repair. Its limit states that native-parser evidence "does not establish universal grammar coverage or runtime target resolution."
- Applicability: Documents why earlier example-family corrections missed subsequent contexts and preserves the distinction between native discovery and intentional legacy conversion.
- Counterevidence: Historical descriptions and repair observations are draft records, not fresh execution or an independently certified commit ledger.
- Validation: Keep each earlier preservation and execution obligation; add runtime binding evidence rather than repeat syntax-only validation.

### S6 — F6 binding, alias and removal correction history

- URL: docs/research/review-powershell-version-resolver-and-install-f6-2.md
- Retrieved: 2026-11-07
- Locator: Round 7–9 corrections, lines 219–237; Unresolved limits, lines 239–241
- Fragment: "Round 7 found two regressions in that repair. F8 requires compatible variable-selected checks to remain runnable"; later sections record declared-alias and removal support. "No result proves compatibility for aliases inherited from the environment, function shadowing, arbitrary process-creation APIs, external scripts, stdin programs or every PowerShell 7 feature."
- Applicability: Explains both the omitted function-binding dimension and why blanket refusal is an unsuitable substitute for preserving known callable behavior.
- Counterevidence: The page records successful bounded alias and variable cases; it does not claim general name resolution.
- Validation: Preserve compatible variable and alias acceptance while adding function-aware binding cases.

### S7 — Hashtable correction regressions

- URL: tool/test_shell_round_six.py
- Retrieved: 2026-11-07
- Locator: Key preservation and native oracle, lines 46–98
- Fragment: `assert repair._pwsh(command, "ps") == command`; host verdict outputs must preserve the same command. Native comparisons assert `actual == expected` for exact command spans and repaired scripts must have zero parse errors.
- Applicability: Shows that cycle 1 strengthened syntax preservation and exact-span evidence.
- Counterevidence: A valid function invocation is still a command span, so these assertions cannot distinguish a function from an application.
- Validation: Retain these controls and pair them with binding metadata and observable function behavior.

### S8 — Native parser oracle implementation

- URL: tool/test_shell_round_five.py
- Retrieved: 2026-11-07
- Locator: native_commands, lines 41–78
- Fragment: `executable = shutil.which("powershell")`; non-Windows or missing executable skips the oracle. It calls Parser.ParseInput, enumerates CommandAst nodes and records `name = $command.GetCommandName()`, target kind/extents, parse errors, hashtable pairs and parser version.
- Applicability: The oracle records syntax and command names, not runtime CommandType or selected application paths.
- Counterevidence: Its structural evidence remains useful and does not execute supplied scripts.
- Validation: Add function declaration records and controlled runtime lookup/output evidence; run supported-version checks explicitly rather than infer PS7 coverage from this helper.

### S9 — Existing alias shadowing preservation control

- URL: tool/test_shell_round_eight.py
- Retrieved: 2026-11-07
- Locator: Alias controls, lines 93–115
- Fragment: `command = "Set-Alias powershell python; powershell outside/check.py"`; `_pwsh(command, "ps") == command`, and both host verdicts must preserve the command without denial.
- Applicability: Provides an existing preservation pattern for a same-named non-executable binding.
- Counterevidence: The control covers an alias, not a function declaration or function-provider mutation.
- Validation: Add paired function and filter controls through the same repair and host boundaries.

### S10 — Recorded command repair policy

- URL: docs/plans/done/agent-commands/0-overview.md
- Retrieved: 2026-11-07
- Locator: Decisions, lines 30–47
- Fragment: "Scope: Claude Code and Codex both." "a rule that can repair a command repairs it and lets it run"; "`powershell`: rewritten to `pwsh -NoProfile`."
- Applicability: Explains why complete removal of modernization would change an established product decision.
- Counterevidence: The broad policy does not specify how to distinguish functions from external executables.
- Validation: Preserve supported external modernization while proving local-call preservation on both hosts.

### S11 — Microsoft PowerShell command precedence

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_command_precedence?view=powershell-7.5
- Retrieved: 2026-11-07
- Locator: Command precedence, lines 43–68
- Fragment: "If you don't specify a path," … "Alias"; "Function"; "Cmdlet"; "External executable files". "This only applies to loaded commands."
- Applicability: A loaded function named powershell precedes external executable lookup for the reported bare invocation.
- Counterevidence: Explicit paths select their specified location. An unloaded module does not necessarily supersede an existing executable; the page documents that qualification.
- Validation: In isolated fixtures, record Get-Command CommandType after declarations and verify that repair preserves the selected callable.

### S12 — Microsoft PowerShell functions

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_functions?view=powershell-7.5
- Retrieved: 2026-11-07
- Locator: Long description and Function syntax, lines 37–68; Filter syntax, lines 90–98
- Fragment: "Within script files and script-based modules, functions must be defined before they can be called." "filter [<scope:>]<name> {<statement list>}"
- Applicability: The reproducer defines its function before calling it. Filters and optional scope modifiers also need consideration.
- Counterevidence: Textual presence alone does not prove that a conditional or nested declaration has executed before a call.
- Validation: Pair definition-before-call fixtures with calls before declaration and definitions inside uncalled bodies.

### S13 — Microsoft PowerShell scope rules

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_scopes?view=powershell-7.5
- Retrieved: 2026-11-07
- Locator: Scope rules and Parent and child scopes, lines 41–61
- Fragment: "An item is visible in the scope that it was created and in any child scopes, unless you explicitly make it private."
- Applicability: Function visibility cannot be modeled merely by treating every brace as an interchangeable scope.
- Counterevidence: Explicit scope modifiers alter visibility, modules have their own session-state hierarchy, and session state cannot be accessed across runspace instances.
- Validation: Check supported local/parent visibility, scope modifiers and isolated-runspace assumptions using controlled native fixtures.

### S14 — Microsoft PowerShell Function provider

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_function_provider?view=powershell-7.5
- Retrieved: 2026-11-07
- Locator: Detailed description, lines 47–52; Creating, deleting and changing functions, lines 118–155
- Fragment: "The PowerShell Function provider lets you get, add, change, clear, and delete the functions and filters in PowerShell."
- Applicability: A function-keyword-only recognizer would omit another documented way to introduce or remove a shadowing binding.
- Counterevidence: Provider mutations may depend on computed paths, scopes and execution outcomes; the documentation does not make them statically decidable.
- Validation: Test literal Function: writes/removals and retain explicit uncertainty for unproved mutations.

### S15 — Microsoft CommandAst.GetCommandName API

- URL: https://learn.microsoft.com/en-us/dotnet/api/system.management.automation.language.commandast.getcommandname?view=powershellsdk-7.4.0
- Retrieved: 2026-11-07
- Locator: Definition, lines 57–60
- Fragment: "Returns the name of the command invoked by this ast." "This command name may not be known statically, in which case null is returned."
- Applicability: The existing oracle extracts a command name; interpreting that result as external executable identity requires additional evidence.
- Counterevidence: Known literal names still provide useful syntactic information. The API does not itself offer a runtime binding guarantee.
- Validation: Keep name/extents assertions separate from runtime Function/Application metadata and behavior assertions.

### S16 — Microsoft FunctionDefinitionAst API

- URL: https://learn.microsoft.com/en-us/dotnet/api/system.management.automation.language.functiondefinitionast?view=powershellsdk-7.4.0
- Retrieved: 2026-11-07
- Locator: Definition and properties, lines 54–87
- Fragment: "The ast that represents a function or filter definition. The function is always named." "If true, the filter keyword was used."
- Applicability: Provides declaration structure for extending the existing parsing-only oracle with function/filter names and body extents.
- Counterevidence: A declaration node establishes syntax, not whether its statement executes or its binding is visible at a later runtime call.
- Validation: Record declaration extents alongside command extents, then confirm binding separately in harmless fixtures.

## Claims

- C1 (source; S5): Earlier F3 repairs successively covered grouped data, standalone legacy checks, PowerShell 7 expression operators and hashtable roles; their historical sequence is recorded in the prior draft and supplied reviews.
- C2 (source; S4, S7): The current classifier checks hashtable context before generic boundaries, with exact-span and host-preservation regressions for that correction.
- C3 (interpretation; S1, S3): Invocation discovery initially classifies literal spelling as a literal program and subsequently adjusts declared aliases; it has no corresponding function-binding model.
- C4 (source; S2): The PowerShell repair rewrites literal exact-name powershell targets to pwsh, except on Windows when the selected version is below 7.
- C5 (source; S11): For loaded commands without a specified path, PowerShell resolves aliases before functions, then cmdlets, then external executables; explicit paths and unloaded modules qualify this rule.
- C6 (source; S12, S13): Function definition order, scope visibility and runspace state affect callable availability; functions in scripts must be defined before they are called.
- C7 (source; S14, S16): Function/filter declarations have dedicated AST structure, while the Function provider supplies additional creation, mutation and removal mechanisms.
- C8 (interpretation; S5, S7, S8, S11, S15, S16): The existing native oracle proves syntax and spans for its installed Windows PowerShell cases, but neither CommandAst membership nor GetCommandName proves an Application binding.
- C9 (source; S6, S9): Declared alias shadowing already has preservation controls; prior research documents compatible-call regressions from blanket refusal and explicitly leaves function shadowing unproved.
- C10 (interpretation; S1, S2, S3, S11, S12): Static inspection predicts that the reported final powershell function call is classified literal and rewritten when version 7 is selected, despite PowerShell function precedence.
- C11 (interpretation; S1, S2, S3, S4, S9, S11, S12, S13, S14, S15, S16): A durable correction should distinguish syntax, readable target spelling and binding evidence, preserve known local callables, and suppress executable replacement where that binding is ambiguous.
- C12 (interpretation; S1, S5): Legacy quoted-gate conversion is a separate application policy and must retain its execution controls; native command discovery alone cannot replace that policy.
- C13 (interpretation; S1, S6): Relabeling known function calls as unresolved would trigger existing child-target refusals and risks replacing a mutation bug with rejection of valid helper calls.
- C14 (interpretation; S2, S10): Modernizing powershell commands on both hosts is a recorded product decision, so removing all modernization would change the intended behavior.
- C15 (interpretation; S1, S2, S3, S5, S6, S7, S8, S9, S11, S12, S13, S14, S15, S16): A discriminating experiment combines declaration/command AST records with controlled runtime binding metadata, output, exit status and both host verdicts; syntax-only success cannot confirm this fix.

## Core theory

PowerShell separates parsing a command invocation from resolving its name. For loaded commands without an explicit path, aliases precede functions, functions precede cmdlets, and external executables come afterwards. A function defined earlier in the same script can therefore own the name powershell. Scope, execution order and provider mutations affect which binding is visible. CommandAst.GetCommandName reports a name, possibly null; it does not certify that the name identifies an application. These conditions explain why syntactically exact invocation spans remain insufficient for executable replacement. [C5, C6, C7, C8]

## Applicability

Keep the corrected syntax-role checks in powershell_syntax.py. At shell.invocations() lines 172–180, separate target spelling from target binding: resolve alias chains and then consider visible functions/filters before classifying a bare name as an external candidate. Function declarations must be recognized as declarations, without treating their bodies as already executed. Use execution order and supported scope rules for proven cases; retain uncertainty for conditional definitions, provider mutations or effects the analyzer cannot prove. A conservative suppression of replacement in an ambiguous context is preferable to inventing external identity. Preserve literal source names and target extents. At command_repair._pwsh() lines 31–34, require the external-candidate decision before replacement. A known function call must remain runnable and unchanged; do not merely relabel it `unresolved`, because nested_commands() currently refuses that kind. Preserve independent discovery of genuine child-shell calls inside function bodies. Alias-to-function and variable-to-function calls need the same binding decision. The replacement destination also needs consideration: inserting the bare name pwsh can itself encounter a local binding. Prove or explicitly select the intended replacement executable within the supported policy. [C3, C4, C5, C6, C7, C11, C13]

## Hypothesis

The remaining cause is conflating a statically readable command name with an external executable identity. `record()` labels a one-word literal target as `literal`; the alias tracker changes that classification only when it encounters alias state. An ordinary function declaration contributes no binding state, so the final powershell remains eligible for `_pwsh()` replacement. The native parser oracle cannot reject this mistake: a function call is also a CommandAst whose command name is powershell. The next repair needs a separate binding decision before executable replacement, with explicit function/filter and uncertain outcomes rather than an external-executable default whenever alias lookup misses. [C3, C4, C8, C10, C11]

## Counterevidence

- The earlier fixes have focused regression coverage and the hashtable precedence defect is visibly corrected. Their recurrence does not imply those fixes were ineffective within their tested scope. [C1, C2]
- Alias shadowing is already preserved, including a Set-Alias powershell python control. This demonstrates an existing mechanism for avoiding some name-based rewrites; extending binding classification may suffice without replacing all discovery code. [C3, C9]
- On Windows with selected major version below 7, `_pwsh()` returns the original command. The reported mutation requires the branch that permits modernization. [C4]
- PowerShell precedence applies to loaded commands. Explicit paths and commands in unloaded modules have different conditions. The evidence does not justify suppressing every executable whose basename happens to match any function declared anywhere in the text. [C5, C6]
- The exact current failure has not been executed here. Source inspection strongly predicts it, but runtime output, launch behavior and supported-version details remain experimental obligations. [C10, C15]

## Alternatives

- Add another punctuation exclusion in command_position(). The final powershell is a valid command position; punctuation is no longer the distinguishing information. [C2, C5, C10]
- Use CommandAst presence or exact target extents as sufficient replacement authorization. Both the original function call and the rewritten external call can parse successfully. Retain this oracle for syntax, and add binding and behavior evidence. [C8, C15]
- Skip replacement whenever the text contains `function powershell`. This can serve as a conservative containment measure, but comments, strings, execution order, filters, provider-created functions, scope and alias precedence require explicit handling. A global textual blacklist does not demonstrate correct command binding. [C5, C6, C7, C11]
- Mark all function-related calls unresolved and reuse the existing refusal. That would avoid this rewrite by rejecting valid helper calls, repeating the compatibility regression documented after the earlier blanket variable refusal. Distinguish known local callables from unresolved executable targets. [C9, C13]
- Run Get-Command once in a separate process before repair. Its state cannot establish a function created later by the inspected script or the state of another runspace. Use it inside controlled fixture execution as evidence, not as a universal preflight resolver. [C6, C15]
- Disable all powershell modernization. That removes this mutation but abandons the recorded repair policy. First restrict the existing transformation to supported external-command cases. [C4, C14]
- Adopt a native parser at runtime immediately. It can improve declaration and scope structure, but still needs a binding policy and cannot establish arbitrary runtime effects by parsing alone. Extend the existing oracle before choosing that architecture. [C7, C8, C11]

## Experiment

No checks were executed in this research turn. First capture invocations(), nested_commands() and _pwsh() output for the exact round-10 input under selected versions 5 and 7. Predicted baseline: the final call is program=powershell, kind=literal, and major 7 replacement changes it to pwsh -NoProfile. Independently parse the source, recording errors, FunctionDefinitionAst name/body extents and CommandAst target extents; the final call should remain a valid command node even though its binding is a function. Then run only a bounded harmless variant: `function powershell { param($Command) Write-Output helper }; powershell -Command 'Write-Output external-child; exit 17'`. In isolated native sessions, compare original and repaired stdout, stderr and exit status, and record Get-Command metadata after the declaration. The original should print helper without launching a child; the faulty replacement is predicted to print external-child and preserve child status 17. After repair, require unchanged function behavior and no fake child-shell extraction. Extend with case variants, filter declarations, bare/quoted call-operator forms, literal variables, aliases to the function, aliases overriding the function, alias removal revealing the function, supported scope modifiers, function removal and provider-created functions. Include conditional definitions, a call before declaration, definitions inside uncalled functions, and child-scope versus dot-sourced definitions to expose unsupported binding assumptions. Retain genuine external invocation controls, actual external children inside function bodies, and a locally shadowed pwsh destination control. Exercise both actual host verdict functions with their respective payload shapes; preservation must survive updatedInput handling without a new refusal. Keep the previous grouped-data, PowerShell 7 expression, hashtable-key and standalone legacy-gate execution controls. Record installed parser/runtime versions and skips. Refute the immediate hypothesis if current discovery already distinguishes the final function call, replacement leaves it unchanged with version 7 selected, or native lookup in the controlled fixture resolves it to an Application rather than the declared Function. [C1, C2, C3, C4, C5, C6, C7, C8, C9, C12, C13, C15]

## Result

pending — the fix records what it observed here, with the tests and the commit.

## Unresolved limits

This is draft research, not a verified fix or shared rule. Reads and web research were performed; no files were written, no shell was run and no tests passed in this turn. Commit identities and historical correction descriptions come from the supplied review history and earlier draft research, not an independent Git-history audit. The existing native_commands() helper selects Windows PowerShell and conditionally skips, so its evidence cannot establish PowerShell 7 behavior by itself. Arbitrary imports, inherited session state, dynamically generated functions, provider mutations and execution-dependent scope changes remain outside a demonstrated static model. The proposed bounded classifier must document those limits rather than convert absence of known bindings into universal proof of external identity. [C6, C7, C8, C9, C11, C15]
