# Review research — F1, cycle 1

Draft research before the fix of review round 2 of PR #270, written 2026-11-01. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

A grounds file that the spec validator accepts (specs.fields keeps it via specs.inside, specs.missing does not flag it) must reach work.files as a grounded candidate in Git's repository-relative spelling. Otherwise it is neither loaded nor listed as available, even when filtering is off. Round 2 showed an absolute path inside the repository (C:/proj/a.py:1 on Windows) still breaks this. The broken invariant: every grounds path validation accepts maps to the same repo-relative path in grounded().

Trigger: repeat, rounds 1, 2.

- [P1] [P1] tool/main/file_filter.py:83 — Accepted absolute grounds paths are still silently excluded

## Failed fixes

- Round 1 (fcbd970 -> cb5651a): grounded() gained its own lexical canonicalization: strip specs.LINE (adds the en dash), replace backslashes, posixpath.normpath, then reject '', '.', '..', a '../' or '/' prefix, and any drive-letter prefix. This fixed './a.py:3' and 'a.py:1–3' but did not hold. It is a second, independent definition of 'inside the repository' that never consults the repository root. The validator's definition (specs.inside: (repo / ref).resolve() has repo.resolve() among its parents) also accepts absolute paths, Windows drive paths and filesystem-resolved spellings. The fix rejected every 'C:' path outright, and its test (test_file_filter.py:110, 'C:/x.py' expected dropped) enshrined the gap instead of checking parity with the validator.
- Round 2: no correction yet. The pattern predicts that patching only absolute paths (e.g. stripping a 'C:/proj/' prefix as a string) would leave other members of the difference: backslash absolute 'C:\proj\a.py', different-case spellings on a case-insensitive filesystem, and paths through a symlinked folder.

## Sources

### S1 — pathlib — Object-oriented filesystem paths (Python 3 docs)

- URL: https://docs.python.org/3/library/pathlib.html
- Retrieved: 2026-11-01
- Locator: Pure paths; Operators; Path.resolve; PurePath.relative_to
- Fragment: "If a segment is an absolute path, all previous segments are ignored (like os.path.join())" ... "On Windows, the drive is not reset when a rooted relative path segment (e.g., r'\foo') is encountered: PureWindowsPath('c:/Windows', '/Program Files') -> PureWindowsPath('c:/Program Files')". resolve(): "Make the path absolute, resolving any symlinks ... '..' components are also eliminated (this is the only method to do so)"; "If strict is False, the path is resolved as far as possible and any remainder is appended without checking whether it exists." relative_to(): "When walk_up is false (the default), the path must start with other ... In all other cases, such as the paths referencing different drives, ValueError is raised." Warning: "It does not check or access the underlying file structure ... call resolve() first if necessary to resolve symlinks." Footnote: a lexical operation that "raises ValueError when its inputs' anchors differ".
- Applicability: Explains why specs.inside's `repo / ref` accepts absolute paths inside the repo while grounded()'s string rules cannot, and why resolve() followed by relative_to(repo.resolve()) gives the repo-relative path.
- Counterevidence: resolve() follows symlinks, so its result can differ from the link path Git tracks. No statement on Windows case normalization.
- Validation: The parity test in `experiment` passes for absolute, backslash and relative refs and still drops '../out.py' and outside absolute paths.

### S2 — git-ls-files documentation (repo.or.cz mirror)

- URL: https://repo.or.cz/git/raj.git/blob/HEAD:/Documentation/git-ls-files.txt
- Retrieved: 2026-11-01
- Locator: --full-name; quoting under core.quotePath
- Fragment: When run from a subdirectory, the command usually outputs paths relative to the current directory; --full-name forces paths to be output relative to the project top directory. Without -z, pathnames with "unusual" characters are quoted as explained for the configuration variable core.quotePath; with -z the filename is output verbatim.
- Applicability: Tree.files holds top-level-relative POSIX paths (ls-tree run at the worktree root with core.quotepath=off). This is the spelling grounded() must produce. Absolute or backslash spellings never match it.
- Counterevidence: Third-party mirror and about ls-files, not ls-tree; ls-tree's path-relativity is assumed to match when run at the root.
- Validation: Assert candidates() returns 'a.py' (POSIX, no prefix) for an absolute grounds ref.

