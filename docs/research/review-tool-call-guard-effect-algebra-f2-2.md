# Review research — F2, cycle 2

Draft research before the fix of review round 3 of PR #272, written 2026-11-02. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F2: every path the guard approves must be the path the OS actually touches when the command runs. `contained()` resolves links once, before the command runs, using non-strict realpath. A statement that creates or moves a link therefore changes, unseen, where a later path in the same command lands. A hard link also changes it invisibly in every later call, because realpath can never see hard links. Round 3 shows two forms the shared link rule does not cover. First: `tar -xf links.tar -C safe; printf x > safe/link/target.txt`, where the archive member `link` points to ../../outside. Extraction emits only write_tree(safe), so collapse() never fires. Second: `New-Item -ItemType HardLink -Path link -Value ../outside/target.txt` emits only the link at `link`, with no write to the outside source. So it passes, and a later `Set-Content link x` changes the outside file.

Trigger: repeat, rounds 2, 3.

- [P1] [P1] tool/guard_models.py:243 — Link creation remains unaccounted for in extraction and PowerShell hard links

## Failed fixes

- Round 1 (eb6794a→c9770fe): `ln` and `mklink` became top by program name. `cp -s`, `cp -l`, New-Item link types, rsync, extraction and git were left untouched. Round 2 found `cp -s` three lines further down in the same m_copy.
- Round 2 (c9770fe→1f77357): added `link`/`linktree` effects and an order-insensitive rule in guard.collapse (guard.py:832-855), which is the right shared mechanism. But emitting those effects was still opt-in per model. cp, rsync and New-Item opted in. tar -x, unzip, Expand-Archive, Copy-Item -Recurse, git checkout-type verbs, git apply/patch, and every rename (mv, Move-Item, Rename-Item, git mv) did not. Extraction was left out on purpose, based on evidence that does not apply. bsdtar refusing `..` member names and refusing to extract through symlinks, and GNU tar's zero-length placeholder for risky symlinks, protect only the extraction itself. GNU tar still creates the real `..` symlink at the end of extraction, and a later shell statement follows it. core.symlinks governs git only, not tar.
- Round 2 also wrote the 'a hard link writes its source' step inline in m_copy only (guard_models.py:206-207), and as a plain `write` even when the copy is recursive. New-Item HardLink (guard_windows.py:166-172) and rsync --link-dest=DIR (guard_models.py:589-600) emit no source effect. So the step in cycle 1's hypothesis (3) held only where a model chose to implement it.

## Sources

### S1 — bsdtar(1) — libarchive man page (FreeBSD people.freebsd.org copy)

- URL: https://people.freebsd.org/~kientzle/libarchive/bsdtar.1.txt
- Retrieved: 2026-11-02
- Locator: -P option; -U option; SECURITY section
- Fragment: -P: 'Preserve pathnames. By default, absolute pathnames (those that begin with a / character) have the leading slash removed both when creating archives and extracting from them. Also, tar will refuse to extract archive entries whose pathnames contain .. or whose target directory would be altered by a symlink. This option suppresses these behaviors.' -U: 'Without this option, tar overwrites existing files, which preserves existing hardlinks. With this option, existing hardlinks will be broken, as will any symlink that would affect the location of an extracted file.' SECURITY: 'If the final path element is a symlink, it will be removed and replaced with the archive entry.' This is an older copy, and other implementations (NetBSD tar) describe -P differently.
- Applicability: m_tar -x (guard_models.py:241-243). The protections cover member names and paths during extraction. Nothing limits where a created symlink member points, so `safe/link` can point to ../../outside after tar exits.
- Counterevidence: This protection does stop an archive from writing through its own links during extraction. It is an older man-page copy, and current versions may differ.
- Validation: The tar case in the experiment is denied after the fix; tar alone still passes.

### S2 — Red Hat Bugzilla 1759140 — GNU tar delayed symlink list memory use

