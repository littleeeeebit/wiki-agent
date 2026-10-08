"""Jev routes real execution; its judgment cannot manufacture measured proof."""

# ruff: noqa: F811 — pytest injects imported fixtures by name.

import copy
import json
import threading
from pathlib import Path

import decision
import pytest

from fixtures.review_decision import evaluate
from common.language import language
from main import decisions, review_inspection, specs, verification
from test_local_verification import cloud_spec, cloud_world, git_world  # noqa: F401
from test_loop import Reviewer, Worker, commit, git, looped, no_machine_settings, template  # noqa: F401


def test_diff_context_includes_late_code_instead_of_only_the_first_large_document():
    text = "diff --git a/README.md b/README.md\n" + "x" * 15000 + "\ndiff --git a/app.py b/app.py\n+changed code"
    context = review_inspection.diff_context(text)
    assert "a/app.py" in context["text"] and "+changed code" in context["text"]
    assert context["files"] == 2 and context["truncated"] and len(context["text"]) <= 10000


def routing(calls, *, skip=(), judgment="covered"):
    def inspect(state, questions, trace, budget, stage):
        phase = "selection" if "run" in next(iter(questions.values()))["criteria"] else "judgment"
        calls.append((phase, copy.deepcopy(state)))
        answers = evaluate(state, questions, trace, budget, stage)
        for cid in questions:
            if cid.startswith("coverage:"):
                continue
            choice = "skip" if cid in skip else "run" if phase == "selection" else judgment
            offered = questions[cid]["criteria"]
            answers[cid] = {"choice": choice, "confidence": 1.0,
                            "probabilities": {key: float(key == choice) for key in offered}}
        return answers
    return inspect


def catalog(world, count=15):
    world.contract["flows"] += [{**world.contract["flows"][0], "id": f"unrelated-{i}",
        "title": f"Unrelated inventory {i}", "paths": [f"inventory-{i}/**"],
        "command": "python -c \"raise RuntimeError('unselected flow ran')\""} for i in range(count - 1)]
    (world.repo / verification.MANIFEST).write_text(json.dumps(world.contract), encoding="utf-8")
    commit(world.repo, "catalog.txt")
    git(world.repo, "push", "origin", "main")
    world.settings["manifest_digest"] = verification.manifest(world.repo)[1]
    (world.repo / verification.LOCAL).write_text(json.dumps(world.settings), encoding="utf-8")
    return {f["id"] for f in world.contract["flows"] if f["id"] != "health"}


def test_fifteen_flows_are_selected_in_one_call_then_actual_api_evidence_is_judged(cloud_world, monkeypatch):
    world, calls = cloud_world, []
    skip = catalog(world)
    monkeypatch.setattr(decisions, "transport", lambda cfg: routing(calls, skip=skip))
    cloud_spec(world)
    done = looped("cloud")
    assert done["state"] == "머지 가능", done.get("stopped")
    assert [phase for phase, _ in calls] == ["selection", "judgment"]
    assert len(calls[0][1]["flows"]) == 15 and len(calls[1][1]["flows"]) == 1
    assert all("command" not in row for row in calls[0][1]["flows"])
    assert [row["id"] for row in done["local_verification"]["flows"]] == ["health"]
    assert calls[1][1]["flows"][0]["evidence"]["requests"][0]["status"] == 200
    assert Reviewer.made and not Worker.made
    audit = done["review_inspection"]
    for phase in ("selection", "judgment"):
        assert audit[phase]["engine"] == "jev" and audit[phase]["budget"]["used"]["calls"] == 1
        assert set(audit[phase]["request"]["questions"]) == {f["id"] for f in audit[phase]["request"]["state_en"]["flows"]}
        assert len(json.dumps(audit[phase]["request"]).encode()) < 90000
    health = next(f for f in calls[0][1]["flows"] if f["id"] == "health")
    assert health["changed_paths"] and not health["required"]
    question = audit["selection"]["request"]["questions"]["health"]["question"]["instructions"]
    assert json.dumps({k: health[k] for k in ("required", "title", "assertions", "previous_outcome")}) in question
    assert not verification.merge_proven(world.repo, Path(done["worktree"]), done,
                                         done["pr"]["head"], done["local_verification"]["base_oid"])
    changed = copy.deepcopy(done)
    changed["local_verification"]["flows"][0]["evidence"]["observations"][0]["actual"] = "Different observation"
    contract = review_inspection.current(world.repo, Path(done["worktree"]), changed, done["pr"]["head"],
                                         done["local_verification"]["base_oid"])
    assert review_inspection.judgment_problem(changed, contract)
    changed["rev"] += 1
    assert not review_inspection.current(world.repo, Path(done["worktree"]), changed, done["pr"]["head"],
                                         done["local_verification"]["base_oid"])["inspection_selected"]


