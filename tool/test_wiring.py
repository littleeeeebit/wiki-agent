"""The machine's wiring watched live, redone for every repository at once, and the map's lists behind its counts."""

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


def test_rewire_is_refused_off_main_and_otherwise_unwires_every_connected_repository(tmp_path):
    repos = [tmp_path / "a", tmp_path / "b"]
    web = client()
    calm = {"needed": False, "refused": "", "lines": [], "links": [], "trust": False, "digest": ""}
    undone: list[tuple[str, bool]] = []
    with patch.object(wiring, "connected", return_value=repos), patch.object(connect, "hub", return_value=calm), \
         patch.object(connect, "move", side_effect=AssertionError("옮길 것이 없으면 옮기지 않는다")), \
         patch.object(connect, "unwire", side_effect=lambda path, write=True: undone.append((path.name, write)) or []):
        with patch.object(wiring, "hub_branch", return_value="feature"):
            assert "feature" in web.get("/api/rewire/plan").json()["refused"]
            answer = web.post("/api/rewire", json={"hub": ""})
            assert answer.status_code == 409 and "main" in answer.json()["detail"]
            assert not [w for _n, w in undone if w], "거절되면 아무것도 쓰지 않는다"
        undone.clear()
        with patch.object(wiring, "hub_branch", return_value="main"):
            plan = web.get("/api/rewire/plan").json()
            assert plan["refused"] == "" and [r["name"] for r in plan["repos"]] == ["a", "b"]
            assert undone == [("a", False), ("b", False)], "계획은 쓰지 않고 읽기만 한다"
            undone.clear()
            drained()
            assert web.post("/api/rewire", json={"hub": ""}).json()["ok"]
            assert undone == [("a", True), ("b", True)]
            assert {"kind": "connect", "repo": ""} in drained()

            with patch.object(connect, "hub", return_value={**calm, "needed": True, "digest": "d"}):
                assert web.post("/api/rewire", json={"hub": ""}).status_code == 409, "허브 이전은 확인받은 digest 가 있어야 한다"


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
