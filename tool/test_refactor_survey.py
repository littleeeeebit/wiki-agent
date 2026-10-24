"""The purpose-mode survey (`docs/plans/refactor/7-purpose-modes.md`): a read-only
map, one turn per pipeline and an integration turn, run in a detached worktree of
the base commit, ending in ordinary unstarted cards and nothing else.

The host is a stand-in behind `refactor.ChatSession` that answers each prompt
with its block and reports a model; Git and the gate run.
"""
# ruff: noqa: F811 — a borrowed fixture is named again by each test that takes it

import json
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest

from agent.chat_session import Event
from main import query, refactor, refactor_survey, specs
from test_main import client, no_machine_settings  # noqa: F401 — the fixture is autouse
from test_specs import repo  # noqa: F401 — `repo` is a fixture

FILES = {"src/orders.py": "def place(order):\n    return order\n",
         "src/billing.py": "def charge(order):\n    return order\n",
         "src/shared.py": "def check(value):\n    return value\n"}
MAP = {"pipelines": [{"id": "orders", "name": "Orders", "entry": "src/orders.py", "paths": ["src/orders.py"],
                      "role": "Takes an order"},
                     {"id": "billing", "name": "Billing", "entry": "src/billing.py", "paths": ["src/billing.py"],
                      "role": "Charges an order"}],
       "shared": [{"path": "src/shared.py", "used_by": ["orders", "billing"]}],
       "excluded": [{"path": "dist", "reason": "build output"}], "omm": {"used": False, "diverged": []},
       "unchecked": []}
LEVEL = {"level": "medium", "why": "two callers"}


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def report(pid: str) -> dict:
    return {"id": pid, "what": f"{pid} work", "inputs": "an order", "outputs": "a result",
            "links": [{"pipeline": "billing" if pid == "orders" else "orders", "how": "calls"}],
            "owns": "its own step", "misplaced": [],
            "debts": [{"kind": "duplication", "summary": "repeated check", "scope": "2 copies",
                       "evidence": [{"path": f"src/{pid}.py", "symbol": "place" if pid == "orders" else "charge"}],
                       "size": LEVEL, "difficulty": LEVEL, "risk": LEVEL, "if_left": "they drift"}],
            "change": "fold the check", "checked": [f"src/{pid}.py"], "unchecked": [], "proposals": []}


def spec(key: str, pipeline: str = "orders", **extra) -> dict:
    return {"key": key, "title": f"Split {key}", "pipeline": pipeline, "files": ["src/orders.py"],
            "boundary": "order intake", "problem": "mixed", "evidence": [{"path": "src/orders.py", "symbol": "place"}],
            "change": "split place", "keep": ["return value"], "verify": [{"method": "tests", "criterion": "pass"}],
            "depends": [], "impact": "none", "risk": LEVEL, "rollback": "revert", **extra}


SURVEY = {"coverage": {"scope": "src", "unchecked": [], "excluded": []},
          "specs": [spec("shared-check", "shared:src/shared.py"), spec("orders-split", depends=["shared-check"])]}


class Host:
    """Answers each survey prompt; `answers` overrides one pipeline's block."""

    made: list = []
    answers: dict = {}
    model = "claude-test-1"
    afterword = ""
    repair = None

    def __init__(self, path, **rest):
        self.path, self.rest, self.id, self.parent_id = Path(path), rest, uuid.uuid4().hex, None
        self.model_name = Host.model
        Host.made.append(self)

    def say(self, text, halt=None):
        self.text, system = text, self.rest["system"]
        if "block was refused" in text:   # the same block again unless `repair` fixes it
            tag = text.split("Your `", 1)[1].split("`", 1)[0]
            value = Host.repair or text.split(f"```{tag}\n", 1)[1].split("```", 1)[0]
        elif system == refactor_survey.MAP_PROMPT:
            tag, value = "refactor-map", MAP
        elif system == refactor_survey.PIPELINE_PROMPT:
            pid = text.rsplit("Explain pipeline `", 1)[1].split("`", 1)[0]
            tag, value = "refactor-pipeline", Host.answers.get(pid, report(pid))
        else:
            tag, value = "refactor-survey", Host.answers.get("survey", SURVEY)
        block = value if isinstance(value, str) else json.dumps(value)
        answer = f"Read.\n\n```{tag}\n{block}\n```"
        if Host.afterword:   # a hook had the model go on after its report
            yield Event("progress", answer)
            answer = Host.afterword
        yield Event("done", answer, {"tokens": {"in": 3, "out": 2}}, self.id)

    stop = close = lambda *_: None


