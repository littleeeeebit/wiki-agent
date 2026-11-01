"""Form choices in the decision registry (`decisions.FORMS`): `pr.form` and
`refactor.mode`. Jev is a stand-in behind `decisions.transport`; nothing goes
to a network. The fixture sets are the committed ones under `eval/jev/branches/`.
"""
# ruff: noqa: F811 — a borrowed fixture is named again by each test that takes it

import json
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import decision
from main import decisions, pr_text, refactor_survey, specs
from main.query import ROOT
from test_main import client, no_machine_settings, until  # noqa: F401 — the fixture is autouse
from test_refactor_survey import MAP, spec as survey_spec, surveyed  # noqa: F401 — `surveyed` is a fixture
from test_specs import fresh_workers, repo  # noqa: F401 — fixtures, one autouse

POINTS = ("pr.form", "refactor.mode")


def sure(choice: str, options: list[str], confidence: float = 0.9) -> dict:
    rest = (1 - confidence) / (len(options) - 1)
    return {"choice": choice, "confidence": confidence,
            "probabilities": {o: confidence if o == choice else rest for o in options}}


@pytest.fixture
def jev(tmp_path, monkeypatch):
    """Jev answering `pick(state, options in the order asked)`; every request kept."""

    asked = []

    def answering(pick, confidence: float = 0.9):
        def transport(cfg):
            def evaluate(state, questions, trace, budget, stage):
                budget.call()
                options = list(questions["action"]["criteria"])
                asked.append({"state": state, "options": options})
                trace.append({"stage": stage, "model": "jev-test", "usage": {"input_tokens": 9, "output_tokens": 1}})
                return {"action": sure(pick(state, options), options, confidence)}
            return evaluate

        monkeypatch.setattr(decisions, "transport", transport)
        return asked

    monkeypatch.setattr(decisions, "LOGS", tmp_path / "actions")
    monkeypatch.setattr(decisions.knowledge, "english",
                        lambda texts, seconds, **kw: [{"status": "original_english", "text": t} for t in texts])
    return answering


def cfg(mode: str) -> decision.Config:
    return decision.Config(mode, decision.MODEL, "file", "", "k")


def records(log) -> list[dict]:
    return decisions.history(log)


# -- the registry ---------------------------------------------------------------


def test_each_form_point_declares_forms_baseline_and_a_frozen_fixture_set_of_thirty_or_more():
    for point in POINTS:
        f = decisions.FORMS[point]
        assert f["question"].endswith("?") and f["baseline"] and f["entry"]
        assert len(f["forms"]) >= 2 and all(isinstance(d, str) and d and "\n" not in d for d in f["forms"].values())
        held = json.loads((ROOT / decisions.BRANCHES / f["fixtures"]).read_text(encoding="utf-8"))
        items = held["fixtures"]
        assert held["point"] == point and len(items) >= 30 and held["sha256"] == decisions.digest(items)
        for item in items:
            assert item["label"] in f["forms"]
            assert item["sha256"] == decisions.digest({k: v for k, v in item.items() if k != "sha256"}), item["id"]
    assert {"feature-with-behaviour-change", "fix", "refactor-no-behaviour", "plan-document", "files-only"} \
        <= set(decisions.FORMS["pr.form"]["forms"])
    assert set(decisions.FORMS["refactor.mode"]["forms"]) == set(refactor_survey.MODES)
    # One shared guide, one section per form, and the three worked examples each under its form.
    assert set(pr_text.SECTIONS) == set(decisions.FORMS["pr.form"]["forms"])
    assert all("## Form:" not in s for s in [pr_text.SHARED, *pr_text.SECTIONS.values()])
    for form, example in (("refactor-no-behaviour", "선제 발화"), ("fix", "로그인하면"),
                          ("feature-with-behaviour-change", "음성 합성")):
        assert example in pr_text.SECTIONS[form]
    assert "## Rules the server checks" in pr_text.guide("fix") and "## Rules" not in pr_text.SECTIONS["fix"]


def test_the_server_lists_both_form_points_with_operations_baseline_and_provisional_status():
    got = client().get("/api/jev").json()["agent_decisions"]
    for point in POINTS:
        listed = got["points"][point]
        assert listed["operations"] == list(decisions.FORMS[point]["forms"])
        assert listed["baseline"] == decisions.FORMS[point]["baseline"]
        provisional = decisions.fitted(point, decision.config().model) is None
        assert listed["provisional"] is provisional and listed["active"] is False
        item = next(i for i in got["items"] if i["point"] == point)
        assert item["provisional"] is provisional and item["allowed_operations"] == listed["operations"]
    assert decisions.ACTIVATED == frozenset(), "the active path stays off in this change"