def test_missing_jev_stops_before_any_expensive_collection(cloud_world, monkeypatch):
    world = cloud_world
    cloud_spec(world)
    monkeypatch.setattr(decision, "config", lambda *args: decision.Config("off", "fixture-jev", "fixture"))
    monkeypatch.setattr(verification, "execute", lambda *a, **k: pytest.fail("Missing Jev must not run the catalog"))
    done = looped("cloud")
    assert done["state"] == "멈춤" and not Reviewer.made and not Worker.made
    assert done["review_inspection"]["selection"]["reason"] == "missing_key"


def test_uncertain_selection_still_stops_before_collection(cloud_world, monkeypatch):
    calls = []
    def uncertain(state, questions, trace, budget, stage):
        answers = routing(calls)(state, questions, trace, budget, stage)
        for answer in answers.values():
            answer.update(choice="defer", confidence=1.0,
                          probabilities={key: float(key == "defer") for key in answer["probabilities"]})
        return answers
    cloud_spec(cloud_world)
    monkeypatch.setattr(decisions, "transport", lambda cfg: uncertain)
    monkeypatch.setattr(verification, "execute", lambda *a, **k: pytest.fail("Uncertain selection must not execute"))
    done = looped("cloud")
    assert done["state"] == "멈춤" and not Reviewer.made
    assert done["review_inspection"]["selection"]["status"] == "uncertain"
    assert [stage for stage, _ in calls] == ["selection"]


def test_jev_cannot_skip_an_explicitly_required_flow(cloud_world, monkeypatch):
    world, calls = cloud_world, []
    skip = catalog(world, 2)
    cloud_spec(world, review={"flows": list(skip)})
    monkeypatch.setattr(decisions, "transport", lambda cfg: routing(calls, skip=skip))
    monkeypatch.setattr(verification, "execute", lambda *a, **k: pytest.fail("Rejected selection must not execute"))
    done = looped("cloud")
    assert done["state"] == "멈춤" and not Reviewer.made
    assert next(iter(skip)) in calls[0][1]["required"]["flows"]
    assert next(f for f in calls[0][1]["flows"] if f["id"] in skip)["required"]


@pytest.mark.parametrize("old_head", [None, "f" * 40])
def test_jev_can_exclude_an_unrelated_failure_without_erasing_it(cloud_world, monkeypatch, old_head):
    world, calls = cloud_world, []
    skip = catalog(world, 2)
    spec = cloud_spec(world)
    failed = {"id": next(iter(skip)), "head": old_head or spec["pr"]["head"], "ok": False,
              "reason": "Known unrelated failure", "evidence": {}}
    attempt = {"head": failed["head"], "environment_digest": None, "failures": [failed["id"] + "/known"],
               "reason": failed["reason"]}
    verification.keep(spec, flows=[failed], failure_attempts=[attempt])
    monkeypatch.setattr(decisions, "transport", lambda cfg: routing(calls, skip=skip))
    done = looped("cloud")
    assert done["state"] == "머지 가능"
    assert failed in done["local_verification"]["excluded_flows"]
    assert done["local_verification"]["failure_attempts"] == [attempt]
    assert not verification.uninvestigated(world.repo, Path(done["worktree"]), done, done["pr"]["head"])
    assert [r["id"] for r in done["local_verification"]["flows"]] == ["health"]
    offered = next(f for f in calls[0][1]["flows"] if f["id"] in skip)
    assert not offered["required"] and offered["previous_outcome"] == "failed_or_incomplete"


