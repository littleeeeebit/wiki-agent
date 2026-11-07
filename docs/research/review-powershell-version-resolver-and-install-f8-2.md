# Review research — F8, cycle 2

Draft research before the fix of review round 22 of PR #307, written 2026-11-07. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F8 breaks the compatibility promise that proved PowerShell 5.1 forms remain executable. The current source deterministically predicts refusal for `$options=@{InputObject=@((1,2)); Process={Write-Output ok}}; ForEach-Object @options`: binding discovery retains the options map, but array decoding cannot interpret the grouped comma expression. The exception propagates through both capability traversals, affecting explicit ps5 and pwsh7 resolver forms before launch. This is a static trace, not an executed reproduction. [C1, C2]

Trigger: recurrence, rounds 7, 18, 19, 20, 22.

- [P1] [P1] tool/common/powershell_splats.py:88 — grouped comma expressions are rejected inside collected array statements / when a compatible splat contains `@((1,2))` / a valid PowerShell 5.1 command is denied

## Failed fixes

- Round 7 corrected blanket refusal of variable-selected executables by proving straight-line literal bindings. That restored the reported executable forms, but binding provenance and recursive collection-value interpretation remained separate concerns. The earlier refusal policy had addressed uncertain child-shell admission without preserving the compatible constant-bound cases. [C3]
- Round 18 added construction-time resolution of captured literal values, including script blocks. It repaired capture provenance; nested literal maps still lacked a decoding branch. Round 19 added recursive map validation, preserving nested blocks and keys as data, but array decoding still assumed comma-delimited contents. These histories are reconstructed from the supplied findings and the prior research record, rather than independently inspected historical commits. [C3]
- Round 20 consolidated nested and positional array decoding and added statement separators, native checks, capture contexts and callback controls. The prior research records successful focused checks. The current recurrence does not show that consolidation failed: its shared helper still recognizes array-valued expressions only through explicit @(...). A grouped comma expression reaches that helper after grouping is stripped and falls outside its grammar. [C2, C4]
- Round 22 has no reported correction. Its failing composition was absent from the inspected matrices: they cover comma expressions, statement arrays, nested @() and grouping around empty @(), but not grouping around a comma expression. More contexts using the same fixed VALUES list could not expose that missing expression form. [C4, C5]

## Sources

### S1 — Repository PowerShell compatibility promise

- URL: docs/chat-setup.md
- Retrieved: 2026-11-07
- Locator: Lines 19–25
- Fragment: A PC with only Windows PowerShell 5.1 can use the compatible command forms and offers `PowerShell 7 설치`.
- Applicability: Defines the compatibility invariant F8 breaks.
- Counterevidence: Does not promise static resolution of arbitrary dynamic commands.
- Validation: Proved compatible fixtures resolve unchanged through both gate forms.

### S2 — Current splat value and array decoder

- URL: tool/common/powershell_splats.py
- Retrieved: 2026-11-07
- Locator: map_entries(), array_values(), value_span(), literal(); lines 56–145
- Fragment: if not text.startswith("@(") or not enclosed(text[1:], "(", ")"): return None ... elements = list(parts(first, last, ",")) ... nested = array_values(*elements[0]) ... if enclosed(source[start:end], "(", ")"): return value_span(start + 1, end - 1) ... if len(names) != 1 or value is None: raise Refusal
- Applicability: Shows the exact failure sequence and the shared nested/positional array consumer.
- Counterevidence: The splitter respects nesting and the decoder already handles explicit nested @() arrays; the missing form is narrower.
- Validation: Grouped comma values decode with correct shapes and source spans, while unsupported expressions remain unresolved.

### S3 — Literal binding provenance

- URL: tool/common/powershell_syntax.py
- Retrieved: 2026-11-07
- Locator: bound_span(), lines 28–84
- Fragment: if enclosed(source, "(", ")"): return literal(start + 1, start + len(source) - 1) ... if enclosed(source, "{", "}") or container.startswith("@") and (enclosed(container[1:], "{", "}") or enclosed(container[1:], "(", ")")): return start, start + len(source) ... # Only an unevaluated assignment/return prefix may precede this call in the current statement.
- Applicability: Explains why the outer map is retained without its internal expression being decoded; also bounds root assignment support.
- Counterevidence: A retained container span is provenance evidence, not proof that every contained expression is supported.
- Validation: Observe successful outer-map binding before decoding failure; retain existing invalidation behavior.

### S4 — Capability inspection and resolver propagation

