"""hook — the one entry point the user-level hooks call.

`"<python>" "<wiki>/tool/hook.py" <host> <script> [args...]`

Wiring used to live in each checkout's `.claude/settings.json` and
`.codex/hooks.json`, with the checkout's absolute path baked into every
command. Both files are gitignored, so a fresh worktree — which is what Orca
opens after every CLI update — had no hooks at all, and nothing said so. The
hosts' user-level settings are read in every directory, so the wiring moves
there and the project is worked out here, per call, from where the session is.

The project is the repository's main clone when it carries
`.wiki/adapter.toml`: that is where `project.md`, the decisions and the
trajectory accumulate, and a worktree is a view of the same repository. A
checkout that is not attached passes silently — these hooks fire in every
directory on the machine now.
"""

from __future__ import annotations

import io
import json
import os
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from workspace.sessions import checkout  # noqa: E402

# The scripts that read a target repository and need to be told which one.
PROJECT = {"inject.py", "session_state.py", "sync.py"}
LEGACY = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json"}


def project_for(cwd: Path) -> tuple[Path, Path] | None:
    """`(project, checkout)` for a directory, or `None` when it is not attached."""

    top, repo, _branch = checkout(cwd)
    for candidate in (repo, top):
        if candidate and (Path(candidate) / ".wiki/adapter.toml").is_file():
            return Path(candidate), Path(top)
    return None


def legacy(top: Path, host: str, script: str) -> bool:
    """Does this checkout still carry a per-project install of the same hook?

    The host runs both layers. Running the hook twice doubles the injection,
    so while an old install is still there it keeps the job.
    """

    try:
        text = (top / LEGACY[host]).read_text(encoding="utf-8")
    except (OSError, KeyError):
        return False
    # The substring only screens. Somebody's `--watch "<wiki>/tool/inject.py"`
    # names the path as data, so whether a command runs it is `apply.runs`'s
    # call — imported only past the screen, since it costs a fifth of a second.
    if (HERE / script).as_posix() not in text:
        return False
    from apply import dispatches, runs

    try:
        settings = json.loads(text)
    except ValueError:
        return False
    return any(
        runs(str(h.get("command", "")), script) and not dispatches(str(h.get("command", "")))
        for groups in (settings.get("hooks") or {}).values()
        for group in groups
        for h in group.get("hooks", [])
    )


def main(argv: list[str]) -> int:
    # Whatever the script it runs prints goes out through this stream.
    sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) < 2 or argv[0] not in LEGACY or not (HERE / argv[1]).is_file():
        print("hook skipped: usage hook.py <claude|codex> <script> [args]", file=sys.stderr)
        return 0
    host, script, extra = argv[0], argv[1], argv[2:]
    raw = sys.stdin.buffer.read()
    try:
        cwd = Path(json.loads(raw.decode("utf-8") or "{}").get("cwd") or os.getcwd())
    except (ValueError, AttributeError):
        cwd = Path.cwd()
    found = project_for(cwd)
    if found is None:
        return 0
    project, top = found
    if legacy(top, host, script):
        return 0
    if script in PROJECT:
        extra += ["--project", str(project)]
    if script == "session_state.py":
        extra += ["--checkout", str(top)]

    # In this process rather than a second interpreter: the hooks run under
    # 10-second budgets and a Python start is a real share of that. `argv[0]`
    # is the script's own, which is what `hook_diagnostics` arms on.
    sys.argv = [str(HERE / script), *extra]
    sys.stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    runpy.run_path(str(HERE / script), run_name="__main__")
    return 0


if __name__ == "__main__":
    try:
        _code = main(sys.argv[1:])
    except SystemExit:
        raise
    except Exception as _error:  # noqa: BLE001
        print(f"hook skipped: {type(_error).__name__}", file=sys.stderr)
        _code = 0
    raise SystemExit(_code)
