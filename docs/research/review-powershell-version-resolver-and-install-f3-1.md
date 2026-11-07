# Review research — F3, cycle 1

Draft research before the fix of review round 6 of PR #307, written 2026-11-07. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F3 is a recurring violation of executable/data separation. Repair must preserve grouped paths, expressions, arguments and hashtable keys as data, while still executing supported legacy gate commands. In round 6, `$map = @{ powershell = 'value' }` reaches a generic command-boundary rule before hashtable context is considered. With PowerShell 7 selected, executable repair consequently predicts `$map = @{ pwsh -NoProfile = 'value' }`. This is a static code trace, not an executed reproduction. [C1, C2, C3]

Trigger: recurrence, rounds 1, 2, 3, 6.

- [P1] [P1] tool/common/powershell_syntax.py:12 — a hashtable key is classified as a command / when an unquoted key is named `powershell` / the PowerShell 7 repair mutates valid data

## Failed fixes

- After round 1 at e2e98be, the reported repair excluded grouped data and required a following argument before inserting `&`. That corrected the reported parenthesized assignment but excluded standalone quoted gate executables. Current grouped-data regressions corroborate the intended preservation boundary; the historical change description comes from the supplied review history. [C4]
- After round 2 at 0fb8ffa, standalone legacy gate positions were admitted again. Current tests require quoted executable paths to become invocations at the start, after a statement separator and before a pipeline or chain. The repair still inferred arguments partly by excluding recognized expression operators, leaving PowerShell 7 operators uncovered. [C5, C6]
- After round 3 at 3304b40, PowerShell 7 expression operators were excluded from invocation repair. Current tests cover `??`, ternary syntax and a `??=` non-rewrite case. This fixed another expression family without establishing a structural classification of all executable and data positions. [C6, C9]
- The intervening F6 assignment-discovery repair shared invocation discovery between capability inspection and executable replacement, added assignment and hashtable-value contexts, and compared a large positive matrix with the native parser. Its nine negative controls omitted command-like hashtable keys. In the current classifier, the generic boundary acceptance still precedes the hashtable-specific branch, allowing round 6's false positive. This is an interaction with another finding's repair, not an additional F3 round. [C1, C7, C8, C9]

## Sources

### S1 — Current command-position classifier and syntax masking

- URL: tool/common/powershell_syntax.py
- Retrieved: 2026-11-07
- Locator: command_position, lines 8-30; syntax, lines 92-171
- Fragment: boundary = r"(?:^|[;&|{(\n])\s*"; `if re.search(boundary + end, prefix, re.I): return True` precedes `if stack and stack[-1][1]:`. The latter branch recognizes `{` preceded by `@` and tests a key/value prefix.
- Applicability: The first return accepts the reported key before enclosing hashtable context is examined. Masking preserves the unquoted key.
- Counterevidence: The classifier already distinguishes some assignments, foreach collections and hashtable values.
- Validation: Compare key-slot and value-command target spans across separators and nesting.

### S2 — Invocation discovery, legacy conversion and launch

- URL: tool/common/shell.py
- Retrieved: 2026-11-07
- Locator: invocations, lines 102-108; invoked, lines 169-192; resolve and launch, lines 242-272
- Fragment: `if command_position(bare[:match.start()]): yield match`; invoked() separately computes `argument` and `standalone`; resolve() applies invoked() to gate and registered-check forms before launch wraps the result.
- Applicability: Shows the two distinct classification paths and how their outputs reach checks and execution.
- Counterevidence: Existing conversion excludes grouped assignments and recognized expression operators; explicit calls bypass insertion.
- Validation: Check invocation spans separately from legacy conversion results and actual fixture execution.

### S3 — PowerShell executable replacement

- URL: tool/command_repair.py
- Retrieved: 2026-11-07
- Locator: _pwsh, lines 24-49
- Fragment: `if dialect == "ps":`; `if os.name == "nt" and shell.chosen().major < 7: return command`; otherwise replacement iterates `shell.invocations(command)` and changes matching literal targets to `pwsh` or `pwsh -NoProfile`.
- Applicability: Converts a discovery false positive into a source mutation, subject to version and target conditions.
- Counterevidence: The 5.1-only Windows path preserves the original; path-qualified targets are not replaced by this literal-name condition.
- Validation: Assert exact unchanged key text under both selected versions and replacement only at actual invocation spans.

