# Review research — F2, cycle 1

Draft research before the fix of review round 2 of PR #272, written 2026-11-02. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F2 (guard link following): every path effect the guard approves must be the path the OS actually touches when the command runs. `contained()` in tool/guard.py resolves symlinks and junctions with `realpath` once, when the call is checked, against the filesystem as it is before execution. With the default strict=False, a component that does not exist yet is appended unchanged, so `link/target.txt` resolves inside the checkout when `link` is missing. `derive()` judges a whole compound command as one unordered set of effects against that single snapshot. So any statement that creates a link, whether a symlink, a junction or a hard link, makes a later path in the same command resolve at run time to something other than what was checked. Hard links are never visible to realpath, so a hard link to an outside file stays an escape for every later call as well. Round 1: `ln -sf ../other link; rm link/target.txt` passes. Round 2: `cp -s ../outside/target.txt link; printf x > link` passes.

Trigger: repeat, rounds 1, 2.

- [P1] [P1] tool/guard_models.py:181 — Copy link modes can redirect a later write outside the grant

## Failed fixes

- Round 1 (eb6794a to c9770fe): `m_copy` returns top for the program name `ln` (tool/guard_models.py:178-180), and `cmd_builtin` returns top for `mklink` (tool/guard_windows.py:212-214). This closes the two spellings in the finding by program name, not by capability. Link creation is also an option of `cp` (-s, -l, --symbolic-link, --link) and a side effect of copying or extracting existing links: `cp -P/-d/-a` and `cp -R`, which does not follow source symlinks by default; `rsync -a/-l/-H/--link-dest`; `tar -x` and `unzip` with symlink members; git verbs that check out tree content holding symlink blobs; and PowerShell `New-Item -ItemType SymbolicLink|Junction|HardLink`, where `itemtype` is parsed by PS_VALUES and then ignored, so it emits a plain `write(path)`. Round 2 found `cp -s` at once, through the same `m_copy`, three lines below the `ln` check.
- Neither fix gave the effect algebra a way to say that a statement changes how later paths resolve. Every model that can create a link still reports a plain `write` (or nothing), and `reduce()` threads only `cwds` between statements, not link-structure changes. This repeats the pattern recorded for F1 in docs/research/review-tool-call-guard-effect-algebra-f1-1.md: each round patched the command named in the finding, while the invariant belongs to a capability shared across many models.

## Sources

### S1 — cp(1) - Linux manual page (man7.org)

- URL: https://man7.org/linux/man-pages/man1/cp.1.html
- Retrieved: 2026-11-02
- Locator: options -a, -d, -l, -P, -R, -s, --preserve
- Fragment: -a, --archive: 'same as -dR --preserve=all'. -d: 'same as --no-dereference --preserve=links'. -l, --link: 'hard link files instead of copying'. -P, --no-dereference: 'never follow symbolic links in SOURCE'. -s, --symbolic-link: 'make symbolic links instead of copying'. --preserve: 'links' for hard links. -L: 'always follow symbolic links in SOURCE'. -H: 'follow command-line symbolic links in SOURCE'. The page does not state the default for recursive copies.
- Applicability: m_copy in tool/guard_models.py: -s/-l create links directly; -a/-d/-P copy existing symlinks as symlinks. All are link-capable forms that today emit only write/write_tree of the destination.
- Counterevidence: This is the GNU man page; BSD and macOS cp and uutils differ in option spellings and defaults. Silent on the recursive default.
- Validation: Test family 1 includes cp -s, -sf, --symbolic-link, -l, --link, -P, -a and -r, each followed by a write through the link; all must be denied after the fix.

### S2 — bug-coreutils 2014-05 thread on cp symlink dereference wording (coreutils manual text)

