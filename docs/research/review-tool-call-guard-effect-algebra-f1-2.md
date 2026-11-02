# Review research — F1, cycle 2

Draft research before the fix of review round 3 of PR #272, written 2026-11-02. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F1 (guard.destination-models). The rule being broken: write effects must cover the files a command actually writes, including protected files under a destination directory. The protected paths are `.git`, `.claude`, `.codex`, `.wiki/adapter.toml` and `verification.toml`, in the checkout and in main. Round 3 at 1f77357 shows two leaks. `rsync ../adapter.toml .wiki` produces only `write(<checkout>/.wiki)`, because m_ssh (guard_models.py:598-600) treats the last operand as an exact file and never calls `into_dir`. `curl -O http://localhost:8000/verification.toml` produces `write(<checkout>)` (guard_models.py:536). The real targets, `.wiki/adapter.toml` and `<checkout>/verification.toml`, are never checked. `guard_grant.outside()` (guard_grant.py:160) checks for protected descendants only when the effect is `delete` or `write_tree`, so a plain `write(dir)` sitting inside a write root passes.

Trigger: repeat, rounds 2, 3.

- [P1] [P1] tool/guard_models.py:600 — Directory destinations still omit protected files in other models

## Failed fixes

- Round 1 (eb6794a): added `into_dir` to m_copy for trailing-slash destinations only. Round 2 then reproduced the leak without the slash.
- Round 2 (c9770fe): `into_dir` covered the last operand and `-t`, a `write_tree` kind was added, and cp -r, tar -x and unzip were converted. That covered only the POSIX cp/mv family. Copy-Item/Move-Item, the cmd builtins and tar -P were left, and round 3 raised them.
- Cycle 1 research (page e4bdd7e), applied in 1f77357: it proposed a destination helper routed through every copy/move model, including 'the last operand of rsync/scp', and `write_tree` for `curl -O` and `wget`. The fix applied only part of that list. Copy-Item, cmd copy/move, curl `--output-dir` and wget `-P` were converted. m_ssh (rsync/scp) still has no `into_dir`. `curl -O` and wget's default output still emit `write(here)`. The list was itself incomplete: Invoke-WebRequest `-OutFile <folder>` (guard_windows.py:184), `gh pr checkout` and `gh repo clone` with no directory (guard_models.py:771-773), and `patch` with no target (guard_models.py:219) all emit a plain `write(dir)` too. Both cycles made soundness depend on every model picking the right kind, and each fix converted only the models it listed.

## Sources

### S1 — Everything curl — Download to a file named by the URL

- URL: https://everything.curl.dev/usingcurl/downloads/url-named.html
- Retrieved: 2026-11-02
- Locator: section on -O, --output-dir and --remote-name-all
- Fragment: 'The -O option selects the local filename to use by picking the filename part of the URL that you provide.' After a redirect, 'it does not change the filename curl uses for storing this.' -O 'otherwise implies saving the file in the current directory'; you 'can still change the directory in which that file is saved by using --output-dir'. --remote-name-all makes -O the default for every URL. The page does not cover -J/--remote-header-name, which lets the server choose the name.
- Applicability: m_curl at guard_models.py:535-536 emits write(here) for -O. The real target is here/<last URL path segment>, e.g. <checkout>/verification.toml in the round 3 reproduction.
- Counterevidence: Because the name comes from the URL, curl -O without -J could be modelled precisely. A per-model fix is possible here, but that does not hold for wget, Invoke-WebRequest -OutFile <folder> or curl -J.
- Validation: After the fix, `curl -O http://localhost:8000/verification.toml` at the checkout root is denied with the protected-file reason, and `curl -O http://localhost:8000/x.txt` run in `safe/` is still allowed.

### S2 — rsync(1) manual page

