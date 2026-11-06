# Review research — F6, cycle 2

Draft research before the fix of review round 6 of PR #307, written 2026-11-07. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F6 breaks child-shell capability enforcement: executable code passed to Windows PowerShell 5.1 must be checked against that child's capabilities, independently of the selected parent version. For `$target = 'powershell'; & $target -Command 'Get-Error'`, current discovery recognizes neither the variable-selected child nor its quoted body. Static inspection predicts an empty feature result, no classic-child restriction, and acceptance by the capability boundary. This is a prediction from source inspection, not an execution result from this research turn. [C1, C2]

Trigger: recurrence, rounds 2, 3, 4, 5, 6.

- [P1] [P1] tool/common/shell.py:105 — variable-selected classic children are not capability-checked / when `powershell` is assigned to a variable and invoked with `&` / a 7-only body reaches Windows PowerShell 5.1

## Failed fixes

- Round 2, reported at 0fb8ffa: grouped-literal normalization restored executable names hidden inside transparent parentheses. It repaired a literal representation, while retaining discovery restricted to recognizable shell names. Variable-selected targets never enter that path. [C4]
- Round 3, reported at 3304b40: EncodedCommand decoding added another body representation, recursive inspection and malformed-payload rejection. These checks still require discovery of the child first. An undiscovered target bypasses decoding entirely. [C3, C4]
- Round 4, reported at 0803a14: argument reconstruction handled quoted options and command tails, and rejected computed arguments to recognized children. The unresolved policy applies to arguments only after a literal executable has been found; it does not cover an unresolved executable. [C3, C4]
- Round 5, reported at 20823ef and corrected before dd6048c: shared invocations() discovery and command_position() added assignment and other statement contexts. This removed the duplicated PowerShell discovery restriction identified in cycle 1. However, the shared enumerator still emits only literal powershell/pwsh targets. Its differential oracle likewise discards nonliteral targets, so increasing the context matrix could not expose the variable-target omission. [C4, C9]
- Round 6, reported at dd6048c: no correction exists yet. Cycle 1 explicitly left computed invocation targets unresolved, but the implementation represents that limitation as no discovered child rather than an unresolved capability decision. The research limitation therefore remained an executable acceptance path. [C2, C8, C9]

## Sources

### S1 — Shared PowerShell discovery and capability boundary

- URL: tool/common/shell.py
- Retrieved: 2026-11-07
- Locator: command_syntax(), invocations(), nested_commands(), classic_seven_only(), seven_only(): lines 79-154; resolve() and launch(): lines 242-272
- Fragment: "Shared literal shell discovery, retaining executable and argument source spans."; `for nested in invocations(text):`; `for _, body in nested_commands(text):`; `if classic_seven_only(line):`
- Applicability: Literal-only discovery determines which child bodies reach feature inspection. launch checks classic children independently of parent selection.
- Counterevidence: Discovered children receive recursive inspection, and literal target normalization already handles transparent grouping.
- Validation: Require every executable target occurrence to produce a resolved or unresolved record; verify the exact reproducer cannot yield an empty-compatible result.

### S2 — PowerShell masking, command positions and native argument reconstruction

- URL: tool/common/powershell_syntax.py
- Retrieved: 2026-11-07
- Locator: command_position(): lines 8-30; arguments(): lines 47-89; syntax(): lines 92-171
- Fragment: "Statement starts and complete assignment LHSs admit a command; arguments do not."; "Read literal native arguments until the outer command ends; refuse computed values."; "A same-length code view and quoted spans, retaining only string subexpressions."
- Applicability: The assignment repair affects command position. Quoted target assignments and command bodies are masked; computed-argument rejection is reached only after shell discovery.
- Counterevidence: String subexpressions retain executable syntax, and computed arguments to discovered children already raise ValueError.
- Validation: Preserve data masking and computed-argument refusal while adding unresolved target handling.

### S3 — Executable repair and capability refusal

