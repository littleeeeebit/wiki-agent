# Review research — F3, cycle 1

Draft research before the fix of review round 2 of PR #272, written 2026-11-02. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F3: every effect a PowerShell member invocation can have must reach the guard's effect set. A method or property setter that writes, deletes or copies files has to produce a write/delete effect or `top`. Today the guard decides whether a member call is safe from the method NAME alone, and only for one spelling: the lexer records `w.method` only when the source reads `.Name(` or `::Name(` (tool/guard.py:291-293), and `words_effects` (tool/guard.py:540-555) accepts any name in `SAFE_METHODS` (tool/guard_windows.py:17-27) whatever the receiver. PowerShell picks the member from the receiver's runtime type and offers many invocation channels besides `.Name(`. So a mutating member reached by a safe-listed name, or through another channel, gives only `read`. Round 1: `(Get-Item README.md).CopyTo('C:/outside/target.md')`. Round 2: `Get-Item ../outside/target.txt | ForEach-Object -MemberName Delete`.

Trigger: repeat, rounds 1, 2.

- [P1] [P1] tool/guard_windows.py:176 — Pipeline method invocation still loses mutation effects

## Failed fixes

- Round 1 (eb6794a to c9770fe) removed `copyto` from SAFE_METHODS and added a regression case for that exact spelling (tool/test_guard.py:284). It closed one name in one channel. The name list is still keyed by name, not by the receiver's type, so another safe-listed name that mutates for some receiver type still passes in the same syntax. `replace` is safe-listed because of String.Replace, but FileInfo.Replace(dest, backup) overwrites dest and deletes the source. `foreach` is safe-listed and dispatches any method or property setter given as a string.
- Round 1 also left every non-`.Name(` channel outside the check. `ps_cmdlet` returns `set()` for any cmdlet starting with `foreach-` or `where-` (PS_READ_PREFIX, tool/guard_windows.py:84-89, 176-177), so `ForEach-Object -MemberName Delete` and its positional and alias forms (`% Delete`, `foreach Delete`) never reach `words_effects`. Round 2's reviewer reached the same invariant through that channel.

## Sources

### S1 — ForEach-Object (Microsoft.PowerShell.Core) - PowerShell 7.6

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/foreach-object
- Retrieved: 2026-11-02
- Locator: Syntax: PropertyAndMethodSet; Example 6, Example 7; Parameters -MemberName, -ArgumentList; Notes (aliases)
- Fragment: Simplified syntax: 'you specify a property or method name of the object in the pipeline. ForEach-Object returns the value of the property or method for each object in the pipeline.' -MemberName: 'Specifies the name of the member property to get or the member method to call. The members must be instance members, not static members. Wildcard characters are permitted, but work only if the resulting string resolves to a unique value.' Position: 0. -ArgumentList: 'Specifies an array of arguments to a method call.' Example 7: 'ForEach-Object -MemberName Split -ArgumentList "."' and 'foreach Split "."' ('omits the names of the MemberName and ArgumentList parameters, which are optional'). Aliases: '%', 'foreach'. Scriptblock syntax: 'The scriptblock can contain any PowerShell script.'
- Applicability: PS_READ_PREFIX in tool/guard_windows.py treats every foreach- cmdlet as a reader, so the PropertyAndMethodSet channel (named, positional, alias and wildcard) invokes arbitrary instance methods unchecked.
- Counterevidence: Instance members only, so static [IO.File]::Delete cannot be reached this way. A wildcard that matches several members fails at run time.
- Validation: The -MemberName, positional, alias and wildcard cases in the experiment are denied after the fix; `gci | % Name` still passes.

