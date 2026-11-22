"""hook — the one entry point the user-level hooks call.

`"<python>" "<wiki>/tool/hook.py" <host> <script> [args...]`

Wiring used to live in each checkout's `.claude/settings.json` and
`.codex/hooks.json`, with the checkout's absolute path baked into every
command. Both files are gitignored, so a fresh worktree
had no hooks at all, and nothing said so. The
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
import re
import runpy
import sys
from pathlib import Path

# Before anything that touches the disk, this file's own path included: imported, it arms a PreToolUse wrapper's
# credential-session deny (`hook_deadline.PRETOOL`). Run as a script, this folder is already first on the path.
# The project lookup and the data paths load inside `main`, after it.
import hook_deadline

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# The scripts that read a target repository and need to be told which one.
PROJECT = {"inject.py", "session_state.py", "sync.py"}
LEGACY = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json"}


def closed(script: str, why: str) -> int:
    """A PreToolUse check (`claude_pretool`, `codex_pretool`) that never ran: denied in a session carrying a
    oneshot's secrets (`secrets_held`, which loads with nothing else), passed as before in any other."""
    if script in hook_deadline.PRETOOL and (out := __import__("secrets_held").held_closed(why)):
        hook_deadline.send(out)   # the one answer: the deadline's never follows it
    return 0


def project_for(cwd: Path) -> tuple[Path, Path] | None:
    """`(project, checkout)` for a directory, or `None` when it is not attached.

    Read off `.git` rather than asked of `git rev-parse`: every hook of every tool call
    comes through here, and starting `git` was most of a hook's time."""

    try:
        cwd = cwd.resolve()
        # git could not start in a folder that is missing or refuses listing, and the hooks passed.
        os.scandir(cwd).close()
        from common import layout

        found = layout(cwd)
    except OSError:
        return None
    if found is None:
        return None
    top, _gitdir, _common, repo = found
    # Inside `.git` git answers "not a work tree", and the hooks passed there.
    if (top / ".git") in (cwd, *cwd.parents):
        return None
    for candidate in (repo, top):
        if (candidate / ".wiki/adapter.toml").is_file():
            return candidate, top
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
    # First, before the script check, reading stdin and resolving the project:
    # all of it counts against the host's limit. Its fallback stays empty until
    # the project is known to be attached, and an empty one never exits — the
    # host decides then. Only as the host's process: a test calling `main`
    # would be ended by the timer.
    pre = len(argv) > 1 and argv[1] in hook_deadline.PRETOOL
    timed = len(argv) > 1 and (argv[1] in hook_deadline.LIMITS or pre) and Path(sys.argv[0]).name == "hook.py"
    if timed and not (pre and hook_deadline._armed):   # imported as the host's process, it is armed already
        try:   # a wrapper's deny is in place from here, before stdin and the project are read
            hook_deadline.arm(hook_deadline.PRETOOL[argv[1]] if pre else hook_deadline.LIMITS[argv[1]][0],
                              hook_deadline.pretool() if pre else None)
        except Exception:  # noqa: BLE001 — without a deadline the hook still runs, as it did before one
            timed = False
    if len(argv) < 2 or argv[0] not in LEGACY or not (HERE / argv[1]).is_file():
        print("hook skipped: usage hook.py <claude|codex> <script> [args]", file=sys.stderr)
        return closed(argv[1] if len(argv) > 1 else "", "훅이 검사 스크립트를 찾지 못했다")   # a wrapper gone
    host, script, extra = argv[0], argv[1], argv[2:]
    raw = sys.stdin.buffer.read()
    try:
        cwd = Path(json.loads(raw.decode("utf-8") or "{}").get("cwd") or os.getcwd())
    except (ValueError, AttributeError):
        cwd = Path.cwd()
    found = project_for(cwd)
    if found is None:
        return closed(script, "훅이 이 작업의 프로젝트를 찾지 못했다")
    project, top = found
    # A credential session runs every check here: a legacy entry may not cover this call (its matcher, its event).
    held = bool(os.environ.get(__import__("secrets_held").SECRETS_HELD))
    if not held and legacy(top, host, script):
        return 0
    if script == "claude_pretool.py" and not held:
        # Each check it holds keeps the rule it had as a hook of its own.
        from claude_pretool import MEMBERS

        if old := [s for s in MEMBERS if legacy(top, host, s)]:
            extra += ["--without", ",".join(old)]
    if script in PROJECT:
        extra += ["--project", str(project)]
    if script == "session_state.py":
        extra += ["--checkout", str(top)]
    if script == "inject.py":
        # The host decides the ceiling the injection is fitted to, and without
        # one the injection deduplicates nothing — `inject.recall`. It also
        # carries the host-specific output limit.
        extra += ["--host", host]

    # In this process rather than a second interpreter: the hooks run under
    # 10-second budgets and a Python start is a real share of that. `argv[0]`
    # is the script's own, which is what `hook_diagnostics` arms on.
    sys.argv = [str(HERE / script), *extra]
    sys.stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    nonce = os.environ.get("WIKI_PROBE", "")
    # The fallback goes live only now, the project known to be attached, and
    # in probe mode only once the buffer that forwards it is in place.
    # A PreToolUse check still running then denies in a credential session (`secrets_held`); in any other there is
    # no fallback, and the host decides as before.
    fallback = (hook_deadline.stalled(script) if script in hook_deadline.LIMITS else hook_deadline.pretool()) \
        if timed else None
    if not nonce:
        hook_deadline.best = fallback
        runpy.run_path(str(HERE / script), run_name="__main__")
        return 0
    # A connection test (`main/connect.probe`) started the host that started
    # this. What the script prints is held, passed on unchanged, and noted.
    # A text stream over bytes, not `StringIO`: every script calls
    # `sys.stdout.reconfigure`, which `StringIO` does not have.
    held = io.BytesIO()
    done: list[bool] = []

    def forward() -> None:
        # Once, from the `finally` or from the deadline's hard exit, which
        # skips the `finally`; the second caller waits for the first. Under the
        # lock the deadline writes its answer under, so the buffer is never
        # read halfway through an answer.
        with hook_deadline.lock:
            if done:
                return
            done.append(True)
            sys.stdout.flush()
            printed = held.getvalue().decode("utf-8", "replace")
            sys.stdout = out
            out.write(printed)
            out.flush()
            # Final: what the probe recorded is what went out, so no answer
            # may follow it.
            hook_deadline.close()
            probed(nonce, host, script, raw, printed)

    # The buffer, its forwarding and the fallback go in together: an answer
    # written in between would miss the buffer or the record.
    with hook_deadline.lock:
        out, sys.stdout = sys.stdout, io.TextIOWrapper(held, encoding="utf-8", write_through=True)
        hook_deadline.before_exit = forward
        hook_deadline.best = fallback
    try:
        runpy.run_path(str(HERE / script), run_name="__main__")
    finally:
        forward()
    return 0