- URL: https://bugzilla.redhat.com/show_bug.cgi?id=1759140
- Retrieved: 2026-11-02
- Locator: bug description
- Fragment: When extracting a symlink to an absolute file name or to a file in a parent directory, tar 'first creates a placeholder (a regular file of zero length) in its place' and records it in a list of delayed links. It replaces the placeholder once it is certain the location is not needed for another file; 'Quite often this becomes certain only at the end of extraction.' Delayed creation is disabled with -P (--absolute-names).
- Applicability: This explains why the cycle-1 claim that extraction is safe was wrong across statements. The real `..` symlink exists once tar finishes, and the next statement in the command follows it.
- Counterevidence: This is a secondary source (a bug report quoting the code), not the GNU tar manual. The placeholder does protect the extraction itself.
- Validation: An optional real GNU tar run in a POSIX scratch directory shows the link present after extraction.

### S3 — git commit 78a8d641 — introduce core.symlinks (gitweb mirror)

- URL: https://rtime.ciirc.cvut.cz/gitweb/git.git/commitdiff_plain/78a8d641c14dc2f0a306d787fa1e42d400b3af4d
- Retrieved: 2026-11-02
- Locator: Documentation/config.txt hunk for core.symlinks
- Fragment: core.symlinks: 'If false, symbolic links are checked out as small plain files that contain the link text. git-update-index and git-add will not change the recorded type to regular file. Useful on filesystems like FAT that do not support symbolic links. The default is true.' This is the 2007 text; the current git-config page was not fetched.
- Applicability: m_git (guard_models.py:697). Work-tree-changing verbs create real symlinks when core.symlinks is true (the POSIX default) and plain files when it is false (common on Windows). This justifies gating git's link flag on the effective setting, and only for git.
- Counterevidence: The wording may have changed since 2007. The setting does not apply to tar, unzip or cp, which is the reviewer's point.
- Validation: The git case in the experiment is denied with core.symlinks=true and passes with false.

### S4 — rename(2) — Linux man page

- URL: https://linux.die.net/man/2/rename
- Retrieved: 2026-11-02
- Locator: DESCRIPTION
- Fragment: 'If oldpath refers to a symbolic link, the link is renamed; if newpath refers to a symbolic link, the link will be overwritten.'
- Applicability: mv (guard_models.py:200), Move-Item and Rename-Item (guard_windows.py:70, 152-162) and git mv emit plain write and delete effects. A moved symlink lands at a new name that realpath sees only lexically, so `mv a/lnk b; printf x > b/f` escapes in the same way.
- Counterevidence: This is the Linux page. On Windows, MoveFile also moves reparse points without following them, but that was not checked here.
- Validation: The mv and Rename-Item cases in the experiment are denied after the fix.

### S5 — New-Item (Microsoft.PowerShell.Management) — PowerShell 7.6

- URL: https://learn.microsoft.com/powershell/module/microsoft.powershell.management/new-item
- Retrieved: 2026-11-02
- Locator: -ItemType; -Value (alias Target); Example 7
- Fragment: -ItemType in a FileSystem drive allows 'File', 'Directory', 'SymbolicLink', 'Junction', 'HardLink'. -Value alias: Target. 'Creating a SymbolicLink type on Windows requires elevation as administrator. However, Windows 10 (build 14972 or newer) with Developer Mode enabled no longer requires elevation creating symbolic links.' Retrieved in the cycle-1 research, not re-fetched this cycle.
- Applicability: guard_windows.py:166-172 maps HardLink to `link` at -Path and ignores -Value, so the source inode write is missing.
- Counterevidence: Symbolic links may need privileges, but HardLink and Junction do not.
- Validation: New-Item HardLink to ../outside/target.txt on its own is denied as outside the write roots.

### S6 — tool/guard.py at 1f77357 — collapse()

