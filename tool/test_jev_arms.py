"""Stage 11 of docs/plans/jev: each question arm's requests and its offline comparison (`tool/eval/arms.py`)."""

import json

import pytest


def test_each_jev_question_arm_asks_only_what_it_changes(tmp_path, capsys):
    from eval import arms, policy

    case = {"id": "c", "query": "Why was X dropped in 2024?", "current_state": "long state",
            "requirements": [{"text": "why X was dropped"}],
            "passages": [{"heading": "h0", "text": "a"}, {"heading": "h1", "text": "b", "coverage": "partial"}],
            "repair": {"options": ["sources", "bridge"], "rest": ["hub"], "choice": "sources"}}
    # A: one request per passage and one for coverage over its listed passages; no current_state.
    view = arms.view(case)
    assert [sorted(q) for _, _, q in view] == [["conflict_p0", "redirect_p0", "useful_p0"],
                                               ["conflict_p1", "redirect_p1", "useful_p1"], ["coverage_r0"]]
    assert [[p["id"] for p in s["passages"]] for _, s, _ in view] == [["p0"], ["p1"], ["p0"]]
    assert not any("current_state" in s for _, s, _ in view) and arms.view({**case, "passages": []}) == []
    # B: the requirement's words, conflict as two literal questions combined in code, the concrete source.
    (_, _, route), (_, _, judge) = arms.direct(case)
    assert route["source_hub"]["question"]["instructions"].startswith("Does the query ask about something")
    assert '"why X was dropped"' in judge["coverage_r0"]["question"]["instructions"]
    assert "conflict_p0" not in judge and {"conflictquery_p0", "conflictother_p0"} <= set(judge)
    assert arms.combined({"conflictquery_p0": 0.8, "conflictother_p0": 0.5, "useful_p0": 0.3}) == \
        {"useful_p0": 0.3, "conflict_p0": 0.4}
    # C: the repair in both option orders, defer last; a choice that moves with the order defers.
    first, flipped = arms.order(case)
    assert list(first[2]["repair"]["question"]["criteria"]) == ["sources", "bridge", "defer"]
    assert list(flipped[2]["repairflipped"]["question"]["criteria"]) == ["bridge", "sources", "defer"]
    a = {"choice": "sources", "confidence": 0.8, "probabilities": {"sources": 0.8, "bridge": 0.1, "defer": 0.1}}
    b = {"choice": "bridge", "confidence": 0.6, "probabilities": {"sources": 0.3, "bridge": 0.6, "defer": 0.1}}
    assert arms.agreed({"repair": a, "repairflipped": b})["repair"]["choice"] == "defer"
    same = arms.agreed({"repair": a, "repairflipped": {**a, "confidence": 0.7}})["repair"]
    assert same["choice"] == "sources" and same["confidence"] == 0.7
    # Only the arm's own kinds replace the recorded answers: a resampled route is not its effect.
    assert arms.merged("direct", {"retrieve": 0.9, "useful_p0": 0.2}, {"retrieve": 0.1, "useful_p0": 0.9,
                                                                       "source_hub": 0.7}) == \
        {"retrieve": 0.9, "useful_p0": 0.2, "source_hub": 0.7}
    # D: analysis always, ask only for a query in several parts; labels from the v7 intents.
    intent = {"id": "i", "variants": {"en": "Q"}, "analysis": False,
              "route_segments": [{"text": "pasted", "ask": False}, {"text": "asked", "ask": True}]}
    [(_, state, questions)] = arms.plain(intent, arms.PLAIN)
    assert state["query_parts"] == {"r0": "pasted", "r1": "asked"} and sorted(questions) == \
        ["analysis", "ask_r0", "ask_r1"]
    assert list(arms.plain({**intent, "route_segments": [{"text": "Q", "ask": True}]}, arms.OLD_PLAIN)[0][2]) == \
        ["analysis"]
    assert arms.plain_labelled([intent], {"i": {"analysis": 0.1, "ask_r0": 0.2, "ask_r1": 0.9}}) == \
        {"ask": [(0.2, False), (0.9, True)], "analysis": [(0.1, False)]}
    # Adopted when the total falls and no kind rises past one case's worst cost.
    costs = {k: {True: {"no": 10, "uncertain": 0, "yes": 0}, False: {"no": 0, "uncertain": 0, "yes": 1}}
             for k in ("k", "j")}
    assert arms.compared({"k": {"cost": 5}}, {"k": {"cost": 3}}, costs)["adopted"]
    risen = arms.compared({"k": {"cost": 20}, "j": {"cost": 0}}, {"k": {"cost": 0}, "j": {"cost": 11}}, costs)
    assert risen["risen"] == ["j"] and not risen["adopted"]
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
    for arm, new in (("view", old), ("order", {k: {"repair": v["repair"], "repairflipped": v["repair"]}
                                               for k, v in old.items() if "repair" in v})):
        path = tmp_path / f"{arm}.json"
        path.write_text(json.dumps({"arm": arm, "dataset_sha256": sha, "new": {"answers": new}}), encoding="utf-8")
        capsys.readouterr()
        assert arms.main([arm, "--answers", str(path)]) == 0
        got = json.loads(capsys.readouterr().out)
        assert got["cost_old"] == got["cost_new"] and not got["adopted"] and not got["risen"]
    with pytest.raises(SystemExit):
        arms.main(["direct", "--answers", str(tmp_path / "view.json")])   # another arm's record
