"""connect — attaching a repository to the wiki, and knowing whether it is.

A repository is connected when its `.wiki/adapter.toml` has its required slot,
the hosts' user-level hooks call this hub, no per-project install of the old
kind is left beside them, Codex trusts the hooks, and a real session of each
host got the injection. The cheap items are read on every listing; the
expensive two — the sessions and Codex's trust — are read from the record the
last `[연결]` or `[다시 시험]` left in `raw/connect/<repo>.json`.

`[연결]` moves the machine's wiring to this hub first when it is elsewhere
(asked, with every line shown), writes the adapter into the original checkout
— one of the two writes the server makes there — takes the old per-project
hooks out, and tests both hosts. With the survey on, `survey` goes on from
there. After the survey's pull request merges, `handover` takes the original's
uncommitted adapter out of the way of the fast-forward that brings the merged
one.

What reaches the screen is read by a person and stays Korean.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import apply
import setup_agents
from agent import cli_command

from . import channels, specs, work
from .query import ROOT

RECORDS = ROOT / "raw" / "connect"
PROBES = RECORDS / "probe"
HOSTS = ("claude", "codex")
LABEL = {"claude": "Claude", "codex": "Codex"}
ADAPTER = ".wiki/adapter.toml"
OPTIONAL = specs.SLOTS[1:]     # every slot `[연결]` writes but the gate
PROBE_SECONDS = 240
RETRY = 60.0     # seconds between two handovers of one repository, from the listing

# What the hooks themselves write into a target's `.wiki/` — `sync`, the
# trajectory, the team installer. Not the server's writes, and not a person's
# uncommitted work a handover must stop for.
GENERATED = {".wiki/corpus.json", ".wiki/graph.json", ".wiki/trajectory.jsonl",
             ".wiki/installed-agents.json", ".wiki/.gitignore"}

router = APIRouter()
_records = threading.RLock()
_moving = threading.Lock()     # one hub move at a time
_probing: set[str] = set()


# -- The record ---------------------------------------------------------------

def record(name: str) -> dict:
    try:
        saved = json.loads((RECORDS / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def keep(name: str, **fields) -> dict:
    """Merged into the record and swapped in whole. Every change is told to
    the screens: a test that ends in the background changes a row."""

    with _records:
        now = {**record(name), **fields}
        file = RECORDS / f"{name}.json"
        file.parent.mkdir(parents=True, exist_ok=True)
        temporary = file.with_suffix(".tmp")
        temporary.write_text(json.dumps(now, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(file)
    work.feed.put({"kind": "connect", "repo": name})
    return now


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- The state ------------------------------------------------------------------

def adapter_of(path: Path) -> tuple[dict | None, str]:
    """The adapter's table, `None` when there is none, and why it cannot be read."""

    file = path / ADAPTER
    if not file.is_file():
        return None, ""
    try:
        return tomllib.loads(file.read_text(encoding="utf-8")), ""
    except (OSError, ValueError) as exc:
        return {}, f"adapter 를 못 읽는다 — {exc}"


def tracked(path: Path, rel: str = ADAPTER) -> bool:
    return specs.sh(["git", "ls-files", "--error-unmatch", "--", rel], path).returncode == 0


def leftover(path: Path, host: str) -> list[str]:
    """What `apply.unwire` would take out of this checkout's own settings."""

    return apply.unwire(apply.read_json(path / setup_agents.SETTINGS[host]))


def users() -> dict[str, bool]:
    """Does each host's user level call this hub's `hook.py`? Read once per listing."""

    return {host: apply.user_wired(host) for host in HOSTS}