- URL: tool/command_repair.py
- Retrieved: 2026-11-07
- Locator: _pwsh(): lines 24-49; refusal(): lines 52-74; repaired(): lines 127-149
- Fragment: `for match in shell.invocations(command):`; `if shell.classic_seven_only(command):`; `except ValueError as exc:`
- Applicability: PowerShell repair now shares literal discovery with inspection. refusal converts capability ValueErrors into explicit denial.
- Counterevidence: POSIX repair intentionally retains separate discovery. Selected 5.1 sessions deliberately avoid executable replacement.
- Validation: Verify unresolved targets reach refusal even when repair makes no change or raises and the host falls back to the original command.

### S4 — Assignment-context matrix and its literal-only native oracle

- URL: tool/test_shell_round_five.py
- Retrieved: 2026-11-07
- Locator: CONTEXTS, TARGETS, MODES and DATA: lines 16-38; native_commands(): lines 41-67; capability matrix: lines 92-135
- Fragment: `TARGETS = ["powershell", '& ("outside/powershell.exe")']`; `if ($target -is [System.Management.Automation.Language.StringConstantExpressionAst]) { $target.Value }`; `assert list(shell.nested_commands(command)) == [("powershell", body)]`
- Applicability: The matrix varies contexts and body representations but fixes targets to two literal forms. The oracle enumerates command nodes and then drops nonliteral target expressions.
- Counterevidence: The tests check parse errors, negative data controls, both parent versions and actual host verdict functions for their supplied literal cases.
- Validation: Record nonliteral target kinds and unresolved names, then assert capability outcomes for those records instead of omitting them.

### S5 — F6 cycle 1 research and recorded repair limits

- URL: docs/research/review-powershell-version-resolver-and-install-f6-1.md
- Retrieved: 2026-11-07
- Locator: Failed fixes: lines 17-22; hypothesis and counterevidence: lines 199-209; Result and Unresolved limits: lines 225-233
- Fragment: "An undiscovered child is represented identically to the absence of a child."; "Inspection and PowerShell executable repair now share discovery; POSIX discovery is unchanged."; "Recognizing these literal children does not prove compatibility for arbitrary computed invocation targets, script files, standard-input programs or every PowerShell 7 feature."
- Applicability: Records the earlier mechanisms, the assignment repair and the explicitly unresolved target class now implicated by round 6.
- Counterevidence: This is draft evidence. Its repair-turn results were not rerun here, and the current implementation has already corrected the old duplicated PowerShell recognizer.
- Validation: Keep literal-context regressions while requiring an explicit decision for the previously excluded target class.

### S6 — Microsoft: Windows PowerShell call operator

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_operators?view=powershell-5.1
- Retrieved: 2026-11-07
- Locator: Call operator &: lines 195-206
- Fragment: "lets you run commands that are stored in variables and represented by strings or scriptblocks."; "The call operator doesn't parse strings."
- Applicability: The reproducer supplies only the executable name through the variable and supplies options separately, matching documented invocation semantics.
- Counterevidence: A variable holding a complete command line is not equivalent: the call operator does not split that string into executable and parameters. Scriptblock targets also require separate treatment.
- Validation: Use parsing plus a harmless native fixture to distinguish variable executable invocation from unused string data and whole-command strings.

### S7 — Microsoft: CommandAst.GetCommandName

- URL: https://learn.microsoft.com/en-us/dotnet/api/system.management.automation.language.commandast.getcommandname?view=powershellsdk-7.6.0
- Retrieved: 2026-11-07
- Locator: Definition: lines 57-60; Returns: lines 74-78
- Fragment: "This command name may not be known statically, in which case null is returned."
- Applicability: The documentation gives variable-selected and module-expression targets as examples. Unknown name does not mean the command node is absent.
- Counterevidence: The API can return known names. Its unknown result alone does not prove the command is PowerShell or incompatible.
- Validation: Keep CommandAst records with null names and require a deliberate unresolved capability outcome.

