"""apply — attach the wiki to a target repository.

Everything it prints is read by whoever is installing, so those strings are
Korean.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from markdown_emphasis import recovery  # noqa: E402
from wiki import WIKI, adapter_path, front_matter, slots_for  # noqa: E402

# The distribution name and the import name from `requirements-hooks.txt`.
# You cannot ask whether a pip name is installed, so the pair is needed. A
# test catches this table drifting from that file, which is the only thing
# that makes writing it out twice by hand bearable.
NEEDED = {"PyYAML": "yaml", "markdown-it-py": "markdown_it"}

# The rest of what the hooks' interpreter has to have — things that come with
# the version rather than from pip. One version left `tomllib` out: moving the
# check to a package list took the stdlib condition with it, and a 3.10
# interpreter passed as a wiring target. "Everything the hooks use" reaches
# the stdlib and the version floor.
FLOOR = (3, 11)
BUILTIN = {"tomllib": "tomllib"}

HOOK_MARK = "inject.py"
SESSION_MARK = "session_state.py"
SYNC_MARK = "sync.py"
CONTINUATION_MARK = "declared_continuation.py"
# Every script this wiki wires by name, besides the pages' `enforce.pretooluse`.
OWNED = (HOOK_MARK, SESSION_MARK, SYNC_MARK, CONTINUATION_MARK, "keepalive.py",
         "codex_pretool.py", "host_boundary.py", "deny.py")

# Recognize the retired bridge by its executable/script, never by an
# account-home path: CODEX_HOME still holds the selected CLI login.
TRANSPORT = re.compile(r"[\\/]\.orca[\\/]agent-hooks[\\/]|"
                       r"(?:^|[\s\"'])orca(?:\.exe|\.cmd|\.ps1)?(?:[\s\"']|$)|"
                       r"ORCA_AGENT_HOOK_(?:ENDPOINT|PORT)", re.I)

# The quoted arguments of a hook command. Our own writer emits
# `"<python>" "<wiki>/tool/<script>"`, optionally behind `& ` for PowerShell
# and followed by flags, so the script is always the second quoted argument.
ARGS = re.compile(r'"([^"]*)"')


def script_arg(command: str) -> str | None:
    """The argument our writer puts the script in, or `None`.

    The position is the point. Scanning every quoted argument answers "does
    this command mention the path" and a command can mention it as data —
    `"python" "audit.py" --watch "<wiki>/tool/inject.py"` is somebody else's
    hook watching our file, and reading it as ours overwrote their hook.
    """

    args = ARGS.findall(command)
    if len(args) < 2:
        return None
    # `"<python>" "<wiki>/tool/hook.py" <host> <script>` — the user-level form.
    # The script it dispatches to is the one that counts, and it sits next to
    # the dispatcher.
    if dispatches(command):
        words = command.split(f'"{args[1]}"', 1)[1].split()
        return str(Path(args[1]).parent / words[1]) if len(words) >= 2 else None
    return args[1]


def dispatches(command: str) -> bool:
    """Is this the user-level form, `"<python>" "<...>/hook.py" <host> <script>`?"""

    args = ARGS.findall(command)
    return len(args) >= 2 and Path(args[1].replace("\\", "/")).name == "hook.py"


def runs(command: str, script: str) -> bool:
    """Does this command run *this wiki's* copy of `script`?

    Asked of the quoted arguments, as a path, against the one directory that
    can answer it — `HERE`. Three review rounds went to weaker answers, each
    one a shape that looked like the path instead of being it: the bare
    filename matched `--watch korean_progress.py`, `tool/<script>` matched
    `custom-tool/<script>`, and comparing the parent component matched a
    person's own `C:/project/tool/<script>`. Only this wiki's own directory
    tells those apart, and it is known here.

    A relative path is never ours. This writer only ever emits an absolute
    one, so `"python" "tool/inject.py"` in a project's settings was written by
    someone else and is run relative to wherever the host starts the hook.
    Resolving it here would measure it against this process's working
    directory instead — and from the hub root that lands on `HERE`, which made
    the answer depend on where `apply.py` happened to be run from.

    There is no guess for a path that is simply missing. An earlier draft
    claimed a dangling `<...>/tool/<script>` as this wiki's old location, and
    a person's hook on a mapped drive that is offline for a minute is the same
    string. Nothing in the command separates those two, so nothing here tries;
    `stale` reports them instead and the person decides.
    """

    arg = script_arg(command)
    if arg is None:
        return False
    where = Path(arg.replace("\\", "/"))
    if where.name != script or not where.is_absolute():
        return False
    try:
        return where.resolve() == (HERE / script).resolve()
    except OSError:
        return False


def stale(settings: dict) -> list[str]:
    """Hooks naming one of this wiki's scripts at a path that is not there.

    Never removed, only said out loud. Moving the wiki leaves exactly this
    behind: the old entry names a file that is gone, the host runs it on every
    tool call, python exits 2, and the session stops being able to do
    anything. That happened here once. But the same line is also a colleague's
    hook on a share that is temporarily unreachable, and deleting that is
    destroying their settings over a network blip. Which one it is cannot be
    read off the string, so it is handed to the person who can tell.
    """

    owned = {*OWNED, *declared()[1]}
    found: list[str] = []
    for groups in (settings.get("hooks") or {}).values():
        for group in groups:
            for entry in group.get("hooks", []):
                arg = script_arg(str(entry.get("command", "")))
                if arg is None:
                    continue
                where = Path(arg.replace("\\", "/"))
                if where.name not in owned or not where.is_absolute():
                    continue
                try:
                    if where.exists() or where.resolve() == (HERE / where.name).resolve():
                        continue
                except OSError:
                    continue
                found.append(
                    f"훅이 없는 파일을 가리킨다: {where.as_posix()}. "
                    "이 위키를 옮겼다면 그 항목을 지워라. 남의 훅이면 그대로 둬라"
                )
    return found


def declared() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """The deny rules and PreToolUse scripts the pages declare.

    Each comes with the page it came from. Enforcement that cannot name the
    rule behind it is enforcement that survives the rule being deleted.
    """

    denies: dict[str, list[str]] = {}
    scripts: dict[str, list[str]] = {}
    for scope in ("operator", "craft"):
        directory = WIKI / scope
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            enforce = _meta(path).get("enforce")
            if not isinstance(enforce, dict):
                continue
            page = f"{scope}/{path.stem}"
            for rule in enforce.get("deny") or []:
                denies.setdefault(str(rule), []).append(page)
            script = enforce.get("pretooluse")
            if script:
                scripts.setdefault(str(script), []).append(page)
    return denies, scripts


# One `apply` pass asks `declared` dozens of times; parsing every page's YAML
# each time was most of `--check`. A page is parsed again once it changes.
_parsed: dict[Path, tuple[int, int, dict]] = {}


def _meta(path: Path) -> dict:
    stat = path.stat()
    seen = _parsed.get(path)
    if seen is None or seen[:2] != (stat.st_mtime_ns, stat.st_size):
        seen = (stat.st_mtime_ns, stat.st_size, front_matter(path.read_text(encoding="utf-8"))[0])
        _parsed[path] = seen
    return seen[2]


def unfilled(adapter: str | None, project: Path | None = None) -> dict[str, list[str]]:
    """Slots left unfilled, counting only the pages that will be injected."""

    values = slots_for(adapter, project)
    missing: dict[str, list[str]] = {}
    for scope in ("operator", "craft"):
        directory = WIKI / scope
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            meta, body = front_matter(path.read_text(encoding="utf-8"))
            if str(meta.get("severity")) not in ("landmine", "contract"):
                continue
            for name in sorted(set(meta.get("slots") or [])):
                if name not in values:
                    missing.setdefault(name, []).append(f"{scope}/{path.stem}")
    return missing


def hook_entry(python: str, adapter: str | None, project: str = "", host: str = "") -> dict:
    where = (f' --project "{project}"' if project else "") + (f" --host {host}" if host else "")
    selection = f' --adapter "{adapter}"' if adapter and not (
        project and (Path(project) / ".wiki/adapter.toml").exists()
    ) else ""
    return {
        "hooks": [
            {
                "type": "command",
                "command": (
                    f'"{python}" "{(HERE / "inject.py").as_posix()}"'
                    f"{selection}{where}"
                ),
                # Was 10. `inject.py` attaches an English rendering of the
                # utterance, which brought a Gemini round trip in — measured
                # at 1.2 seconds. `inject.py` hands the translator its own
                # 8-second deadline (`BUDGET`), so even at its worst the hook
                # finishes inside this one.
                "timeout": 15,
                "statusMessage": "위키 확인",
            }
        ]
    }


def session_entry(python: str, project: str) -> dict:
    return {
        "hooks": [
            {
                "type": "command",
                "command": (
                    f'"{python}" "{(HERE / "session_state.py").as_posix()}"'
                    + (f' --project "{project}"' if project else "")
                ),
                # Was 15. `report()` translates decisions, plans and document
                # titles in one batch. Even batched, the first session is the
                # slowest, because the cache is empty.
                "timeout": 25,
                "statusMessage": "위키: 현재 상태",
            }
        ]
    }


def sync_entry(python: str, project: str) -> dict:
    """The hook that brings the wiki level with the repository, on Stop."""

    return {
        "hooks": [
            {
                "type": "command",
                "command": (
                    f'"{python}" "{(HERE / "sync.py").as_posix()}"'
                    + (f' --project "{project}"' if project else "") + " --quiet"
                ),
                "timeout": 30,
                "statusMessage": "위키 갱신",
            }
        ]
    }


def continuation_entry(python: str) -> dict:
    """The hook that reverts a turn which promised to continue and called nothing.

    It takes no `--project`. All the judgement needs is the transcript path
    and the hook receives that on stdin, so passing a path as an argument
    would leave two places stating the same thing.
    """

    return {
        "hooks": [
            {
                "type": "command",
                "command": (
                    f'"{python}" "{(HERE / "declared_continuation.py").as_posix()}"'
                ),
                "timeout": 10,
                "statusMessage": "이어서 한다고 적었나",
            }
        ]
    }


def script_entry(python: str, script: str, status: str) -> dict:
    return {
        "hooks": [
            {
                "type": "command",
                "command": f'"{python}" "{(HERE / script).as_posix()}"',
                "timeout": 10,
                "statusMessage": status,
            }
        ]
    }


def put_hook(settings: dict, event: str, script: str, entry: dict) -> list[str]:
    """Attach one hook to one event, updating the command if it is already there.

    `runs` decides what is ours, not the name. Matching by name cannot tell
    our hook from one the person attached, and then it overwrites theirs.
    """

    groups = settings.setdefault("hooks", {}).setdefault(event, [])
    for group in groups:
        for existing in group.get("hooks", []):
            if runs(str(existing.get("command", "")), script):
                wanted = entry["hooks"][0]
                if any(existing.get(k) != v for k, v in wanted.items() if k != "statusMessage"):
                    existing.update(wanted)
                    return [f"{event} 훅 명령 갱신: tool/{script}"]
                return []
    groups.append(entry)
    return [f"{event} 훅 추가: tool/{script}"]


# Hook scripts that were renamed: old name on the left, current one on the right.
#
# `put_hook` only adds and updates. So when a page renames its script the old
# entry stays in `settings.json`, and the moment the script is deleted the hook
# points at a file that is not there — which fails every tool call with "can't
# open file". That is not a hypothetical; it happened while writing this.
#
# Only what this table names gets removed.
RETIRED = {"korean_progress.py": "english_progress.py"}


def retire(settings: dict, gone: str, instead: str) -> list[str]:
    """Drop an owned entry, but only once its replacement is already wired.

    Removing the old one first would leave enforcement quietly off in between.
    Running twice has to give the same answer, so an entry already gone is not
    an error and not a change.
    """

    groups = settings.get("hooks", {}).get("PreToolUse", [])
    has_new = any(
        runs(str(h.get("command", "")), instead)
        for group in groups
        for h in group.get("hooks", [])
    )
    if not has_new:
        return []

    changes: list[str] = []
    for group in list(groups):
        kept = [h for h in group.get("hooks", []) if not runs(str(h.get("command", "")), gone)]
        if len(kept) != len(group.get("hooks", [])):
            changes.append(f"PreToolUse 옛 훅 제거: {gone} → {instead}")
            group["hooks"] = kept
        if not group.get("hooks"):
            groups.remove(group)
    return changes


def merge(
    settings: dict,
    denies: list[str],
    hook: dict,
    scripts: dict[str, dict] | None = None,
    session: dict | None = None,
    sync: dict | None = None,
    continuation: dict | None = None,
) -> list[str]:
    """Merge into the settings and return what was added. Existing values stand."""

    changes: list[str] = []

    permissions = settings.setdefault("permissions", {})
    existing = permissions.setdefault("deny", [])
    for rule in denies:
        if rule not in existing:
            existing.append(rule)
            changes.append(f"deny 추가: {rule}")

    changes += put_hook(settings, "UserPromptSubmit", HOOK_MARK, hook)
    for script, entry in sorted((scripts or {}).items()):
        changes += put_hook(settings, "PreToolUse", script, entry)
    if session:
        changes += put_hook(settings, "SessionStart", SESSION_MARK, session)
    if sync:
        changes += put_hook(settings, "Stop", SYNC_MARK, sync)
    if continuation:
        changes += put_hook(settings, "Stop", CONTINUATION_MARK, continuation)
    # Wire the new one first, retire the old one after. The other order leaves
    # a window with no enforcement at all.
    for gone, instead in RETIRED.items():
        changes += retire(settings, gone, instead)
    return changes


def dispatched(entry: dict, agent: str) -> dict:
    """The same hook, called through `hook.py` so it works out the project itself."""

    hook = entry["hooks"][0]
    script = script_arg(hook["command"])
    name = Path(script).name
    hook["command"] = hook["command"].replace(
        f'"{(HERE / name).as_posix()}"', f'"{(HERE / "hook.py").as_posix()}" {agent} {name}', 1)
    return entry


def configure(settings: dict, project: Path | None, adapter: str | None, python: str, agent: str) -> list[str]:
    """One wiring definition, shared by the install and the drift check.

    `project=None` is the user-level install: no project in any command, every
    hook going through `hook.py`.
    """
    where = project.as_posix() if project else ""
    # The dispatcher supplies the host for global hooks; a per-project
    # command supplies it directly for the host-specific output ceiling.
    host = agent if project else ""
    wrap = (lambda entry: dispatched(entry, agent)) if project is None else (lambda entry: entry)
    denies, scripts = declared()
    if agent == "codex":
        entries = [
            ("UserPromptSubmit", HOOK_MARK, hook_entry(python, adapter, where)),
            ("SessionStart", SESSION_MARK, session_entry(python, where)),
            ("PreToolUse", "codex_pretool.py",
             script_entry(python, "codex_pretool.py", "위키: 도구 실행 검사")),
            ("Stop", SYNC_MARK, sync_entry(python, where)),
            ("Stop", CONTINUATION_MARK, continuation_entry(python)),
        ]
        changes = []
        for event, mark, entry in entries:
            wrap(entry)
            if sys.platform == "win32":
                # Codex runs this through PowerShell, where a quoted path
                # without `&` in front of it is just a string.
                entry["hooks"][0]["command"] = "& " + entry["hooks"][0]["command"]
            if mark == CONTINUATION_MARK:
                entry["hooks"][0]["command"] += " --codex"
            if event in ("SessionStart", "UserPromptSubmit"):
                # A measured injection reaches about 20,000 characters. At the
                # default of 2,500 tokens a rule turns into a file preview, so
                # this is given room without being let off the leash.
                entry["hooks"][0]["additionalContextLimit"] = 12000
            changes += put_hook(settings, event, mark, entry)
    else:
        pre = {s: wrap(script_entry(python, s, "진행 설명 확인")) for s in scripts}
        if project is None:
            # `permissions.deny` in the user settings would bind every
            # repository on the machine. `deny.py` behind the dispatcher
            # binds only the attached ones.
            pre["deny.py"] = wrap(script_entry(python, "deny.py", "위키: 차단 규칙"))
        changes = merge(
            settings,
            list(denies) if project else [],
            wrap(hook_entry(python, adapter, where, host)),
            pre,
            wrap(session_entry(python, where)),
            wrap(sync_entry(python, where)),
            wrap(continuation_entry(python)),
        )
    # Native wiring and terminal transport removal are applied together so
    # an upgrade cannot leave a missing script in a host event.
    for event, groups in settings.get("hooks", {}).items():
        for group in list(groups):
            before = group.get("hooks", [])
            kept = [h for h in before if not runs(str(h.get("command", "")), "keepalive.py")
                    and not any(TRANSPORT.search(str(h.get(key, "")))
                                for key in ("command", "commandWindows", "command_windows"))]
            if len(kept) != len(before):
                group["hooks"] = kept
                changes.append(f"{event} 옛 데스크톱 전송 훅 제거")
                if not kept:
                    groups.remove(group)
    return changes


def installed_agents(project: Path) -> list[str] | None:
    """This machine's choice. What was installed survives the settings being wiped."""
    path = project / ".wiki/installed-agents.json"
    if not path.exists():
        return None
    agents = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(agents, list) or not agents or any(a not in ("claude", "codex") for a in agents):
        raise ValueError(f"설치 호스트 목록이 잘못됐다: {path}")
    return agents