def probed(nonce: str, host: str, script: str, raw: bytes, printed: str) -> None:
    """One line in this hub's `raw/connect/probe/<nonce>.jsonl`: which event
    ran and whether it injected. A line per hook, appended — a session runs
    several, and the one that does not inject must not overwrite the one that
    did. A failure is swallowed: the hook still passes, and the test, with no
    line to read, fails."""

    if not re.fullmatch(r"[0-9a-f]{8,64}", nonce):
        return
    try:
        event = json.loads(raw.decode("utf-8") or "{}").get("hook_event_name") or script
        try:
            context = json.loads(printed)["hookSpecificOutput"]["additionalContext"]
        except (ValueError, KeyError, TypeError):
            context = ""
        from common.paths import RAW

        folder = Path(os.environ["WIKI_PROBE_ROOT"]) if os.environ.get("WIKI_PROBE_ROOT") else RAW / "connect/probe"
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / f"{nonce}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"host": host, "event": event, "script": script,
                                 "injected": bool(context), "chars": len(context)}) + "\n")
    except Exception:  # noqa: BLE001 — `craft/hooks-fail-open`
        pass


def run(argv: list[str]) -> int:
    script = argv[1] if len(argv) > 1 else ""   # read first: `main` hands `sys.argv` to the script it runs
    try:
        return main(argv)
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001
        print(f"hook skipped: {type(error).__name__}", file=sys.stderr)
        return closed(script, f"훅이 실패했다 ({type(error).__name__})")


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
