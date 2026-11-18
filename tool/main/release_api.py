"""release_api — the repository screen's release card and its button (`docs/plans/release/0-overview.md`, step 5).

The card reads what a press would do: the last production version, how many commits `main` is ahead of it, and
the version each choice would publish, from the one selector the attempt uses (`release.version`). Before the
first production release it shows the seed and offers no choice.

Publishing stays locked (`LOCKED`). The plan requires that build and repair code run under another Windows
account than the release key before anything is published, and that is not in place. The button runs the
unpublished rehearsal instead (`release_rehearsal.rehearse`): two builds of origin's `main`, the commit the card
shows, signed with a disposable test key and checked, with a source failure handed to a repair task as a press's
would be.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from common import errorlog
from fastapi import APIRouter, HTTPException

from . import channels, release, release_publish, release_rehearsal, release_repair
from . import github_release as gh

router = APIRouter()

LOCKED = ("실제 게시는 잠겨 있다 — 빌드와 수정 작업이 서명 키와 같은 Windows 계정에서 돌아서, "
          "다른 계정으로 분리한 뒤에 켠다. 지금은 시험 빌드만 한다(아무것도 올리지 않는다)")
REF = "origin/main"   # what the card pins and what the test build builds
SHUTDOWN_WAIT = 120   # seconds a closing server gives a stopped test build to remove its checkout and keys

_runs: dict[str, dict] = {}   # project name -> its rehearsal: the running one, or the last one's summary
_lock = threading.Lock()


def _repo(name: str) -> Path:
    repo = channels.repo_for(name)
    if repo is None:
        raise HTTPException(404, f"`{name}` 은 이 서버가 아는 프로젝트가 아니다")
    return repo


def card(repo: Path) -> dict:
    """What the card shows for `repo`, read from origin's `main` and the repository's GitHub Releases. A choice
    whose version Android cannot carry is shown with why, beside the choices that remain."""

    name = release.remote(repo)
    sha = release.pin(repo, REF)
    cfg = release.settings(repo, sha)
    first = release.seed(repo, sha, cfg["version_source"])
    slug = gh.slug(name)
    listed, taken = gh.releases(slug), gh.tags(slug)
    shapes = [release_publish._shape(r) for r in listed]
    prior = release_publish.latest(listed)
    versions, refused = {}, {}
    for bump in release.BUMPS if prior else (None,):
        try:
            versions[bump or "first"] = release.version(shapes, taken, bump, first)
        except ValueError as exc:
            refused[bump or "first"] = str(exc)
    if prior:
        since = gh.tag(slug, prior["tag_name"])
        count = ahead(repo, since, sha) if since else None   # a listed release whose tag is gone: unknown
    else:
        count = ahead(repo, None, sha)
    attempts = release_publish.ledger(name)["attempts"]
    return {"remote": name, "sha": sha, "last": prior and prior["tag_name"], "ahead": count, "versions": versions,
            "refused": refused, "attempt": attempts[-1] if attempts else None, "locked": LOCKED}


def ahead(repo: Path, since: str | None, sha: str) -> int | None:
    """Commits in `sha` that `since` lacks, or every commit before a first release. None when this clone does
    not have the tag's commit."""

    span = f"{since}..{sha}" if since else sha
    try:
        return int(release.git(repo, "rev-list", "--count", span).strip())
    except (ValueError, subprocess.SubprocessError):
        return None


def summary(result: dict) -> dict:
    """The rehearsal's outcome as the card shows it: each build's version and what was refused, the two
    refusals, the repair task a source failure went to, and any checkout left behind. The facts stay out."""

    rounds = [{"version": r["version"], **({"failed": r["failed"]} if "failed" in r else {}),
               "refused": r.get("refused") or []} for r in result["rounds"]]
    refusals = {k: len(v) for k, v in result["refusals"].items()}
    return {"ok": result["ok"], "rounds": rounds, "refusals": refusals, "repair": result.get("repair"),
            "left": result.get("left") or []}


