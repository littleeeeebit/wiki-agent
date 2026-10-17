"""Guided labelling, resumable turns and verified training evidence."""

import sys
import pytest
import augment
import gate
import label
import notes
import pool
import store
from test_laya import folders, write, NOUL, candidate  # noqa: F401 -- preserve autouse fixture

# ruff: noqa: F811 -- pytest injects the imported fixture by name


def truth(i):
    return i % 3 != 0


def labelled(i):
    return {"state": {"i": i}, "questions": {"useful_p0": NOUL}, "expected": {"useful_p0": truth(i)},
            "provenance": {"state_hash": f"h{i}", "source": f"s{i}"}}


def guided_world(stamp=False, rule_helps=True):
    """A labeller wrong on every fourth state until its guide holds `rule`, a verifier that
    answers the truth (or, with `stamp`, accepts whatever it is shown), and a lesson that
    proposes `rule`. Returns the call and the list of calls made."""

    made = []

    def call(prompt, payload, model, effort):
        made.append((prompt.name, payload))
        if prompt.name == "label.md":
            i = payload["state"]["i"]
            ruled = "rule" in payload.get("guide", [])
            right = (ruled or i % 4 != 0) if rule_helps else (i % 4 != 0 and not (ruled and i % 4 == 1))
            said = truth(i) if right else not truth(i)
            return {"model": model, "cost_usd": 0.01,
                    "json": {"answers": {n: {"answer": "yes" if said else "no", "confidence": 0.9}
                                         for n in payload["questions"]}}}
        if prompt.name == "verify.md":
            out = {}
            for it in payload["items"]:
                real = "yes" if truth(it["state"]["i"]) else "no"
                answer = it["proposed"] if stamp else real
                out[it["id"]] = {"evidence": "e", "answer": answer, "confidence": 0.95,
                                 "verdict": "accept" if it["proposed"] == answer else "reject"}
            return {"model": model, "cost_usd": None, "json": {"verdicts": out}}
        return {"model": model, "cost_usd": None, "json": {"rules": ["rule"], "why": "fourth states"}}
    return call, made


def test_the_guided_labeller_learns_from_rejections_keeps_verified_labels_and_never_pays_twice(folders):
    import guided
    rows = [labelled(i) for i in range(80)]
    call, made = guided_world()
    common = dict(labeller="haiku", verifier="sol", rounds=2, per_round=40, max_turns=10_000, log=lambda *_: None)
    got = guided.run("useful", "pilot", rows, max_usd=100, call=call, **common)
    first, second = got["rounds"]
    # Round 0: every fourth state is labelled wrongly, rejected, and taught back as a rule.
    assert (first["guide"], first["kept"] + first["rejected"], first["unreliable"]) == (0, 40, 0)
    assert first["rejected"] >= guided.LESSON_AT and first["kept_match_earlier"] == 1.0 and "trial" not in first
    # Round 1 labels its states with both guides; the candidate earns more verified labels and is adopted.
    assert second["trial"]["adopted"] and second["trial"]["kept_rate"][1] == 1.0 > second["trial"]["kept_rate"][0]
    assert (second["guide"], second["kept"], second["rejected"]) == (1, 40, 0)
    round1 = [p for name, p in made if name == "label.md"][40:]
    assert len(round1) == 80 and sum(p.get("guide") == ["rule"] for p in round1) == 40
    kept = guided.read(folders / "data" / "useful" / "pilot.jsonl")
    assert len(kept) == first["kept"] + 40 and {r["provenance"]["guide"] for r in kept} == {0, 1}
    assert all(r["expected"]["useful_p0"] == truth(r["state"]["i"]) for r in kept)
    assert sum(n["category"] == "verifier_reject" for n in notes.ledger("useful")) == first["rejected"]
    assert [g["round"] for g in guided.read(folders / "data" / "useful" / "pilot.guide.jsonl")] == [1]
    # Every verified batch carried its flipped copies, and the verifier never saw which guide wrote an answer.
    sent = [p for name, p in made if name == "verify.md"]
    assert all(guided.MIRRORS < len(p["items"]) <= guided.BATCH + guided.MIRRORS for p in sent)
    assert all(set(it) == {"id", "state", "question", "proposed"} for p in sent for it in p["items"])
    # A rerun replays every turn from the file and pays for none.
    paid = len(made)
    assert guided.run("useful", "pilot", rows, max_usd=100, call=call, **common)["rounds"] == got["rounds"]
    assert len(made) == paid and len(guided.read(folders / "data" / "useful" / "pilot.jsonl")) == len(kept)