- URL: tool/guard.py
- Retrieved: 2026-11-02
- Locator: collapse lines 832-855; derive 817-829
- Fragment: `singles = [p for k, p in effs if k == "link"]`; `trees = [p for k, p in effs if k == "linktree"]`; `if not singles and not trees: return effs`. Only effects already tagged link or linktree trigger the rule; a write_tree passes through unchanged.
- Applicability: The shared rule exists but depends on models choosing to tag their effects. Adding write_tree to `trees` makes every tree-writing model link-capable without editing those models.
- Counterevidence: Treating every write_tree as a possible link holder also denies benign tree-copy-then-edit compounds.
- Validation: The reviewer's tar case becomes top with no change to m_tar.

### S7 — tool/guard_models.py at 1f77357 — m_copy, m_tar, m_archive, m_ssh, m_git

- URL: tool/guard_models.py
- Retrieved: 2026-11-02
- Locator: m_copy 176-208; m_tar 228-246; m_archive 249-259; m_ssh 589-600; m_git 697
- Fragment: m_copy: `if hard: out |= writes("write", srcs, ctx)` — the hard-link source write exists only here, and as `write` even for -R. m_tar -x: `writes("write_tree", ...)` with the comment 'its descendants are unknown'. m_ssh: rsync_link maps the destination to linktree; --link-dest DIR gets no effect. m_git: `out |= paths("write", here(g), g)` for every GIT_WRITE verb. mv: `writes("delete", srcs, ctx)` plus a plain write to the destination.
- Applicability: This shows which models opted in and which did not, and that the hard-link step is duplicated, or missing, per model.
- Counterevidence: Some of these models are sound on their own; the gap appears only when they are combined with a later write, or across calls for hard links.
- Validation: Every case in the experiment maps to one of these lines.

### S8 — tool/guard_windows.py at 1f77357 — new-item, copy/move/rename-item, expand-archive

- URL: tool/guard_windows.py
- Retrieved: 2026-11-02
- Locator: lines 70, 152-172
- Fragment: `link = itemtype in ("symboliclink", "junction", "hardlink")`; `out = arrayed(target) | {("link" if link and k == "write" else k, p) for k, p in writes("write", target, ctx)}` — -Value is never read. Expand-Archive and Copy-Item -Recurse emit write_tree; rename-item emits delete plus write.
- Applicability: This is where the New-Item HardLink gap is, and where rename destinations need the link flag.
- Counterevidence: None found.
- Validation: PowerShell cases in the experiment.

### S9 — docs/research/review-tool-call-guard-effect-algebra-f2-1.md — cycle 1 research and result

- URL: docs/research/review-tool-call-guard-effect-algebra-f2-1.md
- Retrieved: 2026-11-02
- Locator: Hypothesis (3); Result, 'Scoping (recorded, not hidden)'
- Fragment: Hypothesis listed `tar -x`, `unzip`, git materialising verbs and `patch` as link-capable, and said 'A hard link from a literal source also emits write(source)'. Result: 'tar -x, unzip, git checkout/… and patch are not made link-capable', citing core.symlinks=false and 'libarchive/bsdtar and git refuse ../absolute members by default'.
- Applicability: This records that the scope was narrowed on purpose, and why. The reasons given (member-name checks, core.symlinks) do not apply to links created during extraction.
- Counterevidence: The precision concern behind the scoping is real and is still the main cost.
- Validation: The fix restores the cases that were left out and reports any corpus verdicts that change.

## Claims

- C1 (source; S1, S2): tar's extraction-time protections (refusing `..` member names, refusing symlink-altered target directories, GNU tar's delayed placeholders) do not stop tar from leaving a symlink that points outside the extraction directory once it exits. A later statement that writes through that link escapes.
- C2 (source; S6, S7): collapse() fires only on effects tagged link or linktree. Extraction and other tree writes emit write_tree, which bypasses the rule.
- C3 (source; S3): core.symlinks decides whether git checks out symlink entries as real links or as plain files, and it affects only git.
- C4 (source; S4, S7, S8): A rename moves a symlink object without following it, so mv, Move-Item, Rename-Item and git mv can place an existing outside-pointing link at a name that is checked only lexically. None of them emit a link effect today.
- C5 (source; S5, S7, S8): The hard-link source write is implemented only in m_copy, and as a plain write even for recursive copies. New-Item HardLink and rsync --link-dest emit no source effect.
- C6 (interpretation; S6, S7, S9): The fixes keep failing because link-capability and the hard-link source write are decided model by model, so each review finds the next model that did not opt in. Keying the rule on effect kinds (every write_tree, every rename destination) plus one shared hard-link helper would cover models not yet named.