### S4 — Grouped executable-path data controls

- URL: tool/test_shell_review.py
- Retrieved: 2026-11-07
- Locator: test_grouped_and_expression_executable_paths_remain_data, lines 94-112
- Fragment: Contexts include `$program = ({value}); Write-Output $program`, `Write-Output ({value})`, `$programs = @({value})` and `$programs = @{{ location = ({value}) }}`; both ps5 and pwsh7 resolutions must equal the original.
- Applicability: Corroborates the grouped-data contract introduced for the first reported F3 failure.
- Counterevidence: These tests do not identify an unquoted hashtable key as data.
- Validation: Retain these preservation controls alongside new key-role cases.

### S5 — Standalone legacy gate execution controls

- URL: tool/test_shell_round_two.py
- Retrieved: 2026-11-07
- Locator: Standalone conversion, lines 30-38; chains and execution, lines 99-121
- Fragment: Standalone executable contexts expect `& ` before the quoted path. The Windows fixture writes `fixture-ran`, exits 17, and is checked through gate, registered-check, server-gate and refactor-runner consumers; missing Windows PowerShell skips execution.
- Applicability: Defines the application compatibility requirement that native invocation discovery alone cannot satisfy.
- Counterevidence: The native execution check is conditional on Windows and an installed executable.
- Validation: Retain actual execution and failure-status checks when restructuring classification.

### S6 — PowerShell 7 expression preservation controls

- URL: tool/test_shell_round_three.py
- Retrieved: 2026-11-07
- Locator: test_ps7_expression_operators_never_turn_path_values_into_invocations, lines 13-24
- Fragment: The operator table includes `?? "fallback"`, `??= "fallback"` and `? "first" : "second"`; invoked() and pwsh7 resolution must preserve the input, while ps5 resolution raises.
- Applicability: Records the targeted operator-family correction and selected-form expectations.
- Counterevidence: Preserving the `??=` input is not proof that a literal string is a valid assignment target; these assertions do not validate every input with a native parser.
- Validation: Preserve these non-rewrite assertions and separately parse valid expression examples with PowerShell 7.

### S7 — Assignment-discovery matrix and nine negative controls

- URL: tool/test_shell_round_five.py
- Retrieved: 2026-11-07
- Locator: CONTEXTS and DATA, lines 16-38; native oracle, lines 41-75; integration and preservation, lines 92-135
- Fragment: Positive contexts include `$map = @{{ value = {child} }}` and a quoted-key equivalent. DATA has nine entries, including a hashtable value containing `Write-Output x= powershell`, but no command-like key. The oracle checks whether any target contains `powershell`.
- Applicability: Explains why existing differential coverage validates value discovery without detecting a key false positive.
- Counterevidence: The oracle uses real CommandAst nodes and the integration cases cover both selected parent versions and actual host verdict functions.
- Validation: Add key/value paired cases and compare exact command-target extents instead of existence alone.

### S8 — Prior F6 discovery research and recorded repair results

- URL: docs/research/review-powershell-version-resolver-and-install-f6-1.md
- Retrieved: 2026-11-07
- Locator: Applicability, lines 195-197; Result and Unresolved limits, lines 225-233
- Fragment: "Inspection and PowerShell executable repair now share discovery"; the recorded table has "132 positive scripts" and "nine negative controls". "This remains draft evidence for independent review, not a verified finding or shared rule."
- Applicability: Documents the intervening shared-discovery repair and the scope of its recorded evidence.
- Counterevidence: This is an earlier draft's record, not a fresh execution result or an independently certified historical ledger.
- Validation: Extend its context experiment to the omitted key role and preserve its positive assignment/value cases.

### S9 — Microsoft PowerShell hashtable syntax

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_hash_tables
- Retrieved: 2026-11-07
- Locator: Syntax, especially key/value separators and value requirements
- Fragment: "Use a semicolon (`;`) or a line break to separate the key-value pairs." "Values must be valid PowerShell expressions."
- Applicability: A separator inside a hashtable can begin another key/value entry rather than a command statement.
- Counterevidence: Hashtable values and nested expressions still require syntax-aware inspection; the whole literal cannot be treated as inert text.
- Validation: Test first and subsequent entries, nested hashtables and ordered dictionaries against the native parser.