# The hosts' user-level settings. `setup_agents --global` writes here, and
# every checkout on the machine — a fresh worktree included — reads them.
# `WIKI_USER_HOME` stands in for the home directory, so a test never reads —
# or is judged against — the machine's real install.
_HOME = os.environ.get("WIKI_USER_HOME")


def user_files(agent: str) -> list[Path]:
    """Every user-level settings file the host may start from on this machine.

    Claude uses its default settings. Codex uses the default home and an
    explicitly selected `CODEX_HOME`; no other application's accounts are scanned.
    """

    home = Path(_HOME or Path.home())
    if agent == "claude":
        return [home / ".claude/settings.json"]
    homes = [home / ".codex"]
    if not _HOME:
        if os.environ.get("CODEX_HOME"):
            homes.append(Path(os.environ["CODEX_HOME"]))
    seen: dict[str, Path] = {}
    for path in homes:
        if path.is_dir() or path == home / ".codex":
            seen.setdefault(os.path.normcase(str(path.resolve())), path / "hooks.json")
    return list(seen.values())


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def drift(settings: dict, project: Path | None, agent: str) -> list[str]:
    """What installing would change in these settings, plus what it cannot fix."""

    settings = json.loads(json.dumps(settings))
    # Only our own hooks. If somebody else's command becomes `commands[0]`,
    # the first quoted token in it is read as "the installed interpreter", and
    # perfectly sound wiring reports drift — which is what then fails the gate.
    commands = [h.get("command", "") for g in settings.get("hooks", {}).get("UserPromptSubmit", [])
                for h in g.get("hooks", []) if runs(h.get("command", ""), HOOK_MARK)]
    # The executable is a per-machine value. Keeping the installed interpreter
    # avoids reporting a path difference as drift.
    quoted = re.match(r'(?:&\s*)?"([^"]+)"', commands[0]) if commands else None
    python = quoted[1] if quoted else sys.executable
    adapter_match = re.search(r'--adapter\s+(?:"([^"]+)"|(\S+))', commands[0]) if commands else None
    adapter = (adapter_match[1] or adapter_match[2]) if adapter_match else (project.name if project else None)
    changes = configure(settings, project, adapter, python, agent)
    return changes + restricted(settings) + stale(settings)


