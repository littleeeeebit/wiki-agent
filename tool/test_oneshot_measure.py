"""oneshot step 12: the real measurement between the backend's build and its review.

See `docs/plans/oneshot/12-review-converges-on-measurement.md`. The backend and its measuring script are small
stand-ins the test commits, started for real by the server; GitHub is not reached (`published` is replaced).
"""

import json
import sys
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import oneshot_measure as measure, oneshot_needs, oneshot_spec, release
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_build import APP, built_world, check, git, o, report, spec, to_build, write  # noqa: F401
from test_oneshot_spec import world  # noqa: F401 — `world` is a fixture

SCRIPT = '''import json, os, pathlib, sys

out = pathlib.Path(sys.argv[1])
key = os.environ.get("BUDGET_KEY", "")
if key == "wrong":
    report = {"input_error": {"input": "M2", "why": "서비스가 키 " + key + " 를 거절했다"}}
else:
    report = {"metrics": {"total_ok": os.environ.get("BUDGET_URL") == "https://example.test/live"},
              "notes": "키 " + key + " 로 불렀다"}
(out / "clips").mkdir()
(out / "clips" / "sample.wav").write_bytes(b"RIFF")
(out / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
print("used", key)
'''
KEY = "sk-measure-" + "7" * 20


@pytest.fixture
def at_measure(built_world):  # noqa: F811 — the imported fixture, by its name
    tree, sent = built_world
    to_build(tree, sent)
    write(tree, "app.py", APP)
    write(tree, "measure.py", SCRIPT)
    plan = json.loads((tree / ".wiki/measure.json").read_text(encoding="utf-8"))
    step = plan["inputs"][0]["guide"][0]
    plan["inputs"].append({"id": "M2", "what": "서비스 키", "kind": "secret", "env": "BUDGET_KEY",
                           "guide": [step, step, {**step, "copy": "키"}]})
    plan["clips"] = True
    write(tree, ".wiki/measure.json", json.dumps(plan, ensure_ascii=False))
    git(tree, "add", "-A")
    git(tree, "commit", "-m", "build")
    assert check(tree, "만들었다.\n" + report()) is not None and o()["stage"] == "measure"
    oneshot_spec.saved("budget", oneshot={"working": None})
    pr = {"number": 1, "url": "https://github.test/pull/1", "branch": "b", "base": "main"}
    with patch.object(measure, "published", lambda repo: oneshot_spec.saved(repo, oneshot={
            "pr1": {**pr, "head": o()["built_head"]}})):
        yield tree, sent
    oneshot_needs.stored("budget", "BUDGET_KEY", None)


def given(key=KEY, url="https://example.test/live"):
    measure.give("budget", "M1", measure.Value(value=url))
    measure.give("budget", "M2", measure.Value(value=key))


def test_the_plan_names_each_step_its_copied_value_and_every_outside_call(at_measure):
    tree, _ = at_measure
    plan = json.loads((tree / ".wiki/measure.json").read_text(encoding="utf-8"))
    s = spec()
    assert measure.checked(plan, s, tree)["inputs"][1]["env"] == "BUDGET_KEY"
    short = {**plan, "inputs": [{**plan["inputs"][0], "guide": plan["inputs"][0]["guide"][:2]}]}
    with pytest.raises(ValueError, match="셋 이상"):
        measure.checked(short, s, tree)
    uncopied = {**plan, "inputs": [{**plan["inputs"][0], "guide": [plan["inputs"][0]["guide"][0]] * 3}]}
    with pytest.raises(ValueError, match="copy"):
        measure.checked(uncopied, s, tree)
    blind = {**plan, "inputs": [{**plan["inputs"][0], "guide": [{"window": "브라우저", "do": "누른다"}] * 3}]}
    with pytest.raises(ValueError, match="see"):
        measure.checked(blind, s, tree)
    with pytest.raises(ValueError, match="health"):
        measure.checked({**plan, "serve": {**plan["serve"], "health": "http://example.com/health"}}, s, tree)
    s["oneshot"]["design"]["outside"] = [{"id": "X1"}]
    with pytest.raises(ValueError, match="X1"):   # a real call the measurement never reaches
        measure.checked(plan, s, tree)