- URL: https://lists.gnu.org:443/archive/html/bug-coreutils/2014-05/msg00084.html
- Retrieved: 2026-11-02
- Locator: Paul Eggert quoting the manual; seen via search summary
- Fragment: Current manual text as quoted: 'When copying from a symbolic link, cp normally follows the link only when not copying recursively or when --link (-l) is used.' Older text: 'By default, cp follows symbolic links only when not copying recursively. This default can be overridden with the --no-dereference (-d), --dereference (-L), and -H options.' Since a 2013 change, cp --link dereferences a source symlink before creating the hard link unless -P is given. Read through a search summary; the thread was not fetched.
- Applicability: Plain `cp -r src dst` copies symlinks inside src as symlinks, so it is link-capable even with no link flag. `cp -l` creates a hard link to the source, or to its symlink target.
- Counterevidence: Secondary access only, and the wording may have changed since 2014.
- Validation: `cp -r ../d d; printf x > d/link/f`, where ../d holds a symlink, must be denied.

### S3 — New-Item (Microsoft.PowerShell.Management) - PowerShell 7.6

- URL: https://learn.microsoft.com/powershell/module/microsoft.powershell.management/new-item
- Retrieved: 2026-11-02
- Locator: parameter -ItemType; parameter -Value; Example 7
- Fragment: -ItemType in a FileSystem drive allows 'File', 'Directory', 'SymbolicLink', 'Junction', 'HardLink'. Note: 'Creating a SymbolicLink type on Windows requires elevation as administrator. However, Windows 10 (build 14972 or newer) with Developer Mode enabled no longer requires elevation creating symbolic links.' -ItemType alias: Type. -Value alias: Target. Example 7: 'New-Item -ItemType SymbolicLink -Path .\link -Target .\Notice.txt'; 'The target of the symbolic link can be a relative path.' Alias: ni. -Force: 'Beginning in PowerShell 7.4, this parameter also allows you to overwrite an existing Junction.'
- Applicability: PS_TARGETS['new-item'] in tool/guard_windows.py emits ('write', path) and ignores itemtype, so New-Item and ni link creation is the PowerShell counterpart of the ln and mklink gap.
- Counterevidence: Symbolic links may need elevation or Developer Mode on Windows, but Junction and HardLink do not, so the gap stays open without privileges.
- Validation: New-Item or ni with -ItemType or -Type SymbolicLink/Junction/HardLink, followed by a write through the link, must be denied; a HardLink to an outside literal must be denied on its own.

### S4 — COMPRESS-727 (Apache Commons Compress) - symlink-slip in archive extraction

- URL: https://mail-archive.com/issues@commons.apache.org/msg155089.html
- Retrieved: 2026-11-02
- Locator: issue description: attack, why lexical checks fail, proposed mitigation
- Fragment: 'A symlink entry inside the archive (for example link -> /etc or link -> ../..) followed by a later entry written through that link (link/evil).' The lexical check 'on target/link/evil passes because the string starts with target, but link is a symlink and the write escapes'; the check 'does not look at the file system'. Mitigation: never follow 'a link when materializing a child path'; walk components with no-follow semantics; the policy option ALLOW_WITHIN_ROOT is specified to 'Never follow the new link when writing later entries.'
- Applicability: The guard has the same structure at command level: `resolve()`/`paths()` are lexical, and `contained()` looks at the filesystem before any statement runs, so a link created by an earlier statement (or archive member) is invisible to the check on a later path.
- Counterevidence: The issue is about an in-process extractor that can resolve at use time; the guard is a static pre-execution check and cannot, so it must over-approximate instead.
- Validation: `tar -xf p.tar -C safe; printf x > safe/link/f` and the unzip equivalent must be denied.

### S5 — os.path.realpath - Python documentation

- URL: https://docs.python.org/3/library/os.path.html
- Retrieved: 2026-11-02
- Locator: os.path.realpath, strict parameter
- Fragment: 'By default, the path is evaluated up to the first component that does not exist, is a symlink loop, or whose evaluation raises OSError. All such components are appended unchanged to the existing part of the path.' 'Changed in version 3.8: Symbolic links and junctions are now resolved on Windows.' With strict=True the first error is re-raised.
- Applicability: `contained()` calls realpath with strict=False. A path through a link that does not exist yet keeps its lexical form and is judged inside the checkout. A link that already exists is resolved, including Windows junctions on 3.8+, which is why the cross-call symlink case is sound.
- Counterevidence: realpath does not reveal hard links at all, so even links that already exist are invisible when they are hard links.
- Validation: The loop and compound tests show realpath cannot see links created in the same command; the hard-link-alone test shows why hard links need write(source).

