# Review research — F6, cycle 1

Draft research before the fix of review round 5 of PR #307, written 2026-11-07. It is not a verified fact or a shared rule: the review reads it with the fix.

Finding: F6. Invariant: child-shell capability enforcement.

## Problem

F6's invariant is child-shell capability enforcement. F4's separate invariant is catalogue coverage; the F7 correction to the earlier F4 draft records that distinction.

F6 repeatedly breaks the same boundary: executable child PowerShell code must be checked against the child's version, even when its parent is PowerShell 7, while ordinary quoted data remains data. The current reproducer, $result = powershell -Command 'Get-Error', places a literal child invocation on an assignment's right-hand side. Command discovery misses it, so neither recursive capability inspection nor executable repair reaches it. The code therefore predicts acceptance of the unchanged Windows PowerShell child under either selected parent version. This is a static finding, not a runtime observation from this turn. [C1, C2]

Trigger: recurrence, rounds 2, 3, 4, 5.

- [P1] [P1] tool/common/shell.py:106 — assignment-position child shells are not inspected / when a literal `powershell` invocation is an assignment value / a 7-only body runs under Windows PowerShell 5.1

## Failed fixes

- Round 2, reported at 0fb8ffa: the grouped-target correction exposed literal call targets inside transparent parentheses. Current code and regressions cover grouping depths and independently selected parent versions. That repair addressed target spelling after the call operator; it did not establish command discovery across PowerShell statement contexts. [C3, C9]
- Round 3, reported at 3304b40: the encoded-command correction added Base64/UTF-16LE decoding, recursive inspection, and rejection of unreadable payloads. This closed another command-body representation, but decoding only happens after the wrapper regex discovers the child executable. Assignment-position children still never reach decoding or inspection. [C2, C4]
- Round 4, reported at 0803a14: argument parsing replaced inspection of raw option spellings, allowing quoted options and reconstructing split string-valued command tails. Current regressions verify these improvements. However, arguments() is called only after nested_commands() matches the executable. The unchanged punctuation-based discovery regex remains the entry gate. [C2, C5]
- Round 5, reported at 20823ef: no correction is reported yet. Both nested_commands() and command_repair.POWERSHELL omit assignment context. These are separate recognizers with overlapping limitations, so repair is not an independent backstop. Existing repair evidence varies target grouping, options, bodies and parent versions, but does not cross those variations with assignment-position invocation. [C2, C9]

## Sources

### S1 — PowerShell command discovery and launch boundary

- URL: tool/common/shell.py
- Retrieved: 2026-11-07
- Locator: command_syntax and nested_commands, lines 79-121; classic_seven_only and seven_only, lines 124-147; resolve and launch, lines 235-265
- Fragment: wrapper = r"(?:^|[;&|{(\n])\s*(?:&\s*)?(?:[^\s;&|(){}]+[/\\])?(?P<program>powershell|pwsh)(?:\.exe)?\s+"; "An explicit Windows PowerShell child stays on 5.1 even under a 7 host."; "for _, body in nested_commands(text):"
- Applicability: Discovery controls whether child bodies reach recursive feature checks. launch checks explicit classic children independently of the selected parent's version.
- Counterevidence: The file already implements grouping normalization, argument reconstruction, encoded decoding and recursive inspection for discovered children.
- Validation: Compare standalone and assignment-position extraction and launch decisions under selected parent versions 5 and 7.

### S2 — Executable repair and compatibility refusal

- URL: tool/command_repair.py
- Retrieved: 2026-11-07
- Locator: POWERSHELL, _pwsh and refusal, lines 17-65
- Fragment: POWERSHELL = re.compile(r"(?i)(^|[;&|(\n]\s*)powershell(?:\.exe)?(?=\s|$)"); "if not POWERSHELL.search(bare): return command"; "if shell.classic_seven_only(command):"
- Applicability: The independent repair recognizer also lacks assignment context. refusal delegates classic-child inspection to the shared shell module.
- Counterevidence: Repair deliberately masks data and avoids switching executables when only 5.1 is selected; preserving those distinctions is necessary.
- Validation: Assert that confirmed assignment-position executable spans are handled while equals signs in arguments and quoted data remain untouched.

