"""The labelling pipeline, the error notebook and the loop, with stub labellers and models.

    python -m pytest laya -q -p no:cacheprovider
"""

import json
import socket

import pytest

import gate
import generate
import kinds
import label
import loop
import notes
import pool
import train

NOUL = {"type": "noul", "instructions": "Is the passage useful?"}


@pytest.fixture(autouse=True)
def folders(tmp_path, monkeypatch):
    monkeypatch.setattr(label, "DATA", tmp_path / "data")
    monkeypatch.setattr(loop, "DATA", tmp_path / "data")
    monkeypatch.setattr(notes, "NOTES", tmp_path / "notes")
    monkeypatch.setattr(generate, "DATA", tmp_path / "data")
    monkeypatch.setattr(generate, "searching", lambda: True)
    monkeypatch.setattr(gate, "POOL", tmp_path / "pool.jsonl")
    monkeypatch.setattr(gate, "TESTS", tmp_path / "tests.json")
    monkeypatch.setattr(train, "DATA", tmp_path / "data")
    write(tmp_path / "pool.jsonl", [])
    (tmp_path / "tests.json").write_text('{"tests": []}', encoding="utf-8")
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


@pytest.mark.parametrize("cut", ["train.judged.jsonl", "train.jsonl"])
def test_an_interrupted_verdict_is_finished_on_the_rerun(monkeypatch, cut):
    row = candidate(0)
    row["questions"] = {f"useful_p{i}": {"kind": "useful", "question": NOUL} for i in range(10)}
    write(label.DATA / "useful" / "train.candidates.jsonl", [row])

    def turn(model, state, questions):   # two agreed, eight disagreed
        return {"model": model, "cost_usd": 0.0,
                "answers": {n: {"answer": "yes" if int(n[8:]) < 2 or model == "a" else "no", "confidence": 0.9}
                            for n in questions}}

    original = label.append

    def interrupted(path, value):
        if path.name == cut:
            raise RuntimeError("interrupted")
        original(path, value)

    monkeypatch.setattr(label, "append", interrupted)
    with pytest.raises(RuntimeError):
        label.label("useful", "train", 3, ["a", "b"], 2, turn=turn, log=lambda *_: None)
    monkeypatch.setattr(label, "append", original)
    stats = label.label("useful", "train", 3, ["a", "b"], 0, turn=turn, log=lambda *_: None)
    assert (stats["agreed"], stats["dropped"], stats["states"], stats["turns"]) == (2, 8, 1, 0)
    assert len(label.read(label.DATA / "useful" / "train.jsonl")) == 1
    assert len(notes.ledger("useful")) == 8
    label.label("useful", "train", 3, ["a", "b"], 0, turn=turn, log=lambda *_: None)
    assert len(label.read(label.DATA / "useful" / "train.jsonl")) == 1 and len(notes.ledger("useful")) == 8