def restricted(settings: dict) -> list[str]:
    """This wiki's hooks sitting in a group whose `matcher` narrows them.

    Installing cannot repair it — `put_hook` finds the entry and leaves the
    group alone — and a `matcher: "Read"` quietly keeps a Bash block from ever
    being asked.
    """

    # A hook is ours when it runs one of our scripts, in either form — not
    # when somebody's `--watch "<wiki>/tool/hook.py"` names the path.
    owned = {*OWNED, *declared()[1]}
    mine = lambda c: ours(c) or any(runs(c, s) for s in owned)  # noqa: E731
    return [f"{event} 위키 훅에 제한 matcher가 있다"
            for event, groups in (settings.get("hooks") or {}).items()
            for group in groups
            if group.get("matcher") not in (None, "", "*") and any(
                mine(str(h.get("command", ""))) for h in group.get("hooks", []))]


def installed(agent: str, python: str) -> set[tuple[str, str]]:
    """`(event, command)` for exactly the user-level hooks this install writes.

    What trust is granted against. A shape, however tight, also admits
    `"C:/other.exe" "<wiki>/tool/hook.py" …` and `hook.py codex setup_agents.py`
    — commands this installer never wrote. Equality with its own output does not.
    """

    wired: dict = {}
    configure(wired, None, None, python, agent)
    return {(event, h["command"]) for event, groups in wired["hooks"].items()
            for g in groups for h in g["hooks"]}