### S8 — Microsoft: Windows PowerShell scope rules

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_scopes?view=powershell-5.1
- Retrieved: 2026-11-07
- Locator: Scope rules and Parent and child scopes: lines 39-61; Scope modifiers: lines 81-95
- Fragment: "An item is visible in the scope that it was created and in any child scopes, unless you explicitly make it private."
- Applicability: Variable resolution depends on scope, visibility and mutation. A global textual assignment lookup cannot establish the value used by every invocation.
- Counterevidence: Simple straight-line assignments can be analyzed conservatively. Module functions have their own session-state hierarchy, and explicit scope modifiers can change where values are read or written.
- Validation: If constant-bound target support is added, test shadowing, scoped names and mutation; unsupported cases must remain unresolved.

### S9 — Microsoft: Parser.ParseInput

- URL: https://learn.microsoft.com/en-us/dotnet/api/system.management.automation.language.parser.parseinput?view=powershellsdk-7.4.0
- Retrieved: 2026-11-07
- Locator: ParseInput(String, Token[], ParseError[]): lines 60-91
- Fragment: "Parse input that does not come from a file."; "Returns errors, if any, discovered while parsing the script."
- Applicability: Provides a structural parsing oracle with an AST, tokens and errors for supplied test text.
- Counterevidence: Parsing does not resolve runtime bindings. Grammar differences, parser failures and deployment cost still need assessment.
- Validation: Parse test payloads without invoking them; preserve errors as analysis failures rather than empty discovery.

### S10 — Microsoft: Ast.FindAll

- URL: https://learn.microsoft.com/en-us/dotnet/api/system.management.automation.language.ast.findall?view=powershellsdk-7.4.0
- Retrieved: 2026-11-07
- Locator: Definition: line 54; searchNestedScriptBlocks: lines 70-79
- Fragment: "Traverse the entire Ast, returning all nodes in the tree for which predicate returns true."; "Search nested functions and script block expressions."
- Applicability: Allows command-node enumeration beneath assignments and within nested scriptblocks when requested.
- Counterevidence: The predicate and nesting option determine coverage. Syntactic occurrence does not establish runtime reachability or target identity.
- Validation: Enumerate command occurrences before target classification and retain unresolved nodes at every inspected nesting level.

### S11 — Codex repair, fallback and refusal ordering

- URL: tool/codex_pretool.py
- Retrieved: 2026-11-07
- Locator: verdict(): lines 9-24
- Fragment: `fixed, notes = repair(payload, "codex")`; `fixed, notes = payload, []`; `answer = refusal(fixed, "codex") or checked(fixed)`
- Applicability: Codex checks the repaired payload, or the original payload after a repair exception.
- Counterevidence: A genuinely repaired executable may legitimately change compatibility. Repair failure itself does not guarantee refusal.
- Validation: Inspect updatedInput on acceptance and inject repair failure to confirm the original unresolved target is still refused.

### S12 — Claude refusal and repair ordering

- URL: tool/claude_pretool.py
- Retrieved: 2026-11-07
- Locator: verdict(): lines 38-50 and 73-75
- Fragment: `if denied := refusal(payload): return denied`; `payload, notes = repair(payload)`; `out["hookSpecificOutput"]["updatedInput"] = payload["tool_input"]`
- Applicability: Claude can refuse the original incompatible or unresolved command before repair.
- Counterevidence: This ordering differs from Codex, so their decisions on original text need not match when executable repair is possible.
- Validation: Exercise the actual verdict and validate any accepted updatedInput, preserving the host's refusal-before-repair behavior.

### S13 — Effect guard admission policy

- URL: tool/guard_grant.py
- Retrieved: 2026-11-07
- Locator: Module policy: lines 8-12; outside(): lines 291-296
- Fragment: "A program, option or form it cannot read passes"; `if kind in ("read", "top", "spawn"): return None`
- Applicability: An unknown-effect result from the general guard is not a version-compatibility refusal.
- Counterevidence: The guard can deny independently established effects outside the grant. That does not certify a child's PowerShell version.
- Validation: Demonstrate that the capability boundary refuses unresolved targets independently of the effect guard's permissive result.