def test_shared_path_overlap_does_not_force_the_catalog_or_repeat_cached_setup(cloud_world, monkeypatch):
    world, calls = cloud_world, []
    skip = catalog(world)
    for flow in world.contract["flows"]:
        flow["paths"] = world.contract["flows"][0]["paths"]
    (world.repo / verification.MANIFEST).write_text(json.dumps(world.contract), encoding="utf-8")
    commit(world.repo, "shared-paths.txt")
    git(world.repo, "push", "origin", "main")
    world.settings["manifest_digest"] = verification.manifest(world.repo)[1]
    (world.repo / verification.LOCAL).write_text(json.dumps(world.settings), encoding="utf-8")
    cloud_spec(world, review={"evidence": ["api"]})
    monkeypatch.setattr(decisions, "transport", lambda cfg: routing(calls, skip=skip))
    done = looped("cloud")
    assert done["state"] == "머지 가능"
    assert all(f["changed_paths"] for f in calls[0][1]["flows"])
    assert [r["id"] for r in done["local_verification"]["flows"]] == ["health"]
    assert set(done["review_inspection"]["selection"]["request"]["questions"]) == {"coverage:api", "health", *skip}
    monkeypatch.setattr(specs, "gate", lambda *a, **k: pytest.fail("Cached selected proof must not rerun setup or commands"))
    contract = review_inspection.current(world.repo, Path(done["worktree"]), done, done["pr"]["head"],
                                          done["local_verification"]["base_oid"])
    refreshed = verification.execute(world.repo, done, Path(done["worktree"]), done["pr"]["head"],
        contract["base_oid"], threading.Event(), flow_ids=["health"], enforced_digest=contract["digest"])
    assert refreshed["local_verification"]["state"] == "runtime_passed"


def test_ignore_only_commit_reuses_receipt_behind_timeout_without_preparing_corpus(cloud_world, monkeypatch):
    world = cloud_world
    spec = cloud_spec(world)
    done = looped("cloud")
    path, old = Path(done["worktree"]), done["pr"]["head"]
    passed = done["local_verification"]["flows"][0]
    ignore = path / ".gitignore"
    ignore.write_text(ignore.read_text(encoding="utf-8") + "/.runtime.zip\n", encoding="utf-8")
    git(path, "add", ".gitignore")
    git(path, "commit", "-m", "Ignore a local archive")
    head = git(path, "rev-parse", "HEAD")
    spec = specs.update(spec["repo"], spec["id"], pr={**done["pr"], "head": head})
    interrupted = {**passed, "head": head, "executed_head": head, "ok": False, "blocked": True,
                   "evidence": {}, "finished_at": 2, "attempts": [passed]}
    spec = verification.keep(spec, state="waiting_environment", head=head, flows=[interrupted])
    base = done["local_verification"]["base_oid"]
    spec, contract = review_inspection.prepare(world.repo, path, spec,
        review_inspection.current(world.repo, path, spec, head, base), threading.Event())
    monkeypatch.setattr(specs, "gate", lambda *a, **k: pytest.fail("Valid historical proof must not prepare or execute again"))
    done = verification.execute(world.repo, spec, path, head, base, threading.Event(),
        flow_ids=["health"], enforced_digest=contract["digest"])
    row = done["local_verification"]["flows"][0]
    assert row["ok"] and row["head"] == head and row["executed_head"] == old
    assert row["evidence"] == passed["evidence"] and row["attempts"][-1]["blocked"]
    assert not verification.proven(world.repo, path, done, head, base, flow_ids=["health"])
    flow = verification.manifest(path)[0].flows[0]
    explicit = flow.model_copy(update={"paths": [".gitignore"]})
    assert not verification.reusable(row, explicit, head, row["signature"], path)
    (path / "unmapped-input").write_text("changed", encoding="utf-8")
    git(path, "add", "unmapped-input")
    git(path, "commit", "-m", "Change an unmapped input")
    assert not verification.reusable(row, flow, git(path, "rev-parse", "HEAD"), row["signature"], path)