- URL: https://download.samba.org/pub/rsync/rsync.1
- Retrieved: 2026-11-02
- Locator: COPYING TO A DIFFERENT NAME; USAGE (trailing slash on the source)
- Fragment: A single item is renamed to the destination path only if: 'The transfer list must consist of a single item (either a file or an empty directory)', 'The final element of the destination path must not exist as a directory', and 'The destination path must not have been specified with a trailing slash'. 'Under those circumstances, rsync will use the specified destination path as the filename of the destination.' Otherwise the item goes into the destination directory. 'A trailing slash on the source changes this behavior to avoid creating an additional directory level at the destination.' The page does not spell out the multiple-sources case.
- Applicability: m_ssh at guard_models.py:598-600: `rsync ../adapter.toml .wiki`, where .wiki exists as a directory, writes .wiki/adapter.toml, but the model emits only write(.wiki).
- Counterevidence: When the destination does not exist and has no trailing slash, rsync writes exactly the named path, so write(dest) is right in that case. Whether .wiki exists cannot be known statically, so the judge must assume the directory case.
- Validation: After the fix, `rsync ../adapter.toml .wiki` and `scp ../adapter.toml .wiki` are denied with the protected-file reason, while `rsync a.txt safe/` stays allowed.

### S3 — Invoke-WebRequest (Microsoft.PowerShell.Utility) — PowerShell 7.6

- URL: https://learn.microsoft.com/powershell/module/microsoft.powershell.utility/invoke-webrequest
- Retrieved: 2026-11-02
- Locator: Parameters, -OutFile
- Fragment: 'If you omit the path, the default is the current location.' 'Starting in PowerShell 7.4, you can specify a folder path without the filename. When you do, the command uses the filename from the last segment of the resolved URI after any redirections. When you specify a folder path for OutFile, you can't use the Resume parameter.' Condition: PowerShell 7.4 or later only; before 7.4 the value is a file path.
- Applicability: guard_windows.py:184 emits write(<OutFile>). With a folder value, the real file is <folder>/<name chosen after redirects>, so the server can choose `adapter.toml` under `.wiki`. No review round has named this site yet.
- Counterevidence: On Windows PowerShell 5.1 and pwsh before 7.4, a folder value is treated as a file path and fails, so the hole exists only on 7.4+.
- Validation: After the fix, pwsh `Invoke-WebRequest http://localhost:8000/x -OutFile .wiki` and `-OutFile .` at the checkout root are denied with the protected-file reason, and `-OutFile safe/out.bin` stays allowed.

### S4 — wget content disposition (Simplified Guide) and bug-wget list on --trust-server-names