def ours(command: str) -> bool:
    """Does this command, in full, have the shape our writer gives the dispatcher?

    For recognising our hooks inside settings (`restricted`), whatever
    interpreter they were installed with. It is a shape, not an identity —
    trust is granted against `installed()` instead. Asking about pieces of the
    command let `echo "python" "<wiki>/tool/hook.py"; <anything>` through, so
    the whole of it has to match: an optional `& `, a quoted interpreter with
    nothing a shell expands inside, the dispatcher, a host, one of this
    wiki's scripts, and bare flags. Nothing else may follow.
    """

    shape = re.fullmatch(
        r'(?:& )?"[^"$`]+" "([^"]+)" (?:claude|codex) ([\w.]+\.py)(?: --[a-z-]+)*', command)
    if not shape or not (HERE / shape[2]).is_file():
        return False
    try:
        return Path(shape[1]).resolve() == (HERE / "hook.py").resolve()
    except OSError:
        return False


def user_wired(agent: str) -> bool:
    """Is this wiki fully attached in every user-level file of this host?"""

    try:
        return all(not drift(read_json(path), None, agent) for path in user_files(agent))
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return False




def keep_denies(settings: dict) -> list[str]:
    """Put the pages' deny rules into a checkout's own `permissions.deny`.

    The user-level install judges them in `deny.py`, which passes when the
    hook fails or times out. The host enforces `permissions.deny` itself, so
    the checkouts named at install keep it; worktrees fall back to `deny.py`.
    Only added, never removed — a rule a person wrote there stays.
    """

    existing = settings.setdefault("permissions", {}).setdefault("deny", [])
    missing = [rule for rule in declared()[0] if rule not in existing]
    existing.extend(missing)
    return [f"deny 추가: {rule}" for rule in missing]


