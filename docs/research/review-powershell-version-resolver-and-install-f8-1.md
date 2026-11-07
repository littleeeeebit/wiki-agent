# Review research — F8, cycle 1

Draft research before the fix of review round 20 of PR #307, written 2026-11-07. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F8 breaks the requirement that proved, compatible PowerShell commands remain runnable on Windows PowerShell 5.1. The current reproducer is $options=@{InputObject=@(1;2); Process={Write-Output ok}}; ForEach-Object @options. Binding discovery recognizes the literal collection, but value decoding treats 1;2 as one unsupported scalar and raises Refusal. This refusal propagates through capability inspection before launch. Newline-separated values encounter the same mismatch. These are static conclusions from the current source, not executed reproductions. [C1, C3, C5]

Trigger: recurrence, rounds 7, 18, 19, 20.

- [P1] [P1] tool/common/powershell_splats.py:91 — statement-separated literal arrays are rejected in splatted parameter values / when an `@(...)` value uses a semicolon or newline instead of commas / a compatible PowerShell 5.1 command is denied

## Failed fixes

- Round 7: the preceding F6 repair preserved unresolved executable targets but refused even targets assigned known literal values. Prior research explicitly allowed that compatibility restriction and described constant-bound support as optional; review rejected that tradeoff. The correction introduced straight-line binding proof and positive variable-invocation controls. That fixed executable selection, but did not establish a compositional contract for later collection-value analysis. [C2, C3]
- Round 18: according to the supplied review history, the correction added resolution of captured literal variables inside splatted values. Current code resolves each variable at its containing assignment's construction epoch, and tests cover captured script blocks, scalars, arrays and reassignment. Nested maps were still outside the value decoder's accepted forms. The repair addressed capture provenance without closing the supported value grammar under container composition. Historical changes are reconstructed from the supplied findings and current code/tests; the individual commit snapshots were not independently inspected. [C3, C4]
- Round 19: according to the supplied history, the correction added recursive nested-map validation while keeping nested keys and blocks as data. Current tests exercise direct, captured, ordered and nested maps. Both array paths still partition only on commas, so arrays containing semicolon- or newline-separated literal statements remain rejected. The map repair held for its tested cases but left the adjacent array grammar assumption intact. [C3, C4, C7]
- Round 20 has no reported correction yet. Changing only the nested array branch would leave the separate top-level positional-splat array branch with the same comma-only assumption. [C3, C8]

## Sources

### S1 — Repository setup compatibility promise

- URL: docs/chat-setup.md
- Retrieved: 2026-11-07
- Locator: lines 19–25
- Fragment: A PC with only Windows PowerShell 5.1 can use the compatible command forms and offers `PowerShell 7 설치`.
- Applicability: Defines the user-visible compatibility invariant.
- Counterevidence: Does not promise static resolution of arbitrary dynamic commands.
- Validation: Proved compatible forms remain accepted at gate and registered-check boundaries.

### S2 — Earlier F6 research and round-7 compatibility correction

- URL: docs/research/review-powershell-version-resolver-and-install-f6-2.md
- Retrieved: 2026-11-07
- Locator: Applicability; Counterevidence; Round 7 correction — F8 and F9
- Fragment: Supporting constant-bound variables is optional subsequent work requiring a documented, conservative subset; it is unnecessary to close the reported bypass. ... The commit titled `Restore compatible variable checks and hook fault handling` supersedes the blanket refusal of literal-bound variables ... Unproved targets remain explicit refusals.
- Applicability: Records both the earlier tradeoff that caused F8 and its subsequent correction.
- Counterevidence: The document labels its repair observations draft evidence, not verified research or a shared rule.
- Validation: Preserve compatible bound-target acceptance and refusal of genuinely unresolved targets.

### S3 — Literal binding proof and masked source view

- URL: tool/common/powershell_syntax.py
- Retrieved: 2026-11-07
- Locator: bound_span(), lines 28–84; syntax(), lines 199–278
- Fragment: if enclosed(source, "{", "}") or container.startswith("@") and (enclosed(container[1:], "{", "}") or enclosed(container[1:], "(", ")")): return start, start + len(source) ... # Only an unevaluated assignment/return prefix may precede this call in the current statement.
- Applicability: Container spans are admitted before their internal values are decoded; uncertain statement contexts invalidate proof.
- Counterevidence: Recognizing a container span does not certify every expression inside it.
- Validation: Observe a proved options span before the current failure, and retain invalidation across unknown mutations.

### S4 — Splat parameter and value decoder

