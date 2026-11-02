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
import shutil
import tempfile

from guard import HERE, Ctx, contained, derive, real_path


@dataclass
class Grant:
    roots: list
    protected: list
    executables: set
    hosts: set
    remotes: tuple
    top: str = ""                       # the checkout the remotes were read from
    hooks: str = ""                     # a hooks folder git is configured to run from inside a write root


# Inside a write root, but they decide the grant or what runs next: never granted.
PROTECTED = (".git", ".claude", ".codex", ".wiki/adapter.toml", "verification.toml")
SEARCH = "python tool/search"


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


# A destination git reaches that is not a network host the guard can name: a local path, a `file://` URL,
# or one a URL rewrite may change. A push to it is `top`, and it is never granted as a host.
UNREAD = "\0unread"
# A remote name marking a configuration that may make any git call fetch (a partial clone) or append to a
# file (a Trace2 target), or a file the reader does not open that may say so.
LAZY = "\0lazy"


def git_configs() -> list:
    """The text of every global and system configuration file git reads besides the repository's own."""
    home, env = Path.home(), os.environ
    places = [home / ".gitconfig", Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "git/config",
              Path("/etc/gitconfig"), Path(env.get("PROGRAMDATA") or "C:/ProgramData") / "Git/config"]
    places += [Path(env[k]) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM") if env.get(k)]
    if git := shutil.which("git"):
        # Git for Windows keeps its system file beside its install: `<root>/etc/gitconfig`, `<root>/mingw64/...`.
        places += [p / "etc/gitconfig" for p in Path(git).resolve().parents[:3]]
    out = []
    for p in places:
        try:
            out.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return out


def remote_hosts(common: Path) -> tuple:
    """`((remote, host), ...)` for every `url` and `pushurl` of every remote, and for a branch's upstream
    given as a URL (`("", host)`), since a push may reach any of them."""
    from guard_models import host_of

    try:
        text = (common / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""                       # git works without one; the global and system files still apply
    # A URL rewrite, or an included file that may hold one or another remote, changes where a push goes, and
    # a remote or branch set outside the repository adds destinations this reader does not take from there.
    # Any mention in any file git reads, in any syntax (a key after a header, a continued line), counts.
    # `worktreeConfig` adds a private file per worktree, which may name its own push URL. A URL-specific
    # `followRedirects` wins over the `-c http.followRedirects=false` git's network rows require. A bundle
    # URI, packfile URIs, a proxy or a resolve override (in a file or the environment) add a host besides
    # the remote's, and a cookie file is where git writes what the server sends.
    others = r"(?:^|[\s\]])(?:bundleuri|uriprotocols|(?:git)?proxy\w*|curloptresolve|cookiefile|savecookies)\s*=" \
             r"|\[\s*bundle\b"
    rewritten = re.search(r"(?im)insteadof|\[\s*include|worktreeconfig|followredirects|" + others, text) \
        is not None or any(re.search(r"(?im)insteadof|followredirects|\[\s*(include|remote|branch)|" + others, t)
                           for t in git_configs()) \
        or any(os.environ.get(k) for k in ("http_proxy", "https_proxy", "all_proxy", "HTTPS_PROXY", "ALL_PROXY"))
    # A promisor remote lets any read fetch, and a Trace2 target lets any call append to a file; a file this
    # reader does not open may hold either, and so may a submodule's own configuration, which a read that
    # recurses (by `submodule.recurse`, with no flag) uses for its objects.
    lazy = re.search(r"(?i)partialclone|promisor|trace2|\[\s*(include|submodule)|worktreeconfig", text) \
        or any(re.search(r"(?i)partialclone|promisor|trace2|\[\s*(include|submodule)", t) for t in git_configs()) \
        or (common / "modules").exists()
    found, section = [], ("", "")
    for line in text.splitlines():
        if not line.strip() or re.match(r"\s*[#;]", line):
            continue
        # A continued line joins the next one into a value this reader does not decode.
        rewritten = rewritten or line.rstrip().endswith("\\")
        if line.lstrip().startswith("["):
            m = re.fullmatch(r'\s*\[\s*([\w-]+)(?:\s+"([^"\\]*)")?\s*\]\s*', line)
            # Only a header alone on its line, `[section]` or `[section "sub"]`, is read; anything else
            # (a key after the header, the old `[section.sub]` form) leaves every destination unread.
            rewritten = rewritten or not m
            section = (m.group(1).lower(), m.group(2) or "") if m else ("", "")
            continue
        m = re.match(r"\s*([\w-]+)\s*(?:=\s*(.*))?$", line)
        key, value = (m.group(1).lower(), (m.group(2) or "").rstrip()) if m else ("", "")
        if section[0] == "remote" and key in ("url", "pushurl"):
            name = section[1]
        elif section[0] == "branch" and key in ("remote", "pushremote"):
            name = ""
        else:
            continue
        if not re.fullmatch(r"[^\s\"\\;#]+", value):
            rewritten = True            # a quoted, escaped or commented value git decodes differently
        elif name or re.search(r"[:/\\]", value):
            found.append((name, value))
    out = set()
    for name, value in found:
        networked = re.match(r"[a-zA-Z][\w+.-]*://", value) and not value.lower().startswith("file:") \
            or re.match(r"[^/\\:\s]+@[^/\\:\s]+:", value)
        out.add((name, host_of(value) if networked and not rewritten else UNREAD))
    return tuple(sorted(out | ({(LAZY, UNREAD)} if lazy else set())))


def hook_folders(common: Path) -> list:
    """Every folder a `core.hooksPath` in any configuration git reads may name. Only an absolute value on a
    line of its own is read as written. A relative one depends on which worktree runs it, and a value in
    another syntax, an include or a per-worktree file is unseen: each is `None`, a folder the task may write."""
    try:
        texts = [(common / "config").read_text(encoding="utf-8", errors="replace")]
    except OSError:
        texts = []
    texts += git_configs()
    unseen = any(re.search(r"(?i)\[\s*include|worktreeconfig", t) for t in texts)
    out = [None] if unseen else []
    for mention in re.finditer(r"(?i)hookspath", "\n".join(texts)):
        line = mention.string[mention.string.rfind("\n", 0, mention.start()) + 1:].split("\n", 1)[0]
        m = re.fullmatch(r"(?i)\s*hookspath\s*=\s*([^\s\"\\;#$%]+)\s*", line)
        where = m and Path(m.group(1)).expanduser()
        out.append(str(where) if where and where.is_absolute() else None)
    return out


def task_spec(top: Path, gitdir: Path, main: Path) -> dict:
    """The hub's spec for the branch checked out here, when it names this checkout; `{}` otherwise."""
    try:
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    branch = head.removeprefix("ref: refs/heads/") if head.startswith("ref: ") else ""
    sid = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]+", "-", branch.lower())).strip("-")[:64].rstrip("-")
    try:
        spec = json.loads((HERE.parent / "raw/specs" / main.name / f"{sid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    worktree = spec.get("worktree") if isinstance(spec, dict) else None
    return spec if worktree and Path(worktree).resolve() == top else {}


def plan_root(top: Path, spec: dict) -> Path | None:
    """A plan task's artifact root, from the hub's spec for this branch; `None` for any other task."""
    root = str(spec.get("artifact_root") or "").strip("/")
    if spec.get("review_profile") != "plan" or not root:
        return None
    # A root that would leave the checkout narrows nothing: the checkout stays the bound.
    return top / root if contained(str(top / root), [str(top)]) else top


def grant(cwd: Path, payload: dict) -> Grant | None:
    """What this task may do, computed for this call; `None` where no repository is attached."""
    from guard_models import host_of, spawn_of

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

    spec = task_spec(top, gitdir, main)
    roots = [plan_root(top, spec) or top, *temps()]
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
    # A path entry names that file (`.venv/bin/python`), keyed the way the judge keys a path-named command.
    executables |= {e[1] for s in strings("executables") for e in spawn_of(s, ctx) if e[0] == "spawn"}
    remotes = remote_hosts(common)
    hosts = {"127.0.0.1"} | {h for _, h in remotes if h != UNREAD} | {host_of(s) for s in strings("network_hosts")}
    protected = sorted({str(p / x) for p in (top, main) for x in PROTECTED})
    writable = [h or top.as_posix() for h in hook_folders(common) if h is None
                or contained(h, [str(r) for r in roots]) and not any(contained(h, [p]) for p in protected)]
    return Grant([str(r) for r in roots], protected, executables, hosts, remotes, top.as_posix(),
                 writable[0] if writable else "")


def protected_below(target: str, roots: list) -> bool:
    """`target` is, or lies in, a protected name at any depth below a write root that holds it."""
    where = real_path(target)
    for root in roots:
        if contained(target, [root]):
            parts = re.split(r"[\\/]", os.path.relpath(where, real_path(root)).lower())
            if {".claude", ".codex", "verification.toml"} & set(parts) \
                    or any(parts[i:i + 2] == [".wiki", "adapter.toml"] for i in range(len(parts))):
                return True
    return False


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
    if kind == "repo" and g.hooks:
        return (f"git 이 작업이 고칠 수 있는 훅 폴더({g.hooks})의 훅을 실행한다 — `core.hooksPath` 를 쓰기 루트 "
                "밖으로 옮기기 전에는 어떤 허가로도 열리지 않으니, 사람이 직접 실행한다")
    # Any path effect on a directory that holds a protected path may reach it: a destination directory, a
    # URL- or server-named download, a patch or an extraction writes names the command does not show, and a
    # write to an existing directory either lands under it or fails. Only git's own write of the repository
    # it runs in (`repo`) is granted on the checkout as a whole.
    reaches_protected = kind != "repo" and any(contained(p, [target]) for p in g.protected)
    # A `.git` anywhere makes a repository whose configuration git would trust: no write lays one down. And a
    # protected name below any write root is another worktree's copy, which a commit there would carry home.
    git_dir = kind != "repo" and (".git" in re.split(r"[\\/]", target.lower()) or protected_below(target, g.roots))
    if any(contained(target, [p]) for p in g.protected) or reaches_protected or git_dir:
        return "허가를 정하는 파일이다 — 어떤 `[guard]` 항목으로도 열리지 않고, 사람이 직접 고친다"
    if contained(target, g.roots):
        return None
    return "쓰기 루트 밖이다 — `.wiki/adapter.toml` 의 `[guard] write_roots` 에 이 경로를 담은 디렉터리를 더하면 열린다"