### S2 — about_Arrays - PowerShell 7.6 (ForEach() and Where() methods)

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_arrays
- Retrieved: 2026-11-02
- Locator: Methods of arrays > ForEach(); ForEach(string propertyName, object[] newValue); ForEach(string methodName, object[] arguments); Member-access enumeration
- Fragment: Overloads: 'ForEach(string propertyName)', 'ForEach(string propertyName, object[] newValue)', 'ForEach(string methodName)', 'ForEach(string methodName, object[] arguments)'. 'The ForEach() method can also be used to retrieve, or set property values for every item in the collection.' Example: '(dir 'C:\Temp').ForEach('LastAccessTime', (Get-Date))'. 'You can use the ForEach() method to execute an object's method on every item in the collection.' PowerShell 7.6-preview.5 added PSForEach()/PSWhere() aliases. Where(): 'The syntax requires the usage of a scriptblock.' Member enumeration: 'enables you to get values ... but not to set values'; 'To set the values you must use a method': '$files.set_LastWriteTime(...)'. Caution: 'Since the method is executed for each item in the collection, care should be taken'.
- Applicability: `foreach` (and `where`) in SAFE_METHODS lets `.ForEach('Delete')` and `.ForEach('LastAccessTime', value)` pass as a safe method. PSForEach is a new spelling of the same dispatcher.
- Counterevidence: Where() takes only a scriptblock, so `.Where` with a string is not a dispatcher; the scriptblock form of ForEach is already reduced as a group.
- Validation: The `.ForEach('Delete')` and `.ForEach('LastAccessTime', ...)` cases are denied; `(1..3).ForEach({ $_ * 2 })` still passes.

### S3 — about_Methods - PowerShell 7.6

- URL: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_methods
- Retrieved: 2026-11-02
- Locator: Long description; Member-access enumeration; Using .NET methods that take filesystem paths
- Fragment: 'Objects have properties, which store data about the object, and methods that let you change the object.' 'the FileInfo object includes the CopyTo method that copies the file'. 'Starting in PowerShell 4.0, method invocation by using dynamic method names is supported.' Member-access enumeration: 'PowerShell automatically enumerates the items in the collection and invokes the method on each item.' '.NET methods use the process working directory. PowerShell cmdlets use the Runspace location.' The page also shows the pure String 'Replace method' example.
- Applicability: Dynamic names (`.$m()`) escape the lexer's identifier-only regex (tool/guard.py:291). Methods that change objects are the norm, not the exception. Method path arguments resolve against a cwd the guard does not track.
- Counterevidence: The page does not spell out the dynamic-name syntax, and the `.$m()` form is not quoted from it.
- Validation: The `$m = 'Delete'; (...).$m()` case is denied after the fix.

### S4 — FileInfo.Replace Method (System.IO) - Microsoft Learn

- URL: https://learn.microsoft.com/dotnet/api/System.IO.FileInfo.Replace
- Retrieved: 2026-11-02
- Locator: method summary; destinationBackupFileName parameter; exceptions (via search result summary, page not fetched)
- Fragment: 'Replaces the contents of a specified file with the file described by the current FileInfo object, deleting the original file, and creating a backup of the replaced file.' 'Pass null to the destinationBackupFileName parameter if you do not want to create a backup of the file being replaced.' Throws if either file is missing. The exceptions list 'The current operating system is not Microsoft Windows NT or later.' Seen through a search summary only.
- Applicability: `replace` is in SAFE_METHODS for String.Replace; on a FileInfo receiver the same name overwrites a destination outside the grant and deletes the source, with the same syntax round 1 closed for CopyTo.
- Counterevidence: Needs the destination to exist already; on non-Windows .NET it may throw. Read via search summary, not fetched.
- Validation: `(Get-Item README.md).Replace('../outside/target.md', $null)` is denied after the fix while `'x'.Replace('x','y')` still passes.

### S5 — tool/guard.py at b9823b3 (lexer method capture, words_effects, simple)

