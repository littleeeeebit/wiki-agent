"""Real Git and subprocess experiments; no model calls or production evidence."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import improvement as engine
from improvement import Experiment, Refused, admissible, atomic, contract, git, score


ADAPTER = '''import json, os, subprocess, sys
from pathlib import Path
request = json.load(sys.stdin)
fixture = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
stage = sys.argv[1]
root = Path(request["root"])
value = float((root / "setting.txt").read_text(encoding="utf-8"))
usage = {"calls": 1, "tokens": 10}
if stage == "propose":
    label = root.name
    plan = fixture.get("plans", {}).get(label, {"value": 0.8, "hypothesis": "reduce unnecessary context"})
    name = plan.get("path", "setting.txt")
    previous = (root / name).read_text(encoding="utf-8").strip()
    patch = "diff --git a/{0} b/{0}\\n--- a/{0}\\n+++ b/{0}\\n@@ -1 +1 @@\\n-{1}\\n+{2}\\n".format(name, previous, plan["value"])
    result = {"edits": [{"component": plan.get("component", "context_mgmt"),
                        "hypothesis": plan["hypothesis"], "paths": [name]}], "patch": patch}
elif stage == "critic":
    result = {"verdict": "reject" if fixture.get("critic_reject") else "accept", "reasons": ["fixture review"]}
    if fixture.get("critic_mutates"):
        (root / "setting.txt").write_text("0.99\\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "setting.txt"], check=True, capture_output=True)
else:
    reward = 0.1 if request["split"] == "held_out" and value > 0.5 and fixture.get("heldout_regression") else value
    result = {"trials": [{"id": i, "trial": t, "reward": reward, "tokens": fixture.get("trial_cost", 10)}
                         for i in request["ids"] for t in range(request["trials"])],
              "guards": {"integrity": not fixture.get("guard_failure", False)}}
    usage["tokens"] = sum(row["tokens"] for row in result["trials"])
    if fixture.get("missing_trial"):
        result["trials"].pop()
    if fixture.get("mutate_candidate"):
        (root / "setting.txt").write_text("0.99\\n", encoding="utf-8")
if not (fixture.get("unknown_usage") == stage):
    result["usage"] = usage
result["cache"] = os.environ["WIKI_IMPROVEMENT_CACHE"]
print(json.dumps(result))
'''


def repository(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Improvement test")
    git(path, "config", "user.email", "test@example.invalid")
    (path / "setting.txt").write_text("0.5\n", encoding="utf-8")
    (path / "eval").mkdir()
    (path / "eval/rubric.txt").write_text("original\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-m", "Initial synthetic harness")
    return path


@pytest.fixture
def world(tmp_path):
    hub = repository(tmp_path / "hub")
    repo = repository(tmp_path / "left" / "project")
    other = repository(tmp_path / "right" / "project")
    controller = tmp_path / "controller"
    controller.mkdir()
    adapter = controller / "adapter.py"
    adapter.write_text(ADAPTER, encoding="utf-8")
    fixtures = controller / "tasks.json"
    atomic(fixtures, {})
    config = {"schema": "wiki-improvement/1", "model": "fixed-test-policy-v1", "rounds": 3,
              "candidates": 2, "trials": 2, "edits_min": 1, "edits_max": 2,
              "stall_window": 1, "prune_window": 2, "delta": 0.01, "beta0": 0.1, "beta1": 1.0,
              "w_score": 1.0, "w_cost": 1.0, "w_novelty": 0.0,
              "limits": {"seconds": 60, "calls": 100, "tokens": 100_000},
              "tasks": {"evolve": ["evolve-a", "evolve-b"], "held_out": ["unseen-x", "unseen-y"]},
              "components": {"context_mgmt": ["setting.txt"], "config": ["eval/"]},
              "guards": ["integrity"], "protected": [], "controller_files": ["tasks.json"],
              "commands": {stage: [sys.executable, str(adapter), stage, str(fixtures)]
                           for stage in ("propose", "critic", "evaluate")}}
    config_path = controller / "experiment.json"

    def make(scope="project", target=None, name="experiment", fixture=None, **changes):
        atomic(fixtures, fixture or {})
        atomic(config_path, {**config, **changes})
        exp = Experiment(target or (hub if scope == "hub" else repo), scope, name,
                         hub=hub, store=tmp_path / "state")
        exp.initialize(config_path)
        return exp

    return SimpleNamespace(hub=hub, repo=repo, other=other, config=config, config_path=config_path,
                           fixtures=fixtures, adapter=adapter, make=make, tmp=tmp_path)


@pytest.mark.parametrize("scope", ["hub", "project"])
def test_complete_cycle_preserves_source_and_hands_off_to_review(world, scope):
    repo = world.hub if scope == "hub" else world.repo
    before = git(repo, "rev-parse", "HEAD")
    exp = world.make(scope=scope, fixture={"plans": {
        "r0-c0": {"value": 0.4, "hypothesis": "use a smaller context"},
        "r0-c1": {"value": 0.8, "hypothesis": "remove irrelevant context"}}})
    state = exp.round()
    assert state["incumbent"]["evaluation"]["score"] == 0.8
    assert [c["verdict"] for c in state["history"]] == ["rejected", "accepted"]
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "branch", "--show-current") == "main"
    assert not git(repo, "status", "--porcelain")
    for request in (exp.root / "records").glob("*-request.json"):
        assert "unseen-x" not in request.read_text(encoding="utf-8")
    handoff = exp.handoff()
    assert handoff["review_required"] and handoff["gate_required"] and not handoff["deployed"]
    assert git(repo, "rev-parse", handoff["branch"]) == state["incumbent"]["commit"]
    assert exp.read()["held_out"]["phase"] == "passed"
    assert exp.handoff() == handoff
    assert git(repo, "rev-parse", "HEAD") == before
    with pytest.raises(Refused, match="Held-out evidence"):
        exp.round()
    assert all(not f.read_bytes().startswith(b"\xef\xbb\xbf") for f in exp.root.rglob("*.json"))


def test_scope_and_repository_identity_isolate_same_names(world):
    project = world.make()
    other = world.make(target=world.other)
    hub = world.make(scope="hub")
    assert len({p.root for p in (project, other, hub)}) == 3
    assert project.key != other.key
    for scope, repo in (("hub", world.repo), ("project", world.hub)):
        with pytest.raises(Refused, match="Hub experiments"):
            Experiment(repo, scope, "experiment", hub=world.hub, store=world.tmp / "state")
    assert engine.listing(world.repo, hub=world.hub, store=world.tmp / "state")["scope"] == "project"
    result = project.read()
    result["repo_key"] = other.key
    project.save(result)
    with pytest.raises(Refused, match="ownership"):
        project.read()


def test_evaluator_and_manifest_are_pinned(world):
    exp = world.make()
    world.adapter.write_text(ADAPTER + "\n# Changed criterion\n", encoding="utf-8")
    with pytest.raises(Refused, match="frozen input changed"):
        exp.round()
    assert not exp.read()["rounds"]


def test_environment_changes_cannot_be_counted_as_harness_gains(world, monkeypatch):
    exp = world.make()
    monkeypatch.setenv("IMPROVEMENT_TEST_ENVIRONMENT", "different")
    with pytest.raises(Refused, match="environment changed"):
        exp.round()
    assert not exp.read()["rounds"]


def test_hub_candidate_cannot_rewrite_evaluation(world):
    exp = world.make(scope="hub", fixture={"plans": {
        f"r0-c{v}": {"value": "weakened", "path": "eval/rubric.txt", "component": "config",
                     "hypothesis": "weaken the rubric"} for v in range(2)}})
    state = exp.round()
    assert all("cannot edit" in c["reason"] for c in state["history"])
    assert state["incumbent"]["commit"] == state["base"]
    assert not list((exp.root / "records").glob("r0-*-eval-request.json"))


def test_rejected_hypotheses_are_retained_and_not_redrawn(world):
    exp = world.make(fixture={"plans": {f"r{r}-c{v}": {"value": 0.4, "hypothesis": "shorten context"}
                                       for r in range(2) for v in range(2)}})
    first = exp.round()
    second = exp.round()
    assert first["trajectory"] == [0.5, 0.5]
    assert len(second["history"]) == 4
    assert "already rejected" in second["history"][2]["reason"]
    request = json.loads((exp.root / "records/r1-c0-propose-request.json").read_text(encoding="utf-8"))
    assert len(request["history"]) == 2
    assert request["directives"]["stalled"]


def test_critic_runs_before_evaluation_and_rejection_skips_it(world):
    exp = world.make(fixture={"critic_reject": True})
    state = exp.round()
    assert all("critic rejected" in c["reason"] for c in state["history"])
    assert len(list((exp.root / "records").glob("r0-*-critic-result.json"))) == 2
    assert not list((exp.root / "records").glob("r0-*-eval-request.json"))


def test_critic_cannot_change_the_patch_it_accepts(world):
    exp = world.make(fixture={"critic_mutates": True})
    state = exp.round()
    assert all("critic changed" in c["reason"] for c in state["history"])
    assert state["incumbent"]["commit"] == state["base"]


def test_unknown_usage_stops_and_never_resets_allowance(world):
    exp = world.make(fixture={"unknown_usage": "propose"})
    with pytest.raises(Refused, match="usage is missing"):
        exp.round()
    state = exp.read()
    assert state["spent"]["tokens"] == 40
    assert state["stopped"] and state["rounds"][0]["phase"] == "stopped"
    restored = Experiment(world.repo, "project", exp.name, hub=world.hub, store=world.tmp / "state")
    with pytest.raises(Refused, match="usage is missing"):
        restored.round()
    assert restored.read()["spent"] == state["spent"]


def test_budget_exhaustion_is_persisted_across_restart(world):
    exp = world.make(limits={"seconds": 60, "calls": 1, "tokens": 100})
    with pytest.raises(Refused, match="allowance is exhausted"):
        exp.round()
    assert exp.read()["spent"]["calls"] == 1
    assert exp.read()["spent"]["tokens"] == 40


def test_interrupted_call_and_concurrent_operations_are_refused(world):
    exp = world.make()
    with exp.locked():
        with pytest.raises(Refused, match="running operation"):
            with exp.locked():
                pass
    state = exp.read()
    state["active"] = {"stage": "evaluate"}
    exp.save(state)
    with pytest.raises(Refused, match="interrupted with unknown usage"):
        exp.round()


def test_failed_heldout_ends_search_and_prevents_adoption(world):
    exp = world.make(fixture={"heldout_regression": True})
    exp.round()
    with pytest.raises(Refused, match="Held-out validation did not pass"):
        exp.handoff()
    assert exp.read()["held_out"]["phase"] == "failed"
    assert not exp.read()["handoff"]
    with pytest.raises(Refused, match="Held-out evidence"):
        exp.round()


def test_modified_winner_cannot_be_handed_off(world):
    exp = world.make()
    state = exp.round()
    (Path(state["incumbent"]["checkout"]) / "setting.txt").write_text("0.9\n", encoding="utf-8")
    with pytest.raises(Refused, match="checkout changed"):
        exp.handoff()
    assert not exp.read()["held_out"]


def test_context_is_frozen_from_the_owner_only(world):
    (world.repo / ".wiki").mkdir()
    (world.repo / ".wiki/local.md").write_text("Private project invariant\n", encoding="utf-8")
    # The source remains a snapshot input, not a candidate patch or shared rule.
    exp = world.make(context_files=[".wiki/local.md"])
    exp.round()
    request = json.loads((exp.root / "records/r0-c0-propose-request.json").read_text(encoding="utf-8"))
    assert request["context"] == {".wiki/local.md": "Private project invariant\n"}
    with pytest.raises(Refused, match="path escapes"):
        world.make(name="escape", context_files=["../right/project/setting.txt"])


@pytest.mark.parametrize("change", [
    {"limits": {"seconds": 0, "calls": 1, "tokens": 1}},
    {"limits": {"seconds": float("inf"), "calls": 1, "tokens": 1}},
    {"delta": -1}, {"edits_min": 3},
    {"tasks": {"evolve": ["same"], "held_out": ["same"]}},
    {"controller_files": []}, {"commands": {"propose": "python fake.py"}},
    {"components": {"memory": ["../other/"]}},
])
def test_invalid_experiments_cannot_start(world, change):
    # A malformed manifest is rejected before baseline execution.
    world.config_path.write_text(json.dumps({**world.config, **change}), encoding="utf-8")
    with pytest.raises((Refused, ValueError)):
        contract({**world.config, **change}, world.config_path)


def test_complete_denominator_and_unknown_cost_do_not_inflate_scores():
    result = score({"trials": [{"id": "a", "trial": 0, "reward": 1, "tokens": 10}],
                    "guards": {"integrity": True}}, ["a", "b"], 2, ["integrity"])
    assert result["score"] == 0.25 and result["missing"] == 3
    cfg = {"delta": .01, "beta0": .1, "beta1": 1, "w_score": 1, "w_cost": 1, "w_novelty": .1}
    assert not admissible(result, result, 0.25, cfg, 0)[0]
    for rows in ([{"id": "a", "trial": 0, "reward": 1, "tokens": None}],
                 [{"id": "a", "trial": 0, "reward": 1, "tokens": 1}] * 2):
        with pytest.raises(Refused):
            score({"trials": rows}, ["a"], 1, ["integrity"])


def test_selection_applies_noise_cost_novelty_and_noncompensatory_guards():
    cfg = {"delta": .02, "beta0": .1, "beta1": 1, "w_score": 1, "w_cost": 1, "w_novelty": .1}
    base = {"score": .5, "cost": 100, "missing": 0, "guards": {"integrity": True}}
    assert admissible({**base, "score": .6, "cost": 110}, base, .5, cfg, 0)[0]
    assert not admissible({**base, "score": .6, "cost": 200}, base, .5, cfg, 0)[0]
    assert not admissible({**base, "score": .46, "cost": 50}, base, .5, cfg, 1)[0]
    assert admissible({**base, "cost": 90}, base, .5, cfg, 0)[0]
    assert admissible(base, base, .5, cfg, 1)[0]
    assert not admissible({**base, "score": 1, "guards": {"integrity": False}}, base, .5, cfg, 1)[0]


def test_status_route_observes_the_selected_repository(monkeypatch):
    from main import improvements

    repo = Path("selected-project")
    monkeypatch.setattr(improvements, "current_repo", lambda: repo)
    monkeypatch.setattr(improvements.improvement, "listing", lambda target: {"selected": str(target)})
    assert improvements.status() == {"selected": str(repo)}


def test_cli_status_is_read_only(world):
    exp = world.make()
    # CLI scope resolution must work against a real hub while preserving the injected store.
    import improve

    def injected(repo, scope, name):
        return Experiment(repo, scope, name, hub=world.hub, store=world.tmp / "state")

    original = improve.Experiment
    improve.Experiment = injected
    try:
        before = exp.state_file.read_bytes()
        assert improve.main(["--repo", str(world.repo), "--scope", "project", "--name", exp.name, "status"]) == 0
        assert before == exp.state_file.read_bytes()
    finally:
        improve.Experiment = original


def test_native_roles_are_fresh_read_only_sessions_with_reported_usage(monkeypatch, tmp_path):
    import improvement_host
    from agent.chat_session import Event

    sessions = []

    class Host:
        def __init__(self, path, **kwargs):
            self.id = str(len(sessions))
            self.kwargs, self.closed = kwargs, False
            sessions.append(self)

        def say(self, text, halted):
            request = json.loads(text)
            value = {"edits": [], "patch": ""} if request["stage"] == "propose" else {"verdict": "accept", "reasons": []}
            yield Event("done", json.dumps(value), {"tokens": {"in": 10, "out": 5}})

        def close(self):
            self.closed = True

        def stop(self, halted):
            halted.set()

    monkeypatch.setattr(improvement_host, "ChatSession", Host)
    results = [improvement_host.run({"stage": stage, "root": str(tmp_path), "limits": {"seconds": 1}},
                                    "fixed-role-v1", "high") for stage in ("propose", "critic")]
    assert results[0]["role_session"] != results[1]["role_session"]
    assert all(result["usage"] == {"calls": 1, "tokens": 15} for result in results)
    assert all(s.kwargs["tools"] == "Read,Glob,Grep" and s.closed for s in sessions)