### S3 — PowerShell string masking and literal native arguments

- URL: tool/common/powershell_syntax.py
- Retrieved: 2026-11-07
- Locator: arguments, lines 22-64; syntax, lines 67-146
- Fragment: "Read literal native arguments until the outer command ends; refuse computed values."; "if char in '$({[':" raises ValueError; "A same-length code view and quoted spans, retaining only string subexpressions."
- Applicability: Explains why the quoted Get-Error body disappears from the outer feature scan and why argument improvements cannot help an undiscovered executable.
- Counterevidence: String subexpressions remain executable in the syntax view, and computed arguments to discovered children are already rejected.
- Validation: Preserve quoted-data controls and unresolved-argument rejection while changing command discovery.

### S4 — Grouped target and child-version regressions

- URL: tool/test_shell_round_two.py
- Retrieved: 2026-11-07
- Locator: Grouped literal and classic child tests, lines 69-96
- Fragment: command = "& " + target + (" -Command 'Get-Error'" if "powershell" in program else ""); @pytest.mark.parametrize("major", [5, 7]); def test_grouped_safe_child_and_literal_shell_text_remain_allowed
- Applicability: Documents the grouped-target repair, parent-independent classic-child checks and safe-data constraints.
- Counterevidence: Target grouping and body variants are exercised after a leading call operator, not as bare assignment-position children.
- Validation: Retain these regressions and cross their invocation forms with confirmed assignment contexts.

### S5 — Encoded child-command regressions

- URL: tool/test_shell_round_three.py
- Retrieved: 2026-11-07
- Locator: Encoded command tests, lines 41-92
- Fragment: @pytest.mark.parametrize("target", ["powershell", '& ("outside/powershell.exe")']); command = f"{target} -{option} {quote}{body}{quote}"; def test_unreadable_encoded_bodies_are_denied_instead_of_skipping_inspection
- Applicability: Covers encoded option variants, both parent versions, malformed payloads and nested encoded children.
- Counterevidence: The target matrix does not put the executable on an assignment RHS.
- Validation: Apply encoded-body equivalence and malformed-payload rejection to children found in assignment contexts.

### S6 — Quoted options, reconstructed tails and unresolved arguments

- URL: tool/test_shell_round_four.py
- Retrieved: 2026-11-07
- Locator: Quoted options and split command tails, lines 30-54; computed arguments and safe data, lines 69-89
- Fragment: @pytest.mark.parametrize("target", ["powershell", "& 'powershell.exe'"]); assert list(shell.nested_commands(command)) == [("powershell", "Get-Error")]; def test_computed_native_child_arguments_are_denied_as_unresolved
- Applicability: Shows what the latest argument-aware correction tests and which safety distinctions must survive.
- Counterevidence: The tested command starts with the target; assignment-position discovery is not exercised.
- Validation: Run equivalent body and option tests after structural discovery across command contexts.

### S7 — Microsoft PowerShell assignment operators

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_assignment_operators
- Retrieved: 2026-11-07
- Locator: Syntax; Using the assignment operator
- Fragment: "The value can be a single value, an array of values, or a command, expression, or statement."
- Applicability: A command on the RHS is a documented assignment form. The page also illustrates command-pipeline assignment and chained assignments.
- Counterevidence: An assignment value can also be data; an equals sign alone does not identify an invocation.
- Validation: Use the native parser to distinguish assignment RHS CommandAst nodes from literal-valued assignments.

### S8 — Microsoft PowerShell parsing modes

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parsing?view=powershell-5.1
- Retrieved: 2026-11-07
- Locator: Expression mode; Argument mode, especially lines 79-103
- Fragment: "When parsing, PowerShell first looks to interpret input as an expression. But when a command invocation is encountered, parsing continues in argument mode."
- Applicability: Command position depends on parsing context. Argument mode separately treats strings, variables, parentheses, script blocks and subexpressions.
- Counterevidence: Special syntaxes and native argument transmission prevent a single punctuation rule or blanket quote removal from reproducing all parsing behavior.
- Validation: Compare positive command contexts and negative argument/data controls with the PowerShell parser.

