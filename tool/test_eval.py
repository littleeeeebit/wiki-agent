"""The stage 1 baseline replays exactly, and a changed ranking is caught.

The BM25 record is replayed here. The hybrid one (`smoke.hybrid.json`)
needs the e5 model, which a test run does not download.
"""

import json
import os
from pathlib import Path

import pytest

from eval import baseline

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "eval/jev/smoke.json"
RECORDED = ROOT / "eval/jev/smoke.bm25.json"


def test_the_recorded_smoke_baseline_replays_exactly():
    before = json.loads(RECORDED.read_text(encoding="utf-8"))
    assert before["retrieval"]["method"] == "bm25"
    after = baseline.run(SMOKE, before["retrieval"]["k"], method="bm25")
    assert baseline.differences(before, after) == []
    assert after["usage"]["jev_calls"] == 0 and after["models"]["jev"] is None
    # Ids are corpus-relative: no temporary folder leaks into a result.
    assert all(h["id"].startswith(("hub/", "repo/")) for q in after["queries"] for h in q["hits"])


def test_hybrid_is_never_recorded_from_an_incomplete_index(monkeypatch):
    """Vectors that did not finish leave BM25 ranking. Recorded under the
    hybrid name, it would be a baseline of something the daemon does not serve."""

    asked = []

    class Incomplete:
        files = {}
        embedder = type("E", (), {"state": "off"})()

        def complete(self):
            return False

    monkeypatch.setattr(baseline, "local_index", lambda repo, hub, vectors: asked.append(vectors) or Incomplete())
    with pytest.raises(RuntimeError, match="not recording BM25 as hybrid"):
        baseline.run(SMOKE)
    assert asked == [True]


WINDOWS = pytest.mark.skipif(os.name != "nt", reason="a backslash or a drive is a separator only on Windows")


@pytest.mark.parametrize("name", [
    "../escape.md", "repo/../../escape.md", "docs/a.md", "hub", "{abs}",
    pytest.param("repo/..\\..\\escape.md", marks=WINDOWS),
    pytest.param("C:escape.md", marks=WINDOWS),
])
def test_a_synthetic_file_stays_inside_the_corpus(tmp_path, monkeypatch, name):
    """A manifest names where its pages go; none may land outside `hub/` or
    `repo/` in the scratch folder, and nothing is written before that is known.
    The scratch folder is made inside `tmp_path`, so an escape lands where
    this test can see it."""

    monkeypatch.setattr(baseline.tempfile, "tempdir", str(tmp_path / "scratch"))
    (tmp_path / "scratch").mkdir()
    monkeypatch.chdir(tmp_path / "scratch")
    manifest = json.loads(SMOKE.read_text(encoding="utf-8"))
    manifest["corpus"]["files"] = {"repo/docs/first.md": "# First\n\nok\n",
                                   name.format(abs=(tmp_path / "escape.md").as_posix()): "# Escape\n\nwritten\n"}
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="outside the corpus"):
        baseline.run(path, method="bm25")
    written = [p for p in tmp_path.rglob("*.md")]
    assert written == [], f"written before or despite the check: {written}"


def test_a_different_outcome_is_a_difference():
    before = json.loads(RECORDED.read_text(encoding="utf-8"))
    after = json.loads(RECORDED.read_text(encoding="utf-8"))
    after["queries"][0]["hits"].reverse()
    after["retrieval"]["k"] = 3
    assert baseline.differences(before, after) == [
        f"retrieval options changed: {before['retrieval']} -> {after['retrieval']}", "smoke-01: hits differ"]