@pytest.fixture
def surveyed(repo, tmp_path, monkeypatch):
    for rel, text in FILES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    _git(repo, "add", "src")
    _git(repo, "commit", "-qm", "src")
    monkeypatch.setattr(refactor, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(refactor, "ChatSession", Host)
    Host.made, Host.answers, Host.model, Host.afterword, Host.repair = [], {}, "claude-test-1", "", None
    query.config("refactor", "proj").update(refactor_mode="preserve", refactor_generation=0)
    return repo


def start(api, key: str = "survey-req-0001", mode: str = "preserve", model: str = "claude-test"):
    return api.post("/api/refactors", json={"request_id": key, "mode": mode, "role": {"model": model, "effort": ""}})


def settled(api, rid: str) -> dict:
    for _ in range(600):
        run = next(r for r in api.get("/api/refactors").json()["runs"] if r["id"] == rid)
        if run["state"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError(f"the survey never settled: {run}")


def test_a_survey_reads_only_its_base_and_ends_in_unstarted_cards(surveyed):
    api = client()
    head, tree = _git(surveyed, "rev-parse", "HEAD"), _git(surveyed, "status", "--porcelain")
    run = settled(api, start(api).json()["id"])
    assert run["state"] == "done" and run["phase"] == "done", run["stopped"]
    assert run["base"] == head and run["baseline"]["ok"] and run["baseline"]["failures"] == []
    assert set(run["pipelines"]) == {"orders", "billing"}
    assert [m["phase"] for m in run["models"]] == ["map", "pipeline:orders", "pipeline:billing", "integrate"]
    assert all(m["reported"] == "claude-test-1" for m in run["models"])
    asked = [h.rest for h in Host.made]
    assert all(not r["write"] and r["tools"] == "Read,Glob,Grep" and r["model"] == "claude-test" for r in asked)
    assert all(h.path == refactor_survey.place(surveyed, run["id"]) for h in Host.made), "never the checkout"
    assert "Selected mode: `preserve`" in Host.made[0].text and "no `.omm/`" in Host.made[0].text
    assert not refactor_survey.place(surveyed, run["id"]).exists(), "the survey's worktree is removed"
    assert (_git(surveyed, "rev-parse", "HEAD"), _git(surveyed, "status", "--porcelain")) == (head, tree)
    cards = [specs.load("proj", sid) for sid in run["specs"]]
    assert all(c["state"] == "정리됨" and c["worktree"] is None and c["pr"] is None for c in cards)
    shared, orders = cards
    assert orders["refactor"]["depends"] == [shared["id"]] and orders["refactor"]["base"] == head
    assert orders["source"]["refactor_mode"] == "preserve" and orders["refactor"]["mode"] == "preserve"
    assert orders["refactor"]["role"] == {"model": "claude-test", "effort": ""}, "Start offers the survey's model"
    edit = {"rev": 1, "goal": "Other", "out": [], "done": [], "slug": orders["id"]}
    assert api.put(f"/api/specs/{orders['id']}", json=edit).status_code == 409, "refined in the conversation only"
    assert start(api).json()["id"] == run["id"], "the request key starts nothing twice"


def test_one_live_survey_per_repository_and_a_new_one_retires_old_cards(surveyed, monkeypatch):
    api = client()
    first = settled(api, start(api).json()["id"])
    gate, original = threading.Event(), refactor_survey.baselined
    monkeypatch.setitem(refactor.PHASES, "baseline", lambda w, run: (gate.wait(5), original(w, run))[1])
    second = start(api, "survey-req-0002").json()
    refused = start(api, "survey-req-0003")
    assert refused.status_code == 409 and second["id"] in refused.json()["detail"]
    gate.set()
    second = settled(api, second["id"])
    assert second["state"] == "done"
    assert all(specs.load("proj", sid) is None for sid in first["specs"]), "unstarted cards of the older survey go"
    assert all(specs.load("proj", sid) for sid in second["specs"])


def test_a_malformed_pipeline_stops_and_resume_skips_what_finished(surveyed):
    api = client()
    Host.answers = {"billing": "{not json"}
    run = settled(api, start(api).json()["id"])
    assert run["state"] == "stopped" and run["stopped"]["reason"] == "format" and list(run["pipelines"]) == ["orders"]
    assert refactor_survey.place(surveyed, run["id"]).exists(), "kept for the resume"
    Host.answers, Host.made = {}, []
    query.config("refactor", "proj")["refactor_generation"] = 2   # the mode switched away and back
    api.post(f"/api/refactors/{run['id']}/resume")
    run = settled(api, run["id"])
    assert run["state"] == "done"
    assert [m["phase"] for m in run["models"]][-2:] == ["pipeline:billing", "integrate"]
    assert len(Host.made) == 2, "orders was not asked again"
    assert run["generation"] == 2 and all(specs.load("proj", sid)["source"]["refactor_generation"] == 2
                                          for sid in run["specs"]), "a survey with no cards yet resumes into this generation"


def test_a_refused_block_goes_back_once_with_its_reason(surveyed):
    api = client()
    Host.answers, Host.repair = {"billing": "{not json"}, json.dumps(report("billing"))
    run = settled(api, start(api).json()["id"])
    assert run["state"] == "done", run["stopped"]
    assert [m["phase"] for m in run["models"]][2:4] == ["pipeline:billing", "pipeline:billing:repair"]
    asked = next(h.text for h in Host.made if "block was refused" in h.text)
    assert "Expecting property name" in asked and "{not json" in asked, "the reason and the refused block go back"


def test_a_block_before_a_hooks_afterword_still_counts(surveyed):
    api = client()
    Host.afterword = "That sentence describes the pipeline; it is not a promise."
    run = settled(api, start(api).json()["id"])
    assert run["state"] == "done", run["stopped"]


def test_another_model_answering_stops_the_survey(surveyed):
    api = client()
    Host.model = "claude-other-9"
    run = settled(api, start(api).json()["id"])
    assert run["state"] == "stopped" and run["stopped"]["reason"] == "model"
    assert run["models"] == [{"phase": "map", "requested": "claude-test", "reported": "claude-other-9"}]


def test_the_mode_must_be_the_focus_mode(surveyed):
    api = client()
    assert start(api, mode="rework").status_code == 409


@pytest.mark.parametrize("bad, why", [
    ({"title": "Investigate the order flow"}, "조사"),
    ({"pipeline": "orders+billing"}, "pipeline"),
    ({"files": ["src/missing.py"]}, "기준 커밋에 없다"),
    ({"tests": {"contract": [{"test": "t", "old": "a", "new": "b", "why": "c"}]}}, "simplify"),
    ({"measure": {"metric": "ms", "task": "t", "argv": ["x"], "repeat": 3}}, "rework"),
])
def test_specs_that_only_look_or_cross_pipelines_are_refused(surveyed, bad, why):
    with pytest.raises(ValueError, match=why):
        refactor_survey.spec_shape(spec("x", **bad), "preserve", MAP, surveyed)


def test_a_path_is_kept_as_git_writes_it(surveyed):
    made = refactor_survey.spec_shape(spec("x", files=["./src/orders.py"],
                                           evidence=[{"path": "src/../src/orders.py", "symbol": "place"}]),
                                      "preserve", MAP, surveyed)
    assert made["files"] == ["src/orders.py"] and made["evidence"][0]["path"] == "src/orders.py"


def test_a_gate_that_writes_into_the_survey_tree_leaves_the_base_to_read(surveyed, monkeypatch):
    api, seen = client(), []
    def dirty(path, cmds, halt, *rest, **kw):
        (path / "src/orders.py").write_text("VALUE = 999\n", encoding="utf-8")
        return {"ok": False, "code": 0, "reason": "the command changed the tree", "tail": "", "head": ""}
    monkeypatch.setattr(specs, "judge", dirty)
    original = refactor.PHASES["map"]
    monkeypatch.setitem(refactor.PHASES, "map", lambda w, run: (
        seen.append((refactor_survey.place(w.repo, w.rid) / "src/orders.py").read_text(encoding="utf-8")),
        original(w, run))[1])
    run = settled(api, start(api).json()["id"])
    assert run["state"] == "done", run["stopped"]
    assert seen == [FILES["src/orders.py"]], "the map turn reads the base commit, not what the gate wrote"


def test_a_survey_tree_that_cannot_be_rebuilt_stops_the_survey(surveyed, monkeypatch):
    api, seen, sh = client(), [], specs.sh
    def dirty(path, cmds, halt, *rest, **kw):
        (path / "src/orders.py").write_text("VALUE = 999\n", encoding="utf-8")
        return {"ok": False, "code": 1, "reason": "", "tail": "", "head": ""}
    monkeypatch.setattr(specs, "judge", dirty)
    monkeypatch.setattr(specs, "sh", lambda argv, *rest, **kw: subprocess.CompletedProcess(
        argv, 1, "", "Permission denied") if argv[:3] == ["git", "worktree", "remove"] else sh(argv, *rest, **kw))
    monkeypatch.setitem(refactor.PHASES, "map", lambda w, run: seen.append(run))
    run = settled(api, start(api).json()["id"])
    assert run["state"] == "stopped" and "되돌리지 못했다" in run["stopped"]["detail"], run
    assert not seen, "no turn reads what the gate wrote"


def test_a_map_path_glob_becomes_the_files_it_matches(surveyed):
    owned = {**MAP["pipelines"][0], "paths": ["src/orders.py", "src/*.py"]}
    shaped = refactor_survey.mapped_shape({**MAP, "pipelines": [owned, MAP["pipelines"][1]]}, surveyed)
    assert shaped["pipelines"][0]["paths"] == ["src/orders.py", "src/billing.py", "src/shared.py"]
    with pytest.raises(ValueError, match="맞는 파일이"):
        refactor_survey.mapped_shape({**MAP, "pipelines": [{**owned, "paths": ["src/*.rs"]}, MAP["pipelines"][1]]},
                                     surveyed)


def test_dependency_cycles_and_unknown_dependencies_are_refused(surveyed):
    loop = {"coverage": {"scope": "s"}, "specs": [spec("a", depends=["b"]), spec("b", depends=["a"])]}
    with pytest.raises(ValueError, match="돌아온다"):
        refactor_survey.survey_shape(loop, "preserve", MAP, surveyed)
    with pytest.raises(ValueError, match="없는 명세"):
        refactor_survey.survey_shape({"coverage": {"scope": "s"}, "specs": [spec("a", depends=["z"])]},
                                     "preserve", MAP, surveyed)
    with pytest.raises(ValueError, match="nothing"):
        refactor_survey.survey_shape({"coverage": {"scope": "s"}, "specs": []}, "preserve", MAP, surveyed)


def test_low_debt_needs_a_reason(surveyed):
    clean = {**report("orders"), "debts": []}
    with pytest.raises(ValueError, match="low_debt_reason"):
        refactor_survey.pipeline_shape(clean, "orders", {"orders", "billing"}, surveyed)


def test_an_omm_document_is_offered_with_its_staleness(surveyed):
    (surveyed / ".omm/orders").mkdir(parents=True)
    (surveyed / ".omm/orders/description.md").write_text("orders\n", encoding="utf-8")
    _git(surveyed, "add", ".omm")
    _git(surveyed, "commit", "-qm", "omm")
    (surveyed / "src/orders.py").write_text("def place(order):\n    return [order]\n", encoding="utf-8")
    _git(surveyed, "commit", "-qam", "code moved on")
    api = client()
    run = settled(api, start(api).json()["id"])
    told = Host.made[0].text
    assert run["state"] == "done" and "`.omm/` structure document" in told and "orders" in told
    assert "src/orders.py" in told.split("since `.omm` was last committed", 1)[1], "the code changed since"


def test_failures_are_named_or_unparsed():
    assert refactor_survey.failures({"code": 0, "tail": ""}) == []
    assert refactor_survey.failures({"code": 1, "tail": "FAILED tool/test_a.py::test_x - boom\n"}) == \
        ["tool/test_a.py::test_x"]
    assert refactor_survey.failures({"code": 1, "tail": "something broke\n"}) is None
    spaced = "FAILED t.py::test_case[case one] - boom\nFAILED t.py::test_case[a] b] - x\nFAILED t.py::test_case[case two]\n"
    assert refactor_survey.failures({"code": 1, "tail": spaced}) == \
        ["t.py::test_case[a] b]", "t.py::test_case[case one]", "t.py::test_case[case two]"]
    assert refactor_survey.failures({"code": 1, "tail": "FAILED t.py::test_case[case\n"}) is None
    for doubtful in ("FAILED t.py::test_case[case] - one] - AssertionError", "FAILED t.py::test_case[case] - one]",
                     "FAILED t.py::test_case[p] - assert [1] == [2]"):   # the id may end at either `]`
        assert refactor_survey.failures({"code": 1, "tail": doubtful + "\n"}) is None, doubtful
    plain = "FAILED t.py::test_x - assert [1] - [2] == [3]\nFAILED t.py::test_case[x - y] - boom\n"
    assert refactor_survey.failures({"code": 1, "tail": plain}) == ["t.py::test_case[x - y]", "t.py::test_x"]
    for name in ("tests/unit cases/test_cases.py::test_one", "tests/a - b/test_x.py::TestX::test_y"):   # a path's spaces
        assert refactor_survey.failures({"code": 1, "tail": f"FAILED {name} - AssertionError: x\n"}) == [name]
    closing = "FAILED t.py::test_x - boom\nFAILED (failures=1)\n"   # unittest's closing count is no test
    assert refactor_survey.failures({"code": 1, "tail": closing}) == ["t.py::test_x"]
    mixed = ("FAILED tests/test_old.py::test_old - assert result.ok\nFAILED docs/case2.txt::case2.txt\n"
             "    ERROR checks/bootstrap.py - ImportError: no a.b\nFAILED t.py::Names::test-with-hyphen\n")
    assert refactor_survey.failures({"code": 1, "tail": mixed}) == [   # a doctest, an indented line, a hyphen
        "checks/bootstrap.py", "docs/case2.txt::case2.txt", "t.py::Names::test-with-hyphen", "tests/test_old.py::test_old"]
    assert refactor_survey.failures({"code": 1, "tail": "--- FAIL: TestX\n    --- FAIL: TestX/sub (0.00s)\n"}) == \
        ["TestX", "TestX/sub"]
    conda = "ERROR tests/test_b.py - ImportError\nERROR conda.cli.main_run:execute(127): `conda run` failed.\n"
    assert refactor_survey.failures({"code": 1, "tail": conda}) == ["tests/test_b.py"], "a wrapper's log is no test"
    errored = {"code": 1, "tail": "ERROR tests/test_b.py::test_c[case one] - boom\n"}
    assert refactor_survey.failures(errored) == ["tests/test_b.py::test_c[case one]"]


def test_a_cancelled_survey_stops_and_resumes_from_its_phase(surveyed, monkeypatch):
    api = client()
    gate, original = threading.Event(), refactor_survey.baselined
    monkeypatch.setitem(refactor.PHASES, "baseline", lambda w, run: (gate.wait(5), original(w, run))[1])
    rid = start(api).json()["id"]
    api.post(f"/api/refactors/{rid}/cancel")
    gate.set()
    run = settled(api, rid)
    assert run["state"] == "stopped" and run["stopped"]["reason"] == "cancelled" and not Host.made
    api.post(f"/api/refactors/{rid}/resume")
    assert settled(api, rid)["state"] == "done"


def test_the_earlier_workflow_is_read_only_and_a_restart_lets_go_of_it(surveyed):
    from test_specs import made, spec_block

    api = client()
    legacy = {"id": "aaaabbbb", "repo": "proj", "mode": "cleanup", "scope": "project", "request_id": "legacy-req-01",
              "role": {"model": "", "effort": ""}, "spent": {"seconds": 0, "calls": 0, "tokens": 0}, "phase": "steps",
              "state": "running", "stopped": None, "steps": [], "tests": None, "created": 1.0, "updated": 1.0}
    refactor.file_of("proj", "aaaabbbb").parent.mkdir(parents=True, exist_ok=True)
    refactor.file_of("proj", "aaaabbbb").write_text(json.dumps(legacy), encoding="utf-8")
    unstarted, held, plain = (made(surveyed, spec_block(slug=s))[0]["id"] for s in ("old-cleanup", "old-step", "plain"))
    specs.update("proj", unstarted, refactor={"mode": "cleanup", "files": ["a.txt"]})
    specs.update("proj", held, refactor={"run": "aaaabbbb", "block": True})
    refactor.recover()
    assert refactor.load("proj", "aaaabbbb")["stopped"]["reason"] == "restart"
    assert specs.load("proj", unstarted) is None, "an unstarted card of the earlier workflow is set aside"
    assert specs.load("proj", held)["refactor"] == {"run": "aaaabbbb", "block": False} and specs.load("proj", plain)
    assert api.post("/api/refactors/aaaabbbb/resume").status_code == 409
    assert api.post(f"/api/specs/{held}/start", json={}).status_code == 409
    assert start(api, mode="cleanup").status_code == 400
    listed = api.get("/api/refactors").json()["runs"]
    assert [r["mode"] for r in listed] == ["cleanup"], "its record is still read as it was"