## Core theory

This is CWE-59 link following as a check-then-use (TOCTOU) problem: the guard checks every name in a static snapshot taken before the command runs, and anything that can make a name resolve somewhere else before it is used makes that check invalid. Link creation is not limited to programs whose job is making links. Any operation that writes entries whose contents come from data can create a symlink entry: extraction, tree copy, sync, checkout. bsdtar refuses to extract 'entries whose pathnames contain .. or whose target directory would be altered by a symlink', but it does not limit the targets of the symlinks it creates. GNU tar delays risky symlinks behind a zero-length placeholder only until it is sure the location will not be needed again, 'quite often ... only at the end of extraction', and then creates the real link. rename(2) moves a symlink object without following it, so a move can place an existing outside-pointing link at a new name. Git writes a symlink entry as a symlink unless core.symlinks is false, in which case it writes 'small plain files that contain the link text'. A guard that cannot resolve names at use time has to over-approximate. The over-approximation is sound only when every operation that may write a link is flagged, which argues for keying on the effect kind rather than on a list of programs.

## Applicability

guard.collapse (tool/guard.py:832-855) is where the effect set is judged, and it is order-insensitive. Change (1) belongs there: the `trees` list should include `write_tree` effects as well as `linktree`, and `linktree` can then become an alias. m_tar -x (guard_models.py:241-243), m_archive unzip (249-254), Expand-Archive (guard_windows.py:163-165), Copy-Item -Recurse (guard_windows.py:152-162) and cp -r already emit write_tree, so they are covered with no change to those models. The reviewer's tar case becomes write_tree(safe) plus write(safe/link/target.txt) strictly under safe, which is top. Change (2) touches m_copy's mv branch (guard_models.py:200), move-item and rename-item in guard_windows.py (lines 70, 152-162) and git mv in m_git. Change (3) replaces guard_models.py:206-207 and adds a call in guard_windows.py new-item, using `named['value']` (alias Target) as the source. rsync --link-dest adds write_tree(DIR). Change (4) touches m_git at guard_models.py:697, which today emits a plain write(here) for every GIT_WRITE verb. guard_grant.outside (tool/guard_grant.py:144-165) stays as it is.

## Hypothesis

The shared rule is now in place, but which writes count as possible links is still listed program by program, and the hard-link source write is still copied into individual models. Each review finds the next program left off the list. The fix should come from effect kinds, so a new program gets the safe behaviour by default. (1) In collapse(), treat every `write_tree` as a possible link holder, as `linktree` is treated today. write_tree already means 'a tree whose contents the archive or source decides', so this covers tar -x, unzip, Expand-Archive, Copy-Item -Recurse, cp -r and rsync at once, including models not yet written. (2) Rename destinations (mv, Move-Item, Rename-Item newname, git mv) emit `linktree`, because rename(2) moves a symlink without following it. (3) One shared helper, used by cp -l, New-Item HardLink and rsync --link-dest, emits the source write: write_tree(source) when the copy is recursive or is --link-dest, otherwise write(source). A variable source makes the command top. (4) git verbs that change the work tree (checkout, switch, restore, reset, merge, pull, rebase, cherry-pick, revert, stash, am, apply, clone, worktree add, submodule) emit linktree over the work tree when the repository's effective core.symlinks is true. Read that setting at grant time; when false, git writes plain files. Optional refinement to keep `tar -xf a.tar && rm a.tar` allowed: a delete of an immediate child of a tree root does not go through a new link, because unlink removes the link itself. Writes and deeper deletes do go through.

## Counterevidence

