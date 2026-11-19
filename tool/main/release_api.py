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

import secrets
import subprocess
import threading
import time
from pathlib import Path

from common import errorlog
from fastapi import APIRouter, HTTPException

from . import channels, release, release_publish, release_rehearsal, release_repair, runtime
from . import github_release as gh

router = APIRouter()

LOCKED = ("실제 게시는 잠겨 있다 — 빌드와 수정 작업이 서명 키와 같은 Windows 계정에서 돌아서, "
          "다른 계정으로 분리한 뒤에 켠다. 지금은 시험 빌드만 한다(아무것도 올리지 않는다)")
REF = "origin/main"   # what the card pins and what the test build builds
SHUTDOWN_WAIT = 120   # seconds a closing server gives a stopped test build to remove its checkout and keys

_runs: dict[str, dict] = {}   # project name -> its rehearsal: the running one, or the last one's summary
_lock = threading.Lock()
_deadline: list[float] = []   # when shutdown stops waiting: set by its first `close_all`, kept by any later one
# Every snapshot of a run carries `boot`, a fresh random name for each server lifespan, and `rev`, the run's
# change within it. Only `rev`s of one boot are compared; the card treats another boot as a new server and starts
# over, so no order between servers, clocks or stored counters is ever needed.
_boot = [secrets.token_hex(6)]   # renamed whenever a lifespan takes ownership (`recover`)
_revs: dict[str, int] = {}   # project name -> the last `rev` its runs were given in this process


def _rev(name: str) -> int:
    """The next `rev` of `name`'s runs, stamped on every change of one, so the card keeps the newest state
    whatever order answers arrive in. Called under `_lock`."""

    _revs[name] = _revs.get(name, 0) + 1
    return _revs[name]


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
    return {"ok": result["ok"], "sha": result.get("sha"), "rounds": rounds, "refusals": refusals,
            "repair": result.get("repair"), "left": result.get("left") or []}


def _repairing(name: str, halt: threading.Event):
    """`release_repair.start` behind one decision made under `_lock`: either the person's stop came first and no
    repair starts, or the repair starts and from then on the run can no longer be stopped (`stop` refuses)."""

    def start(repo: Path, record: dict) -> str:
        with _lock:
            if halt.is_set():
                raise RuntimeError("멈춤 — 사람이 시험 빌드를 멈춰서 수정 작업을 시작하지 않았다")
            _runs[name].update(repairing=True, rev=_rev(name))
        return release_repair.start(repo, record)

    return start


def _rehearsing(name: str, repo: Path, halt: threading.Event, sha: str | None = None) -> None:
    try:
        result = release_rehearsal.rehearse(repo, REF, halt, repair=_repairing(name, halt), expect=sha)
        done = {"state": "done", **summary(result)}
    except Exception as exc:   # noqa: BLE001 — the card shows why; the server goes on
        errorlog.record("release-rehearse", exc, repo=str(repo))
        done = {"state": "failed", "error": f"{type(exc).__name__}: {exc}", "left": getattr(exc, "left", [])}
    with _lock:
        _runs[name] = {**_runs[name], **done, "ended": time.time(), "stopped": halt.is_set(), "rev": _rev(name)}
        for key in ("halt", "thread", "repairing", "stopping"):
            _runs[name].pop(key, None)


def shown(name: str) -> dict:
    """The run as the card sees it, or `idle` before this server ran one, always with its `rev` and `boot`."""

    with _lock:
        run = _runs.get(name) or {"state": "idle", "rev": _revs.get(name, 0)}
        return {**{k: v for k, v in run.items() if k not in ("halt", "thread")}, "boot": _boot[0]}


@router.get("/api/release/{name}")
def get_card(name: str) -> dict:
    """The card, with `read`: the `rev` of `name`'s runs when its reading began. A run that ended after it may
    have seen another `main`, so the card is read again; the run snapshot attached at the end says nothing about
    when the preview's own inputs were read."""

    repo = _repo(name)
    with _lock:
        read = _revs.get(name, 0)
    try:
        out = card(repo)
    except (ValueError, OSError, subprocess.SubprocessError, gh.Failed) as exc:
        out = {"error": str(exc) or type(exc).__name__, "locked": LOCKED}
    return {**out, "read": read, "rehearsal": shown(name)}


@router.get("/api/release/{name}/rehearsal")
def get_rehearsal(name: str) -> dict:
    """The test build's state alone, for the card to follow while it runs, without reading GitHub again."""

    _repo(name)
    return {"rehearsal": shown(name)}