- URL: tool/common/powershell_splats.py
- Retrieved: 2026-11-07
- Locator: parts(), map_entries(), literal(), lines 35–94; positional arrays, lines 107–114
- Fragment: values = [literal(a, b) for a, b in parts(start + 2, end - 1, ",")] ... for first, last in parts(start + 2, end - 1, ","): ... # A collection captures the value when its containing assignment executes. ... # Entries are validated recursively, but their blocks and keys remain data.
- Applicability: Shows both comma-only array paths, construction-time variable lookup, recursive map validation and refusal on undecoded values.
- Counterevidence: Its splitter already tracks nesting; extending that existing structure may suffice for a bounded repair.
- Validation: Both array paths accept supported statement sequences and return correct shapes and original callback spans.

### S5 — Captured splat-value regression matrix

- URL: tool/test_shell_splat_values.py
- Retrieved: 2026-11-07
- Locator: CAPTURES and POSITIONAL, lines 12–28; positive, negative and native tests
- Fragment: "$callback={body}; $options=@{{{key}=@($callback)}}" ... "$callback={body}; $items=@($callback); $options=@{{{key}=$items}}" ... def test_unproved_callback_values_remain_refused
- Applicability: Demonstrates capture, reassignment and boundary coverage, with single-item array forms rather than statement-separated array values.
- Counterevidence: These tests cover meaningful temporal and native behavior; they are not merely duplicate unit assertions.
- Validation: Retain these controls and add separator/container composition cases.

### S6 — Nested-map splat regression matrix

- URL: tool/test_shell_splat_maps.py
- Retrieved: 2026-11-07
- Locator: DATA, lines 12–23; compatibility, data-role, negative and native tests
- Fragment: "@{Name='data-fixture'; Items=@(@{Name='child-fixture'})}" ... def test_nested_map_fields_do_not_become_callbacks_or_parallel_parameters ... def test_unproved_nested_map_entries_remain_refused
- Applicability: Shows recursive-map support and preservation of data roles, but no semicolon/newline-separated array-value matrix.
- Counterevidence: Nested arrays already occur in tests; the missing dimension is their statement structure.
- Validation: Compose the existing map cases with literal statement arrays while preserving unknown-value refusals.

### S7 — Callback parameter roles and source spans

- URL: tool/common/powershell_functions.py
- Retrieved: 2026-11-07
- Locator: callback_arguments(), lines 224–286; define(), lines 288–296
- Fragment: for name, a, b, code, values in parameters(self.source, position, splat[1]): ... # Pipeline binding can execute a ScriptBlock input value, but not an array of blocks. ... self.splatted.update(values)
- Applicability: Explains why successful decoding must retain array shape, callback roles and original source coordinates.
- Counterevidence: Decoding a valid array does not independently prove its parameter conversion or callback behavior.
- Validation: Check callback order and binding behavior separately from InputObject data and Parallel requirements.

### S8 — Capability inspection and resolver propagation

- URL: tool/common/shell.py
- Retrieved: 2026-11-07
- Locator: invocations(), lines 177–225; nested_commands(), classic_seven_only(), seven_only(); resolve(), lines 367–385
- Fragment: function_only = functions.define(program, text[item.target_end:], start) ... for invocation in invocations(text): ... if form != "bash" and classic_seven_only(command):
- Applicability: Both capability traversals reach splat analysis; the resolver does not convert its Refusal into acceptance.
- Counterevidence: A refusal during analysis does not establish an actual engine incompatibility.
- Validation: Record direct analyzer outcomes and both explicit PowerShell resolver forms before and after repair.

### S9 — Microsoft about_Operators — PowerShell 5.1

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_operators?view=powershell-5.1
- Retrieved: 2026-11-07
- Locator: Array subexpression operator @( ), lines 170–190
- Fragment: Returns the result of one or more statements as an array. The result is always an array of 0 or more objects.
- Applicability: Direct 5.1 documentation defines statement-output collection and illustrates semicolon-separated commands inside @().
- Counterevidence: The documented statements may execute commands; this does not make arbitrary contents statically known.
- Validation: Use harmless literal-statement fixtures on actual 5.1 and compare resulting values.

### S10 — Microsoft PowerShell language specification — Statements

- URL: https://learn.microsoft.com/en-us/powershell/scripting/lang-spec/chapter-08?view=powershell-7.5
- Retrieved: 2026-11-07
- Locator: Editorial note; section 8.1 Statement blocks and lists
- Fragment: This documentation is presented here for historical reference. ... statement-terminator: ; new-line-character
- Applicability: Found through an actual web search; explains semicolon and newline statement boundaries.
- Counterevidence: The page describes the historical PowerShell 3.0 specification and explicitly says it does not reflect the current language.
- Validation: Confirm the proposed supported forms with native 5.1 and 7 parser results rather than relying on historical grammar alone.

### S11 — Microsoft SDK — ArrayExpressionAst

