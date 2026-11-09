"""The machine's wiring watched live, redone for every repository at once, and the map's lists behind its counts."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from main import channels as chat_channels
from main import connect, wiring, work
from test_main import _documented, _repo, _two_projects, client, no_machine_settings  # noqa: F401 — the fixture is autouse


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture(autouse=True)
def feed():
    with patch.object(work, "feed", work.Feed()) as fresh:
        yield fresh


def drained() -> list[dict]:
    """What the screens were told since the last call, without the sequence numbers."""

    found = [{k: v for k, v in e.items() if k != "seq"} for e in work.feed.events]
    work.feed.events.clear()
    return found


class Ticks:
    """A `halt` that lets `watch` look `n` more times, then stops it."""

    def __init__(self, n: int):
        self.n = n

    def wait(self, _seconds: float) -> bool:
        self.n -= 1
        return self.n < 0


def test_the_hub_branch_is_read_from_head_and_empty_when_detached(tmp_path):
    hub = _repo(tmp_path)
    with patch.object(wiring, "ROOT", hub):
        git(hub, "checkout", "-qB", "main")
        assert wiring.hub_branch() == "main" and wiring.refused() == ""
        git(hub, "checkout", "-qb", "spec-views-main-experiment")
        assert wiring.hub_branch() == "spec-views-main-experiment"
        assert "spec-views-main-experiment" in wiring.refused(), "main 이 아닌 허브는 다시 연결을 거절한다"
        git(hub, "checkout", "-q", "--detach")
        assert wiring.hub_branch() == "" and "분리된 HEAD" in wiring.refused()


def test_a_change_of_branch_or_user_settings_tells_the_screens_once_each(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    branch = ["main"]
    with patch.object(wiring, "hub_branch", side_effect=lambda: branch[0]), \
         patch.object(wiring.apply, "user_files", side_effect=lambda host: [settings] if host == "claude" else []):
        drained()
        wiring.watch(Ticks(1), every=0)
        assert drained() == [], "바뀐 것이 없으면 알리지 않는다"

        looks = iter([lambda: None, lambda: settings.write_text('{"hooks": {}}', encoding="utf-8"),
                      lambda: branch.__setitem__(0, "other"), lambda: None])

        class Changing(Ticks):
            def wait(self, seconds):
                next(looks)()
                return super().wait(seconds)

        wiring.watch(Changing(3), every=0)
        assert drained() == [{"kind": "connect", "repo": ""}] * 2, "설정 파일과 브랜치가 바뀔 때마다 한 번씩"


def test_rewire_writes_exactly_the_confirmed_plan_or_nothing(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"

    def settings(repo: Path, text: str | None = None) -> dict | None:
        """Write `text` as `repo`'s Claude settings, or read them back."""
        file = repo / ".claude" / "settings.json"
        if text is None:
            return json.loads(file.read_text(encoding="utf-8"))
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
        return None

    settings(a, "{}"), settings(b, "{}")
    rows = [{"path": str(a), "state": "일부"}]
    web = client()
    calm = {"needed": False, "refused": "", "lines": [], "links": [], "trust": False, "digest": ""}

    def confirm() -> tuple[dict, object]:
        plan = web.get("/api/rewire/plan").json()
        return plan, web.post("/api/rewire", json={"plan": plan["digest"]})

    with patch.object(connect, "hub", return_value=calm), patch.object(connect, "hosts", return_value=["claude"]), \
         patch.object(chat_channels, "projects", side_effect=lambda: list(rows)), \
         patch.object(connect.apply, "declared", return_value=({"Bash(git reset --hard*)": []}, {})), \
         patch.object(connect, "move", side_effect=AssertionError("옮길 것이 없으면 옮기지 않는다")):
        with patch.object(wiring, "hub_branch", return_value="feature"):
            plan, answer = confirm()
            assert "feature" in plan["refused"]
            assert answer.status_code == 409 and "main" in answer.json()["detail"] and settings(a) == {}
        with patch.object(wiring, "hub_branch", return_value="main"):
            plan = web.get("/api/rewire/plan").json()
            assert [r["name"] for r in plan["repos"]] == ["a"] and plan["repos"][0]["unwire"]
            assert settings(a) == {}, "계획은 쓰지 않고 읽기만 한다"
            rows.append({"path": str(b), "state": "일부"})   # another window connected b meanwhile
            assert web.post("/api/rewire", json={"plan": plan["digest"]}).status_code == 409
            assert settings(a) == settings(b) == {}, "확인한 계획과 다르면 아무것도 쓰지 않는다"
            drained()
            plan, answer = confirm()
            assert answer.json() == {"ok": True, "written": [str(a / ".claude/settings.json"), str(b / ".claude/settings.json")],
                                     "failed": []}
            assert "permissions" in settings(a) and "permissions" in settings(b)
            assert {"kind": "connect", "repo": ""} in drained()

            settings(a, "{}"), settings(b, "{broken")
            plan, answer = confirm()
            assert "읽을 수 없다" in plan["repos"][1]["refused"] and answer.status_code == 409
            assert settings(a) == {}, "읽지 못하는 파일이 하나라도 있으면 첫 쓰기 전에 거절한다"

            settings(b, "{}")
            local = a / ".claude" / "settings.local.json"
            local.write_text('{"disableAllHooks": true}', encoding="utf-8")
            plan, answer = confirm()
            assert "disableAllHooks" in plan["repos"][0]["refused"] and answer.status_code == 409
            assert settings(a) == {} and json.loads(local.read_text(encoding="utf-8")) == {"disableAllHooks": True}, \
                "사람이 꺼 둔 훅은 지우지 않고 거절한다"
            local.unlink()

            real = Path.write_text

            def full(self: Path, *args, **kwargs):
                if b in self.parents:
                    raise OSError("디스크가 가득 찼다")
                return real(self, *args, **kwargs)

            drained()
            with patch.object(Path, "write_text", full):
                plan, answer = confirm()
            done = answer.json()
            assert done["ok"] is False and done["written"] == [str(a / ".claude/settings.json")]
            assert done["failed"] == [f"{b / '.claude/settings.json'}: 디스크가 가득 찼다"]
            assert {"kind": "connect", "repo": ""} in drained(), "반쯤 쓴 것도 화면들이 다시 읽는다"

            settings(a, "{}"), settings(b, "{}")
            with patch.object(connect, "hub", return_value={**calm, "needed": True, "digest": "d"}), \
                 patch.object(connect, "move", side_effect=lambda _d, done: done.append("user")) as move:
                assert web.post("/api/rewire", json={"plan": plan["digest"]}).status_code == 409, "허브 이전도 계획에 묶인다"
                plan, answer = confirm()
                assert answer.json()["ok"] and answer.json()["written"][0] == "user"
                move.assert_called_once()

    # The hub move itself failing partway: the real `move`, its user files in tmp_path.
    home = [tmp_path / "home/.claude/settings.json", tmp_path / "home/.codex/hooks.json"]
    for file in home:
        file.parent.mkdir(parents=True)
        file.write_text("{}", encoding="utf-8")
    moving = {**calm, "needed": True, "lines": [{"file": str(f), "change": "c"} for f in home], "digest": "d"}
    rows.clear()
    real = Path.write_text

    def second(self: Path, *args, **kwargs):
        if self == home[1]:
            raise OSError("디스크가 가득 찼다")
        return real(self, *args, **kwargs)

    with patch.object(wiring, "hub_branch", return_value="main"), \
         patch.object(chat_channels, "projects", side_effect=lambda: list(rows)), \
         patch.object(connect, "_snapshot", return_value=(moving, [(f, {"moved": True}, ["c"]) for f in home], [], "py")), \
         patch.object(connect, "RECORDS", tmp_path / "records"):
        plan = web.get("/api/rewire/plan").json()
        with patch.object(Path, "write_text", second):
            done = web.post("/api/rewire", json={"plan": plan["digest"]}).json()
    assert done == {"ok": False, "written": [str(home[0])], "failed": ["허브 이전: OSError: 디스크가 가득 찼다"]}
    assert json.loads(home[0].read_text(encoding="utf-8")) == {"moved": True}
    assert json.loads(home[1].read_text(encoding="utf-8")) == {}


def test_the_listing_names_the_hub_branch(tmp_path):
    with patch.object(wiring, "hub_branch", return_value="feature"):
        assert client().get("/api/connect").json()["hub"]["branch"] == "feature"


def test_the_map_carries_the_orphans_and_findings_behind_its_counts(tmp_path):
    repos = _two_projects(tmp_path)
    _documented(repos["a"], "a")
    (repos["a"] / "docs" / "alone.md").write_text("# Alone\n", encoding="utf-8")
    (repos["a"] / ".wiki" / "a-rule.md").write_text(
        "---\nscope: a\nseverity: landmine\ntriggers: [\"x\"]\nreads: [docs/a.md, docs/gone.md]\n---\n# a rule\n",
        encoding="utf-8")
    with patch.object(chat_channels, "repo_for", side_effect=repos.get):
        data = client().get("/api/graph", params={"repo": "a"}).json()
    assert data["orphans"] == ["docs/alone.md"] and data["metrics"]["orphans"] == 1
    assert len(data["findings"]) == data["metrics"]["lint"]
    assert {"kind": "끊어진 포인터", "text": "`.wiki/a-rule` 이 `docs/gone.md` 를 가리키는데 없다"} in data["findings"]