def test_a_candidate_guide_that_earns_fewer_verified_labels_is_not_adopted(folders):
    import guided
    call, _ = guided_world(rule_helps=False)
    got = guided.run("useful", "pilot", [labelled(i) for i in range(80)], labeller="haiku", verifier="sol",
                     rounds=2, per_round=40, max_turns=10_000, max_usd=100, call=call, log=lambda *_: None)
    second = got["rounds"][1]
    assert not second["trial"]["adopted"] and second["guide"] == 0 and got["guide"]["version"] == 0
    kept = guided.read(folders / "data" / "useful" / "pilot.jsonl")
    assert {r["provenance"]["guide"] for r in kept} == {0}
    (trial,) = guided.read(folders / "data" / "useful" / "pilot.guide.jsonl")
    assert trial["adopted"] is False and trial["rules"] == ["rule"]


def test_the_spending_limit_stops_and_a_rerun_resumes_without_paying_twice(folders, monkeypatch):
    import guided
    rows = [labelled(i) for i in range(80)]
    common = dict(labeller="haiku", verifier="sol", rounds=2, per_round=40, max_turns=10_000, log=lambda *_: None)
    call, made = guided_world()
    cut = guided.run("useful", "pilot", rows, max_usd=0.05, call=call, **common)
    # Turns already in flight may finish past the limit, at most one worker pool's worth.
    assert cut["stopped"] == "limit" and 5 <= len(made) < 5 + guided.LABELLERS and cut["usd_in_all"] >= 0.05
    whole = guided.run("useful", "pilot", rows, max_usd=100, call=call, **common)
    call2, made2 = guided_world()
    monkeypatch.setattr(label, "DATA", folders / "fresh")
    alone = guided.run("useful", "pilot", rows, max_usd=100, call=call2, **common)
    assert whole["stopped"] == "done" and whole["rounds"] == alone["rounds"] and len(made) == len(made2)


def test_guided_rows_come_from_candidates_with_a_generator_provenance_or_from_labels(folders):
    import guided
    write(folders / "data" / "useful" / "c.jsonl", [{**candidate(0), "provenance": {"generator": "g"}}])
    write(folders / "data" / "useful" / "l.jsonl", [labelled(1)])
    (c,), (lab,) = guided.rows_of("useful", "c.jsonl"), guided.rows_of("useful", "l.jsonl")
    assert c == {"state": {"text": "state 0"}, "questions": {"useful_p0": NOUL},
                 "provenance": {"state_hash": "h0", "source": "obs-0"}}
    assert lab == labelled(1)


def test_an_agreed_absence_is_kept_though_the_verifier_has_no_evidence_to_quote():
    import guided
    item = {"q": NOUL, "value": False}
    said = {"evidence": "none", "answer": "no", "verdict": "unclear"}
    assert guided.kept(item, {**said, "confidence": 1.0})
    assert not guided.kept(item, {**said, "confidence": 0.5})                     # unsure
    assert not guided.kept(item, {**said, "answer": "yes", "confidence": 1.0})   # disagrees
    assert not guided.kept(item, "unreliable")


def test_a_turn_that_fails_is_tried_again_and_only_its_success_is_kept(folders):
    import guided
    call, made = guided_world()
    flaky = {"left": 2}

    def failing(prompt, payload, model, effort):
        if prompt.name == "verify.md" and flaky["left"]:
            flaky["left"] -= 1
            made.append((prompt.name, payload))
            return {"model": model, "cost_usd": None, "error": "RuntimeError: None", "json": {}}
        return call(prompt, payload, model, effort)
    got = guided.run("useful", "pilot", [labelled(i) for i in range(1, 4)], labeller="haiku",
                     verifier="sol", rounds=1, per_round=3, max_turns=100, max_usd=100, call=failing,
                     log=lambda *_: None)
    assert got["stopped"] == "done" and sum(name == "verify.md" for name, _ in made) == 3
    journal = [r for r in guided.read(folders / "data" / "useful" / "pilot.turns.jsonl") if r["prompt"] == "verify.md"]
    assert ["error" in r for r in journal] == [True, True, False]   # every call journalled, one replayed


