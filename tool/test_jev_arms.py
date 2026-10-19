"""Stage 11 of docs/plans/jev: each question arm's requests and its offline comparison (`tool/eval/arms.py`)."""

import json

import pytest


CASE = {"id": "c", "query": "Why was X dropped in 2024?", "current_state": "long state",
        "requirements": [{"text": "why X was dropped"}],
        "passages": [{"heading": "h0", "text": "a"}, {"heading": "h1", "text": "b", "coverage": "partial"}],
        "repair": {"options": ["sources", "bridge"], "rest": ["hub"], "choice": "sources"}}
# What the recorded calibration side was asked: its route had no analysis or progress question.
NAMES = {"retrieve", "source_hub", "source_documents", "source_memory", "source_papers", "coverage_r0", "repair",
         *(f"{k}_p{i}" for k in ("useful", "conflict", "redirect") for i in (0, 1))}


def test_each_jev_question_arm_changes_only_its_own_variable():
    from eval import arms

    # A: one request per passage and one for coverage over its listed passages; no current_state.
    view = arms.view(CASE, NAMES)
    assert [sorted(q) for _, _, q in view] == [["conflict_p0", "redirect_p0", "useful_p0"],
                                               ["conflict_p1", "redirect_p1", "useful_p1"], ["coverage_r0"]]
    assert [[p["id"] for p in s["passages"]] for _, s, _ in view] == [["p0"], ["p1"], ["p0"]]
    assert not any("current_state" in s for _, s, _ in view) and arms.view({**CASE, "passages": []}, NAMES) == []
    # B: the recorded requests, question for question, but for the literal wording.
    (_, _, route), (_, _, judge) = arms.direct(CASE, NAMES)
    assert sorted(route) == sorted(n for n in NAMES if n == "retrieve" or n.startswith("source_"))
    assert route["source_hub"]["question"]["instructions"].startswith("Does the query ask about something")
    assert '"why X was dropped"' in judge["coverage_r0"]["question"]["instructions"]
    assert "conflict_p0" not in judge and {"conflictquery_p0", "conflictother_p0"} <= set(judge)
    # C: the recorded judge request again, every question kept, only the repair's options flipped.
    [(_, _, flipped, prefix)] = arms.order(CASE, NAMES)
    assert prefix == arms.FLIPPED and sorted(flipped) == sorted(n for n in NAMES if n.split("_")[0] in
                                                                ("useful", "conflict", "redirect", "coverage", "repair"))
    assert list(flipped["repair"]["question"]["criteria"]) == ["bridge", "sources", "defer"]
    # D: analysis always, ask only for a query in several parts.
    intent = {"id": "i", "variants": {"en": "Q"}, "analysis": False,
              "route_segments": [{"text": "pasted", "ask": False}, {"text": "asked", "ask": True}]}
    [(_, state, questions)] = arms.plain(intent, arms.PLAIN)
    assert state["query_parts"] == {"r0": "pasted", "r1": "asked"} and sorted(questions) == \
        ["analysis", "ask_r0", "ask_r1"]
    assert list(arms.plain({**intent, "route_segments": [{"text": "Q", "ask": True}]}, arms.OLD_PLAIN)[0][2]) == \
        ["analysis"]
    assert arms.plain_labelled([intent], {"i": {"analysis": 0.1, "ask_r0": 0.2, "ask_r1": 0.9}}) == \
        ([(0.1, False)], [[(0.2, False), (0.9, True)]])


def test_jev_arm_answers_combine_in_code_as_the_workflow_reads_them():
    import decision
    from eval import arms

    assert arms.combined({"conflictquery_p0": 0.8, "conflictother_p0": 0.5, "useful_p0": 0.3}, {}) == \
        {"useful_p0": 0.3, "conflict_p0": 0.4}
    # C: the recorded answer is the old order's; a choice that moves with the order defers.
    a = {"choice": "sources", "confidence": 0.8, "probabilities": {"sources": 0.8, "bridge": 0.1, "defer": 0.1}}
    b = {"choice": "bridge", "confidence": 0.6, "probabilities": {"sources": 0.3, "bridge": 0.6, "defer": 0.1}}
    assert arms.agreed({"flipped:repair": b}, {"repair": a})["repair"]["choice"] == "defer"
    same = arms.agreed({"flipped:repair": {**a, "confidence": 0.7}}, {"repair": a})["repair"]
    assert same["choice"] == "sources" and same["confidence"] == 0.7
    # An option only one order named keeps its mass (PR #182 review): accepted before, uncertain now.
    sparse = arms.agreed({"flipped:repair": {"choice": "sources", "confidence": 0.8,
                                             "probabilities": {"sources": 0.2, "bridge": 0.8}}},
                         {"repair": {"choice": "sources", "confidence": 0.8,
                                     "probabilities": {"sources": 0.6, "defer": 0.4}}})["repair"]
    assert sparse["probabilities"] == {"sources": 0.4, "defer": 0.2, "bridge": 0.4}
    rule = decision.Policy("fit", {"repair": {"confidence": 0.5, "margin": 0.2}})
    assert decision.verdict(rule, "repair", sparse) == "uncertain"
    # Only the arm's own kinds replace the recorded answers: a resampled route is not its effect.
    assert arms.merged("direct", {"retrieve": 0.9, "useful_p0": 0.2}, {"retrieve": 0.1, "useful_p0": 0.9,
                                                                       "source_hub": 0.7}) == \
        {"retrieve": 0.9, "useful_p0": 0.2, "source_hub": 0.7}
    assert arms.merged("order", {"repair": a, "useful_p0": 0.2}, {"flipped:repair": b, "flipped:useful_p0": 0.9}) \
        == {"repair": {**arms.agreed({"flipped:repair": b}, {"repair": a})["repair"]}, "useful_p0": 0.2}
    # Ask, an intent at a time as `Flow.route` reads it: beside a sure ask, an uncertain one is
    # dropped as material; with none sure it stays; should all go, none does.
    assert arms.ask_cost([(0.99, True), (0.5, True)], 0.2, 0.8) == (1, 0)
    assert arms.ask_cost([(0.5, True), (0.5, False)], 0.2, 0.8) == (0, 1)
    assert arms.ask_cost([(0.01, True), (0.01, False)], 0.2, 0.8) == (0, 1)
    # The review's case: an uncertain ask beside a sure one is still dropped, so no gain.
    old = [[(0.99, True), (0.01, True)], *[[(0.99, True), (0.01, False)]] * 3]
    new = [[(0.99, True), (0.50, True)], *[[(0.99, True), (0.01, False)]] * 3]
    assert arms.fit_ask([[(0.99, True)]])["fitted"] is False
    result = arms.compared({"ask": arms.fit_ask(old)}, {"ask": arms.fit_ask(new)})
    assert result["cost_old"] == result["cost_new"] == 10 and not result["adopted"]
    # Adopted when the total falls and no kind rises past one case's worst cost.
    assert arms.compared({"useful": {"cost": 5}}, {"useful": {"cost": 3}})["adopted"]
    risen = arms.compared({"useful": {"cost": 20}, "redirect": {"cost": 0}},
                          {"useful": {"cost": 0}, "redirect": {"cost": 6}})
    assert risen["risen"] == ["redirect"] and not risen["adopted"]