def test_an_unregistered_form_is_refused_and_an_unoffered_answer_never_runs(jev, tmp_path):
    with pytest.raises(ValueError, match="unregistered form"):
        decisions.choose_form("pr.form", {}, "chore", log=("r", "s"), occasion="o", cfg=cfg("active"))
    jev(lambda state, options: "chore")   # an answer outside the forms offered
    got = decisions.ask("pr.form", dict(decisions.FORMS["pr.form"]["forms"]), {"goal": "x"}, cfg("active"),
                        decisions.Budget(**decisions.ACTION), decisions.form_policy(cfg("active")))
    assert got["choice"] is None and got["status"] == "invalid"


# -- shadow, and the active path behind the gate ------------------------------------


def test_while_provisional_the_baseline_runs_and_jevs_pick_is_recorded_beside_it(jev):
    asked = jev(lambda state, options: "fix")
    got = decisions.choose_form("pr.form", {"goal": "Return to the page after login"},
                                "feature-with-behaviour-change", log=("r", "s"), occasion="pr:h", cfg=cfg("active"))
    assert got == {"form": "feature-with-behaviour-change", "record": None}, "not fitted: Jev waits for nothing"
    until(lambda: records(("r", "s")))
    rec = records(("r", "s"))[0]
    assert rec["point"] == "pr.form" and rec["status"] == "provisional" and rec["order"] == "natural"
    assert rec["predicted"] == rec["accepted"] == "fix" and rec["probability"] == pytest.approx(0.9)
    assert rec["selected"] == rec["baseline"] == "feature-with-behaviour-change"
    assert rec["offered"] == asked[0]["options"][:-1] == list(decisions.FORMS["pr.form"]["forms"])
    assert rec["prompt_version"] == decisions.FORM_VERSION
    assert decisions.choose_form("pr.form", {}, "fix", log=("r", "t"), occasion="o", cfg=cfg("off")) \
        == {"form": "fix", "record": None} and len(asked) == 1, "off asks nothing"


def fitted_and_activated(monkeypatch, point: str) -> None:
    monkeypatch.setattr(decisions, "ACTIVATED", frozenset({point}))
    monkeypatch.setattr(decisions, "fitted", lambda p, model, root=ROOT: {"accuracy": {"low": 0.95}} if p == point else None)


def test_a_fitted_activated_point_runs_jevs_form_and_the_baseline_on_doubt(jev, monkeypatch):
    fitted_and_activated(monkeypatch, "pr.form")
    jev(lambda state, options: "fix")
    got = decisions.choose_form("pr.form", {"goal": "x"}, "feature-with-behaviour-change", log=("r", "s"),
                                occasion="a", cfg=cfg("active"))
    assert got["form"] == "fix" and got["record"]["status"] == "fitted" and got["record"]["basis"] == "jev"
    jev(lambda state, options: "fix", confidence=0.4)
    doubt = decisions.choose_form("pr.form", {"goal": "x"}, "feature-with-behaviour-change", log=("r", "s"),
                                  occasion="b", cfg=cfg("active"))
    assert doubt["form"] == "feature-with-behaviour-change" and doubt["record"]["predicted"] == "fix"
    shadow = decisions.choose_form("pr.form", {"goal": "x"}, "plan-document", log=("r", "u"), occasion="c",
                                   cfg=cfg("shadow"))
    assert shadow == {"form": "plan-document", "record": None}, "fitted still needs active mode"


# -- pr.form at the worker's request ------------------------------------------------


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


@pytest.fixture
def branch(tmp_path):
    path = tmp_path / "w"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "start")
    start = git(path, "rev-parse", "HEAD")
    (path / "code.py").write_text("back = True\n", encoding="utf-8")
    git(path, "add", "code.py")
    git(path, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "change")
    return path, {"id": "s", "repo": "r", "goal": "Return to the page after login", "start_head": start,
                  "writing": {"head": git(path, "rev-parse", "HEAD")}}


def test_the_workers_request_is_the_baseline_forms_whatever_jev_picks_while_provisional(jev, branch, monkeypatch):
    path, spec = branch
    asked = jev(lambda state, options: "fix")
    monkeypatch.setattr(pr_text.decision, "config", lambda project=None: cfg("active"))
    text = pr_text.writing(spec, path, path)
    assert text == pr_text.guide("feature-with-behaviour-change").replace("<start>", spec["start_head"][:12])
    until(lambda: records(("r", "s")))
    assert records(("r", "s"))[0]["predicted"] == "fix"
    assert asked[0]["state"]["changed_files"] == [{"path": "code.py", "added": 1, "removed": 0}]
    fitted_and_activated(monkeypatch, "pr.form")
    assert pr_text.writing(spec, path, path) == pr_text.guide("fix").replace("<start>", spec["start_head"][:12])