def test_turns_count_failed_calls_and_make_one_call_per_key_and_prompt_text(folders):
    import threading
    import time

    import guided
    made = []

    def failing(prompt, payload, model, effort):
        made.append(payload)
        return {"model": model, "cost_usd": 0.3, "error": "RuntimeError: None", "json": {}}

    path = folders / "t.jsonl"
    with pytest.raises(guided.Spent):
        guided.Turns(path, failing, 100, 0.5, log=lambda *_: None)(guided.VERIFY, {}, "sol")
    reopened = guided.Turns(path, failing, 100, 0.5, log=lambda *_: None)
    assert len(made) == 2 and reopened.usd == pytest.approx(0.6)          # a rerun counts the failed spend
    with pytest.raises(guided.Spent):
        reopened(guided.VERIFY, {}, "sol")
    assert len(made) == 2

    def slow(prompt, payload, model, effort):
        made.append(prompt.read_text(encoding="utf-8"))
        time.sleep(0.2)
        return {"model": model, "cost_usd": 0.1, "json": {"text": made[-1]}}

    made.clear()
    turns, prompt = guided.Turns(folders / "u.jsonl", slow, 100, 10), folders / "p.md"
    prompt.write_text("old", encoding="utf-8")
    workers = [threading.Thread(target=turns, args=(prompt, {"same": 1}, "sol")) for _ in range(2)]
    [w.start() for w in workers]
    [w.join() for w in workers]
    assert made == ["old"]                                              # the second asker read the first's turn
    prompt.write_text("new", encoding="utf-8")
    assert turns(prompt, {"same": 1}, "sol") == {"text": "new"}         # an edited prompt is asked again


def test_a_refused_turn_is_an_empty_reply_asked_once_and_replayed(folders):
    import guided
    made = []

    def refusing(prompt, payload, model, effort):
        made.append(payload)
        return {"model": model, "cost_usd": 0.01, "error": "RuntimeError: API Error: Haiku 5.5 can't help with this.",
                "json": {}}

    path = folders / "r.jsonl"
    assert guided.Turns(path, refusing, 100, 1)(guided.label.PROMPT, {"s": 1}, "haiku") == {}
    assert guided.Turns(path, refusing, 100, 1)(guided.label.PROMPT, {"s": 1}, "haiku") == {}
    assert len(made) == 1                                                # not retried, then replayed
    with pytest.raises(guided.Spent):                                    # a lesson needs rules: still a failure
        guided.Turns(path, refusing, 100, 0.03, log=lambda *_: None)(guided.LESSON, {"s": 2}, "sol",
                                                                     ok=lambda j: isinstance(j.get("rules"), list))
    row = {"state": "s", "questions": {"n": {"type": "noul", "kind": "useful", "instructions": "q?"}},
           "provenance": {"state_hash": "h", "source": "c"}}
    turns = guided.Turns(folders / "o.jsonl", refusing, 100, 1)
    res = guided.outcomes(turns, "useful", [row], [[]], "haiku", "sol", "high", "0")
    assert res[0][("h", "n")][0] == "invalid"                            # the round settles without it


def test_a_round_with_a_failed_turn_settles_nothing_until_a_rerun(folders):
    import guided
    call, made = guided_world()
    asked, down = [], {"on": True}

    def flaky(prompt, payload, model, effort):
        asked.append(prompt.name)
        if prompt.name == "verify.md" and down["on"] and "lesson.md" in asked:   # round 1's checks fail
            return {"model": model, "cost_usd": None, "error": "RuntimeError: None", "json": {}}
        return call(prompt, payload, model, effort)

    rows = [labelled(i) for i in range(80)]
    common = dict(labeller="haiku", verifier="sol", rounds=2, per_round=40, max_turns=10_000, max_usd=100,
                  log=lambda *_: None)
    cut = guided.run("useful", "pilot", rows, call=flaky, **common)
    assert cut["stopped"] == "failed_turns" and len(cut["rounds"]) == 1
    assert guided.read(folders / "data" / "useful" / "pilot.guide.jsonl") == []   # no trial decided on failures
    down["on"] = False
    whole = guided.run("useful", "pilot", rows, call=flaky, **common)
    assert whole["stopped"] == "done" and whole["rounds"][1]["trial"]["adopted"] and whole["rounds"][1]["kept"] == 40

    # One state's questions split over two verifier batches: the state is written whole, once.
    wide = {"state": {"i": 1}, "questions": {f"useful_p{k}": NOUL for k in range(21)},
            "provenance": {"state_hash": "w", "source": "s"}}
    down["on"], asked[:] = True, ["lesson.md"]
    cut = guided.run("useful", "wide", [wide], call=flaky, **{**common, "rounds": 1, "per_round": 1})
    assert cut["stopped"] == "failed_turns" and guided.read(folders / "data" / "useful" / "wide.jsonl") == []
    down["on"] = False
    guided.run("useful", "wide", [wide], call=flaky, **{**common, "rounds": 1, "per_round": 1})
    (row,) = guided.read(folders / "data" / "useful" / "wide.jsonl")
    assert len(row["expected"]) == 21