### S9 — Windows PowerShell command-line interface

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe?view=powershell-5.1
- Retrieved: 2026-11-07
- Locator: -Command, lines 81-114; -EncodedCommand, lines 124-126
- Fragment: "The value of Command can be `-`, a script block, or a string."; "The string must be formatted using UTF-16LE character encoding."
- Applicability: The second quotation concerns EncodedCommand. For string-valued Command, subsequent arguments contribute to executed command text; ScriptBlock and standard-input forms have separate behavior.
- Counterevidence: ScriptBlock values are recognized only when called from a PowerShell host. The string-tail sentence names pwsh on the powershell.exe page, so concrete native transmission still warrants version-specific validation.
- Validation: Retain separate ordinary-string and encoded-body tests; do not apply string-tail reconstruction indiscriminately to stdin or ScriptBlock forms.

### S10 — PowerShell Parser.ParseInput API

- URL: https://learn.microsoft.com/en-us/dotnet/api/system.management.automation.language.parser.parseinput?view=powershellsdk-7.4.0
- Retrieved: 2026-11-07
- Locator: ParseInput(String, Token[], ParseError[])
- Fragment: "Parse input that does not come from a file."; "Returns errors, if any, discovered while parsing the script."
- Applicability: Provides an AST, tokens and parse errors for supplied text, suitable for a parsing-only comparison rather than executing the finding payload.
- Counterevidence: The API does not resolve runtime values or guarantee identical grammar acceptance across PowerShell versions.
- Validation: Parse assignment and standalone equivalents under available supported versions; retain and assess parse errors.

### S11 — PowerShell Ast.FindAll API

- URL: https://learn.microsoft.com/en-us/dotnet/api/system.management.automation.language.ast.findall?view=powershellsdk-7.6.0
- Retrieved: 2026-11-07
- Locator: Definition; searchNestedScriptBlocks parameter; Applies to
- Fragment: "Traverse the entire Ast, returning all nodes in the tree for which predicate returns true."; "Search nested functions and script block expressions."
- Applicability: Allows command-node enumeration beneath assignments and optionally within nested functions and script blocks; the API lists Windows PowerShell 5.1 support.
- Counterevidence: Syntactic presence does not prove that a node executes on a particular control-flow path; traversal policy must be deliberate.
- Validation: Compare discovered literal child command nodes and spans with the implementation's invocation records.

### S12 — PowerShell CommandAst.GetCommandName limits

- URL: https://learn.microsoft.com/en-us/dotnet/api/system.management.automation.language.commandast.getcommandname?view=powershellsdk-7.6.0
- Retrieved: 2026-11-07
- Locator: Definition and runtime-dependent examples, lines 57-60
- Fragment: "This command name may not be known statically, in which case null is returned."
- Applicability: Prevents treating unresolved command names as proof that no child invocation exists.
- Counterevidence: The documentation illustrates variable-based targets and an expression returning a module; it does not claim that literal bare targets are unresolved.
- Validation: Test bare targets, grouped constant targets and computed targets separately; preserve an explicit unresolved outcome.

### S13 — Codex repair-before-refusal ordering

- URL: tool/codex_pretool.py
- Retrieved: 2026-11-07
- Locator: verdict, lines 9-24
- Fragment: fixed, notes = repair(payload, "codex"); answer = refusal(fixed, "codex") or checked(fixed)
- Applicability: The actual Codex decision concerns the repaired payload, including any child executable replacement.
- Counterevidence: Repair exceptions retain the original payload, so unchanged incompatible children must still be caught by refusal.
- Validation: Inspect updatedInput and the final child executable when testing the actual verdict function.

### S14 — Claude refusal-before-repair ordering