def test_a_torn_last_line_was_never_written_and_corruption_is_raised(tmp_path):
    import store

    path = tmp_path / "j.jsonl"
    store.append(path, {"a": 1})
    with path.open("a", encoding="utf-8") as f:
        f.write('{"b": "torn')
    assert store.read(path) == [{"a": 1}]
    store.append(path, {"c": 3})
    assert store.read(path) == [{"a": 1}, {"c": 3}]
    with path.open("a", encoding="utf-8") as f:
        f.write('{"d": 4}')   # whole, only its newline lost
    store.append(path, {"e": 5})
    assert store.read(path) == [{"a": 1}, {"c": 3}, {"d": 4}, {"e": 5}]
    path.write_text('{"a": 1}\n{"bad\n{"c": 3}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        store.read(path)
    path.write_bytes(b'{"good": 1}\n{"text": "' + "—".encode()[:1])   # torn inside a character
    assert store.read(path) == [{"good": 1}]
    store.append(path, {"next": "—"})
    assert store.read(path) == [{"good": 1}, {"next": "—"}]
    path.write_bytes(b'{"good": 1}\n{"text": "\xe2"}\n')   # a committed line that does not decode
    with pytest.raises(ValueError):
        store.read(path)


def test_a_paid_diagnosis_is_journalled_before_its_page(monkeypatch):
    import store

    for i in range(notes.PAGE_AT):
        notes.record("useful", "disagree", round=1, slice="base", state_hash=f"h{i}", source="s", question="q")
    calls = []
    original = store.write

    def cut(path, text):
        if path.name == "disagree--base.md":
            raise RuntimeError("interrupted before the page")
        original(path, text)

    monkeypatch.setattr(store, "write", cut)
    with pytest.raises(RuntimeError):
        notes.write_pages("useful", lambda *_: calls.append(1) or {"cause": "Paid once.", "next": "Go on."},
                          log=lambda *_: None, limit=1)
    monkeypatch.setattr(store, "write", original)
    notes.write_pages("useful", lambda *_: calls.append(1) or {"cause": "Paid twice."}, log=lambda *_: None)
    page = (notes.NOTES / "useful" / "disagree--base.md").read_text(encoding="utf-8")
    assert len(calls) == 1 and "Paid once." in page and "Go on." in page


def test_a_page_whose_diagnosis_was_never_journalled_is_never_overwritten():
    for i in range(notes.PAGE_AT):
        notes.record("useful", "disagree", round=1, slice="base", state_hash=f"h{i}", source="s", question="q")
    page = notes.NOTES / "useful" / "disagree--base.md"
    page.write_text("---\ncount: 5\ndiagnosed: 5\nstatus: open\n---\n\n## Cause\n\nPaid earlier.\n\n"
                    "## Next\n\nGo on.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="disagree--base.md"):
        notes.write_pages("useful", lambda *_: pytest.fail("never paid again"), log=lambda *_: None)
    assert "Paid earlier." in page.read_text(encoding="utf-8")


def test_notes_under_the_earlier_short_id_are_not_written_again():
    h = "0123456789ab" + "0" * 52
    write(notes.NOTES / "useful" / "ledger.jsonl",
          [{"id": f"useful-0-disagree-{h[:12]}-useful_p0", "kind": "useful", "round": 0, "category": "disagree",
            "slice": "base", "state_hash": h, "source": "s", "question": "useful_p0", "answers": []}])
    write(label.DATA / "useful" / "train.judged.jsonl",
          [{"state_hash": h, "labelled": 0, "dropped": 1, "by_category": {"disagree": 1}, "row": {},
            "notes": [{"category": "disagree", "round": 0, "slice": "base", "state_hash": h, "source": "s",
                       "question": "useful_p0", "intended": None, "state": {}, "answers": []}]}])
    stats = label.label("useful", "train", 5, ["a", "b"], 0, turn=lambda *_: pytest.fail("no paid call"),
                        log=lambda *_: None)
    assert stats["dropped"] == 1 and len(notes.ledger("useful")) == 1


def test_a_verdict_torn_mid_write_is_judged_again_from_the_ledger(monkeypatch):
    write(label.DATA / "useful" / "train.candidates.jsonl", [candidate(0)])

    def turn(model, state, questions):
        return {"model": model, "cost_usd": 0.0,
                "answers": {"useful_p0": {"answer": "yes" if model == "a" else "no", "confidence": 0.9}}}

    original = label.append

    def torn(path, value):
        if path.name == "train.judged.jsonl":
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(value)[:30])
            raise RuntimeError("interrupted mid-write")
        original(path, value)

    monkeypatch.setattr(label, "append", torn)
    with pytest.raises(RuntimeError):
        label.label("useful", "train", 1, ["a", "b"], 2, turn=turn, log=lambda *_: None)
    monkeypatch.setattr(label, "append", original)
    stats = label.label("useful", "train", 1, ["a", "b"], 0, turn=turn, log=lambda *_: None)
    assert (stats["dropped"], stats["states"], stats["turns"]) == (1, 1, 0)
    assert len(notes.ledger("useful")) == 1


def test_records_from_before_verdicts_and_split_names_still_resume():
    assert loop.restore([{"round": 0, "splits": ["train"]}, {"round": 1, "slice": ["options", 2], "adopted": True},
                         {"prune": "aug-1", "removed": True}])[0] == ["train"]
    assert loop.restore([{"round": 1, "slice": ["options", 2], "adopted": True}])[0] == ["train", "aug-1"]
    write(label.DATA / "useful" / "train.judged.jsonl",
          [{"state_hash": "h0", "labelled": 1, "dropped": 0, "by_category": {}}])
    write(label.DATA / "useful" / "train.candidates.jsonl", [candidate(0)])
    stats = label.label("useful", "train", 5, ["a", "b"], 0, turn=lambda *_: pytest.fail("no paid call"),
                        log=lambda *_: None)
    assert (stats["agreed"], stats["states"]) == (1, 1)


def test_notes_of_states_sharing_a_hash_prefix_are_both_kept():
    rows = [candidate(0), candidate(1)]
    rows[0]["state_hash"], rows[1]["state_hash"] = "0123456789ab" + "0" * 52, "0123456789ab" + "1" * 52
    write(label.DATA / "useful" / "train.candidates.jsonl", rows)

    def turn(model, state, questions):
        return {"model": model, "cost_usd": 0.0,
                "answers": {"useful_p0": {"answer": "yes" if model == "a" else "no", "confidence": 0.9}}}

    label.label("useful", "train", 5, ["a", "b"], 10, turn=turn, log=lambda *_: None)
    assert len(notes.ledger("useful")) == 2


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
    notes.write_pages("useful", lambda *_: pytest.fail("no turns left"), log=lambda *_: None, limit=0)
    assert "Negation flips it." in page.read_text(encoding="utf-8")   # grown without a turn: kept
    seen.clear()
    notes.write_pages("useful", lambda prompt, payload: seen.append(payload) or {"cause": "Now numbers too."},
                      log=lambda *_: None)
    assert len(seen) == 1 and "Now numbers too." in page.read_text(encoding="utf-8")   # the deferred update
    assert "Generate negated states." in page.read_text(encoding="utf-8")   # a section not given is kept
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
        return wrong, {k: k not in wrong for k in keys}

    def generate(kind, slice, n, history, round, max_turns):
        asked.append((round, slice))
        limits.append(max_turns)
        done = [r["round"] for r in history["rounds"] if "slice" in r and not r.get("started")]
        assert done == list(range(1, round))   # every earlier round's outcome
        started = label.read(loop.DATA / kind / "rounds.jsonl")[-1]
        assert (started["round"], started["started"]) == (round, True)   # journalled before any paid turn
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
    log = [r for r in label.read(loop.DATA / "useful" / "rounds.jsonl") if not r.get("started")]
    assert [r.get("adopted") for r in log[1:4]] == [True, False, False]
    assert log[2]["reason"] == "within_noise" and log[3]["reason"] == "ambiguous_slice"
    assert log[4]["prune"] == "aug-1" and log[4]["removed"] is False
    assert sorted(notes.brief("useful")["avoid"]) == [["label", "True"], ["options", 2]]
    left = [n for n in notes.ledger("useful") if n["round"] == 1]
    assert len(left) == 12 and all(n["slice"] == ["options", 2] for n in left)


def test_labelling_that_stops_short_stops_the_loop_without_condemning_the_slice():
    keys = validation()

    def score(kind, ckpt):
        return set(keys[:30]), dict.fromkeys(keys)

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


def test_running_the_loop_again_continues_it():
    keys = validation()
    calls = []

    def score(kind, ckpt):
        return set(keys[:12] if "aug-2" in ckpt else keys[:30]), dict.fromkeys(keys)

    def generate(kind, slice, n, history, round, max_turns):
        calls.append((round, slice))
        path = loop.DATA / kind / f"aug-{round}.candidates.jsonl"
        if not path.exists():
            write(path, [{"slice": list(slice)}])
        return 0

    results = iter([{"agreed": 0, "dropped": 0, "turns": 5, "stopped": "turn_limit"},   # run 1, round 1
                    {"agreed": 27, "dropped": 3, "turns": 5, "stopped": "target"},      # run 2, round 1 resumed
                    {"agreed": 27, "dropped": 3, "turns": 5, "stopped": "target"}])     # run 2, round 2

    def label_(kind, split, target, round, slice, max_turns):
        return next(results)

    common = {"train": train_stub, "score": score, "generate": generate, "label": label_, "round_turns": 5,
              "log": lambda *_: None}
    loop.run("useful", max_turns=5, **common)
    out = loop.run("useful", max_turns=10, **common)
    # Round 1 resumes with its own slice; it is falsified, so round 2 is a new number and a new split.
    assert calls == [(1, ("options", 2)), (1, ("options", 2)), (2, ("length", "short"))]
    assert out["splits"] == ["train", "aug-2"]
    assert loop.restore(label.read(loop.DATA / "useful" / "rounds.jsonl")) == \
        (["train", "aug-2"], set(), 2, None, (2, ("length", "short")))

    def wrong_slice(kind, slice, n, history, round, max_turns):
        write(loop.DATA / kind / f"aug-{round}.candidates.jsonl", [{"slice": ["length", "long"]}])
        return 0

    with pytest.raises(ValueError):
        loop.run("useful", max_turns=10, **common | {"generate": wrong_slice})


@pytest.mark.parametrize("cut", ["outcome", "status"])
def test_an_interruption_around_a_rounds_outcome_resumes_the_same_round(monkeypatch, cut):
    keys = validation()
    calls = []

    def generate(kind, slice, n, history, round, max_turns):
        calls.append((round, slice))
        path = loop.DATA / kind / f"aug-{round}.candidates.jsonl"
        if not path.exists():
            write(path, [{"slice": list(slice)}])
        return 0

    common = {"train": train_stub, "score": lambda kind, ckpt: (set(keys[:30]), dict.fromkeys(keys)),
              "generate": generate,
              "label": lambda *_: {"agreed": 27, "dropped": 3, "turns": 0, "stopped": "target"},
              "rounds": 1, "max_turns": 10, "round_turns": 5, "log": lambda *_: None}
    original_append, original_status = loop.append, notes.set_status

    def append(path, row):
        if row.get("reason") == "within_noise":
            raise RuntimeError("interrupted before the outcome")
        original_append(path, row)

    def set_status(kind, name, status, round, slice="base"):
        if status == "falsified":
            raise RuntimeError("interrupted before the page")
        original_status(kind, name, status, round, slice)

    if cut == "outcome":
        monkeypatch.setattr(loop, "append", append)
    else:
        monkeypatch.setattr(notes, "set_status", set_status)
    with pytest.raises(RuntimeError):
        loop.run("useful", **common)
    monkeypatch.setattr(loop, "append", original_append)
    monkeypatch.setattr(notes, "set_status", original_status)
    loop.run("useful", **common | {"rounds": 2})
    page = notes.NOTES / "useful" / "student_wrong--options-2.md"
    assert notes.front(page)["status"] == "falsified"
    if cut == "outcome":   # the outcome was never journalled: round 1 is done again, on its own slice
        assert calls[:2] == [(1, ("options", 2)), (1, ("options", 2))]
    else:                  # journalled: replayed onto the page, and round 2 moves on
        assert calls[:2] == [(1, ("options", 2)), (2, ("length", "short"))]


def test_a_round_that_does_not_fit_the_budget_never_starts():
    keys = validation()
    out = loop.run("useful", train=train_stub, score=lambda kind, ckpt: (set(keys[:30]), dict.fromkeys(keys)),
                   generate=lambda *_: pytest.fail("no paid call"), label=lambda *_: pytest.fail("no paid call"),
                   max_turns=19, round_turns=20, log=lambda *_: None)
    assert out["spent"] == 0 and label.read(loop.DATA / "useful" / "rounds.jsonl")[-1]["stopped"] == "budget"


def test_the_loop_counts_only_the_questions_scored():
    keys = validation()
    common = {"train": train_stub, "generate": lambda *_: pytest.fail("no paid call"),
              "label": lambda *_: pytest.fail("no paid call"), "max_turns": 0, "round_turns": 20,
              "log": lambda *_: None}
    # Ten questions over the length limit are left out: accuracy is over the thirty scored.
    out = loop.run("useful", score=lambda kind, ckpt: (set(keys[:15]), dict.fromkeys(keys[:30])), **common)
    assert out["accuracy"] == pytest.approx(15 / 30)
    assert label.read(loop.DATA / "useful" / "rounds.jsonl")[0]["left_for_length"] == 10
    with pytest.raises(ValueError):   # nothing scored is no accuracy at all
        loop.run("useful", score=lambda kind, ckpt: (set(), {}), **common)
    with pytest.raises(ValueError):   # wrong where it was not scored
        loop.run("useful", score=lambda kind, ckpt: ({keys[35]}, dict.fromkeys(keys[:30])), **common)


def test_pool_keeps_one_row_per_state_and_unwraps_requests():
    sent = {"state": {"q": "x"}, "questions": {"useful_p0": {"question": NOUL}, "mystery_1": NOUL}}
    found = [{"id": "late", "startTime": "2026-02", "input": json.dumps(sent), "metadata": {}},
             {"id": "early", "startTime": "2026-01", "input": sent, "metadata": {"decision_kind": "retrieval"}},
             {"id": "other", "startTime": "2026-03", "input": {"state": {"q": "y"}, "questions": {"mystery_1": NOUL}}}]
    out = pool.rows(found)
    assert [r["id"] for r in out] == ["early"]
    assert out[0]["questions"] == {"useful_p0": {"kind": "useful", "question": NOUL}}


def test_gate_keeps_near_duplicates_and_a_states_kinds_on_one_side():
    text = " ".join(f"word{i}" for i in range(200))
    passages = [{"id": "p0", "text": "a passage"}]
    rows = [{**candidate(i), "decision_kind": "retrieval", "state": {"query": text + f" tail{i}", "passages": passages}}
            for i in range(3)]
    rows.append({**candidate(9), "decision_kind": "retrieval",
                 "state": {"query": "something else entirely", "passages": passages}})
    assert sorted(len(u) for u in gate.units(rows)) == [1, 3]
    reserved = gate.reserve(rows)
    assert len(reserved["tests"]) == 1          # two thirds of two units, rounded
    assert all(r["kinds"] == ["conflict", "useful"] and "state" not in r for r in reserved["tests"][0])


def test_rows_alike_only_in_a_passage_view_are_one_unit_and_kept_out_of_development():
    shared = " ".join(f"shared{i}" for i in range(200))

    def row(i, other):
        passages = [{"id": "p0", "text": shared}, {"id": "p1", "text": " ".join(f"{other}{k}" for k in range(600))}]
        return {"id": f"obs-{i}", "state_hash": f"h{i}", "decision_kind": "retrieval",
                "state": {"query": "q", "passages": passages},
                "questions": {"useful_p0": {"kind": "useful", "question": NOUL}}}

    a, b = row(1, "alpha"), row(2, "beta")
    whole = gate.shingles(a["state"]), gate.shingles(b["state"])
    assert len(whole[0] & whole[1]) < 0.8 * len(whole[0] | whole[1])   # the whole states differ
    assert [len(u) for u in gate.units([a, b])] == [2]
    assert gate.development([a, b], {"h2"}) == []                      # a's view is b's, which is gated
    assert gate.development([a, b], set()) == [a, b]


def passage(tag):
    return {"id": "p0", "text": " ".join(f"{tag}{k}" for k in range(80))}


def judged(query):
    """What `generate.captured` returns for `query`: one grade-evidence request, two passages."""

    state = {"query": query, "passages": [passage(f"{query}a"), {**passage(f"{query}b"), "id": "p1"}]}
    return [{"state": state, "questions": {"useful_p0": NOUL, "useful_p1": NOUL, "conflict_p0": NOUL}}]


class Teacher:
    def __init__(self):
        self.turns = []

    def __call__(self, model, seed):
        self.turns.append(seed)
        return {"model": model, "cost_usd": 0.1, "seed": seed, "pages": ["docs/a.md"],
                "queries": [{"text": f"s{seed}q{i}", "type": "answered"} for i in range(2)]}


def test_generate_stops_at_target_and_resumes_without_paying_twice(folders):
    teacher, ran = Teacher(), []

    def capture(query):
        ran.append(query)
        return judged(query)

    out = generate.generate("useful", "train", 3, "opus", 5, capture=capture, turn=teacher, log=lambda *_: None)
    assert out["stopped"] == "target" and out["have"] == 3 and teacher.turns == [0] and ran == ["s0q0", "s0q1"]
    rows = label.read(folders / "data" / "useful" / "train.candidates.jsonl")
    assert [list(r["questions"]) for r in rows] == [["useful_p0"], ["useful_p1"], ["useful_p0"]]
    assert rows[0]["state"]["passages"] == [passage("s0q0a")]          # the passage view
    assert rows[0]["provenance"]["query"] == "s0q0" and rows[0]["provenance"]["seed"] == 0
    out = generate.generate("useful", "train", 4, "opus", 5, capture=capture, turn=teacher, log=lambda *_: None)
    assert teacher.turns == [0, 1] and ran == ["s0q0", "s0q1", "s1q0"]   # s0's queries are not run again
    assert out["have"] == 4


def test_generate_stops_at_its_turn_limit_and_records_a_failed_query(folders):
    teacher = Teacher()

    def capture(query):
        if query.endswith("q1"):
            raise RuntimeError("retrieval failed")
        return judged(query)

    out = generate.generate("useful", "train", 100, "opus", 2, capture=capture, turn=teacher, log=lambda *_: None)
    assert out["stopped"] == "turn_limit" and teacher.turns == [0, 1] and out["have"] == 4
    runs = label.read(folders / "data" / "useful" / "generate.runs.jsonl")
    assert [r["run"] for r in runs if "error" in r] == ["t0-q1", "t1-q1"]


def test_generate_drops_near_duplicates_of_the_gate_and_of_other_splits(folders):
    gated = {"id": "obs", "state_hash": "g", "decision_kind": "retrieval", **judged("s0q0")[0],
             "questions": {"useful_p0": {"kind": "useful", "question": NOUL}}}
    write(folders / "pool.jsonl", [gated])
    (folders / "tests.json").write_text(json.dumps({"tests": [[{"state_hash": "g"}]]}), encoding="utf-8")
    teacher = Teacher()
    out = generate.generate("useful", "validation", 10, "opus", 1, capture=judged, turn=teacher,
                            log=lambda *_: None)
    assert out["near_gate"] == 2 and out["have"] == 2          # s0q0's request is the gate's, both views
    out = generate.generate("useful", "train", 10, "opus", 0, capture=judged, turn=teacher, log=lambda *_: None)
    assert out["have"] == 0                                    # no turn allowed, and nothing to rerun
    write(folders / "data" / "useful" / "generate.runs.jsonl", [])   # run the paid queries again
    out = generate.generate("useful", "train", 10, "opus", 0, capture=judged, turn=teacher, log=lambda *_: None)
    assert out["near_kept"] == 2 and out["have"] == 0          # validation already holds them
    with pytest.raises(ValueError):
        generate.generate("route", "train", 1, "opus", 1, capture=judged, turn=teacher)


def test_generate_checks_a_request_whole_before_cutting_it_into_views(folders):
    shared = " ".join(f"shared{i}" for i in range(1200))

    def request(tag):
        return {"query": "q", "passages": [{"id": "p0", "text": shared},
                                           {"id": "p1", "text": " ".join(f"{tag}{i}" for i in range(80))}]}

    both = {"useful_p0": NOUL, "useful_p1": NOUL}
    write(folders / "pool.jsonl", [{"id": "obs", "state_hash": "g", "decision_kind": "retrieval",
                                    "state": request("old"), "questions": {
                                        n: {"kind": "useful", "question": NOUL} for n in both}}])
    (folders / "tests.json").write_text(json.dumps({"tests": [[{"state_hash": "g"}]]}), encoding="utf-8")
    # The new request's p1 view is unlike anything gated, but the whole request is the gate's.
    out = generate.generate("useful", "train", 10, "opus", 1, turn=Teacher(), log=lambda *_: None,
                            capture=lambda q: [{"state": request("new"), "questions": both}])
    assert out["near_gate"] == 4 and out["have"] == 0


def test_generate_keeps_one_request_on_one_split(folders):
    shared = " ".join(f"shared{i}" for i in range(400))

    def capture(query):
        # The same question answered over a different second passage: one request, near-duplicate whole.
        return [{"state": {"query": "q", "passages": [{"id": "p0", "text": shared},
                                                       {"id": "p1", "text": f"{query} " + shared[:200]}]},
                 "questions": {"useful_p0": NOUL, "useful_p1": NOUL}}]

    out = generate.generate("useful", "validation", 1, "opus", 1, capture=capture, turn=Teacher(),
                            log=lambda *_: None)
    assert out["have"] == 1
    out = generate.generate("useful", "train", 10, "opus", 0, capture=capture, turn=Teacher(),
                            log=lambda *_: None)
    assert out["other_split"] == 2 and out["have"] == 0        # s0q1 is near s0q0, which went to validation


def test_a_teacher_turn_left_pending_stops_new_turns_until_it_is_abandoned(folders):
    teacher = Teacher()
    ledger = folders / "data" / "useful" / "generate.ledger.jsonl"
    write(ledger, [{"seed": 0, "pending": True, "model": "opus", "prompt_version": "x"}])
    out = generate.generate("useful", "train", 10, "opus", 3, capture=judged, turn=teacher, log=lambda *_: None)
    assert out["stopped"] == "unsettled_turn" and out["unsettled"] == [0] and teacher.turns == []
    generate.abandon("useful", 0)
    out = generate.generate("useful", "train", 1, "opus", 3, capture=judged, turn=teacher, log=lambda *_: None)
    assert teacher.turns == [1] and out["have"] == 1           # seed 0 is never sent again
    with pytest.raises(SystemExit):
        generate.abandon("useful", 1)                          # settled, not pending


def test_generate_refuses_without_every_gate_state_or_a_running_search(folders):
    teacher = Teacher()
    (folders / "tests.json").write_text(json.dumps({"tests": [[{"state_hash": "missing"}]]}), encoding="utf-8")
    with pytest.raises(SystemExit):
        generate.generate("useful", "train", 1, "opus", 1, capture=judged, turn=teacher)
    (folders / "tests.json").write_text('{"tests": []}', encoding="utf-8")
    with pytest.raises(SystemExit):
        generate.generate("useful", "train", 1, "opus", 1, capture=judged, turn=teacher, ready=lambda: False)
    runs = folders / "data" / "useful" / "generate.runs.jsonl"
    write(runs, [{"run": "t0-q0", "split": "train", "candidates": 0, "error": "RuntimeError: x"}])
    generate.generate("useful", "train", 0, "opus", 0, capture=judged, turn=teacher)   # a failed run owns nothing
    write(runs, [{"run": "t0-q0", "split": "train", "candidates": 1, "originals": [judged("a")[0]]}])
    with pytest.raises(SystemExit, match="no claim"):   # from before claims: its split's ownership is lost
        generate.generate("useful", "validation", 1, "opus", 1, capture=judged, turn=teacher)
    assert teacher.turns == []


def test_a_run_interrupted_after_its_candidates_keeps_its_request_on_its_split(folders, monkeypatch):
    real, runs_written = generate.append, []

    class Interrupted(BaseException):
        pass

    def append(path, row):
        if path.name == "generate.runs.jsonl":
            runs_written.append(row)
            if len(runs_written) == 2:   # the claim is written, then a candidate, then this: the run's end
                raise Interrupted()
        real(path, row)

    monkeypatch.setattr(generate, "append", append)
    teacher = Teacher()
    with pytest.raises(Interrupted):
        generate.generate("useful", "train", 1, "opus", 1, capture=judged, turn=teacher, log=lambda *_: None)
    monkeypatch.setattr(generate, "append", real)
    out = generate.generate("useful", "validation", 5, "opus", 0, capture=judged, turn=teacher, log=lambda *_: None)
    validation = label.read(folders / "data" / "useful" / "validation.candidates.jsonl")
    assert {r["provenance"]["run"] for r in validation} == {"t0-q1"}   # t0-q0 stays train's
    assert out["have"] == 2 and teacher.turns == [0]


def test_a_run_interrupted_after_its_claim_finishes_on_the_claim_not_a_new_capture(folders, monkeypatch):
    real, captures = generate.append, []

    class Interrupted(BaseException):
        pass

    def append(path, row):
        if path.name == "train.candidates.jsonl":
            raise Interrupted()
        real(path, row)

    def capture(query):   # the index changed between the interruption and the rerun
        captures.append(query)
        return judged("before" if len(captures) == 1 else "after")

    monkeypatch.setattr(generate, "append", append)
    teacher = Teacher()
    with pytest.raises(Interrupted):
        generate.generate("useful", "train", 1, "opus", 1, capture=capture, turn=teacher, log=lambda *_: None)
    monkeypatch.setattr(generate, "append", real)
    generate.generate("useful", "train", 1, "opus", 0, capture=capture, turn=teacher, log=lambda *_: None)
    train = label.read(folders / "data" / "useful" / "train.candidates.jsonl")
    assert len(captures) == 1 and train[0]["state"]["passages"] == [passage("beforea")]
    out = generate.generate("useful", "validation", 5, "opus", 0, capture=capture, turn=teacher, log=lambda *_: None)
    validation = label.read(folders / "data" / "useful" / "validation.candidates.jsonl")
    assert {r["provenance"]["run"] for r in validation} == {"t0-q1"} and out["have"] == 2
    assert teacher.turns == [0]


def test_a_search_daemon_gone_mid_run_leaves_the_query_and_starts_no_turn(folders):
    up = [True]
    teacher = Teacher()

    def turn(model, seed):
        up[0] = False   # the daemon dies while the teacher writes
        return teacher(model, seed)

    def capture(query):
        if not up[0]:
            raise generate.Unavailable("retrieval_unavailable")   # what `captured` gives with no daemon
        return judged(query)

    out = generate.generate("useful", "train", 10, "opus", 3, capture=capture, turn=turn, ready=lambda: up[0],
                            log=lambda *_: None)
    assert out["stopped"] == "search_unavailable" and teacher.turns == [0] and out["have"] == 0
    assert out["reason"] == "t0-q0: retrieval_unavailable"
    assert label.read(folders / "data" / "useful" / "generate.runs.jsonl") == []
    up[0] = True
    out = generate.generate("useful", "train", 10, "opus", 0, capture=capture, turn=turn, ready=lambda: True,
                            log=lambda *_: None)
    assert out["have"] == 4 and teacher.turns == [0]            # the paid queries ran on the rerun


def test_captured_sends_nothing_and_stops_at_the_first_judge(monkeypatch):
    import sys
    if str(generate.ROOT / "tool") not in sys.path:
        sys.path.append(str(generate.ROOT / "tool"))   # after this folder, as `generate.captured` adds it
    import decision
    import translate
    from main import knowledge

    import search

    seen, asked = {}, []
    monkeypatch.setattr(knowledge, "retrieve_from_daemon", lambda *a, **k: asked.append(k) or None)

    def prepare(query, project, cfg, cache, cancel):
        seen["parts"] = translate.parts(query, 1e12)           # the translator's only request answers nothing
        seen["fallback"] = knowledge.falls_back(cfg)
        knowledge.retrieve_from_daemon({}, project, 1.0)       # a running daemon only
        seen["cold"] = knowledge.cold({}, project, cancel)     # no index built here
        with pytest.raises(RuntimeError):
            search.spawn()
        with pytest.raises(OSError, match="leaves this machine"):   # whatever path tries it
            socket.create_connection(("example.com", 443), timeout=1)
        with socket.socket() as s, pytest.raises(OSError, match="leaves this machine"):
            s.connect(("203.0.113.1", 443))
        for stage in ("route", "judge", "judge"):
            if not cancel.is_set():
                decision.evaluate(cfg, {"stage": stage}, {"useful_p0": NOUL}, [], None, stage)
        seen["project"] = project
        return {"status": "cancelled"}

    monkeypatch.setattr(knowledge, "prepare", prepare)
    real = translate._ask
    got = generate.captured("Who owns deployment, and which port?")
    assert got == [{"state": {"stage": "judge"}, "questions": {"useful_p0": NOUL}}]
    assert seen == {"parts": None, "fallback": False, "cold": None, "project": generate.ROOT}
    assert asked == [{"start": False}]
    assert translate._ask is real and search.spawn is not None and knowledge.cold.__name__ == "cold"
    assert socket.getaddrinfo.__module__ == "socket" and socket.socket.connect.__name__ == "connect"


def test_a_capture_tells_a_failed_retrieval_from_an_empty_one(monkeypatch):
    import sys
    if str(generate.ROOT / "tool") not in sys.path:
        sys.path.append(str(generate.ROOT / "tool"))
    from main import knowledge

    for status, reason, limits, got in (
            ("unavailable", "retrieval_unavailable", [], generate.Unavailable),
            ("unavailable", "normalization_failed", [{"baseline": "retrieval_unavailable"}], generate.Unavailable),
            ("exhausted", "deadline", [], generate.Unavailable),
            ("cancelled", "cancelled", [], generate.Unavailable),
            ("unavailable", "normalization_failed", [], RuntimeError),   # this query's own, every time
            ("partial", "no_repair", [], []), ("ready", "covered", [], [])):
        monkeypatch.setattr(knowledge, "prepare", lambda *a, d={"status": status, "reason": reason,
                                                                "limits": limits}, **k: d)
        if got == []:
            assert generate.captured("q") == []
        else:
            with pytest.raises(got, match=reason):
                generate.captured("q")


def test_the_teacher_samples_documents_the_capture_can_search():
    import sys
    if str(generate.ROOT / "tool") not in sys.path:
        sys.path.append(str(generate.ROOT / "tool"))
    from search import HUB, sources

    searched = {p.resolve() for p in sources.listing(HUB, generate.ROOT)}
    assert all((generate.ROOT / p["path"]).resolve() in searched for p in generate.sample(0))


def test_a_passage_kind_sees_only_its_passage():
    state = {"query": "q", "passages": [{"id": "p0", "text": "a"}, {"id": "p1", "text": "b"}],
             "complete_passages": ["p0", "p1"]}
    assert kinds.view("useful_p1", "useful", state) == {"query": "q", "passages": [{"id": "p1", "text": "b"}],
                                                        "complete_passages": ["p1"]}
    assert kinds.view("coverage_r0", "coverage", state) is state
    with pytest.raises(ValueError):
        kinds.view("conflict_p7", "conflict", state)


def test_training_leaves_out_what_is_too_long_and_packs_code_with_labels(folders, monkeypatch):
    import zipfile

    def row(i, text):
        return {"state": {"query": "q", "passages": [{"id": "p0", "text": text}]},
                "questions": {"useful_p0": NOUL}, "expected": {"useful_p0": i % 2 == 0},
                "provenance": {"state_hash": f"h{i}"}}

    write(folders / "data" / "useful" / "train.jsonl", [row(0, "short"), row(1, "long " * 50)])
    write(folders / "data" / "useful" / "train.candidates.jsonl", [row(2, "unlabelled")])
    monkeypatch.setattr(train, "tokens", lambda agent, state, q: len(state["passages"][0]["text"].split()))
    path, left = train.merged("useful", ["train"], None, 10)
    rows = label.read(path)
    assert left == 1 and [r["state"]["passages"][0]["text"] for r in rows] == ["short"]
    assert set(rows[0]) == {"state", "questions", "expected"}          # Laya's format, no provenance
    with zipfile.ZipFile(train.pack("useful")) as z:
        names = set(z.namelist())
        packed = json.loads(z.read("laya/packed.json"))
    assert set(packed["splits"]) == {"train"} and len(packed["commit"]) == 40   # a result traces back to these
    assert {"laya/train.py", "laya/label.py", "laya/stage1.py", "laya/data/useful/train.jsonl"} <= names
    assert not any("test_" in n or "candidates" in n or "pool" in n.split("/")[-1].split(".")[0] for n in names
                   if n.startswith("laya/data/"))
    assert "laya/test_laya.py" not in names

    # `train` with the package stubbed: the parallel layout, and calibration that asks only
    # the questions it has targets for (a row whose second question is too long).
    import sys
    import types
    from pathlib import Path
    seen, agent = {}, types.SimpleNamespace(
        fit_temperatures=lambda recs: seen.update(recs=recs), save_calibration=lambda path: None,
        predict=lambda state, asked, **k: {"answers": {n: {"noul": 0.8, "confidence": 0.8} for n in asked}})

    def finetune(data, start, out, config):
        Path(out).mkdir(parents=True)
        seen["config"] = config
        return {}

    def records_from_labeled(agent, pairs):
        assert all(set(questions) == set(targets) for _, questions, targets in pairs)
        return pairs

    mixed = row(4, "short") | {"questions": {"useful_p0": NOUL, "useful_p1": NOUL | {"long": True}},
                               "expected": {"useful_p0": True, "useful_p1": False}}
    write(folders / "data" / "useful" / "calibration.jsonl", [mixed])
    monkeypatch.setattr(train, "tokens", lambda agent, state, q: 3000 if q.get("long") else 1)
    monkeypatch.setattr(train, "base_dir", lambda name: "base")
    monkeypatch.setattr(train, "CHECKPOINTS", folders / "checkpoints")
    for name, attrs in {"laya": {"load": lambda *a, **k: agent},
                        "laya.train": {"TrainConfig": lambda **k: k, "finetune": finetune},
                        "laya.calibrate": {"records_from_labeled": records_from_labeled}}.items():
        monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(**attrs))
    train.train("useful", ["train"], micro_batch=4)
    assert seen["config"]["option_layout"] == "parallel" and seen["config"]["micro_batch"] == 4
    assert [list(q) for _, q, _ in seen["recs"]] == [["useful_p0"]]

    # `score` hands back each answer's confidence beside the answers the loop reads.
    write(folders / "data" / "useful" / "validation.jsonl", [row(0, "short"), row(1, "short")])
    sure = {}
    wrong, predicted = train.score("useful", "base", sure)
    assert predicted == {("h0", "useful_p0"): True, ("h1", "useful_p0"): True} and wrong == {("h1", "useful_p0")}
    assert sure == dict.fromkeys(predicted, 0.8)