def test_a_malformed_lesson_is_a_failed_turn_not_a_guide(folders, monkeypatch):
    import guided
    call, made = guided_world()

    def malformed(prompt, payload, model, effort):
        if prompt.name == "lesson.md":
            made.append((prompt.name, payload))
            return {"model": model, "cost_usd": None, "json": {"rules": None, "why": "no rule"}}
        return call(prompt, payload, model, effort)

    rows = [labelled(i) for i in range(80)]
    common = dict(labeller="haiku", verifier="sol", rounds=2, per_round=40, max_turns=10_000, max_usd=100,
                  log=lambda *_: None)
    for _ in range(2):   # and the rerun asks again rather than replaying it
        got = guided.run("useful", "pilot", rows, call=malformed, **common)
        assert got["stopped"] == "failed_turns" and len(got["rounds"]) == 1   # round 1 waits for its lesson
    assert sum(name == "lesson.md" for name, _ in made) == 2 * guided.ATTEMPTS
    resumed = guided.run("useful", "pilot", rows, call=call, **common)
    monkeypatch.setattr(label, "DATA", folders / "fresh")
    clean = guided.run("useful", "pilot", rows, call=guided_world()[0], **common)
    assert resumed["rounds"] == clean["rounds"] and resumed["rounds"][1]["trial"]["adopted"]
    assert guided.read(folders / "data" / "useful" / "pilot.jsonl") == guided.read(folders / "fresh" / "useful" / "pilot.jsonl")


def test_a_split_written_with_another_setup_is_not_added_to(folders, monkeypatch):
    import guided
    call, _ = guided_world()
    rows = [labelled(i) for i in range(80)]
    common = dict(labeller="haiku", verifier="sol", rounds=2, per_round=40, max_turns=10_000, max_usd=100,
                  call=call, log=lambda *_: None)
    guided.run("useful", "pilot", rows, **common)
    guided.run("useful", "pilot", rows, **{**common, "rounds": 3})          # more rounds of the same setup
    with pytest.raises(SystemExit, match="another setup"):
        guided.run("useful", "pilot", rows[:60], **common)
    reworded = [{**r, "questions": {"useful_p0": {**NOUL, "instructions": "Reworded."}}} for r in rows]
    with pytest.raises(SystemExit, match="another setup"):                  # same states, other questions
        guided.run("useful", "pilot", reworded, **common)
    bare = [{k: v for k, v in r.items() if k != "expected"} for r in rows]
    two = [{**r, "questions": {"useful_p0": NOUL, "useful_p1": NOUL}} for r in bare]
    guided.run("useful", "pair", two, **common)
    swapped = [{**r, "questions": {"useful_p1": NOUL, "useful_p0": NOUL}} for r in bare]
    with pytest.raises(SystemExit, match="another setup"):                  # the same questions in another order
        guided.run("useful", "pair", swapped, **common)
    gold = [{**r, "expected": {"useful_p0": not r["expected"]["useful_p0"]}} for r in rows]
    guided.run("useful", "pilot", gold, **common)                           # an answer to score against is not setup
    with pytest.raises(SystemExit, match="another setup"):
        guided.run("useful", "pilot", rows, **{**common, "verifier": "opus"})
    with monkeypatch.context() as m:   # the same turns kept under another rule are other labels and lessons
        m.setattr(guided, "KEEP_RULE", guided.KEEP_RULE + 1)
        with pytest.raises(SystemExit, match="another setup"):
            guided.run("useful", "pilot", rows, **common)
    edited = folders / "verify.md"
    edited.write_text(guided.VERIFY.read_text(encoding="utf-8") + "\nOne more rule.\n", encoding="utf-8")
    monkeypatch.setattr(guided, "VERIFY", edited)
    with pytest.raises(SystemExit, match="another setup"):
        guided.run("useful", "pilot", rows, **common)
    assert guided.run("useful", "other", rows, **common)["stopped"] == "done"