### S10 — Microsoft PowerShell parsing modes

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parsing
- Retrieved: 2026-11-07
- Locator: Expression mode and Argument mode
- Fragment: "But when a command invocation is encountered, parsing continues in argument mode." "Parentheses (`()`) begin a new expression"
- Applicability: Supports distinguishing syntax roles rather than treating punctuation or executable-looking strings as sufficient evidence of invocation.
- Counterevidence: Expressions can contain nested commands; grouping alone is not proof that all contained text is data.
- Validation: Pair literal grouped paths with grouped commands and compare their command-target spans.

### S11 — Historical PowerShell expression grammar

- URL: https://learn.microsoft.com/en-us/powershell/scripting/lang-spec/chapter-07
- Retrieved: 2026-11-07
- Locator: Editorial note; section 7.1.9 Hash literal expression
- Fragment: "hash-entry: key-expression = new-lines~opt~ statement"; "key-expression: simple-name unary-expression"; "This specification does not reflect the current state of PowerShell."
- Applicability: Explicitly distinguishes the key expression from the value statement and allows more than bare string keys.
- Counterevidence: This is historical PowerShell 3.0 documentation; current-version behavior requires independent confirmation.
- Validation: Confirm the relevant key/value and nested-command structures with installed supported parsers.

### S12 — Microsoft PowerShell call operator

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_operators?view=powershell-7.5
- Retrieved: 2026-11-07
- Locator: Call operator &, quoted script-path example
- Fragment: "If you try to execute the quoted path, PowerShell displays the contents of the quoted string instead of running the script."
- Applicability: Explains why a standalone quoted legacy gate needs an explicit application conversion policy separate from native invocation discovery.
- Counterevidence: The surrounding section requires normal command parsing rules; the call operator does not parse an entire command line embedded in one string.
- Validation: For legacy inputs, compare converted behavior with an explicitly invoked fixture rather than requiring identical original native ASTs.

### S13 — PowerShell Parser.ParseInput API

- URL: https://learn.microsoft.com/en-us/dotnet/api/system.management.automation.language.parser.parseinput?view=powershellsdk-7.4.0
- Retrieved: 2026-11-07
- Locator: ParseInput(String, Token[], ParseError[])
- Fragment: "Parse input that does not come from a file." "Returns errors, if any, discovered while parsing the script."
- Applicability: Supports a parsing-only oracle that returns structural information and errors without executing the researched payload.
- Counterevidence: Parsing does not establish runtime target identity or application-specific legacy intent.
- Validation: Record parser identity, parse errors and exact node extents for original and repaired scripts.

### S14 — PowerShell HashtableAst.KeyValuePairs API

- URL: https://learn.microsoft.com/en-us/dotnet/api/system.management.automation.language.hashtableast.keyvaluepairs?view=powershellsdk-7.4.0
- Retrieved: 2026-11-07
- Locator: Definition and property type
- Fragment: "The pairs of key names and asts for values used to construct the hash table." The declared pair types are `ExpressionAst` and `StatementAst`.
- Applicability: Provides a structural distinction for key/value oracle assertions.
- Counterevidence: An expression node can contain nested executable expressions; key membership alone does not justify suppressing every descendant command.
- Validation: Distinguish the key's literal name extent from command descendants in computed keys and value statements.

## Claims

