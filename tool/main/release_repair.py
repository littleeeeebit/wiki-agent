"""Release step 4 (`docs/plans/release/0-overview.md`, "When an attempt fails"). A release attempt whose build or
checks failed in a way a source change can fix becomes an ordinary task. Its spec is made here and started the
way `[시작]` starts one, so the agent works on its own branch, and its pull request goes through the ordinary
review loop. The release attempt has already ended as failed. Publishing the merged fix is a new press, because
one press authorizes only the commit it pinned.

The repair agent never gets the release key or its password: the spec carries only the failure. The press's
release checkout is gone by then, and the agent works in a task worktree forked from the repository's branch."""

from __future__ import annotations

import time
from pathlib import Path

from . import channels, specs

EVIDENCE = 6000   # characters of the failure the spec carries; the build log's tail is already cut to 40 lines


def spec_for(repo: Path, record: dict, gate: str) -> dict:
    """The repair task's spec: the failed attempt's facts, what is out of bounds, and the repository's gate."""

    failure = record["repair"]["needed"][-EVIDENCE:]
    block = {
        "goal": f"Fix what made the release build of {record['tag']} fail, so the next release press of "
                f"`main` builds and passes its checks",
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
    """The repair task for `record`, saved and started. Its ID is returned once the spec is saved: when the
    start is refused (another turn holds the repository, a review owns its checkout), the spec stays organized
    for `[시작]` and the refusal is raised for the ledger to record."""

    known = channels.repo_for(repo.name)   # the turn's end looks the task's project up by name
    if known is None or known.resolve() != repo.resolve():
        raise ValueError(f"`{repo.name}` 은 이 서버가 아는 프로젝트가 아니다 — 수정 작업이 끝날 곳이 없다")
    gate = specs.gate_of(repo)
    if not gate:
        raise ValueError("연결 먼저 — 이 저장소의 `.wiki/adapter.toml` 에 `gate_cmd` 가 없다")
    fields = spec_for(repo, record, gate)
    now = time.time()
    with specs._files:
        sid = specs.unique(repo, f"release-fix-{record['version'].replace('.', '-')}")
        specs.save({"id": sid, "repo": repo.name, "rev": 1, **fields,
                    "source": {"focus": "release", "remote": record["remote"], "attempt": record["id"],
                               "turn": now, "plan": None},
                    "state": "정리됨", "stopped": None, "worktree": None, "pr": None, "report": None, "gate": None,
                    "fault": None, "history": [{"ts": now, "state": "정리됨"}]})
    try:
        specs.started(repo, sid, specs.Start())
    except Exception as exc:
        raise Started(sid, exc) from exc
    return sid


class Started(Exception):
    """The spec was saved as `task`, but its start was refused."""

    def __init__(self, task: str, cause: Exception):
        super().__init__(f"{task}: {getattr(cause, 'detail', None) or cause}")
        self.task = task