def test_a_verifier_that_accepts_planted_wrong_answers_keeps_nothing():
    import guided
    rows = [labelled(i) for i in range(20)]
    call, made = guided_world(stamp=True)
    got = guided.run("useful", "pilot", rows, labeller="haiku", verifier="sol",
                     rounds=1, per_round=20, max_turns=10_000, max_usd=100, call=call, log=lambda *_: None)
    (only,) = got["rounds"]
    assert (only["kept"], only["unreliable"], only["rejected"]) == (0, 20, 0)
    assert sum(name == "verify.md" for name, _ in made) == 2   # checked once more, reshuffled, then dropped
    assert sum(n["category"] == "verifier_unreliable" for n in notes.ledger("useful")) == 20


def test_uncovered_drops_the_about_pages_and_asks_only_what_was_covered():
    def passage(pid, page):
        return {"id": pid, "heading": f"{page} > Part", "text": f"{page} text"}

    def row(h, about, pages, complete):
        state = {"query": "q", "requirements": [{"id": "r0"}, {"id": "r1"}],
                 "passages": [passage(f"p{i}", page) for i, page in enumerate(pages)], "complete_passages": complete}
        return {"id": h, "state": state, "state_hash": h, "provenance": {"run": h, "about": about},
                "questions": {"coverage_r0": {"kind": "coverage"}, "coverage_r1": {"kind": "coverage"}}}

    rows = [row("a", ["Hash"], ["Hash", "Trees", "Hash"], ["p0", "p1", "p2"]),
            row("b", ["Hash"], ["Hash"], ["p0"]),                    # nothing left once Hash goes
            row("c", ["Hash"], ["Trees", "Graphs"], ["p0", "p1"]),   # nothing from the about page
            row("d", [], ["Hash", "Trees"], ["p0", "p1"]),           # no about page recorded
            row("e", ["Hash"], ["Hash", "Trees"], ["p0", "p1"])]     # no covered requirement
    labelled = [{"expected": {"coverage_r0": True, "coverage_r1": False}, "provenance": {"state_hash": h}}
                for h in "abcd"] + [{"expected": {"coverage_r0": False}, "provenance": {"state_hash": "e"}}]
    (got,) = augment.uncovered(rows, labelled, 10)
    assert [p["id"] for p in got["state"]["passages"]] == ["p1"] and got["state"]["complete_passages"] == ["p1"]
    assert list(got["questions"]) == ["coverage_r0"]                     # only the requirement that was covered
    assert got["state_hash"] == pool.digest(got["state"]) != "a"
    assert {k: got["provenance"][k] for k in ("derived", "from", "dropped")} == {
        "derived": "uncovered", "from": "a", "dropped": ["p0", "p2"]}
    assert rows[0]["state"]["complete_passages"] == ["p0", "p1", "p2"]   # the source row is untouched
    assert len(augment.uncovered(rows * 3, labelled, 2)) == 1            # one state however many sources cut to it
    many = [row(f"m{i}", ["Hash"], ["Hash", f"Page{i} Topic{i}", f"Area{i} Field{i}"], ["p0", "p1", "p2"])
            for i in range(3)]
    covered = [{"expected": {"coverage_r0": True}, "provenance": {"state_hash": f"m{i}"}} for i in range(3)]
    assert len(augment.uncovered(many, covered, 3)) == 3 and len(augment.uncovered(many, covered, 2)) == 2   # at most n
    # A held state matching "a"'s cut, not "a" itself: the cut is left out.
    held = {"state": dict(got["state"]), "questions": {}}
    assert not gate.Near([held]).found(rows[0])
    assert augment.uncovered(rows, labelled, 10, held=[held]) == []
    assert len(augment.uncovered(rows, labelled, 10, held=[{"state": {"other": "far"}, "questions": {}}])) == 1


