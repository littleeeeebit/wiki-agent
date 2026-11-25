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
if key.startswith("wrong"):
    report = {"input_error": {"input": "M2", "why": "the service refused key " + key}}
    if pathlib.Path(__file__).with_name("checked.txt").exists():   # what a build turn adds once a check shows it
        report["input_error"]["checked"] = {"how": "서비스에 직접 물었다", "answer": "키 " + key + " 를 모른다고 했다"}
else:
    report = {"metrics": {"total_ok": os.environ.get("BUDGET_URL") == "https://example.test/live"},
              "notes": "키 " + key + " 로 불렀다"}
(out / "clips").mkdir()
(out / "clips" / "sample.wav").write_bytes(b"RIFF")
(out / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
print("used", key)
'''
KEY = "sk-measure-" + "7" * 20
WRONG = "wrong-" + "8" * 16   # a key the stand-in's service refuses


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
    for name in ("PATH", "TMP", "TEMP", "TMPDIR", "PYTHONUNBUFFERED", "PSMODULEPATH",   # the launcher sets these
                 "PWD", "IFS", "OPTIND", "PPID", "SHLVL", "POSIXLY_CORRECT", "BASH_ENV", "BASH_VERSION"):   # and sh's
        with pytest.raises(ValueError, match="띄우는 쪽이 정하는"):
            measure.checked({**plan, "inputs": [{**plan["inputs"][1], "env": name}]}, s, tree)
    s["oneshot"]["design"]["outside"] = [{"id": "X1"}]
    with pytest.raises(ValueError, match="X1"):   # a real call the measurement never reaches
        measure.checked(plan, s, tree)


def test_a_measurement_runs_the_backend_and_its_script_with_the_inputs_and_the_owner_approves(at_measure):
    tree, sent = at_measure
    with pytest.raises(HTTPException, match="M1, M2"):
        measure.start("budget", measure.Start(head=o()["built_head"]))
    given()
    measure.measure("budget", "owner")
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
    with patch.object(measure.release, "secret_read", lambda name: (_ for _ in ()).throw(OSError("locked"))), \
            pytest.raises(HTTPException, match="읽지 못했다"):   # no note kept behind a mask missing a secret
        measure.decide("budget", measure.Decision(head=run["head"], n=run["n"], approve=True, note=f"자연스럽다 {KEY}"))
    assert not o()["measures"][-1].get("owner") and not any(k == spec()["worktree"] for k in measure.work._busy)
    begun, held, save = [], [], measure.oneshot_spec.saved

    def saving(repo, **kw):   # the worktree stays held through the verdict's save: no turn moves HEAD meanwhile
        held.append(any(k == spec()["worktree"] for k in measure.work._busy))
        return save(repo, **kw)
    with patch.object(measure, "commented", lambda *a: f"PR #1 에 실측 기록을 달지 못했다 — 401 {KEY}"), \
            patch("main.oneshot_review.begin", begun.append), patch.object(measure.oneshot_spec, "saved", saving), \
            patch.object(measure.oneshot_build, "built_env", lambda *a: (_ for _ in ()).throw(ValueError("no SDK"))):
        # the note's mask is read apart from the launch toolchain: a toolchain gone since never blocks the verdict
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
    with patch.object(measure.oneshot_publish.tempfile, "NamedTemporaryFile", lambda *a, **k: (_ for _ in ()).throw(
            OSError("temp volume full"))):   # a failed body file is a failed comment, never a stranded review
        assert "달지 못했다" in measure.commented(spec(), o()["measures"][-1])
    assert KEY not in json.dumps(o(), ensure_ascii=False) and "401" in o()["measures"][-1]["comment_error"]
    from main import oneshot_review
    assert "자연스럽다" in oneshot_review.measured_text(o(), run["head"])


def test_a_failure_at_the_round_cap_goes_to_the_build_agent_when_the_owner_starts_again(at_measure):
    tree, sent = at_measure
    given(url="https://example.test/other")
    from main import oneshot_review
    oneshot_spec.saved("budget", oneshot={"measure_fails": oneshot_review.cap() - 1})
    measure.measure("budget", "owner")
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
    measure.measure("budget", "owner")
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
    measure.measure("budget", "owner")
    with patch.object(measure.oneshot_build, "send", lambda *a: sent.append(a)):
        measure.decide("budget", measure.Decision(head=o()["built_head"], n=o()["measures"][-1]["n"], note="목소리가 끊긴다"))
    assert o()["stage"] == "build" and "목소리가 끊긴다" in sent[-1][2]


def test_an_input_the_script_calls_wrong_goes_to_the_build_until_a_check_outside_the_program_shows_it(at_measure):
    tree, sent = at_measure
    given(key=WRONG)
    measure.measure("budget", "owner")
    last = o()["measures"][-1]
    assert o()["stage"] == "build" and o()["measure_fails"] == o()["input_fails"] == 1   # never the owner's
    assert o()["input_sent"] == [{"input": "M2", "env": ENV, "what": "서비스 키", "kind": "secret"}]
    assert last["status"] == "failed" and last["error"].startswith("실측 스크립트가 주인이 넣은 입력 M2")
    assert "refused" not in last["error"]   # the card shows the server's Korean; the script's reason is the build's
    assert "the service refused" in sent[-1][2] and '"checked"' in sent[-1][2] and WRONG not in sent[-1][2]
    git(tree, "commit", "--allow-empty", "-m", "fix")
    check(tree, "고쳤다.\n" + report())
    oneshot_spec.saved("budget", oneshot={"working": None})
    given(key=WRONG + "9")   # a changed value is the build's to see first again
    assert not o().get("input_sent") and not o()["input_fails"]
    given(key=WRONG)
    measure.measure("budget", "owner")
    assert o()["stage"] == "build" and o()["measure_fails"] == 2 and not o().get("stopped")
    write(tree, "checked.txt", "")
    git(tree, "add", "-A")
    git(tree, "commit", "-m", "check")
    check(tree, "확인했다.\n" + report())
    oneshot_spec.saved("budget", oneshot={"working": None})
    measure.measure("budget", "owner")
    assert o()["stopped"]["reason"] == "measure-input" and o()["stage"] == "measure" and not o()["input_sent"]
    assert o()["measure_fails"] == 0   # the owner's press sends the next report to the build, never to the cap
    assert o()["stopped"]["detail"].startswith("입력 M2 이 틀렸다고 빌드가 확인했다 — 서비스에 직접 물었다 — 서비스의 답: 키 [")
    assert f"[{ENV}]" in o()["stopped"]["detail"] and WRONG not in json.dumps(o(), ensure_ascii=False)
    given()
    measure.start("budget", measure.Start(head=o()["built_head"]))   # one press starts it again
    from main import oneshot
    oneshot._workers["spec:budget"].join(60)
    assert o()["measures"][-1]["status"] == "passed" and o()["measures"][-1]["mode"] == "auto"   # the server's own first


def test_an_input_disputed_only_in_the_owners_measurement_reaches_the_cap_through_machine_passes(at_measure):
    from main import oneshot_review

    store, head = oneshot_spec.store("budget"), o()["built_head"]
    bad = [{"input": i, "env": i, "what": "값", "kind": "text", "why": "no", "said": "말", "asked": ""} for i in ("M1", "M2")]

    def owner(n):   # two inputs in turn: the count is every input's
        return measure.result("budget", store, {"n": n, "head": head, "mode": "owner", "status": "input",
                                                "input_error": bad[n % 2]})

    for n in range(1, oneshot_review.cap()):
        assert owner(n) is not None and o()["input_fails"] == o()["measure_fails"] == n
        measure.result("budget", store, {"n": 50 + n, "head": head, "mode": "auto", "status": "passed"})
        assert o()["measure_fails"] == 0 and o()["input_fails"] == n   # a machine pass leaves the disputes' count
    assert owner(98) is None and o()["stopped"]["reason"] == "measure" and f"{oneshot_review.cap()}번" in o()["stopped"]["detail"]


def test_a_dispute_stops_for_the_owner_only_once_its_own_input_was_handed_to_the_build(at_measure):
    from main import oneshot_review

    store, head = oneshot_spec.store("budget"), o()["built_head"]
    sent = {"input": "M2", "env": ENV, "what": "서비스 키", "kind": "text"}   # a plan before, the same id as text
    bad = {**sent, "kind": "secret", "why": "no", "said": "말", "asked": "주인에게 묻는 말"}
    oneshot_spec.saved("budget", oneshot={"input_sent": [sent], "measure_fails": oneshot_review.cap() - 1})
    assert measure.result("budget", store, {"n": 1, "head": head, "mode": "auto", "status": "input",
                                            "input_error": bad}) is None
    assert o()["stopped"]["reason"] == "measure" and o()["input_sent"] == [sent]   # capped: not handed over yet
    given(key=WRONG)   # a new value: the capped report is of the one before
    measure.start("budget", measure.Start(head=head))
    assert o()["stage"] == "build" and o()["input_sent"] == [] and "`M2`" in at_measure[1][-1][2]   # sent, not handed
    oneshot_spec.saved("budget", oneshot={"stage": "measure", "working": None, "measure_fails": oneshot_review.cap() - 1})
    assert measure.result("budget", store, {"n": 2, "head": head, "mode": "auto", "status": "input",
                                            "input_error": bad}) is None
    measure.start("budget", measure.Start(head=head))   # the retry sends a report of this value: handed over
    assert o()["stage"] == "build" and o()["input_sent"] == [{k: bad[k] for k in measure.DISPUTED}]


def test_after_the_owners_stop_the_next_report_goes_to_the_build_even_at_a_cap_of_one(at_measure, monkeypatch):
    from main import oneshot_review

    monkeypatch.setattr(oneshot_review, "cap", lambda: 1)
    store, head = oneshot_spec.store("budget"), o()["built_head"]
    bad = {"input": "M2", "env": ENV, "what": "서비스 키", "kind": "secret", "why": "no", "said": "말", "asked": "물음"}
    oneshot_spec.saved("budget", oneshot={"input_sent": [{k: bad[k] for k in measure.DISPUTED}]})
    assert measure.result("budget", store, {"n": 1, "head": head, "mode": "auto", "status": "input", "input_error": bad}) is None
    assert o()["stopped"]["reason"] == "measure-input" and o()["input_owed"]
    then = measure.result("budget", store, {"n": 2, "head": head, "mode": "auto", "status": "failed",
                                            "error": "`report.json` 의 input_error 가 ..."})   # even a malformed report
    assert then is not None and o()["stage"] == "build" and not o()["input_owed"]   # one build turn, cap or not
    oneshot_spec.saved("budget", oneshot={"stage": "measure", "working": None})
    assert measure.result("budget", store, {"n": 3, "head": head, "mode": "auto", "status": "input",
                                            "input_error": {**bad, "asked": ""}}) is None   # then the cap again
    oneshot_spec.saved("budget", oneshot={"input_owed": True})
    measure.result("budget", store, {"n": 4, "head": head, "mode": "owner", "status": "owner"})
    assert not o()["input_owed"]   # an owner's measurement that ran through ends what the stop owed


def test_a_stop_or_a_set_aside_result_shows_the_owner_its_sentence_masked_whole(at_measure, monkeypatch):
    store, head, hide = oneshot_spec.store("budget"), o()["built_head"], {"비밀 값이": "[K]"}
    measure.decided("budget", store, {"n": 1, "head": head, "mode": "auto", "status": "passed"}, -1, hide)
    assert o()["measures"][-1]["status"] == "stale" and "비밀 값이" not in json.dumps(o(), ensure_ascii=False)
    monkeypatch.setitem(measure.HALTS, "budget", halted := measure.Halted())
    halted.set()
    measure.decided("budget", store, {"n": 2, "head": head, "mode": "auto", "status": "passed"}, 0, {"실측을 멈췄다": "[K]"})
    assert "실측을 멈췄다" not in o()["stopped"]["detail"]


def test_a_report_naming_no_declared_input_or_a_blank_check_is_never_the_owners(tmp_path):
    plan = {"inputs": [{"id": "M2", "env": "K", "what": "키", "kind": "secret"}],
            "metrics": [{"id": "ok", "person": False, "pass": {"op": "==", "value": True}}]}

    def read(bad, hide=None):
        (tmp_path / "report.json").write_text(json.dumps({"input_error": bad, "metrics": {"ok": True}}), encoding="utf-8")
        return measure.report_of(tmp_path, plan, hide or {})

    long = "sk-" + "5" * 60   # a secret longer than any cut: none of the script's words is repeated
    assert read({"input": long, "why": "x"}, {long: "[K]"}) == read({"input": "M9", "why": "x"}) and "error" in read({})
    assert "error" in read({"input": "M2"}) and "error" in read(None)   # never a passing report around it
    assert "error" in read({"input": "M2", "why": " \n"})   # no reason is no dispute
    told = {"how": "서비스에 직접 물었다", "answer": "그런 키는 없다고 했다"}
    for checked in ("서비스가 모른다고 했다", {"how": "  ", "answer": " "}, {"how": "asked the API 가", "answer": "no 나"},
                    {"how": "확인", "answer": "없다"}, {"how": told["how"]}, {**told, "how": "가나다라 サービスに直接問い合わせた"},
                    {**told, "how": "서비스에 직접 물었다" * 60}):   # no Korean account of a check and its answer, whole
        assert read({"input": "M2", "why": "x", "checked": checked})["input_error"]["asked"] == ""
    secret = "M2 이 틀렸다고"   # a secret spanning the server's words and the input's id
    shown = read({"input": "M2", "why": "x", "checked": told}, {secret: "[K]"})["input_error"]
    assert secret not in shown["said"] + shown["asked"] and "[K]" in shown["asked"] and shown["kind"] == "secret"


def test_a_metric_passes_only_on_a_value_of_its_own_kind_and_an_empty_clip_is_none(tmp_path):
    plan = {"metrics": [{"id": "gaps", "pass": {"op": "==", "value": 0}},
                        {"id": "voice", "pass": {"op": "==", "value": True}}]}

    def oks(values):
        (tmp_path / "report.json").write_text(json.dumps({"metrics": values}), encoding="utf-8")
        return [row["ok"] for row in measure.report_of(tmp_path, plan, {})["metrics"]]

    assert oks({}) == oks({"gaps": None, "voice": None}) == [False, False]   # nothing observed fails
    assert oks({"gaps": False, "voice": 1}) == [False, False]   # `False == 0` and `1 == True` pass nothing
    assert oks({"gaps": 0, "voice": True}) == [True, True]
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips" / "empty.wav").write_bytes(b"")
    (tmp_path / "clips" / "said.wav").write_bytes(b"RIFF")
    assert measure.clips_of(tmp_path, {}) == ["said.wav"]   # a clip with nothing in it is none


def test_the_backend_that_never_answers_fails_the_measurement(at_measure):
    tree, sent = at_measure
    plan = json.loads((tree / ".wiki/measure.json").read_text(encoding="utf-8"))
    plan["serve"]["command"] = f'"{sys.executable}" -c "pass"'
    write(tree, ".wiki/measure.json", json.dumps(plan, ensure_ascii=False))
    git(tree, "commit", "-am", "broken serve")
    oneshot_spec.saved("budget", oneshot={"built_head": git(tree, "rev-parse", "HEAD")})
    given()
    measure.measure("budget", "owner")
    assert o()["measures"][-1]["status"] == "failed" and "떠 있지 않고 끝났다" in o()["measures"][-1]["error"]


def test_the_end_of_a_startup_log_is_cut_only_after_its_secrets_are_masked(tmp_path):
    (log := tmp_path / "serve.log").write_text("start sk-secret-value end", encoding="utf-8")
    assert measure.tail(log, {"sk-secret-value": "[K]"}, size=8) == "[K] end"   # never a fragment of the value
    log.write_text(json.dumps({"key": 'sk-"q\\uote'}), encoding="utf-8")   # logged as JSON: its escaped spelling
    hide = measure.spelled({'sk-"q\\uote': "[K]"})   # as `env_of` gives it
    assert "uote" not in measure.tail(log, hide) and "[K]" in measure.tail(log, hide)
    for written in (b"start sk-line\r\nend-part done", b"start sk-line\r\r\nend-part done"):   # read as text; and
        log.write_bytes(written)                                    # a Windows text-mode write before the read
        assert "end-part" not in measure.tail(log, measure.spelled({"sk-line\r\nend-part": "[K]"}))


def test_the_running_card_drops_the_cut_off_once_the_script_returns_or_raises(at_measure):
    given()
    seen = []

    def gate(*a, **k):
        seen.append(dict(measure.RUNNING["budget"]))
        if len(seen) > 1:
            raise OSError("the script could not start")
        return 0, "", ""

    with patch.object(measure.specs, "gate", gate):
        for _ in range(2):   # returned, then raised
            measure.measure("budget", "owner")
            assert measure.RUNNING["budget"]["cut"] is None and measure.RUNNING["budget"]["ended"]
    assert all(s["cut"] and not s["ended"] for s in seen)   # while it ran, its cut-off was shown


def test_the_scripts_output_is_masked_in_the_same_spellings_as_the_backend_log(at_measure, tmp_path):
    given()
    seen = []
    with patch.object(measure, "spelled", lambda hide: {**hide, "escaped-form": "[E]"}), \
            patch.object(measure.specs, "gate", lambda *a, **k: seen.append(k["hide"]) or (1, "", "")):
        measure.measure("budget", "owner")
    assert seen and seen[0].get("escaped-form") == "[E]" and KEY in seen[0]
    secret = 'sk-"q\\uote'   # a diagnostic serialized inside the report's notes keeps its escape
    (tmp_path / "report.json").write_text(json.dumps({"metrics": {}, "notes": json.dumps({"key": secret})}),
                                          encoding="utf-8")
    found = json.dumps(measure.report_of(tmp_path, {"metrics": []}, measure.spelled({secret: "[K]"})))
    assert "uote" not in found and "[K]" in found


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
    measure.measure("budget", "owner")
    assert o()["measures"][-1]["status"] == "owner"
    head = o()["built_head"]
    for change in (lambda: oneshot_needs.forget("budget", ENV),   # every route: the general one and the boxes
                   lambda: measure.give("budget", "M2", measure.Value(value="sk-new", head=head, env=ENV, kind="secret")),
                   lambda: measure.take_back("budget", "M2", head=head, env=ENV, kind="secret")):
        with patch.object(measure, "retire", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))), \
                pytest.raises(HTTPException, match="바꾸지 않았다"):   # the result set aside first, or the store kept
            change()
        assert release.secret_read(oneshot_needs.name("budget", ENV)) == KEY and o()["measures"][-1]["status"] == "owner"
        load, reads = measure.specs.load, iter(range(99))

        def refused(*a, **k):   # the caller's read passes, the set-aside's reread is refused as `specs.load` does
            return None if next(reads) else load(*a, **k)
        with patch.object(measure.specs, "load", refused), pytest.raises(HTTPException, match="바꾸지 않았다"):
            change()
        assert release.secret_read(oneshot_needs.name("budget", ENV)) == KEY and o()["measures"][-1]["status"] == "owner"
    url = o()["measure_given"]["BUDGET_URL"]["value"]
    for nothing in (lambda: measure.give("budget", "M2", measure.Value(value=KEY, head=head, env=ENV, kind="secret")),
                    lambda: oneshot_needs.forget("budget", "NEVER_GIVEN_KEY"),   # changes nothing: never set aside
                    lambda: measure.give("budget", "M1", measure.Value(value=f" {url} ", head=head, env="BUDGET_URL",
                                                                        kind="text"))):
        with patch.object(measure, "retire", lambda *a, **k: (_ for _ in ()).throw(AssertionError("set aside"))):
            nothing()
        assert o()["measures"][-1]["status"] == "owner"
    with patch.object(measure.release, "secret_read", lambda name: (_ for _ in ()).throw(OSError("locked"))), \
            patch.object(measure, "retire", lambda *a, **k: (_ for _ in ()).throw(AssertionError("set aside"))), \
            pytest.raises(HTTPException, match="읽지 못해") as e:   # whether it would change is unknown: retry
        oneshot_needs.forget("budget", "NEVER_GIVEN_KEY")
    assert e.value.status_code == 409 and o()["measures"][-1]["status"] == "owner"
    with patch.object(measure, "retire", lambda *a, **k: (_ for _ in ()).throw(AssertionError("set aside"))), \
            pytest.raises(HTTPException, match="너무 길다") as e:   # too long for the store: refused before anything
        measure.give("budget", "M2", measure.Value(value="k" * 1281, head=head, env=ENV, kind="secret"))
    assert e.value.status_code == 400 and o()["measures"][-1]["status"] == "owner"
    for refused_by_store in (lambda: measure.give("budget", "M2", measure.Value(value="sk-new", head=head, env=ENV,
                                                                                 kind="secret")),
                             lambda: oneshot_needs.forget("budget", ENV)):   # set aside, then put back
        with patch.object(measure.release, "secret_write", lambda *a: (_ for _ in ()).throw(OSError("denied"))), \
                patch.object(measure.release, "secret_delete", lambda *a: (_ for _ in ()).throw(OSError("denied"))), \
                pytest.raises(HTTPException, match="고치지 못했다"):
            refused_by_store()
        assert release.secret_read(oneshot_needs.name("budget", ENV)) == KEY and o()["measures"][-1]["status"] == "owner"
    with patch.object(oneshot_needs, "written", lambda repo: (_ for _ in ()).throw(PermissionError("busy"))), \
            pytest.raises(HTTPException, match="확인하지 못해") as e:   # the set-aside's file unknown: undone first
        measure.give("budget", "M2", measure.Value(value="sk-new", head=head, env=ENV, kind="secret"))
    assert e.value.status_code == 409 and o()["measures"][-1]["status"] == "owner"
    assert release.secret_read(oneshot_needs.name("budget", ENV)) == KEY and not oneshot_needs.BACK.get("budget")
    with patch.object(measure.release, "secret_write", lambda *a: (_ for _ in ()).throw(OSError("denied"))), \
            patch.object(oneshot_needs, "saved", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))), \
            pytest.raises(HTTPException, match="되돌리지 못했다"):   # nor can the result be put back yet
        measure.give("budget", "M2", measure.Value(value="sk-new", head=head, env=ENV, kind="secret"))
    assert release.secret_read(oneshot_needs.name("budget", ENV)) == KEY and o()["measures"][-1]["status"] == "stale"
    with patch.object(oneshot_needs, "written", lambda repo: (_ for _ in ()).throw(PermissionError("busy"))), \
            pytest.raises(HTTPException, match="되돌리지 못했다"):   # which write the file is, unknown: kept for a retry
        measure.give("budget", "M2", measure.Value(value=KEY, head=head, env=ENV, kind="secret"))
    assert o()["measures"][-1]["status"] == "stale" and oneshot_needs.BACK.get("budget")
    measure.give("budget", "M2", measure.Value(value=KEY, head=head, env=ENV, kind="secret"))   # the retry puts it back
    assert o()["measures"][-1]["status"] == "owner" and not oneshot_needs.BACK.get("budget")
    with patch.object(measure.release, "secret_write", lambda *a: (_ for _ in ()).throw(OSError("denied"))), \
            patch.object(oneshot_needs, "saved", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))), \
            pytest.raises(HTTPException, match="되돌리지 못했다"):
        measure.give("budget", "M2", measure.Value(value="sk-new", head=head, env=ENV, kind="secret"))
    for value in ("https://example.test/other", url):   # saved over it, even back to the same record: never put back
        measure.give("budget", "M1", measure.Value(value=value, head=head, env="BUDGET_URL", kind="text"))
    measure.give("budget", "M2", measure.Value(value=KEY, head=head, env=ENV, kind="secret"))
    assert o()["measures"][-1]["status"] == "stale" and not oneshot_needs.BACK.get("budget")
    oneshot_needs.BACK["budget"] = [(oneshot_needs.written("budget"), {"measures": [*o()["measures"][:-1],
                                                                                 {**o()["measures"][-1], "status": "owner"}]})]
    measure.specs.save(spec())   # the same record saved by another route, as the task editor does: a new file
    measure.give("budget", "M2", measure.Value(value=KEY, head=head, env=ENV, kind="secret"))
    assert o()["measures"][-1]["status"] == "stale" and not oneshot_needs.BACK.get("budget")
    given(key="sk-other-" + "8" * 20)   # a new value while the verdict waits: that result no longer stands
    assert o()["measures"][-1]["status"] == "stale"
    measure.measure("budget", "owner")
    assert o()["measures"][-1]["status"] == "owner"
    shown = oneshot_needs.forget("budget", ENV)   # the general route that removes a secret at any stage
    assert o()["measures"][-1]["status"] == "stale" and shown["oneshot"]["measures"][-1]["status"] == "stale"
    gate = measure.specs.gate

    def refused(*args, **kwargs):   # a removal refused while the script runs changes nothing it ran with
        with patch.object(measure, "retire", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))), \
                pytest.raises(HTTPException, match="바꾸지 않았다"):
            oneshot_needs.forget("budget", ENV)
        oneshot_needs.forget("budget", "NEVER_GIVEN_KEY")   # nothing there to remove
        oneshot_needs.stored("budget", ENV, KEY)   # the value it ran with, given again
        return gate(*args, **kwargs)
    given()
    with patch.object(measure.specs, "gate", refused):
        measure.measure("budget", "owner")
    assert o()["measures"][-1]["status"] == "owner"

    def changing(*args, **kwargs):   # the value removed while the script runs
        oneshot_needs.forget("budget", ENV)
        return gate(*args, **kwargs)
    given()
    with patch.object(measure.specs, "gate", changing):
        measure.measure("budget", "owner")
    assert o()["measures"][-1]["status"] == "stale" and o()["stage"] == "measure" and not o().get("measure_fails")
    given()
    read = measure.report_of

    def late(*args):   # the value removed after the script, before the result is saved
        oneshot_needs.forget("budget", ENV)
        return read(*args)
    with patch.object(measure, "report_of", late):
        measure.measure("budget", "owner")
    assert o()["measures"][-1]["status"] == "stale"
    given()
    runs = len(o()["measures"])
    oneshot_spec.saved("budget", oneshot={"pr1": {**o()["pr1"], "head": "old"}})   # PR #1 is published first
    with patch.object(measure.oneshot_publish, "published", lambda repo: oneshot_needs.forget("budget", ENV)):
        measure.measure("budget", "owner")   # the value taken back meanwhile: a stop for the owner, no failed run
    assert len(o()["measures"]) == runs and not o().get("measure_fails") and "M2" in o()["stopped"]["detail"]
    assert not list((oneshot_spec.store("budget").folder / "measure").glob("*.raw"))   # no folder made for it
    oneshot_spec.saved("budget", oneshot={"pr1": {**o()["pr1"], "head": o()["built_head"]}})
    given()
    measure.measure("budget", "owner")
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
    def partly(src, dst, **_):   # some bytes written, then the disk is full
        Path(dst).write_bytes(b"RI")
        raise OSError("disk full")

    with patch.object(oneshot_kept.shutil, "copyfile", partly):
        why = measure.kept_from(raw, tmp_path / "m1", {"sk-1": "[K]"})
    assert "보관하지 못했다" in why and not raw.exists()   # the copy failed: the raw folder goes all the same
    assert sorted(p.name for p in (tmp_path / "m1" / "clips").iterdir()) == []   # no partial clip, no `.part`
    raw = wrote(tmp_path, {"report.json": '{"metrics": {}}'})
    with patch.object(oneshot_kept.Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(OSError("read fault"))):
        why = measure.kept_from(raw, tmp_path / "m3", {})
    assert "보관하지 못했다" in why   # a failed read is no invalid report: the run stops for the owner
    looked = oneshot_kept.plain

    def unlooked(p, *a):   # the report's own look fails: never taken for a missing report
        if p.name == "report.json":
            raise PermissionError("look")
        return looked(p, *a)

    raw = wrote(tmp_path, {"report.json": '{"metrics": {}}'})
    with patch.object(oneshot_kept, "plain", unlooked):
        why = measure.kept_from(raw, tmp_path / "m5", {})
    assert "보관하지 못했다" in why and not raw.exists()
    raw = wrote(tmp_path, {"serve.log": "sk-1"})
    with patch.object(oneshot_kept, "masked", lambda *a: (_ for _ in ()).throw(MemoryError())):
        why = measure.kept_from(raw, tmp_path / "m6", {"sk-1": "[K]"})
    assert "MemoryError" in why and not raw.exists()   # anything unforeseen: the raw folder goes all the same
    raw = wrote(tmp_path, {"serve.log": "sk-1"})
    with patch.object(oneshot_kept, "spelled", lambda *a: (_ for _ in ()).throw(MemoryError())):
        why = measure.kept_from(raw, tmp_path / "m7", {"sk-1": "[K]"})
    assert "MemoryError" in why and not raw.exists()   # the mask's own setup too
    raw = wrote(tmp_path, {"serve.log": "sk-1"})
    real = oneshot_kept.os.lstat

    def denied(p, *a, **k):   # the raw folder cannot be looked at: never taken for gone
        if Path(p) == raw:
            raise PermissionError("look")
        return real(p, *a, **k)

    with patch.object(oneshot_kept.os, "lstat", denied), patch.object(oneshot_kept.shutil, "rmtree", lambda *a, **k: None):
        assert "지우지 못했다" in oneshot_kept.removed(raw)
    (tmp_path / "measure").mkdir()
    with patch.object(oneshot_kept.os, "scandir", lambda *a: (_ for _ in ()).throw(PermissionError("listing"))):
        why = oneshot_kept.fresh(tmp_path / "measure" / "m4", tmp_path / "measure" / "m4.raw")
    assert "읽지 못했다" in why and not (tmp_path / "measure" / "m4.raw").exists()   # never taken for no leftover
    raw = wrote(tmp_path, {"report.json": '{"metrics": {}}'})
    with patch.object(oneshot_kept.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full"))):
        why = measure.kept_from(raw, tmp_path / "m2", {})
    assert why and sorted(p.name for p in (tmp_path / "m2").iterdir()) == []   # no report and no `.part` left


def test_an_unforeseen_failure_of_the_run_still_says_why_its_folder_remains(at_measure, tmp_path):
    given()
    with patch.object(measure.product, "served", lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))), \
            patch.object(measure, "kept_from", lambda *a: "실측 원본 폴더 `x` 를 지우지 못했다"):
        measure.measure("budget", "owner")
    assert "지우지 못했다" in o()["stopped"]["detail"] and not o().get("measures")
    oneshot_spec.saved("budget", oneshot={"stopped": None})
    with patch.object(measure.product, "served", lambda *a: (_ for _ in ()).throw(FileNotFoundError(f"{KEY}\\gate"))):
        measure.measure("budget", "owner")   # a launch error naming an input: the folder goes, and the text is masked
    assert "FileNotFoundError" in o()["stopped"]["detail"] and KEY not in json.dumps(o(), ensure_ascii=False)
    oneshot_spec.saved("budget", oneshot={"stopped": None})
    with patch.object(measure, "has", lambda *a: True), \
            patch.object(measure.release, "secret_read", lambda name: (_ for _ in ()).throw(OSError("locked"))):
        measure.measure("budget", "owner")   # present a moment ago, the read for the launch failed: a stop, before any folder
    assert "읽지 못했다" in o()["stopped"]["detail"] and not o().get("measures")
    assert not list((oneshot_spec.store("budget").folder / "measure").glob("*.raw"))
    oneshot_spec.saved("budget", oneshot={"stopped": None})
    read = set()

    def once(now, x):   # the early check reads the store; a second read of the same secret fails
        if x["kind"] == "secret" and x["id"] in read:
            return False
        read.add(x["id"])
        return True

    with patch.object(measure, "has", once), \
            patch.object(measure.product, "served", lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))):
        measure.measure("budget", "owner")   # a secret's presence is the launch read's, never a second read of the store
    assert "예상하지 못한" in o()["stopped"]["detail"]
    oneshot_spec.saved("budget", oneshot={"stopped": None})
    with patch.object(measure, "report_of", lambda *a: {"io": "실측 보고서를 읽지 못했다 (PermissionError)"}):
        measure.measure("budget", "owner")   # the disk, not the build: no failure counted
    assert "읽지 못했다" in o()["stopped"]["detail"] and not o().get("measures") and not o().get("measure_fails")
    raw = tmp_path / "m1.raw"
    (raw / "report.json").parent.mkdir()
    (raw / "report.json").write_text("{}", encoding="utf-8")
    with patch.object(measure.Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked"))):
        assert "io" in measure.report_of(raw, {"metrics": []}, {})
    assert "error" in measure.report_of(tmp_path / "none", {"metrics": []}, {})   # no report: the build's
    told = []
    with patch.object(measure.oneshot_spec, "saved", lambda *a, **k: (_ for _ in ()).throw(OSError("locked"))), \
            patch.object(measure.work, "notice", lambda *a: told.append(a)):
        measure.noted_comment("budget", "PR #1 에 실측 기록을 달지 못했다")   # never raises into the review's start
    assert "저장하지도 못했다" in told[-1][1]
    with patch.object(measure.release, "secret_read", lambda name: (_ for _ in ()).throw(OSError("locked"))), \
            patch.object(oneshot_needs, "secret_needs", lambda s: [("N1", {"env": "DESIGN_KEY"})]):
        with pytest.raises(ValueError, match="읽지 못했다"):   # a design secret for the verdict: never taken for none
            oneshot_needs.gate_env(spec(), None, True)
        assert oneshot_needs.gate_env(spec(), {}) == {}   # the gates' own read still treats it as not given
        with pytest.raises(ValueError, match="DESIGN_KEY"):   # the measurement's launch: a design-only secret too
            measure.env_of(spec(), {"inputs": []})
    reads = []
    with patch.object(measure.release, "secret_read", lambda name: reads.append(name) or "tok"), \
            patch.object(oneshot_needs, "secret_needs", lambda s: [("N1", {"env": "A_TOKEN"})]):
        env, _ = measure.env_of(spec(), {"inputs": [{"id": "M1", "kind": "secret", "env": "A_TOKEN"}]})
    assert env["A_TOKEN"] == "tok" and len(reads) == 1   # named by the design and the plan: read once
    with patch.object(measure.release, "secret_read", lambda name: None), \
            patch.object(oneshot_needs, "secret_needs", lambda s: [("N1", {"env": "A_TOKEN"})]), \
            patch.dict(measure.os.environ, {"A_TOKEN": "server's own"}, clear=True):   # filtered down to nothing
        assert "A_TOKEN" not in measure.env_of(spec(), {"inputs": [{"id": "M1", "kind": "secret", "env": "A_TOKEN"}]})[0]
        assert oneshot_needs.gate_env(spec(), dict(measure.os.environ), True) == {}   # the verdict's base: never None
    plan = {"inputs": [{"id": "M1", "kind": "secret", "env": "A_TOKEN"}, {"id": "M2", "kind": "secret", "env": "B_TOKEN"}]}
    with patch.object(measure.release, "secret_read", lambda name: "same-token"):   # two inputs, one value
        env, hide = measure.env_of(spec(), plan, {"A_TOKEN": "server's own"})
    assert env["A_TOKEN"] == env["B_TOKEN"] == "same-token" and "same-token" in hide
    with patch.object(measure.release, "secret_read", lambda name: None):   # not held: never the server's variable
        assert "A_TOKEN" not in measure.env_of(spec(), plan, {"A_TOKEN": "server's own"})[0]


def test_a_kept_report_masks_numbers_and_punctuation_and_stays_json(tmp_path):
    hide = {"424242": "[PIN]", '"}': "[P]"}
    plan = {"metrics": [{"id": "rate", "pass": {"op": ">=", "value": 1}}]}

    def masked_report(raw, h=hide):
        return json.loads((kept(raw, h) / "report.json").read_text(encoding="utf-8"))

    raw = wrote(tmp_path, {"report.json": json.dumps({"metrics": {"rate": 424242}, "notes": 'a"}b'})})
    assert measure.report_of(raw, plan, hide)["metrics"][0]["value"] is None   # never shown as a measurement
    assert masked_report(raw) == {"metrics": {"rate": "[PIN]"}, "notes": "a[P]b"}
    dot = measure.spelled({"·": "[D]", "%C2%B7": "[D]"})   # two keys that mask alike, the mark itself a secret
    raw = wrote(tmp_path, {"report.json": json.dumps({"metrics": {}, "extra": {"·": 1, "%C2%B7": 2}})})
    assert sorted(masked_report(raw, dot)["extra"]) == ["[D]", "[D][D]"]
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
    assert not any(isinstance(h, urllib.request.ProxyHandler) and h.proxies for h in measure.product.NO_REDIRECT.handlers)
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
        assert measure.product.answers(f"http://127.0.0.1:{server.server_port}/health") == 302
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
        assert measure.product.owned(NS(pid=os.getpid()), f"http://127.0.0.1:{port}/health")   # it accepted the connection
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:   # a process that started nothing answering there
            assert not measure.product.owned(other, f"http://127.0.0.1:{port}/health")
        finally:
            other.kill()
            other.wait()
            for c in accepted:
                c.close()
    assert not measure.product.owned(NS(pid=os.getpid()), f"http://127.0.0.1:{port}/health")   # nothing answers
    row = lambda pid, here, there: NS(pid=pid, laddr=NS(ip=here[0], port=here[1]),   # noqa: E731
                                      raddr=NS(ip=there[0], port=there[1]))
    table = NS(net_connections=lambda kind: [row(99, ("::1", 8000), ("::1", 50000)),   # IPv6, the same two ports
                                             row(11, ("::ffff:127.0.0.1", 8000), ("::ffff:127.0.0.1", 50000))])
    assert measure.product.accepted_by(table, ("127.0.0.1", 8000), ("127.0.0.1", 50000)) == 11   # both ends, mapped read
    queued = iter([[row(None, ("127.0.0.1", 8000), ("127.0.0.1", 50000))]] * 2 + [[row(11, ("127.0.0.1", 8000),
                                                                                       ("127.0.0.1", 50000))]])
    assert measure.product.accepted_by(NS(net_connections=lambda kind: next(queued)), ("127.0.0.1", 8000),
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
        measure.measure("budget", "owner")
        assert o()["measures"][-1]["status"] == "failed" and "이미 다른 프로그램" in o()["measures"][-1]["error"]
    finally:
        other.shutdown()
        other.server_close()


def test_a_stop_pressed_before_the_worker_starts_still_stops_it(at_measure):
    given()
    measure.HALTS["budget"] = measure.Halted()
    measure.stop("budget")   # pressed between `[실측 시작]` and the worker's first line
    measure.measure("budget", "owner")
    assert o()["stopped"]["reason"] == "measure" and "멈췄다" in o()["stopped"]["detail"]
    assert not o().get("measures")


def test_a_plan_that_no_longer_holds_goes_back_to_the_build_agent(at_measure):
    tree, sent = at_measure
    write(tree, ".wiki/measure.json", "{")
    git(tree, "commit", "-am", "broken plan")
    built = git(tree, "rev-parse", "HEAD")   # an older run's refusal of it still stands in the history
    finding = {"location": "app.py:2", "severity": "P1", "reason": "음수", "evidence": "add(-1)", "area": "a"}
    oneshot_spec.saved("budget", oneshot={"built_head": built,
                                          "reviews": [{"verdict": "refuse", "head": built, "findings": [finding]}]})
    assert oneshot_spec.view(spec())["measure"]["plan"] is None
    measure.start("budget", measure.Start(head=o()["built_head"]))
    assert o()["stage"] == "build" and sent[-1][1] == "build" and "no longer holds" in sent[-1][2]
    from main import oneshot_review
    again = oneshot_review.build_turn(spec())   # what `[재개]` sends after that turn stopped
    assert "no longer holds" in again and "review refused HEAD" not in again and not getattr(again, "refusal", None)
    oneshot_spec.saved("budget", oneshot={"fix": None})   # a recheck of a moved tree, or an older plan repair
    again = oneshot_review.build_turn(spec())
    assert "review refused HEAD" not in again and not getattr(again, "refusal", None)   # only `fix: review` resends it


def test_the_worker_measures_with_the_built_heads_plan_whatever_the_tree_holds(at_measure):
    tree, _ = at_measure
    given()
    git(tree, "update-index", "--skip-worktree", ".wiki/measure.json")   # an edit git status does not show
    write(tree, ".wiki/measure.json", "{")
    measure.HALTS["budget"] = measure.Halted()   # as `start` makes it: no stop pressed in an earlier run holds
    measure.measure("budget", "owner")
    assert o()["measures"][-1]["status"] == "owner"
    assert measure.committed(tree, "HEAD", ".wiki/no-such-file") is None


def test_a_committed_plan_that_is_not_utf_8_goes_back_to_the_build_agent(at_measure):
    tree, sent = at_measure   # a code-page byte in a step: never repaired into a plan the worker cannot read
    plan = tree / ".wiki/measure.json"
    plan.write_bytes(plan.read_bytes().replace(b'"what": "', b'"what": "\x97', 1))
    git(tree, "commit", "-am", "plan in a code page")
    oneshot_spec.saved("budget", oneshot={"built_head": git(tree, "rev-parse", "HEAD")})
    assert oneshot_spec.view(spec())["measure"]["plan"] is None
    measure.start("budget", measure.Start(head=o()["built_head"]))
    assert o()["stage"] == "build" and sent[-1][1] == "build" and "no longer holds" in sent[-1][2]