def unwire(settings: dict) -> list[str]:
    """Drop this wiki's per-project hooks, for a machine that moved to the user level.

    Only commands that run this wiki's own scripts directly. The dispatcher's
    entries, and everybody else's, stay.
    """

    owned = {*OWNED, *declared()[1]}
    changes: list[str] = []
    for event, groups in (settings.get("hooks") or {}).items():
        for group in list(groups):
            kept = [h for h in group.get("hooks", [])
                    if dispatches(str(h.get("command", "")))
                    or not any(runs(str(h.get("command", "")), s) for s in owned)]
            if len(kept) != len(group.get("hooks", [])):
                changes.append(f"{event} 프로젝트 훅 제거")
                group["hooks"] = kept
            if not group.get("hooks"):
                groups.remove(group)
    return changes


def wiring_drift(project: Path, agents: tuple[str, ...] | None = None) -> list[tuple[str, str]]:
    """Read-only. The adapter's expected agents, or for an unregistered project,
    whichever agents are installed.

    A host wired at the user level is judged there, and a per-project install
    left beside it is the drift: the dispatcher steps aside for it, so the
    checkout keeps its old absolute paths.
    """
    project = project.resolve()
    paths = {"claude": project / ".claude/settings.json", "codex": project / ".codex/hooks.json"}
    if agents is None:
        try:
            source = adapter_path(project.name, project, wiki=WIKI)
            declared_agents = tomllib.loads(source.read_text(encoding="utf-8")).get("agents", []) if source and source.exists() else []
            if not isinstance(declared_agents, list) or any(a not in paths for a in declared_agents):
                raise ValueError("알 수 없는 adapter agents")
            declared_agents = installed_agents(project) or declared_agents
        except (OSError, ValueError, TypeError):
            return [("훅 배선 드리프트", "adapter 또는 설치 호스트 목록을 읽을 수 없다")]
        agents = tuple(declared_agents) or tuple(agent for agent, path in paths.items() if path.exists())
    findings = []
    for agent in agents:
        try:
            settings = read_json(paths[agent])
            if user_wired(agent):
                copy = json.loads(json.dumps(settings))
                changes = [f"{c} — 전역 설치가 있다. `setup_agents.py --global --project` 로 고쳐라"
                           for c in unwire(copy) + (keep_denies(copy) if agent == "claude" else [])]
            else:
                changes = drift(settings, project, agent)
            findings.extend(("훅 배선 드리프트", f"{agent}: {change}") for change in changes)
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            findings.append(("훅 배선 드리프트", f"{agent}: 설정을 읽을 수 없다"))
    return findings