### S14 — Existing bounded literal binding in the effect parser

- URL: tool/guard.py
- Retrieved: 2026-11-07
- Locator: bind() and literal(): lines 502-540; run(): lines 543-585
- Fragment: "`w` with each plain variable the script bound to a literal replaced by it, or marked unknown."; "Only an assignment that runs at this level, not in a pipeline or a branch, binds what follows it."
- Applicability: Shows existing machinery that could inform a bounded target resolver without introducing unrestricted textual substitution.
- Counterevidence: This machinery serves effect analysis, has a deliberately bounded grammar and does not provide a capability-specific admission guarantee.
- Validation: Evaluate any reused binding logic against scope, reassignment and branch cases while retaining an explicit unresolved outcome.

## Claims

- C1 (source; S1): The boundary recursively checks discovered child bodies and separately restricts recognized classic children regardless of selected parent version.
- C2 (interpretation; S1, S2, S3): For the exact variable-target reproducer, literal discovery and masking statically predict empty extraction, no recognized feature, unchanged repair and acceptance by the capability checks.
- C3 (source; S1, S2): Recognized child shells receive argument reconstruction, encoded decoding and computed-argument rejection; these mechanisms are downstream of executable discovery.
- C4 (interpretation; S1, S2, S3, S4, S5): The successive repairs expanded literal target, body, argument and command-position coverage, but retained literal-only target discovery. The assignment repair did share discovery, so the old duplicate PowerShell-recognizer diagnosis is no longer sufficient.
- C5 (source; S6): The call operator supports targets stored in variables as strings or scriptblocks; it does not parse a target string into a complete command and its parameters.
- C6 (source; S7): CommandAst.GetCommandName can return null for an existing command whose name depends on runtime values, including variables and module expressions.
- C7 (interpretation; S7, S9, S10): ParseInput exposes an AST and parse errors, while FindAll can enumerate matching nodes and optionally search nested functions and scriptblocks. These APIs do not establish runtime target identity.
- C8 (interpretation; S1, S2, S3, S4, S5, S6, S7, S9, S10): Closing this recurrence requires preserving unresolved executable targets through shared analysis and giving them an explicit capability outcome. Conservative refusal closes the reported admission gap without requiring general runtime-value resolution; parser adoption alone does not.
- C9 (interpretation; S4, S5, S10): The current context matrix and its native oracle validate literal targets while omitting nonliteral target records, leaving the unresolved-target policy untested.
- C10 (interpretation; S3, S11, S12): Codex repairs before refusal and falls back to the original payload on repair exceptions; Claude invokes refusal before repair. Acceptance checks must account for the actual payload each host evaluates.
- C11 (source; S13, S14): The separate effect parser has bounded literal binding and unknown outcomes, but its grant policy intentionally permits top and spawn effects.
- C12 (interpretation; S6, S7, S8, S13, S14): A bounded constant-target resolver may improve compatibility, but scope and mutation require conservative invalidation. Reusing effect-analysis machinery requires a separate capability policy; unsupported resolution must remain explicit.

## Core theory

PowerShell separates a command occurrence from the runtime value that selects its target. The call operator supports commands stored in variables, while its target string is not itself reparsed as a complete command line. Microsoft's CommandAst documentation explicitly allows a statically unknown command name, including variable and module-expression examples. ParseInput and FindAll can expose command structure without resolving runtime bindings. Therefore an AST can identify an invocation while leaving its target unknown; treating that unknown as no invocation is an implementation error, not a parser guarantee. Rejecting unresolved capability decisions is the proposed policy derived from these facts. [C5, C6, C7, C8]

## Applicability