- URL: tool/claude_pretool.py
- Retrieved: 2026-11-07
- Locator: verdict, lines 38-50
- Fragment: if denied := refusal(payload): return denied; payload, notes = repair(payload)
- Applicability: Claude can reject an incompatible explicit classic child before executable repair is attempted.
- Counterevidence: This differs from Codex ordering, so identical assertions about original payloads under a selected version 7 are not automatically appropriate.
- Validation: Test the actual Claude verdict alongside Codex and direct launch, accounting for which command each evaluates.

### S15 — Earlier draft research on child argument reconstruction

- URL: docs/research/review-powershell-version-resolver-and-install-f4-1.md
- Retrieved: 2026-11-07
- Locator: Historical tree 20823ef, Applicability, Hypothesis and Result, lines 143-169; the current F4 page is the F7 provenance correction.
- Fragment: "Replace the single-span child-body extraction"; "The proposed hypothesis addresses the additional child-argument gap; the named F4 finding is separately covered"; "This result is draft evidence for independent review, not a verified finding or shared rule."
- Applicability: Historical F6 argument-reconstruction evidence that was misfiled under F4. It explains the prior repair but does not establish F4 catalogue coverage; F7 corrects that attribution in the current F4 page.
- Counterevidence: It is draft historical evidence, not an independently verified F6 round history. Current code already joins the argument tail.
- Validation: Keep split-tail regressions while independently testing the earlier command-discovery stage.

## Claims

- C1 (source; S1): The current boundary recursively inspects discovered child bodies and separately rejects recognized 7-only code in explicit Windows PowerShell children, including under a selected PowerShell 7 parent.
- C2 (interpretation; S1, S2, S3): For the reported bare assignment-position child, both executable recognizers miss the invocation; quoted-body masking then leaves no recognized Get-Error feature. This statically predicts acceptance without repair.
- C3 (interpretation; S1, S4): Grouped-target normalization and its regressions cover literal call targets, child-version independence and safe literal data, but do not establish bare assignment-position discovery.
- C4 (interpretation; S1, S5, S9): Encoded-body decoding and regressions handle a distinct command representation, including malformed input and nesting, but operate only after executable discovery.
- C5 (interpretation; S1, S3, S6): The current argument-aware stage supports quoted options, reconstructed command tails and unresolved-argument rejection; it cannot inspect a child that discovery never yields.
- C6 (source; S7, S8, S9): PowerShell permits command-valued assignments, distinguishes expression and argument parsing modes, and gives Command and EncodedCommand different body representations and conditions.
- C7 (interpretation; S9, S10, S11, S12): The official parser exposes ASTs and parse errors, AST traversal can enumerate nested command nodes, and command names can remain statically unknown. These APIs do not alone reconstruct every runtime child invocation.
- C8 (interpretation; S1, S2, S3, S7, S8, S10, S11, S12): A shared context-aware invocation representation, preserving source spans and unresolved states, addresses the recurring discovery/interpretation mismatch more directly than independently expanding punctuation regexes.
- C9 (interpretation; S4, S5, S6): The inspected repair regressions vary grouping, encoded bodies, quoted options and selected parent versions while keeping child targets at command start or after a call operator; they lack the assignment-context cross product relevant to round 5.
- C10 (source; S13, S14): Claude invokes refusal before repair, whereas Codex repairs first and invokes refusal on the resulting payload.
- C11 (interpretation; S1, S6, S15): The earlier F4 draft investigated incomplete child argument tails and records prior results; the current implementation already reconstructs those tails, so that hypothesis does not explain the remaining assignment-position discovery gap.
- C12 (interpretation; S1, S2, S4, S5, S6, S7, S8, S9, S10, S11, S12, S13, S14): A native-parser comparison plus context-equivalence regressions, negative data controls and actual host-verdict checks can distinguish a complete assignment-position repair from another example-specific patch.

## Core theory