def _repairing(name: str, halt: threading.Event):
    """`release_repair.start` behind one decision made under `_lock`: either the person's stop came first and no
    repair starts, or the repair starts and from then on the run can no longer be stopped (`stop` refuses)."""

    def start(repo: Path, record: dict) -> str:
        with _lock:
            if halt.is_set():
                raise RuntimeError("멈춤 — 사람이 시험 빌드를 멈춰서 수정 작업을 시작하지 않았다")
            _runs[name]["repairing"] = True
        return release_repair.start(repo, record)

    return start


def _rehearsing(name: str, repo: Path, halt: threading.Event) -> None:
    try:
        result = release_rehearsal.rehearse(repo, REF, halt, repair=_repairing(name, halt))
        done = {"state": "done", **summary(result)}
    except Exception as exc:   # noqa: BLE001 — the card shows why; the server goes on
        errorlog.record("release-rehearse", exc, repo=str(repo))
        done = {"state": "failed", "error": f"{type(exc).__name__}: {exc}"}
    with _lock:
        _runs[name] = {**_runs[name], **done, "ended": time.time(), "stopped": halt.is_set()}
        for key in ("halt", "thread", "repairing"):
            _runs[name].pop(key, None)


def shown(name: str) -> dict | None:
    with _lock:
        run = _runs.get(name)
        return run and {k: v for k, v in run.items() if k not in ("halt", "thread")}


@router.get("/api/release/{name}")
def get_card(name: str) -> dict:
    repo = _repo(name)
    try:
        out = card(repo)
    except (ValueError, OSError, subprocess.SubprocessError, gh.Failed) as exc:
        out = {"error": str(exc) or type(exc).__name__, "locked": LOCKED}
    return {**out, "rehearsal": shown(name)}


@router.get("/api/release/{name}/rehearsal")
def get_rehearsal(name: str) -> dict:
    """The test build's state alone, for the card to follow while it runs, without reading GitHub again."""

    _repo(name)
    return {"rehearsal": shown(name)}


@router.post("/api/release/{name}/rehearse")
def rehearse(name: str) -> dict:
    repo = _repo(name)
    with _lock:
        if (_runs.get(name) or {}).get("state") == "running":
            raise HTTPException(409, f"{name} 의 시험 빌드가 이미 돌고 있다")
        halt = threading.Event()
        worker = threading.Thread(target=_rehearsing, args=(name, repo, halt), daemon=True)
        _runs[name] = {"state": "running", "started": time.time(), "halt": halt, "thread": worker}
        worker.start()
    return {"rehearsal": shown(name)}


@router.post("/api/release/{name}/stop")
def stop(name: str) -> dict:
    with _lock:
        run = _runs.get(name)
        if not run or run.get("state") != "running":
            raise HTTPException(409, f"{name} 에 돌고 있는 시험 빌드가 없다")
        if run.get("repairing"):
            raise HTTPException(409, "수정 작업을 이미 만들기 시작해서 시험 빌드를 멈출 수 없다 — 작업 목록에서 본다")
        run["halt"].set()
    return {"rehearsal": shown(name)}


def close_all() -> None:
    """At server shutdown: every running test build stopped, and waited for while it removes its checkout and
    its test keys, so a restart leaves neither behind."""

    with _lock:
        running = [run for run in _runs.values() if run.get("state") == "running"]
        for run in running:
            run["halt"].set()
    deadline = time.monotonic() + SHUTDOWN_WAIT
    for run in running:
        run["thread"].join(max(0.0, deadline - time.monotonic()))


def recover() -> None:
    """Every pending attempt carried on at server start (`release_publish.recover`), off the start path:
    each one may wait on GitHub. A press already authorized it; nothing new is published."""

    def sweep() -> None:
        try:
            for row in release_publish.recover():
                if row.get("last_error"):
                    errorlog.record("release-recover", RuntimeError(row["last_error"]), remote=row["remote"])
        except Exception as exc:   # noqa: BLE001 — a failed sweep is recorded; the server goes on
            errorlog.record("release-recover", exc)

    threading.Thread(target=sweep, daemon=True).start()
