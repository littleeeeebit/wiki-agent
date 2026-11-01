# Review research — F1, cycle 1

Draft research before the fix of review round 2 of PR #272, written 2026-11-02. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F1 (guard.destination-models): the write effects must cover the files a command actually writes, including protected files under a destination directory. The grant says `.wiki/adapter.toml` and `<checkout>/verification.toml` are never writable. But a command that writes *into* a directory still emits only `write(<dir>)`. The containment check accepts that because the directory sits inside a write root. Round 3 reproductions: `Copy-Item -LiteralPath ../adapter.toml -Destination .wiki` emits only `write(<checkout>/.wiki)`, and PowerShell overwrites `.wiki/adapter.toml`. `tar -xPf payload.tar -C safe` emits only `write_tree(safe)`, but with -P an absolute member can land on the protected adapter outside `safe`. Either way the next call is judged against a grant the agent has already rewritten.

Trigger: repeat, rounds 1, 2.

- [P1] [P1] tool/guard_windows.py:155 — Protected destinations remain unchecked outside the repaired cp model

## Failed fixes

- Round 1 (head eb6794a) answered 'cp /tmp/adapter.toml .wiki/'. It added `into_dir` to `m_copy`: a destination with a trailing slash also names `<dir>/<basename>`. That fixed one spelling inside one model. Round 2 reproduced the same thing without the trailing slash (`cp /tmp/adapter.toml .wiki`).
- Round 2 (head c9770fe) extended `into_dir`: the last operand of a copy with two or more operands, and the `-t` target, also name `<dir>/<basename>`. It added a `write_tree` kind, which `outside()` checks for protected descendants, and used it for `cp -r`, `tar -x` and `unzip`. That covered the POSIX `cp`/`mv`/`install` family only. `Copy-Item`/`Move-Item` (`PS_TARGETS` in guard_windows.py), the cmd builtins `copy`/`xcopy`/`robocopy`/`move`, and the network or download destinations never call `into_dir`. They still emit the bare directory. `tar -x` now emits `write_tree(-C dir)`, but nothing handles `-P`/`--absolute-names`, which lets members escape that directory. So round 3 raised the same invariant again through `Copy-Item` and `tar -P`. Both rounds fixed the model named in the finding instead of the shared meaning of the effect.

## Sources

### S1 — Copy-Item (Microsoft.PowerShell.Management) - PowerShell 7.6

- URL: https://learn.microsoft.com/powershell/module/microsoft.powershell.management/copy-item
- Retrieved: 2026-11-02
- Locator: Example 1; Example 2 and its Note; parameter -Destination
- Fragment: Example 1: 'This example copies the mar1604.log.txt file to the C:\Presentation directory.' -Destination: 'Specifies the path to the new location. The default is the current directory. To rename the item being copied, specify a new name in the value of the Destination parameter.' Example 2 Note: 'If the path C:\Drawings doesn't exist the cmdlet copies all the files from the Logfiles folder tree into a single folder C:\Drawings, overwriting any files with the same name.' Example 3 Note: with a wildcard path and a destination that does not exist, 'all files in C:\Logfiles are copied to C:\Drawings\Logs as a file.'
- Applicability: Copy-Item and Move-Item in guard_windows.py PS_TARGETS. An existing-directory destination writes <dest>/<basename(src)>. A missing Destination writes into the current directory.
- Counterevidence: Wildcard sources and -Recurse with a missing destination behave differently from the basename rule (contents are copied, or the destination is created or overwritten). Those forms need write_tree or top, not the basename rule.
- Validation: The registry-walk test runs `Copy-Item -LiteralPath ../adapter.toml -Destination .wiki` and `Copy-Item ../verification.toml` with no Destination. Both must be denied with the protected-file reason after the fix and must pass on c9770fe before it.

### S2 — GNU tar manual - Absolute File Names (mirror of the GNU tar info manual)

- URL: https://www-zeuthen.desy.de/dv/documentation/unixguide/infohtml/tar/absolute.html
- Retrieved: 2026-11-02
- Locator: section 'Absolute File Names', option -P / --absolute-names
- Fragment: On extraction tar 'strips any leading slashes (/) from the member name'; a member named /etc/passwd is extracted as etc/passwd. 'File names containing .. can cause problems when extracting'; tar warns about them when creating an archive and rejects attempts to extract them. -P/--absolute-names: 'Do not strip leading slashes from file names, and permit file names containing a .. file name component.' Condition: this describes GNU tar only. The primary gnu.org page returned HTTP 429, so this mirror was read, and it may be older than the current manual.
- Applicability: m_tar in guard_models.py. Without -P, `write_tree(-C dir)` covers extraction. With -P or --absolute-names, members may land anywhere, so the effect must be top.
- Counterevidence: The mirror may predate current GNU tar. Options such as --transform were not checked for whether they can create '..' after the check runs.
- Validation: `tar -xPf p.tar -C safe` and `tar --absolute-names -xf p.tar -C safe` must reduce to top after the fix. `tar -xf p.tar -C safe` must stay write_tree(safe).

