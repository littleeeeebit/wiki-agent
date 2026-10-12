"""Reliability PR 5's eighth set (`docs/plans/reliability/5-english-baseline.md`, v8): fresh work-start fixtures
for the reworded prompt (W2), v7's revealed intents for the operational gates, and v7's gates with the deadline
grace (C1). No network."""

import hashlib
import json
from pathlib import Path

JEV = Path(__file__).resolve().parents[1] / "eval" / "jev"
V8, V7 = JEV / "reliability-v8", JEV / "reliability-v7"
EARLIER = ("", "-v2", "-v3", "-v4", "-v5", "-v6", "-v7")


def load(folder: Path, name: str) -> dict:
    return json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))


def shape(f: dict) -> str:
    files = f["spec"]["grounds"]["files"]
    return "dir" if any(p.endswith("/") for p in files) else "file" if files else "none"


def test_the_eighth_set_reruns_v7_s_intents_unchanged():
    data, v7 = load(V8, "intents"), load(V7, "intents")
    assert data["intents"] == v7["intents"] and data["corpus"] == v7["corpus"] and data["labels"] == v7["labels"]
    assert (data["id"], data["version"]) == ("reliability-intents-v8", 8)


def test_the_eighth_action_set_holds_out_54_fresh_work_start_fixtures():
    actions, v5 = load(V8, "actions"), load(JEV / "reliability-v5", "actions")
    assert [f for f in actions["fixtures"] if f["split"] == "calibration"] == \
        [f for f in v5["fixtures"] if f["split"] == "calibration" and f["point"] == "work.start"]
    held = [f for f in actions["fixtures"] if f["split"] == "held_out"]
    assert all(f["review"] == "r8" and f["point"] == "work.start" for f in held)
    assert sorted(f["label"] for f in held) == ["clarify"] * 18 + ["dispatch"] * 18 + ["evidence"] * 18
    # The shape v7 missed, tested on more cases, and no label given away by how files are named.
    shapes = {label: sorted(shape(f) for f in held if f["label"] == label) for label in ("clarify", "dispatch", "evidence")}
    assert shapes["clarify"] == shapes["dispatch"] == shapes["evidence"] and shapes["dispatch"].count("none") >= 9
    seen = []
    for v in EARLIER:
        path = JEV / f"reliability{v}" / "actions.json"
        if path.exists():
            seen += json.loads(path.read_text(encoding="utf-8"))["fixtures"]
    assert not {f["id"] for f in held} & {f["id"] for f in seen}
    assert not {f["spec"]["goal"] for f in held} & {f["spec"]["goal"] for f in seen if "spec" in f}


def test_gates_v8_are_v7_s_but_for_what_the_ceiling_counts():
    gates, raw = load(V8, "gates"), (V7 / "gates.json").read_bytes()
    v7 = json.loads(raw)
    assert gates["copied_from"] == {"path": "eval/jev/reliability-v7/gates.json", "version": 7,
                                    "sha256": hashlib.sha256(raw).hexdigest()}
    assert gates["cohorts"] == v7["cohorts"]
    changed = [(a, b) for a, b in zip(gates["gates"], v7["gates"]) if a != b]
    assert len(gates["gates"]) == len(v7["gates"]) and [a["id"] for a, _b in changed] == ["operating_ceiling"]
    (new, old), = changed
    assert {k: v for k, v in new.items() if k != "about"} == {k: v for k, v in old.items() if k != "about"}
    assert "50 ms" in new["about"]


def test_the_draft_carries_no_review_of_its_own_yet():
    actions = load(V8, "actions")
    assert "r8" not in actions["labels"]["reviews"]
    assert actions["labels"]["reviewed_by"] is None and actions["labels"]["reviewed_at"] is None