Extend the shared analysis used by shell.py and PowerShell executable repair so it retains command occurrences, target expression kinds, source spans, resolution status and argument/body status. A native AST is the strongest available structural oracle; using it in production requires measuring availability and cost. Preserve nonliteral command nodes instead of filtering them away. Resolve supported literals and grouped literals, inspect literal scriptblock bodies recursively, and retain an explicit unresolved outcome for variable or computed targets. The first repair should refuse an unresolved executable target that cannot be excluded as a child PowerShell invocation, under either parent version, with a diagnostic asking for a literal target. Supporting constant-bound variables is optional subsequent work requiring a documented, conservative subset; it is unnecessary to close the reported bypass. Preserve existing argument reconstruction, encoded decoding and child-version checks for resolved shells. Do not rewrite a variable assignment merely because its value resembles an executable. Apply the capability decision to the final text at launch and refusal boundaries, including Codex's original-payload fallback after repair failure. An unresolved outcome must survive as an explicit capability refusal rather than disappearing into an empty list or an uncaught parser exception. Keep the general effect guard's separate permissive policy unchanged. [C3, C7, C8, C10, C11, C12]

## Hypothesis

The persistent cause is a missing unresolved-target outcome. Discovery answers only which literal PowerShell children it recognizes; callers consume an empty enumeration as if it established the absence of incompatible children. Earlier fixes enlarged the recognized subset without changing that inference. Sharing discovery improved consistency, but also made inspection and repair share the same omission. The next correction must distinguish no relevant invocation, resolved invocation and unresolved executable target, then prevent unresolved targets that could select a classic child from silently passing the capability boundary. [C2, C8, C9]

## Counterevidence

- The earlier repairs closed real gaps. Grouped literals, encoded bodies, quoted options, reconstructed tails and literal assignment contexts now have implementation support and regression evidence. The hypothesis concerns the remaining inference from missing discovery, not wholesale failure of those repairs. [C3, C4]
- The reported assignment is simple enough for bounded constant propagation. Rejecting it is conservative and can also reject valid dynamic commands; that compatibility cost must be explicit. It does not show that every variable target is incompatible. [C5, C8, C12]
- The existing effect parser already has limited literal binding and an unknown-effect representation. Some analysis machinery may be reusable, but its admission policy intentionally allows unknown effects and does not establish child-version compatibility. [C11, C12]
- An AST alone cannot identify every runtime target, establish that every discovered node executes, or reconstruct every native argument transmission rule. Parser adoption without an unresolved policy would repeat the failure. [C6, C7, C8]
- Actual host verdicts can differ because Claude refuses before repair and Codex repairs before refusal. Other independent checks might also deny a payload. No complete host execution was performed here. [C10]

## Alternatives

- Add variable substitution for the exact `$target = 'powershell'` example. This could support a narrow convenience case, but textual substitution does not establish binding across reassignment, branches, scopes or intervening mutation. Establish the unresolved fallback first; add proven constant cases separately. [C8, C12]
- Adopt CommandAst.GetCommandName() and skip null results. Microsoft explicitly documents null for runtime-dependent targets; this preserves the same omission behind a different parser. [C6, C8]
- Search every quoted string for powershell or Get-Error. This would misclassify data, including a variable containing a shell name that is never invoked and a child printing the text Get-Error. Existing safe-data controls require structural distinction. [C3, C4, C5]
- Select PowerShell 7 for the parent. A variable whose runtime value is powershell can still select Windows PowerShell 5.1. Parent selection does not establish child identity. [C1, C2, C5]
- Rely on guard.verdict() or change all unknown effects to denial. The guard deliberately admits top and spawn effects. Its grant policy is separate from version compatibility, so changing it is broader than F6 and unnecessary. Reusing analysis would require preserving unresolved information and applying a distinct capability policy. [C11, C12]
- Resolve targets by executing their expressions during inspection. That introduces execution before the capability decision and still does not prove the value will remain unchanged. Prefer parsing and conservative resolution; any unsupported expression remains unresolved. [C6, C7, C12]

## Experiment