def status(path: Path, wired: dict[str, bool] | None = None) -> dict:
    """`{state, missing, notes}`. `일부` names every item that is missing; a
    note is something to see that does not make it partial."""

    data, broken = adapter_of(path)
    if data is None:
        return {"state": "미연결", "missing": [], "notes": [], "probing": path.name in _probing}
    wired = users() if wired is None else wired
    rec = record(path.name)
    slots = {k: str(v).strip() for k, v in (data.get("slots") or {}).items()}
    agents = data.get("agents", list(HOSTS))
    agents = [a for a in agents if a in HOSTS] if isinstance(agents, list) else list(HOSTS)
    missing, notes = [], []
    if broken:
        missing.append(broken)
    elif not slots.get("gate_cmd"):
        missing.append("gate_cmd 빈 칸")
    empty = [k for k in OPTIONAL if not slots.get(k)]
    if empty:
        notes.append("채워야 함 — " + ", ".join(empty))
    for host in agents:
        if not wired.get(host):
            missing.append(f"사용자 단위 hook 없음 ({LABEL[host]})")
        try:
            old = leftover(path, host)
        except (OSError, ValueError):
            old = ["읽지 못함"]
        if old:
            missing.append(f"옛 hook 남음 ({LABEL[host]})")
        tried = (rec.get("probe") or {}).get(host)
        if not tried:
            missing.append(f"{LABEL[host]} 시험 안 함")
        elif not tried.get("ok"):
            missing.append(f"{LABEL[host]} 시험 실패")
    if "codex" in agents and rec.get("trust") is not True:
        missing.append("Codex 신뢰 대기" if rec.get("trust") is False else "Codex 신뢰 미확인")
    handed = rec.get("handover") or {}
    if handed.get("state") == "대기":
        missing.append(f"adapter 반영 대기 — {handed.get('reason', '')}")
    elif not rec.get("survey") and not tracked(path) \
            and git(path, "check-ignore", "-q", "--", ADAPTER).returncode:   # an ignored one is local by choice
        notes.append("adapter 커밋 안 됨")
    return {"state": "일부" if missing else "연결 완료", "missing": missing, "notes": notes,
            "probing": path.name in _probing, "survey": rec.get("survey")}


# -- The session test ---------------------------------------------------------

def spawn(args: list[str], cwd: Path, env: dict, timeout: float) -> subprocess.CompletedProcess:
    """The one place a host CLI is started for a test. A test stands in here."""

    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)


def cheapest() -> str:
    """The Codex model a one-word test runs on.

    ponytail: by name — a `mini` model, else Codex's default. The model list
    carries no price; read one if it ever does."""

    models = channels.codex_models()
    chosen = next((m for m in models if "mini" in m["id"]), None) \
        or next((m for m in models if m.get("is_default")), models[0])
    return chosen["id"].removeprefix("codex:")


def command(host: str) -> list[str]:
    if host == "claude":
        return [*cli_command("claude"), "-p", "--model", "haiku", "Reply OK"]
    return [*cli_command("codex"), "exec", "--model", cheapest(), "-c", 'model_reasoning_effort="low"', "Reply OK"]