- URL: tool/common/shell.py
- Retrieved: 2026-11-07
- Locator: invocations(), lines 177–225; nested_commands() through seven_only(), lines 229–279; resolve(), lines 367–385
- Fragment: function_only = functions.define(program, text[item.target_end:], start) ... for nested in invocations(text): ... if command is None or form == "ps5" and seven_only(command): ... if form != "bash" and classic_seven_only(command):
- Applicability: Both PowerShell resolver forms reach invocation analysis, which reaches splat decoding.
- Counterevidence: An analysis exception does not establish native engine incompatibility.
- Validation: Exercise explicit resolver forms and actual boundary outcomes, rather than relying on platform-selected defaults.

### S5 — Consumer of decoded parameter shapes and callback spans

- URL: tool/common/powershell_functions.py
- Retrieved: 2026-11-07
- Locator: callback_arguments(), lines 224–286
- Fragment: for name, a, b, code, values in parameters(self.source, position, splat[1]): ... literal = values and re.fullmatch(r"[\s(@),0]*", value) ... # Pipeline binding can execute a ScriptBlock input value, but not an array of blocks. ... self.splatted.update(values)
- Applicability: Decoded shape and original callback coordinates affect command-binding analysis downstream.
- Counterevidence: Acceptance of a literal container alone does not establish its parameter conversion or execution role.
- Validation: Keep callback spans and order correct; retain array distinctions and nested-map data roles.

### S6 — Current statement-array regression matrix

- URL: tool/test_shell_array_statements.py
- Retrieved: 2026-11-07
- Locator: VALUES and CONTEXTS, lines 14–24; COLLECTED, lines 25–37; parser, native, boundary and negative tests
- Fragment: VALUES = ["1,2", "1;2", "1\n2", "1\r\n2", ... "@(1;2);3"] ... ("", "@((@()); BLOCK)", ["0"]) ... # Statement-array checks also exercise explicit ps5/pwsh7 forms through resolve().
- Applicability: The inspected matrices cover multiple separators and grouped empty arrays, but omit grouped comma expressions. The final sentence describes the test body rather than quoting a source comment.
- Counterevidence: Existing checks meaningfully test output cardinality, callbacks, boundaries and native behavior; the missing dimension is expression composition.
- Validation: Add recursive grouping/comma cases and assert nested shape, not only acceptance or joined output.

### S7 — F8 cycle-1 research and attributed repair observations

- URL: docs/research/review-powershell-version-resolver-and-install-f8-1.md
- Retrieved: 2026-11-07
- Locator: Failed fixes, lines 15–18; Result and limits, lines 196–212
- Fragment: The repair shares one nesting-aware literal-array decoder between nested values and positional splats ... the focused suite in `tool/test_shell_array_statements.py` reported `174 passed` ... These observations ... do not establish that arbitrary statement outputs or side effects are statically resolvable. ... Historical commit snapshots for rounds 18 and 19 were not independently available through the original source-reading tools.
- Applicability: Records earlier fix scope, reported execution evidence and its explicit limits.
- Counterevidence: A draft repair report is not independently rerun evidence and does not establish coverage of the current grouped-comma case.
- Validation: Preserve recorded forms and reproduce the newly missing composition independently.

### S8 — Microsoft about_Operators for Windows PowerShell 5.1

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_operators?view=powershell-5.1
- Retrieved: 2026-11-07
- Locator: Array subexpression operator @( ), lines 170–190
- Fragment: Returns the result of one or more statements as an array. The result is always an array of 0 or more objects.
- Applicability: Defines @() as statement-result collection, including empty output.
- Counterevidence: Statements can execute commands; the documentation does not make arbitrary statement values statically known.
- Validation: Compare native collected values and cardinality with the bounded decoder.

### S9 — Microsoft historical PowerShell language specification: expressions

- URL: https://learn.microsoft.com/en-us/powershell/scripting/lang-spec/chapter-07?view=powershell-7.6
- Retrieved: 2026-11-07
- Locator: Editorial note; §7.1.1 Grouping parentheses; §7.3 Binary comma operator
- Fragment: This specification does not reflect the current state of PowerShell. ... (2,4),6 # Length 2; values [Object[]],int
- Applicability: Explains why grouped comma operands require preservation of array nesting rather than global flattening.
- Counterevidence: This is the historical PowerShell 3.0 specification; native 5.1 and 7 observations must corroborate its application.
- Validation: Measure recursive native shape for grouped comma expressions and compare it with decoded slots.