### S3 — bsdtar(1) - FreeBSD Manual Pages

- URL: https://man.freebsd.org/cgi/man.cgi?query=bsdtar
- Retrieved: 2026-11-02
- Locator: option '-P, --absolute-paths'
- Fragment: 'Preserve pathnames. By default, absolute pathnames (those that begin with a / character) have the leading slash removed both when creating archives and extracting from them. Also, tar will refuse to extract archive entries whose pathnames contain .. or whose target directory would be altered by a symlink. This option suppresses these behaviors.'
- Applicability: m_tar on Windows, where tar.exe is bsdtar (S4). The long option spelling here is --absolute-paths, not GNU's --absolute-names, so the model must treat both spellings and -P as top.
- Counterevidence: The older macOS bsdtar page lists no long form, so which spellings are accepted depends on the version. Treating every spelling as top is safe whatever the version.
- Validation: `tar --absolute-paths -xf p.tar -C safe` must reduce to top after the fix, alongside the -P case.

### S4 — The Windows Process Journey - tar.exe (BSD tar Archive Tool)

- URL: https://medium.com/@boutnaru/the-windows-process-journey-tar-exe-bsd-tar-archive-tool-d307a4412c46
- Retrieved: 2026-11-02
- Locator: search result summary; the article itself was not fetched
- Fragment: The search summary says the binary is at %windir%\System32\tar.exe and is built on libarchive, and that Microsoft added it in Windows 10 version 1803. This is a secondary source; no Microsoft page confirming it was found.
- Applicability: The guard runs on Windows (win32 in this checkout), so `tar` in a PowerShell or cmd call is bsdtar and follows S3's -P semantics.
- Counterevidence: Secondary source only. A Git Bash session may put GNU tar on PATH ahead of System32, so the same command can run either implementation.
- Validation: Covered by treating -P and both long spellings as top whichever tar runs. The test does not depend on which binary is on PATH.

### S5 — ZipFile.ExtractToDirectory Method (.NET API reference)

- URL: https://learn.microsoft.com/dotnet/api/system.io.compression.zipfile.extracttodirectory
- Retrieved: 2026-11-02
- Locator: Exceptions: IOException; seen through search summary
- Fragment: Search summary of the reference: IOException is thrown when 'extracting an archive entry would create a file outside the directory specified by destinationDirectoryName (for example, this might happen if the entry name contains parent directory accessors)'. The same exception is thrown for other reasons too (duplicate entry names, an existing file without overwrite). After an error, 'the archive will remain partially extracted'. Not fetched directly.
- Applicability: Expand-Archive in PS_TARGETS. If it extracts through this API, write_tree(DestinationPath) is sound for path traversal, and Expand-Archive needs no top for absolute or '..' members.
- Counterevidence: I did not confirm that Expand-Archive calls ExtractToDirectory on every PowerShell version (5.1 vs 7.x). Partial extraction before the exception still writes inside the destination.
- Validation: `Expand-Archive x.zip -DestinationPath .wiki` must be denied, because write_tree(.wiki) contains the protected adapter. `Expand-Archive x.zip -DestinationPath out` must pass as write_tree(out).

### S6 — Cousot, Abstract Interpretation (SEFM 2007) and UW CSE599 abstract interpretation slides

- URL: https://www.di.ens.fr/~cousot/publications.www/Cousot-SEFM-2007.pdf
- Retrieved: 2026-11-02
- Locator: search result summary: soundness criterion; local-to-global soundness
- Fragment: Search summary: abstract interpretation computes an abstract semantics whose properties over-approximate the concrete semantics. An abstraction is sound when a proof in the abstract domain establishes the concrete property. A course note attributes to Cousot and Cousot the result that if every atomic statement's abstract semantics is locally sound, the analysis is sound. Read through summaries only; the papers were not fetched.
- Applicability: Each guard model is an atomic transfer function from argv to effects. A model that emits write(dir) for a write to dir/x is not locally sound against outside()'s containment test, and fixing other models cannot make up for it.
- Counterevidence: The guard is not a fixpoint analysis. It reduces a finite parse once, so only the local-soundness idea carries over, not the lattice or iteration machinery.
- Validation: Check local soundness per model with the registry-walk test: every destination-taking model, given a protected file's parent as destination, must produce an effect that outside() denies.

