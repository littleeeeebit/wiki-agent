"""What a task may do, computed once per tool call, and why an effect falls outside it — for `guard`.

The grant is read off files, never a model: the checkout's root (or a plan
task's artifact root), the temporary directories and the session's memory
folder as write roots; the executables the adapter's gate, verification and
registered checks spawn, parsed by the same algebra; the git remotes' hosts
plus the loopback the search daemon answers on; and an optional `[guard]`
table that adds write roots, network hosts and executables but never a
command pattern.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile

from guard import HERE, Ctx, contained, derive


@dataclass
class Grant:
    roots: list
    protected: list
    executables: set
    hosts: set
    remotes: tuple


# Inside a write root, but they decide the grant or what runs next: never granted.
PROTECTED = (".git", ".claude", ".codex", ".wiki/adapter.toml", "verification.toml")
SEARCH = "python tool/search"
GUARD_KEYS = ("write_roots", "network_hosts", "executables")


def temps() -> list[Path]:
    """The temporary directories, Git Bash's `/tmp` (`TEMP`) first; Python's own may differ (`TMPDIR`)."""
    found = [os.environ.get(k) for k in ("TEMP", "TMP")] + [tempfile.gettempdir()]
    return list(dict.fromkeys(Path(p) for p in found if p))


def toml(path: Path) -> dict:
    import tomllib

    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def layout(cwd: Path) -> tuple | None:
    """`(checkout, git dir, common dir, main clone)` read off git's own files, without running git."""
    top = next((p for p in (cwd, *cwd.parents) if (p / ".git").exists()), None)
    if top is None:
        return None
    gitdir = top / ".git"
    if gitdir.is_file():
        m = re.match(r"gitdir:\s*(.+)", gitdir.read_text(encoding="utf-8", errors="replace").strip())
        if not m:
            return None
        gitdir = (top / m.group(1).strip()).resolve()
    common = gitdir
    if (gitdir / "commondir").is_file():
        common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
    return top, gitdir, common, common.parent if common.name == ".git" else common


def remote_hosts(common: Path) -> tuple:
    from guard_models import host_of

    hosts, name = {}, None
    try:
        text = (common / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    for line in text.splitlines():
        if m := re.match(r'\s*\[remote\s+"([^"]+)"\]', line):
            name = m.group(1)
        elif line.strip().startswith("["):
            name = None
        elif name and (m := re.match(r"\s*(?:push)?url\s*=\s*(\S+)", line)):
            hosts.setdefault(name, host_of(m.group(1)))
    return tuple(sorted(hosts.items()))


def plan_root(top: Path, gitdir: Path, main: Path) -> Path | None:
    """A plan task's artifact root, from the hub's spec for this branch; `None` for any other task."""
    try:
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    branch = head.removeprefix("ref: refs/heads/") if head.startswith("ref: ") else ""
    sid = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]+", "-", branch.lower())).strip("-")[:64].rstrip("-")
    try:
        spec = json.loads((HERE.parent / "raw/specs" / main.name / f"{sid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    root, worktree = str(spec.get("artifact_root") or "").strip("/"), spec.get("worktree")
    if spec.get("review_profile") != "plan" or not root or not worktree or Path(worktree).resolve() != top:
        return None
    # A root that would leave the checkout narrows nothing: the checkout stays the bound.
    return top / root if contained(str(top / root), [str(top)]) else top


def grant(cwd: Path, payload: dict) -> Grant | None:
    """What this task may do, computed for this call; `None` where no repository is attached."""
    from guard_models import exe, host_of

    found = layout(cwd.resolve())
    if found is None:
        return None
    top, gitdir, common, main = found
    adapter = next((p / ".wiki/adapter.toml" for p in (main, top) if (p / ".wiki/adapter.toml").is_file()), None)
    if adapter is None:
        return None
    data = toml(adapter)
    table = data.get("guard") if isinstance(data.get("guard"), dict) else {}

    def strings(key: str) -> list:
        value = table.get(key)
        return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []

    roots = [plan_root(top, gitdir, main) or top, *temps()]
    if payload.get("transcript_path"):
        roots.append(Path(str(payload["transcript_path"])).parent / "memory")
    roots += [top / Path(r).expanduser() for r in strings("write_roots") if not re.search(r"[$%]", r)]
    checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
    verification = (toml(top / "verification.toml") or toml(main / "verification.toml")).get("verification") or {}
    commands = [str((data.get("slots") or {}).get("gate_cmd") or ""), SEARCH]
    commands += [str(verification.get(k) or "") for k in ("round", "local")]
    commands += [str(c.get("cmd") or "") for c in checks.values() if isinstance(c, dict)]
    ctx = Ctx("posix", frozenset({top.as_posix()}), Path.home().as_posix(), temps()[0].as_posix(), os.name == "nt")
    executables = {e[1] for c in commands if c.strip() for e in derive(c, "posix", ctx) if e[0] == "spawn"}
    executables |= {exe(s) for s in strings("executables")}
    remotes = remote_hosts(common)
    hosts = {"127.0.0.1"} | {h for _, h in remotes} | {host_of(s) for s in strings("network_hosts")}
    protected = sorted({str(p / x) for p in (top, main) for x in PROTECTED})
    return Grant([str(r) for r in roots], protected, executables, hosts, remotes)


def outside(effect: tuple, g: Grant) -> str | None:
    """Why `effect` leaves the grant, naming what would allow it; `None` when it is inside."""
    kind, target = effect
    if kind == "read":
        return None
    if kind == "top":
        return f"판단할 수 없는 형태다({target}) — 어떤 허가로도 열리지 않으니, 리터럴 경로와 직접 실행하는 명령으로 풀어 쓴다"
    if kind == "spawn":
        return None if target in g.executables else \
            f"허가된 실행 파일이 아니다 — `.wiki/adapter.toml` 의 `[guard] executables` 에 `{target}` 를 더하면 열린다"
    if kind == "network":
        return None if target in g.hosts else \
            f"허가된 호스트가 아니다 — `.wiki/adapter.toml` 의 `[guard] network_hosts` 에 `{target}` 를 더하면 열린다"
    # A delete or a tree write (recursive copy, extraction) also reaches a protected path it contains.
    reaches_protected = kind in ("delete", "write_tree") and any(contained(p, [target]) for p in g.protected)
    if any(contained(target, [p]) for p in g.protected) or reaches_protected:
        return "허가를 정하는 파일이다 — 어떤 `[guard]` 항목으로도 열리지 않고, 사람이 직접 고친다"
    if contained(target, g.roots):
        return None
    return "쓰기 루트 밖이다 — `.wiki/adapter.toml` 의 `[guard] write_roots` 에 이 경로를 담은 디렉터리를 더하면 열린다"