- URL: https://learn.microsoft.com/en-us/dotnet/api/system.management.automation.language.arrayexpressionast?view=powershellsdk-1.1.0
- Retrieved: 2026-11-07
- Locator: Definition; constructor; SubExpression property
- Fragment: ArrayExpressionAst(IScriptExtent, StatementBlockAst) ... The array literal (e.g. `1,2,3`) is represented by ArrayLiteralAst.
- Applicability: Provides distinct structural representations for array subexpressions and comma expressions; lists the 5.1 reference assembly.
- Counterevidence: AST structure alone does not resolve runtime bindings or side effects; the page also carries a prerelease-information caveat.
- Validation: Extend the oracle to record statement blocks and comma-expression elements with engine versions.

### S12 — Splat callback integration and parser fixtures

- URL: tool/test_shell_splats.py
- Retrieved: 2026-11-07
- Locator: MAPS and CALLS; negative controls; native fixtures, lines 142–170
- Fragment: sources = [binding.format(key=key, body=BODY) + "; " + call for binding in MAPS for key, call in CALLS] ... assert all(row["errors"] == 0 for row in native_commands(sources))
- Applicability: The native parser matrix uses predefined callback-map forms and checks parse errors; it does not inspect array statement structure.
- Counterevidence: Separate native fixtures already check callback output and repeated-call binding behavior.
- Validation: Add array structure and runtime-value checks while retaining callback and incompatible-child controls.

### S13 — Existing native AST oracle

- URL: tool/test_shell_round_five.py
- Retrieved: 2026-11-07
- Locator: native_commands(), lines 41–78
- Fragment: if os.name != "nt" or not executable: pytest.skip("Windows PowerShell AST differential oracle") ... Parser]::ParseInput ... errors = @($errors).Count; targets = $targets; commands = $records; pairs = $pairs
- Applicability: A parser-only oracle already exists but records commands and hashtable pairs, not array statement structure.
- Counterevidence: It is Windows-dependent and skips without Windows PowerShell; its presence is not evidence it ran.
- Validation: Record actual engine versions, array nodes and skip status during implementation verification.

## Claims

- C1 (interpretation; S1, S9): The repository promises compatible forms remain usable on 5.1; proved literal statement arrays belong within the compatibility question.
- C2 (source; S2): Earlier research explicitly permitted blanket unresolved-target refusal; its round-7 correction records that this rejected compatible literal-bound targets and was superseded by binding proof.
- C3 (interpretation; S3, S4, S8): Current binding discovery admits the enclosing literal containers, while the independent value decoder rejects statement-separated arrays; capability traversal propagates that refusal.
- C4 (interpretation; S4, S5, S6): Current implementation and tests support captured values and nested maps, but those additions do not establish support for all compositions of the accepted container forms.
- C5 (source; S9, S10): PowerShell 5.1 documents @() as collecting statement results, including zero results; the historical statement grammar permits semicolon and newline terminators.
- C6 (interpretation; S9, S10, S11, S13): Array subexpressions and comma expressions have distinct AST structure. Native parsing can check that structure, but cannot alone prove runtime values or capability effects.
- C7 (interpretation; S5, S6, S12, S13): Existing tests cover boundaries, capture timing, nested data and native behavior, but the inspected matrices omit statement-separated array values; native helpers may skip.
- C8 (interpretation; S4, S7, S8): Both nested and positional array paths need consistent decoding, and their results must preserve callback spans, data roles and array shape for downstream analysis.
- C9 (interpretation; S2, S3, S4, S6, S7, S9, S11, S12): A shared bounded, composition-aware decoder with native structural and behavioral checks is the preferred repair; blanket acceptance or refusal would lose existing guarantees.
- C10 (interpretation; S4, S8, S13): The refusal path is a static prediction requiring implementation-time confirmation; repository test definitions and native-oracle availability do not establish executed results.

## Core theory

Microsoft's PowerShell 5.1 documentation defines @() as collecting the results of one or more statements into an array, including zero results, and provides a semicolon-separated example. The historical language specification defines semicolons and newlines as statement terminators; it explicitly warns that it is historical rather than a current specification. The SDK also distinguishes ArrayExpressionAst, which contains a statement block, from ArrayLiteralAst for comma expressions. Together these sources explain why recognizing the enclosing array syntax is insufficient: an analyzer must account for the supported statements and their resulting value shape. These language facts do not establish that arbitrary statements are statically resolvable. [C5, C6]

## Applicability

Unify the two @() decoding paths in powershell_splats.py around one bounded array/value analysis. Within the existing proved subset, support statement sequences separated by semicolons or newlines, alongside comma expressions, recursively through captured variables and nested maps. Preserve original block spans, construction-time capture lookup, array shape and parameter roles. Use lexical nesting and the existing masked syntax view so separators inside strings, comments, nested containers and script blocks are not treated as outer separators. Keep unknown values and potentially mutating statements unresolved. A shared helper should make nested values and positional splats agree; it should not rewrite the submitted command. Confirm the supported grammar against native ASTs and harmless runtime fixtures. [C3, C6, C8, C9]

