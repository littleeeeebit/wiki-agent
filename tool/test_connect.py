"""Connecting a repository: the state a row shows, the real-session test, the
hub move behind a confirmation, `[연결]`'s guesses, the survey's limits and
bounds, and the handover of the original's adapter after the survey merges.

GitHub is a stand-in behind `specs.sh`; git is real. The remote a handover
fetches is a bare repository that `url.<bare>.insteadOf` puts behind the
GitHub address the pull request names.
"""

import ast
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import apply
import setup_agents
from agent.chat_session import Event
from main import channels as chat_channels
from main import connect, loop, specs, survey, work
from main import query as chat
from test_main import _repo, client, no_machine_settings  # noqa: F401 — the fixture is autouse

HERE = Path(__file__).resolve().parent
URL = "https://github.com/o/proj"
ALL = {"claude": True, "codex": True}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


@pytest.fixture(autouse=True)
def records(tmp_path):
    with patch.object(connect, "RECORDS", tmp_path / "connect"), \
         patch.object(survey, "RATES", tmp_path / "connect" / "survey" / "rates.json"):
        yield


def connected(repo: Path) -> None:
    """Everything a row checks, in place: a tracked adapter with its gate, both
    hosts tested, Codex trusted."""

    (repo / ".wiki").mkdir(exist_ok=True)
    (repo / ".wiki/adapter.toml").write_text(connect.adapter_text({
        "gate_cmd": "git --version", "review_dir": "r", "scratch_dirs": "s", "live_cmd": "l", "server_stop": "x"}),
        encoding="utf-8")
    git(repo, "add", ".wiki/adapter.toml")
    git(repo, "commit", "-qm", "adapter")
    ok = {"ok": True, "ts": time.time(), "detail": ""}
    connect.keep(repo.name, probe={"claude": ok, "codex": ok}, trust=True)


# -- the state of a row ------------------------------------------------------------


def test_each_missing_item_makes_the_row_partial_and_is_named(tmp_path):
    repo = _repo(tmp_path)
    assert connect.status(repo, ALL)["state"] == "미연결"
    connected(repo)
    assert connect.status(repo, ALL) | {"survey": None} == {
        "state": "연결 완료", "missing": [], "notes": [], "probing": False, "survey": None}

    def missing(**wired) -> list[str]:
        found = connect.status(repo, {**ALL, **wired})
        assert found["state"] == ("일부" if found["missing"] else "연결 완료")
        return found["missing"]

    assert missing(claude=False) == ["사용자 단위 hook 없음 (Claude)"]
    assert missing(codex=False) == ["사용자 단위 hook 없음 (Codex)"]

    old = {}
    apply.configure(old, repo, "proj", sys.executable, "claude")   # the per-project install of before
    (repo / ".claude").mkdir()
    (repo / ".claude/settings.json").write_text(json.dumps(old), encoding="utf-8")
    assert missing() == ["옛 hook 남음 (Claude)"]
    (repo / ".claude/settings.json").unlink()

    connect.keep(repo.name, probe={"claude": {"ok": False}, "codex": {"ok": True}})
    assert missing() == ["Claude 시험 실패"]
    connect.keep(repo.name, probe={"claude": {"ok": True}})
    assert missing() == ["Codex 시험 안 함"]
    connect.keep(repo.name, probe={"claude": {"ok": True}, "codex": {"ok": True}}, trust=False)
    assert missing() == ["Codex 신뢰 대기"]
    connect.keep(repo.name, trust=True, handover={"state": "대기", "reason": "머지 커밋을 아직 모른다"})
    assert missing() == ["adapter 반영 대기 — 머지 커밋을 아직 모른다"]
    connect.keep(repo.name, handover=None)

    (repo / ".wiki/adapter.toml").write_text('[slots]\nlive_cmd = "l"\n', encoding="utf-8")
    found = connect.status(repo, ALL)
    assert found["missing"] == ["gate_cmd 빈 칸"]
    assert found["notes"] == ["채워야 함 — review_dir, scratch_dirs, server_stop"], "빈 선택 슬롯은 알림이다"