- Precision cost: common agent compounds become top, for example `cp -r tpl dst && sed -i ... dst/x`, `tar -xf a.tar -C out && cat > out/cfg`, `Copy-Item -Recurse a b; Set-Content b\x y`, and on POSIX repositories with core.symlinks true, `git checkout br && printf ... > f`. The cycle-1 fix left extraction and git out because of exactly this cost.
- During one extraction, GNU tar (placeholder) and bsdtar (refuses symlink-altered target directories unless -P) do protect themselves. This is real protection; it just stops at the end of the tar process.
- Symlinks and junctions created in one call are resolved by realpath on the next call, so the cross-call case for them stays sound without this change. Only hard links need the source write across calls.
- On Windows, creating a symbolic link needs elevation or Developer Mode, but junctions and hard links need neither, so the PowerShell gap is real without privileges.

## Alternatives

- Keep adding link-capable models one at a time (tar, then unzip, then mv...). Rejected: this is the pattern that failed in all three rounds.
- Reverse the default completely: every write is link-capable except a closed set of content writers (redirections, tee, sed -i, dd of=, touch, mkdir, Set-Content, Add-Content). This converges best, but it touches every model and the corpus at once. It is worth doing if a fourth round finds another write-kind gap.
- When a tree source is a literal path inside the grant, walk it at check time (os.walk, followlinks=False) and drop the link flag if it contains no symlinks. This recovers precision for `cp -r tpl dst && edit`. It is unsound if an earlier statement in the same command writes under the source, and it does not work for archives. Treat it as a later precision refinement.
- Make every extraction and tree copy top on its own. Sound, but it denies `tar -xf a.tar -C out` alone, which the existing test test_a_link_alone_is_allowed rightly keeps allowed.
- A run-time backstop that scans write roots after the command for new outside-pointing links or files with st_nlink>1. Strong, but it acts after the write happens. Useful as a second layer, not as the fix.

## Experiment

Add parametrised cases to tool/test_guard.py and run them first against 1f77357, where each should pass (gap confirmed), then after the fix, where each should be denied: `tar -xf links.tar -C safe; printf x > safe/link/target.txt`; `unzip p.zip -d safe; printf x > safe/link/f`; PowerShell `Expand-Archive p.zip safe; Set-Content safe\link\f x`; `Copy-Item -Recurse ..\d d; Set-Content d\link\f x`; `mv a/lnk b; printf x > b/f`; `Rename-Item lnk b; Set-Content b\f x`; `git checkout other; printf x > link/f` with core.symlinks=true in the fixture repo; `New-Item -ItemType HardLink -Path link -Value ../outside/target.txt` on its own; `rsync -a --link-dest=../outside src/ dst/` on its own; `cp -lR .wiki x` on its own, which must report a protected path. These must still pass: `tar -xf bundle.tar -C out`, `unzip bundle.zip -d out`, `cp -r src dst`, `cp -s a b`, `tar -xf a.tar && rm a.tar` (with the delete-depth refinement), and `git checkout other; printf x > f` when core.symlinks=false. Then run the existing corpus and `python -m ruff check tool laya` and list every corpus entry whose verdict changed. Optionally, in a POSIX scratch directory outside the repository, build links.tar with a `link -> ../../outside` member and run the reviewer's command once to confirm the escape with real GNU tar.

## Result

pending — the fix records what it observed here, with the tests and the commit.

## Unresolved limits

This research ran no code. The GNU tar behaviour comes from a Red Hat bug report and mailing-list sources, not the current tar manual. The bsdtar text comes from an older libarchive man-page copy. Whether PowerShell Expand-Archive (.NET ZipFile) ever restores symlinks was not checked; it is covered only because it already emits write_tree. Whether git apply and patch create symlinks from mode-120000 diffs was not checked against git documentation. The delete-depth refinement assumes rm and unlink do not follow the final component. That holds for POSIX rm, but PowerShell Remove-Item on junctions has behaved differently across versions, so the refinement should apply only to POSIX rm. The precision cost on the real corpus is unknown until the experiment runs. The New-Item -Value semantics come from the cycle-1 source, not a fresh fetch this cycle.