## Hypothesis

The recurring cause is disagreement between binding proof and value interpretation, reinforced by tests organized around each reported example. bound_span() admits literal container spans without decoding their contents; powershell_splats.literal() independently implements a smaller value language. Every newly supported container or capture exposes another missing composition. The present concrete error is modeling @() as a comma-delimited element list rather than a statement-output collector. Fail-closed handling then turns incomplete analysis of a compatible form into a compatibility refusal. This does not imply unknown expressions should be accepted. [C3, C4, C5, C7]

## Counterevidence

- The earlier fixes were substantive: executable bindings now have conservative invalidation, captured values use construction-time lookup, and nested-map blocks remain data. The evidence supports a remaining grammar mismatch, not failure of every earlier repair. [C2, C3, C4]
- Existing tests already cover multiple boundaries and include native fixtures. The missing dimension is statement-separated array composition; there is no basis for saying the project has no integration or native validation. [C4, C7]
- PowerShell array subexpressions can contain executable statements and side effects. Native syntax validity alone cannot prove their values or capability effects. The proposed acceptance expansion must remain bounded to analyzed forms. [C5, C6, C9]
- No test or native PowerShell process was run in this research session. The predicted refusal could be refuted by an implementation-time observation showing a different active code path. [C3, C10]

## Alternatives

- Add semicolon and newline separators only at line 91. This likely addresses the exact nested-value example, but leaves the positional-array path at line 109 inconsistent and provides no evidence about nesting, continuation or callback roles. Use one shared decoder and a composition matrix. [C3, C8, C9]
- Split every array on comma, semicolon and newline without checking structure. This would confuse outer statement boundaries with separators inside nested blocks or strings, and could mishandle continued comma expressions. Retain nesting-aware analysis and compare structure with the native parser. [C3, C6, C9]
- Catch Refusal and declare an undecoded value compatible. That would discard the unresolved outcome protecting callback and child-shell analysis. Preserve explicit refusal for unknown values. [C2, C8, C9]
- Require users to rewrite all arrays with commas or install PowerShell 7. The documented language supports statement-based arrays, and this exception is raised by the shared analyzer, including traversal under a 7 parent. Neither approach repairs the compatibility invariant. [C1, C3, C5]
- Replace all production analysis with the native AST immediately. The AST is a useful structural oracle, but does not by itself resolve captured variables, mutation or callback execution roles. Start by validating and sharing the bounded value grammar; evaluate a production parser replacement separately. [C3, C6, C8, C9]

## Experiment

In the repair session, first record parameters(), seven_only(), classic_seven_only(), resolve(), launch(), executable repair and both actual host verdict functions for the exact round-20 source. Explicitly request form='ps5' and form='pwsh7' for both gate and registered-check so platform defaults cannot bypass analysis. Prediction before repair: parameters() raises Refusal while decoding InputObject; the capability consumers consequently refuse rather than classify this source as compatible. Compare comma, semicolon, LF and CRLF forms. Then cross direct values, captured array variables, captured maps containing arrays, nested maps and top-level positional callback arrays. Include mixed comma/newline formatting, comments, empty and singleton arrays, quoted separators and block-internal semicolons. Extend the native parser oracle to record ArrayExpressionAst.SubExpression statements and ArrayLiteralAst elements; record engine versions and parse errors without executing the supplied sources. Separately run harmless fixtures on actual 5.1 and 7: the reported source should print ok, and a variant using Process={ $_ -join ',' } should preserve the input value as 1,2. Compare original and repaired output, stderr and status. Callback-array fixtures must preserve callback order and original function-binding spans; nested InputObject maps must remain data. Retain negative controls for $external, intervening mutation, dynamic expressions, splatted Parallel on 5.1, and incompatible explicit classic children. The hypothesis is supported if native structure and execution accept the literal statement forms while only the analyzer rejects them, and the shared decoder closes that gap without changing the negative controls. Refute it if binding provenance is already lost before decoding or the native engines produce materially different structure. Run affected shell tests and the repository's required round lint in the implementation session; record native skips separately from passes. No checks were executed here. [C3, C6, C7, C8, C9, C10]

## Result

pending — the fix records what it observed here, with the tests and the commit.

## Unresolved limits

This is draft research from current tracked source, supplied review history and retrieved Microsoft documentation. Historical commit snapshots for rounds 18 and 19 were not independently available through the source-reading tools. No files were changed and no tests, shell commands or native fixtures were executed. The proposed bounded decoder is not a proof of compatibility for arbitrary PowerShell, arbitrary statement side effects, mutable reference aliases or every parameter-binding conversion. The historical language specification needs corroboration from the actual supported engines; existing native helpers skip when the required platform or executable is absent. [C6, C7, C10]
