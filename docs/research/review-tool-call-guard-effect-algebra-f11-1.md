# Review research — F11, cycle 1

Draft research before the fix of review round 3 of PR #272, written 2026-11-02. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F11: when a command is named by a path (contains a slash), the guard picks its effect model from the basename. `exe()` removes the directory, and `run_argv()` then applies the trusted READERS/MODELS entry for that name (`cat` gets no effects, `git` gets the git model). The rule this breaks: a trusted model may only describe a program whose identity has been checked. A command named by a path runs the file at that path with no PATH search, so its basename says nothing about which program it is. An unchecked path must be refused, either as a spawn the grant has to name or as `top`. It must never borrow a trusted model, and never match a grant entry that only shares its basename.

Trigger: repeat, rounds 2, 3.

- [P1] [P1] tool/guard_models.py:821 — Being outside writable roots does not verify executable identity

## Failed fixes

- Round 2 to round 3 (commit 1f77357): added `planted(raw, ctx)` at tool/guard_models.py:804, called at line 843. A slash-named command is treated as an ungranted spawn only if it resolves inside a write root, cannot be resolved, or (when there are no roots) is relative. Every other path falls through to lines 848-851 and keeps the basename model. This lists the bad cases (files this session could write) and treats everything else as verified. But 'outside every write root' does not mean 'system program'. `../outside/cat` could have been written before this session, by another process, by a sibling checkout, or by a download, and the guard knows nothing about any of these. The new tests cover only `./cat` and `./git` (inside the root). `tool/test_guard.py:432-434` asserts the opposite of the rule: `/usr/bin/time` keeps its model because it is outside the roots. That made the wrong boundary a regression requirement.
- The round-2 fix also left a related hole: its fallback `{("spawn", name)}` is keyed by the basename, and `guard_grant.outside()` compares that against `g.executables`, which is also built from basenames (guard_grant.py:136-137 via `exe()`). So a path-named file whose basename happens to be granted, for example `./python` when the gate command spawns `python`, is still accepted. The F11 tests pass only because `cat` and `git` are not spawn-granted names.

## Sources

### S1 — exec(3) — Linux manual page (Ubuntu manpages)

- URL: https://manpages.ubuntu.com/manpages/questing/man3/exec.3.html
- Retrieved: 2026-11-02
- Locator: execlp/execvp/execvpe description, PATH search
- Fragment: As summarised in the search result: the exec functions repeat the shell's PATH search only when the specified filename does not contain a slash (/); if the filename includes a slash, PATH is ignored and the file at the specified pathname is executed.
- Applicability: A slash-named command in run_argv runs exactly the file at that path, so the basename `exe()` returns does not identify the program.
- Counterevidence: This is the libc/Linux reading; Windows CreateProcess and cmd.exe resolve names differently (see S4).
- Validation: Test (1) in the experiment: `../outside/cat` must not receive the `cat` model.

### S2 — Austin Group Defect Tracker, issue 1789 (quoting XCU 2.9.1.1 Command Search and Execution)

- URL: https://austingroupbugs.net/view.php?id=1789
- Retrieved: 2026-11-02
- Locator: quoted text of 2.9.1.1 Command Search and Execution
- Fragment: As summarised in the search result: if the command name does not contain any slash characters, the command is searched for in PATH; if the command name contains at least one slash, and the named utility exists, the shell executes it with the command name as the path and as arg0. The issue itself questions how arg0 interacts with a slash-containing name, so that wording may be revised.
- Applicability: Same rule for the shell dialect `posix` that run_argv models: a path name is not a PATH lookup.
- Counterevidence: The open issue concerns arg0 wording, not the no-search rule.
- Validation: Bare names keep their models (experiment test 4); slash names do not (tests 1 and 2).

### S3 — OWASP Input Validation Cheat Sheet

- URL: https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet
- Retrieved: 2026-11-02
- Locator: Allowlist vs Denylist
- Fragment: As summarised in the search result: denylisting dangerous characters or patterns is described as a massively flawed approach; allowlist validation involves defining exactly what IS authorized, and by definition, everything else is not authorized. OWASP also notes that input validation should not be the primary defense against XSS or SQL injection, where output encoding and parameterized queries still matter.
- Applicability: `planted()` is a denylist (suspect = inside the roots) and lets through everything it did not list; the rule needs the allowlist form: trusted only if explicitly verified or granted.
- Counterevidence: The guidance is about input validation in web applications; carrying it over to executable identity is my interpretation, not OWASP's.
- Validation: No path outside an explicit list or grant receives a model, whatever its location (tests 1 and 2).

### S4 — NeedCurrentDirectoryForExePathW function (processenv.h) — Microsoft Learn