def test_code_reads_the_form_off_the_diff():
    def facts(*rows, **spec):
        return pr_text.form_facts({"goal": "g", **spec}, "".join(f"{a}\t{d}\t{p}\n" for a, d, p in rows))

    assert pr_text.form_baseline(facts(("3", "1", "a.py"), review={"criteria": ["refactor"]})) == "refactor-no-behaviour"
    assert pr_text.form_baseline(facts(("3", "1", "a.py"), refactor={"mode": "preserve"})) == "refactor-no-behaviour"
    assert pr_text.form_baseline(facts(("9", "0", "docs/plan.md"), (".wiki/graph.json", "1", "1")[::-1])) \
        == "plan-document"
    assert pr_text.form_baseline(facts(("9", "0", "docs/p/a.md"), ("4", "0", "docs/p/data.py"),
                                       artifact_root="docs/p")) == "plan-document"
    assert pr_text.form_baseline(facts(("9", "0", "eval/x.json"), ("-", "-", "img.png"))) == "files-only"
    assert pr_text.form_baseline(facts(("9", "0", "tool/prompts/a.md"))) == "feature-with-behaviour-change"
    assert pr_text.form_baseline(facts(("9", "2", "a.py"), refactor={"mode": "simplify"})) \
        == "feature-with-behaviour-change"


# -- refactor.mode at the survey's cards ---------------------------------------------


@pytest.fixture
def survey(tmp_path):
    root = tmp_path / "base"
    (root / "src").mkdir(parents=True)
    (root / "src" / "orders.py").write_text("def place():\n    pass\n", encoding="utf-8")
    w = SimpleNamespace(repo=tmp_path / "proj", rid="sv1")
    return w, {"mode": "preserve", "map": MAP}, root


def test_a_provisional_mode_choice_keeps_the_surveys_mode_and_records_jevs(jev, survey, monkeypatch):
    w, run, root = survey
    jev(lambda state, options: "simplify")
    monkeypatch.setattr(decision, "config", lambda project=None: cfg("active"))
    s = refactor_survey.spec_shape(survey_spec("orders-split"), "preserve", MAP, root)
    assert refactor_survey.moded(w, run, s, "orders-split-sv1") == {}
    until(lambda: records(("proj", "orders-split-sv1")))
    rec = records(("proj", "orders-split-sv1"))[0]
    assert rec["point"] == "refactor.mode" and rec["predicted"] == "simplify" and rec["selected"] == "preserve"


def test_a_fitted_disagreement_is_checked_again_under_jevs_mode_and_marked_beside_the_surveys(jev, survey,
                                                                                               monkeypatch):
    w, run, root = survey
    fitted_and_activated(monkeypatch, "refactor.mode")
    monkeypatch.setattr(decision, "config", lambda project=None: cfg("active"))
    monkeypatch.setattr(refactor_survey, "checkout", lambda w_, run_: root)
    s = refactor_survey.spec_shape(survey_spec("orders-split"), "preserve", MAP, root)
    jev(lambda state, options: "simplify")
    assert refactor_survey.moded(w, run, s, "a-sv1") == {"jev_mode": "simplify"}
    assert records(("proj", "a-sv1"))[0]["outcomes"] == [{"status": "executed", "mode": "simplify"}]
    # A spec that does not hold under Jev's mode is not marked: `measure` belongs to rework alone.
    reworked = refactor_survey.spec_shape(
        survey_spec("orders-fast", measure={"metric": "ms", "task": "place", "argv": ["python", "b.py"], "repeat": 3}),
        "rework", MAP, root)
    jev(lambda state, options: "preserve")
    assert refactor_survey.moded(w, {**run, "mode": "rework"}, reworked, "b-sv1") == {}
    refused = records(("proj", "b-sv1"))[0]["outcomes"][0]
    assert refused["status"] == "refused" and "rework" in refused["reason"]
    assert "measure" not in json.dumps(refactor_survey.mode_state(reworked)), "Jev never reads a mode-only field"


