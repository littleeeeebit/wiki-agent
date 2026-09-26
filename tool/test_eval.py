"""The stage 1 baseline replays exactly, and a changed ranking is caught."""

import json
from pathlib import Path

from eval import baseline

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "eval/jev/smoke.json"
RECORDED = ROOT / "eval/jev/smoke.baseline.json"


def test_the_recorded_smoke_baseline_replays_exactly():
    before = json.loads(RECORDED.read_text(encoding="utf-8"))
    after = baseline.run(SMOKE, before["retrieval"]["k"])
    assert baseline.differences(before, after) == []
    assert after["usage"]["jev_calls"] == 0 and after["models"]["jev"] is None
    # Ids are corpus-relative: no temporary folder leaks into a result.
    assert all(h["id"].startswith(("hub/", "repo/")) for q in after["queries"] for h in q["hits"])


def test_a_different_outcome_is_a_difference():
    before = json.loads(RECORDED.read_text(encoding="utf-8"))
    after = json.loads(RECORDED.read_text(encoding="utf-8"))
    after["queries"][0]["hits"].reverse()
    after["retrieval"]["k"] = 3
    assert baseline.differences(before, after) == [
        f"retrieval options changed: {before['retrieval']} -> {after['retrieval']}", "smoke-01: hits differ"]