def test_a_jev_arm_never_passes_its_tokens_and_keeps_what_it_paid_for(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from eval import arms

    sent = []

    def evaluate(cfg, state, questions, trace, budget, stage):
        sent.append(state["n"])
        trace.append({"model": "m", "usage": None if state["n"] == 2 else {"input_tokens": 300, "output_tokens": 50}})
        return {name: 0.5 for name in questions}

    monkeypatch.setattr(arms.decision, "evaluate", evaluate)
    q = {"x": {"question": {"type": "noul", "instructions": "?"}}}
    items = [(f"k{n}", [("judge", {"n": n}, q)]) for n in range(3)]
    size = len(json.dumps({"state": {"n": 0}, "questions": {"x": q["x"]["question"]}}, sort_keys=True).encode())
    most = arms.bound(size, 1, arms.SEED_OVERHEAD, arms.SEED_OUTPUT)
    journal, cfg = tmp_path / "j.jsonl", SimpleNamespace(model="m")
    # Room for one request's bound, not two: the second is never sent.
    got = arms.asked(cfg, items, most + 100, journal)
    assert sent == [0] and not got["complete"] and list(got["answers"]) == ["k0"]
    assert got["costs"]["input_tokens"] + got["costs"]["output_tokens"] <= most + 100
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 1
    # A rerun reads back what was paid for and asks only the rest; no count is charged its bound.
    got = arms.asked(cfg, items, 10**6, journal)
    assert sent == [0, 1, 2] and got["complete"] and sorted(got["answers"]) == ["k0", "k1", "k2"]
    entries = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert [e["input_tokens"] for e in entries][:2] == [300, 300] and entries[2]["input_tokens"] >= size
    assert arms.asked(cfg, items, 0, journal)["complete"] and sent == [0, 1, 2]


def test_a_jev_arm_compares_offline_on_the_real_split(tmp_path, capsys):
    from eval import arms, policy

    # The gate's coverage: A reads only the listed passages, B quotes the requirement.
    row = {"state": {"query": "q", "current_state": "s", "requirements": [{"id": "r0", "text": "first"}],
                     "passages": [{"id": "p0", "text": "a"}, {"id": "p1", "text": "b"}], "complete_passages": ["p1"]},
           "expected": {"coverage_r0": False}, "provenance": {"state_hash": "g"}}
    [(_, s, q)] = arms.gate_requests("view", row)
    assert [p["id"] for p in s["passages"]] == ["p1"] and "current_state" not in s and list(q) == ["coverage_r0"]
    [(_, s, q)] = arms.gate_requests("direct", row)
    assert s is row["state"] and '"first"' in q["coverage_r0"]["question"]["instructions"]
    assert arms.gate_compared([row], {("g", "coverage_r0"): 0.7}, {"g": {"coverage_r0": 0.2}}) == \
        {"questions": 1, "old_all": "0/1", "old_yes": "0/0", "old_no": "0/1",
         "new_all": "1/1", "new_yes": "0/0", "new_no": "1/1"}

    # Offline over the real split: the recorded answers given back as an arm's change nothing.
    old = json.loads(policy.SCORE_FILE.read_text(encoding="utf-8"))["answers"]
    sha = policy.sha(policy.DATASET.read_bytes())
    for arm, new in (("view", old), ("order", {k: {"flipped:repair": v["repair"]}
                                               for k, v in old.items() if "repair" in v})):
        path = tmp_path / f"{arm}.json"
        path.write_text(json.dumps({"arm": arm, "dataset_sha256": sha, "complete": True, "new": {"answers": new}}),
                        encoding="utf-8")
        capsys.readouterr()
        assert arms.main([arm, "--answers", str(path)]) == 0
        got = json.loads(capsys.readouterr().out)
        assert got["cost_old"] == got["cost_new"] and not got["adopted"] and not got["risen"]
    with pytest.raises(SystemExit):
        arms.main(["direct", "--answers", str(tmp_path / "view.json")])   # another arm's record
    path.write_text(json.dumps({"arm": "order", "dataset_sha256": sha, "complete": False, "new": {"answers": {}}}),
                    encoding="utf-8")
    with pytest.raises(SystemExit):
        arms.main(["order", "--answers", str(path)])   # an incomplete record is never compared
