"""oneshot — from an empty folder and one sentence to a repository a spec can start from.

`docs/plans/oneshot/2-bootstrap.md` is the design. A run lives in
`raw/oneshot/<run>.json` until bootstrap writes its commit point, the
`spec` pointer; from then on the spec's `oneshot` field owns it and the run
record is never written again.

Stages here: `intake` (one model turn restates the owner's sentence),
`environment` (one model turn proposes profile values the server's schema
allows; the owner confirms one card), `bootstrap` (no model: nine
sub-steps, each checking its effect before acting), then `spec`, where the
next step of the plan begins. Every stage can stop with a reason and resume.

What reaches the screen is read by a person and stays Korean; the prompts
are English.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent import ChatSession
from common import swap, worktree_home
from workspace import TASK, create

from . import channels, connect, prerequisite_review, runtime, specs
from .oneshot_profile import (KINDS, SCHEMA, SUPPORTED_KINDS, allowed, charged, cut, gate_cmd, ledger, problems,
                              proposal)
from .query import ROOT

RUNS = ROOT / "raw" / "oneshot"
PROFILES = RUNS / "profiles"
VERSION = 1
BRANCH = "oneshot-initial"   # also the spec id: a valid task name, so the branch and the spec share it
INTENT_PROMPT = (ROOT / "tool/prompts/oneshot-intent.md").read_text(encoding="utf-8")
PROFILE_PROMPT = (ROOT / "tool/prompts/oneshot-profile.md").read_text(encoding="utf-8")
TOOLS = "Read,Glob,Grep"     # the run folder holds nothing to read; no tool writes
BLOCK = re.compile(r"^```(oneshot-intent|oneshot-profile)[ \t]*\r?\n(.*?)^```[ \t]*$", re.M | re.S)
MAX_SENTENCE = 2_000
HEARTBEAT = 30.0   # seconds between a running turn's `inflight.seen` writes

STEPS = ("recheck", "folder", "files", "commit", "register", "remote", "origin", "publish", "spec")
RUNNING = {"intake": "intake", "environment": "profile", "bootstrap": "bootstrap"}

router = APIRouter()
_lock = threading.RLock()   # run files, name reservations and workers, read and written as one step
_workers: dict[str, threading.Thread] = {}


# -- Run records ------------------------------------------------------------------

def file_of(rid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{16}", rid or ""):
        raise HTTPException(404, "그런 원샷 실행이 없다")
    return RUNS / f"{rid}.json"


def load(rid: str) -> dict:
    try:
        return json.loads(file_of(rid).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(404, "그런 원샷 실행이 없다") from None


def save(run: dict) -> None:
    if run.get("committed"):
        raise RuntimeError("인계가 끝난 실행 기록은 다시 쓰지 않는다")
    file = file_of(run["id"])
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix(".tmp")
    temporary.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    swap(temporary, file)


def runs() -> list[dict]:
    found = []
    for file in sorted(RUNS.glob("*.json")):
        try:
            found.append(json.loads(file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return found


def changed(rid: str, **fields) -> dict:
    with _lock:
        run = load(rid)
        run.update(fields)
        save(run)
        return run


def stop(rid: str, reason: str, detail: str) -> dict:
    with _lock:
        run = load(rid)
        return changed(rid, working=None, stopped={"reason": reason, "detail": detail, "state": run["state"]})


# -- The profile -------------------------------------------------------------------

def saved_profile(kind: str) -> dict:
    try:
        return json.loads((PROFILES / f"{kind}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def prefilled(kind: str) -> dict:
    """The kind's saved profile over the schema's defaults; `asked` fields stay empty on a first run."""

    base = {k: f["default"] for k, f in SCHEMA.items()}
    return {**base, **{k: v for k, v in saved_profile(kind).items() if k in SCHEMA}}


# -- Model turns -----------------------------------------------------------------------

def role_config() -> dict:
    from .query import config

    return config("next")


def codex_role() -> bool:
    return str(role_config().get("model") or "").startswith("codex:")


class Failed(RuntimeError):
    """A turn that ended without an answer; `meta` keeps what it reported, its cost included."""

    def __init__(self, text: str, meta: dict) -> None:
        super().__init__(text)
        self.meta = meta


def heartbeat(rid: str, ended: threading.Event) -> None:
    """While a turn runs, `inflight.seen` moves on: how long it lived, should the server die inside it."""

    while not ended.wait(HEARTBEAT):
        with _lock:
            if inflight := load(rid).get("inflight"):
                changed(rid, inflight={**inflight, "seen": time.time()})


def ask(rid: str, role: str, system: str, text: str) -> tuple[str, dict, bool]:
    """One turn in the run's own folder: `(final, meta, codex)`. Patched in tests."""

    cfg = role_config()
    where = RUNS / rid
    where.mkdir(parents=True, exist_ok=True)
    chat = ChatSession(where, tools=TOOLS, system=system, model=cfg["model"], effort=cfg["effort"])
    final, meta, failed = "", {}, ""
    ended = threading.Event()
    threading.Thread(target=heartbeat, args=(rid, ended), daemon=True, name=f"oneshot-beat-{rid}").start()
    try:
        for ev in chat.say(text, runtime.stopping):
            if ev.kind == "done":
                final, meta = ev.text, dict(ev.meta or {})
                if meta.pop("error", False):
                    failed = final or "완료된 답이 없다"
            elif ev.kind == "error":
                failed = ev.text
    finally:
        ended.set()
        chat.close()
    if failed:
        raise Failed(failed, meta)
    return final, meta, chat.is_codex