### S7 — tool/guard_models.py at c9770fe

- URL: tool/guard_models.py
- Retrieved: 2026-11-02
- Locator: into_dir (line 149); m_copy (line 176); m_tar (line 210); m_ssh (line 536); m_wget (line 514); m_curl (line 498)
- Fragment: `into_dir` names `<dir>/<source-basename>` but is called only from `m_copy`. `m_tar` sets `write_tree` for -x and treats -I/--to-command and similar as top, but never checks -P/--absolute-names. `m_ssh` writes the last rsync/scp operand as a plain `paths('write', w, ctx)`. `m_wget` and `m_curl` emit plain `write` for -P/--output-dir and `write(here)` for downloads named by the remote.
- Applicability: These are the other POSIX places where the bare-directory stand-in survives the round 2 fix.
- Counterevidence: For rsync and scp, the destination file name follows the same basename rule only for a single file. A directory source needs write_tree.
- Validation: The registry walk includes `rsync ../adapter.toml .wiki`, `scp host:adapter.toml .wiki`, `wget -P .wiki URL` and `curl --output-dir .wiki -O URL`. All must be denied after the fix.

### S8 — tool/guard_windows.py at c9770fe

- URL: tool/guard_windows.py
- Retrieved: 2026-11-02
- Locator: PS_TARGETS (lines 57-69); ps_cmdlet PS_TARGETS branch (lines 144-156); cmd_builtin copy/xcopy/robocopy/move (lines 217-221)
- Fragment: 'copy-item': [('write', ('destination',), 1)] and 'move-item': [('delete', PATH, 0), ('write', ('destination',), 1)], written via `writes(kind, found, ctx)` with no basename step. When neither -Destination nor a second positional is given, `found` is empty and no write is emitted. cmd: `dest = ops[1:2] if name == 'robocopy' else ops[-1:] ...; return writes('write', dest, ctx)`.
- Applicability: This is the round 3 reproduction site (finding evidence guard_windows.py:155) and the same pattern in the cmd builtins.
- Counterevidence: robocopy's file filters are arguments after the destination. The basename rule must take names from those filters, or use write_tree when there are none.
- Validation: The registry walk must include Copy-Item, Move-Item and cmd copy/xcopy/robocopy/move into `.wiki`, and Copy-Item with no Destination from `..`. All must be denied after the fix.

### S9 — tool/guard_grant.py at c9770fe

- URL: tool/guard_grant.py
- Retrieved: 2026-11-02
- Locator: PROTECTED (line 34); outside() (lines 144-163)
- Fragment: PROTECTED = ('.git', '.claude', '.codex', '.wiki/adapter.toml', 'verification.toml'). In outside(): `reaches_protected = kind in ('delete', 'write_tree') and any(contained(p, [target]) for p in g.protected)`. A plain `write` is denied only when the target lies under a protected path, never when a protected path lies under the target.
- Applicability: This is the shared judge every model routes through, and it gives `write` its exact-file meaning. Because verification.toml sits at the checkout root, `write(<checkout>)` used as a stand-in also hides a protected file.
- Counterevidence: Treating every write as reaching descendants would deny all git write verbs, which emit write(<checkout>). The shared check cannot simply be widened without a separate kind for git's repository writes.
- Validation: After the fix, the git corpus entries (commit, add, checkout) must still pass. That shows the fix went into the models and the destination helper, not into widening outside() for plain writes.

### S10 — Task spec rounds for tool-call-guard-effect-algebra (PR #272)

- URL: raw/specs/wiki-agent/tool-call-guard-effect-algebra.json
- Retrieved: 2026-11-02
- Locator: rounds[].items with component 'guard.destination-models'; rounds[1].disposition F1
- Fragment: Round 1: 'cp /tmp/adapter.toml .wiki/ emits only write(<checkout>/.wiki)'. Round 2: 'cp /tmp/adapter.toml .wiki passes because into_dir recognizes only syntactic directory destinations ... Recursive copies and archive extraction likewise emit directory writes'. Disposition: 'into_dir now names <dir>/<basename> for 2+-operand cp/mv/install, and recursive copy / tar-x / unzip emit write_tree'. Round 3: 'The cp regression is fixed, but Copy-Item -LiteralPath ../adapter.toml -Destination .wiki still emits only write(<checkout>/.wiki) ... tar -xPf payload.tar -C safe checks only safe'.
- Applicability: This is the record of the failed fixes. Each round's fix was scoped to the command spelled in the finding, while the stated invariant ('write effects cover actual destination files and protected descendants') stayed the same.
- Counterevidence: The reviewer's round 3 examples are just two points. Other destination models may already be precise in ways I did not see in this reading.
- Validation: The next review round must not raise guard.destination-models again. The registry-walk test is the receipt that the invariant is checked across models, not per spelling.