### S6 — CWE-59 records (CIRCL CVE search), tagged alongside CWE-367

- URL: https://cve.circl.lu/cwe/CWE-59
- Retrieved: 2026-11-02
- Locator: search result summary of listed advisories (GHSA-7xmq-g46g-f8pv; uutils cp TOCTOU; filelock CVE-2026-22701)
- Fragment: Search summary: sandbox media paths 'could be validated first and read later through a separate path. A symlink retarget between those steps could cause reads outside sandboxRoot'; the fix moved reads to root-scoped, boundary-safe paths at use time. A TOCTOU flaw in uutils cp 'allows an attacker to bypass no-dereference intent'. The pattern is check, then use, with link creation in between. Seen through search summaries; the MITRE definition page was not fetched.
- Applicability: The guard's check (hook time) and use (command execution) are separated by the command's own earlier statements, which can create links. This is CWE-59/CWE-367 where the attacker is the checked command itself.
- Counterevidence: Aggregator pages, not MITRE's definition; the classic case is a concurrent attacker, whereas here the link comes from the same command, which is easier to model statically.
- Validation: Same as the experiment: link creation followed by use within one command must be denied.

### S7 — CVE-2025-26625 (Git LFS) - writes through crafted symlinks in the working tree

- URL: https://access.redhat.com/security/cve/CVE-2025-26625
- Retrieved: 2026-11-02
- Locator: search result summary of the Red Hat, Debian and ALAS entries
- Fragment: Search summary: git lfs checkout and git lfs pull 'did not check for symbolic links before writing to files in the working tree, so a crafted repository could direct writes to arbitrary locations'. CWE-59. Red Hat: setting core.symlinks to false 'mitigates this vulnerability for new repositories. However, existing repositories containing symbolic or hard links are still vulnerable'. Seen through search summaries only.
- Applicability: Git operations that materialise tree content can create symlinks that a later write follows. That makes m_git write verbs link-capable, and core.symlinks decides whether they create real links.
- Counterevidence: This is a Git LFS bug, not core git checkout; core git has its own protections against writing through symlinks in its own checkout, but not for a later shell statement that writes through the created link.
- Validation: `git checkout other; printf x > link/f` and `git apply p.diff; printf x > link`, where the tree or patch holds a symlink, must be denied.

### S8 — tool/guard.py at c9770fe

- URL: tool/guard.py
- Retrieved: 2026-11-02
- Locator: resolve() line 648; paths() line 688; contained() line 719; reduce() line 493; derive() line 748
- Fragment: `resolve()` comment: '`..` stays: on POSIX it is resolved after symlinks, by `contained`.' contained() docstring: 'Is `path` inside one of `roots` once links, junctions and `..` are resolved?', implemented with `mod.realpath(p)`. reduce() threads `cwds` and `consts` between statements and does `out |= eff`, so effects form an unordered set. derive() returns `{READ} | reduce(...)`.
- Applicability: Link resolution happens once, at check time, against the filesystem before execution. There is no channel for a statement to tell the guard that later paths resolve differently.
- Counterevidence: The cwd threading shows that ordered state between statements is possible here, but loops, pipelines and `&` make an ordered rule unsound for links.
- Validation: The fix should add the link rule where the set is judged (derive or the caller of outside), not in each model's later-path handling.

### S9 — tool/guard_models.py at c9770fe

- URL: tool/guard_models.py
- Retrieved: 2026-11-02
- Locator: m_copy lines 176-190; m_tar 210-225; m_archive 228-238; m_ssh 536-558; m_git 616-692; MODELS 740-752
- Fragment: m_copy: `if name == "ln": return top("ln creates a link the guard cannot follow"), None`; for cp, short flags are joined only to test r/R/a for recursion, and -s/-l/-P/-d are never examined. m_tar -x: `writes("write_tree", -C or here)`. unzip: `writes("write_tree", -d or here)`. m_ssh: rsync's last operand is `paths("write", w, ctx)`. m_git: every GIT_WRITE verb emits `paths("write", here(g), g)`.
- Applicability: These are the link-capable forms the round 1 fix left as plain write or write_tree. Round 2's `cp -s` is the first of them.
- Counterevidence: Some of these (cp -r, tar -x, git) are only dangerous when combined with another path in the same command; alone they are judged soundly on the next call.
- Validation: Family 1 of the experiment covers each model listed here.