### S3 — tool/main/specs.py — inside(), missing(), fields(), LINE

- URL: tool/main/specs.py
- Retrieved: 2026-11-01
- Locator: lines 87, 364-366, 411-413, 484-485
- Fragment: LINE = re.compile(r":\d+(?:[-–]\d+)?$"); def inside(repo, ref): target = (repo / LINE.sub("", ref)).resolve(); return repo.resolve() in target.parents; listed["files"] = [f for f in listed["files"] if inside(repo, f)] — "A path outside the repository grounds nothing here. One inside that does not exist is kept and shown as missing."; missing(): not (repo / LINE.sub("", f)).is_file()
- Applicability: The validator's definition, which accepts any ref that resolves under repo, including absolute ones.
- Counterevidence: none given
- Validation: After the fix, inside() is defined via the shared function, so the two cannot diverge.

### S4 — tool/main/file_filter.py — grounded(), candidates(); tool/test_file_filter.py:108-113

- URL: tool/main/file_filter.py
- Retrieved: 2026-11-01
- Locator: file_filter.py:73-85, 127-149; test_file_filter.py:110
- Fragment: path = posixpath.normpath(LINE.sub("", f.strip()).replace("\\", "/")) ...; if path not in ("", ".", "..") and not path.startswith(("../", "/")) and not re.match(r"[A-Za-z]:", path): out.append(path)  — test: spec(files=("./a.py:3", "a.py:1–3", "sub\\..\\b.md:2-4", "../out.py", "C:/x.py")) expects ["a.py", "b.md"]
- Applicability: The filter's separate lexical definition: it never receives repo, rejects every drive-letter path, and the round-1 test locks that in.
- Counterevidence: none given
- Validation: grounded() takes repo and the test asserts parity with fields().

## Claims

- C1 (source; S1, S3): Joining an absolute ref onto the repo root with pathlib yields the ref itself, so specs.inside() accepts absolute paths that resolve under the repository.
- C2 (source; S1): resolve() followed by relative_to(repo.resolve()) gives a repository-relative path for any ref inside the repo, and raises ValueError when the ref lies elsewhere or on another drive.
- C3 (interpretation; S2, S4): Tree.files holds Git's top-level-relative POSIX spelling, so a grounds path must be converted to that spelling before the membership check.
- C4 (interpretation; S3, S4): The repeat finding comes from two independent definitions of an in-repository grounds path, a filesystem one in specs.inside and a lexical one in file_filter.grounded. Each fix shrank their difference by one spelling without removing it.
- C5 (source; S4): The round-1 test asserts a drive-letter path is dropped, which locked in the gap that round 2 reported.

## Core theory

pathlib's join drops all previous segments when the argument is absolute, and on Windows keeps the drive for a rooted-relative segment. So `repo / ref` already gives the right target for relative, absolute and drive-rooted refs. resolve() makes the path absolute, resolves symlinks and is the only pathlib method that eliminates '..'. relative_to() is lexical, raises ValueError when the anchors differ, and the docs advise calling resolve() first. Git reports tracked paths relative to the top-level directory. Mapping 'resolve, then relative_to the resolved root' therefore yields Git's spelling for any ref the validator accepts.

## Applicability

tool/main/specs.py:364-366 (inside) and :484-485 (missing) already use repo / LINE.sub('', ref) with resolve(). tool/main/file_filter.py:73-85 (grounded) re-derives the same answer lexically without repo. Tree.files (file_filter.py:64) holds `git ls-tree -r --name-only` output, i.e. top-level-relative POSIX paths. candidates() (file_filter.py:127-149) has `repo` in hand, which is channels.repo_for(spec['repo']) from decisions.py:625,642. The tree comes from the task worktree `path`, so absolute grounds must be resolved against `repo`, not tree.root: an absolute ref names the main checkout the validator saw. The merged-spec overlap at file_filter.py:145 must use the same mapping, or a merged spec grounded as 'C:/proj/a.py' never overlaps 'a.py'.

## Hypothesis