def test_a_rechecked_spec_is_asked_its_mode_again_and_loses_the_old_texts_mark(jev, surveyed, monkeypatch):
    from test_refactor_run import ONE, surveyed_in
    from test_refactor_survey import Host, _git, settled

    sid = surveyed_in("simplify", survey_spec("orders-split"))["specs"][0]   # Jev off: the survey asks nothing
    card = specs.load("proj", sid)
    specs.save({**card, "refactor": {**card["refactor"], "jev_mode": "rework"}})   # earned by the old text
    (surveyed / "src/orders.py").write_text("def place(order):\n    return [order]\n", encoding="utf-8")
    _git(surveyed, "commit", "-qam", "the target moved")
    monkeypatch.setattr(decision, "config", lambda project=None: cfg("active"))
    Host.answers = {"survey": {**ONE, "specs": [survey_spec("orders-split", title="Split place again")]}}
    jev(lambda state, options: "preserve")
    api = client()

    def recheck(n: int) -> dict:
        return settled(api, api.post(f"/api/refactors/specs/{sid}/recheck",
                                     json={"request_id": f"recheck-req-000{n}", "mode": "simplify"}).json()["id"])

    assert recheck(1)["state"] == "done"
    assert "jev_mode" not in specs.load("proj", sid)["refactor"], "the rewritten text's mode is asked afresh"
    until(lambda: records(("proj", sid)))
    shadow = records(("proj", sid))[0]
    assert shadow["predicted"] == "preserve" and shadow["selected"] == "simplify" and shadow["occasion"].startswith("survey:")
    fitted_and_activated(monkeypatch, "refactor.mode")
    assert recheck(2)["state"] == "done"
    rewritten = specs.load("proj", sid)
    assert rewritten["refactor"]["mode"] == "simplify" and rewritten["refactor"]["jev_mode"] == "preserve"
    assert records(("proj", sid))[-1]["outcomes"] == [{"status": "executed", "mode": "preserve"}]


# -- the evaluation -------------------------------------------------------------------


def evaluated(tmp_path, monkeypatch, pick):
    from eval import branches

    root = tmp_path / "hub"
    shutil.copytree(ROOT / decisions.BRANCHES, root / decisions.BRANCHES, ignore=shutil.ignore_patterns("results"))
    labels = {}
    for point in POINTS:
        for f in decisions.fixtures(point, root):
            state, _ = branches.case(point, f)
            labels[json.dumps(state, sort_keys=True)] = f["label"]
    lock = threading.Lock()
    seen = []

    def ask(point, options, state, cfg_, budget, pol):
        with lock:
            seen.append(list(options))
        choice = pick(labels[json.dumps(state, sort_keys=True)], list(options))
        answer = sure(choice, list(options))
        return {"answer": answer, "choice": choice, "status": "decided", "usage": None}

    result = branches.evaluate(list(POINTS), cfg("shadow"), root=root, ask=ask)
    out = root / decisions.BRANCHES / "results" / "20261101T000000Z.json"
    out.parent.mkdir(parents=True)
    out.write_text(json.dumps(result), encoding="utf-8")
    return result, root, seen


def test_the_evaluation_asks_each_fixture_in_both_orders_and_fits_only_what_agrees(tmp_path, monkeypatch):
    result, root, seen = evaluated(tmp_path, monkeypatch, lambda label, options: label)
    for point in POINTS:
        forms = list(decisions.FORMS[point]["forms"])
        got = result["points"][point]
        assert seen.count(forms) == seen.count(forms[::-1]) == got["n"]
        assert got["accuracy"]["value"] == 1.0 and got["accuracy"]["low"] >= 0.90 and got["order_agreement"] == 1.0
        assert got["fits"] and decisions.fitted(point, decision.MODEL, root) is not None
    assert decisions.fitted("pr.form", "another-model", root) is None
    # Newest is the recorded run time, not the file name: a newer failing run unfits a passing one named
    # to sort first, and a result with no zoned run time counts for nothing.
    results = root / decisions.BRANCHES / "results"
    passing = json.loads((results / "20261101T000000Z.json").read_text(encoding="utf-8"))
    (results / "20261101T000000Z.json").rename(results / "latest.json")
    (results / "zzz.json").write_text(json.dumps({**passing, "ran_at": "2099-01-01T00:00:00"}), encoding="utf-8")
    assert decisions.fitted("pr.form", decision.MODEL, root) is not None
    failing = {**passing, "ran_at": "2099-01-01T00:00:00+00:00",
               "points": {p: {**r, "order_agreement": 0.5} for p, r in passing["points"].items()}}
    (results / "20990101T000000Z.json").write_text(json.dumps(failing), encoding="utf-8")
    assert decisions.fitted("pr.form", decision.MODEL, root) is None
    # Always the first option read: the two orders never agree, and nothing fits.
    result, root, _ = evaluated(tmp_path / "first", monkeypatch, lambda label, options: options[0])
    for point in POINTS:
        got = result["points"][point]
        assert got["order_agreement"] == 0.0 and not got["fits"]
        assert decisions.fitted(point, decision.MODEL, root) is None