### S10 — tool/guard_windows.py at c9770fe

- URL: tool/guard_windows.py
- Retrieved: 2026-11-02
- Locator: PS_TARGETS line 61; PS_VALUES line 70; cmd_builtin mklink lines 212-214
- Fragment: `"new-item": [("write", ("path", "name"), 0)]`; `itemtype` is listed in PS_VALUES, so its value is consumed and never consulted. cmd: `if name == "mklink": return top("mklink creates a link the guard cannot follow"), None`.
- Applicability: New-Item/ni link creation passes as a plain write: the PowerShell form of the gap that was closed for cmd's mklink only.
- Counterevidence: None found; this is a plain omission.
- Validation: New-Item -ItemType SymbolicLink/Junction/HardLink cases in experiment family 1 and 2.

### S11 — tool/guard_grant.py at c9770fe

- URL: tool/guard_grant.py
- Retrieved: 2026-11-02
- Locator: outside() lines 144-163
- Fragment: `kind, target = effect` and each effect is judged alone: top is refused, spawn is checked against executables, and write/delete/write_tree are checked with `contained()` against protected paths and write roots.
- Applicability: Because outside() sees one effect at a time, a rule about combinations (a link effect plus another path effect) has to sit above it, over the whole effect set.
- Counterevidence: A link effect could instead be judged here as an ordinary write at its location; that is the right treatment when it is alone.
- Validation: After the fix, single-effect judgments are unchanged; only mixed sets become top.

## Claims

- C1 (source; S5, S8, S4): The guard resolves links once, at check time, with non-strict realpath. A path through a link that does not exist yet keeps its lexical form, so a link created earlier in the same command is invisible to the check on a later path.
- C2 (source; S1, S2, S7, S9): Link creation is not limited to ln and mklink: GNU cp creates links with -s/-l and copies existing symlinks as symlinks with -P/-d/-a and, by default, -R; git operations that materialise tree content can create symlinks. The models for these emit only plain write or write_tree today.
- C3 (source; S3, S10): PowerShell New-Item creates SymbolicLink, Junction and HardLink items in the file system, and the guard's new-item model ignores -ItemType and emits a plain write.
- C4 (source; S4, S6): Creating a link and then writing through it in one unit of execution is the known symlink-slip / CWE-59 TOCTOU pattern, and lexical path checks cannot catch it.
- C5 (interpretation; S8, S11, S5): Both fixes failed because they removed program names, not the capability: the effect algebra has no effect for a change to name resolution, and outside() judges effects one at a time, so the shared fix is a link effect plus an order-insensitive rule over the whole effect set.
- C6 (interpretation; S1, S2, S9): A hard link from a literal source should also count as a write to that source, because realpath never reveals hard links and the escape persists across later calls.

## Core theory

Link following, CWE-59, is a time-of-check/time-of-use (TOCTOU) weakness, often filed with CWE-367: a path is validated first and used later, and a symlink created or retargeted in between redirects the use. The fix the advisories describe is to resolve at use time, or to keep untrusted code from creating links on the checked paths. The archive form of this is called 'symlink-slip' in Apache Commons Compress COMPRESS-727: a symlink entry followed by a later entry written through it. A lexical check on target/link/evil passes 'because the string starts with target, but link is a symlink and the write escapes', since the check 'does not look at the file system'. The proposed remedy never follows a link created during the same operation ('Never follow the new link when writing later entries'). Python's realpath resolves only components that exist and appends the rest unchanged, so it cannot see a link that does not exist yet. A static guard cannot resolve at use time, so it must over-approximate: once an operation may create a link, any path touched in the same unit of execution is unknown.