def test_unsupported_drops_the_passages_a_verifier_quoted_for_a_yes():
    turns = [{"json": {"verdicts": {
        "i0": {"answer": "yes", "evidence": "\"The cache   holds keys for an hour\" ... \"short\""},
        "i1": {"answer": "no", "evidence": "\"Trees grow from the root upwards\""},     # not a yes
        "i2": {"answer": "yes", "evidence": "none"}}}},
        {"json": {"rules": ["a lesson turn"]}}]
    assert augment.quotes(turns) == {"The cache holds keys for an hour"}         # "short" is under QUOTE

    def row(h, kind, texts):
        state = {"query": "q", "requirements": [{"id": "r0"}],
                 "passages": [{"id": f"p{i}", "heading": "Page", "text": t} for i, t in enumerate(texts)],
                 "complete_passages": [f"p{i}" for i in range(len(texts))]}
        return {"id": h, "state": state, "state_hash": h, "provenance": {"run": h, "type": kind},
                "questions": {"coverage_r0": {"kind": "coverage"}}}

    rows = [row("a", "partial", ["Note. The cache holds keys\nfor an hour.","Trees grow from the root upwards."]),
            row("b", "answered", ["The cache holds keys for an hour.", "Other text entirely."]),    # a type not asked
            row("c", "partial", ["The cache holds keys for an hour."]),                          # nothing left
            row("d", "partial", ["Nothing quoted here at all.", "Nor here."])]                   # nothing quoted
    labelled = [{"expected": {"coverage_r0": True}, "provenance": {"state_hash": h}} for h in "abcd"]
    (got,) = augment.unsupported(rows, labelled, augment.quotes(turns), {"partial"}, 10)
    assert [p["id"] for p in got["state"]["passages"]] == ["p1"] and got["state"]["complete_passages"] == ["p1"]
    assert {k: got["provenance"][k] for k in ("derived", "from", "dropped")} == {
        "derived": "unsupported", "from": "a", "dropped": ["p0"]}
    assert got["id"] == "a-unsupported" and list(got["questions"]) == ["coverage_r0"]


def test_unsupported_quotes_only_train_splits(tmp_path, monkeypatch):
    folder = tmp_path / "coverage"
    passage = {"id": "p0", "heading": "Page", "text": "The cache holds keys for an hour."}
    state = {"query": "q", "requirements": [{"id": "r0"}], "passages": [passage, {**passage, "id": "p1", "text": "x"}],
             "complete_passages": ["p0", "p1"]}
    store.append(folder / "train.candidates.jsonl", {"id": "a", "state": state, "state_hash": "a",
                 "provenance": {"run": "a", "type": "partial"}, "questions": {"coverage_r0": {"kind": "coverage"}}})
    store.append(folder / "train.jsonl", {"expected": {"coverage_r0": True}, "provenance": {"state_hash": "a"}})
    store.append(folder / "calibration.candidates.jsonl", {"state": {"other": 1}, "state_hash": "c", "questions": {}})
    said = {"json": {"verdicts": {"i0": {"answer": "yes", "evidence": "\"The cache holds keys for an hour.\""}}}}
    # mixed kept only its train label, though its verifier judged the calibration state too.
    for split, inputs, h in (("full1", "a", "a"), ("full1-cal", "c", "c"), ("mixed", "ac", "a"), ("odd", "z", "a")):
        store.append(folder / f"{split}.turns.jsonl", said)
        store.append(folder / f"{split}.jsonl", {"expected": {"coverage_r0": True}, "provenance": {"state_hash": h}})
        for i in inputs:
            store.append(folder / f"{split}.candidates.jsonl", {"state_hash": i})
    store.append(folder / "bare.turns.jsonl", said)
    monkeypatch.setattr(augment, "DATA", tmp_path)
    monkeypatch.setattr(gate, "reserved", lambda: set())
    monkeypatch.setattr(gate, "POOL", tmp_path / "pool.jsonl")

    def run(split, *names):
        monkeypatch.setattr(sys, "argv", ["augment.py", "unsupported", "5", "--split", split, "--types", "partial"]
                            + (["--quotes-from", *names] if names else []))
        augment.main()
    for names, refusal in ((["full1", "full1-cal"], "train splits only"), (["mixed"], "train splits only"),
                           (["bare"], "input rows are kept"), (["odd"], "not that split's input"),
                           ([], "go with unsupported")):
        with pytest.raises(SystemExit, match=refusal):
            run("aug2", *names)
    assert not (folder / "aug2.candidates.jsonl").exists()
    run("aug2", "full1")
    (got,) = store.read(folder / "aug2.candidates.jsonl")
    assert got["provenance"]["dropped"] == ["p0"]