- URL: https://learn.microsoft.com/en-us/windows/win32/api/processenv/nf-processenv-needcurrentdirectoryforexepathw
- Retrieved: 2026-11-02
- Locator: Remarks
- Fragment: As summarised in the search result: cmd.exe calls this function because it does its own path resolution before calling CreateProcess; if it returns TRUE, cmd.exe uses the path ".;%PATH%" for the executable search, and if FALSE, only "%PATH%". For a name without a backslash only the existence of NoDefaultCurrentDirectoryInExePath is checked, not its value; a name containing a backslash always returns TRUE.
- Applicability: Limits the fix: in the `cmd` dialect even a bare name can run a file from the current directory, so basename identity for bare names is not fully safe there either.
- Counterevidence: PowerShell does not search the current directory (a community report, not this page), so this applies only to `cmd`.
- Validation: Not covered by this fix; recorded under limits.

### S5 — tool/guard_models.py (repository)

- URL: tool/guard_models.py
- Retrieved: 2026-11-02
- Locator: lines 45-48 exe(); 804-821 planted(); 838-851 run_argv()
- Fragment: `exe()`: "An executable's identity: its file name, lowercased, without the Windows launcher suffix." `planted()` docstring: "A system binary (`/usr/bin/time`, `C:/Windows/...`) is outside every write root and keeps its model; a path that resolves inside a write root — or cannot be resolved — could be planted". Line 843: `if re.search(r"[\\/]", raw) and planted(raw, ctx): return {("spawn", name)}, None`, followed by `if name in READERS and name not in MODELS: return set(), None`.
- Applicability: Shows both the denylist predicate and the basename identity used for both the model and the spawn key.
- Counterevidence: none given
- Validation: After the fix, no slash-named command reaches line 848.

### S6 — tool/guard_grant.py (repository)

- URL: tool/guard_grant.py
- Retrieved: 2026-11-02
- Locator: lines 136-137 and 153-155
- Fragment: `executables = {e[1] for c in commands if c.strip() for e in derive(c, "posix", ctx) if e[0] == "spawn"}` / `executables |= {exe(s) for s in strings("executables")}` / `if kind == "spawn": return None if target in g.executables else ...`
- Applicability: Grant matching is by basename, so a path-named file whose basename is granted is accepted. A path-keyed spawn has to change both sides, derive and the `exe(s)` normalisation of adapter strings, together.
- Counterevidence: Whether `python` or another spawn name is actually in this repository's derived set has not been checked by running.
- Validation: Experiment test (2), and test (3) for gate commands that are still allowed.

### S7 — tool/test_guard.py (repository)

- URL: tool/test_guard.py
- Retrieved: 2026-11-02
- Locator: lines 413-434
- Fragment: `"F11-local-executable": ("./cat", "Bash", "허가된 실행 파일이 아니다")`, `"F11-local-modelled-program": ("./git push origin HEAD", ...)`; `def test_a_system_executable_by_absolute_path_keeps_its_model(repo): # The planted-binary rule is scoped to paths the task could write: a system path is trusted. assert call(repo, "/usr/bin/time python tool/search q") is None`
- Applicability: The regression tests cover only paths inside the roots, and one test asserts the outside-the-roots-means-trusted reading the review rejected.
- Counterevidence: none given
- Validation: Line 434 is rewritten to the chosen policy and the outside-the-roots case is added.

## Claims

- C1 (source; S1, S2): A command name that contains a slash is executed as that exact file with no PATH search, so its basename does not identify the program.
- C2 (source; S3): Authorization should list what is permitted and deny everything else, because a list of known-bad patterns fails on cases it did not anticipate.
- C3 (interpretation; S5, S7, S3): The round-2 fix failed because `planted()` treats every path outside the write roots as a verified system program, and a test enshrines that reading.
- C4 (interpretation; S5, S6): Because spawn effects and grant entries are both keyed by basename, a path-named file whose basename is granted passes even when `planted()` is True; the fix must key slash-named spawns by resolved path on both the derive and the judge side.
- C5 (source; S4): In the cmd dialect a bare command name can also resolve to a file in the current directory, so the fix for slash names does not make bare-name identity safe there.

## Core theory

Under POSIX and the exec family, a command name that contains a slash is not looked up in PATH: the file at that exact path is executed. Its identity is therefore the path, not the name. Security validation guidance says to define exactly what is authorized and deny everything else, because a list of known-bad patterns fails on cases the author did not anticipate.

## Applicability

tool/guard_models.py:843-851: the slash branch reaches basename dispatch whenever `planted()` returns False. tool/guard_models.py:804-821: `planted()` is the denylist itself. tool/guard_models.py:847 and 851, together with tool/guard_grant.py:136-137 and 153-155: spawn identity and grant matching both use `exe()` basenames. tool/test_guard.py:432-434: the test that asserts the denylist reading. tool/guard_windows.py:191 and 204 also emit `("spawn", name)` for Start-Process and cmdlets, so the same basename identity should be checked there when the key changes.