def unusable(python: str) -> list[str]:
    """What the hooks' interpreter is missing. Empty means it can be wired.

    Looks at whatever `--python` points at rather than at this process. The
    interpreter running the install and the one the hooks will run under can
    differ, and it is the second one that dies quietly.
    """

    names = {f"Python {FLOOR[0]}.{FLOOR[1]} 이상": None, **BUILTIN, **NEEDED}
    script = (
        "import sys\n"
        "bad = []\n"
        f"if sys.version_info < {FLOOR}:\n"
        f"    bad.append({next(iter(names))!r})\n"
    )
    for name, module in {**BUILTIN, **NEEDED}.items():
        script += (f"try:\n    import {module}\n"
                   f"except Exception:\n    bad.append({name!r})\n")
    script += "print('\\n'.join(bad))\n"

    try:
        done = subprocess.run([python, "-c", script], capture_output=True,
                              encoding="utf-8", errors="replace")
    except OSError as error:
        # A path that is missing entirely, or cannot be executed, is still
        # this function's question to answer. Raising here hands the person a
        # traceback where an explanation belongs.
        return [f"실행할 수 없다 ({type(error).__name__})"]
    if done.returncode:
        # An interpreter that cannot even run this check has answered.
        return [f"{python} 을 못 돌린다"]
    return [line for line in done.stdout.splitlines() if line.strip()]