@router.post("/api/release/{name}/rehearse")
def rehearse(name: str, sha: str | None = None) -> dict:
    """One test build of `REF`. `sha` is the commit the card showed; a `REF` that has moved since is refused."""

    repo = _repo(name)
    with _lock:   # the same lock `close_all` snapshots under, so no start slips in after it
        if runtime.stopping.is_set():
            raise HTTPException(503, "서버가 꺼지는 중이라 시험 빌드를 시작하지 않는다")
        prior = _runs.get(name)
        if (prior or {}).get("state") == "running":
            raise HTTPException(409, f"{name} 의 시험 빌드가 이미 돌고 있다")
        halt = threading.Event()
        worker = threading.Thread(target=_rehearsing, args=(name, repo, halt, sha), daemon=True)
        _runs[name] = {"state": "running", "id": secrets.token_hex(6), "started": time.time(), "halt": halt,
                       "thread": worker, "rev": _rev(name)}
        try:
            worker.start()
        except RuntimeError as exc:   # the OS refused a thread: the project is not left looking busy
            _runs.pop(name)
            if prior:
                _runs[name] = prior
            raise HTTPException(503, f"시험 빌드를 시작하지 못했다 — {exc}") from exc
    return {"rehearsal": shown(name)}


@router.post("/api/release/{name}/stop")
def stop(name: str, run_id: str = "") -> dict:
    """Stop the running test build named by `run_id`, the one the person saw: a stop that arrives after that build
    ended, or names none, never stops another."""

    with _lock:
        run = _runs.get(name)
        if not run or run.get("state") != "running":
            raise HTTPException(409, f"{name} 에 돌고 있는 시험 빌드가 없다")
        if not run_id:
            raise HTTPException(409, "멈출 시험 빌드를 밝히지 않았다 — 카드를 다시 열고 멈춘다")
        if run_id != run["id"]:
            raise HTTPException(409, "멈추려던 시험 빌드는 이미 끝났다 — 지금 도는 것은 다른 시험 빌드다")
        if run.get("repairing"):
            raise HTTPException(409, "수정 작업을 이미 만들기 시작해서 시험 빌드를 멈출 수 없다 — 작업 목록에서 본다")
        run["halt"].set()
        run.update(stopping=True, rev=_rev(name))
    return {"rehearsal": shown(name)}


def close_all() -> None:
    """At server shutdown: every running test build stopped, and waited for while it removes its checkout and
    its test keys, so a restart leaves neither behind. Its commands end within a second of the stop
    (`release.stoppable`). A build whose repair has begun is not stopped: the repair is let finish starting.
    One wait for every call, and a build still running when it ends is recorded."""

    with _lock:   # after `runtime.stopping`, so no start is admitted past this snapshot
        running = [(name, run) for name, run in _runs.items() if run.get("state") == "running"]
        for _, run in running:
            if not run.get("repairing"):
                run["halt"].set()
        if not _deadline:
            _deadline.append(time.monotonic() + SHUTDOWN_WAIT)
    for name, run in running:
        run["thread"].join(max(0.0, _deadline[0] - time.monotonic()))
        if run["thread"].is_alive():
            errorlog.record("release-rehearse-shutdown", TimeoutError(
                f"{name} 의 시험 빌드가 서버 종료 대기 {SHUTDOWN_WAIT}초 안에 끝나지 않았다 — "
                f"{release.WORK} 아래에 빌드 폴더나 시험 키가 남았을 수 있다"), project=name)


def recover() -> None:
    """Every pending attempt carried on at server start (`release_publish.recover`), off the start path:
    each one may wait on GitHub. A press already authorized it; nothing new is published.

    Called once each time a server lifespan takes ownership, so that lifespan's shutdown gets its own whole
    wait (`close_all`), never one an earlier lifespan in this process spent."""

    with _lock:
        _deadline.clear()
        _boot[0] = secrets.token_hex(6)

    def sweep() -> None:
        try:
            for row in release_publish.recover():
                if row.get("last_error"):
                    errorlog.record("release-recover", RuntimeError(row["last_error"]), remote=row["remote"])
        except Exception as exc:   # noqa: BLE001 — a failed sweep is recorded; the server goes on
            errorlog.record("release-recover", exc)

    threading.Thread(target=sweep, daemon=True).start()