- C1 (interpretation; S1): Static inspection predicts that a hashtable key after `{`, semicolon or newline can be accepted by the generic boundary return before hashtable context is checked.
- C2 (source; S2): shell.invocations() delegates candidate command-position classification to command_position(); legacy quoted-path conversion uses a separate invoked() classifier.
- C3 (interpretation; S1, S2, S3): When executable switching is permitted, _pwsh() consumes discovered literal powershell targets and replaces their source spans, propagating a key false positive into mutation.
- C4 (source; S4): Current regressions require grouped and assigned executable-looking strings to remain unchanged across both PowerShell forms.
- C5 (source; S2, S5): Current regressions require supported standalone quoted legacy checks to receive invocation syntax and preserve actual execution output and failure status.
- C6 (interpretation; S2, S6): Current repair uses operator exclusions, with dedicated regressions preserving PowerShell 7 operator examples; those tests alone do not establish full syntax validity or coverage.
- C7 (source; S7): The current assignment matrix includes hashtable-value commands, omits command-like key negatives, and its parser assertion checks target existence rather than exact invocation spans.
- C8 (source; S8): Prior F6 research records shared discovery and a 132-positive/nine-negative experiment, explicitly retaining draft status and scope limits.
- C9 (interpretation; S1, S2, S3, S4, S5, S6, S7, S8): The recurrence is best explained by successive example-family corrections without a complete enclosing-role classification and without negative cases paired to newly admitted positive contexts.
- C10 (interpretation; S9, S10): PowerShell interpretation depends on parsing context, and hashtable separators delimit entries; therefore these punctuation characters do not universally establish command position.
- C11 (interpretation; S11, S14): The historical grammar distinguishes key expressions from value statements, and the current AST property exposes that distinction; neither warrants treating all descendants of a key expression as inert.
- C12 (interpretation; S2, S5, S12): Native discovery and legacy gate conversion require distinct policies because a standalone quoted path is native string data but is intentionally converted into execution by this application's gate contract.
- C13 (interpretation; S1, S2, S3, S4, S5, S6, S7, S8, S9, S10, S11, S12, S13, S14): A context-first repair validated by exact native command spans, paired key/value controls and separate legacy fixture execution checks can distinguish a durable correction from another punctuation-specific patch.

## Core theory

PowerShell changes interpretation according to syntactic context: expressions, commands and arguments have different roles. [Parsing](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parsing) describes the transition to argument mode after encountering an invocation. [Hashtable syntax](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_hash_tables) gives braces, equals signs and separators specific roles within key/value entries. The historical grammar distinguishes a key expression from a value statement, and the current AST API separately represents them. A quoted path alone remains a string in native PowerShell; inserting `&` is therefore an intentional application compatibility transformation, not merely recognition of an existing invocation. [C10, C11, C12]

## Applicability

In tool/common/powershell_syntax.py:8-30, establish enclosing delimiter kinds and the current hashtable-entry role before accepting generic command boundaries. Track entry separators at the appropriate nesting level; distinguish @{...} from script blocks and distinguish key slots from value statements. Preserve actual commands inside values and nested expressions, including computed key expressions where the native parser confirms a command. In shell.invocations() and command_repair._pwsh(), replace only confirmed executable target spans. Retain a separate, explicitly bounded legacy conversion policy in shell.invoked() for gate and registered-check text. The native parser can supply an independent structural oracle, but it cannot infer legacy gate intent from a standalone quoted string. [C1, C2, C3, C10, C11, C12, C13]

## Hypothesis

The recurring cause is classifying syntactic roles from nearby punctuation and operator exclusions before establishing the enclosing context. The immediate defect is precedence: command_position() returns True for a prefix ending in `{`, `;` or newline before its stack can establish that the candidate occupies a hashtable key slot. String masking cannot hide an unquoted key. Sharing this classifier then propagates the same false positive into executable replacement. A second underlying problem is treating native invocation discovery and deliberate legacy gate conversion as though they had identical semantics. [C1, C2, C3, C9, C10]

## Counterevidence

- The prior fixes address real distinctions: grouped path preservation, standalone legacy execution and PowerShell 7 operator preservation have dedicated regressions. Their recurrence does not show that those fixes were wholly ineffective. [C4, C5, C6]
- The current classifier already contains hashtable-aware logic. Reordering and strengthening a bounded context recognizer may be sufficient; this research does not establish that a runtime AST service is necessary. [C1, C13]
- The F6 repair already introduced a native-parser oracle and substantial context coverage. The weakness is the omitted negative role and limited assertions, rather than complete absence of independent syntax evidence. [C7, C8]
- Executable replacement is conditional: on Windows with only PowerShell 5.1 selected, _pwsh() returns the original command. The destructive replacement described here requires the repair path that permits switching to PowerShell 7. [C3]
- The language specification explicitly describes historical PowerShell 3.0 behavior. It supports the structural distinction, but cannot establish PowerShell 7 operator behavior; version-specific parser checks remain necessary. [C11, C13]

## Alternatives

