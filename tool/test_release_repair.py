"""Release step 4: a failed attempt becomes an ordinary task, started as `[시작]` starts one."""

from __future__ import annotations

import pytest

from main import release_repair, specs

RECORD = {"id": "20261118-120000-abcdef", "remote": "github.com/o/budget", "tag": "v0.1.1", "version": "0.1.1",
          "sha": "a" * 40, "repair": {"needed": "빌드 실패 — 1 로 끝났다\nerror: cannot find symbol Foo"}}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(specs, "SPECS", tmp_path / "specs")
    monkeypatch.setattr(specs, "gate_of", lambda repo, path=None: "python -m pytest -q")
    (repo := tmp_path / "budget").mkdir()
    monkeypatch.setattr(release_repair.channels, "repo_for", lambda name: repo if name == "budget" else None)
    return repo


def test_a_repair_task_carries_the_failure_and_starts_as_an_ordinary_task(repo, monkeypatch):
    started = []
    monkeypatch.setattr(specs, "started", lambda r, sid, body: started.append((r, sid, body)) or {})

    sid = release_repair.start(repo, RECORD)
    spec = specs.load("budget", sid)
    assert sid == "release-fix-0-1-1" and started == [(repo, sid, specs.Start())]
    assert spec["state"] == "정리됨" and spec["done"] == ["python -m pytest -q"]
    assert spec["source"] == {**spec["source"], "focus": "release", "remote": RECORD["remote"], "attempt": RECORD["id"]}
    told = specs.brief(spec)   # what the agent reads
    assert "cannot find symbol Foo" in told and "v0.1.1" in told and "Version files" in told
    assert "password" in told and "RELEASE_KEY_PASSWORD" not in told

    assert release_repair.start(repo, RECORD) == "release-fix-0-1-1-2"   # a second failure, a second task


def test_a_refused_start_keeps_the_task_for_the_person_and_names_it(repo, monkeypatch):
    def busy(r, sid, body):
        raise specs.HTTPException(409, "이 저장소에서 다른 작업이 돌고 있다")

    monkeypatch.setattr(specs, "started", busy)
    with pytest.raises(release_repair.Started) as refused:
        release_repair.start(repo, RECORD)
    assert refused.value.task == "release-fix-0-1-1" and "다른 작업" in str(refused.value)
    assert specs.load("budget", "release-fix-0-1-1")["state"] == "정리됨"


@pytest.mark.parametrize("known", ["nothing", "another folder"])
def test_a_repository_the_server_does_not_know_gets_no_task(repo, monkeypatch, tmp_path, known):
    # The turn's end finds the task's project by name; unknown, the task would sit in `작업 중` forever.
    monkeypatch.setattr(release_repair.channels, "repo_for", lambda name: None if known == "nothing" else tmp_path)
    with pytest.raises(ValueError, match="아는 프로젝트가 아니다"):
        release_repair.start(repo, RECORD)
    assert not (specs.SPECS / "budget").exists()


def test_a_repository_without_a_gate_gets_no_task(repo, monkeypatch):
    monkeypatch.setattr(specs, "gate_of", lambda repo, path=None: "")
    with pytest.raises(ValueError, match="gate_cmd"):
        release_repair.start(repo, RECORD)
    assert not (specs.SPECS / "budget").exists()