PowerShell syntax determines whether text is an expression, a command invocation or a command argument. An assignment value may itself be a command or statement; encountering a command switches parsing into argument mode. Quoting therefore cannot be interpreted independently of syntactic role. The executable's CLI adds another interpretation step: Command supports string, ScriptBlock and standard-input forms, whereas EncodedCommand carries UTF-16LE command text. [Assignment operators](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_assignment_operators), [Parsing](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parsing?view=powershell-5.1), [Windows PowerShell CLI](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe?view=powershell-5.1). These facts support structural discovery followed by argument interpretation; that design recommendation is our interpretation. [C6, C8]

## Applicability

Replace the PowerShell-dialect command-position guesses with one shared invocation enumerator used by capability inspection and executable repair. It should identify actual command positions, including assignment RHS statements; preserve target source spans; normalize supported literal call targets; and then use the existing argument reconstruction, encoded-body decoding and recursive child-version checks. Distinguish resolved invocations, unresolved candidate invocations and ordinary data. Preserve rejection of unresolved child arguments rather than turning extraction failure into an empty compatible result. Repair only confirmed executable spans, then validate the command that will execute. Keep POSIX parsing separate. The official PowerShell AST is a suitable discovery implementation or differential-test oracle: ParseInput returns syntax and errors, and FindAll can enumerate CommandAst nodes beneath assignments and nested blocks. GetCommandName can return null, so constant grouped targets still need deliberate handling and computed targets need an explicit unresolved policy. Parser errors must not silently become an empty invocation list. [C7, C8]

## Hypothesis

The recurring cause is incomplete reconstruction of invocation semantics in successive stages. Earlier fixes expanded literal-target normalization and argument/body interpretation, while command discovery still approximates executable position with a punctuation list. An undiscovered child is represented identically to the absence of a child. Quoted body masking then removes the only visible Get-Error text, producing an empty capability result. A second recognizer in command repair makes the same context mistake. Adding '=' would address the reported example, but would not by itself distinguish a genuine assignment RHS from an ordinary argument containing '='. [C2, C6, C8]

## Counterevidence

- The earlier fixes did solve substantive parts of the problem: grouped literal targets, encoded bodies, quoted options, split command tails and rejection of computed child arguments all have current regressions. The remaining defect is before these stages, rather than proof that their implementations are wholly ineffective. [C3, C4, C5]
- A narrow assignment-aware repair may be sufficient for the supported literal syntax without introducing a runtime AST dependency. The research establishes a discovery gap, not that a particular parser architecture is mandatory. [C2, C8]
- AST discovery does not establish runtime executable identity for aliases, functions, computed names or arbitrary expressions. GetCommandName explicitly permits an unknown result. An AST alone also does not reconstruct the native child's received command string. [C6, C7]
- Host ordering matters: Claude checks refusal before repair; Codex repairs first and checks the repaired payload. Under a selected PowerShell 7 parent, a successfully rewritten child can legitimately receive a different decision from an unchanged powershell child. [C10]
- The earlier F4 research page investigated split command tails and records a prior experiment. Current code already joins that tail. Its old hypothesis should not be reused as the explanation for the assignment-position recurrence, and its recorded results were not rerun here. [C11]

## Alternatives

- Add '=' to both regex character classes. This is a possible immediate correction, but require syntactic-context validation: Write-Output x=powershell -Command 'Get-Error' contains an equals sign without launching a child PowerShell. A punctuation-only expansion risks classifying or rewriting data. Prefer a shared context-aware discovery rule with an AST comparison. [C6, C8, C12]
- Expand only the option decoder or seven_only feature patterns. Neither addresses this reproducer: the existing argument parser and Get-Error detector never receive the hidden child body. [C2, C5]
- Select PowerShell 7 for the parent and rely on that upgrade. An unchanged explicit powershell invocation still launches Windows PowerShell; parent selection cannot supply its missing capabilities. [C1, C2]
- Reject every nested PowerShell invocation or search all quoted text for feature names. This conflicts with existing safe-child and literal-data controls. Preserve the executable/data distinction. [C3, C4, C5]
- Adopt a full runtime AST service immediately. It could remove punctuation-based discovery, but startup cost, parser-version behavior and unresolved expressions need measurement. First use the installed parser as an independent oracle for the bounded literal grammar; choose runtime integration or a validated local recognizer from that evidence. [C7, C8, C12]

