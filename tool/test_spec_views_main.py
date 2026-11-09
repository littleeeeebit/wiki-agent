"""The spec-views main experiment: the full-pipeline arm and its judgments, driven by scripted stubs (no model)."""

import json
from pathlib import Path

import pytest

from eval import spec_views as harness
from eval import spec_views_main as main_report
from eval import spec_views_pipeline as pipeline
from eval import spec_views_report as spec_report
from test_spec_views import commit_all, fix, ran, stub
from test_spec_views import replay as replay_fixture

replay = replay_fixture
MAIN = json.loads((Path(__file__).resolve().parents[1] / "eval" / "spec-views" / "main-1.json").read_text(encoding="utf-8"))
REQ = {"id": "R1", "text": "add returns the sum of its arguments", "source": {"kind": "original", "locator": "request"},
       "status": "confirmed"}


def counted(script):
    """A scripted implementer the report counts: it carries the configured implementer's name."""
    impl = stub(script)
    impl.name = "claude-cli"
    return impl


def planner(fail=()):
    """A scripted planner answering the step its message asks for; a step in `fail` returns no spec block."""
    calls = []

    def call(ws, system, message, tools, limits):
        step = ("selection" if message.startswith("A write session") else
                "enrichment" if message.startswith("Below are") else "generation")
        calls.append(step)
        spec = {"goal": "add adds", "out": [], "done": ["python -m pytest"], "decisions": []}
        text = {"generation": f"```spec\n{json.dumps(spec)}\n```",
                "enrichment": f"```spec\n{json.dumps({**spec, 'requirements': [REQ]})}\n```",
                "selection": '["R1"]'}[step]
        return {"result": "no block" if step in fail else text, "usd": 0.05, "tokens": {"input": 1}, "turns": 1,
                "cause": "", "error": "", "infra": False}

    call.calls = calls
    return call


@pytest.fixture
def main(replay):
    """The replay's config as a main experiment, its pipeline arm and the tasks file committed with it."""
    hub = Path(replay.cfg["config_root"])
    (hub / "tasks.json").write_text(json.dumps({"tasks": [{"id": "t1", "source": {"text": "make add add"}}]}),
                                    encoding="utf-8")
    cfg = {**replay.cfg, "kind": "main", "config_commit": commit_all(hub), "tasks_file": "tasks.json",
           "planner": {"model": "m"}, "limits": {**replay.cfg["limits"], "prepare_usd": 0.5},
           "pipeline": {**MAIN["pipeline"], "experiment": "t-pipe", "repetitions": 2, "ceilings": {"usd": 10, "hours": 1}},
           **{k: MAIN[k] for k in ("candidate", "pairs", "adoption", "metrics", "setting")},
           "held_out": {"excluded": [], "configs": []}, "specs_dir": str(replay.folder)}
    return replay, cfg


def test_every_pipeline_repetition_makes_its_own_spec_from_the_original_and_pays_for_it(main):
    rp, cfg = main
    call, seen = planner(), []

    def script(ws, system, stop):
        seen.append(system)
        fix(ws, system, stop)

    got = pipeline.run(cfg, counted(script), call, rp.scratch)
    assert got["pending"] == 0 and not ran(cfg)   # nothing in the fixed-spec journal
    arm = pipeline.arm(cfg)
    assert call.calls == ["generation", "enrichment", "selection"] * 2
    cells = ran(arm)
    assert sorted((r["condition"], r["repetition"]) for r in cells) == [("A", 1), ("A", 2), ("D", 1), ("D", 2)]
    assert all(r["success"] for r in cells)
    specs_made = {r["unit"]: r for r in ran(arm, ("pipeline_spec",))}
    assert len(specs_made) == 2 and all(r["unit"] in specs_made for r in cells)   # A and D share one generation
    assert sum(harness.D_NOTE in s for s in seen) == 2
    per = spec_report.report(arm)["per_condition"]
    # Every repetition's spec calls count: A pays its generation, D all three steps.
    assert per["A"]["cost_per_verified_success"] == pytest.approx((0.2 + 0.1) / 2)
    assert per["D"]["cost_per_verified_success"] == pytest.approx((0.2 + 0.3) / 2)


