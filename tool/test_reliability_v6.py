"""Reliability PR 5's sixth set (`docs/plans/reliability/5-english-baseline.md`, v6): a fresh held-out test of
the answer path changed on v5's results, with answer support judged on the intents labelled fact or abstain
(option G2). No network."""

import json
from pathlib import Path

from eval import report

JEV = Path(__file__).resolve().parents[1] / "eval" / "jev"
V6 = JEV / "reliability-v6"


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
    assert got["answer_support"]["detail"]["analysis"]["D"]["denominator"] == 500, "analysis is reported beside"
    assert got["added_latency"]["value"] == 17.5 and got["added_latency"]["verdict"] == "fail", "latency reads all"
    assert {g["id"]: g["verdict"] for g in report.judge(gates, found(20), reviewed=True)}["answer_support"] == "pass"
    assert {g["id"]: g["verdict"] for g in report.judge(v5, found(20), reviewed=True)}["answer_support"] == "fail", \
        "v5 reads the pooled rate, and its pooled coverage interval"
