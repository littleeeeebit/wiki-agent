"""What a task may do, computed once per tool call, and why an effect falls outside it — for `guard`.

The grant is read off files, never a model: the checkout's root (or a plan
task's artifact root), the temporary directories and the session's memory
folder as write roots, and the git remotes' hosts plus the loopback the
search daemon answers on.

The guard is open by default: it denies only what it can read as leaving
the grant — a write or delete outside the write roots, a protected file, a
push to a host no remote names. A program, option or form it cannot read
passes; the agent is not an adversary, and a closed table that opens one
row at a time cost more than the accidents it caught.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from common import layout  # noqa: F401 -- `guard_git.common_dir` reads it from here
from guard import HERE, contained, real_path


@dataclass
class Grant:
    roots: list
    protected: list
    hosts: set
    remotes: tuple
    top: str = ""                       # the checkout the remotes were read from
    hooks: str = ""                     # a hooks folder git is configured to run from inside a write root
    adapter: str = ""                   # this checkout's adapter, when the task fills its slots


# Inside a write root, but they decide the grant or what runs next: never granted.
PROTECTED = (".git", ".claude", ".codex", ".wiki/adapter.toml", "verification.toml")


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


# A destination git reaches that is not a network host the guard can name: a local path, a `file://` URL,
# or one a URL rewrite may change. A push to it is `top`, and it is never granted as a host.
UNREAD = "\0unread"
# A remote name marking a configuration that may make any git call fetch (a partial clone) or append to a
# file (a Trace2 target), or a file the reader does not open that may say so.
LAZY = "\0lazy"


def git_configs() -> list:
    """The text of every global and system configuration file git reads besides the repository's own."""
    env = os.environ
    # Git on Windows takes `HOME`, then `HOMEDRIVE`+`HOMEPATH`, then `USERPROFILE`; Python's home skips `HOME`.
    # Every candidate is read, since one more file only makes the reader stricter.
    homes = {Path.home()} | {Path(h) for h in (env.get("HOME"), env.get("USERPROFILE"),
                                               env.get("HOMEDRIVE", "") + env.get("HOMEPATH", "")) if h}
    places = [p for home in homes for p in (home / ".gitconfig", home / ".config/git/config")]
    places += [Path("/etc/gitconfig"), Path(env.get("PROGRAMDATA") or "C:/ProgramData") / "Git/config"]
    places += [Path(env["XDG_CONFIG_HOME"]) / "git/config"] if env.get("XDG_CONFIG_HOME") else []
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
    # The id is the branch folded to a file name, so `fix/slots` finds `fix-slots`: a recorded branch must match.
    named = spec.get("branch") if isinstance(spec, dict) else None
    return spec if worktree and Path(worktree).resolve() == top and named in (None, branch) else {}


def plan_root(top: Path, spec: dict) -> Path | None:
    """A plan task's artifact root, from the hub's spec for this branch; `None` for any other task."""
    root = str(spec.get("artifact_root") or "").strip("/")
    if spec.get("review_profile") != "plan" or not root:
        return None
    # A root that would leave the checkout narrows nothing: the checkout stays the bound.
    return top / root if contained(str(top / root), [str(top)]) else top


def grant(cwd: Path, payload: dict) -> Grant | None:
    """What this task may do, computed for this call; `None` where no repository is attached."""

    found = layout(cwd.resolve())
    if found is None:
        return None
    top, gitdir, common, main = found
    adapter = next((p / ".wiki/adapter.toml" for p in (main, top) if (p / ".wiki/adapter.toml").is_file()), None)
    if adapter is None:
        return None
    spec = task_spec(top, gitdir, main)
    roots = [plan_root(top, spec) or top, *temps()]
    if payload.get("transcript_path"):
        roots.append(Path(str(payload["transcript_path"])).parent / "memory")
    remotes = remote_hosts(common)
    hosts = {"127.0.0.1"} | {h for _, h in remotes if h != UNREAD}
    # The git directories themselves, whatever they are named: a bare common directory (`shared.git`) holds
    # the configuration and hooks git trusts, with no `.git` in its path.
    protected = sorted({str(p / x) for p in (top, main) for x in PROTECTED} | {str(gitdir), str(common)})
    writable = [h or top.as_posix() for h in hook_folders(common) if h is None
                or contained(h, [str(r) for r in roots]) and not any(contained(h, [p]) for p in protected)]
    # The survey and a spec in a repository with no `gate_cmd` exist to fill the slots: the hub's spec
    # (never a model) opens this checkout's adapter to them, and review and [Merge] judge what they wrote.
    fills = str(top / ".wiki/adapter.toml") if spec.get("adapter") is True else ""
    return Grant([str(r) for r in roots], protected, hosts, remotes, top.as_posix(),
                 writable[0] if writable else "", fills)