def main() -> int:
    # Down a pipe the default here is cp949. The encoding is not left to the
    # environment.
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="위키를 대상 저장소에 붙인다")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--adapter", default=None, help="로컬 adapter가 없는 기존 허브 설치의 이름")
    parser.add_argument("--python", default=sys.executable, help="훅을 돌릴 인터프리터")
    parser.add_argument("--write", action="store_true", help="실제로 쓴다")
    parser.add_argument("--check", action="store_true", help="미적용 변경이 있으면 종료 코드 1")
    parser.add_argument("--agent", choices=("claude", "codex"), default="claude")
    args = parser.parse_args()
    if args.check and args.write:
        parser.error("--check와 --write는 함께 쓸 수 없다")

    project = args.project.expanduser().resolve()
    adapter = args.adapter or project.name
    if not project.is_dir():
        print(f"저장소가 없다: {project}")
        return 2

    print(f"# apply — {project.name}\n")

    # The interpreter that will run the hooks has to be able to import
    # everything they use. When it cannot, the hooks quietly do nothing, which
    # is the worst shape a layer of enforcement can fail in.
    #
    # This check exists in exactly one place. There are two ways in — the
    # `apply --write` the README documents, and `setup_agents` — and putting
    # it on one of them let every machine that came through the other pass,
    # every time, with the emphasis hook attached. Writing the wiring happens
    # in this one function, so the check lives in this one function too.
    missing = unusable(args.python)
    if missing:
        needs = WIKI / "requirements-hooks.txt"
        print(f"`{args.python}` 이 {', '.join(missing)} 를 못 읽는다. 훅이 조용히 죽는다.")
        print(f"{recovery(args.python, needs)} 를 돌리거나"
              " `--python` 으로 다른 인터프리터를 대라.")
        return 2

    source = adapter_path(adapter, project)
    if source is None or not source.exists():
        print(f"어댑터가 없다: `adapters/{adapter}.toml`")
        print("슬롯 값 없이 붙이면 페이지가 `{gate_cmd}` 같은 빈칸째로 실린다.\n")

    missing = unfilled(adapter, project)
    if missing:
        print("## 안 채워진 슬롯\n")
        for name, where in sorted(missing.items()):
            print(f"- `{{{name}}}` — {', '.join(where)}")
        print(f"\n`{source}` 의 `[slots]` 에 채워라.\n")

    denies, scripts = declared()
    missing_scripts = [s for s in scripts if not (HERE / s).exists()]
    if missing_scripts:
        print(f"선언된 훅 스크립트가 없다: {', '.join(missing_scripts)}")
        return 2

    settings_path = project / (".codex" if args.agent == "codex" else ".claude") / (
        "hooks.json" if args.agent == "codex" else "settings.json"
    )
    before = (
        json.loads(settings_path.read_text(encoding="utf-8"))
        if settings_path.exists()
        else {}
    )
    kept = len((before.get("permissions") or {}).get("deny") or [])

    settings = json.loads(json.dumps(before))
    changes = configure(settings, project, adapter, args.python, args.agent)

    local = project / ".wiki"
    if local.is_dir():
        knowledge = sorted(local.glob("*.md"))
        records = sorted((local / "decisions").glob("*.md"))
        print("## 프로젝트 위키\n")
        for path in knowledge:
            print(f"- `.wiki/{path.stem}`")
        print(f"- `.wiki/decisions/` — 결정 기록 {len(records)}건\n")
    else:
        print("## 프로젝트 위키\n")
        print("`.wiki/` 가 없다. 규칙은 붙지만 이 저장소의 지식은 안 실린다.")
        print("`tool/harvest.py` 로 결정을 캐고 지식 페이지를 쓰라.\n")

    print("## deny 규칙\n")
    for rule, where in sorted(denies.items()):
        print(f"- `{rule}` ← {', '.join(where)}")
    print(f"\n기존 규칙 {kept}개는 그대로 둔다.\n")

    if scripts:
        print("## PreToolUse 훅\n")
        for script, where in sorted(scripts.items()):
            print(f"- `{script}` ← {', '.join(where)}")
        print()

    print("## 바뀌는 것\n")
    if not changes:
        print("없음. 이미 붙어 있다.")
    for line in changes:
        print(f"- {line}")
    print()

    if not args.write:
        print("`--write` 를 주면 쓴다.")
        drift = wiring_drift(project, (args.agent,)) if args.check else []
        if drift and not changes:
            for _kind, message in drift:
                print(f"- {message}")
        return int(args.check and bool(changes or missing or drift))
    if not changes:
        return 0

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"썼다: {settings_path}")
    if args.agent == "codex":
        print("Codex /hooks에서 새 훅을 검토하고 신뢰해야 실행된다. 신뢰 설정은 자동으로 쓰지 않는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