## Hypothesis

Two things together cause this. First, `planted()` is a denylist: it asks whether this session could have written the file, when the rule needs a positive check that the file is a known system program, which nothing performs. Second, a command named by a path is identified everywhere by its basename (dispatch, the spawn effect, grant matching), when its real identity is the file at that path. As long as the basename is the identity, any denylist will leave paths it did not anticipate, and outside-the-roots is the case it missed this time. The fix: in `run_argv`, a command name containing a slash never reaches READERS/MODELS. It becomes a spawn keyed by its resolved path (for example `("spawn", resolved_path)`), and `load` derives grant executables through the same `derive`, so gate commands such as `.venv/bin/python` still match. Optionally, a small fixed list of system directories that sit outside every write root can keep the basename model. That list should be added only if a real gate command needs it.

## Counterevidence

- A command named by a bare word does go through a PATH lookup. Its basename identity is reasonable there, provided PATH has not been changed; tool/guard.py:675 `ENV_EXEC` already treats an assignment to PATH as an exec-affecting variable. So the trouble is limited to slash-named commands, and bare names should not be swept into the fix.
- Refusing `/usr/bin/time python ...` by default is stricter than the current test expects. If an adapter's gate command uses an absolute system path, it will break unless the grant is derived from the same path-keyed effect, as proposed.

## Alternatives

- Extend `planted()` with more suspect cases (sibling directories, home, temp): this is still a denylist and fails again on the next unanticipated location. Rejected.
- A fixed allowlist of system directories (/usr/bin, /bin, /usr/sbin, /sbin, %SystemRoot%\System32, Git for Windows usr/bin): acceptable as a second step, but on Windows it needs realpath plus a check that none of these directories is inside a write root. Not first, because nothing in the repository needs it except one test.
- Hash or signature verification of the binary: real identity, but heavy, platform-specific and not needed. YAGNI.
- Make every slash-named command `top`: also safe, but it removes the useful route of granting a specific local tool (e.g. `.venv/bin/python`) through derived gate commands. A path-keyed spawn keeps that route.

## Experiment

Add these cases to tool/test_guard.py, then run `pytest tool/test_guard.py`. (1) An existing executable `../outside/cat` outside every root: denied with '허가된 실행 파일이 아니다', or as `top`. (2) With `python` in the derived executables, `./python x` and `../outside/python x` are denied. Run this one before the fix; if it passes now, the basename-grant part of the hypothesis is wrong. (3) A gate command `.venv/bin/python -m pytest` written in the adapter: the same command is still allowed, because grant derivation and judging produce the same path key. (4) Bare `cat file` and `git status` keep their models. (5) Rewrite the `/usr/bin/time` test to match the chosen policy: denied unless granted by path, or allowed through the explicit system-directory list.

## Result

The hypothesis held, including the basename-grant part. On `1b4df77`:

- `../outside/cat README.md` was allowed as the `cat` reader.
- `./python tool/x.py`, `../outside/python tool/x.py`, `Start-Process ../outside/python` and `& ./python x` were allowed as the granted `python`.
- `/usr/bin/time python …` was allowed as the `time` prefix.

The fix is in the commit that adds this result, on top of `1b4df77`. It removes `planted()`. A command name containing a path separator never reaches `READERS` or `MODELS`. Instead, `guard_models.spawn_of` keys it by its resolved path; on Windows the key is lowercased and drops the launcher suffix. Start-Process uses the same helper, and so do path entries in `[guard] executables`, so `.venv/bin/python` granted by path matches the same command. No system-directory list was added, because no gate or verification command of this repository runs a program by path.

After the fix, `test_a_path_named_executable_is_judged_by_its_path` (6 forms) and `test_a_path_granted_by_path_is_allowed` pass, and bare `cat` and `git status` keep their models. The old `/usr/bin/time` test pinned the opposite policy and was removed.

Cost on the corpus: two of the 260 commands go from allowed to denied, `/usr/bin/time python …` and `& C:/…/python.exe`. Six corpus entries now record a path-keyed spawn.

## Unresolved limits

Bare-name lookups are not verified either. Microsoft documents that cmd.exe searches the current directory before PATH unless NoDefaultCurrentDirectoryInExePath is set, so in the `cmd` dialect a planted `cat.exe` in the cwd could run under the bare name `cat`. That is a related hole outside F11's wording; the review may raise it next. I have not checked how `resolve()` handles symlinks or junctions for a path-keyed spawn, nor whether `PATHEXT` resolution on Windows changes the key (e.g. `./tool` vs `./tool.cmd`). The basename-grant hole (failed_fixes[1]) is inferred from reading the code, not observed in a run.