def protected_below(target: str, roots: list) -> bool:
    """`target` is, or lies in, a protected name at any depth below a write root that holds it. A folder
    named `.wiki` counts too: a folder moved or copied onto it would put its `adapter.toml` there."""
    where = real_path(target)
    for root in roots:
        if contained(target, [root]):
            parts = [p.rstrip(". ") or p for p in re.split(r"[\\/]", os.path.relpath(where, real_path(root)).lower())]
            if {".git", ".claude", ".codex", "verification.toml"} & set(parts) or parts[-1] == ".wiki" \
                    or any(parts[i:i + 2] == [".wiki", "adapter.toml"] for i in range(len(parts))):
                return True
    return False


# The most entries a delete's folder is looked through for protected names; a larger one is denied.
WALK = 20000


def holds_protected(target: str, copies: bool = False) -> bool:
    """A delete of `target` may reach a protected name below it: one is there, a link or junction is (its
    contents are another folder's, which some deleters follow), or the folder is too large to look through.
    With `copies`, `target` is a linked worktree: its own `.git` file and its copies of the other names go
    with it, and only a link, a junction or a repository below it counts."""
    if os.path.islink(target) or os.path.isjunction(target):
        return True
    seen, stack = 0, [top := real_path(target)]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            return True
        for e in entries:
            seen += 1
            name = e.name.lower()
            if seen > WALK or e.is_symlink() or e.is_junction() or name == ".git" and not (copies and folder == top) \
                    or not copies and (name in (".claude", ".codex", "verification.toml")
                                       or name == "adapter.toml" and os.path.basename(folder).lower() == ".wiki"):
                return True
            if e.is_dir(follow_symlinks=False):
                stack.append(e.path)
    return False


def plain(p: str) -> str:
    """`p` absolute, `..` folded, in the platform's case; links left as they are."""
    return os.path.normcase(os.path.abspath(p))


def single(p: str) -> bool:
    """`p` is absent or has one name: a hard link shares its contents with another file, `.git/config` say."""
    try:
        return os.stat(p, follow_symlinks=False).st_nlink <= 1
    except FileNotFoundError:
        return True
    except OSError:
        return False


def outside(effect: tuple, g: Grant) -> str | None:
    """Why `effect` leaves the grant, naming what would allow it; `None` when it is inside."""
    kind, target = effect
    # Open by default: a form the guard cannot read, and any program it starts, pass.
    if kind in ("read", "top", "spawn"):
        return None
    if kind == "network":
        return None if target in g.hosts else \
            f"원격 저장소가 아닌 호스트({target})로 보낸다 — `git remote add` 로 원격을 먼저 더하면 열린다"
    if kind == "repo" and g.hooks:
        return (f"git 이 작업이 고칠 수 있는 훅 폴더({g.hooks})의 훅을 실행한다 — `core.hooksPath` 를 쓰기 루트 "
                "밖으로 옮기기 전에는 어떤 허가로도 열리지 않으니, 사람이 직접 실행한다")
    # Any path effect on a directory that holds a protected path may reach it: a destination directory, a
    # URL- or server-named download, a patch or an extraction writes names the command does not show, and a
    # write to an existing directory either lands under it or fails. Only git's own write of the repository
    # it runs in (`repo`) is granted on the checkout as a whole.
    if g.adapter and kind == "write" and plain(target) == plain(g.adapter) == real_path(g.adapter) \
            and single(g.adapter) and contained(target, g.roots):
        return None   # the adapter itself, reached through no link: a link there would carry the write elsewhere
    reaches_protected = kind != "repo" and any(contained(p, [target]) for p in g.protected)
    # A `.git` anywhere makes a repository whose configuration git would trust: no write lays one down. And a
    # protected name below any write root is another worktree's copy, which a commit there would carry home.
    # Windows drops a name's trailing dots and spaces, so `.git.` makes `.git`.
    names = {p.rstrip(". ") or p for p in re.split(r"[\\/]", target.lower())}
    git_dir = kind != "repo" and (".git" in names or protected_below(target, g.roots))
    # A delete reaches whatever lies below it, another worktree's protected copy included.
    # `git worktree remove` of a linked worktree takes that checkout's copies with it; a link inside could
    # carry git's delete into another folder.
    holds = kind in ("delete", "worktree") and holds_protected(target, copies=kind == "worktree")
    if any(contained(target, [p]) for p in g.protected) or reaches_protected or git_dir:
        return "허가를 정하는 파일이다 — 어떤 명령으로도 열리지 않고, 사람이 직접 고친다"
    if holds:
        return ("지울 폴더 안에 허가를 정하는 파일, 링크나 접합점이 있다 — 사람이 직접 지운다. 이 저장소의 연결된 "
                "worktree 는 안의 링크를 푼 뒤 `git worktree remove` 로 지울 수 있다")
    if contained(target, g.roots):
        return None
    return "쓰기 루트 밖이다 — 체크아웃, 임시 폴더, 세션 메모리 폴더 안에서만 쓰고 지운다"