def block(final: str, name: str):
    found = [m for m in BLOCK.finditer(final) if m.group(1) == name]
    if len(found) != 1:
        raise ValueError(f"`{name}` 블록이 하나 있어야 한다")
    try:
        return json.loads(found[0].group(2))
    except ValueError as exc:
        raise ValueError(f"`{name}` 블록이 JSON 이 아니다 — {exc}") from exc


def turned(rid: str, role: str, system: str, text: str, name: str, check) -> object | None:
    """A turn whose block passes `check`, asked again once with the reason; `None` after stopping."""

    reason = ""
    for _ in range(2):
        if runtime.stopping.is_set():
            stop(rid, "restart", "서버가 꺼지는 중이라 멈췄다")
            return None
        codex = codex_role()
        with _lock:
            run = load(rid)
            if why := allowed(run.get("spend"), run.get("profile") if run.get("confirmed") else None, role, codex):
                stop(rid, "budget", why)
                return None
            # Recorded before the turn is sent: a server that dies inside it leaves this for `recover` to charge.
            changed(rid, inflight={"role": role, "codex": codex, "sent": time.time()})
        started = time.monotonic()
        try:
            final, meta, codex = ask(rid, role, system, text if not reason else
                                     f"{text}\n\nYour last `{name}` block was refused: {reason}. Return it again.")
        except Exception as exc:   # a broken turn still owes the screen a reason; an unreported cost is unknown, never zero
            with _lock:
                run = load(rid)
                cost = (getattr(exc, "meta", None) or {}).get("cost_usd")
                changed(rid, inflight=None, spend=charged(run.get("spend"), role, codex, cost, time.monotonic() - started))
            if runtime.stopping.is_set():
                stop(rid, "restart", "서버가 꺼지는 중이라 멈췄다")
            else:
                stop(rid, "block", f"모델 턴이 실패했다 — {exc}")
            return None
        with _lock:
            run = load(rid)
            changed(rid, inflight=None,
                    spend=charged(run.get("spend"), role, codex, meta.get("cost_usd"), time.monotonic() - started))
        try:
            return check(block(final, name))
        except ValueError as exc:
            reason = str(exc)
    stop(rid, "block", f"`{name}` 블록이 두 번 거부됐다 — {reason}")
    return None


# -- S0 intake ---------------------------------------------------------------------------