## Applicability

tool/guard.py: `contained()` (line 719) is the only place link resolution happens. It runs realpath when the command is checked. `paths()` (line 688) and `resolve()` (line 648) are lexical. `reduce()` (line 493) threads only cwds and consts between statements and unions effects into an unordered set. `derive()` (line 748) returns that set, and guard_grant.outside() judges each effect in isolation (tool/guard_grant.py:144). tool/guard_models.py `m_copy` (176-190) treats `cp -s`, `cp -l`, `cp -a`, `cp -P` and `cp -R` as write/write_tree of the destination only. `m_tar` (210-225) emits write_tree(-C dir) for -x, and `m_archive` (228-238) emits write_tree(-d) for unzip, with no link effect. `m_ssh` (536-558) emits a plain write for the rsync destination. `m_git` (616-692) emits write(<checkout>) for every write verb. tool/guard_windows.py PS_TARGETS 'new-item' (line 61) emits ('write', path) whatever the -ItemType. The cmd builtins return top for `mklink` only. Unmodelled link programs (`link`, `fsutil hardlink`, `junction`) fall to ('spawn', name) and are blocked unless allow-listed in executables. Symlinks that already exist on disk at check time are handled by realpath. The gap is links created within one command, and hard links at any time.

## Hypothesis

The guard's soundness rests on an unstated assumption: name resolution, meaning symlinks, junctions and hard links, does not change between the check and the end of the command. Link creation is a change to name resolution, not a write to a file, and the algebra has no effect for it. Enumerating link-creating programs cannot converge, because the capability is spread across options of copy, archive, sync, VCS and PowerShell item models. The fix belongs at the shared level. (1) Add one effect kind, for example ('link', where), and have every link-capable form emit it: ln (top today, keep), `cp` when -s/-l/--symbolic-link/--link or any symlink-preserving mode (-P/-d/-a/--no-dereference/--preserve=links|all, or -R/-r without -L) is set, `rsync` with -a/-l/-H/--links/--link-dest, `tar -x`, `unzip`, the git verbs that materialise tree content (checkout, switch, restore, reset, merge, pull, rebase, cherry-pick, revert, stash, am, apply, clone, worktree add, submodule), `patch` (git-style diffs can create symlinks), `New-Item`/`ni` with -ItemType or -Type SymbolicLink/Junction/HardLink, and `mklink`. (2) Add one order-insensitive rule at the command level, in `derive()` or wherever the effect set is judged: if the set holds a link effect and any other write, delete or write_tree path effect, the command is top. It must be order-insensitive because a `for`/`while` loop runs a later statement before an earlier one, and pipeline members and `&` jobs run concurrently, so a source-order flag threaded like `cwds` would miss `for i in 1 2; do printf x > link; cp -s ../o link; done`. (3) A hard link from a literal source also emits write(source), or write_tree(source) for `cp -lR` and `rsync --link-dest=DIR`. A later write through the hard link changes the source inode, and realpath will never reveal it in a later call. A link effect alone, with no other path effect, can stay judged as a write at its own location: a symlink or junction created there is resolved by realpath on every later call.

## Counterevidence

- For symlinks and junctions, the cross-call case is already sound: once the link exists, the next call's realpath resolves it. Both reviewer reproductions are within one command, which supports a command-level rule rather than permanent top for every link-capable form.
- Making the command top whenever a link effect appears beside another path effect costs precision on common commands: `git checkout -b x && printf ... > f`, `tar -xf a.tar -C out && rm a.tar`, `cp -r src dst && touch dst/x`. Git write verbs appear constantly in agent commands. One mitigation is to emit the link effect only for verbs that materialise tree content, and only when core.symlinks may be true. On Windows, Git commonly defaults core.symlinks to false, but Git Bash or WSL may not.
- On Windows, creating a symbolic link needs elevation or Developer Mode (New-Item docs). Junctions and hard links need neither, so that requirement does not remove the risk.
- GNU tar extracts symlink members by default and, by default, replaces an existing symlink that collides with a directory member. Its default protections (stripping leading slashes, refusing '..') apply to member names. Whether they constrain hard-link member targets in every version was not confirmed, so hard links from tar are not proven to be confined to the extraction directory.

