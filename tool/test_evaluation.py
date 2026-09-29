"""Stage 10 of `docs/plans/jev/`: the frozen evaluation set, the resumable
comparison, the report's gates and the per-project canary. No network: the
arms here are the two that send nothing (A and C over BM25), and Jev is a
stand-in wherever a choice is asked."""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

import decision
from eval import compare, dataset, report
from main import decisions
from search import sources

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def data():
    return dataset.load()


def test_the_set_is_frozen_as_the_plan_asks(data):
    intents = data["intents"]
    assert len(intents) == 120 and len(dataset.variants(data)) == 120
    assert sum(i["split"] == "held_out" for i in intents) == 60
    # Every category in both splits, as declared before any comparison ran.
    assert all(n["calibration"] >= 5 and n["held_out"] >= 5 for n in data["categories"].values())
    assert set(data["categories"]) == set(dataset.CATEGORIES)
    assert dataset.invalid(data) == [] and dataset.unresolved(data) == []
    # English only, as Jev is measured: no Korean wording, and none slipped into an English one.
    assert all(set(i["variants"]) == {"en"} for i in intents)
    assert not any(re.search(r"[가-힣]", i["variants"]["en"]) for i in intents)
    # Reviewed labels say by whom and when; the report shows the reviewer, a model included.
    assert data["labels"]["reviewed_by"] and data["labels"]["reviewed_at"]


def test_a_broken_label_or_count_is_caught(data):
    bad = json.loads(json.dumps(data))
    bad["intents"][0]["split"] = "held_out" if bad["intents"][0]["split"] == "calibration" else "calibration"
    assert "the declared category counts are not the intents' counts" in dataset.invalid(bad)
    bad = json.loads(json.dumps(data))
    fact = next(i for i in bad["intents"] if i["evidence"])
    fact["evidence"][0] = ["repo/docs/nowhere.md"]
    assert dataset.unresolved(bad) == [f"{fact['id']}: repo/docs/nowhere.md"]


def test_alternative_evidence_and_sections_are_scored_as_labelled(data):
    fact = next(i for i in data["intents"] if i["id"] == "fact-01")
    assert dataset.recall(fact, ["repo/docs/overview.md#Harbor overview"]) == 1.0, "either alternative meets it"
    bridge = next(i for i in data["intents"] if i["id"] == "bridge-01")
    assert dataset.recall(bridge, ["repo/docs/owners.md#Owners", "repo/docs/teams/atlas.md#History"]) == 0.5
    assert dataset.needed(next(i for i in data["intents"] if i["id"] == "multi-10")) == ["documents", "hub"]


def test_the_calibration_split_holds_no_held_out_question(data):
    derived = dataset.calibration(data)
    held_out = {i["variants"]["en"] for i in data["intents"] if i["split"] == "held_out"}
    assert not held_out & {c["query"] for c in derived["cases"]}
    kinds = {c.get("repair", {}).get("choice") for c in derived["cases"]}
    assert {"bridge", "subqueries", "sources"} <= kinds
    assert any(not c["retrieve"] for c in derived["cases"]) and any(
        p["redirect"] for c in derived["cases"] for p in c.get("passages", []))
    # Every label the policy fit reads is there: route, source, passage and coverage.
    from eval import policy

    answers = {c["id"]: {"retrieve": 0.9, **{f"source_{s}": 0.9 for s in c.get("sources", {})},
                         **{f"{k}_p{n}": 0.1 for n, _p in enumerate(c.get("passages", []))
                            for k in ("useful", "conflict", "redirect")},
                         **{f"coverage_r{n}": 0.9 for n, _r in enumerate(c.get("requirements", []))}}
               for c in derived["cases"]}
    assert all(len(pairs) for pairs in policy.labelled(derived, answers).values())


def arms(folder: Path, *extra: str) -> int:
    return compare.main([str(folder), "--arms", "A", "C", "--method", "bm25", "--languages", "en",
                         "--ids", "bridge-01", "paper-03", "memory-02", *extra])


@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    """One recorded run of the free arms, shared: recording is the slow part."""

    folder = tmp_path_factory.mktemp("arms") / "run"
    cache = sources.records_folder(ROOT).parent
    before = set(cache.iterdir()) if cache.exists() else set()
    assert arms(folder) == 0
    return folder, cache, before