### S10 — Microsoft SDK ArrayExpressionAst

- URL: https://learn.microsoft.com/en-us/dotnet/api/system.management.automation.language.arrayexpressionast?view=powershellsdk-7.6.0
- Retrieved: 2026-11-07
- Locator: Definition, lines 40–54; constructor and SubExpression, lines 81–89; found by web search and opened
- Fragment: The ast that represents an array expression, e.g. `@(1)`. The array literal (e.g. `1,2,3`) is represented by ArrayLiteralAst.
- Applicability: Provides independent structural evidence that array subexpressions and comma literals are distinct constructs.
- Counterevidence: The page carries a prerelease-information caveat. AST structure alone does not establish runtime bindings, side effects or callback roles.
- Validation: Use native AST nesting as a structural oracle and separate runtime fixtures as a behavioral oracle.

### S11 — Native execution fixture availability and behavior

- URL: tool/test_shell_functions.py
- Retrieved: 2026-11-07
- Locator: native(), lines 26–31
- Fragment: executable = shutil.which("pwsh" if major == 7 else "powershell") ... if os.name != "nt" or not executable: pytest.skip("The selected native PowerShell is not installed")
- Applicability: Native fixture definitions do not imply that either engine ran in a given test selection.
- Counterevidence: The earlier research separately reports successful native runs; helper skip behavior does not negate those attributed observations.
- Validation: Record engine versions, actual outcomes and skips separately in the implementation session.

## Claims

- C1 (interpretation; S1, S4): The compatibility promise applies to proved compatible forms, and the current failure propagates before launch through PowerShell capability inspection.
- C2 (interpretation; S2, S3, S4): For the exact round-22 trigger, current source predicts successful outer-map binding followed by failure to decode the comma expression exposed by removing grouping.
- C3 (interpretation; S2, S3, S7): Earlier fixes restored literal-bound executables, captured values and nested-map support, but did not establish a recursively complete value grammar for the supported compositions.
- C4 (interpretation; S2, S6, S7): The latest repair already shared array decoding and recorded passing checks. The present omission survives that consolidation because grouped comma expressions remain outside the shared helper's grammar.
- C5 (interpretation; S6): The inspected current matrices cover grouping around empty array subexpressions, but omit grouping around comma expressions; consumer permutations cannot compensate for that missing expression form.
- C6 (interpretation; S2, S8, S9, S10): A correct bounded repair must distinguish array-valued comma expressions from collection of statement output and preserve nesting when a grouped array is an outer comma operand.
- C7 (source; S8, S9, S10): Microsoft documents statement-result collection for @(), distinguishes comma literals structurally, and illustrates grouping-dependent array nesting in a specification explicitly marked historical.
- C8 (interpretation; S2, S3, S5, S6, S9): The repair should extend the bounded recursive decoder while preserving captured provenance, array shape, callback source spans, data roles and unresolved-value refusals.
- C9 (interpretation; S3, S4, S7, S11): The current refusal remains a static prediction. Prior recorded passes and available native helpers do not establish execution of this new case; historical attribution and native availability remain explicit limits.

## Core theory

PowerShell distinguishes array construction by comma from collection of statement results by @(). Parentheses can enclose an array expression while also protecting that array as an operand of a larger comma expression. Consequently, evaluating an expression's array shape and collecting its statement output are separate operations. Microsoft's historical specification explicitly illustrates the nested shape of `(2,4),6`, but warns that it does not describe the current language; native 5.1 and 7 checks must corroborate its application here. The SDK independently represents array subexpressions and comma literals with different AST node types. These sources do not establish that arbitrary executable expressions are statically resolvable. [C6, C7]

## Applicability

Repair the bounded value grammar in powershell_splats.py. Decode supported comma expressions recursively, including those exposed by grouping, into an array shape with original element and callback spans. Let @() collect the resulting statement output at the appropriate enumeration boundary. A small shared internal representation or equivalent helpers can implement this; a wholesale parser replacement is not required. Preserve a nested array when it is an operand of an outer comma expression: accepting @((1,2)) must not flatten @((1,2),3) into three scalar slots. Keep construction-time capture lookup, nested-map data roles, explicit unresolved values and the existing callback consumer contract. Reuse this analysis for named values and positional splats. Do not implement acceptance by rewriting the submitted source. [C2, C6, C8]

## Hypothesis

