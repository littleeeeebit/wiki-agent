"""`work.files`: the candidate builder, the one relevance request, the
fallback that loads everything, the first turn's two lists, and the replay
that sends no turn to a coding session. Jev is a stand-in behind
`decisions.transport`; git is real."""

import contextlib
import io
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import decision
from common.language import language
from eval import file_filter as replay
from main import channels, decisions, file_filter, specs, work
from test_main import until


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, files: dict[str, str], message: str = "c") -> str:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def spec(sid: str = "now", files=("a.py:3", "gone.py"), **over) -> dict:
    return {"id": sid, "repo": "proj", "rev": 1, "goal": "Rename the port setting", "out": ["the screen"],
            "done": ["python -m ruff check .", "the setting is renamed"], "history": [],
            "grounds": {"pages": [], "files": list(files), "rules": []},
            "decisions": [{"what": "keep the old name as an alias", "why": "", "rejected": ""}], **over}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A repository with a merged history, its spec records, and Jev's records under the test's folder."""

    repo = tmp_path / "proj"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "e@e"], ["config", "user.name", "e"]):
        git(repo, *args)
    base = commit(repo, {"a.py": "PORT = 1\n", "b.md": "# Ports\n", "c.py": "x = 1\n" * 100, "d.py": "y = 1\n"})
    overlap = commit(repo, {"c.py": "x = 2\n" * 100})
    other = commit(repo, {"d.py": "y = 2\n"})
    late = commit(repo, {"b.md": "# Ports, later\n"})
    monkeypatch.setattr(specs, "SPECS", tmp_path / "specs")
    monkeypatch.setattr(decisions, "LOGS", tmp_path / "actions")
    for sid, c, files, ts in (("overlap", overlap, ["a.py:1"], 10.0), ("other", other, ["x.py"], 11.0),
                              ("late", late, ["a.py"], 99.0)):
        (specs.SPECS / "proj").mkdir(parents=True, exist_ok=True)
        (specs.SPECS / "proj" / f"{sid}.json").write_text(json.dumps(spec(
            sid, files, state="머지됨", merge={"commit": c, "base": "main"},
            history=[{"ts": ts - 5, "state": "작업 중"}, {"ts": ts, "state": "머지됨"}])), encoding="utf-8")
    monkeypatch.setattr(decisions.knowledge, "english", lambda texts, seconds, **kw: [
        {"status": "original_english", "text": t} if language(t) == "en" else {"status": "unavailable", "text": None}
        for t in texts])
    return repo, base


class Jev:
    """Relevance answers by path; counts requests."""

    def __init__(self, by_path: dict[str, float] | Exception):
        self.by_path, self.requests = by_path, []

    def __call__(self, cfg):
        def evaluate(state, questions, trace, budget, stage):
            budget.call()
            self.requests.append((state, questions))
            trace.append({"stage": stage, "model": "jev-test", "usage": {"input_tokens": 2200, "output_tokens": 40}})
            if isinstance(self.by_path, Exception):
                raise self.by_path
            paths = {c["id"]: c["path"] for c in state["candidates"]}
            return {i: self.by_path.get(paths[i], 0.5) for i in questions}
        return evaluate


def cfg(**over) -> decision.Config:
    return decision.Config(**{"mode": "active", "model": "jev-1.13", "key_source": "file", "key": "k", **over})


# -- the candidates -------------------------------------------------------------


def test_candidates_are_grounds_then_search_then_overlapping_merges_present_and_capped(world, monkeypatch):
    repo, _ = world
    tree = file_filter.Tree(repo)
    got = file_filter.candidates(spec(), repo, tree, hits=["b.md", "a.py", "nowhere.md"], before=50.0)
    # `gone.py` and `nowhere.md` are not in the tree; `late` merged after the start; `other` shares no ground.
    assert got == [{"path": "a.py", "via": ["grounds", "search"]}, {"path": "b.md", "via": ["search"]},
                   {"path": "c.py", "via": ["merged:overlap"]}]
    assert [c["path"] for c in file_filter.candidates(spec(), repo, tree, hits=[])] == ["a.py", "b.md", "c.py"], \
        "with no start time every merged spec counts, `late` (b.md) too"
    assert file_filter.candidates(spec(files=()), repo, tree, hits=["d.py"]) == [{"path": "d.py", "via": ["search"]}], \
        "no grounds overlap nothing"
    monkeypatch.setattr(decisions, "MAX_FILES", 2)
    assert len(file_filter.candidates(spec(), repo, tree, hits=["b.md"], before=50.0)) == 2


def test_every_grounds_path_the_spec_checks_accept_names_its_tracked_file(world):
    repo, _ = world
    refs = [str(repo / "a.py") + ":1", str(repo / "a.py").replace("/", "\\") + ":2", (repo / "b.md").as_posix(),
            "./a.py:3", "a.py:1–3", "sub\\..\\b.md:2-4", *([str(repo / "A.PY")] if os.name == "nt" else []),
            "../out.py", str(repo.parent / "x.py")]
    kept = specs.fields(repo, {"goal": "g", "done": [], "grounds": {"files": refs}}, "gate")["grounds"]["files"]
    assert kept == refs[:-2] and not specs.missing(repo, {"grounds": {"files": kept}}), "outside refs are dropped"
    s = spec(files=kept)
    tree = file_filter.Tree(repo)
    assert all(specs.relative(repo, r) in tree.files for r in kept), "each accepted ref in git's spelling"
    assert file_filter.grounded(s, repo) == ["a.py", "b.md"]
    assert [c["path"] for c in file_filter.candidates(s, repo, tree, hits=[], before=50.0)] == ["a.py", "b.md", "c.py"], \
        "`overlap` grounds `a.py:1` too"


def test_search_hits_outside_the_repository_are_not_candidates(world, monkeypatch):
    repo, _ = world
    monkeypatch.setattr(file_filter.search, "ask", lambda *a, **k: [
        {"path": str(repo / "b.md"), "locator": {"path": "b.md"}},
        {"path": str(repo.parent / "hub" / "craft" / "x.md"), "locator": {"path": "craft/x.md"}}])
    assert file_filter.searched("ports", repo) == ["b.md"]


# -- the judgement ----------------------------------------------------------------


def test_one_request_judges_every_file_and_the_record_keeps_each_probability(world, monkeypatch):
    repo, _ = world
    stand_in = Jev({"a.py": 0.95, "b.md": 0.1, "c.py": 0.5})
    monkeypatch.setattr(decisions, "transport", stand_in)
    offered = file_filter.candidates(spec(), repo, file_filter.Tree(repo), hits=["b.md"], before=50.0)
    rec = file_filter.judge(spec(), offered, cfg())
    [(state, questions)] = stand_in.requests
    assert len(questions) == 3 and all(q["type"] == "noul" for q in questions.values())
    assert state["specification"]["goal"] == "Rename the port setting"
    assert rec["status"] == "filtered" and rec["threshold"] == 0.2 and rec["point"] == "work.files"
    assert rec["kept"] == [{"path": "a.py", "p": 0.95}, {"path": "c.py", "p": 0.5}], "a doubt keeps the file"
    assert rec["dropped"] == [{"path": "b.md", "p": 0.1}]
    assert rec["jev"]["usage"] == {"input_tokens": 2200, "output_tokens": 40}


@pytest.mark.parametrize("how, reason", [
    ({"key": ""}, "no_key"),
    ({"mode": "off"}, "mode_off"),
    (decision.JevError("network"), "unavailable:network"),
    ("korean", "normalization_failed"),
])
def test_a_filter_that_cannot_judge_loads_every_file_and_says_fallback(world, monkeypatch, how, reason):
    repo, _ = world
    stand_in = Jev(how if isinstance(how, Exception) else {"a.py": 0.0, "b.md": 0.0})
    monkeypatch.setattr(decisions, "transport", stand_in)
    s = spec(goal="포트 설정 이름을 바꾼다") if how == "korean" else spec()
    offered = file_filter.candidates(s, repo, file_filter.Tree(repo), hits=["b.md"], before=50.0)
    rec = file_filter.judge(s, offered, cfg(**how) if isinstance(how, dict) else cfg())
    assert rec["status"] == "fallback" and rec["reason"] == reason
    assert [f["path"] for f in rec["kept"]] == ["a.py", "b.md", "c.py"] and rec["dropped"] == []
    assert len(stand_in.requests) == (1 if isinstance(how, Exception) else 0), "nothing Korean is sent"


# -- the first turn -------------------------------------------------------------


def test_kept_files_are_loaded_and_dropped_ones_listed_as_available(world, monkeypatch):
    repo, _ = world
    commit(repo, {"long.py": "\n".join(f"line {i}" for i in range(500)), "ticks.md": "```\ncode\n```\n",
                  "blob.bin": "a\x00b"})
    text = file_filter.render(["a.py", "long.py", "ticks.md", "blob.bin"], ["b.md", "d.py"], file_filter.Tree(repo))
    loaded, available = text.split("Available, not loaded")
    assert "### `a.py`\n```\nPORT = 1\n```" in loaded
    assert f"### `long.py`, cut to {file_filter.LINES} of 500 lines" in loaded and "line 199" in loaded \
        and "line 200" not in loaded
    assert "````\n```\ncode\n```\n````" in loaded, "a fence longer than the file's own"
    assert "Also needed, not loaded (binary or over the size limit):\n- `blob.bin`" in loaded
    assert available.splitlines()[1:] == ["- `b.md`", "- `d.py`"] and "y = 1" not in available
    monkeypatch.setattr(file_filter, "TOTAL", 10)
    assert "- `long.py`" in file_filter.render(["a.py", "long.py"], [], file_filter.Tree(repo)), "over the total"
    assert file_filter.render([], [], file_filter.Tree(repo)) == ""


def test_a_work_start_sends_the_filtered_first_turn_and_records_it(world, monkeypatch):
    repo, _ = world
    worktree = repo.parent / "wt"
    git(repo, "worktree", "add", "-q", "-b", "now", str(worktree))
    monkeypatch.setattr(decisions, "transport", Jev({"a.py": 0.9, "b.md": 0.05, "c.py": 0.9}))
    monkeypatch.setattr(file_filter.search, "ask", lambda *a, **k: [{"path": str(repo / "b.md"),
                                                                     "locator": {"path": "b.md"}}])
    noted = []
    with patch.object(decisions, "note", lambda run, text: noted.append(text)):
        text = file_filter.first(spec(), repo, worktree, cfg(), occasion="start:1", run=object())
    assert "### `a.py`" in text and "### `c.py`" in text and "# Ports" not in text
    assert text.split("Available, not loaded")[1].splitlines()[1:] == ["- `b.md`"]
    [rec] = decisions.history(("proj", "now"))
    assert rec["point"] == "work.files" and [f["path"] for f in rec["dropped"]] == ["b.md"]
    assert noted == ["Jev · 파일 3개 중 2개를 싣는다"]


def test_shadow_loads_every_file_and_records_the_judgement_unapplied(world, monkeypatch):
    repo, _ = world
    worktree = repo.parent / "wt"
    git(repo, "worktree", "add", "-q", "-b", "now", str(worktree))
    monkeypatch.setattr(decisions, "transport", Jev({"a.py": 0.1, "c.py": 0.1}))
    monkeypatch.setattr(file_filter.search, "ask", lambda *a, **k: None)
    text = file_filter.first(spec(), repo, worktree, cfg(mode="shadow"), occasion="start:1")
    assert "### `a.py`" in text and "### `c.py`" in text and "Available, not loaded" not in text
    until(lambda: decisions.history(("proj", "now")))
    [rec] = decisions.history(("proj", "now"))
    assert rec["applied"] is False and rec["mode"] == "shadow" and [f["path"] for f in rec["dropped"]] == ["a.py", "c.py"]


def test_api_jev_lists_the_file_point_as_provisional():
    got = decisions.coverage(cfg())
    assert got["points"]["work.files"]["baseline"] == "the full list"
    assert got["points"]["work.files"]["operations"] == ["keep", "drop"]
    [item] = [i for i in got["items"] if i["point"] == "work.files"]
    assert item["decisions"] == {"relevance": "provisional"} and item["provisional"]


# -- the replay -----------------------------------------------------------------


def test_one_arm_the_tokenizer_misses_puts_both_arms_on_the_estimate(monkeypatch):
    answers = [b'{"input_tokens": 1000}', OSError("timed out")]

    def urlopen(req, timeout):
        got = answers.pop(0)
        if isinstance(got, Exception):
            raise got
        return contextlib.nullcontext(io.BytesIO(got))

    monkeypatch.setattr(replay.settings, "pick", lambda found, name, default=None: ("key", "file"))
    monkeypatch.setattr(replay.urllib.request, "urlopen", urlopen)
    assert replay.count("s" * 40, ["a" * 4000, "a" * 400]) == ([1010, 110], "estimate:chars/4")


def test_the_replay_measures_both_arms_and_never_opens_a_coding_session(world, monkeypatch, tmp_path):
    repo, base = world
    monkeypatch.setattr(decisions, "transport", Jev({"a.py": 0.9, "c.py": 0.05}))
    monkeypatch.setattr(file_filter.search, "ask", lambda *a, **k: None)
    monkeypatch.setattr(channels, "repo_for", lambda name: repo if name == "proj" else None)
    monkeypatch.setattr(decision, "config", lambda project=None: cfg())
    monkeypatch.setattr(replay.settings, "pick", lambda found, name, default=None: (None, "default"))

    def no_session(*a, **k):
        raise AssertionError("the replay opened a coding session")

    out = tmp_path / "out.json"
    with patch.object(work, "ChatSession", no_session), patch.object(work, "session", no_session):
        assert replay.main(["--replay", "1", "--out", str(out)]) == 0
    [row] = json.loads(out.read_text(encoding="utf-8"))["specs"]
    # The latest merge is `late`; it started at 94, so `overlap` (merged at 10) counts and itself is left out.
    assert row["spec"] == "late" and row["status"] == "filtered"
    assert [c["path"] for c in row["offered"]] == ["a.py", "c.py"]
    assert [f["path"] for f in row["dropped"]] == ["c.py"] and row["tokens_after"] < row["tokens_before"]
    assert row["jev"]["usage"] == {"input_tokens": 2200, "output_tokens": 40} and row["start"] != base