This is the original read-only proposal; the repair-turn observations are recorded under Result.

In the subsequent implementation session, first record extraction, feature detection, classic-child classification, executable repair, resolve(), launch(), refusal() and both actual host verdicts for the exact reproducer under selected parent majors 5 and 7. Static prediction: nested_commands() is empty, seven_only() is empty, classic_seven_only() is false, repair leaves the command unchanged, and the capability checks permit it. Independently parse the supplied text without executing it. Record every CommandAst with its target kind and extent, including null names; the variable-selected command must remain visible. Extend the current oracle rather than retaining its StringConstantExpressionAst-only output. After correction, the reproducer must either resolve to a checked classic child and be denied, or produce an explicit unresolved-target refusal; empty-compatible classification is a failure. Cross direct and grouped variables with Command, quoted Command, split tails, EncodedCommand and nested child bodies. Add reassignment, branch-dependent binding, scoped shadowing, unknown external values, computed target expressions and a classic child nested under a literal pwsh target. Preserve passing controls for unused shell-name assignments, quoted command examples, ordinary arguments, safe literal children and inspected literal scriptblocks. Under a conservative unresolved policy, safe but unresolved variable targets may be refused; assert that documented outcome explicitly. If constant-variable support is implemented, require safe resolved targets to pass and invalidated bindings to become unresolved. Exercise actual host payload forms and inspect updatedInput whenever accepted. Force repair and analysis failures to verify that fallback cannot bypass the capability refusal. A separate harmless native fixture using an absolute executable path and a body that prints a marker and exits 17 can confirm variable invocation semantics. Refute the hypothesis if current discovery already preserves and rejects the target, or native parsing does not identify the invocation. No experiments or tests were executed in this read-only research turn. [C2, C7, C8, C9, C10, C12]

## Result

Observed in the repair commit titled `Preserve hashtable data and refuse unresolved PowerShell targets`: before repair, the reported variable call yielded no child and no feature, executable repair left it unchanged, and resolve, launch, refusal and both actual verdict functions accepted it under selected parent versions 5 and 7. `test_native_oracle_retains_unknown_target_kinds_without_execution` now preserves null-name command records, expression kinds and exact target spans. `test_native_variable_fixture_preserves_output_and_failure_status` executes only a harmless absolute-path fixture, printing its marker and returning status 17. The shared analysis now distinguishes literal targets, inspected literal scriptblocks and unresolved executable targets; unresolved targets are refused under either parent version, without attempting constant propagation. `test_unresolved_executable_targets_are_refused_by_every_boundary` covers 324 target/binding/body/version combinations and both host payload forms. `test_unresolved_child_nested_under_literal_shell_is_also_refused`, `test_safe_but_unresolved_targets_have_the_documented_refusal` and `test_failures_cannot_turn_an_unresolved_target_into_host_acceptance` cover recursive inspection, the deliberate compatibility restriction and repair/analysis failures. Existing literal, encoding and safe-data controls remain. The documented unresolved input is inventoried without an executable form. This remains draft evidence for independent review, not a verified finding or shared rule.

The final native target oracle retains 18 unresolved target occurrences across call and dot operators, including computed string expressions and subexpressions. Both harmless native variable-invocation fixtures preserve marker output and status 17. `test_dot_invocation_uses_the_same_unresolved_policy` covers 18 additional target/version cases. These are observed parser and fixture results, not executions of the supplied incompatible bodies.

## Unresolved limits

This remains draft research for F6 cycle 2 of 2. The Result records repair-turn observations; the original experiment was a proposal. Review history is supplied evidence rather than an independently certified ledger. The conservative policy also refuses safe variable-selected executables and variable-held scriptblocks: callers must provide literal targets. Constant propagation is not implemented. Native-parser comparisons cover the installed version and tested cases. No result proves compatibility for aliases, function shadowing, arbitrary process-creation APIs, external scripts, stdin programs or every PowerShell 7 feature. [C4, C6, C7, C8, C12]