def test_a_measurement_runs_the_backend_and_its_script_with_the_inputs_and_the_owner_approves(at_measure):
    tree, sent = at_measure
    with pytest.raises(HTTPException, match="M1, M2"):
        measure.start("budget", measure.Start(head=o()["built_head"]))
    given()
    measure.measure("budget")
    run = o()["measures"][-1]
    assert run["status"] == "owner" and run["metrics"][0]["ok"] and run["clips"] == ["sample.wav"]
    assert KEY not in json.dumps(o(), ensure_ascii=False) and "[BUDGET_KEY]" in run["notes"]
    assert release.secret_read(oneshot_needs.name("budget", "BUDGET_KEY")) == KEY
    assert o()["measure_given"]["BUDGET_URL"]["value"] == "https://example.test/live"
    assert str(measure.clip("budget", run["n"], "sample.wav").path).endswith("sample.wav")
    shown = oneshot_spec.view(spec())["measure"]
    assert [x["given"] for x in shown["plan"]["inputs"]] == [True, True] and "value" not in json.dumps(shown["plan"]["inputs"])
    with pytest.raises(HTTPException, match="판정"):   # the owner listens before another run
        measure.start("budget", measure.Start(head=o()["built_head"]))
    with pytest.raises(HTTPException, match="적는다"):
        measure.decide("budget", measure.Decision(head=run["head"], approve=False))
    begun = []
    with patch.object(measure, "commented", lambda *a: None), patch("main.oneshot_review.begin", begun.append):
        measure.decide("budget", measure.Decision(head=run["head"], approve=True, note="자연스럽다"))
    assert o()["stage"] == "review" and begun == ["budget"] and o()["measures"][-1]["owner"]["approve"]
    from main import oneshot_review
    assert "자연스럽다" in oneshot_review.measured_text(o(), run["head"])


def test_a_failed_measurement_or_a_refusal_goes_back_to_the_build_agent_which_must_commit(at_measure):
    tree, sent = at_measure
    given(url="https://example.test/other")
    measure.measure("budget")
    assert o()["stage"] == "build" and o()["fix"] == "measure" and o()["measures"][-1]["status"] == "failed"
    assert sent[-1][1] == "build" and "total_ok=False" in sent[-1][2] and "measure.json" in sent[-1][2]
    # The same HEAD comes back with no commit: refused before any gate.
    then = check(tree, "고쳤다.\n" + report())
    assert o()["stage"] == "build" and o()["attempts"] == 1 and then is not None
    then()
    assert "커밋이 하나도 늘지 않았다" in sent[-1][2]
    git(tree, "commit", "--allow-empty", "-m", "fix")
    check(tree, "고쳤다.\n" + report())
    assert o()["stage"] == "measure"
    oneshot_spec.saved("budget", oneshot={"working": None})
    given()
    measure.measure("budget")
    with patch.object(measure.oneshot_build, "send", lambda *a: sent.append(a)):
        measure.decide("budget", measure.Decision(head=o()["built_head"], note="목소리가 끊긴다"))
    assert o()["stage"] == "build" and "목소리가 끊긴다" in sent[-1][2]


def test_an_input_the_script_calls_wrong_stops_for_the_owner_without_showing_it(at_measure):
    given(key="wrong")
    measure.measure("budget")
    assert o()["stopped"]["reason"] == "measure-input" and o()["stage"] == "measure"
    assert "M2" in o()["stopped"]["detail"] and "[BUDGET_KEY]" in o()["stopped"]["detail"]
    given()
    measure.start("budget", measure.Start(head=o()["built_head"]))   # one press starts it again
    from main import oneshot
    oneshot._workers["spec:budget"].join(60)
    assert o()["measures"][-1]["status"] == "owner"


def test_the_backend_that_never_answers_fails_the_measurement(at_measure):
    tree, sent = at_measure
    plan = json.loads((tree / ".wiki/measure.json").read_text(encoding="utf-8"))
    plan["serve"]["command"] = f'"{sys.executable}" -c "pass"'
    write(tree, ".wiki/measure.json", json.dumps(plan, ensure_ascii=False))
    git(tree, "commit", "-am", "broken serve")
    oneshot_spec.saved("budget", oneshot={"built_head": git(tree, "rev-parse", "HEAD")})
    given()
    measure.measure("budget")
    assert o()["measures"][-1]["status"] == "failed" and "떠 있지 않고 끝났다" in o()["measures"][-1]["error"]