def test_a_failed_enrichment_fails_only_the_candidate_and_a_still_runs(main):
    rp, cfg = main
    cfg = {**cfg, "pipeline": {**cfg["pipeline"], "repetitions": 1}}
    pipeline.run(cfg, counted(fix), planner({"enrichment"}), rp.scratch)
    cells = {r["condition"]: r for r in ran(pipeline.arm(cfg))}
    assert cells["A"]["success"] and (cells["D"]["success"], cells["D"]["failure"], cells["D"]["infra"]) == \
        (False, "spec", False)
    # D's cell never started a session, so it has no code commit; it does not undo the implementation pass.
    assert not cells["D"].get("code_commit")
    harness.journal(cfg, {"kind": "implementation", "code_commit": cells["A"]["code_commit"], "ok": True,
                          "command": "pytest", "seconds": 0})
    assert main_report.report(cfg)["implementation"]["ok"]


def test_the_report_judges_three_stages_and_keeps_the_written_notes(main):
    rp, cfg = main
    harness.run(cfg, counted(fix), counted(fix), rp.folder, rp.scratch)
    pipeline.run(cfg, counted(fix), planner(), rp.scratch)
    rep = main_report.report(cfg)
    assert not rep["implementation"]["ok"]   # no implementation check journalled
    assert rep["validity"]["checks"]["frozen_inputs"] is True and rep["validity"]["checks"]["all_cells_ran"]
    # One task gives no interval: neither adoption nor rejection, a normal conclusion.
    assert rep["adoption"]["fixed"]["verdict"] == rep["adoption"]["overall"] == "undetermined"
    text = main_report.markdown(rep, cfg, "pr1 failed because …")
    assert "further verification needed" in text and "pr1 failed because …" in text and "SPEC_VIEWS" in text
    assert text.index(main_report.NOTES[0]) < text.index("pr1 failed") < text.index(main_report.NOTES[1])
    # A narrowed experiment says what it carries over, and lists what it leaves out under "Not run".
    narrowed = main_report.markdown(rep, {**cfg, "context": "narrows main-1", "not_run": ["B-C: resume main-1"]})
    assert narrowed.index("narrows main-1") < narrowed.index("## Three judgments")
    assert narrowed.index("## Not run") < narrowed.index("- B-C: resume main-1") < narrowed.index("## The setting")


def _cmp(d, r):
    return {"success_difference": dict(zip(("value", "low", "high"), d), n=10),
            "cost_ratio": dict(zip(("value", "low", "high"), r), n=10)}


@pytest.mark.parametrize("d, r, verdict, path", [
    ((0.10, 0.02, 0.20), (1.00, 0.80, 1.10), "adopt", "quality"),
    ((0.00, -0.02, 0.03), (0.80, 0.60, 0.95), "adopt", "cost"),
    ((0.04, 0.01, 0.20), (1.00, 0.80, 1.05), "undetermined", None),   # 5 points not reached
    ((0.00, -0.30, 0.30), (1.00, 0.50, 1.50), "undetermined", None),
    ((-0.20, -0.40, -0.05), (1.30, 1.15, 1.60), "reject", None),     # the intervals exclude both paths
])
def test_adoption_follows_the_committed_paths(d, r, verdict, path):
    got = main_report.adoption(_cmp(d, r), (0, "none"), MAIN["adoption"])
    assert got["verdict"] == verdict and (path is None or got[path])


@pytest.mark.parametrize("cand, base, blocks", [
    ({"critical": 2, "hard": 0}, {"critical": 2}, False),
    ({"critical": 3, "hard": 0}, {"critical": 2}, True),
    ({"critical": 1, "hard": 1}, {"critical": 4}, True),   # a forbidden change or deletion blocks outright
])
def test_a_critical_regression_blocks_relative_to_a(cand, base, blocks):
    n, _ = main_report.blocking(cand, base)
    assert bool(n) == blocks
    if blocks:
        good = _cmp((0.3, 0.2, 0.4), (0.5, 0.4, 0.6))
        assert main_report.adoption(good, (n, "x"), MAIN["adoption"])["verdict"] == "reject"


def test_the_main_tasks_never_meet_a_pilot_or_tuned_task():
    main_report.held_out(MAIN)
    with pytest.raises(harness.Refused, match="pr198"):
        main_report.held_out({**MAIN, "tasks": [*MAIN["tasks"], {"id": "pr198"}]})