def test_the_free_arms_record_resume_and_refuse_other_options(ran):
    folder, cache, before = ran
    rows = compare.rows(folder, "arms")
    assert len(rows) == 6 and {r["arm"] for r in rows} == {"A", "C"}
    assert all(r["leaks"] == [] and r["breaches"] == [] and r["cost"]["jev_requests"] == 0 for r in rows)
    bridge = next(r for r in rows if r["key"] == "bridge-01:en:C:0")
    # The walk reached the rota page; with nothing graded it waits past k and is not handed over.
    assert "repo/docs/teams/atlas.md#Rota" in bridge["beyond_k"]
    assert "repo/docs/teams/atlas.md#Rota" not in bridge["evidence"]
    assert any(e.startswith("papers/colbert.md") for e in next(r for r in rows if r["intent"] == "paper-03")["evidence"])
    # Resuming records nothing twice; other options for the same folder are another measurement.
    assert arms(folder) == 0 and len(compare.rows(folder, "arms")) == 6
    with pytest.raises(SystemExit, match="another measurement"):
        arms(folder, "--k", "5")
    # This run's records and index under the user cache are gone with its scratch corpus.
    assert (set(cache.iterdir()) if cache.exists() else set()) == before


def test_a_batch_stops_at_its_ceiling_and_says_why(tmp_path):
    folder = tmp_path / "run"
    assert arms(folder, "--minutes", "0") == 0
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    assert run["batches"][-1]["stopped"] == "minutes" and run["batches"][-1]["left"] == 6
    assert compare.rows(folder, "arms") == []
    with pytest.raises(SystemExit):
        compare.main([str(tmp_path / "x"), "--usd", "11"])


def test_the_report_resamples_intents_and_judges_the_frozen_gates(ran):
    got = report.build([ran[0]])
    table = got["arms"]["arms"]
    assert table["A"]["recall"]["denominator"] == 3 and table["C"]["candidate_recall"]["value"] >= \
        table["A"]["candidate_recall"]["value"]
    verdicts = {g["id"]: g["verdict"] for g in got["gates"]}
    assert verdicts["integrity"] == "pass" and verdicts["operating_ceiling"] == "pass"
    assert verdicts["graph_benefit"] == "not_measured", "no D arm ran"
    # Same resamples, same seed: the same interval.
    groups = [(1.0, 1.0), (0.0, 1.0), (1.0, 2.0)]
    assert report.boot(groups, report.ratio, 200, 1, 0.95) == report.boot(groups, report.ratio, 200, 1, 0.95)


@pytest.mark.parametrize("measured, verdict", [
    ({"value": 0.2, "low": 0.05, "high": 0.3}, "pass"),
    ({"value": 0.2, "low": -0.02, "high": 0.4}, "inconclusive"),
    ({"value": 0.0, "low": -0.03, "high": 0.05}, "fail"),
])
def test_an_improvement_the_interval_does_not_support_is_inconclusive(measured, verdict):
    assert report.improvement(measured, 0.10) == verdict


def test_a_pass_on_unreviewed_labels_is_provisional_and_zero_errors_must_stay_zero():
    gates = json.loads(report.GATES.read_text(encoding="utf-8"))
    found = {"arms": {"arms": {"A": {"integrity": 0, "breaches": 0, "unsupported_claim_rate": {"value": 0.0},
                                     "seconds": {"p95": 0.1}},
                               "D": {"integrity": 0, "breaches": 0, "unsupported_claim_rate": {"value": 0.05},
                                     "seconds": {"p95": 2.1}}},
                      "differences": {"bridge_recall_D_minus_A": {"value": 0.3, "low": 0.1, "high": 0.5}},
                      "language": {}}}
    got = {g["id"]: g["verdict"] for g in report.judge(gates, found, reviewed=False)}
    assert got["graph_benefit"] == "provisional" and got["integrity"] == "pass"
    assert got["answer_support"] == "fail", "A had no unsupported claim, so D may have none"
    assert {g["id"]: g["verdict"] for g in report.judge(gates, found, reviewed=True)}["graph_benefit"] == "pass"