def test_korean_control_literal_does_not_translate_english_observation(cloud_world, monkeypatch):
    world, seen = cloud_world, []
    runner = world.repo / "verify.py"
    suffix = "; 질문하기 shown; suggested name '한영대학.hwp'; managed source '한영대학.hwp'"
    runner.write_text(runner.read_text(encoding="utf-8").replace("str(payload)", f"str(payload) + {suffix!r}"), encoding="utf-8")
    commit(world.repo, "control-literal.txt")
    git(world.repo, "push", "origin", "main")
    original = decisions.normalized

    def inspected(state, *args, **kwargs):
        if state["flows"] and "evidence" in state["flows"][0]:
            actual = state["flows"][0]["evidence"]["observations"][0]["actual"]
            assert "`질문하기` shown" in actual and language(actual) == "en"
            assert actual.count("`한영대학.hwp`") == 2
            seen.append(actual)
        return original(state, *args, **kwargs)

    monkeypatch.setattr(decisions, "normalized", inspected)
    cloud_spec(world)
    done = looped("cloud")
    assert done["state"] == "머지 가능" and seen
    actual = done["local_verification"]["flows"][0]["evidence"]["observations"][0]["actual"]
    assert actual.endswith(suffix) and "`" not in actual


def test_uncertain_evidence_judgment_keeps_receipts_and_blocks_review(cloud_world, monkeypatch):
    world, calls = cloud_world, []
    cloud_spec(world)
    monkeypatch.setattr(decisions, "transport", lambda cfg: routing(calls, judgment="defer"))
    done = looped("cloud")
    assert done["state"] == "멈춤" and not Reviewer.made
    assert done["local_verification"]["flows"][0]["evidence"]["requests"][0]["status"] == 200
    assert done["review_inspection"]["judgment"]["status"] == "uncertain"


def test_jev_cannot_judge_a_failed_receipt_into_a_pass(cloud_world, monkeypatch):
    world, calls = cloud_world, []
    spec = cloud_spec(world)
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    base = specs.current_merge_base(path, "main", head)
    spec, contract = review_inspection.prepare(world.repo, path, spec,
        review_inspection.current(world.repo, path, spec, head, base), threading.Event())
    world.failed.write_text("fail", encoding="utf-8")
    spec = verification.execute(world.repo, spec, path, head, base, threading.Event(),
                                flow_ids=["health"], enforced_digest=contract["digest"])
    monkeypatch.setattr(decisions, "transport", lambda cfg: routing(calls))
    with pytest.raises(ValueError):
        review_inspection.assess(world.repo, path, spec, contract, threading.Event())
    assert not calls and spec["local_verification"]["state"] == "waiting_cloud"


def test_frozen_selection_tampering_is_not_accepted(cloud_world):
    world = cloud_world
    spec = cloud_spec(world)
    path, head = Path(spec["worktree"]), spec["pr"]["head"]
    base = specs.current_merge_base(path, "main", head)
    spec, contract = review_inspection.prepare(world.repo, path, spec,
        review_inspection.current(world.repo, path, spec, head, base), threading.Event())
    spec["review_inspection"]["selection"]["result"]["answers"]["health"]["choice"] = "skip"
    assert review_inspection.selected(spec, contract["inspection_identity"], contract["inspection_catalog"]) is None
