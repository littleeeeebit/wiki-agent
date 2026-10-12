"""Reliability PR 5's seventh set (`docs/plans/reliability/5-english-baseline.md`, v7): fresh held-out cases for
the reworded ASK, the work-start and review-fix prompts and the answer path, judged by v6's gates. No network."""

import json
from pathlib import Path

from eval import dataset
from test_reliability_v5 import strata

JEV = Path(__file__).resolve().parents[1] / "eval" / "jev"
V7 = JEV / "reliability-v7"
EARLIER = ("", "-v2", "-v3", "-v4", "-v5", "-v6")


def forwarded(intent):
    """A question someone else asked, quoted as material: the shape v5's and v6's misses shared."""

    return any(not s["ask"] and s["text"].rstrip('"').endswith("?") for s in intent["route_segments"])


def test_the_seventh_set_holds_out_fresh_cases_for_material_and_the_answers():
    data = dataset.load(V7 / "intents.json")
    assert dataset.invalid(data) == [] and dataset.unresolved(data) == [] and dataset.frozen(data) == []
    earlier = [dataset.load(JEV / f"reliability{v}" / "intents.json") for v in EARLIER]
    assert [i for i in data["intents"] if i["split"] == "calibration"] == \
        [i for i in earlier[-1]["intents"] if i["split"] == "calibration"]
    held = [i for i in data["intents"] if i["split"] == "held_out"]
    assert all(i["review"] == "r7" for i in held)
    # v3–v6's segments calibrated the ASK change (#125): none of their wordings or ids comes back.
    assert not {i["variants"]["en"] for i in held} & {i["variants"]["en"] for d in earlier for i in d["intents"]}
    assert not {i["id"] for i in held} & {i["id"] for d in earlier for i in d["intents"]}
    old_pages, new_pages = dataset.pages(earlier[-1]), dataset.pages(data)
    assert {n: new_pages.get(n) for n in old_pages} == old_pages
    cohorts = strata(data)
    assert all(len(c) >= 54 for c in cohorts.values())
    assert sum(map(forwarded, cohorts["material"])) >= 12
    assert sorted(i["fault"]["kind"] for i in held if i.get("fault")) == \
        ["cancelled"] * 2 + ["exhausted"] * 2 + ["unavailable"] * 2


def test_the_seventh_action_set_holds_out_54_fresh_fixtures_per_changed_point():
    actions = json.loads((V7 / "actions.json").read_text(encoding="utf-8"))
    v5 = json.loads((JEV / "reliability-v5" / "actions.json").read_text(encoding="utf-8"))
    points = ("work.start", "loop.fix")
    assert [f for f in actions["fixtures"] if f["split"] == "calibration"] == \
        [f for f in v5["fixtures"] if f["split"] == "calibration" and f["point"] in points]
    held = [f for f in actions["fixtures"] if f["split"] == "held_out"]
    assert all(f["review"] == "r7" and f["point"] in points for f in held)
    labels = {p: sorted(f["label"] for f in held if f["point"] == p) for p in points}
    assert labels == {"work.start": ["clarify"] * 18 + ["dispatch"] * 18 + ["evidence"] * 18,
                      "loop.fix": ["context"] * 27 + ["fix"] * 27}
    # How a specification names its files says nothing of its label (review r7, round 1).
    shapes = {label: sorted("dir" if any(p.endswith("/") for p in f["spec"]["grounds"]["files"])
                            else "file" if f["spec"]["grounds"]["files"] else "none"
                            for f in held if f["label"] == label) for label in ("clarify", "dispatch", "evidence")}
    assert shapes["clarify"] == shapes["dispatch"] == shapes["evidence"]
    seen = []
    for v in EARLIER:
        path = JEV / f"reliability{v}" / "actions.json"
        if path.exists():
            seen += json.loads(path.read_text(encoding="utf-8"))["fixtures"]
    assert not {f["id"] for f in held} & {f["id"] for f in seen}
    assert not {f["spec"]["goal"] for f in held if "spec" in f} & {f["spec"]["goal"] for f in seen if "spec" in f}
    assert not {x for f in held for x in f.get("findings", [])} & {x for f in seen for x in f.get("findings", [])}


def test_gates_v7_are_v6s_and_the_set_is_frozen_on_one_date():
    gates = json.loads((V7 / "gates.json").read_text(encoding="utf-8"))
    v6 = json.loads((JEV / "reliability-v6" / "gates.json").read_text(encoding="utf-8"))
    assert gates["gates"] == v6["gates"] and gates["cohorts"] == v6["cohorts"]
    assert gates["copied_from"]["path"] == "eval/jev/reliability-v6/gates.json"
    data = json.loads((V7 / "intents.json").read_text(encoding="utf-8"))
    actions = json.loads((V7 / "actions.json").read_text(encoding="utf-8"))
    assert data["frozen"] == gates["frozen"] == actions["frozen"] == data["labels"]["reviews"]["r7"]["at"] == \
        actions["labels"]["reviews"]["r7"]["at"] == data["labels"]["reviewed_at"] == actions["labels"]["reviewed_at"]