@pytest.mark.parametrize("low, verdict", [(-0.179, "pass"), (-0.21, "fail"), (None, "inconclusive")])
def test_answer_support_allows_coverage_short_of_a_s_by_its_margin_at_the_interval_s_lower_end(low, verdict):
    # Version 3: the fifth held-out run (D − A −0.104, lower end −0.179) passes; a lower end past −0.20 fails.
    # Review round 2: with one paired intent there is no interval, and none is no margin met.
    gates = json.loads(report.GATES.read_text(encoding="utf-8"))
    assert next(g for g in gates["gates"] if g["id"] == "answer_support")["coverage_margin"] == -0.20
    found = {"arms": {"arms": {"A": {"integrity": 0, "breaches": 0, "unsupported_claim_rate": {"value": 0.37},
                                     "seconds": {"p95": 0.1}},
                               "D": {"integrity": 0, "breaches": 0, "unsupported_claim_rate": {"value": 0.0},
                                     "seconds": {"p95": 2.1}}},
                      "differences": {"coverage_D_minus_A": {"value": -0.104, "low": low, "high": -0.04}}}}
    got = {g["id"]: g["verdict"] for g in report.judge(gates, found, reviewed=True)}
    assert got["answer_support"] == verdict


def test_held_out_action_fixtures_pass_the_execution_boundary_once(tmp_path, monkeypatch):
    env = tmp_path / "jev.env"
    env.write_text("TYPESAFE_API_KEY=test-key\nWIKI_JEV_MODE=active\n", encoding="utf-8")
    monkeypatch.setenv("JEV_ENV", str(env))

    def transport(cfg):
        def evaluate(state, questions, trace, budget, stage):
            budget.call()
            options = list(questions["action"]["criteria"])
            pick = next((o for o in options if o not in ("dispatch", "fix", "none", decision.DEFER)), options[0])
            trace.append({"stage": stage, "model": "jev-test", "usage": {"input_tokens": 10, "output_tokens": 1}})
            return {"action": {"choice": pick, "confidence": 0.9,
                               "probabilities": {o: 0.9 if o == pick else 0.1 / (len(options) - 1) for o in options}}}
        return evaluate

    folder = tmp_path / "actions"
    with patch.object(decisions, "transport", transport):
        assert compare.main([str(folder), "--experiment", "actions", "--ids", "s03", "c01", "f02", "f01"]) == 0
    rows = compare.rows(folder, "actions")
    assert len(rows) == 4 and all(r["violations"] == [] for r in rows), "a tampered or repeated proposal passed"
    assert all(r["admitted"] == r["selected"] for r in rows)
    got = report.actions_report(rows)
    assert got["fixtures"] == 4 and got["selected_right_rate"] == 0.75 and got["executed_unoffered"] == 0


def test_active_mode_can_be_limited_to_named_checkouts(tmp_path, monkeypatch):
    env = tmp_path / "jev.env"
    env.write_text("TYPESAFE_API_KEY=k\nWIKI_JEV_MODE=active\n", encoding="utf-8")
    monkeypatch.setenv("JEV_ENV", str(env))
    canary, other = tmp_path / "canary", tmp_path / "other"
    assert decision.config(other).mode == "active", "no list: active everywhere, as before"
    decision.save({"mode": None, "active_projects": [str(canary)]})
    assert decision.config(canary).mode == "active"
    assert (decision.config(other).mode, decision.config(other).canary) == ("shadow", True)
    # Where active came from is still said, so the window's form keeps the saved mode.
    assert decision.config(other).mode_source == "file" and not decision.config(canary).canary
    assert decision.config().mode == "shadow", "an unknown project is not the canary"
    # The window's form carries no list: saving it keeps the canary.
    decision.save({"mode": "active", "disabled_sources": ["papers"], "limits": {}})
    assert decision.config(canary).mode == "active" and decision.config(other).mode == "shadow"
    assert decision.config(canary).status()["active_projects"] == [decision.canonical(canary)]
    with pytest.raises(ValueError, match="absolute"):
        decision.save({"active_projects": ["relative/path"]})
    decision.save({"active_projects": []})
    assert decision.config(other).mode == "active", "rolled back: the list emptied"


def test_the_outage_and_rollback_rehearsal_leaves_everything_as_it_was():
    from eval import rollout

    # The rehearsal's closed port answers with this error after Windows spends
    # about 2 s retrying; the same error at once takes the same path.
    def refused(self):
        raise ConnectionRefusedError(10061, "refused")

    with patch.object(decision.Pinned, "connect", refused):
        got = rollout.rehearse()
    assert got["ok"], [c for c in got["checks"] if not c["ok"]]
    assert len(got["checks"]) >= 20
