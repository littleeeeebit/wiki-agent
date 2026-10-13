"""The labelling pipeline, the error notebook and the loop, with stub labellers and models.

    python -m pytest laya -q -p no:cacheprovider
"""

import json

import pytest

import label
import loop
import notes
import pool

NOUL = {"type": "noul", "instructions": "Is the passage useful?"}


@pytest.fixture(autouse=True)
def folders(tmp_path, monkeypatch):
    monkeypatch.setattr(label, "DATA", tmp_path / "data")
    monkeypatch.setattr(loop, "DATA", tmp_path / "data")
    monkeypatch.setattr(notes, "NOTES", tmp_path / "notes")
    return tmp_path


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def candidate(i):
    return {"id": f"obs-{i}", "state_hash": f"h{i}", "state": {"text": f"state {i}"},
            "questions": {"useful_p0": {"kind": "useful", "question": NOUL},
                          "conflict_p0": {"kind": "conflict", "question": NOUL}}}


def test_agreed_names_why_a_question_is_dropped():
    yes, no = {"answer": "yes", "confidence": 0.9}, {"answer": "no", "confidence": 0.9}
    assert label.agreed(NOUL, yes, yes) == (True, None)
    assert label.agreed(NOUL, yes, no) == (None, "disagree")
    assert label.agreed(NOUL, yes, {"answer": "yes", "confidence": 0.5}) == (None, "unsure")
    assert label.agreed(NOUL, yes, {"answer": "maybe", "confidence": 0.9}) == (None, "invalid")
    assert label.agreed(NOUL, yes, {"answer": "yes", "confidence": True}) == (None, "invalid")
    assert label.agreed(NOUL, yes, None) == (None, "invalid")
    assert label.agreed(NOUL, yes, "yes") == (None, "invalid")
    assert label.agreed(NOUL, {"answer": "yes", "confidence": float("nan")}, {"answer": "yes", "confidence": 0.1}) \
        == (None, "invalid")
    assert label.agreed(NOUL, {"answer": "yes", "confidence": 2}, yes) == (None, "invalid")
    assert label.agreed(NOUL, yes, yes, intended=False) == (None, "teacher_miss")
    choice = {"type": "choice", "instructions": "?", "criteria": {"supports": "s", "contradicts": "c"}}
    assert label.agreed(choice, {"answer": "supports", "confidence": 1}, {"answer": "supports", "confidence": 1}) \
        == ("supports", None)
    assert label.agreed(choice, {"answer": ["supports"], "confidence": 0.9}, yes) == (None, "invalid")


def test_label_refills_drops_into_the_notebook_and_never_pays_twice():
    write(label.DATA / "useful" / "train.candidates.jsonl", [candidate(i) for i in range(5)])
    plan = {"h0": ("yes", "no"), "h1": ("yes", "yes"), "h2": ("no", "no"), "h3": ("no", "no"), "h4": ("yes", "yes")}
    calls = []

    def turn(model, state, questions):
        calls.append((model, state["text"]))
        assert set(questions) == {"useful_p0"}   # only the kind's questions
        h = "h" + state["text"].split()[-1]
        said = plan[h][0 if model == "a" else 1]
        conf = 0.5 if h == "h2" else 0.9
        return {"model": model, "cost_usd": 0.01, "answers": {"useful_p0": {"answer": said, "confidence": conf}}}

    stats = label.label("useful", "train", 2, ["a", "b"], 100, turn=turn, log=lambda *_: None, round=1,
                        slice=("length", "short"))
    assert (stats["agreed"], stats["dropped"], stats["stopped"]) == (2, 2, "target")
    assert stats["by_category"] == {"disagree": 1, "unsure": 1}
    assert stats["turns"] == 8 and abs(stats["cost_usd"] - 0.08) < 1e-9
    rows = label.read(label.DATA / "useful" / "train.jsonl")
    assert [r["expected"] for r in rows] == [{"useful_p0": True}, {"useful_p0": False}]
    assert rows[0]["provenance"] == {"state_hash": "h1", "source": "obs-1", "labellers": ["a", "b"]}
    noted = notes.ledger("useful")
    assert [(n["category"], n["state_hash"], n["slice"]) for n in noted] == \
        [("disagree", "h0", ["length", "short"]), ("unsure", "h2", ["length", "short"])]
    assert [a["answer"] for a in noted[0]["answers"]] == ["yes", "no"]

    calls.clear()
    stats = label.label("useful", "train", 3, ["a", "b"], 100, turn=turn, log=lambda *_: None)
    assert calls == [("a", "state 4"), ("b", "state 4")]
    # The split's whole record, not this run's: the agreement rate a resumed split reports.
    assert (stats["agreed"], stats["dropped"], stats["states"], stats["stopped"]) == (3, 2, 5, "target")
    assert stats["by_category"] == {"disagree": 1, "unsure": 1} and stats["turns"] == 2
    assert len(notes.ledger("useful")) == 2   # a judged state is never noted again