def intent_of(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("`oneshot-intent` 는 객체여야 한다")
    for key in ("restated", "kind", "name", "users", "first_screen"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"`{key}` 가 비었다")
    if value["kind"] not in KINDS:
        raise ValueError(f"`kind` 는 {', '.join(KINDS)} 중 하나다")
    name = specs.slugged(value["name"])
    if not name:
        raise ValueError("`name` 을 저장소 이름으로 쓸 수 없다")
    return {k: value[k].strip() for k in ("restated", "kind", "users", "first_screen")} | {"name": name}


def intake(rid: str, edit: str = "") -> None:
    run = load(rid)
    text = (f"The owner's sentence, verbatim:\n\n{run['sentence']}\n" +
            (f"\nYour previous restatement:\n\n{json.dumps(run['intent'], ensure_ascii=False)}\n"
             f"\nThe owner's correction:\n\n{edit}\n" if edit else ""))
    found = turned(rid, "intake", INTENT_PROMPT, text, "oneshot-intent", intent_of)
    if found is not None:
        if found["kind"] not in SUPPORTED_KINDS:
            found["unsupported"] = True
        changed(rid, intent=found, working=None)


# -- Names ---------------------------------------------------------------------------------

def gh(args: list[str], cwd: Path | None = None, timeout: float = 120):
    return specs.sh(["gh", *args], cwd or ROOT, timeout)


def gh_user() -> str:
    done = gh(["api", "user", "--jq", ".login"], timeout=30)
    return done.stdout.strip() if not done.returncode else ""


def remote_id(owner: str, name: str) -> str | None:
    """The GitHub repository's node id, `""` when there is none, `None` when GitHub could not be asked."""

    done = gh(["repo", "view", f"{owner}/{name}", "--json", "id", "--jq", ".id"], timeout=30)
    if not done.returncode:
        return done.stdout.strip()
    err = (done.stderr or "").lower()
    return "" if "could not resolve" in err or "not found" in err else None


def conflicts(rid: str, path: Path, name: str, owner: str, remote: bool = True) -> list[str]:
    """Why `name` cannot be this run's, before anything is written. Empty when it can."""

    found = []
    if name == channels.WIKI.name:
        found.append("허브와 같은 이름이다")
    if (connect.RECORDS / f"{name}.json").exists():
        found.append(f"같은 이름의 연결 기록이 있다: raw/connect/{name}.json")
    if name in connect.added_names():
        found.append("같은 이름으로 등록된 폴더가 있다")
    same = channels.WORKSPACE / name
    if same.exists() and same.resolve() != path.resolve():
        found.append(f"작업 공간에 같은 이름의 폴더가 있다: {same}")
    for other in runs():
        if other["id"] != rid and not other.get("committed") and (other.get("identity") or {}).get("name") == name:
            found.append("다른 원샷 실행이 이 이름을 잡고 있다")
    if remote and owner:
        known = remote_id(owner, name)
        if known is None:
            found.append("GitHub 에 물어보지 못했다 — GitHub CLI 로그인 상태를 확인하라")
        elif known:
            found.append(f"GitHub 에 같은 이름의 저장소가 있다: {owner}/{name}")
    return found


def empty(path: Path) -> bool:
    return path.is_dir() and not any(path.iterdir())


# -- S1 environment -------------------------------------------------------------------------

def environment(rid: str) -> None:
    run = load(rid)
    kind = run["intent"]["kind"]
    base = prefilled(kind)
    if not base.get("github_owner"):
        base["github_owner"] = gh_user()
    shown = {k: {**{x: f[x] for x in ("options", "enabled", "text", "number") if x in f}} for k, f in SCHEMA.items()}
    text = (f"The owner's sentence:\n\n{run['sentence']}\n\nThe confirmed restatement:\n\n"
            f"{json.dumps(run['intent'], ensure_ascii=False)}\n\nThe schema:\n\n{json.dumps(shown)}\n\n"
            f"The prefilled profile:\n\n{json.dumps(base, ensure_ascii=False)}\n")
    found = turned(rid, "profile", PROFILE_PROMPT, text, "oneshot-profile", proposal)
    if found is not None:
        values = {**base, **{k: v["value"] for k, v in found.items()}}
        changed(rid, profile=values, proposed=found, working=None)


# -- Bootstrap ---------------------------------------------------------------------------------

def planned(run: dict) -> dict[str, str]:
    """Every file the base commit holds, by path, as the run writes it."""

    p, intent, name = run["profile"], run["intent"], run["identity"]["name"]
    ignore = [".venv/", "__pycache__/", "*.pyc", ".pytest_cache/", ".env", "node_modules/", "dist/", "artifacts/"]
    setup = {"uv": "uv sync",
             "venv": f"python -m venv .venv\n.venv/{'Scripts' if p['os_now'] == 'windows' else 'bin'}/python -m pip install -e .",
             "conda": f"conda create -n {p.get('conda_env')} python=3.12\nconda activate {p.get('conda_env')}"}[p["python_env"]]
    readme = (f"# {name}\n\n{intent['restated']}\n\n> {run['sentence']}\n\n"
              f"## Setup\n\n```\n{setup}\n```\n")
    files = {".gitignore": "\n".join(ignore) + "\n", "README.md": readme,
             ".wiki/adapter.toml": connect.adapter_text({"gate_cmd": gate_cmd(p), "review_dir": "artifacts/review",
                                                         "scratch_dirs": "artifacts/", "live_cmd": "", "server_stop": ""})}
    if p["os_later"] == "other":
        files[".gitattributes"] = "* text=auto eol=lf\n"
    if p["visibility"] == "public" and run.get("license_text"):
        files["LICENSE"] = run["license_text"]
    return files


def git(path: Path, *args: str, timeout: float = 60):
    return specs.sh(["git", *args], path, timeout)


def tracked(path: Path) -> set[str]:
    done = git(path, "ls-tree", "-r", "--name-only", "main")
    return set(done.stdout.splitlines()) if not done.returncode else set()


def blob(text: str) -> str:
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def as_planned(path: Path, files: dict[str, str]) -> bool:
    """`main` is one commit holding exactly `files`, each byte for byte as planned."""

    if tracked(path) != set(files) or git(path, "rev-list", "--count", "main").stdout.strip() != "1":
        return False
    return all(git(path, "rev-parse", f"main:{rel}").stdout.strip() == blob(text) for rel, text in files.items())


class Stop(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason, self.detail = reason, detail


def step(rid: str, name: str, fn) -> dict:
    """Run sub-step `name` unless recorded done; record it right after its effect."""

    run = load(rid)
    if name not in run["steps"]:
        if runtime.stopping.is_set():   # no new outward step once the server is going down
            raise Stop("restart", "서버가 꺼지는 중이라 멈췄다")
        fn(run)
        with _lock:
            run = load(rid)
            run["steps"][name] = time.time()
            save(run)
    return load(rid)


def s_recheck(run: dict) -> None:
    ident = run["identity"]
    known = (run.get("github") or {}).get("node_id")
    found = conflicts(run["id"], Path(ident["path"]), ident["name"], ident["owner"], remote=not known)
    if found:
        raise Stop("folder", "; ".join(found))


def s_folder(run: dict) -> None:
    path, files = Path(run["identity"]["path"]), planned(run)
    if not path.exists():
        path.mkdir(parents=True)
        return
    mine = {p.split("/")[0] for p in files} | {".git"}
    extra = [c.name for c in path.iterdir() if c.name not in mine]
    if extra:
        raise Stop("folder", f"폴더에 이 실행이 쓰지 않은 것이 있다: {', '.join(sorted(extra)[:5])}")


def s_files(run: dict) -> None:
    path = Path(run["identity"]["path"])
    if run["profile"]["visibility"] == "public" and not run.get("license_text"):
        done = gh(["api", f"licenses/{run['profile']['license']}", "--jq", ".body"], timeout=30)
        if done.returncode or not done.stdout.strip():
            raise Stop("github", "라이선스 본문을 받지 못했다")
        text = done.stdout.replace("[year]", time.strftime("%Y")).replace("[fullname]", run["identity"]["owner"])
        run = changed(run["id"], license_text=text)
    for rel, text in planned(run).items():
        file = path / rel
        if file.exists():
            if file.read_bytes() != text.encode("utf-8"):
                raise Stop("folder", f"`{rel}` 이 이 실행이 쓸 내용과 다르다 — 덮어쓰지 않는다")
            continue
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8", newline="\n")


def s_commit(run: dict) -> None:
    path, files = Path(run["identity"]["path"]), planned(run)
    if not (path / ".git").exists():
        if git(path, "init", "-b", "main").returncode:
            raise Stop("folder", "git init 이 실패했다")
    head = git(path, "rev-parse", "--verify", "main")
    if head.returncode:
        # The files step may have finished long ago: what is on disk now is what gets committed, so check it now.
        changed_files = [rel for rel, text in files.items()
                         if not (path / rel).is_file() or (path / rel).read_bytes() != text.encode("utf-8")]
        if changed_files:
            raise Stop("folder", f"`{changed_files[0]}` 이 이 실행이 쓴 내용과 다르다 — 커밋하지 않는다")
        for args in (["add", "--", *files], ["commit", "-m", f"Start {run['identity']['name']} from oneshot"]):
            done = git(path, *args)
            if done.returncode:   # git's own words: a missing user.name is the owner's to set
                raise Stop("folder", f"기본 커밋을 만들지 못했다 — {(done.stderr or done.stdout).strip()[-300:]}")
    now = git(path, "rev-parse", "main").stdout.strip()
    if not as_planned(path, files) or (run.get("base") and now != run["base"]):
        raise Stop("folder", "`main` 이 이 실행의 기본 커밋이 아니다")
    changed(run["id"], base=now)


def s_register(run: dict) -> None:
    path, name = Path(run["identity"]["path"]), run["identity"]["name"]
    if path.parent.resolve() != channels.WORKSPACE.resolve():
        try:
            connect.add(connect.Folder(path=str(path)))
        except HTTPException as exc:
            raise Stop("folder", str(exc.detail)) from exc
    if channels.repo_for(name) is None or channels.repo_for(name).resolve() != path.resolve():
        raise Stop("folder", "이 이름으로 이 폴더를 찾지 못했다")
    text = planned(run)[".wiki/adapter.toml"]
    connect.keep(name, hash=connect.digest(text.encode("utf-8")), adapter=text, written=time.time(), handover=None)


def s_remote(run: dict) -> None:
    ident = run["identity"]
    known = (run.get("github") or {}).get("node_id")
    now = remote_id(ident["owner"], ident["name"])
    if now is None:
        raise Stop("github", "GitHub 에 물어보지 못했다 — GitHub CLI 로그인 상태를 확인하라")
    if known:
        if now != known:
            raise Stop("github", "기록한 GitHub 저장소가 사라졌거나 바뀌었다")
        return
    if now:
        if not run.get("adopt"):
            raise Stop("github", f"GitHub 에 기록하지 않은 같은 이름의 저장소가 있다: {ident['owner']}/{ident['name']} — "
                                 "커밋이 없으면 [이어 쓰기], 아니면 이름을 바꿔라")
        commits = gh(["api", f"repos/{ident['owner']}/{ident['name']}/commits"], timeout=30)
        # Only an answer that shows no commits adopts: GitHub's 409 for an empty repository, or an empty list.
        empty_repo = commits.returncode and "Git Repository is empty" in f"{commits.stdout}\n{commits.stderr}"
        if not empty_repo and commits.returncode:
            raise Stop("github", "커밋이 없는 저장소인지 확인하지 못했다 — 이어 쓰지 않는다")
        if not empty_repo and commits.stdout.strip() != "[]":
            raise Stop("github", "커밋이 있는 저장소는 이어 쓰지 않는다")
        changed(run["id"], github={"node_id": now})
        return
    flag = "--public" if run["profile"]["visibility"] == "public" else "--private"
    if gh(["repo", "create", f"{ident['owner']}/{ident['name']}", flag]).returncode:
        raise Stop("github", "GitHub 저장소를 만들지 못했다")
    made = remote_id(ident["owner"], ident["name"])
    if not made:
        raise Stop("github", "만든 GitHub 저장소를 확인하지 못했다")
    changed(run["id"], github={"node_id": made})


def url_of(run: dict) -> str:
    return f"https://github.com/{run['identity']['owner']}/{run['identity']['name']}.git"


def origin_is_ours(run: dict) -> None:
    """Every URL `origin` fetches from and pushes to, after `insteadOf` rewrites, is the run's repository."""

    path, url = Path(run["identity"]["path"]), url_of(run)
    for kind in ([], ["--push"]):
        have = git(path, "remote", "get-url", *kind, "--all", "origin")
        urls = have.stdout.split()
        if have.returncode or not urls or any(u.removesuffix(".git") != url.removesuffix(".git") for u in urls):
            raise Stop("github", f"origin 이 이 실행의 저장소만 가리키지 않는다: {' '.join(urls) or '없음'}")


def s_origin(run: dict) -> None:
    path = Path(run["identity"]["path"])
    if git(path, "remote", "get-url", "origin").returncode:
        if git(path, "remote", "add", "origin", url_of(run)).returncode:
            raise Stop("github", "origin 을 추가하지 못했다")
    origin_is_ours(run)


def s_publish(run: dict) -> None:
    """Every earlier step is checked again here: a record of it is no proof the folder or origin stayed so."""

    path, ident = Path(run["identity"]["path"]), run["identity"]
    if git(path, "rev-parse", "main").stdout.strip() != run["base"] or not as_planned(path, planned(run)):
        raise Stop("folder", "`main` 이 확인한 기본 커밋이 아니다 — 푸시하지 않는다")
    origin_is_ours(run)
    if remote_id(ident["owner"], ident["name"]) != (run.get("github") or {}).get("node_id"):
        raise Stop("github", "GitHub 저장소가 기록한 것과 다르거나 확인하지 못했다 — 푸시하지 않는다")
    remote = git(path, "ls-remote", "origin", "refs/heads/main", timeout=120)
    if not remote.returncode and remote.stdout.split()[:1] == [run["base"]]:
        return
    # Before any spec exists no evidence is declared; the shared check still owns every server push.
    if prerequisite_review.push(path, {}, ["git", "push", "-u", "origin", "main"]).returncode:
        raise Stop("github", "`main` 을 푸시하지 못했다")


def s_spec(run: dict) -> None:
    path, name = Path(run["identity"]["path"]), run["identity"]["name"]
    branch = git(path, "branch", "--show-current").stdout.strip()
    if branch != "main" or git(path, "rev-parse", "HEAD").stdout.strip() != run["base"]:
        raise Stop("folder", "원본 체크아웃이 기본 커밋의 `main` 이 아니다")
    spec = specs.load(name, BRANCH)
    if spec is not None and (spec.get("oneshot") or {}).get("run") != run["id"]:
        raise Stop("folder", f"이 실행의 것이 아닌 명세 `{BRANCH}` 가 있다")
    tree = worktree_home(path) / BRANCH
    if tree.exists():
        at = git(tree, "rev-parse", "HEAD").stdout.strip()
        if git(tree, "branch", "--show-current").stdout.strip() != BRANCH or at != run["base"]:
            raise Stop("folder", f"이 실행의 것이 아닌 작업트리가 있다: {tree}")
    elif not git(path, "rev-parse", "--verify", f"refs/heads/{BRANCH}").returncode:
        raise Stop("folder", f"이 실행의 것이 아닌 브랜치 `{BRANCH}` 가 있다")
    else:
        try:
            tree = create(path, BRANCH, linked=True)
        except (ValueError, RuntimeError) as exc:
            raise Stop("folder", str(exc)) from exc
    if spec is None:
        now = time.time()
        specs.save({"id": BRANCH, "repo": name, "rev": 1, "goal": run["intent"]["restated"], "out": [],
                    "done": [gate_cmd(run["profile"])],
                    "grounds": {"pages": [], "files": [], "rules": []}, "decisions": [],
                    "source": {"focus": "oneshot", "turn": now, "plan": None}, "state": "작업 중", "stopped": None,
                    "worktree": str(tree), "workspace_mode": "worktree", "branch": BRANCH, "start_head": run["base"],
                    "return_branch": "main", "pr": None, "report": None, "gate": None, "fault": None,
                    "history": [{"ts": now, "state": "정리됨"}, {"ts": now, "state": "작업 중"}],
                    "oneshot": {"version": VERSION, "run": run["id"], "stage": "spec", "spend": ledger(run.get("spend")),
                                "sentence": run["sentence"], "intent": run["intent"], "profile": run["profile"]}})


SUBSTEPS = {"recheck": s_recheck, "folder": s_folder, "files": s_files, "commit": s_commit, "register": s_register,
            "remote": s_remote, "origin": s_origin, "publish": s_publish, "spec": s_spec}


def bootstrap(rid: str) -> None:
    try:
        for name in STEPS:
            step(rid, name, SUBSTEPS[name])
    except Stop as exc:
        stop(rid, exc.reason, exc.detail)
        return
    except Exception as exc:   # an unforeseen failure still stops with a reason
        stop(rid, "folder", f"{type(exc).__name__}: {exc}")
        return
    with _lock:
        run = load(rid)
        kind = run["intent"]["kind"]
        PROFILES.mkdir(parents=True, exist_ok=True)
        (PROFILES / f"{kind}.json").write_text(json.dumps(run["profile"], ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8", newline="\n")
        # The commit point, one write: from here the spec owns the run and this record is never written again.
        run.update(state="spec", working=None, committed=True, spec={"repo": run["identity"]["name"], "id": BRANCH})
        file = file_of(rid)
        temporary = file.with_suffix(".tmp")
        temporary.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        swap(temporary, file)


# -- Workers ------------------------------------------------------------------------------------

def launch(rid: str, work: str, *args, **fields) -> None:
    """One worker per run. Callers hold `_lock` from their state check through this call, and hand
    the stage's new state in `fields`: it is written with `working` only once the worker may start,
    so a refusal leaves the run as it was."""

    fn = {"intake": intake, "profile": environment, "bootstrap": bootstrap}[work]
    with _lock:
        if runtime.stopping.is_set():
            raise HTTPException(503, "서버가 종료 중이다. 다시 시작한 뒤 이어가라")
        if (running := _workers.get(rid)) is not None and running.is_alive():
            raise HTTPException(409, "이 실행은 이미 돌고 있다")
        changed(rid, **fields, working=work, stopped=None)
        thread = threading.Thread(target=fn, args=(rid, *args), daemon=True, name=f"oneshot-{rid}")
        _workers[rid] = thread
        thread.start()


def close_all(wait: bool = True) -> list[threading.Thread]:
    """At shutdown, after `runtime.stopping` is set: each worker stops at its next step or turn with
    `restart`; a step already running finishes first. Its threads, joined unless `wait` is false."""

    with _lock:
        threads = [t for t in _workers.values() if t.is_alive()]
    for thread in threads if wait else []:
        thread.join()
    return threads


def recover() -> None:
    """At server start: a stage that was running when the server stopped is stopped with `restart`,
    and a turn it had sent and never charged is charged as unknown, never zero."""

    for run in runs():
        if run.get("working") and not run.get("committed"):
            if inflight := run.get("inflight"):
                sent = float(inflight.get("sent") or time.time())
                elapsed, lived = max(0.0, time.time() - sent), max(0.0, float(inflight.get("seen") or sent) - sent)
                changed(run["id"], inflight=None,
                        spend=cut(run.get("spend"), inflight["role"], inflight["codex"], elapsed, lived))
            stop(run["id"], "restart", "서버가 다시 켜졌다")


def view(run: dict) -> dict:
    errors, warnings = problems(run["profile"]) if run.get("profile") else ([], [])
    return {**{k: v for k, v in run.items() if k != "license_text"}, "errors": errors, "warnings": warnings,
            "schema": SCHEMA, "steps_order": list(STEPS)}


# -- HTTP ------------------------------------------------------------------------------------------

class Start(BaseModel):
    sentence: str
    path: str = ""   # a picked empty folder; empty means a new folder in the workspace


class Intent(BaseModel):
    confirm: bool
    edit: str = ""
    name: str = ""


class Profile(BaseModel):
    values: dict
    confirm: bool = False


class Resume(BaseModel):
    adopt: bool = False


@router.post("/api/oneshot")
def start(body: Start) -> dict:
    sentence = body.sentence.strip()
    if not sentence or len(sentence) > MAX_SENTENCE:
        raise HTTPException(400, f"만들고 싶은 것을 1–{MAX_SENTENCE}자로 적어라")
    picked = ""
    if body.path.strip():
        path = Path(body.path.strip()).expanduser()
        if not path.is_absolute() or not empty(path):
            raise HTTPException(400, f"비어 있는 폴더여야 한다 — 숨김 파일도 없어야 한다: {body.path}")
        picked = str(path.resolve())
    rid = secrets.token_hex(8)
    with _lock:
        save({"id": rid, "version": VERSION, "created": time.time(), "state": "intake", "working": None,
              "stopped": None, "sentence": sentence, "picked": picked, "intent": None, "confirmed": False,
              "profile": None, "proposed": {}, "identity": None, "steps": {}, "github": {}, "base": None,
              "adopt": False, "spend": ledger(None), "spec": None})
        try:
            launch(rid, "intake")
        except HTTPException:   # the record exists already: left stopped, so `[재개]` takes it on
            stop(rid, "restart", "서버가 꺼지는 중이라 시작하지 못했다")
            raise
    return view(load(rid))


@router.get("/api/oneshot")
def listing() -> dict:
    return {"runs": [view(r) for r in runs() if not r.get("committed")]}


@router.get("/api/oneshot/{rid}")
def status(rid: str) -> dict:
    return view(load(rid))


def idle(run: dict, state: str) -> None:
    if run.get("committed"):
        raise HTTPException(409, "이미 명세로 넘어간 실행이다")
    if run["state"] != state or run.get("working"):
        raise HTTPException(409, f"지금 할 수 없다 — {run['state']}{' 진행 중' if run.get('working') else ''}")


@router.post("/api/oneshot/{rid}/intent")
def intent(rid: str, body: Intent) -> dict:
    with _lock:
        run = load(rid)
        idle(run, "intake")
        if run.get("stopped") or not run.get("intent"):
            raise HTTPException(409, "다시 말할 재진술이 없다")
        if not body.confirm:
            if not body.edit.strip():
                raise HTTPException(400, "고칠 내용을 적어라")
            launch(rid, "intake", body.edit.strip())
            return view(load(rid))
        if run["intent"].get("unsupported"):
            raise HTTPException(409, f"`{run['intent']['kind']}` 는 이 버전에서 만들 수 없다 — 지금은 webapp 만")
        name = specs.slugged(body.name or run["intent"]["name"])
        if run["picked"]:
            path = Path(run["picked"])
            name = path.name
            if specs.slugged(name) != name:
                raise HTTPException(400, f"고른 폴더 이름을 저장소 이름으로 쓸 수 없다: {name}")
            if not empty(path):
                raise HTTPException(409, "고른 폴더가 이제 비어 있지 않다")
        else:
            path = channels.WORKSPACE / name
            if path.exists() and not empty(path):
                raise HTTPException(409, f"작업 공간에 같은 이름의 폴더가 있다: {path}")
        if not name or not TASK.fullmatch(name):
            raise HTTPException(400, "저장소 이름은 소문자·숫자·- 만")
        owner = gh_user()
        if not owner:
            raise HTTPException(409, "GitHub CLI 에 로그인하지 않았다")
        found = conflicts(rid, path, name, owner)
        if found:
            raise HTTPException(409, "; ".join(found))
        # Identity is fixed here, under the lock, before anything is written.
        launch(rid, "profile", state="environment",
               identity={"path": str(path), "name": name, "owner": owner, "spec_id": BRANCH, "branch": BRANCH})
    return view(load(rid))


@router.post("/api/oneshot/{rid}/profile")
def profile(rid: str, body: Profile) -> dict:
    with _lock:
        run = load(rid)
        idle(run, "environment")
        if run.get("profile") is None:
            raise HTTPException(409, "카드가 아직 없다")
        unknown = set(body.values) - set(SCHEMA)
        if unknown:
            raise HTTPException(400, f"스키마에 없는 필드: {', '.join(sorted(unknown))}")
        values = {**run["profile"], **body.values}
        if body.confirm:
            errors, _ = problems(values)
            if errors:
                raise HTTPException(400, "; ".join(errors))
            ident = run["identity"]
            if values["github_owner"] != ident["owner"]:
                found = conflicts(rid, Path(ident["path"]), ident["name"], values["github_owner"])
                if found:
                    raise HTTPException(409, "; ".join(found))
                ident = {**ident, "owner": values["github_owner"]}
            launch(rid, "bootstrap", profile=values, identity=ident, confirmed=True, state="bootstrap")
        else:
            return view(changed(rid, profile=values))
    return view(load(rid))


@router.post("/api/oneshot/{rid}/resume")
def resume(rid: str, body: Resume) -> dict:
    """`[재개]`: the stage that stopped runs again; bootstrap skips every sub-step already recorded."""

    with _lock:
        run = load(rid)
        if run.get("committed") or not run.get("stopped") or run.get("working"):
            raise HTTPException(409, "멈춘 실행이 아니다")
        launch(rid, RUNNING[run["state"]], **({"adopt": True} if body.adopt else {}))
    return view(load(rid))
