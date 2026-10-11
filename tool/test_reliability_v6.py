"""Reliability PR 5's sixth set (`docs/plans/reliability/5-english-baseline.md`, v6): a fresh held-out test of
the answer path changed on v5's results, with answer support judged on the intents labelled fact or abstain
(option G2). No network."""

import json
from pathlib import Path

from eval import dataset, report
from test_reliability_v5 import strata

JEV = Path(__file__).resolve().parents[1] / "eval" / "jev"
V6 = JEV / "reliability-v6"


def test_the_sixth_set_holds_out_fresh_cases_for_the_changed_route_and_answers():
    data = dataset.load(V6 / "intents.json")
    assert dataset.invalid(data) == [] and dataset.unresolved(data) == [] and dataset.frozen(data) == []
    earlier = [dataset.load(JEV / f"reliability{v}" / "intents.json") for v in ("", "-v2", "-v3", "-v4", "-v5")]
    assert [i for i in data["intents"] if i["split"] == "calibration"] == \
        [i for i in earlier[-1]["intents"] if i["split"] == "calibration"]
    held = [i for i in data["intents"] if i["split"] == "held_out"]
    assert all(i["review"] == "r6" for i in held)
    assert not {i["variants"]["en"] for i in held} & {i["variants"]["en"] for d in earlier for i in d["intents"]}
    assert not {i["id"] for i in held} & {i["id"] for d in earlier for i in d["intents"]}
    old_pages, new_pages = dataset.pages(earlier[-1]), dataset.pages(data)
    assert {n: new_pages.get(n) for n in old_pages} == old_pages
    # v5's sizing: one miss each still clears a 0.90 Wilson lower bound at 53.
    assert all(len(c) >= 54 for c in strata(data).values())
    # The route change's family: what to do in a routine case a standing policy covers, a fact.
    assert sum(i["category"] == "analysis" and not i["analysis"] for i in held) >= 12
    assert sorted(i["fault"]["kind"] for i in held if i.get("fault")) == \
        ["cancelled"] * 2 + ["exhausted"] * 2 + ["unavailable"] * 2
    assert not (V6 / "actions.json").exists(), "A3: v6 measures the answer path and routing only"
    gates = json.loads((V6 / "gates.json").read_text(encoding="utf-8"))
    assert data["frozen"] == gates["frozen"] == data["labels"]["reviews"]["r6"]["at"] == "2026-10-11"


def test_gates_v6_judge_answer_support_on_the_non_analysis_intents_and_latency_on_all():
    # G2: v5's held-out answers at ac90615, D 82/500 and A 76/472 on analysis, 35/241 and 84/447 on the
    # rest. Pooled the reduction is 0.093 and fails; on the rest it is 0.227, still short of 0.25.
    gates = json.loads((V6 / "gates.json").read_text(encoding="utf-8"))
    v5 = json.loads((JEV / "reliability-v5" / "gates.json").read_text(encoding="utf-8"))
    assert [{k: v for k, v in g.items() if k not in ("intents", "about")} for g in gates["gates"]] == \
        [{k: v for k, v in g.items() if k != "about"} for g in v5["gates"]], "only answer support's cohort changes"

    def rate(u, n):
        return {"value": u / n, "denominator": n}

    def found(d_other):
        arm = {"integrity": 0, "breaches": 0, "seconds": {"p95": 1.0}}
        return {"answers": {"arms": {
            "A": {**arm, "unsupported_claim_rate": rate(160, 919), "unsupported_claim_rate_analysis": rate(76, 472),
                  "unsupported_claim_rate_other": rate(84, 447), "answer_seconds": {"p95": 25.1}},
            "D": {**arm, "unsupported_claim_rate": rate(117, 741), "unsupported_claim_rate_analysis": rate(82, 500),
                  "unsupported_claim_rate_other": rate(d_other, 241), "answer_seconds": {"p95": 42.6}}},
            "differences": {"coverage_D_minus_A": {"value": -0.5, "low": -0.6, "high": -0.4},
                            "coverage_other_D_minus_A": {"value": 0.0, "low": -0.03, "high": 0.03}}},
            "answer_cohort": {"cohort": 252, "missing": 0, "incomplete": 0}}

    got = {g["id"]: g for g in report.judge(gates, found(35), reviewed=True)}
    assert got["answer_support"]["verdict"] == "fail" and got["answer_support"]["value"] == 0.2272
    assert got["answer_support"]["detail"]["analysis"]["unsupported_claim_rate"]["D"]["denominator"] == 500, \
        "analysis is reported beside"
    assert got["added_latency"]["value"] == 17.5 and got["added_latency"]["verdict"] == "fail", "latency reads all"
    assert {g["id"]: g["verdict"] for g in report.judge(gates, found(20), reviewed=True)}["answer_support"] == "pass"
    assert {g["id"]: g["verdict"] for g in report.judge(v5, found(20), reviewed=True)}["answer_support"] == "fail", \
        "v5 reads the pooled rate, and its pooled coverage interval"


def test_the_report_splits_coverage_and_support_by_the_intents_label():
    # Review round 1: G2 reports the analysis intents' coverage beside the gate, not only their support.
    data = dataset.load(V6 / "intents.json")
    gates = json.loads((V6 / "gates.json").read_text(encoding="utf-8"))
    analysis, fact = "analysis-91", "pasted-43"
    graded = {(analysis, "A"): ({"q0": "answered"}, [True]), (analysis, "D"): ({}, [False]),
              (fact, "A"): ({"q0": "answered"}, [False, True]), (fact, "D"): ({"q0": "answered"}, [True, True])}
    rows = [{"intent": iid, "arm": arm, "rep": 0, "language": "en", "evidence": [], "sources": [], "direct": False,
             "rejected": [], "status": "ready", "reason": "", "elapsed_ms": 0, "leaks": [], "breaches": [],
             "grade": {"parts": parts, "claims": [{"supported": s} for s in claims], "forbidden": []}}
            for (iid, arm), (parts, claims) in graded.items()]
    found = report.arms_report(rows, data, {"k": 8}, gates)
    arms, diffs = found["arms"], found["differences"]
    assert (arms["A"]["coverage_analysis"]["value"], arms["D"]["coverage_analysis"]["value"]) == (1.0, 0.0)
    assert (arms["A"]["coverage_other"]["value"], arms["D"]["coverage_other"]["value"]) == (1.0, 1.0)
    assert diffs["coverage_analysis_D_minus_A"]["value"] == -1.0 and diffs["coverage_other_D_minus_A"]["value"] == 0
    assert arms["D"]["unsupported_claim_rate_analysis"]["value"] == 1.0
    assert arms["D"]["unsupported_claim_rate_other"]["value"] == 0.0
    detail = next(g for g in report.judge(gates, {"answers": found, "answer_cohort": {
        "cohort": 2, "missing": 0, "incomplete": 0}}, reviewed=True) if g["id"] == "answer_support")["detail"]
    assert detail["analysis"]["coverage"]["D"]["value"] == 0.0
    assert detail["analysis"]["coverage_D_minus_A"]["value"] == -1.0