- URL: https://www.simplified.guide/wget/content-disposition
- Retrieved: 2026-11-02
- Locator: content-disposition guide; bug-wget 2010-08 message (https://lists.gnu.org/archive/html/bug-wget/2010-08/msg00005.html)
- Fragment: --content-disposition makes wget use the file name from the server's Content-Disposition header. Content-Disposition values 'are entirely controlled by the remote server'. The 2010 bug-wget message says --content-disposition implies --trust-server-names, which takes the local name from the redirected URL, and which is not the default because 'it could lead to overwriting an unpredictable file name in the current directory'. Both options can be switched on in ~/.wgetrc. Condition: these are secondary sources; the primary GNU manual returned HTTP 429.
- Applicability: m_wget at guard_models.py:565-566 emits write(here) when there is no -O. The real name comes from the URL or the server, and ~/.wgetrc, which lies outside the command text, can make it server-chosen. Only a tree-level judgement is sound.
- Counterevidence: With the defaults and no wgetrc, wget names the file from the original URL, and a precise basename model would work. The guard cannot see wgetrc, though.
- Validation: After the fix, `wget http://localhost:8000/verification.toml` at the checkout root is denied with the protected-file reason, and wget run in `safe/` stays allowed.

### S5 — The Security Principles of Saltzer and Schroeder (summary of 'The Protection of Information in Computer Systems', 1975)

- URL: https://shostack.org/blog/the-security-principles-of-saltzer-and-schroeder
- Retrieved: 2026-11-02
- Locator: Fail-safe defaults; Complete mediation
- Fragment: Fail-safe defaults: base access decisions on permission rather than exclusion. A design or implementation mistake in a mechanism that gives explicit permission tends to fail by refusing permission, which is safe and will be quickly detected; a mechanism that excludes listed cases fails by allowing access, which may go unnoticed. Complete mediation: every access to every object must be checked for authority. Lecture summaries add a condition: a cached check is acceptable only if nothing relevant in the environment has changed. Condition: this is a secondary summary; the original paper was not fetched.
- Applicability: outside()'s protected check is exclusion-based: it denies only when a model has flagged the effect as delete or write_tree, so every unflagged model fails open, as rounds 1-3 showed. Making the check apply to every path kind turns the default into refusal.
- Counterevidence: Fail-safe defaults costs usability. Here that means refused root-level stand-in writes (curl -O, split, gh pr checkout), which the person may not accept.
- Validation: The judge-level property test, `outside(("write", a), g)` for every proper ancestor a of a protected path, returns the protected-file reason without any model being involved.

### S6 — tool/guard_grant.py — outside()

- URL: tool/guard_grant.py
- Retrieved: 2026-11-02
- Locator: lines 159-162, `reaches_protected = kind in ("delete", "write_tree") and any(contained(p, [target]) for p in g.protected)`
- Fragment: '# A delete or a tree write (recursive copy, extraction) also reaches a protected path it contains.' A protected-descendant check runs only for kind delete or write_tree. A `write` is denied only if its target is itself inside a protected path. PROTECTED (line 34) = .git, .claude, .codex, .wiki/adapter.toml, verification.toml.
- Applicability: This is the single choke point that judges every model's path effects. A one-line change here covers every model at once.
- Counterevidence: Because .git and verification.toml are direct children of the checkout, every write(<checkout>) will be refused after the change.
- Validation: After the change, the reproductions in the experiment are denied, the controls pass, and the corpus diff lists only writes whose target is an ancestor of a protected path.

### S7 — tool/guard_models.py and tool/guard_windows.py — remaining write(dir) stand-ins

- URL: tool/guard_models.py
- Retrieved: 2026-11-02
- Locator: guard_models.py:219 (m_patch), 263 (m_split), 536 (m_curl -O), 566 (m_wget), 598-600 (m_ssh last operand), 771-773 (gh clone / pr checkout); guard_windows.py:184 (Invoke-WebRequest -OutFile)
- Fragment: m_ssh: `elif k == len(pos) - 1 and k: out |= {... for kind, p in paths("write", w, ctx)}`, with no into_dir. m_curl: `out |= paths("write", here(ctx), ctx)` for -O/--remote-name. m_wget: `if not named and "--spider" not in flags: out |= paths("write", here(ctx), ctx)`. Invoke-WebRequest: `writes("write", named.get("outfile", []), ctx)`.
- Applicability: At 1f77357 there are at least seven model sites that emit write(dir) for a directory whose children the program actually writes. That is more than the two that round 3 named, which shows enumeration has not converged.
- Counterevidence: Some sites (split, gh clone) write fixed or repository-derived names that are rarely protected, so refusing them at the root is a false positive rather than a closed hole.
- Validation: The repository grep for `paths("write", here(ctx)` and the corpus diff after the judge change list exactly these sites as newly refused at the checkout root, and none of them is refused from a subdirectory.

## Claims

- C1 (source; S1, S2): For rsync with an existing directory destination, and for curl -O, the real file written is a child of the path the model emits (dest/basename, or cwd/<URL last segment>), not the emitted path itself.
- C2 (interpretation; S2, S3, S7): At 1f77357 several models besides the two that round 3 named still emit write(dir) for directory destinations. Invoke-WebRequest -OutFile <folder> is one of them, and it writes a redirect-chosen name inside the folder.
- C3 (source; S3, S4): For wget (via --content-disposition or wgetrc) and Invoke-WebRequest -OutFile <folder>, the server or a redirect decides the file name, so no static per-model basename is sound. Only a tree-level judgement is.
- C4 (interpretation; S5, S6): The protected check in outside() fails open for any model that does not flag its effect as write_tree or delete. Fail-safe defaults says the check should instead deny every path effect whose target is an ancestor of a protected path, regardless of kind.
- C5 (interpretation; S1, S6, S7): A one-line change at guard_grant.py:160, applying the descendant check to every path kind, closes the rsync and curl -O reproductions and the not-yet-named sites. Its cost is refusing root-level stand-in writes, and curl -O's cost can be cut by naming the URL's last segment.

## Core theory

Saltzer and Schroeder's fail-safe defaults: base access decisions on permission rather than exclusion. A design mistake in a mechanism that grants explicit permission tends to fail by refusing, which is safe and quickly noticed. A mechanism that excludes listed cases fails silently by allowing. Complete mediation: every access is checked, by a mechanism that does not depend on each caller describing the access correctly. The tool documentation shows that for these programs the actual file is decided at run time, by the destination's directory status (rsync), the URL path (curl -O), a redirect (Invoke-WebRequest -OutFile <folder>) or a server header (wget --content-disposition, curl -J). A judge that does static checking cannot rely on a per-model guess for these cases.

## Applicability

guard_grant.py:160, `reaches_protected = kind in ("delete", "write_tree") and ...`, is the single place where every model's path effects are judged against `g.protected`. Widening it to every path kind (write, write_tree, delete) closes rsync/scp (m_ssh:598-600), curl -O (m_curl:536), wget's default output (m_wget:566), Invoke-WebRequest -OutFile (guard_windows.py:184), gh pr checkout/clone (m_gh:771-773), patch (m_patch:219), and any future model that emits `write(dir)`. No model has to be edited for this. `collapse()` (guard.py:842-855) and `render()` still use the write/write_tree distinction for link slips and display, so the kinds stay. Only the protected-descendant check becomes kind-independent.

## Hypothesis

The cause is in the shared judge, not in any one model. `outside()` assumes that a `write` effect names exactly the file written, and it trusts each model to say `write_tree` whenever the target is a directory that the program writes into. A command model often cannot know whether its target is a directory: an rsync/scp/cp destination may be an existing directory, and curl -O, wget, Invoke-WebRequest -OutFile <folder>, gh pr checkout and patch write files whose names come from a URL, a server, a redirect or a diff. So every model that emits `write(d)` for such a d is a separate hole, and enumerating models has not converged in three rounds. The fix that converges is one fail-safe rule in `outside()`: any path effect (write, write_tree or delete) whose target is a proper ancestor of a protected path is treated as reaching that protected path, and is denied with the protected-file reason. Two cases are possible for a write whose target is a proper ancestor of an existing protected path. Either the target is a directory the program writes into, or the write fails. Denying it therefore loses no legitimate exact-file write. After this rule, `into_dir`, basename naming and `write_tree` only add precision. They are no longer needed for soundness. A precision follow-up, which does not affect soundness: name curl -O's file as `here/<last URL path segment>`, as curl documents it, unless -J/--remote-header-name is set. Then a root-level `curl -O` of an unprotected name is not refused.

## Counterevidence

- False positives at the checkout root. `.git` and `verification.toml` sit directly under it, so every `write(<checkout>)` stand-in is refused. That covers `curl -O`, `wget URL`, `split` (m_split emits write(here)), `patch` with no target, `gh pr checkout` and `gh repo clone` with no directory, when run from the root. Some of these are legitimate everyday commands. Running them from a subdirectory still works, and curl -O can be made precise by naming the URL's last segment.
- `mkdir -p .wiki` and `touch .wiki` would be refused even though they do not write adapter.toml. Both are rare and harmless to refuse.
- It is not verified here which entries of the 260-command corpus emit `write(<ancestor of protected>)` today. The rule could refuse more of the corpus than the counterevidence above suggests.
- The rule fixes only the protected-file half of F1. A directory destination that is inside a write root but whose real target is a file outside the roots cannot exist lexically, since a descendant of a root is inside the root. Rsync with a remote destination is already a network effect. No residual escape through this channel was found, but none was ruled out by testing either.

## Alternatives

- Keep converting models: add `into_dir` to m_ssh, write_tree to curl -O, wget and Invoke-WebRequest -OutFile, and so on. Not first: this is the approach that failed in rounds 1-3. The grep at 1f77357 still finds unconverted sites that no review has named yet (Invoke-WebRequest -OutFile <folder>, gh pr checkout, patch), so a fourth round would likely raise another one. It remains worth doing after the judge fix, for precision.
- Check the filesystem in the judge (treat the target as a tree if it is an existing directory). Not chosen: the hook runs before the command, a destination can be created earlier in the same command (`mkdir d; rsync x d`), and this adds a time-of-check race. The lexical ancestor-of-protected check needs no I/O and covers the same case, because a protected descendant only exists under a directory.
- Drop the write/write_tree distinction everywhere. Not chosen: `collapse()` relies on `p != root` for tree links, and `render()` shows tree writes as writes. Only the protected check needs to be unified.

## Experiment

On 1f77357 before the fix, add one test with two parts. (1) A judge-level property test that does not depend on any model. For each protected path p in a test grant, and each proper ancestor a of p inside the write roots, assert that `outside(("write", a), g)` returns the protected-file reason. On 1f77357 it returns None, which confirms that the judge is where the hole is. (2) The reproductions: `rsync ../adapter.toml .wiki`, `scp ../adapter.toml .wiki`, `rsync -a ../x/ .`, `curl -O http://localhost:8000/verification.toml`, `wget http://localhost:8000/verification.toml`, pwsh `Invoke-WebRequest http://localhost:8000/verification.toml -OutFile .wiki` and `-OutFile .`, `gh pr checkout 1`, and `patch -p1 < p.diff` at the checkout root. Expect all to pass on 1f77357 and all to be denied with the protected-file reason after the one-line change at guard_grant.py:160. Controls that must stay allowed: `rsync a.txt safe/`, `curl -O http://localhost:8000/x.txt` with the working directory in `safe/`, `printf x > safe/a`, `cp -r src dst`. Then run the 260-command corpus and list every entry that changes from allowed to denied. Each one must be a write whose target is an ancestor of a protected path, and that list measures the false-positive cost.

## Result

The hypothesis held. On `1b4df77` (guard code as at `1f77357`), `test_every_ancestor_of_a_protected_path_is_judged_protected_for_every_path_kind` failed: `outside(("write", <checkout>/.wiki), g)` returned `None`. All ten forms in `PROTECTED_ANCESTOR_FORMS` were allowed: rsync and scp into `.wiki`, `rsync -a ../x/ .`, `curl -O` and `curl -OJ`, `wget`, Invoke-WebRequest `-OutFile .wiki` and `-OutFile .`, `patch -p1 <` at the root, and `cp -lR .wiki x`.

The fix is in the commit that adds this result, on top of `1b4df77`. `guard_grant.outside` now applies the protected-descendant check to every path kind. The one exception is a new kind, `repo`: git's own write of the repository it runs in (`m_git`, `gh pr checkout`). Without that exception every git write verb would be refused, because `write(<checkout>)` contains `.git`. Three changes add precision without affecting soundness: `curl -O` names `<cwd>/<last URL segment>` (only the directory with `-J`); rsync and scp call `into_dir`; and `gh repo clone` names the repository's directory.

After the fix these pass:

- `test_a_write_to_a_directory_holding_a_protected_file_is_denied` (10 forms).
- The property test above.
- `test_ordinary_writes_beside_protected_files_stay_allowed`, which covers `rsync a.txt safe/`, `curl -O …/x.txt` at the root, `git checkout -b` followed by a commit, `gh pr checkout`, and `-OutFile safe/out.bin`.

Corpus verdicts under a synthetic grant, compared between `1b4df77` and the fix in a scratch worktree: no entry went from allowed to denied because of this rule. One entry that was already denied (`mv $W/.wiki/laya*.md .wiki/`) gained the protected-file reason. The corpus does not measure one cost: `wget URL`, `split` and `patch` with no target are now refused at the checkout root. They still work from a subdirectory.

## Unresolved limits

Not verified: how many corpus entries the wider rule newly refuses, and whether the person accepts refusing root-level `curl -O`, `wget`, `split` and `gh pr checkout`. The primary GNU wget manual returned HTTP 429. The wget content-disposition and trust-server-names behaviour, and the fact that `~/.wgetrc` can enable them outside the command, come from secondary sources. The original 1975 Saltzer and Schroeder paper was not fetched; its principles are quoted from a widely cited summary. The ancestor rule protects only the listed protected paths. Whether other grant-defining files should join PROTECTED is outside F1.