## Claims

- C1 (source; S1, S8): Copy-Item copies a file into an existing destination directory, and its Destination defaults to the current directory. So `Copy-Item src -Destination .wiki` writes `.wiki/<basename(src)>`, and `Copy-Item src` with no Destination writes `./<basename(src)>`.
- C2 (interpretation; S8, S1): The Copy-Item and Move-Item models emit only `write(<destination>)` and emit nothing when the destination is omitted. They miss the protected file the cmdlet actually writes.
- C3 (source; S2, S3): By default GNU tar and bsdtar strip leading '/' and refuse '..' members on extraction, so files stay under the extraction directory. -P (GNU --absolute-names, bsdtar --absolute-paths) suppresses this, and the model must reduce it to top.
- C4 (interpretation; S3, S4): On this Windows host, `tar` is likely libarchive bsdtar, so the --absolute-paths spelling must be handled as well as GNU's.
- C5 (source; S5): .NET ZipFile.ExtractToDirectory throws instead of extracting an entry outside the destination. If Expand-Archive uses it, write_tree(DestinationPath) is sound for path traversal.
- C6 (interpretation; S6): Soundness of the whole guard requires each model to be locally sound against the judge. One model that emits a directory for a write to its child breaks soundness, whatever the other models do.
- C7 (source; S7, S8): Outside m_copy, several destination-taking models (Copy-Item, Move-Item, cmd copy/xcopy/robocopy/move, rsync/scp, wget -P, curl --output-dir) still emit the bare-directory stand-in. tar never checks -P.
- C8 (source; S9): outside() checks protected descendants only for delete and write_tree, so a plain write(dir) is treated as the exact file dir. This is where the two meanings of write diverge.
- C9 (interpretation; S10, S9, S6): The earlier fixes failed because each one repaired the model named in the finding, while the invariant concerns the effect kind every destination model shares. A check that walks the model registry, not a list of spellings, is what would show it closed.

## Core theory

Abstract interpretation is sound when the abstract semantics over-approximates the concrete one. A proof made in the abstract domain then holds for every real execution. Global soundness follows from local soundness: if each statement's abstract transfer is locally sound, the whole analysis is (Cousot). Here the abstract element `write(p)` is judged by a containment test that is sound only if the concrete write set is exactly {p}. A model that writes {p/x : x unknown} needs an element whose test covers descendants. Otherwise that one transfer is not locally sound, and no amount of per-command patching elsewhere restores global soundness. The tools' own documentation gives the concrete semantics the models must cover. Copy-Item copies a file *into* an existing directory, renames when Destination is a new name, and defaults Destination to the current directory. GNU tar and bsdtar strip a leading '/' and refuse '..' members by default, so extraction stays under the extraction directory unless -P suppresses those protections.

## Applicability

Where the bare-directory stand-in appears: guard_windows.py `PS_TARGETS['copy-item'] = [('write', ('destination',), 1)]` and `move-item`, written by `writes(kind, found, ctx)` with no basename step; `cmd_builtin` copy/xcopy/robocopy/move (`writes('write', dest, ctx)`); `m_ssh` (last rsync/scp operand as a plain `write`); `m_wget` (`-P` as a plain write, and `paths('write', here(ctx))` when no `-O` is given); `m_curl` (`--output-dir`, and `-O` as `write(here)`); `m_gh` (`download` with `-D`). `m_tar` reads `-C` but never checks `-P`/`--absolute-names`. `guard_grant.outside()` line 158 restricts the descendant check to `kind in ('delete', 'write_tree')`. The protected set includes `<checkout>/verification.toml`, at the checkout root. So any model that emits `write(<checkout>)` for 'something in the current directory' can overwrite it, not only models that write into `.wiki`.

## Hypothesis