def test_the_listing_carries_the_state_instead_of_wired_always(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    repo = _repo(workspace)
    with patch.object(chat_channels, "WORKSPACE", workspace), patch.object(connect, "users", return_value=ALL):
        rows = {r["id"]: r for r in chat_channels.projects()}
        assert rows["proj"]["state"] == "미연결" and not rows["proj"]["wired"]
        connected(repo)
        rows = {r["id"]: r for r in chat_channels.projects()}
        assert rows["proj"]["state"] == "연결 완료" and rows["proj"]["wired"]
        assert chat_channels.WIKI.name in rows


# -- the guesses ----------------------------------------------------------------------


@pytest.mark.parametrize("files, gate", [
    ({"pyproject.toml": ""}, "python -m pytest"),
    ({"setup.cfg": ""}, "python -m pytest"),
    ({"package.json": json.dumps({"scripts": {"test": "vitest"}})}, "npm test"),
    ({"Cargo.toml": ""}, "cargo test"),
    ({"go.mod": ""}, "go test ./..."),
    ({"pytest.ini": "", "package.json": json.dumps({"scripts": {"test": "x"}})}, "python -m pytest"),
    ({"package.json": json.dumps({"scripts": {"build": "x"}})}, ""),
    ({}, ""),
])
def test_the_gate_is_guessed_from_the_first_file_that_says(tmp_path, files, gate):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    slots = connect.guess(tmp_path)
    assert slots["gate_cmd"] == gate
    assert (slots["live_cmd"], slots["server_stop"]) == ("", ""), "추정하지 않는다"
    assert slots["review_dir"] == "artifacts/review" and slots["scratch_dirs"] == "artifacts/"


def test_the_adapter_is_written_once_and_its_hash_kept(tmp_path):
    repo = _repo(tmp_path)
    (repo / "go.mod").write_text("module x\n", encoding="utf-8")
    assert connect.write_adapter(repo)
    text = (repo / ".wiki/adapter.toml").read_bytes()
    assert connect.record("proj")["hash"] == connect.digest(text)
    assert 'gate_cmd = "go test ./..."' in text.decode()
    assert specs._slots(repo).splitlines() == ["- `live_cmd` in `.wiki/adapter.toml`",
                                               "- `server_stop` in `.wiki/adapter.toml`"], "다음 작업 후보의 재료"
    (repo / ".wiki/adapter.toml").write_text("mine\n", encoding="utf-8")
    assert not connect.write_adapter(repo), "있는 adapter 는 덮지 않는다"
    assert (repo / ".wiki/adapter.toml").read_text(encoding="utf-8") == "mine\n"


# -- the session test -----------------------------------------------------------------


def hook_runner(event: str = "SessionStart"):
    """A stand-in CLI that starts the hook the way a host would: a child
    process that inherits the environment."""

    def run(args, cwd, env, timeout):
        return subprocess.run([sys.executable, str(HERE / "hook.py"), "claude", "session_state.py"],
                              input=json.dumps({"cwd": str(cwd), "hook_event_name": event}), env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    return run


def test_a_host_passes_only_when_its_own_session_start_injected(tmp_path):
    repo = _repo(tmp_path)
    connected(repo)
    before = set(connect.PROBES.glob("*")) if connect.PROBES.is_dir() else set()
    with patch.object(connect, "command", return_value=["cli"]):
        with patch.object(connect, "spawn", hook_runner()):
            assert connect.probe(repo, "claude")["ok"]
        quiet = lambda args, cwd, env, timeout: subprocess.CompletedProcess(args, 0, "OK", "")  # noqa: E731
        with patch.object(connect, "spawn", quiet):
            failed = connect.probe(repo, "claude")
            assert not failed["ok"] and failed["detail"] == "위키 hook 이 불리지 않았다"
        with patch.object(connect, "spawn", hook_runner("UserPromptSubmit")):
            assert not connect.probe(repo, "claude")["ok"], "SessionStart 의 주입만 센다"
        (repo / ".wiki/adapter.toml").unlink()   # not attached: the hook passes silently
        with patch.object(connect, "spawn", hook_runner()):
            assert not connect.probe(repo, "claude")["ok"]
    after = set(connect.PROBES.glob("*")) if connect.PROBES.is_dir() else set()
    assert after == before, "시험의 흔적은 읽은 뒤 지운다"


def test_the_hook_writes_nothing_without_the_variable_and_passes_when_it_cannot_write(tmp_path):
    repo = _repo(tmp_path)
    connected(repo)
    payload = json.dumps({"cwd": str(repo), "hook_event_name": "SessionStart"})
    env = {k: v for k, v in os.environ.items() if k != "WIKI_PROBE"}
    before = set(connect.PROBES.glob("*")) if connect.PROBES.is_dir() else set()
    done = subprocess.run([sys.executable, str(HERE / "hook.py"), "claude", "session_state.py"], input=payload,
                          env=env, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0 and "additionalContext" in done.stdout
    assert (set(connect.PROBES.glob("*")) if connect.PROBES.is_dir() else set()) == before

    nonce = "0123456789abcdef"
    blocked = connect.PROBES / f"{nonce}.jsonl"
    blocked.mkdir(parents=True)   # a directory where the file goes: the write fails
    try:
        done = subprocess.run([sys.executable, str(HERE / "hook.py"), "claude", "session_state.py"], input=payload,
                              env={**env, "WIKI_PROBE": nonce}, capture_output=True, text=True, encoding="utf-8")
        assert done.returncode == 0 and "additionalContext" in done.stdout, "기록을 못 써도 hook 은 성공한다"
    finally:
        blocked.rmdir()


# -- the hub move ------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The user level, empty, and an old hub whose skill links point into it.
    The CLIs' version checks and Codex's `app-server` are stood in for, and
    `Path.is_junction` is taken away: 3.11, which the README supports, has none."""

    monkeypatch.delattr(Path, "is_junction", raising=False)
    home = Path(apply._HOME)
    for kept in (home / ".claude", home / ".codex"):
        if kept.exists():
            import shutil

            shutil.rmtree(kept)
    old = tmp_path / "old-hub"
    (old / "tool").mkdir(parents=True)
    (old / "tool/hook.py").write_text("", encoding="utf-8")
    for name in ("after-merge", "gone-here"):
        (old / "skills" / name).mkdir(parents=True)
    (home / ".claude/skills").mkdir(parents=True)
    for name in ("after-merge", "gone-here"):
        link = home / ".claude/skills" / name
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(old / "skills" / name), str(link))
        else:
            os.symlink(old / "skills" / name, link, target_is_directory=True)
    trusted = []
    with patch.object(setup_agents, "run", return_value=""), patch.object(apply, "unusable", return_value=[]), \
         patch.object(setup_agents, "hook_shell", return_value=["sh"]), \
         patch.object(setup_agents.shutil, "which", side_effect=lambda name: name), \
         patch.object(setup_agents, "codex_hooks", side_effect=lambda home, trust, python=None: trusted.append(trust)), \
         patch.object(connect, "trusted", return_value=False), \
         patch.object(connect, "examine", return_value=True):
        yield home, old, trusted


def test_off_windows_nothing_is_a_junction(tmp_path, monkeypatch):
    import stat

    # CPython defines the reparse tags only on Windows.
    monkeypatch.delattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", raising=False)
    assert setup_agents.is_junction(tmp_path) is False


def test_the_hub_move_lists_every_line_before_writing_and_writes_nothing_unconfirmed(tmp_path, home):
    home, old, trusted = home
    workspace = tmp_path / "ws"
    workspace.mkdir()
    repo = _repo(workspace)
    web = client()
    user = home / ".claude/settings.json"
    with patch.object(chat_channels, "WORKSPACE", workspace), patch.object(chat, "_project", "proj"):
        shown = web.get("/api/connect/proj/plan").json()["hub"]
        assert shown["needed"] and shown["trust"] and not shown["refused"]
        assert {Path(line["file"]).name for line in shown["lines"]} == {"settings.json", "hooks.json"}
        assert [(Path(k["link"]).name, k["to"] and Path(k["to"]).parent) for k in shown["links"]] == [
            ("after-merge", chat_channels.WIKI / "skills"), ("gone-here", None)]
        assert not user.exists(), "보이기만 했다"

        headers = {"x-project": "proj"}
        refused = web.post("/api/connect/proj", json={}, headers=headers)
        assert refused.status_code == 409 and "허브 이전" in refused.json()["detail"]
        stale = web.post("/api/connect/proj", json={"hub": "0" * 64}, headers=headers)
        assert stale.status_code == 409
        assert not user.exists() and not (repo / ".wiki/adapter.toml").exists(), "확인 없이는 아무것도 바뀌지 않는다"

        done = web.post("/api/connect/proj", json={"hub": shown["digest"]}, headers=headers)
        assert done.status_code == 200, done.text
    written = {line["change"] for line in shown["lines"] if Path(line["file"]) == user}
    assert written and written == set(apply.configure({}, None, None, sys.executable, "claude")), \
        "확인 창의 줄이 실제로 바뀐 줄이다"
    assert apply.user_wired("claude")
    assert Path(os.readlink(home / ".claude/skills/after-merge").removeprefix("\\\\?\\")) == \
        chat_channels.WIKI / "skills/after-merge"
    assert os.name != "nt" or setup_agents.is_junction(home / ".claude/skills/after-merge"), "정션은 정션으로 남는다"
    assert Path(os.readlink(home / ".claude/skills/gone-here").removeprefix("\\\\?\\")) == old / "skills/gone-here"
    assert trusted == [True], "Codex 신뢰는 확인한 창에서만 쓴다"
    assert (repo / ".wiki/adapter.toml").is_file()


# -- the survey --------------------------------------------------------------------------


class Spender:
    """A write session whose every turn reports `tokens` input tokens."""

    tokens = 0
    heard: list = []

    def __init__(self, path, model="", effort="", write=False, system=""):
        self.path, self.system = Path(path), system
        self.id, self.session_id, self.alive, self.parent_id = "s", None, True, None
        self.is_codex, self.rules = False, []

    def say(self, text, halt=None):
        Spender.heard.append(text)
        yield Event("done", "알겠다", {"session_id": "cli", "error": False, "tokens": {"in": Spender.tokens}}, self.id)

    def reconfigure(self, model, effort):
        pass

    def stop(self, halt):
        pass

    def close(self):
        pass


@pytest.fixture
def surveyed(tmp_path):
    repo = _repo(tmp_path)
    for name in ("src/a/x.py", "src/b/y.py", "README.md"):
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("print(1)\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "code")
    connect.write_adapter(repo)
    Spender.heard = []
    with patch.object(work, "ChatSession", Spender), \
         patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(specs, "sh", lambda args, cwd, timeout=60: subprocess.CompletedProcess(args, 1, "", "no gh")
                      if args[0] == "gh" else subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                                                             encoding="utf-8", timeout=timeout)):
        yield repo
    work.close_all()


def ended(name: str) -> dict:
    for _ in range(400):
        now = connect.record(name).get("survey") or {}
        if now.get("state") in ("끝", "멈춤", "실패"):
            return now
        time.sleep(0.05)
    raise AssertionError(f"조사가 끝나지 않았다 — {connect.record(name).get('survey')}")


def test_the_survey_sends_no_further_turn_once_the_tokens_reach_the_limit(surveyed):
    loop.store(survey=True, survey_tokens=10_000, survey_minutes=60, survey_model="opus")
    Spender.tokens = 50_000
    sid = survey.start(surveyed)
    assert sid == "wiki-bootstrap"
    now = ended("proj")
    assert now["state"] == "끝" and now["why"] == "토큰 한도", now
    assert len(Spender.heard) == 2, "첫 턴, 그리고 완료 보고뿐"
    assert "`.wiki/project.md`" in Spender.heard[0] and "token limit" in Spender.heard[1]
    spec = specs.load("proj", sid)
    path = Path(spec["worktree"])
    assert git(path, "log", "--format=%s", "-1") == "wiki: adapter", "원본의 adapter 가 첫 커밋이다"
    assert spec["survey"]["handover"] and spec["cell"]["model"] == "opus"
    assert "repo_lint.py" in spec["done"][0] and "--no-wiring" in spec["done"][0]


def test_every_turn_runs_under_the_limit_and_an_existing_page_is_skipped(surveyed):
    (surveyed / ".wiki/project.md").write_text("---\nscope: project\n---\n# 있다\n", encoding="utf-8")
    git(surveyed, "add", "-f", ".wiki/project.md")
    git(surveyed, "commit", "-qm", "page")
    loop.store(survey=True, survey_tokens=10_000_000, survey_minutes=60, survey_model="opus")
    Spender.tokens = 10
    survey.start(surveyed)
    assert ended("proj")["state"] == "끝"
    labels = [t for t in Spender.heard]
    assert not any("`.wiki/project.md`:" in t for t in labels), "있는 project.md 의 턴은 없다"
    assert any("src/a/" in t and "src/b/" in t for t in labels)
    assert len(Spender.heard) == 5   # modules, rules, decisions, slots, the report
    assert survey.rates()["factor"] > 0 and survey.RATES.is_file(), "끝까지 간 조사가 계수를 맞춘다"


def test_a_turn_waiting_on_a_person_stops_at_the_deadline(tmp_path):
    refused = HTTPException(409, "a person's turn")
    got = []
    with patch.object(chat, "hold", side_effect=refused):
        waiting = threading.Thread(target=lambda: got.append(survey.turn(tmp_path, {}, "x", time.time() + 0.3)),
                                   daemon=True)
        waiting.start()
        waiting.join(5)
    assert got == [("cut", 0, "시간 한도")], "사람의 턴을 기다리다 한도를 넘기지 않는다"


def test_no_turn_starts_once_the_deadline_has_passed(tmp_path):
    Spender.heard = []
    with patch.object(work, "ChatSession", Spender):
        assert survey.turn(tmp_path, {}, "x", time.time() - 1) == ("cut", 0, "시간 한도")
    assert Spender.heard == [], "한도를 지난 턴은 시작하지 않는다"


class Cutter(Spender):
    """The first turn commits a page, then works on until it is stopped."""

    def say(self, text, halt=None):
        Spender.heard.append(text)
        if len(Spender.heard) == 1:
            (self.path / ".wiki/late.md").write_text("# 늦음\n", encoding="utf-8")
            git(self.path, "add", "-f", ".wiki/late.md")
            git(self.path, "commit", "-qm", "late")
            while not halt.is_set():
                time.sleep(0.05)
        yield Event("done", "알겠다", {"session_id": "cli", "error": False, "tokens": {"in": 1}}, self.id)


def test_a_turn_cut_by_the_time_limit_leaves_nothing_it_committed(surveyed):
    # Two seconds: the settings screen keeps whole minutes, so the saved file cannot say it.
    limits = {**survey.DEFAULTS, "survey": True, "survey_tokens": 10_000_000, "survey_minutes": 2 / 60}
    with patch.object(work, "ChatSession", Cutter), patch.object(survey, "settings", return_value=limits):
        survey.start(surveyed)
        now = ended("proj")
    assert now["why"] == "시간 한도", now
    path = Path(specs.load("proj", "wiki-bootstrap")["worktree"])
    assert git(path, "log", "--format=%s", "-1") == "wiki: adapter", "끊긴 턴의 커밋은 HEAD 에 남지 않는다"
    assert not (path / ".wiki/late.md").exists()
    assert "late.md" in git(path, "stash", "show", "--include-untracked", "--name-only", "stash@{0}"), "stash 에 남는다"


def test_a_turn_that_touches_an_existing_file_or_code_is_put_back(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".wiki").mkdir()
    (repo / ".wiki/project.md").write_text("old\n", encoding="utf-8")
    (repo / ".wiki/adapter.toml").write_text("a\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    first = git(repo, "rev-parse", "HEAD")
    (repo / ".wiki/project.md").write_text("new\n", encoding="utf-8")
    (repo / ".wiki/adapter.toml").write_text("b\n", encoding="utf-8")
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (repo / "code.py").write_text("x\n", encoding="utf-8")
    (repo / ".wiki/modules").mkdir()
    (repo / ".wiki/modules/src.md").write_text("page\n", encoding="utf-8")
    with patch.object(specs, "sh", lambda args, cwd, timeout=60: subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, encoding="utf-8")):
        assert sorted(survey.settle(repo, first)) == [".wiki/project.md", "a.txt", "code.py"]
    assert (repo / ".wiki/project.md").read_text(encoding="utf-8") == "old\n", "있는 페이지는 그대로"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "a\n" and not (repo / "code.py").exists()
    assert (repo / ".wiki/adapter.toml").read_text(encoding="utf-8") == "b\n", "adapter 보정은 둔다"
    assert git(repo, "status", "--porcelain") == ""
    assert "modules/src.md" in git(repo, "ls-files", ".wiki")


def test_the_estimate_counts_what_it_reads_and_says_when_the_limit_is_under_it(tmp_path):
    repo = _repo(tmp_path)
    (repo / "big.py").write_text("x" * 40_000, encoding="utf-8")
    (repo / "package-lock.json").write_text("y" * 400_000, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "code")
    with patch.object(specs, "sh", lambda args, cwd, timeout=60: subprocess.CompletedProcess(
            args, 0, json.dumps([{"number": 1}] * 3), "") if args[0] == "gh" else subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, encoding="utf-8")):
        loop.store(survey_tokens=10_000_000, survey_minutes=600)
        guessed = survey.estimate(repo)
        assert guessed["prs"] == 3 and guessed["commits"] == 2 and guessed["files"] == 2, "잠금 파일은 뺀다"
        assert guessed["tokens"] >= 10_000 and not guessed["over"]
        loop.store(survey_tokens=10_000)
        assert survey.estimate(repo)["over"]


def test_sync_harvest_and_session_state_know_nothing_of_the_survey():
    for name in ("sync.py", "harvest.py", "session_state.py"):
        tree = ast.parse((HERE / name).read_text(encoding="utf-8"))
        imported = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names] + \
                   [f"{n.module}.{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                    for a in n.names]
        assert not [i for i in imported if "connect" in i or "survey" in i], (name, imported)
        assert "survey" not in (HERE / name).read_text(encoding="utf-8"), f"{name} 이 조사 설정을 읽는다"


# -- the handover ----------------------------------------------------------------------------


class Merged:
    """`gh pr view` of the survey's pull request."""

    def __init__(self, commit: str | None):
        self.commit, self.real = commit, specs.sh

    def __call__(self, args, cwd, timeout=60):
        if args[0] == "gh":
            view = {"baseRefName": "main", "url": f"{URL}/pull/7",
                    "mergeCommit": {"oid": self.commit} if self.commit else None}
            return subprocess.CompletedProcess(args, 0, json.dumps(view), "")
        return self.real(args, cwd, timeout)


@pytest.fixture
def original(tmp_path):
    """The original checkout, on `main`, tracking an `origin` that is GitHub's
    address in its config and a bare repository underneath; `[연결]` wrote
    its adapter. `merge(text)` lands the survey's squash on the remote."""

    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    repo = _repo(tmp_path)
    git(repo, "config", f"url.{bare.as_posix()}.insteadOf", URL)
    git(repo, "remote", "add", "origin", URL)
    git(repo, "push", "-q", "-u", "origin", "main")
    connect.write_adapter(repo)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    git(other, "config", "user.email", "o@o")
    git(other, "config", "user.name", "o")

    def merge(text: str | None = None, push: bool = True) -> str:
        (other / ".wiki").mkdir(exist_ok=True)
        (other / ".wiki/adapter.toml").write_text(text or (repo / ".wiki/adapter.toml").read_text(encoding="utf-8"),
                                                  encoding="utf-8", newline="\n")
        (other / ".wiki/project.md").write_text("page\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, "commit", "-qm", "squash #7")
        if push:
            git(other, "push", "-q", "origin", "main")
        return git(other, "rev-parse", "HEAD")

    return repo, merge, other


def box(repo: Path) -> list[Path]:
    folder = repo / ".git/wiki-connect"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def untouched(repo: Path, head: str, text: bytes) -> None:
    assert git(repo, "rev-parse", "HEAD") == head
    assert (repo / ".wiki/adapter.toml").read_bytes() == text
    assert box(repo) == []
    assert connect.record(repo.name)["handover"]["state"] == "대기"


def test_the_handover_moves_the_adapter_aside_fast_forwards_and_checks(original):
    repo, merge, _ = original
    commit = merge()
    with patch.object(specs, "sh", Merged(commit)):
        done = connect.handover(repo, 7)
    assert done["ok"], done
    assert git(repo, "rev-parse", "HEAD") == commit
    assert git(repo, "ls-files", ".wiki/adapter.toml") == ".wiki/adapter.toml"
    assert box(repo) == [] and connect.record("proj")["handover"]["state"] == "완료"


@pytest.mark.parametrize("breach", ["off base", "other remote", "dirty", "left copy"])
def test_the_first_two_steps_touch_nothing_when_a_condition_fails(original, breach):
    repo, merge, _ = original
    commit = merge()
    if breach == "off base":
        git(repo, "checkout", "-q", "-b", "elsewhere")
    elif breach == "other remote":
        git(repo, "remote", "set-url", "origin", "https://github.com/x/other")
    elif breach == "dirty":
        (repo / "b.txt").write_text("mine\n", encoding="utf-8")
    else:
        (repo / ".git/wiki-connect").mkdir()
        (repo / ".git/wiki-connect/1-adapter.toml").write_text("old\n", encoding="utf-8")
    head, text = git(repo, "rev-parse", "HEAD"), (repo / ".wiki/adapter.toml").read_bytes()
    with patch.object(specs, "sh", Merged(commit)):
        assert not connect.handover(repo, 7)["ok"]
    if breach == "left copy":
        assert git(repo, "rev-parse", "HEAD") == head and (repo / ".wiki/adapter.toml").read_bytes() == text
        assert len(box(repo)) == 1, "남은 사본 위로 옮기지 않는다"
        assert "앞선 넘기기의 사본" in connect.record("proj")["handover"]["reason"]
    else:
        untouched(repo, head, text)


def test_a_copy_a_person_changed_goes_back_and_the_difference_is_shown(original):
    repo, merge, _ = original
    commit = merge()
    (repo / ".wiki/adapter.toml").write_text('[slots]\ngate_cmd = "mine"\n', encoding="utf-8")
    head, text = git(repo, "rev-parse", "HEAD"), (repo / ".wiki/adapter.toml").read_bytes()
    with patch.object(specs, "sh", Merged(commit)):
        assert not connect.handover(repo, 7)["ok"]
    untouched(repo, head, text)
    assert '+gate_cmd = "mine"' in connect.record("proj")["handover"]["reason"]


def test_an_empty_merge_commit_waits_and_the_next_listing_finishes_it(original, tmp_path):
    repo, merge, _ = original
    commit = merge()
    head, text = git(repo, "rev-parse", "HEAD"), (repo / ".wiki/adapter.toml").read_bytes()
    with patch.object(specs, "sh", Merged(None)):
        assert not connect.handover(repo, 7)["ok"]
    untouched(repo, head, text)
    assert "머지 커밋을 아직 모른다" in connect.record("proj")["handover"]["reason"]

    workspace = repo.parent
    with patch.object(chat_channels, "WORKSPACE", workspace), patch.object(connect, "users", return_value=ALL), \
         patch.object(specs, "sh", Merged(commit)):
        connect.rows()
        assert connect.record("proj")["handover"]["state"] == "대기", "1분에 한 번까지"
        with patch.object(connect, "RETRY", 0):
            rows = {r["id"]: r for r in connect.rows()}
    assert connect.record("proj")["handover"]["state"] == "완료"
    assert git(repo, "rev-parse", "HEAD") == commit and box(repo) == []
    assert not any("adapter 반영 대기" in m for m in rows["proj"]["missing"])


def test_a_merge_not_yet_on_the_remote_waits(original):
    repo, merge, _ = original
    commit = merge(push=False)
    head, text = git(repo, "rev-parse", "HEAD"), (repo / ".wiki/adapter.toml").read_bytes()
    with patch.object(specs, "sh", Merged(commit)):
        assert not connect.handover(repo, 7)["ok"]
    untouched(repo, head, text)


def test_a_failed_fast_forward_puts_the_copy_back(original):
    repo, merge, _ = original
    commit = merge()
    (repo / "local.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", "local.txt")
    git(repo, "commit", "-qm", "diverged")
    head, text = git(repo, "rev-parse", "HEAD"), (repo / ".wiki/adapter.toml").read_bytes()
    with patch.object(specs, "sh", Merged(commit)):
        assert not connect.handover(repo, 7)["ok"]
    untouched(repo, head, text)


def test_a_failed_check_restores_only_into_an_empty_place(original):
    repo, merge, other = original
    commit = merge()
    # A later commit on the remote changes the adapter again: after the
    # fast-forward the place holds that one, not the merged one.
    (other / ".wiki/adapter.toml").write_text("later\n", encoding="utf-8")
    git(other, "commit", "-qam", "later")
    git(other, "push", "-q", "origin", "main")
    with patch.object(specs, "sh", Merged(commit)):
        assert not connect.handover(repo, 7)["ok"]
    assert (repo / ".wiki/adapter.toml").read_text(encoding="utf-8") == "later\n", "찬 자리는 되돌리지 않는다"
    [left] = box(repo)
    assert str(left) in connect.record("proj")["handover"]["reason"], "옮긴 사본의 경로를 알린다"
    with patch.object(specs, "sh", Merged(commit)):
        assert "앞선 넘기기의 사본" in connect.handover(repo, 7)["reason"]


def test_the_merge_of_a_survey_hands_over_instead_of_the_plain_fast_forward(original):
    repo, merge, _ = original
    commit = merge()
    spec = {"id": "wiki-bootstrap", "repo": "proj", "state": "머지 대기", "pr": {"number": 7, "head": commit},
            "survey": {"handover": True}, "history": [{"ts": 0, "state": "정리됨"}], "worktree": None,
            "goal": "이 저장소의 위키 초기화"}
    specs.save(spec)
    with patch.object(specs, "sh", Merged(commit)), patch.object(loop, "forward") as forward, \
         patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None):
        loop.finish(repo, spec, "main", commit, "머지됨")
    forward.assert_not_called()
    assert git(repo, "rev-parse", "HEAD") == commit
    assert any("adapter 를 넘기고" in n for n in specs.load("proj", "wiki-bootstrap")["cleanup"])