The root cause is two implementations of one predicate, not one missing spelling. specs.inside()/missing() interpret a ref with pathlib (repo / ref, then resolve()). grounded() interprets it with string rules that never see repo. Every review round finds another spelling in the set difference. The fix: specs.py gets one function, e.g. relative(repo, ref) -> str | None, that does target = (repo / LINE.sub('', ref)).resolve() and returns target.relative_to(repo.resolve()).as_posix() when repo.resolve() is in target.parents, else None. specs.inside() becomes `relative(...) is not None`. grounded(spec, repo) maps every grounds file through the same function. candidates() passes its `repo` for both the spec's grounds and the merged specs' grounds in the overlap check.

## Counterevidence

- resolve() follows symlinks. Git lists a symlink's own path, so a grounds path through a symlinked folder resolves to the target's path, which may not be in Tree.files. The validator resolves the same way, so validator and filter still agree, but such a file is still not offered.
- If any code path validates grounds against a root other than channels.repo_for(spec['repo']) (e.g. a worktree), resolving against `repo` could map an absolute path differently than validation did. Not verified for every caller of specs.fields.
- Python's docs do not say whether resolve() returns a file's real letter case on Windows. If it keeps the given case, 'A.PY' still fails the exact match against Tree.files even with the shared function.

## Alternatives

- Keep the lexical grounded() and add an 'absolute path under repo' branch: smallest diff, but it is the same per-spelling patching that already failed twice. Backslash absolute and case variants stay divergent.
- Normalize grounds once at specs.fields() time and store repo-relative paths in the spec: fixes new specs at the source, but already-merged specs (read by merged() for overlap and by the eval replay) keep their old spellings, so grounded() needs the shared mapping anyway. Worth doing as a follow-up, not first.
- Match Tree.files case-insensitively, or with os.path.normcase on both sides: handles case but not absolute paths, and it is a third definition of the predicate.

## Experiment

In test_file_filter.py's `world` (tracked a.py, b.md), build grounds files from [str(repo/'a.py')+':1', str(repo/'a.py').replace('/', '\\')+':2', './a.py:3', 'a.py:1–3', 'sub\\..\\b.md:2-4', '../out.py', str(repo.parent/'x.py')]. Run them through specs.fields(repo, ...) and keep what it keeps and what specs.missing does not flag. Assert grounded(spec, repo) maps each kept ref to a path in Tree(repo).files, and that candidates(..., hits=[]) offers a.py and b.md. On Windows also add str(repo/'A.PY'). It fails at cb5651a on the absolute refs; with the shared function it should pass, and the outside refs stay dropped by both sides.

## Result

Observed, not yet independently reviewed. At cb5651a, a temporary repository with tracked `a.py` and the ref `<repo>/a.py:1` (forward slashes, Windows drive) gave `specs.inside` True and `specs.missing` empty, but `file_filter.grounded` returned `[]` and `candidates(..., hits=[])` returned `[]`. The fix follows the hypothesis: `specs.relative(repo, ref)` does `(repo / LINE.sub('', ref)).resolve()` and returns `relative_to(repo.resolve()).as_posix()` or `None`. `specs.inside` is now `relative(...) is not None`, and `grounded(spec, repo)` maps every grounds file through it, for the spec's own grounds and for merged specs' grounds in the overlap check. `searched` reads search hits' absolute paths through the same function. On this Windows machine, `relative` maps all of these to `a.py`: backslash and forward-slash absolute refs, `A.PY` (so `resolve()` returned the file's real letter case here), `./a.py:3` and `a.py:1–3`. `../out.py` and an absolute path beside the repository map to `None`, and the validator drops them too. The experiment is `tool/test_file_filter.py::test_every_grounds_path_the_spec_checks_accept_names_its_tracked_file`: refs go through `specs.fields`, every kept ref maps into `Tree(repo).files`, and candidates offer `a.py`, `b.md` and the overlapping merge's `c.py`. It passes with the fix, in the commit that adds this result. Not checked: paths through a symlinked folder (the counterevidence), and case behaviour on other filesystems.

## Unresolved limits

Not run here (no shell). I did not confirm every caller of specs.fields uses the same root as decisions.py:625. Windows case behaviour of resolve() is not documented on the pathlib page. Symlinked folders and refs on a different drive stay edge cases where Git's spelling and the resolved path can still differ.