- Exclude only the `{` immediately preceded by `@`. This addresses the first key but leaves the same generic acceptance after semicolons and newlines inside a hashtable. Classify the enclosing entry role first. [C1, C10, C13]
- Ignore everything inside a hashtable. That would regress discovered child commands in values and could hide commands nested in computed expressions. Existing positive dictionary-value cases must survive. [C7, C11, C13]
- Skip any candidate followed by `=`. This is a useful negative case, but another spelling heuristic would not establish the candidate's structural role or protect nested and expression contexts. Prefer role classification with independent span comparisons. [C9, C13]
- Use native CommandAst discovery as the sole policy for both functions. That is suitable for existing native invocations, but a standalone quoted legacy gate is a native string expression. Without a separate compatibility rule, this repeats round 2's skipped check. [C5, C12, C13]
- Expand operator exclusions again. The current reproducer contains a hashtable key, so additional expression-operator spellings do not address its cause. [C1, C6, C9]
- Require immediate adoption of a runtime AST service. It is a possible implementation, but startup cost and parser-version selection have not been measured. First extend the existing parsing-only oracle to discriminate key and value spans, then choose a bounded recognizer or runtime integration from that evidence. [C7, C8, C13]

## Experiment

This is the original read-only proposal; the repair-turn observations are recorded under Result.

Before editing, record command_position(), shell.invocations() target spans, nested_commands() and _pwsh() output for the exact key reproducer. Static prediction: discovery emits the key, and repair changes it when switching to 7 is permitted. Independently use Parser.ParseInput without executing supplied scripts, retain parse errors, and enumerate CommandAst target extents plus hashtable key/value extents. Test first, semicolon-separated and newline-separated keys named powershell, powershell.exe and pwsh; vary case, whitespace, comments, nesting and [ordered]@{}. Include `$map = @{ powershell = powershell -Command 'Write-Output ok' }`: discovery must identify only the value invocation, and repair must leave the key unchanged. Add nested script blocks and computed key expressions, admitting their expected command spans only after parser confirmation. Compare exact target spans, not merely whether any powershell command exists. After repair, negative data controls must remain byte-for-byte unchanged, positive invocation spans must remain discoverable, and valid native scripts must retain their parse validity. Separately retain legacy gate tests: a standalone quoted fixture check must execute once and preserve exit 17 through gate, registered-check, server-gate and refactor-runner consumers; grouped assignments and PowerShell 7 expression controls must never launch the fixture. Compare native ASTs for preservation cases, but compare legacy conversion output against an explicitly invoked expected command. Exercise actual hook verdict outputs under selected versions 5 and 7 to detect mutations propagated beyond _pwsh(). Refute the immediate hypothesis if the unchanged current implementation excludes the reported key, or if native parsing identifies that bare key as a command target. All these checks are proposed; none were executed in this research turn. [C1, C2, C3, C4, C5, C6, C7, C10, C11, C12, C13]

## Result

Observed in the repair commit titled `Preserve hashtable data and refuse unresolved PowerShell targets`: before repair, the reported key was emitted as an executable target and changed when 7 was selected. `test_hashtable_keys_remain_data_in_discovery_and_actual_host_outputs` now preserves 96 key/placement/container/version cases through discovery, executable repair and both actual host verdicts. `test_native_oracle_separates_exact_key_and_command_spans` retains parse errors and key/value extents, comparing exact command spans for valid scripts. The installed parser rejects bare dotted keys; those invalid inputs remain unchanged, while quoted dotted-key controls validate the corresponding valid form. `test_key_value_repairs_change_only_native_command_spans_and_still_parse` checks that only value or nested-expression command spans change and that repaired scripts still parse. Existing legacy fixture, grouped-data and PowerShell 7 expression tests remain separate obligations: native discovery does not infer legacy gate intent. Supplied finding strings were inspected, not executed. This remains draft evidence for independent review, not a verified finding or shared rule.

## Unresolved limits

This remains draft research for F3, cycle 1 of 2. The Result records repair-turn experiments, distinct from the original read-only research. Historical identities come from supplied review messages rather than an independently certified ledger. Native-parser evidence covers the installed Windows PowerShell version and the tested grammar; it does not establish universal grammar coverage or runtime target resolution. Legacy conversion retains its explicit application policy and separate execution controls.