The effect algebra uses one effect kind, `write(p)`, for two different things. Some models mean the exact file p (redirections, `touch`, `Set-Content`). Others mean 'some child of p whose name the model did not work out': a copy or move destination that may be an existing directory, a download or extraction directory. The shared judge, `guard_grant.outside()`, reads every `write` as the exact file. It checks for protected descendants only for `delete` and `write_tree`. Every model that passes a destination directory to `writes('write', ...)` is therefore unsound against protected paths. Fixing models one at a time cannot converge: in guard_models.py, guard_windows.py and the cmd builtins, every destination-taking model is a separate place that still emits the stand-in. The fix belongs at the shared level. (1) Add one destination helper, generalised from `into_dir`. For a copy or move it emits `write(dest)` plus `write(dest/basename(src))` for each source, and every copy/move model routes through it: `m_copy`, `Copy-Item`, `Move-Item`, cmd `copy`/`move`/`xcopy`/`robocopy`, and the last operand of `rsync`/`scp`. (2) A writer whose file names come from data emits `write_tree(dir)`: extraction, `Expand-Archive`, `wget -P`, `curl --output-dir`/`-O`, `gh ... download -D`. (3) A flag that lets data choose paths outside the destination is `top`: tar `-P`/`--absolute-names`/`--absolute-paths`. (4) A missing destination defaults to the current directory, as Copy-Item documents. Today `Copy-Item ../verification.toml` emits no write at all, because `pos[1:2]` is empty.

## Counterevidence

- Extending `write_tree` to download and extraction models costs precision. The checkout root holds verification.toml, so `write_tree(<checkout>)` is always denied. `tar -xf a.tar` in the checkout is already denied for this reason, and `wget URL` or `curl -O URL` in the checkout would be newly denied. That is sound but may block ordinary work. Deriving the file name from the URL path for wget/curl -O is a more precise option.
- Copy-Item's directory semantics have exceptions the basename rule does not model. A wildcard source copies the contents (descendants unknown). With `-Recurse` and a destination that does not exist, Copy-Item creates the destination instead of copying into it. Wildcards are already tainted as globs and become `top`, which limits the gap, but `-Container:$false` flattening, filters and -Recurse need a check of their own. With -Recurse, `write_tree` is the safe choice.
- Without -P, GNU tar extracts symlink members last and may use placeholders. Members stay under -C only because of these default protections. Other options (`--transform`, `--keep-directory-symlink`) were not checked and might still change where files land.

## Alternatives

- A filesystem backstop in `outside()`: treat any `write` whose target is an existing directory at check time as `write_tree`. It is attractive because every model routes through it, and it would have caught all three rounds. Not first: `m_git` emits `write(<checkout>)` for every write verb, and `m_gh` does the same for `pr checkout`. The checkout contains verification.toml, so every commit would be denied. It needs a separate effect kind for the repository writes those models emit first. It also reads filesystem state at hook time, so a directory created earlier in the same command is missed. Worth adding as a second layer after the destination helper.
- Keep patching each model as the reviewer finds it. Rejected: three rounds show the reviewer can always name a model that has not been patched yet. The invariant is a property of the effect kind, not of any one command.
- Reduce every copy, move or extraction into an existing directory to `top`. Sound and simple, but it denies `cp a.txt docs/` and most ordinary copies in the corpus. That is too much over-denial while a precise basename rule exists.

## Experiment

Add one parametrised test that walks the model registry instead of listing spellings. For every entry in `MODELS`, `PS_TARGETS` and `CMD_BUILTINS` that has a destination slot, build `<cmd> <dir>/adapter.toml .wiki`, and `<cmd> <dir>/verification.toml .` where the command takes no destination (Copy-Item with -Path only). Assert each one is denied with the protected-file reason, not passed. Also assert: the round 3 repros (`Copy-Item -LiteralPath ../adapter.toml -Destination .wiki`, `tar -xPf payload.tar -C safe`, `tar --absolute-names -xf p.tar -C safe`) are denied, with `top` for the tar cases; `cmd /c copy ..\adapter.toml .wiki` and `robocopy .. .wiki adapter.toml` are denied; and `Copy-Item ../verification.toml` with no Destination is denied. Run it on c9770fe first: it should fail for Copy-Item, Move-Item, the cmd builtins and tar -P, which confirms the hypothesis. It should pass for `cp`/`mv`, which shows the earlier fixes worked only where they were applied. After the fix, the 260-command corpus must still match, apart from entries whose expected effects gain a `<dest>/<basename>` write.

## Result

pending — the fix records what it observed here, with the tests and the commit.

## Unresolved limits

unzip's own handling of '../' and absolute members (Info-ZIP strips them unless `-:` is given) was not confirmed from a primary source in this session. The ZipFile/Expand-Archive claim rests on Microsoft's .NET documentation, seen only through search summaries. I did not confirm that Expand-Archive calls ExtractToDirectory on every PowerShell version. Windows tar.exe being bsdtar comes from secondary sources only. The Cousot material was read through search summaries, not the full paper. The registry walk catches models that have a destination slot, but not a model that writes without declaring one. Read-only status for each model is still a manual decision.