def test_a_malformed_answer_is_noted_invalid_and_labelling_goes_on():
    write(label.DATA / "useful" / "train.candidates.jsonl", [candidate(0), candidate(1)])

    def turn(model, state, questions):
        said = "yes" if state["text"] == "state 0" else {"answer": "yes", "confidence": 0.9}
        return {"model": model, "cost_usd": 0.0, "answers": {"useful_p0": said}}

    stats = label.label("useful", "train", 5, ["a", "b"], 100, turn=turn, log=lambda *_: None)
    assert (stats["agreed"], stats["by_category"]) == (1, {"invalid": 1})
    assert notes.ledger("useful")[0]["answers"] == [{"labeller": "a", "raw": "yes"}, {"labeller": "b", "raw": "yes"}]


def test_a_failed_turn_is_skipped_then_retried_and_the_turn_limit_stops():
    write(label.DATA / "useful" / "train.candidates.jsonl", [candidate(0)])
    def failing(model, state, questions):
        return {"model": model, "cost_usd": None, "answers": {}, "error": "boom"}

    def ok(model, state, questions):
        return {"model": model, "cost_usd": 0.0, "answers": {"useful_p0": {"answer": "yes", "confidence": 1}}}

    stats = label.label("useful", "train", 1, ["a", "b"], 100, turn=failing, log=lambda *_: None)
    assert (stats["agreed"], stats["dropped"], stats["stopped"]) == (0, 0, "candidates_exhausted")
    assert notes.ledger("useful") == []   # a failure is not a disagreement
    assert label.label("useful", "train", 1, ["a", "b"], 1, turn=ok, log=lambda *_: None)["stopped"] == "turn_limit"
    assert label.label("useful", "train", 1, ["a", "b"], 100, turn=ok, log=lambda *_: None)["stopped"] == "target"
    with pytest.raises(ValueError):
        label.label("useful", "train", 1, ["a", "a"], 1, turn=ok)