## Experiment

This section preserves the original proposed experiment. The repair-turn observations appear under Result.

Before editing, record nested_commands(), seven_only(), classic_seven_only(), launch('gate', ...), refusal() and executable repair for the exact assignment reproducer. Static prediction: extraction is empty, seven_only() is empty, classic_seven_only() is false, launch accepts under mocked parent versions 5 and 7, and repair leaves powershell unchanged. Compare with the same child at command start. Independently parse both strings with PowerShell Parser.ParseInput and enumerate CommandAst nodes; a powershell command beneath the assignment should appear without executing the supplied code. Confirm actual assignment execution separately with a harmless body such as Write-Output F6-child-ran; exit 17, comparing captured output and child status with the standalone equivalent. Then build a context matrix: simple, compact, typed, chained and compound assignments; parentheses, subexpressions and script blocks; bare and grouped literal targets; quoted and unquoted options; Command, split Command tails and EncodedCommand. Admit each generated positive case only after the native parser confirms its command structure. Include negative controls for quoted shell text, quoted path assignments, comments and Write-Output x=powershell -Command 'Get-Error'. After the fix, supported literal forms must yield the same reconstructed child body and capability decision across contexts. launch() must reject an unchanged classic child containing a recognized 7-only feature under either parent version. Exercise the actual Claude and Codex verdict functions as well as refusal(): on a 5.1-only selection the incompatible child must be denied; with 7 selected, acceptance requires that the executed child really has been rewritten to 7, otherwise denial remains required. Preserve all grouped-target, encoded, malformed-encoding, quoted-option, split-tail and safe-data regressions. Refute or revise the hypothesis if native parsing finds no assignment-position child, or if current extraction already returns it. These checks are proposed and have not been executed. [C1, C2, C7, C9, C10, C12]

## Result

Observed in the repair commit titled `Recognize assignment-position child shells and correct research provenance`: before the fix, the reported assignment produced no child, no recognized feature and no classic-child restriction; launch accepted it under selected parent versions 5 and 7, both refusal paths passed it, and executable repair left it unchanged. The standalone control was inspected and refused. `test_native_parser_confirms_the_context_matrix_without_execution` parses the supplied strings without executing them; the native parser confirms the generated positive command contexts and rejects the negative controls as child invocations. `test_assignment_and_standalone_fixture_children_preserve_output_and_status` runs only a harmless fixture body: both forms print the same line and preserve status 17. `test_literal_children_have_context_independent_capability_decisions` checks extraction, features, direct launch, refusal and both actual host verdict functions across contexts, targets, body modes and selected parent versions. An unchanged incompatible classic child is denied; acceptance on 7 requires a rewritten child. `test_noncommand_equals_and_literal_shell_data_remain_unchanged` preserves argument and literal controls. Inspection and PowerShell executable repair now share discovery; POSIX discovery is unchanged. F7 separately corrects the earlier F4 record's identity. This remains draft evidence for independent review, not a verified finding or shared rule.

The final differential table contains 132 positive scripts across 22 contexts, two target forms and three body modes, plus nine negative controls. The additional return, throw, exit, foreach-collection and dictionary-value contexts were admitted only after native parsing confirmed their command nodes. Their supplied child bodies were never executed. The 264 parent-version/context cases exercise both actual host verdict functions; the native execution experiment uses only the harmless fixture described above.

## Unresolved limits

This remains draft research for F6 cycle 1 of 2. Round history comes from the supplied review messages; it is not an independently certified ledger. The Result records repair-turn experiments, distinct from the original read-only research. The bounded literal assignment grammar is compared with the installed Windows PowerShell parser; runtime AST service cost and every parser-version combination are not measured. Recognizing these literal children does not prove compatibility for arbitrary computed invocation targets, script files, standard-input programs or every PowerShell 7 feature. Those limits remain explicit rather than an inferred universal guarantee.