The remaining cause is a boundary between expression-value decoding and statement-output collection. parts() classifies outer commas before value_span() removes grouping; after removal, neither literal() nor array_values() recognizes the exposed comma expression. The latest fix shares array handling, so duplicate nested/positional implementations are no longer the primary explanation. A shared helper cannot provide compositional coverage while it equates array-valued expressions with the @() spelling. [C2, C4, C6]

## Counterevidence

- Earlier repairs made substantive improvements. The current code shares array decoding, resolves captured values at their construction epoch and validates nested maps without promoting their blocks to callbacks. This evidence argues against repeating the cycle-1 diagnosis as though none of those changes happened. [C3, C4, C8]
- The prior research reports native and boundary checks passing for its tested forms. The tests also include grouped empty arrays and output-cardinality controls. The narrower gap is grouped comma expressions and their resulting shape, not a complete absence of integration or native validation. [C4, C5]
- PowerShell permits commands, side effects and unknown values inside expressions. Syntax validity alone cannot justify accepting them. The proposed grammar expansion must retain unresolved outcomes outside its proved subset. [C7, C8]
- No reproduction or native process ran in this research session. An observation that binding proof fails earlier, or that a different decoder handles the active command, would refute the predicted path. [C2, C9]

## Alternatives

- Resplit all commas after removing parentheses. This could accept the exact trigger but erase array nesting when a grouped expression is one operand of an outer comma. Decode each operand recursively and distinguish its value from its emitted statement output. [C6, C8]
- Special-case @((1,2)) or retry literal() without parentheses. Grouping is already removed; the missing branch is comma-expression decoding. An exact-pattern patch would leave deeper grouping and callback compositions uncovered. [C2, C5]
- Catch Refusal for InputObject or classify undecoded values as compatible. That would discard information used to distinguish data, executable callbacks and unknown expressions. Preserve refusal for unproved values. [C8]
- Add more separator and boundary permutations alone. Those exercise useful consumers but reuse expression forms that already pass. Add grammar-composition and shape assertions first, then carry them through the boundaries. [C4, C5]
- Replace production analysis with native AST evaluation immediately. Native ASTs are useful structural oracles, but do not independently prove capture timing, mutation or callback roles. First repair and validate the bounded decoder; weigh production parser adoption separately. [C7, C8]

## Experiment

In the implementation session, first record bound_span(), parameters(), seven_only(), classic_seven_only(), and resolve() for the exact round-22 source. Explicitly select gate and registered-check with both ps5 and pwsh7 forms. Prediction: options binding succeeds, parameters() raises Refusal at the grouped comma value, and both resolver forms consequently refuse. Compare @(1,2), @((1,2)), @(((1,2))), @(@(1,2)), @((1,2);3), @((1,2),3), and @(@(1,2),3). On actual Windows PowerShell 5.1 and PowerShell 7, record engine versions, parse errors, parenthesized/comma/array AST nesting, stdout, stderr and status. Run the exact harmless trigger and require exit zero and output ok. Separately measure recursive value shape: @((1,2)) should contain two scalar elements; @((1,2),3) should contain two elements, with a two-element array first. Joined output alone is insufficient to verify this distinction. Cross these expressions through direct map values, captured @() values, maps containing arrays, and positional splats. Add grouped comma expressions containing callbacks; verify original callback spans, order and function-binding behavior, while nested InputObject maps remain data. Retain unknown-variable, dynamic-statement, intervening-mutation, Parallel-on-5.1 and incompatible-classic-child controls. Establish explicit behavior for unary comma and malformed empty operands rather than silently dropping them. Confirm the hypothesis if native engines accept the forms with the expected shape while only static decoding refuses them, then the repair aligns decoding and boundary outcomes without weakening negative controls. Refute or narrow it if binding fails first or native shape differs. Run affected shell tests and the required round lint during implementation; record native skips separately. No checks were executed here. [C2, C5, C6, C7, C8, C9]

## Result

pending — the fix records what it observed here, with the tests and the commit.

## Unresolved limits

This is draft research based on current tracked source, supplied review history, the earlier research record and web searches performed in this session. Historical commit snapshots were not independently inspected. Earlier recorded passes are attributed reports, not checks rerun here. The proposed repair covers a bounded literal grammar; it does not prove arbitrary PowerShell expressions, reference-alias mutation or every parameter conversion. Root assignments containing bare comma expressions also have a separate binding-proof limitation and must not be claimed supported merely because grouped values inside proved @() containers are repaired. [C3, C4, C8, C9]