def probe(path: Path, host: str) -> dict:
    """One real session of `host` in `path`, one turn. It passes when the
    session's own SessionStart hook injected: `hook.py`, started by the CLI
    and inheriting `WIKI_PROBE`, leaves a line saying so in this hub."""

    nonce = secrets.token_hex(8)
    trail = PROBES / f"{nonce}.jsonl"
    detail = ""
    try:
        done = spawn(command(host), path, {**os.environ, "WIKI_PROBE": nonce}, PROBE_SECONDS)
        if done.returncode:
            detail = specs.said(done)
    except Exception as exc:  # noqa: BLE001 — a CLI that would not start is a failed test
        detail = f"{type(exc).__name__}: {exc}"
    try:
        lines = [json.loads(line) for line in trail.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        lines = []
    trail.unlink(missing_ok=True)
    start = [line for line in lines if line.get("event") == "SessionStart"]
    ok = any(line.get("injected") for line in start)
    if not ok and not detail:
        detail = "SessionStart 가 주입하지 않았다" if start else "위키 hook 이 불리지 않았다"
    return {"ok": ok, "ts": time.time(), "detail": detail,
            "chars": max((line.get("chars", 0) for line in start), default=0)}


def trusted(python: str) -> bool:
    """Does every Codex home trust every hook this install writes?"""

    try:
        for file in apply.user_files("codex"):
            hooks = setup_agents.codex_hooks(file.parent, False, python)
            if not hooks or any(h["trustStatus"] != "trusted" for h in hooks):
                return False
        return True
    except Exception:  # noqa: BLE001 — an `app-server` that did not answer is not trust
        return False


def examine(path: Path, then=None) -> bool:
    """Both hosts' tests and Codex's trust, on their own thread; `False` when
    one already runs for this repository. `then` runs after, on success or not."""

    name = path.name
    with _records:
        if name in _probing:
            return False
        _probing.add(name)

    def run() -> None:
        try:
            data, _ = adapter_of(path)
            agents = (data or {}).get("agents", list(HOSTS))
            agents = [a for a in agents if a in HOSTS] if isinstance(agents, list) else list(HOSTS)
            results = {host: probe(path, host) for host in agents}
            keep(name, probe=results, trust=trusted(interpreter()) if "codex" in agents else None)
        finally:
            with _records:
                _probing.discard(name)
            work.feed.put({"kind": "connect", "repo": name})
        if then:
            then()

    threading.Thread(target=run, daemon=True).start()
    return True


# -- Moving the machine to this hub -------------------------------------------

def interpreter() -> str:
    """The hooks' interpreter as installed: the one the user-level commands
    already name, so a server started under another Python does not read as
    a hub that has to move."""

    for host in HOSTS:
        for file in apply.user_files(host):
            try:
                groups = (apply.read_json(file).get("hooks") or {}).values()
            except (OSError, ValueError):
                continue
            for group in (g for gs in groups for g in gs):
                for hook in group.get("hooks", []):
                    command_text = str(hook.get("command", ""))
                    found = re.match(r'(?:&\s*)?"([^"]+)"', command_text)
                    if apply.dispatches(command_text) and found and Path(found[1]).is_file():
                        return found[1]
    return sys.executable


def _snapshot() -> tuple[dict, list, list, str]:
    """`hub()`'s answer with the plan, links and interpreter it was read
    from, so `move` writes the very thing whose digest it checked."""

    python = interpreter()
    try:
        plan = setup_agents.plan_global("both", [], python)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return ({"needed": True, "refused": str(exc), "lines": [], "links": [], "trust": False, "digest": ""},
                [], [], python)
    found = setup_agents.skill_links()
    lines = [{"file": str(path), "change": change} for path, _s, changes in plan for change in changes]
    links = [{"link": str(link), "from": str(old), "to": str(new) if new else None} for link, old, new in found]
    codex = any(Path(line["file"]).name == "hooks.json" for line in lines)
    trust = codex or not trusted(python)
    shown = {"lines": lines, "links": links, "trust": trust}
    return ({"needed": bool(lines or any(link["to"] for link in links) or trust), "refused": "", **shown,
             "digest": digest(json.dumps(shown, sort_keys=True, ensure_ascii=False).encode())}, plan, found, python)


def hub() -> dict:
    """What moving the machine's wiring to this hub changes, every line of
    it, and a digest of exactly that list. `needed` is false when nothing."""

    return _snapshot()[0]


def move(confirmed: str) -> dict:
    """Write what `hub()` showed, only if it is still exactly that. Every
    repository's test was of the old wiring, so the records drop them."""

    with _moving:
        now, plan, found, python = _snapshot()
        if now["refused"]:
            raise HTTPException(409, f"허브를 옮길 수 없다 — {now['refused']}")
        if now["digest"] != confirmed:
            raise HTTPException(409, "확인한 뒤에 바뀔 줄이 달라졌다. 다시 확인한다")
        setup_agents.write_plan(plan)
        for link, _old, new in found:
            if new:
                setup_agents.relink(link, new)
        if now["trust"]:
            for file in apply.user_files("codex"):
                setup_agents.codex_hooks(file.parent, True, python)
        for file in RECORDS.glob("*.json"):
            keep(file.stem, probe={}, trust=None)
        return now


# -- Connecting ----------------------------------------------------------------------

def guess(path: Path) -> dict[str, str]:
    """The slots, as far as the repository's files say. The first match wins."""

    gate = ""
    if any((path / name).is_file() for name in ("pyproject.toml", "setup.cfg", "pytest.ini")):
        gate = "python -m pytest"
    else:
        try:
            scripts = json.loads((path / "package.json").read_text(encoding="utf-8")).get("scripts") or {}
        except (OSError, ValueError, AttributeError):
            scripts = {}
        if isinstance(scripts, dict) and scripts.get("test"):
            gate = "npm test"
        elif (path / "Cargo.toml").is_file():
            gate = "cargo test"
        elif (path / "go.mod").is_file():
            gate = "go test ./..."
    return {"gate_cmd": gate, "review_dir": "artifacts/review", "scratch_dirs": "artifacts/",
            "live_cmd": "", "server_stop": ""}


def adapter_text(slots: dict[str, str]) -> str:
    # A JSON string is a TOML basic string, escapes and all.
    rows = [f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in slots.items()]
    return "\n".join(['agents = ["claude", "codex"]', "", "[slots]", *rows]) + "\n"


def write_adapter(path: Path) -> bool:
    """The adapter, into the original checkout, unless one is there. Its
    hash goes in the record: that is how a handover later tells the copy it
    wrote from one a person changed."""

    file = path / ADAPTER
    if file.exists():
        return False
    text = adapter_text(guess(path))
    file.parent.mkdir(exist_ok=True)
    with open(file, "x", encoding="utf-8", newline="\n") as fh:   # never over one made meanwhile
        fh.write(text)
    keep(path.name, hash=digest(text.encode("utf-8")), adapter=text, written=time.time(), handover=None)
    return True


def unwire(path: Path, write: bool = True) -> list[str]:
    """The old per-project hooks out, the deny rules kept where the host
    enforces them — what `setup_agents --global --project` does."""

    changed = []
    for host in HOSTS:
        file = path / setup_agents.SETTINGS[host]
        if not file.exists() and host != "claude":
            continue
        settings = apply.read_json(file)
        changes = apply.unwire(settings) + (apply.keep_denies(settings) if host == "claude" else [])
        changed += [f"{file}: {change}" for change in changes]
        if changes and write:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return changed


def repo_of(name: str) -> Path:
    path = channels.repo_for(name)
    if path is None:
        raise HTTPException(404, "그런 저장소가 없다")
    return path


# -- Handover ---------------------------------------------------------------------

def git(path: Path, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return specs.sh(["git", *args], path, timeout)


def same_repository(remote: str, pr_url: str) -> bool:
    """`owner/name` of a remote URL against the one a pull request's URL names."""

    def name(url: str) -> str:
        found = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?(?:/pull/\d+)?/?$", url.strip())
        return f"{found[1]}/{found[2]}".lower() if found else ""

    return bool(name(remote)) and name(remote) == name(pr_url)


def handover(repo: Path, n: int) -> dict:
    """The original's uncommitted adapter, handed over to the merged one.

    The six steps of the plan. The copy checked is the copy moved — moved,
    not copied, into `.git/wiki-connect/` — and it stays there until the
    merged adapter is in place. Any step that does not hold stops, restores
    the copy only into an empty place, and leaves the repository waiting on
    the handover, which the listing retries. `{ok, reason}`; `ok` is false
    when it stopped."""

    rec = record(repo.name)
    file = repo / ADAPTER
    moved: Path | None = None

    def wait(reason: str) -> dict:
        left = ""
        if moved is not None:
            if not file.exists():
                os.replace(moved, file)
            else:
                left = str(moved)
                reason += f" — 옮긴 사본: {left}"
        keep(repo.name, handover={"pr": n, "state": "대기", "reason": reason, "left": left, "ts": time.time()})
        return {"ok": False, "reason": reason}

    try:
        view = json.loads(specs.sh(["gh", "pr", "view", str(n), "--json", "baseRefName,mergeCommit,url"],
                                   repo, 60).stdout)
        base, url = view["baseRefName"], view["url"]
    except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired):
        return wait("PR 을 읽지 못했다")
    # 1. Where the original stands.
    on = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if on != base:
        return wait(f"원본이 `{base}` 가 아니라 `{on}` 에 있다")
    upstream = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{base}@{{upstream}}").stdout.strip()
    remote = upstream.split("/", 1)[0] if "/" in upstream else ""
    address = git(repo, "config", "--get", f"remote.{remote}.url").stdout.strip() if remote else ""
    if not same_repository(address, url):
        return wait(f"`{base}` 의 upstream 이 PR 이 머지된 저장소가 아니다")
    dirty = git(repo, "status", "--porcelain", "--untracked-files=all")
    others = [line for line in dirty.stdout.splitlines() if line[3:].strip('"') not in {ADAPTER, *GENERATED}]
    if dirty.returncode or others:
        return wait("adapter 말고도 커밋 안 된 변경이 있다")
    # 2. A copy an earlier handover left is not walked over.
    box = Path(git(repo, "rev-parse", "--absolute-git-dir").stdout.strip()) / "wiki-connect"
    left = sorted(box.iterdir()) if box.is_dir() else []
    if left:
        return wait(f"앞선 넘기기의 사본이 남음 — {left[0]}")
    if file.exists() and not tracked(repo):
        box.mkdir(parents=True, exist_ok=True)
        moved = box / f"{time.time_ns()}-adapter.toml"
        os.replace(file, moved)
        # 3. The copy moved is the copy `[연결]` wrote, or a person's.
        if digest(moved.read_bytes()) != rec.get("hash"):
            diff = "\n".join(difflib.unified_diff((rec.get("adapter") or "").splitlines(),
                                                  moved.read_text(encoding="utf-8").splitlines(),
                                                  "연결이 쓴 것", "원본의 사본", lineterm=""))
            return wait(f"원본의 adapter 가 연결 뒤에 바뀌었다\n{diff}")
    # 4. The merge has reached the remote's base.
    fetched = git(repo, "fetch", remote, f"+refs/heads/{base}:refs/remotes/{remote}/{base}", timeout=120)
    commit = (view.get("mergeCommit") or {}).get("oid") or ""
    if not commit:
        return wait("머지 커밋을 아직 모른다")
    if fetched.returncode or git(repo, "merge-base", "--is-ancestor", commit, f"{remote}/{base}").returncode:
        return wait("머지가 아직 원격에 닿지 않았다")
    # 5. Only a fast-forward; git refuses to overwrite an untracked file.
    forward = git(repo, "merge", "--ff-only", f"{remote}/{base}")
    if forward.returncode:
        return wait(f"fast-forward 하지 못했다 — {specs.said(forward)}")
    # 6. What came is the merged adapter.
    shown = git(repo, "show", f"{commit}:{ADAPTER}")
    if git(repo, "merge-base", "--is-ancestor", commit, "HEAD").returncode or not tracked(repo) \
            or shown.returncode or file.read_text(encoding="utf-8") != shown.stdout:
        return wait("넘긴 뒤의 adapter 가 머지된 것과 다르다")
    if moved is not None:
        moved.unlink()
    keep(repo.name, handover={"pr": n, "state": "완료", "reason": "", "left": "", "ts": time.time()})
    return {"ok": True, "reason": f"adapter 를 넘기고 원본을 `{remote}/{base}` 로 앞으로 옮겼다"}


def retried(path: Path) -> bool:
    """A handover left waiting is walked again from its first step when the
    list is read, once a minute at most. Whether it was."""

    handed = record(path.name).get("handover") or {}
    if handed.get("state") != "대기" or time.time() - handed.get("ts", 0) < RETRY:
        return False
    handover(path, handed["pr"])
    return True


# -- The screen ---------------------------------------------------------------------

def rows() -> list[dict]:
    found = channels.projects()
    again = [retried(Path(row["path"])) for row in found if row["state"] != "미연결"]
    return channels.projects() if any(again) else found


class Connect(BaseModel):
    hub: str = ""        # the digest of the hub move the person confirmed
    survey: bool = False # the person saw the estimate and said yes


class SurveySettings(BaseModel):
    survey: bool
    survey_tokens: int
    survey_minutes: int
    survey_model: str = "opus"
    survey_effort: str = ""


@router.get("/api/connect")
def listing() -> dict:
    from . import survey

    now = hub()
    # The settings modal's hub line: whether this machine's wiring points here yet.
    return {"rows": rows(), "settings": survey.settings(),
            "hub": {"name": channels.WIKI.name, "needed": now["needed"], "refused": now["refused"]}}


@router.get("/api/connect/settings")
def get_settings() -> dict:
    from . import survey

    return survey.settings()


@router.post("/api/connect/settings")
def set_settings(body: SurveySettings) -> dict:
    from . import loop, survey

    if not 10_000 <= body.survey_tokens <= 50_000_000 or not 1 <= body.survey_minutes <= 24 * 60:
        raise HTTPException(400, "토큰 한도는 1만–5천만, 시간 한도는 1–1440분")
    model = body.survey_model.strip()
    if model and not model.startswith("codex:") and not channels.CLAUDE_MODEL.fullmatch(model):
        raise HTTPException(400, "그런 모델 이름은 받지 않는다")
    try:
        allowed = channels.efforts_of(model)
    except Exception as exc:
        raise HTTPException(503, f"Codex 모델 목록 확인 실패: {exc}") from exc
    if body.survey_effort not in allowed:
        raise HTTPException(400, "이 모델이 지원하지 않는 추론 강도")
    loop.store(survey=body.survey, survey_tokens=body.survey_tokens, survey_minutes=body.survey_minutes,
               survey_model=model, survey_effort=body.survey_effort)
    return survey.settings()


@router.get("/api/connect/{name}/plan")
def plan(name: str) -> dict:
    """What `[연결]` will do, for the confirmation: the hub move when one is
    needed, the adapter, the hooks it takes out, and the survey's estimate."""

    from . import survey

    path = repo_of(name)
    exists = (path / ADAPTER).exists()
    return {"hub": hub(), "adapter": None if exists else guess(path), "unwire": unwire(path, write=False),
            "survey": survey.estimate(path) if survey.settings()["survey"] else None}


@router.post("/api/connect/{name}")
def connect(name: str, body: Connect) -> dict:
    from . import survey

    path = repo_of(name)
    if hub()["needed"]:
        if not body.hub:
            raise HTTPException(409, "허브 이전을 먼저 확인받아야 한다")
        move(body.hub)
    write_adapter(path)
    unwire(path)
    go_on = None
    if body.survey and survey.settings()["survey"]:
        go_on = lambda: survey.start(path)  # noqa: E731
    if not examine(path, go_on):
        raise HTTPException(409, "이 저장소의 시험이 아직 돌고 있다")
    return {"ok": True, "row": {"id": name, "path": str(path), **status(path)}}


@router.post("/api/connect/{name}/probe")
def reprobe(name: str) -> dict:
    path = repo_of(name)
    if not (path / ADAPTER).exists():
        raise HTTPException(409, "연결 먼저 — adapter 가 없다")
    if not examine(path):
        raise HTTPException(409, "이 저장소의 시험이 아직 돌고 있다")
    return {"ok": True}