def test_notebook_pages_statuses_and_brief():
    for i in range(notes.PAGE_AT):
        notes.record("useful", "disagree", round=1, slice=("numbers", True), state_hash=f"h{i}", source="s",
                     question="useful_p0", answers=[{"labeller": "a", "answer": "yes", "confidence": 0.9}])
    notes.record("useful", "unsure", round=1, slice="base", state_hash="h9", source="s", question="q")
    page = notes.NOTES / "useful" / "disagree--numbers-true.md"
    assert notes.write_pages("useful", log=lambda *_: None) == ["disagree--numbers-true"]   # unsure: under PAGE_AT
    assert notes.UNDIAGNOSED in page.read_text(encoding="utf-8") and notes.front(page)["rounds"] == "1"
    assert notes.write_pages("useful", lambda *_: {}, log=lambda *_: None, limit=0) == []   # no turns left
    assert notes.write_pages("useful", lambda *_: {}, log=lambda *_: None) == ["disagree--numbers-true"]
    assert notes.UNDIAGNOSED in page.read_text(encoding="utf-8")   # a failed diagnosis is tried again later
    seen = []
    written = notes.write_pages("useful", lambda prompt, payload: seen.append(payload) or
                                {"cause": "Negation flips it.", "next": "Generate negated states."},
                                log=lambda *_: None)
    assert written == ["disagree--numbers-true"]   # undiagnosed, though it did not grow
    assert seen[0]["meaning"] == notes.CATEGORIES["disagree"] and len(seen[0]["errors"]) == notes.PAGE_AT
    assert "Negation flips it." in page.read_text(encoding="utf-8")
    assert notes.write_pages("useful", lambda *_: pytest.fail("unchanged pages are not rewritten"),
                             log=lambda *_: None) == []
    notes.record("useful", "disagree", round=3, slice=("numbers", True), state_hash="h7", source="s", question="q")
    notes.write_pages("useful", log=lambda *_: None)
    assert "Negation flips it." in page.read_text(encoding="utf-8")   # grown without a writer: kept
    notes.set_status("useful", "disagree--numbers-true", "falsified", 2)
    notes.set_status("useful", "student_wrong--length-long", "ambiguous", 3, ("length", "long"))
    assert notes.front(page)["status"] == "falsified" and notes.front(page)["rounds"] == "1,2,3"
    history = notes.brief("useful")
    assert sorted(history["avoid"]) == [["length", "long"], ["numbers", True]]
    assert {p["name"]: p["status"] for p in history["pages"]} == \
        {"disagree--numbers-true": "falsified", "student_wrong--length-long": "ambiguous"}
    assert history["clusters"] == {"disagree--numbers-true": 6, "unsure--base": 1}
    assert "falsified" in (notes.NOTES / "index.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        notes.record("useful", "nope", round=0, slice="base", state_hash="h", source="s", question="q")


def test_floor_separates_a_real_gain_from_noise():
    universe = [(f"h{i}", "q") for i in range(40)]
    gain, noise = loop.floor(universe, set(universe[:30]), set(universe[:12]))
    assert gain == pytest.approx(0.45) and 0 < noise < gain
    gain, noise = loop.floor(universe, set(universe[:12]), set(universe[1:13]))
    assert gain == 0 and gain <= noise
    # Ten states of twenty questions each, moving together: ten draws, not two hundred.
    universe = [(f"h{i}", f"q{j}") for i in range(10) for j in range(20)]
    old = {x for x in universe if int(x[0][1:]) < 7}
    gain, noise = loop.floor(universe, old, set(universe) - old)
    assert gain == pytest.approx(0.4) and noise > gain


def validation():
    rows = [{"state": {"text": f"state {i}"}, "questions": {"useful_p0": NOUL}, "expected": {"useful_p0": i < 20},
             "provenance": {"state_hash": f"h{i}", "source": f"obs-{i}"}} for i in range(40)]
    write(loop.DATA / "useful" / "validation.jsonl", rows)
    return [(f"h{i}", "useful_p0") for i in range(40)]


def train_stub(kind, splits):
    return tuple(splits)


def test_the_loop_adopts_falsifies_explores_and_prunes():
    keys = validation()
    asked, limits = [], []

    def score(kind, ckpt):
        wrong = set(keys[:12] if "aug-1" in ckpt else keys[:30])
        return wrong, {k: False for k in wrong}

    def generate(kind, slice, n, history, round, max_turns):
        asked.append((round, slice))
        limits.append(max_turns)
        assert len(history["rounds"]) == round   # every earlier round's record
        return 5

    def label_(kind, split, target, round, slice, max_turns):
        limits.append(max_turns)
        agreed = 3 if round == 3 else 27
        return {"agreed": agreed, "dropped": 30 - agreed, "turns": 10, "stopped": "target"}

    out = loop.run("useful", train=train_stub, score=score, generate=generate, label=label_, max_turns=1000,
                   round_turns=20, log=lambda *_: None)
    assert out["splits"] == ["train", "aug-1"] and out["accuracy"] == pytest.approx(28 / 40)
    assert out["spent"] == 45 and limits[:4] == [1000, 995, 985, 980]
    # Round 2 aims at the highest error rate left; once it is falsified, round 3 tries
    # another feature, and the slice round 1 aimed at is still open (`targeted`).
    assert asked == [(1, ("options", 2)), (2, ("label", "True")), (3, ("options", 2))]
    log = label.read(loop.DATA / "useful" / "rounds.jsonl")
    assert [r.get("adopted") for r in log[1:4]] == [True, False, False]
    assert log[2]["reason"] == "within_noise" and log[3]["reason"] == "ambiguous_slice"
    assert log[4]["prune"] == "aug-1" and log[4]["removed"] is False
    assert sorted(notes.brief("useful")["avoid"]) == [["label", "True"], ["options", 2]]
    left = [n for n in notes.ledger("useful") if n["round"] == 1]
    assert len(left) == 12 and all(n["slice"] == ["options", 2] for n in left)


def test_labelling_that_stops_short_stops_the_loop_without_condemning_the_slice():
    keys = validation()

    def score(kind, ckpt):
        return set(keys[:30]), {}

    def generate(kind, slice, n, history, round, max_turns):
        return 0

    def short(kind, split, target, round, slice, max_turns):
        return {"agreed": 0, "dropped": 0, "turns": max_turns, "stopped": "turn_limit"}

    out = loop.run("useful", train=train_stub, score=score, generate=generate, label=short, max_turns=20,
                   round_turns=20, writer=lambda *_: pytest.fail("notes take only what a round leaves"),
                   log=lambda *_: None)
    log = label.read(loop.DATA / "useful" / "rounds.jsonl")
    assert log[-1]["stopped"] == "labelling_incomplete" and out["spent"] == 20
    assert notes.brief("useful")["avoid"] == []
    assert notes.front(notes.NOTES / "useful" / "student_wrong--options-2.md")["status"] == "targeted"


def test_a_round_that_does_not_fit_the_budget_never_starts():
    keys = validation()
    out = loop.run("useful", train=train_stub, score=lambda kind, ckpt: (set(keys[:30]), {}),
                   generate=lambda *_: pytest.fail("no paid call"), label=lambda *_: pytest.fail("no paid call"),
                   max_turns=19, round_turns=20, log=lambda *_: None)
    assert out["spent"] == 0 and label.read(loop.DATA / "useful" / "rounds.jsonl")[-1]["stopped"] == "budget"


def test_pool_keeps_one_row_per_state_and_unwraps_requests():
    sent = {"state": {"q": "x"}, "questions": {"useful_p0": {"question": NOUL}, "mystery_1": NOUL}}
    found = [{"id": "late", "startTime": "2026-02", "input": json.dumps(sent), "metadata": {}},
             {"id": "early", "startTime": "2026-01", "input": sent, "metadata": {"decision_kind": "retrieval"}},
             {"id": "other", "startTime": "2026-03", "input": {"state": {"q": "y"}, "questions": {"mystery_1": NOUL}}}]
    out = pool.rows(found)
    assert [r["id"] for r in out] == ["early"]
    assert out[0]["questions"] == {"useful_p0": {"kind": "useful", "question": NOUL}}
