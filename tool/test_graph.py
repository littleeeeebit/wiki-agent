"""Whether the policy graph survives as an artifact."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from graph import build, load_pages  # noqa: E402

PAGE = """---
scope: craft
severity: contract
triggers: ["ㄱ"]
sources: []
links: []
---

# 제목

규칙. 한 줄.
"""


def test_a_round_trip_changes_not_one_character():
    # Catches both a value that will not serialise and one whose type changes
    # on the way back. The reading side sees only this file, so whatever is
    # lost here is never visible on screen again.
    data = build(load_pages([]), [])
    assert json.loads(json.dumps(data, ensure_ascii=False)) == data


def test_the_artifact_declares_its_axis():
    root = Path(tempfile.mkdtemp())
    (root / "craft").mkdir()
    (root / "craft" / "a.md").write_text(PAGE, encoding="utf-8")
    artifact = root / "graph.json"

    done = subprocess.run(
        [sys.executable, str(HERE / "graph.py"), "--json", str(artifact)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "WIKI_ROOT": str(root), "PYTHONIOENCODING": "utf-8"},
    )
    assert done.returncode == 0, done.stderr

    assert b"\r\n" not in artifact.read_bytes()
    assert not artifact.read_bytes().startswith(b"\xef\xbb\xbf")
    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert data["ns"] == "rule"
    assert [n["id"] for n in data["nodes"]] == ["craft/a"]


def test_the_rule_line_is_found_in_either_language():
    # While pages are rewritten in English the two spellings live side by side.
    # Reading only one leaves that page's `rule` empty, taking the map's
    # description and the `--check` comparison with it.
    root = Path(tempfile.mkdtemp())
    (root / "craft").mkdir()
    (root / "craft" / "ko.md").write_text(PAGE, encoding="utf-8")
    (root / "craft" / "en.md").write_text(
        PAGE.replace("# 제목", "# A title").replace("규칙. 한 줄.", "Rule. One line."),
        encoding="utf-8",
    )
    # A line that merely contains the word is not the rule line.
    (root / "craft" / "no.md").write_text(
        PAGE.replace("규칙. 한 줄.", "이 규칙. 은 문장 가운데다."), encoding="utf-8"
    )

    import graph as g

    with patch.object(g, "WIKI", root):
        pages = g.load_pages([])

    assert pages["craft/ko"]["rule"] == "한 줄."
    assert pages["craft/en"]["rule"] == "One line."
    assert pages["craft/no"]["rule"] == ""


def test_it_finds_the_attached_repositories_itself():
    # Holding the names by hand means editing this every time a repository is
    # added, which means not editing it. Folders with no adapter and folders
    # that are not repositories drop out, and a broken workspace setting still
    # has to produce a graph.
    import graph

    workspace = Path(tempfile.mkdtemp())
    wiki = workspace / "위키"
    for name, wired in (("붙은 저장소", True), ("안 붙은 것", False), ("위키", True)):
        repo = workspace / name
        (repo / ".git").mkdir(parents=True)
        if wired:
            (repo / ".wiki").mkdir()
            (repo / ".wiki/adapter.toml").write_text("agents=[]\n", encoding="utf-8")
    (workspace / "저장소_아님").mkdir()

    with patch.object(graph, "WIKI", wiki), patch.object(graph, "HERE", wiki / "tool"):
        assert graph.connected() == [workspace / "붙은 저장소", wiki]
        (wiki / ".chat-local.json").write_text('{"workspace": "."}', encoding="utf-8")
        assert graph.connected() == []          # there are no repositories under the wiki
        (wiki / ".chat-local.json").write_text("깨진 JSON", encoding="utf-8")
        assert graph.connected() == [workspace / "붙은 저장소", wiki]
    # With `WIKI_ROOT` pointing at another hub, its parent is not searched.
    with patch.object(graph, "WIKI", wiki):
        assert graph.connected() == []


def test_the_drawing_code_is_not_in_here():
    # The view is a consumer. If this tool builds a screen there are two
    # copies of the code drawing the same graph, and two copies drift. So
    # there must not be one character of HTML in here.
    source = (HERE / "graph.py").read_text(encoding="utf-8")
    assert "<svg" not in source and "<div" not in source
    assert not (HERE / "graph_view.py").exists()