## Alternatives

- Keep adding program and option names to the top list (cp -s, then cp -l, then New-Item -ItemType...). Rejected: this is exactly what failed twice, and the capability list above shows at least six more models with the same hole.
- A source-order flag in reduce(), threaded like cwds, so that statements after a link-capable statement see every path as top. Fewer false denials for commands where the link comes last, but unsound for loops (a later iteration runs an earlier statement after the link), for pipelines and for `&` background jobs, which run concurrently. It could refine the order-insensitive rule only for straight-line `;`/`&&` sequences outside any loop.
- Top for every link-capable form on its own, as was done for ln and mklink. Sound and simplest, but it would deny plain `tar -xf a.tar -C out`, `unzip`, `cp -r` and git checkouts even when nothing else in the command touches a path, which is too much over-denial while the cross-call case is already sound.
- Follow the link statically: model `ln -s T L` / `cp -s T L` as write_tree(resolve(T relative to dirname(L))). Precise for literal operands, but impossible for links whose targets come from data (archives, copied trees, git blobs), so it cannot be the shared mechanism. Still useful for the hard-link rule, where the source is a literal operand.
- Run-time backstop: after a command runs, scan the write roots for new symlinks or junctions that point outside the grant, or for files with st_nlink > 1. Strongest, but it is not part of the static pre-execution guard and acts only after the write has happened. Worth adding as a second layer, not instead of the static fix.

## Experiment

Add one parametrised test with two families. In the first, each link-capable form X is followed by a write through the link. The forms X: `ln -sf ../o link`, `cp -s ../o/t link`, `cp -sf ../o/t link`, `cp --symbolic-link ../o/t link`, `cp -l ../o/t link`, `cp --link ../o/t link`, `cp -P ../somelink link`, `cp -a ../d d`, `cp -r ../d d`, `rsync -a ../d/ d`, `tar -xf p.tar -C safe`, `unzip p.zip -d safe`, `git apply p.diff`, `git checkout other`, PowerShell `New-Item -ItemType SymbolicLink -Path link -Target ..\o`, `ni -Type Junction link -Value ..\o`, `New-Item -ItemType HardLink -Path link -Value ..\o\t`, and cmd `mklink /J link ..\o`. Each X is combined with `; printf x > link`, `; printf x > link/f` or `; printf x > safe/link/f` as fits the form, plus the loop form `for i in 1 2; do printf x > link; cp -s ../o link; done`. Assert every case is denied. In the second family, assert that a hard link alone from an outside literal source (`cp -l ../outside/t link`, `New-Item -ItemType HardLink ... -Value ../outside/t`) is denied, while `tar -xf a.tar -C out` alone and `cp -r src dst` alone still pass. Run it on c9770fe first: it should fail for every form except ln and mklink, which confirms that the fixes covered names, not the capability. After the fix, everything is denied and the existing corpus still matches, apart from compound commands that mix a link-capable form with other writes. Optionally, on a POSIX scratch directory outside the repository, run `cp -s ../outside/target.txt link; printf x > link` and `tar` symlink-slip once to confirm the escape actually happens.

## Result

pending — the fix records what it observed here, with the tests and the commit.

## Unresolved limits

GNU cp's default for recursive copies (source symlinks not followed unless -L/-H) comes from the coreutils manual wording quoted in a 2014 bug-coreutils thread, seen through a search summary. gnu.org returned HTTP 429, and the man7 page does not state the recursive default. Whether GNU tar and bsdtar keep hard-link member targets inside the extraction directory without -P was not confirmed. MSYS/Git Bash `ln -s` may copy instead of linking, depending on the MSYS winsymlinks setting, which changes the Windows-host risk but not the POSIX one. The git verb list for tree materialisation and the effect of core.symlinks per platform were not verified against git documentation. The CWE and Git LFS evidence comes from aggregator pages and search summaries, not MITRE or git-lfs primary pages. No code was run in this research; the escape itself was reproduced only by the reviewer.
