"""oneshot step 12: the real measurement between the backend's build and its review.

See `docs/plans/oneshot/12-review-converges-on-measurement.md`. The backend and its measuring script are small
stand-ins the test commits, started for real by the server; GitHub is not reached (`published` is replaced).
"""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import oneshot_measure as measure, oneshot_needs, oneshot_spec, release
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_build import APP, built_world, check, git, o, report, spec, to_build, write  # noqa: F401
from test_oneshot_spec import world  # noqa: F401 — `world` is a fixture

APP_ROOT = Path(__file__).resolve().parent.parent
ENV = f"BUDGET_KEY_{os.getpid()}"   # each test process its own Credential Manager entry
SCRIPT = '''import json, os, pathlib, sys

out = pathlib.Path(sys.argv[1])
key = os.environ.get("ENV", "")'''.replace("ENV", ENV) + '''
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
    plan["inputs"].append({"id": "M2", "what": "서비스 키", "kind": "secret", "env": ENV,
                           "guide": [step, step, {**step, "copy": "키"}]})
    plan["clips"] = True
    write(tree, ".wiki/measure.json", json.dumps(plan, ensure_ascii=False))
    git(tree, "add", "-A")
    git(tree, "commit", "-m", "build")
    assert check(tree, "만들었다.\n" + report()) is not None and o()["stage"] == "measure"
    oneshot_spec.saved("budget", oneshot={"working": None})
    pr = {"number": 1, "url": "https://github.test/pull/1", "branch": "b", "base": "main"}
    with patch.object(measure.oneshot_publish, "published", lambda repo: oneshot_spec.saved(repo, oneshot={
            "pr1": {**pr, "head": o()["built_head"]}})):
        yield tree, sent
    oneshot_needs.stored("budget", ENV, None)


def given(key=KEY, url="https://example.test/live"):
    head = o()["built_head"]
    measure.give("budget", "M1", measure.Value(value=url, head=head, env="BUDGET_URL", kind="text"))
    measure.give("budget", "M2", measure.Value(value=key, head=head, env=ENV, kind="secret"))


def test_the_plan_names_each_step_its_copied_value_and_every_outside_call(at_measure):
    tree, _ = at_measure
    plan = json.loads((tree / ".wiki/measure.json").read_text(encoding="utf-8"))
    s = spec()
    assert measure.checked(plan, s, tree)["inputs"][1]["env"] == ENV
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
    assert KEY not in json.dumps(o(), ensure_ascii=False) and f"[{ENV}]" in run["notes"]
    assert release.secret_read(oneshot_needs.name("budget", ENV)) == KEY
    folder = oneshot_spec.store("budget").folder / "measure" / run["folder"]   # what is kept shows no secret
    assert sorted(p.name for p in folder.iterdir()) == ["clips", "report.json", "serve.log"]
    assert sorted(p.name for p in folder.parent.iterdir()) == [run["folder"]]   # the script's own folder is gone
    assert not any(KEY in p.read_text(encoding="utf-8", errors="replace") for p in folder.rglob("*.json"))
    assert o()["measure_given"]["BUDGET_URL"]["value"] == "https://example.test/live"
    assert str(measure.clip("budget", run["n"], "sample.wav").path).endswith("sample.wav")
    shown = oneshot_spec.view(spec())["measure"]
    assert [x["given"] for x in shown["plan"]["inputs"]] == [True, True] and "value" not in json.dumps(shown["plan"]["inputs"])
    with pytest.raises(HTTPException, match="판정"):   # the owner listens before another run
        measure.start("budget", measure.Start(head=o()["built_head"]))
    with pytest.raises(HTTPException, match="적는다"):
        measure.decide("budget", measure.Decision(head=run["head"], n=run["n"], approve=False))
    with pytest.raises(HTTPException, match="판정할 실측이 없다"):   # a screen showing another run
        measure.decide("budget", measure.Decision(head=run["head"], n=run["n"] + 1, approve=True))
    begun, held, save = [], [], measure.oneshot_spec.saved

    def saving(repo, **kw):   # the worktree stays held through the verdict's save: no turn moves HEAD meanwhile
        held.append(any(k == spec()["worktree"] for k in measure.work._busy))
        return save(repo, **kw)
    with patch.object(measure, "commented", lambda *a: f"PR #1 에 실측 기록을 달지 못했다 — 401 {KEY}"), \
            patch("main.oneshot_review.begin", begun.append), patch.object(measure.oneshot_spec, "saved", saving):
        measure.decide("budget", measure.Decision(head=run["head"], n=run["n"], approve=True, note=f"자연스럽다 {KEY}"))
    assert held[0] and not any(k == spec()["worktree"] for k in measure.work._busy)
    assert o()["stage"] == "review" and begun == ["budget"] and o()["measures"][-1]["owner"]["approve"]
    assert "승인한 HEAD" in measure.commented({**spec(), "oneshot": {**o(), "pr1": {"number": 1, "head": "old"}}},
                                          o()["measures"][-1])
    calls = []

    def github(cmd, *a, **k):   # GitHub's PR #1 shows a newer HEAD pushed from elsewhere
        calls.append(cmd[:3])
        return subprocess.CompletedProcess(cmd, 0, "f" * 40 + "\n", "")
    with patch.object(measure.specs, "sh", github):
        assert "승인한 HEAD" in measure.commented(spec(), o()["measures"][-1])
    assert calls == [["gh", "pr", "view"]]   # never commented
    assert KEY not in json.dumps(o(), ensure_ascii=False) and "401" in o()["measures"][-1]["comment_error"]
    from main import oneshot_review
    assert "자연스럽다" in oneshot_review.measured_text(o(), run["head"])


def test_a_failure_at_the_round_cap_goes_to_the_build_agent_when_the_owner_starts_again(at_measure):
    tree, sent = at_measure
    given(url="https://example.test/other")
    from main import oneshot_review
    oneshot_spec.saved("budget", oneshot={"measure_fails": oneshot_review.cap() - 1})
    measure.measure("budget")
    assert o()["stage"] == "measure" and o()["stopped"] and o()["measures"][-1]["status"] == "failed"
    runs = len(o()["measures"])
    with patch.object(measure.oneshot_build, "send", lambda *a: sent.append(a)):
        measure.start("budget", measure.Start(head=o()["built_head"]))
    assert len(o()["measures"]) == runs, "the failed HEAD is never measured again as it is"
    assert o()["stage"] == "build" and o()["fix"] == "measure" and o()["measure_fails"] == 0 and not o()["stopped"]
    assert sent[-1][1] == "build" and "total_ok=False" in sent[-1][2]
    # Back at the measurement with that failure still last and an input taken back: the one lock scope that would
    # launch a worker reads the failure first, so Start hands it to the build agent and measures nothing.
    oneshot_spec.saved("budget", oneshot={"stage": "measure", "working": None, "stopped": None})
    measure.take_back("budget", "M1", head=o()["built_head"], env="BUDGET_URL", kind="text")   # an input taken back
    started = []
    with patch.object(measure.oneshot_build, "send", lambda *a: sent.append(a)), \
            patch.object(measure.oneshot, "start_worker", lambda *a, **k: started.append(a)):
        measure.start("budget", measure.Start(head=o()["built_head"]))
    assert not started and o()["stage"] == "build"


def test_a_failed_measurement_or_a_refusal_goes_back_to_the_build_agent_which_must_commit(at_measure):
    tree, sent = at_measure
    given(url="https://example.test/other")
    measure.measure("budget")
    assert o()["stage"] == "build" and o()["fix"] == "measure" and o()["measures"][-1]["status"] == "failed"
    assert sent[-1][1] == "build" and "total_ok=False" in sent[-1][2] and "measure.json" in sent[-1][2]
    # The same HEAD comes back with no commit: refused before any gate.
    then = check(tree, "고쳤다.\n" + report())
    assert o()["stage"] == "build" and not o()["attempts"] and then is not None   # sent back, never counted
    then()
    assert "커밋이 하나도 늘지 않았다" in sent[-1][2]
    git(tree, "commit", "--allow-empty", "-m", "fix")
    check(tree, "고쳤다.\n" + report())
    assert o()["stage"] == "measure"
    oneshot_spec.saved("budget", oneshot={"working": None})
    given()
    measure.measure("budget")
    with patch.object(measure.oneshot_build, "send", lambda *a: sent.append(a)):
        measure.decide("budget", measure.Decision(head=o()["built_head"], n=o()["measures"][-1]["n"], note="목소리가 끊긴다"))
    assert o()["stage"] == "build" and "목소리가 끊긴다" in sent[-1][2]


def test_an_input_the_script_calls_wrong_stops_for_the_owner_without_showing_it(at_measure):
    given(key="wrong")
    measure.measure("budget")
    assert o()["stopped"]["reason"] == "measure-input" and o()["stage"] == "measure"
    assert "M2" in o()["stopped"]["detail"] and f"[{ENV}]" in o()["stopped"]["detail"]
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


def test_an_input_goes_only_into_the_box_the_owner_saw_and_a_design_secret_is_never_text(at_measure):
    tree, _ = at_measure
    head = o()["built_head"]
    with pytest.raises(HTTPException, match="바뀌었다"):   # a screen from before the plan renamed M2
        measure.give("budget", "M2", measure.Value(value=KEY, head=head, env="BUDGET_URL", kind="text"))
    with pytest.raises(HTTPException, match="바뀌었다"):
        measure.take_back("budget", "M2", head=head, env=ENV, kind="text")
    with pytest.raises(HTTPException, match="바뀌었다"):   # the same box, from the plan of an earlier build
        measure.give("budget", "M2", measure.Value(value=KEY, head="0" * 40, env=ENV, kind="secret"))
    # The plan shown is the built HEAD's, whatever the tree holds now.
    write(tree, ".wiki/measure.json", "{")
    assert oneshot_spec.view(spec())["measure"]["plan"]["inputs"][1]["env"] == ENV
    git(tree, "checkout", "--", ".wiki/measure.json")
    assert "measure_given" not in o() or ENV not in o()["measure_given"]
    plan = json.loads((tree / ".wiki/measure.json").read_text(encoding="utf-8"))
    s = spec()
    with patch.object(measure.oneshot_needs, "secret_needs", lambda spec: [("N1", {"env": ENV, "kind": "secret"})]):
        text = {**plan, "inputs": [plan["inputs"][0], {**plan["inputs"][1], "kind": "text"}]}
        with pytest.raises(ValueError, match="비밀 값"):
            measure.checked(text, s, tree)
    with pytest.raises(ValueError, match="목록"):
        measure.checked({**plan, "metrics": {"x": 1}}, s, tree)
    bad_op = {**plan, "metrics": [{**plan["metrics"][0], "pass": {"op": [], "value": 1}}]}
    with pytest.raises(ValueError, match="pass"):
        measure.checked(bad_op, s, tree)


def test_a_result_set_aside_when_its_inputs_change_or_the_tree_moves_never_strands_the_run(at_measure):
    tree, sent = at_measure
    given()
    measure.measure("budget")
    assert o()["measures"][-1]["status"] == "owner"
    given(key="sk-other-" + "8" * 20)   # a new value while the verdict waits: that result no longer stands
    assert o()["measures"][-1]["status"] == "stale"
    measure.measure("budget")
    assert o()["measures"][-1]["status"] == "owner"
    oneshot_needs.forget("budget", ENV)   # the general route that removes a secret at any stage
    assert o()["measures"][-1]["status"] == "stale"
    gate = measure.specs.gate

    def changing(*args, **kwargs):   # the value removed while the script runs
        oneshot_needs.forget("budget", ENV)
        return gate(*args, **kwargs)
    given()
    with patch.object(measure.specs, "gate", changing):
        measure.measure("budget")
    assert o()["measures"][-1]["status"] == "stale" and o()["stage"] == "measure" and not o().get("measure_fails")
    given()
    read = measure.report_of

    def late(*args):   # the value removed after the script, before the result is saved
        oneshot_needs.forget("budget", ENV)
        return read(*args)
    with patch.object(measure, "report_of", late):
        measure.measure("budget")
    assert o()["measures"][-1]["status"] == "stale"
    given()
    runs = len(o()["measures"])
    oneshot_spec.saved("budget", oneshot={"pr1": {**o()["pr1"], "head": "old"}})   # PR #1 is published first
    with patch.object(measure.oneshot_publish, "published", lambda repo: oneshot_needs.forget("budget", ENV)):
        measure.measure("budget")   # the value taken back meanwhile: a stop for the owner, no failed run
    assert len(o()["measures"]) == runs and not o().get("measure_fails") and "M2" in o()["stopped"]["detail"]
    oneshot_spec.saved("budget", oneshot={"pr1": {**o()["pr1"], "head": o()["built_head"]}})
    given()
    measure.measure("budget")
    run = o()["measures"][-1]
    write(tree, "stray.txt", "x")   # the tree moved since it was measured
    with patch.object(measure.oneshot_build, "send", lambda *a: sent.append(a)):
        measure.decide("budget", measure.Decision(head=run["head"], n=run["n"], approve=True))
    assert o()["stage"] == "build" and o()["measures"][-1]["status"] == "stale" and "moved" in sent[-1][2]
    assert not o()["measures"][-1].get("owner")


def wrote(tmp_path, files: dict) -> Path:
    """A script's own folder holding `files` (a path and its text or bytes)."""

    raw = tmp_path / "m1.raw"
    for name, data in files.items():
        (raw / name).parent.mkdir(parents=True, exist_ok=True)
        (raw / name).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return raw


def kept(raw: Path, hide: dict) -> Path:
    """What a measurement keeps of `raw`, in a new folder; `raw` is gone."""

    out = raw.with_name("m1")
    shutil.rmtree(out, ignore_errors=True)
    assert measure.kept_from(raw, out, hide) == "" and not raw.exists()
    return out


def test_a_kept_report_and_the_clips_folder_show_no_secret_in_any_spelling(tmp_path):
    canary = 'sk-"canary\\' + "9" * 12
    hide = {canary: "[KEY]", "sk-": "[K]"}
    out = kept(wrote(tmp_path, {"report.json": json.dumps({"notes": canary}), "clips/sk-named.wav": b"RIFF",
                                "clips/ok.wav": b"RIFF", "clips/debug.txt": canary, "debug.txt": canary,
                                "serve.log": f"key={json.dumps(canary)}"}), hide)
    assert canary not in json.loads((out / "report.json").read_text(encoding="utf-8"))["notes"]
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*")) == ["clips", "clips/ok.wav", "report.json",
                                                                            "serve.log"]   # nothing else is copied
    assert json.dumps(canary)[1:-1] not in (out / "serve.log").read_text(encoding="utf-8")   # its JSON spelling too
    spelled = "".join(f"\\u{ord(c):04x}" for c in canary)   # every character escaped: decoding brings it back
    out = kept(wrote(tmp_path, {"report.json": '{"notes": "' + spelled + '"}'}), hide)
    assert canary not in json.loads((out / "report.json").read_text(encoding="utf-8"))["notes"]
    raw = wrote(tmp_path, {"report.json": "[" * 5000 + "]" * 5000})   # too deep for the reader
    assert "error" in measure.report_of(raw, {"metrics": []}, hide)
    assert not (kept(raw, hide) / "report.json").exists()
    raw = wrote(tmp_path, {"report.json": b'{"metrics": {}, "notes": "\xb0C"}'})   # a code page, not UTF-8
    assert "error" in measure.report_of(raw, {"metrics": []}, hide)
    assert not (kept(raw, hide) / "report.json").exists()   # never kept after a repair the reader refused
    assert not (kept(wrote(tmp_path, {"clips": canary}), hide) / "clips").exists()   # a file at the folder's path


def test_a_measurement_keeps_no_linked_file_and_one_read_only_file_never_stops_the_cleanup(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "theirs.wav").write_bytes(b"RIFF")
    raw = wrote(tmp_path, {"clips/x": b""})
    (raw / "clips" / "x").unlink()
    os.link(outside / "theirs.wav", raw / "clips" / "linked.wav")   # a second name for a file it did not write
    assert not (kept(raw, {}) / "clips").exists() and (outside / "theirs.wav").exists()
    if sys.platform == "win32":   # a junction needs no privilege on Windows
        raw = wrote(tmp_path, {"report.json": "{}"})
        subprocess.run(["cmd", "/c", "mklink", "/J", str(raw / "clips"), str(outside)], check=True, capture_output=True)
        assert not (kept(raw, {}) / "clips").exists() and (outside / "theirs.wav").exists()   # never followed
    raw = wrote(tmp_path, {"a-debug.txt": "x", "z-debug.txt": "x", "serve.log": "key=sk-1"})
    for name in ("a-debug.txt", "z-debug.txt"):
        os.chmod(raw / name, stat.S_IREAD)
    assert (kept(raw, {"sk-1": "[K]"}) / "serve.log").read_text(encoding="utf-8") == "key=[K]"
    raw = wrote(tmp_path, {})
    raw.mkdir()
    os.link(outside / "theirs.wav", raw / "report.json")   # the report too is never read through a second name
    assert "error" in measure.report_of(raw, {"metrics": []}, {})
    shutil.rmtree(raw)
    if sys.platform == "win32":   # the script's whole folder a junction: never read through, the link removed
        (outside / "report.json").write_text('{"metrics": {}}', encoding="utf-8")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(raw), str(outside)], check=True, capture_output=True)
        assert "error" in measure.report_of(raw, {"metrics": []}, {})
        assert not (kept(raw, {}) / "report.json").exists() and (outside / "report.json").exists()
    held = tmp_path / "served" / "clips"
    held.mkdir(parents=True)
    (held / "ok.wav").write_bytes(b"RIFF")
    os.link(outside / "theirs.wav", held / "other.wav")
    assert measure.plain(held / "ok.wav") and not measure.plain(held / "other.wav")   # what the clip route serves


def test_a_failed_copy_still_removes_the_scripts_folder_and_leaves_no_partial_report(tmp_path):
    from main import oneshot_kept
    raw = wrote(tmp_path, {"report.json": '{"metrics": {}}', "serve.log": "sk-1", "clips/ok.wav": b"RIFF"})
    with patch.object(oneshot_kept.shutil, "copyfile", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))):
        why = measure.kept_from(raw, tmp_path / "m1", {"sk-1": "[K]"})
    assert "보관하지 못했다" in why and not raw.exists()   # the copy failed: the raw folder goes all the same
    raw = wrote(tmp_path, {"report.json": '{"metrics": {}}'})
    with patch.object(oneshot_kept.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full"))):
        why = measure.kept_from(raw, tmp_path / "m2", {})
    assert why and sorted(p.name for p in (tmp_path / "m2").iterdir()) == []   # no report and no `.part` left


def test_an_unforeseen_failure_of_the_run_still_says_why_its_folder_remains(at_measure):
    given()
    with patch.object(measure, "served", lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))), \
            patch.object(measure, "kept_from", lambda *a: "실측 원본 폴더 `x` 를 지우지 못했다"):
        measure.measure("budget")
    assert "지우지 못했다" in o()["stopped"]["detail"] and not o().get("measures")


def test_a_kept_report_masks_numbers_and_punctuation_and_stays_json(tmp_path):
    hide = {"424242": "[PIN]", '"}': "[P]"}
    plan = {"metrics": [{"id": "rate", "pass": {"op": ">=", "value": 1}}]}

    def masked_report(raw, h=hide):
        return json.loads((kept(raw, h) / "report.json").read_text(encoding="utf-8"))

    raw = wrote(tmp_path, {"report.json": json.dumps({"metrics": {"rate": 424242}, "notes": 'a"}b'})})
    assert measure.report_of(raw, plan, hide)["metrics"][0]["value"] is None   # never shown as a measurement
    assert masked_report(raw) == {"metrics": {"rate": "[PIN]"}, "notes": "a[P]b"}
    raw = wrote(tmp_path, {"report.json": json.dumps({"metrics": {"rate": 424242.0}})})
    assert measure.report_of(raw, plan, hide)["metrics"][0]["value"] is None   # the same number, spelled as a float
    assert masked_report(raw) == {"metrics": {"rate": "[PIN]"}}
    raw = wrote(tmp_path, {"report.json": f'{{"metrics": {{"rate": {2 ** 53 + 3}}}}}'})
    assert measure.report_of(raw, plan, {str(2 ** 53 + 4): "[S]"})["metrics"][0]["value"] == 2 ** 53 + 3   # distinct
    raw = wrote(tmp_path, {"report.json": '{"metrics": {"rate": 1e309}}'})
    assert "1e309" not in json.dumps(measure.report_of(raw, plan, {"1e309": "[S]"}))   # refused, its token not shown
    big = {str(2 ** 53 + 1): "[BIG]"}   # past a float's precision: exact as an integer, rounded as a float
    for text in (f'{{"metrics": {{"rate": {2 ** 53 + 1}}}}}', f'{{"metrics": {{"rate": {2 ** 53 + 1}.0}}}}'):
        raw = wrote(tmp_path, {"report.json": text})
        assert measure.report_of(raw, plan, big)["metrics"][0]["value"] is None
        assert masked_report(raw, big) == {"metrics": {"rate": "[BIG]"}}
    raw = wrote(tmp_path, {"report.json": '{"metrics": {}, "notes": "\\ud800"}'})   # a lone surrogate
    assert "error" in measure.report_of(raw, plan, hide)


def test_a_number_that_is_not_finite_is_refused_in_a_report_and_a_plan(tmp_path):
    for text in ('{"a": NaN}', '{"a": Infinity}', '{"a": 1e999}'):
        with pytest.raises(ValueError):
            measure.strict(text)
    (tmp_path / "report.json").write_text('{"metrics": {"rate": Infinity}}', encoding="utf-8")
    assert "error" in measure.report_of(tmp_path, {"metrics": []}, {})
    (tmp_path / ".wiki").mkdir()
    (tmp_path / ".wiki" / "measure.json").write_text("[" * 5000 + "]" * 5000, encoding="utf-8")
    with pytest.raises(ValueError, match="읽지 못했다"):   # too deep: the plan error, with its recovery button
        measure.planned(tmp_path, {})


def test_the_health_check_never_goes_through_a_proxy():
    import urllib.request
    # The empty handler stands in for the default one, which would read the machine's proxy settings.
    assert not any(isinstance(h, urllib.request.ProxyHandler) and h.proxies for h in measure.NO_REDIRECT.handlers)
    assert any(isinstance(h, urllib.request.ProxyHandler) for h in urllib.request.build_opener().handlers) \
        or not urllib.request.getproxies()


def test_a_redirect_from_the_health_address_is_its_status_never_followed():
    import http.server
    import threading

    class Moved(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/elsewhere")
            self.end_headers()

        def log_message(self, *args):
            pass
    server = http.server.HTTPServer(("127.0.0.1", 0), Moved)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert measure.answers(f"http://127.0.0.1:{server.server_port}/health") == 302
    finally:
        server.shutdown()
        server.server_close()


def test_only_the_started_backend_vouches_for_the_health_address():
    import socket
    from types import SimpleNamespace as NS
    import threading
    with socket.socket() as listener:   # this test's own process answers on a loopback port, accepting as a server
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        accepted = []
        threading.Thread(target=lambda: [accepted.append(listener.accept()[0]) for _ in range(2)], daemon=True).start()
        assert measure.owned(NS(pid=os.getpid()), f"http://127.0.0.1:{port}/health")   # it accepted the connection
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:   # a process that started nothing answering there
            assert not measure.owned(other, f"http://127.0.0.1:{port}/health")
        finally:
            other.kill()
            other.wait()
            for c in accepted:
                c.close()
    assert not measure.owned(NS(pid=os.getpid()), f"http://127.0.0.1:{port}/health")   # nothing answers
    row = lambda pid, here, there: NS(pid=pid, laddr=NS(ip=here[0], port=here[1]),   # noqa: E731
                                      raddr=NS(ip=there[0], port=there[1]))
    table = NS(net_connections=lambda kind: [row(99, ("::1", 8000), ("::1", 50000)),   # IPv6, the same two ports
                                             row(11, ("::ffff:127.0.0.1", 8000), ("::ffff:127.0.0.1", 50000))])
    assert measure.accepted_by(table, ("127.0.0.1", 8000), ("127.0.0.1", 50000)) == 11   # both ends, mapped read
    queued = iter([[row(None, ("127.0.0.1", 8000), ("127.0.0.1", 50000))]] * 2 + [[row(11, ("127.0.0.1", 8000),
                                                                                       ("127.0.0.1", 50000))]])
    assert measure.accepted_by(NS(net_connections=lambda kind: next(queued)), ("127.0.0.1", 8000),
                               ("127.0.0.1", 50000)) == 11   # no owner while queued, then the one accepting


def test_the_app_starts_without_psutil_which_the_install_declares():
    root = APP_ROOT
    assert "psutil==" in (root / "requirements-chat.txt").read_text(encoding="utf-8")
    done = subprocess.run([sys.executable, "-c", "import sys; sys.modules['psutil'] = None; from main import app"],
                          cwd=root / "tool", capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]


def test_a_port_something_else_answers_is_refused_before_the_backend_starts(at_measure):
    import http.server
    import threading
    tree, _ = at_measure
    plan = json.loads((tree / ".wiki/measure.json").read_text(encoding="utf-8"))
    port = int(plan["serve"]["health"].split(":")[2].split("/")[0])
    other = http.server.HTTPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        given()
        measure.measure("budget")
        assert o()["measures"][-1]["status"] == "failed" and "이미 다른 프로그램" in o()["measures"][-1]["error"]
    finally:
        other.shutdown()
        other.server_close()


def test_a_stop_pressed_before_the_worker_starts_still_stops_it(at_measure):
    given()
    measure.HALTS["budget"] = measure.Halted()
    measure.stop("budget")   # pressed between `[실측 시작]` and the worker's first line
    measure.measure("budget")
    assert o()["stopped"]["reason"] == "measure" and "멈췄다" in o()["stopped"]["detail"]
    assert not o().get("measures")


def test_a_plan_that_no_longer_holds_goes_back_to_the_build_agent(at_measure):
    tree, sent = at_measure
    write(tree, ".wiki/measure.json", "{")
    git(tree, "commit", "-am", "broken plan")
    oneshot_spec.saved("budget", oneshot={"built_head": git(tree, "rev-parse", "HEAD")})
    assert oneshot_spec.view(spec())["measure"]["plan"] is None
    measure.start("budget", measure.Start(head=o()["built_head"]))
    assert o()["stage"] == "build" and sent[-1][1] == "build" and "no longer holds" in sent[-1][2]