- URL: tool/guard.py
- Retrieved: 2026-11-02
- Locator: lines 291-293 (method capture regex); 540-555 (words_effects); 100 (OPENERS); 566-583 (simple)
- Fragment: Lexer: `re.search(r"(?:\[([\w.`\[\]]+)\])?(?:\.|::)([A-Za-z_]\w*)$", s[start:i])` when `s[i] == "("` sets `w.recv, w.method`. words_effects: `if method == "new": ... SAFE_TYPES ...` `elif method not in SAFE_METHODS: out |= top(f"method {w.method}")`. OPENERS for ps include '{', so scriptblocks are reduced.
- Applicability: This is the only place member effects are judged. It sees only `.Ident(`, and for instance methods it ignores the receiver.
- Counterevidence: Constructors already use receiver-aware checks (SAFE_TYPES), which shows a receiver gate is feasible here.
- Validation: The fix adds the shared member gate here and routes the new channels to it.

### S6 — tool/guard_windows.py at b9823b3 (SAFE_METHODS, aliases, PS_READ_PREFIX, ps_statement)

- URL: tool/guard_windows.py
- Retrieved: 2026-11-02
- Locator: lines 17-27 SAFE_METHODS; 42 aliases; 84-89 PS_READ_PREFIX; 176-177; 184-199 ps_statement
- Fragment: SAFE_METHODS includes 'replace', 'foreach', 'where', 'invoke', 'add', 'insert', 'item', 'getvalue', 'result'. `"%": "foreach-object", "foreach": "foreach-object"`. PS_READ_PREFIX includes "where-", "foreach-". `if canon.startswith(PS_READ_PREFIX): return set(), None`. ps_statement strips `X = value` and returns no command when words[0] starts with '$' or '['.
- Applicability: These are the name-only allow-list and the reader-by-prefix rule that let rounds 1 and 2 through, plus the assignment stripping that likely hides property setters.
- Counterevidence: The assignment path for a group target (`(gi x).IsReadOnly = $true`) was not traced through the lexer, so whether it passes today is unverified.
- Validation: foreach-object gets its own model ahead of PS_READ_PREFIX; the experiment's setter cases are denied.

### S7 — tool/test_guard.py at b9823b3 (ROUND2 regression cases)

- URL: tool/test_guard.py
- Retrieved: 2026-11-02
- Locator: lines 274-296, case 'writing-instance-method'
- Fragment: '# A .NET method that writes is not made safe by its name alone; the receiver is unknown.' 'writing-instance-method': ("(Get-Item README.md).CopyTo('../outside/target.md')", "PowerShell", "판단할 수 없는").
- Applicability: The round 1 test states the right invariant but tests a single name in a single channel, which is why round 2's channel was not caught.
- Counterevidence: none given
- Validation: Replaced or extended by the channel × member parametrised test in the experiment.

## Claims

- C1 (source; S1, S6): ForEach-Object (aliases % and foreach) invokes an arbitrary instance method or property named by -MemberName or by the first positional word, with -ArgumentList as arguments; the guard treats every foreach- cmdlet as a reader without examining that name.
- C2 (source; S1, S2, S3): PowerShell offers several syntactic channels to the same member dispatch: `.Name(...)`, ForEach-Object -MemberName (named, positional, alias, wildcard), the intrinsic `.ForEach('name'[, args])`, and member-access enumeration over collections.
- C3 (interpretation; S2, S3): The intrinsic ForEach() can set a property on every item (`.ForEach('LastAccessTime', value)`) and can call a method by name, so `foreach` in SAFE_METHODS accepts a dispatcher, not a pure method.
- C4 (source; S3, S4): A method name does not fix its effect: String.Replace is pure while FileInfo.Replace overwrites a destination file and deletes the source, so a name-keyed allow-list without the receiver type admits writes.
- C5 (interpretation; S5, S6, S7): Both fixes failed because member effects are judged by name at a single lexical site. Round 1 removed one name and round 2 hit a channel that never reaches that site. A single receiver-aware member gate that every channel routes through, with unknown receivers going to `top` unless the name is harmless for every type, is the fix that can converge.
- C6 (interpretation; S3): Modelling method path arguments precisely would need the process working directory, which .NET methods use instead of the PowerShell location the guard tracks; this argues for `top` over argument modelling as the first fix.

## Core theory

PowerShell members are bound late, against the object's runtime type. about_Methods: objects have 'methods that let you change the object', e.g. FileInfo's 'CopyTo method that copies the file'; 'method invocation by using dynamic method names is supported' from 4.0; member-access enumeration 'invokes the method on each item' of a collection. ForEach-Object's simplified syntax takes 'a property or method name of the object in the pipeline' via -MemberName (position 0, wildcards permitted, with -ArgumentList 'arguments to a method call'). The intrinsic ForEach() has overloads `ForEach(string methodName[, object[] arguments])` and `ForEach(string propertyName, object[] newValue)`, the latter documented as setting LastAccessTime on files. One method name means different things per type: String.Replace is pure, while FileInfo.Replace 'Replaces the contents of a specified file ... deleting the original file'. So a sound static approximation cannot accept a member by name unless the name is harmless for every receiver type the program can reach, and it must cover every syntactic route to the same dispatch.

## Applicability

tool/guard.py:291-293: the lexer sets `w.recv, w.method` only for `(?:\[type\])?(?:\.|::)Ident(` right before `(`. tool/guard.py:540-555 `words_effects` is the only consumer, and it checks `method not in SAFE_METHODS` with no receiver type for instance methods. tool/guard_windows.py:17-27 SAFE_METHODS holds polymorphic or dispatching names: replace, foreach, where, invoke, add, insert, item, getvalue, result. tool/guard_windows.py:42 aliases `%` and `foreach` to foreach-object, and :84-89/176-177 return `set()` for the `foreach-` and `where-` prefixes without looking at -MemberName or positional words. tool/guard_windows.py:184-199 `ps_statement` drops `X =` assignments and returns no command when words[0] starts with `$`/`[` or is a group, so member setters are never seen. The gate belongs in guard.py next to `words_effects` (the shared consumer). The foreach-object model belongs in `ps_cmdlet`, ahead of the PS_READ_PREFIX test.

## Hypothesis

The guard has no single place where a member invocation is judged. Member effects are judged (a) by name only and (b) only where the lexer sees `.Name(`. PowerShell binds a member name to the receiver's runtime type and lets the same invocation be written several ways. Both rounds patched the instance the reviewer named: one name, then (pending) one cmdlet. That cannot converge. Channels known to bypass the check today: ForEach-Object/%/foreach with -MemberName or a positional member name, with -ArgumentList; the intrinsic `.ForEach('Method'[, args])` and `.ForEach('Property', value)`, which pass because `foreach` is safe-listed; `.Name.Invoke()` and `.PSObject.Methods['Name'].Invoke()`, which pass because `invoke` is safe-listed; dynamic and quoted member names `.$m()` and `.'Delete'()`, where the lexer regex needs `[A-Za-z_]` right after the dot, so no method is recorded and nothing is checked; and property assignment such as `(gi x).IsReadOnly = $true`, likely stripped by ps_statement or swallowed as a group (lexer path unverified). The fix is a single member gate, `member(receiver_type_or_None, name, has_args)`, that every channel routes through. It returns no effect only when the receiver type is statically known and safe (string literal, [string], [IO.Path], [math], [regex], [convert], SAFE_TYPES), or when the name is safe for every receiver type: pure read-only names such as tostring, trim, split, tolower, gettype, getfilename and length/name/fullname getters. Names that are polymorphic or that dispatch (replace, foreach, where, invoke, add, insert, clear, item, getvalue, set_*, ...) leave the receiver-independent list. Everything else becomes `top`. ForEach-Object/%/foreach comes out of PS_READ_PREFIX into its own model: scriptblock groups keep being reduced as today, and a -MemberName or positional bare word goes to the gate, where a wildcard name is `top`. Intrinsic `.ForEach`/`.Where`/`.PSForEach`/`.PSWhere` with a non-scriptblock first argument goes to the gate (or is `top`). A dynamic or quoted member name is `top`. Assignment to a member target (anything but a plain `$var` or `$env:`) is `top`.

## Counterevidence

- Precision cost: dropping `replace`, `foreach`, `where` and `invoke` from the receiver-independent list denies common harmless commands (`$s.Replace('a','b')`, `$list.ForEach({...})`, `gci | % Name`). Mitigations: accept when the receiver is a string literal or [string]-typed; keep scriptblock-argument `.ForEach({...})`/`.Where({...})`, since the block is reduced as a group; accept property-name getters in `% Name` with no -ArgumentList from a getter allow-list.
- Even 'pure' names are a heuristic: a type could define Trim() that writes. The guard limits this because Add-Type is top. Add-Member/Update-TypeData ScriptMethod bodies are written as braces groups, which are reduced where they appear, so a renamed mutating body is still analysed at definition.
- Scriptblock channels (`% { $_.Delete() }`, `.ForEach({ $_.Delete() })`) already reach the method check, because braces are PS openers (tool/guard.py:100) and the inner `$_.Delete(` is lexed as a method. Not every channel is open, so the gap is specific to name-as-data channels plus the polymorphic names.

## Alternatives

- Keep removing names from SAFE_METHODS (replace next, then foreach...). Rejected: that is what round 1 did, and it fails as soon as a channel or another polymorphic name is used.
- Model the receiver and arguments, e.g. infer FileInfo from Get-Item and treat CopyTo/Replace/MoveTo arguments as write paths. Rejected as the first step: receiver types come from pipelines, variables and member-access enumeration and are mostly unknowable statically. about_Methods also notes that '.NET methods use the process working directory', not the PowerShell location, so argument paths would need a second cwd the guard does not track. Possible later refinement for literal `(Get-Item lit).CopyTo('lit')`.
- Make every instance-method call `top`. Sound and smallest, but it denies `.Trim()`, `.Split()` and `.ToString()`, which agents write constantly. The receiver-independent pure-name list keeps those.
- Only add a foreach-object model, which is what round 2 asks for literally. Rejected: `.ForEach('Delete')`, `.Delete.Invoke()`, `.$m()`, `.Replace(...)` on FileInfo and property setters stay open.

## Experiment

Add one parametrised test, named for the invariant rather than a round, where each case must be denied (`판단할 수 없는` or `쓰기 루트 밖이다`): `(Get-Item README.md).Replace('../outside/target.md', $null)`; `Get-Item ../outside/target.txt | ForEach-Object -MemberName Delete`; `gi ../outside/target.txt | % Delete`; `gi ../outside/target.txt | foreach -m Delete`; `gi ../outside/target.txt | % -MemberName Del*`; `gi README.md | % CopyTo '../outside/t.md'`; `gi README.md | ForEach-Object -MemberName CopyTo -ArgumentList '../outside/t.md'`; `(Get-Item ../outside/target.txt).ForEach('Delete')`; `(Get-ChildItem ../outside).ForEach('LastAccessTime', (Get-Date))`; `(Get-Item ../outside/target.txt).Delete.Invoke()`; `(Get-Item ../outside/target.txt).PSObject.Methods['Delete'].Invoke()`; `$m = 'Delete'; (Get-Item ../outside/target.txt).$m()`; `(Get-Item ../outside/target.txt).'Delete'()`; `(Get-Item ../outside/target.txt).IsReadOnly = $true`; `(Get-Item ../outside/target.txt).set_IsReadOnly($true)`. Pass controls that must stay allowed: `'a,b'.Split(',')`, `'x'.Replace('x','y')`, `(Get-Date).ToString()`, `Get-ChildItem | ForEach-Object { $_.Name }`, `Get-ChildItem | % Name`, `(1..3).ForEach({ $_ * 2 })`. Run it on c9770fe first: the hypothesis predicts that every case except set_IsReadOnly and the round 1 CopyTo-with-parens form is allowed today. After the fix, all must be denied, the controls pass, and the existing FORMS/ROUND2 corpus is unchanged.

## Result

pending — the fix records what it observed here, with the tests and the commit.

## Unresolved limits

No code was run, so which bypasses pass today is predicted from reading the lexer and models, not observed. That covers property assignment through `ps_statement` and the group path, and whether `.'Delete'()` lexes as a quoted word. FileInfo.Replace's behaviour comes from a search summary of the Microsoft Learn page (not fetched). It requires the destination file to exist and is documented as Windows NT only, so it applies on the Windows host but may throw on other platforms. PSMethod `.Invoke()` and `PSObject.Methods[...]` are not documented on the pages fetched; they rest on general PowerShell knowledge. The exact receiver-independent name list is a judgement call that review should check.
