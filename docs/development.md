# Development and checks

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff check tool
python -m pytest -q tool
python tool/test_lint.py
python tool/test_apply.py
python tool/test_inject.py
python tool/test_declared_continuation.py
python tool/test_repo_lint.py
python tool/test_trajectory.py
python tool/lint.py --check
npm --prefix web ci
npm --prefix web run lint
npm --prefix web run build
python tool/graph.py
```

Inside a virtual environment, run with that environment's Python.
`graph.json` is generated from the shared rules and excluded from git.
Building the map against real projects can pull in those projects' paths and
state, so the result is not added to the public copy. Regenerate the map file
with the `python tool/graph.py` command above.

The automatic checks use a temporary project. Real users' conversation records
are not test input. They confirm settings merging, reinstalling, paths with
Korean characters and spaces, and settings separation between same-named
checkouts. A real OAuth sign-in, the host's automatic events and the answer
quality review are not part of these results.

Having changed a hook implementation, follow the
[host confirmation procedure](hooks-setup.md) as well. Before publishing, run
the [public-copy update procedure](publishing.md) over the files and the git
history.

## Attaching this repository to the rules

This wiki is maintained too, so the same hooks hang on itself. The method is
the one in the [hooks install guide](hooks-setup.md); only `--project` differs,
pointing at this repository. Run it once per checkout.

Start by creating this repository's `.wiki/adapter.toml`. The public copy
keeps wiring files out of git (`tool/test_distribution.py` checks that), so
each checkout writes its own.

```toml
agents = ["claude", "codex"]

[slots]
review_dir = "artifacts/review"
gate_cmd = "python -m pytest tool"
live_cmd = "open a new Claude Code session and a new Codex session, and confirm the hooks actually run"
server_stop = "close the wiki-agent window (tool/app.cmd), or Ctrl+C in the terminal that ran python tool/main"
scratch_dirs = "artifacts/"
```

```powershell
python tool/setup_agents.py --project . --agent both
```

When the target is the wiki itself, the `.wiki/wiki-revision` pin and the
dirty check on the executing code are skipped. The pin answers "which wiki
version is this target bound to", and when the target is the wiki the answer
is always the current HEAD — so it goes stale on every commit and is always
dirty while the tools are being changed. Installing into another project keeps
both checks.

`.wiki/adapter.toml`, `.claude/settings.json`, `.codex/hooks.json` and
`.codex/config.toml` are all excluded from git. They carry absolute paths and
per-machine settings, and the public copy keeps runtime wiring outside version
control. Using Codex means putting `hooks = true` under `[features]` into your
own checkout's `.codex/config.toml` yourself.

On Windows, run the install and the checks from PowerShell. Run inside Git
Bash, `git` resolves to `mingw64/bin`, the installer cannot find Git Bash, and
`tool/test_codex_hooks.py` fails for the same reason. If it has to run in Git
Bash, point `CLAUDE_CODE_GIT_BASH_PATH` at the real `bash.exe`.

Records accumulate only in this repository's `.wiki/`. `corpus.json`,
`graph.json` and `decisions/` are per repository and do not mix with another
project's `.wiki/`. The root `graph.json` and `raw/` are separate — hub assets
used by the chat screen.
