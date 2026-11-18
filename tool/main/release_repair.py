"""Release step 4 (`docs/plans/release/0-overview.md`, "When an attempt fails"). A release attempt whose build or
checks failed in a way a source change can fix becomes an ordinary task. Its spec is made here, and its first
turn is started as `[시작]` starts one. The agent works on its own branch in a linked worktree at the commit the
attempt pinned, never in the person's checkout, and its pull request goes through the ordinary review loop. The
release attempt has already ended as failed. Publishing the merged fix is a new press, because one press
authorizes only the commit it pinned.

The repair agent never gets the release key or its password. The spec carries only the failure, with secrets
redacted, and the build that produced it ran without GitHub tokens or the key password in its environment."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from common import errorlog, host
from workspace import checkout, remove

from . import channels, specs, work

EVIDENCE = 6000   # characters of the failure the spec carries; the build log's tail is already cut to 40 lines


def spec_for(repo: Path, record: dict, gate: str) -> dict:
    """The repair task's spec: the failed attempt's facts, what is out of bounds, and the repository's gate."""

    failure = errorlog.redact(record["repair"]["needed"])[-EVIDENCE:]   # a build log can echo a token
    block = {
        "goal": f"Fix what made the release build of {record['tag']} fail, so the next release press of "
                f"`{record.get('base') or 'main'}` builds and passes its checks",
        "out": ["Version files in the source: the release injects the version at build time and never edits them",
                "The release key, its password and signing: they are the server's and outside this task",
                "Publishing: merging the fix and pressing release again are the person's"],
        "done": [],
        "grounds": {"pages": [], "files": [".wiki/adapter.toml"], "rules": []},
        "decisions": [{"what": f"The release attempt {record['id']} for {record['tag']} at {record['sha'][:12]} "
                               f"failed: {failure}",
                       "why": "The release step builds the adapter's `[release.android]` command with "
                              "RELEASE_VERSION, RELEASE_VERSION_CODE and RELEASE_TAURI_CONFIG set, then checks the "
                              "APK's versionName, versionCode, application ID, ABI and that it is not debuggable",
                       "rejected": "Retrying the same commit: a press publishes only the commit it pinned"}],
    }
    return specs.fields(repo, block, gate)


def start(repo: Path, record: dict) -> str:
    """The repair task for `record`: a linked worktree on a new branch at the commit the attempt pinned, its
    spec, and its first turn. The person's checkout is never switched, and nothing in it is carried over. The
    pull request goes to the branch the attempt pinned. The ID is returned once the spec is saved: a turn that
    cannot start raises `Started`, and the task waits in its worktree for the person."""

    known = channels.repo_for(repo.name)   # the turn's end looks the task's project up by name
    if known is None or known.resolve() != repo.resolve():
        raise ValueError(f"`{repo.name}` 은 이 서버가 아는 프로젝트가 아니다 — 수정 작업이 끝날 곳이 없다")
    top, common, _ = checkout(repo)
    if not top or top != common:   # a task worktree is made beside the original checkout only
        raise ValueError(f"`{repo}` 는 연결된 작업트리라 수정 작업을 만들 수 없다 — 원본 체크아웃을 프로젝트로 골라라")
    now = time.time()
    # `_lock` before `_files`, the order `specs.started` takes them in. The hold is taken before the spec is
    # saved, so no other request can take the new worktree before its first turn does.
    with specs._lock, specs._files:
        sid = specs.unique(repo, f"release-fix-{record['version'].replace('.', '-')}")
        path = specs.create(repo, sid, linked=True, base=record["sha"])
        release = lambda: None   # noqa: E731 — until the hold below is taken
        try:
            release = specs.hold(work._busy, specs._lock, str(path), "그 작업트리를 다른 요청이 쓰고 있다", kind="turn")
            gate = specs.gate_of(repo, path)   # the pinned commit's gate, not the person's branch's
            if not gate:
                raise ValueError("연결 먼저 — 고정한 커밋의 `.wiki/adapter.toml` 에 `gate_cmd` 가 없다")
            specs.save({"id": sid, "repo": repo.name, "rev": 1, **spec_for(repo, record, gate),
                        "source": {"focus": "release", "remote": record["remote"], "attempt": record["id"],
                                   "turn": now, "plan": None},
                        "state": "작업 중", "stopped": None, "worktree": str(path), "branch": sid,
                        "base": record.get("base") or "main", "return_branch": record.get("base") or "main",
                        "start_head": record["sha"], "pr": None, "report": None, "gate": None, "fault": None,
                        "cell": {"model": "", "effort": "", "fast": False},
                        "history": [{"ts": now, "state": "정리됨"}, {"ts": now, "state": "작업 중"}]})
        except BaseException:
            release()
            undo(repo, path, sid, record["sha"])
            raise
    try:
        try:
            work.forget(path)
            chat = work.session(path, "", "", False)   # the screen's work model takes over from the next turn
            work.begin(path, chat, "Start.", release, work.Run(chat, task=True), decide=True)
        except BaseException:
            release()
            raise
    except Exception as exc:
        raise Started(sid, exc) from exc
    return sid


def unshared(spec: dict | None) -> dict:
    """`work.session`'s `ChatSession` arguments for `spec`: a release repair's agent starts without the server's
    GitHub tokens, every time its session is made, since the server publishes its pull request."""

    return {"env": dict.fromkeys(host.TOKENS)} if ((spec or {}).get("source") or {}).get("focus") == "release" else {}


def undo(repo: Path, path: Path, branch: str, sha: str) -> None:
    """The worktree and branch of a task whose spec was never saved: nothing would own them. The branch goes only
    while it still points at the commit it was made at."""

    remove(repo, path, force=True, keep_branch=True)
    subprocess.run(["git", "-C", str(repo), "update-ref", "-d", f"refs/heads/{branch}", sha],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)


class Started(Exception):
    """The spec was saved as `task`, but its start was refused."""

    def __init__(self, task: str, cause: Exception):
        super().__init__(f"{task}: {getattr(cause, 'detail', None) or cause}")
        self.task = task
