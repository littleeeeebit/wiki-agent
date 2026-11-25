"""oneshot step 13: the server measures by itself until every machine metric passes, then the owner once; a run that
builds a PC app measures its program, never a headless server.

See `docs/plans/oneshot/13-measure-the-product.md`. The backend and script are `test_oneshot_measure`'s stand-ins;
the program's desktop is replaced, since a look opens a real one.
"""

import contextlib
import json
import os
import shutil
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch

import owned_job
import pytest
from fastapi import HTTPException

from main import oneshot_measure as measure, oneshot_measure_app as product, oneshot_publish, oneshot_spec
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_build import APP, built_world, check, git, o, report, spec, to_build, write  # noqa: F401
from test_oneshot_measure import ENV, KEY, SCRIPT, at_measure, given  # noqa: F401 — `at_measure` is a fixture
from test_oneshot_spec import world  # noqa: F401 — `world` is a fixture

# The stand-in script, also reporting a metric only a person reaches: the sign-in the owner's measurement makes.
PERSON = SCRIPT.replace(
    'report = {"metrics": {"total_ok": os.environ.get("BUDGET_URL") == "https://example.test/live"},',
    'report = {"metrics": {"total_ok": os.environ.get("BUDGET_URL") == "https://example.test/live",\n'
    '                      "signed_in": os.environ.get("ONESHOT_MEASURE") == "owner"},')
STEP = {"window": "브라우저 — Google 로그인", "do": "계정을 고른다", "see": "연결됐다고 보인다"}


@pytest.fixture
def with_person(at_measure):  # noqa: F811 — the imported fixture, by its name
    tree, sent = at_measure
    assert PERSON != SCRIPT
    write(tree, "measure.py", PERSON)
    plan = json.loads((tree / ".wiki/measure.json").read_text(encoding="utf-8"))
    plan["metrics"].append({"id": "signed_in", "what": "Google 로그인이 됐다", "requirements": ["R1"], "person": True,
                            "pass": {"op": "==", "value": True}})
    plan["during"] = [{**STEP, "metrics": ["signed_in"]}]
    write(tree, ".wiki/measure.json", json.dumps(plan, ensure_ascii=False))
    git(tree, "add", "-A")
    git(tree, "commit", "-m", "person")
    oneshot_spec.saved("budget", oneshot={"stage": "build", "working": "build"})   # the build turn that wrote it
    check(tree, "만들었다.\n" + report())
    assert o()["stage"] == "measure" and o()["built_head"] == git(tree, "rev-parse", "HEAD")
    oneshot_spec.saved("budget", oneshot={"working": None})
    return tree, sent


def started_once() -> None:
    from main import oneshot
    measure.start("budget", measure.Start(head=o()["built_head"]))
    oneshot._workers["spec:budget"].join(120)


def test_the_server_measures_first_and_calls_the_owner_once_every_machine_metric_passed(with_person):
    given()
    assert measure.shown(spec())["measure"]["next"] == "auto"
    started_once()
    run = o()["measures"][-1]
    by = {m["id"]: m for m in run["metrics"]}
    assert run["mode"] == "auto" and run["status"] == "passed" and not o().get("measure_fails")
    assert by["total_ok"]["ok"] and not by["signed_in"]["ok"]   # the person metric is not judged without a person
    assert o()["stage"] == "measure" and measure.shown(spec())["measure"]["next"] == "owner"
    assert measure.RUNNING["budget"]["n"] == run["n"] and measure.shown(spec())["measure"]["running"] is None   # saved
    oneshot_spec.saved("budget", oneshot={"working": "measure"})   # as while the next one runs
    measure.HALTS["budget"] = halt = measure.Halted()
    measure.RUNNING["budget"] = {"n": run["n"] + 1, "head": run["head"], "ts": 7.0, "mode": "owner", "cut": 9.0,
                                 "ended": False, "halt": halt}
    assert measure.shown(spec())["measure"]["running"] == {"n": run["n"] + 1, "mode": "owner", "ts": 7.0, "cut": 9.0,
                                                           "ended": False}
    measure.HALTS["budget"] = measure.Halted()   # a retry's own, before its worker launches: the earlier one is aside
    assert measure.shown(spec())["measure"]["running"] is None
    oneshot_spec.saved("budget", oneshot={"working": None})
    started_once()   # the owner's one measurement, on their screen
    run = o()["measures"][-1]
    assert run["mode"] == "owner" and run["status"] == "owner" and all(m["ok"] for m in run["metrics"])
    with pytest.raises(HTTPException) as e:   # waits for the owner's verdict
        measure.start("budget", measure.Start(head=o()["built_head"]))
    assert e.value.status_code == 409


def test_a_failed_machine_metric_goes_back_alone_and_the_server_measures_again(with_person):
    tree, sent = with_person
    given(url="https://example.test/other")
    started_once()
    run = o()["measures"][-1]
    assert run["mode"] == "auto" and run["status"] == "failed" and o()["stage"] == "build" and o()["fix"] == "measure"
    text = sent[-1][2]
    assert "total_ok=False" in text and "signed_in" not in text   # only what failed, never the person metric
    assert [m["id"] for m in measure.failed_metrics(run)] == ["total_ok"]
    git(tree, "commit", "--allow-empty", "-m", "fix")
    check(tree, "고쳤다.\n" + report())
    oneshot_spec.saved("budget", oneshot={"working": None})
    assert measure.shown(spec())["measure"]["next"] == "auto"   # a new build: the server's own again
    given()
    started_once()
    assert o()["measures"][-1]["status"] == "passed" and not o().get("measure_fails")


def test_each_person_metric_names_the_step_it_needs_and_each_step_its_metrics(with_person):
    tree, _ = with_person
    plan = json.loads((tree / ".wiki/measure.json").read_text(encoding="utf-8"))
    s = spec()
    assert measure.checked(plan, s, tree)["metrics"][1]["person"] is True
    with pytest.raises(ValueError, match="metrics"):
        measure.checked({**plan, "during": [STEP]}, s, tree)
    with pytest.raises(ValueError, match="total_ok"):   # a step naming a metric any machine reaches
        measure.checked({**plan, "during": [{**STEP, "metrics": ["signed_in", "total_ok"]}]}, s, tree)
    alone = {**plan, "during": [], "metrics": plan["metrics"]}
    with pytest.raises(ValueError, match="signed_in"):   # a person metric no step reaches
        measure.checked(alone, s, tree)
    with pytest.raises(ValueError, match="person"):
        measure.checked({**plan, "metrics": [{**plan["metrics"][0], "person": "yes"}, plan["metrics"][1]]}, s, tree)


def program(tree: Path) -> dict:
    """A built program in the worktree, a real Windows executable the look could start, and its `app` block."""

    (tree / "build").mkdir(exist_ok=True)
    shutil.copyfile(sys.executable, tree / "build" / "app.exe")
    return {"run": {"folder": "build", "program": "app.exe", "args": ["--data", "{data}"], "cwd": ".", "env": {}},
            "health": "http://127.0.0.1:47999/api/link/info", "seconds": 20}


def test_a_run_that_builds_a_pc_app_measures_its_program_never_a_server(at_measure):  # noqa: F811 — the imported fixture
    tree, _ = at_measure
    plan = json.loads((tree / ".wiki/measure.json").read_text(encoding="utf-8"))
    s = spec()
    app = program(tree)
    with pytest.raises(ValueError, match="`app` 은 적지 않는다"):   # no PC app: the backend headless, as step 12
        measure.checked({**plan, "app": app}, s, tree)
    s["oneshot"]["intent"] = {"kinds": ["desktop"]}
    with patch.object(measure.oneshot_build, "tools", lambda o: None):   # the PC app's toolchain is 9e's
        with pytest.raises(ValueError, match="`serve` 는 적지 않는다"):
            measure.checked({**plan, "app": app}, s, tree)
        served = {k: v for k, v in plan.items() if k != "serve"}
        assert measure.checked({**served, "app": app}, s, tree)["app"]["run"]["program"] == "app.exe"
        with pytest.raises(ValueError, match="ONESHOT_MEASURE"):   # the server's to set
            measure.checked({**served, "app": {**app, "run": {**app["run"], "env": {"ONESHOT_MEASURE": "owner"}}}}, s, tree)
        with pytest.raises(ValueError, match="data"):   # the data it keeps goes under `{data}`
            measure.checked({**served, "app": {**app, "run": {**app["run"], "args": []}}}, s, tree)
        with pytest.raises(ValueError, match=r"\.exe"):
            measure.checked({**served, "app": {**app, "run": {**app["run"], "program": "app.msix"}}}, s, tree)
        with pytest.raises(ValueError, match="app.health"):
            measure.checked({**served, "app": {**app, "health": "http://example.com/x"}}, s, tree)


class Job:
    def __init__(self, done=False):
        self.done, self.ended = done, []

    def finished(self):
        return self.done

    def members(self):
        return [{"pid": 4242}]

    def end(self, wait):
        self.ended.append(wait)
        return {"clean": True, "reasons": []}


def session(tmp_path: Path, job: Job, seen: dict):
    """A stand-in for `desktop_session`: its look records what it was asked to start, and starts nothing; with
    `refuse` in `seen`, `owned_job` refuses as Windows would, the program itself or the host. `log` is what the
    program writes."""

    refused = owned_job.Refused

    class Look:
        def __init__(self, root, name, owned, disposable):
            self.folder, self.jobs = Path(root) / name, {}
            (self.folder / "evidence").mkdir(parents=True)
            seen.update(owned=owned, disposable=disposable)

        def start(self, key, argv, cwd, extra, halt=None, output=None):
            if seen.get("refuse"):
                raise (owned_job.Unrunnable if seen["refuse"] == "program" else refused)("refused")
            output.write(seen.get("log", "app started").encode())   # what the program prints, to its pipe
            seen["evidence"] = self.folder / "evidence"
            seen.update(key=key, argv=argv, cwd=cwd, extra=extra)
            self.jobs[key] = job

        def observe(self):
            pass

    from contextlib import contextmanager

    @contextmanager
    def look(root, name, owned=True, disposable=False):
        yield Look(root, name, owned, disposable)

    return types.SimpleNamespace(look=look, Halted=type("Halted", (Exception,), {}), Refused=refused)


def test_the_frozen_program_starts_with_its_data_the_inputs_and_whose_measurement_it_is(at_measure, tmp_path):  # noqa: F811 — the imported fixture
    tree, _ = at_measure
    s = spec()
    plan = {"app": program(tree), "inputs": [{"env": "BUDGET_URL"}, {"env": ENV}]}
    env = {"BUDGET_URL": "https://example.test/live", ENV: KEY, "PATH": "C:/gate", "GATE_ONLY_TOKEN": "g"}
    halt, job, seen = measure.Halted(), Job(), {}
    answers = iter([None, 200])
    with patch.dict(sys.modules, desktop_session=session(tmp_path, job, seen)), \
            patch.dict(os.environ, {"SERVER_ONLY_TOKEN": "s3cret"}), \
            patch.object(product, "answers", lambda url: next(answers)), \
            patch.object(product, "vouched", lambda pids, url, status=False: pids == {4242}):
        with product.running(s, plan, tree, env, halt, tmp_path / "out" / "serve.log", {}, "auto") as build:
            assert not job.ended
    data = product.folder(s) / "data"
    assert not seen["owned"] and seen["disposable"] and seen["key"] == "app"   # auto too: the owner's own screen
    assert Path(seen["argv"][0]).name == "app.exe" and product.folder(s) in Path(seen["argv"][0]).parents
    assert seen["argv"][1:] == ["--data", str(data)] and data.is_dir()
    assert seen["extra"]["ONESHOT_MEASURE"] == "auto" and seen["extra"]["BUDGET_URL"] == "https://example.test/live"
    assert seen["extra"][ENV] == KEY and seen["extra"]["PATH"] == "C:/gate"   # what the script gets, no more
    assert seen["extra"]["SERVER_ONLY_TOKEN"] is None   # dropped by `Look.start`: no server secret reaches it
    assert "GATE_ONLY_TOKEN" not in seen["extra"]   # nor one the gate's environment carries that no input names
    assert job.ended == [product.END_SECONDS] and (tmp_path / "out" / "serve.log").read_text() == "app started"
    (data / "kept.txt").write_text("sign-in", encoding="utf-8")
    (tree / "build" / "app.exe").write_bytes(b"rebuilt since the pass")   # the owner never opens a new build
    seen.clear()
    answers = iter([None, 200])
    with patch.dict(sys.modules, desktop_session=session(tmp_path, Job(), seen)), \
            patch.object(product, "answers", lambda url: next(answers)), \
            patch.object(product, "vouched", lambda pids, url, status=False: True):
        with product.running(s, plan, tree, env, halt, tmp_path / "out" / "serve.log", {}, "owner", build) as same:
            assert same == build
    assert not seen["owned"] and seen["extra"]["ONESHOT_MEASURE"] == "owner"   # the owner's own screen
    assert seen["argv"][0] == str(Path(build["folder"]) / "app.exe")   # the copy that passed
    assert (data / "kept.txt").read_text(encoding="utf-8") == "sign-in"   # one data folder for the whole run
    (Path(build["folder"]) / "app.exe").write_bytes(b"changed")   # the frozen copy itself changed
    with patch.dict(sys.modules, desktop_session=session(tmp_path, Job(), {})), \
            patch.object(product, "answers", lambda url: None):
        with pytest.raises(RuntimeError, match="자동 실측부터|자동 실측 시작"), \
                product.running(s, plan, tree, env, halt, tmp_path / "o.log", {}, "owner", build):
            pass
    assert product.changed(None) and product.changed({"identity": ""})


def test_the_program_must_answer_from_its_own_job_and_not_end(at_measure, tmp_path):  # noqa: F811 — the imported fixture
    tree, _ = at_measure
    s, plan = spec(), {"app": program(tree), "inputs": []}
    with patch.dict(sys.modules, desktop_session=session(tmp_path, Job(), {})), \
            patch.object(product, "answers", lambda url: 200):
        with pytest.raises(ValueError, match="이미 다른 프로그램"), \
                product.running(s, plan, tree, {}, measure.Halted(), tmp_path / "a.log", {}, "auto"):
            pass
    answers = iter([None, 200])
    with patch.dict(sys.modules, desktop_session=session(tmp_path, job := Job(), {})), \
            patch.object(product, "answers", lambda url: next(answers)), \
            patch.object(product, "vouched", lambda pids, url, status=False: False):
        with pytest.raises(ValueError, match="띄운 PC 프로그램이 아니다"), \
                product.running(s, plan, tree, {}, measure.Halted(), tmp_path / "b.log", {}, "auto"):
            pass
    assert job.ended
    with patch.dict(sys.modules, desktop_session=session(tmp_path, Job(done=True), {})), \
            patch.object(product, "answers", lambda url: None):
        with pytest.raises(ValueError, match="떠 있지 않고 끝났다"), \
                product.running(s, plan, tree, {}, measure.Halted(), tmp_path / "c.log", {}, "auto"):
            pass
    answers, statuses = iter([None, 200, 200]), iter([False, True])   # its own socket, ready on the second read
    with patch.dict(sys.modules, desktop_session=session(tmp_path, Job(), {})), \
            patch.object(product, "answers", lambda url: next(answers)), \
            patch.object(product, "vouched", lambda pids, url, status=False: next(statuses) if status else True), \
            patch.object(product.time, "sleep", lambda s: None):
        with product.running(s, plan, tree, {}, measure.Halted(), tmp_path / "e.log", {}, "auto") as build:
            pass
    with patch.dict(sys.modules, desktop_session=session(tmp_path, Job(), {"refuse": "program"})), \
            patch.object(product, "answers", lambda url: None):
        with pytest.raises(ValueError, match="띄우지 못했다"), \
                product.running(s, plan, tree, {}, measure.Halted(), tmp_path / "f.log", {}, "auto"):
            pass   # Windows would not run it: the build's to fix, never a desktop stop
    with patch.dict(sys.modules, desktop_session=session(tmp_path, Job(), {"refuse": "host"})), \
            patch.object(product, "answers", lambda url: None):
        with pytest.raises(RuntimeError, match="데스크톱"), \
                product.running(s, plan, tree, {}, measure.Halted(), tmp_path / "f2.log", {}, "auto"):
            pass   # the host could not make the job: the owner's, never a repair spent
    answers = iter([None, 200])
    with patch.dict(sys.modules, desktop_session=session(tmp_path, Job(), {})), \
            patch.object(product, "answers", lambda url: next(answers)), \
            patch.object(product, "vouched", lambda pids, url, status=False: True), \
            patch.object(product.oneshot_desktop, "altered", lambda build: (_ for _ in ()).throw(PermissionError())):
        with pytest.raises(RuntimeError, match="다시 읽지 못했다"), \
                product.running(s, plan, tree, {}, measure.Halted(), tmp_path / "f3.log", {}, "auto"):
            pass   # a copy the host cannot read is never shown as one the program changed
    answers = iter([None, 200])
    with patch.dict(sys.modules, desktop_session=session(tmp_path, Job(), {})), \
            patch.object(product, "answers", lambda url: next(answers)), \
            patch.object(product, "vouched", lambda pids, url, status=False: True):
        with pytest.raises(ValueError, match="자기 사본을 바꿨다"), \
                product.running(s, plan, tree, {}, measure.Halted(), tmp_path / "g.log", {}, "auto") as build:
            (Path(build["folder"]) / "cache.bin").write_bytes(b"beside itself")
    refusing = session(tmp_path, Job(), {})

    def refused(*a, **k):
        raise refusing.Refused("evidence not removed")
    refusing.look = refused   # a desktop that cannot be opened or cleaned is never charged to the build
    with patch.dict(sys.modules, desktop_session=refusing), patch.object(product, "answers", lambda url: None):
        with pytest.raises(RuntimeError, match="데스크톱"), \
                product.running(s, plan, tree, {}, measure.Halted(), tmp_path / "d.log", {}, "auto"):
            pass


def test_the_status_and_the_process_answering_it_are_read_on_one_connection():
    import http.server
    import threading

    class Health(http.server.BaseHTTPRequestHandler):
        code = 200

        def do_GET(self):   # an endpoint that speaks only HTTP/1.1, as `answers`' urllib does
            self.send_response(Health.code if self.request_version == "HTTP/1.1" else 505)
            self.end_headers()

        def log_message(self, *args):
            pass
    server = http.server.HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/api/link/info"
    try:
        assert product.vouched({os.getpid()}, url, status=True)   # this process accepted the very connection that said 200
        assert not product.vouched({os.getpid() + 1}, url, status=True)   # another process's socket
        Health.code = 503
        assert not product.vouched({os.getpid()}, url, status=True)   # its own socket, but not ready
    finally:
        server.shutdown()
        server.server_close()
    assert not product.vouched({os.getpid()}, url, status=True)   # nothing answers


def test_a_result_from_before_the_server_measured_first_is_measured_again_never_judged(with_person):
    given()
    old = {"n": 1, "head": o()["built_head"], "ts": 1, "folder": "m1", "status": "owner", "metrics": []}
    oneshot_spec.saved("budget", oneshot={"measures": [old]})
    with pytest.raises(HTTPException) as e:   # no verdict on a measurement the server never passed
        measure.decide("budget", measure.Decision(head=o()["built_head"], n=1, approve=True, note=""))
    assert e.value.status_code == 409
    started_once()   # the button measures, the server's own first
    assert o()["measures"][-1]["mode"] == "auto" and o()["measures"][-1]["status"] == "passed"


def test_an_input_changed_after_the_servers_pass_sends_it_back_to_the_servers_measurement(with_person):
    given()
    started_once()
    assert measure.shown(spec())["measure"]["next"] == "owner"
    measure.give("budget", "M1", measure.Value(value="https://example.test/live2", head=o()["built_head"],
                                               env="BUDGET_URL", kind="text"))
    assert o()["measures"][-1]["status"] == "stale" and measure.shown(spec())["measure"]["next"] == "auto"


def test_the_build_agent_gets_the_reports_notes_and_the_end_of_the_measured_log(with_person):
    tree, sent = with_person
    given(url="https://example.test/other")
    started_once()
    text = sent[-1][2]
    assert "The report's notes" in text and "로 불렀다" in text and KEY not in text   # masked, as kept
    assert "The end of the log of what it measured" in text


def test_pull_request_one_starts_the_servers_own_measurement_at_once(at_measure):  # noqa: F811 — the imported fixture
    pr = {"number": 1, "url": "https://github.test/pull/1"}
    calls, notes = [], []
    with patch.object(oneshot_publish, "published", lambda repo: pr), \
            patch.object(measure, "start", lambda repo, s: calls.append((repo, s.head))), \
            patch.object(oneshot_publish.work, "notice", lambda *a: notes.append(a)):
        oneshot_publish.publish("budget")
    assert calls == [("budget", o()["built_head"])] and not notes

    def missing(repo, s):
        raise HTTPException(400, "아직 넣지 않은 값: M1")
    with patch.object(oneshot_publish, "published", lambda repo: pr), patch.object(measure, "start", missing), \
            patch.object(oneshot_publish.work, "notice", lambda *a: notes.append(a)):
        oneshot_publish.publish("budget")
    assert "M1" in notes[-1][1]   # an input not given yet: the owner's press starts it


def test_the_owners_measurement_is_chosen_with_the_values_it_measures(with_person):
    given()
    started_once()
    assert measure.shown(spec())["measure"]["next"] == "owner"
    first = measure.planned

    def moved_meanwhile(*a, **k):   # a secret changed after the worker's first read, before the values' read
        plan = first(*a, **k)
        with measure.oneshot._lock:
            measure.retire(spec(), "비밀 값이 바뀌었다")
        return plan
    with patch.object(measure, "planned", moved_meanwhile):
        started_once()
    assert o()["measures"][-1]["mode"] == "auto"   # the server's own again, never the owner's on new values


def test_a_gate_log_that_cannot_be_removed_stops_for_the_owner(at_measure):  # noqa: F811 — the imported fixture
    tree, _ = at_measure
    run = types.SimpleNamespace(halt=measure.Halted(), put=lambda *a, **k: None,   # a gate run's note goes nowhere
                                chat=types.SimpleNamespace(id="c", parent_id=None))
    with patch.object(measure.shutil, "rmtree", lambda *a, **k: None), patch.object(measure.time, "sleep", lambda s: None), \
            pytest.raises(RuntimeError, match="임시 로그"):
        measure.serve_check(tree, run, spec())
    tries, removed = [], shutil.rmtree   # `measure.shutil` is this module: the real one, kept before the patch

    def busy_once(path, *a, **k):   # a file the ended backend held a moment ago, then let go
        tries.append(path)
        if len(tries) == 1:
            raise PermissionError("in use")
        removed(path)
    with patch.object(measure.shutil, "rmtree", busy_once), patch.object(measure.time, "sleep", lambda s: None):
        assert measure.serve_check(tree, run, spec()) == "" and len(tries) == 2


def test_the_builds_health_check_keeps_the_program_off_the_owners_screen(at_measure, tmp_path):  # noqa: F811
    tree, _ = at_measure
    run = types.SimpleNamespace(halt=measure.Halted(), put=lambda *a, **k: None,
                                chat=types.SimpleNamespace(id="c", parent_id=None))
    asked = {}

    @contextlib.contextmanager
    def started(*args, seen=True, **kwargs):
        asked["seen"] = seen
        yield None
    with patch.object(product, "started", started):
        assert measure.serve_check(tree, run, spec()) == ""
    assert asked == {"seen": False}   # only the measurement itself is shown
    job, looked = Job(), {}
    answers = iter([None, 200])
    with patch.dict(sys.modules, desktop_session=session(tmp_path, job, looked)), \
            patch.object(product, "answers", lambda url: next(answers)), \
            patch.object(product, "vouched", lambda pids, url, status=False: True):
        with product.running(spec(), {"app": program(tree), "inputs": []}, tree, {}, measure.Halted(),
                             tmp_path / "h.log", {}, "auto", seen=False):
            pass
    assert looked["owned"]   # a desktop of its own


def test_the_programs_output_reaches_the_disk_only_masked(at_measure, tmp_path):  # noqa: F811 — the imported fixture
    tree, _ = at_measure
    s, plan, seen = spec(), {"app": program(tree), "inputs": []}, {"log": f"printed {KEY} by mistake"}
    answers = iter([None, 200])
    with patch.dict(sys.modules, desktop_session=session(tmp_path, Job(), seen)), \
            patch.object(product, "answers", lambda url: next(answers)), \
            patch.object(product, "vouched", lambda pids, url, status=False: True):
        with product.running(s, plan, tree, {}, measure.Halted(), tmp_path / "k.log", {KEY: "[M2]"}, "auto"):
            pass
    assert not any(seen["evidence"].iterdir())   # never a raw log a failed cleanup could leave behind
    assert (tmp_path / "k.log").read_text(encoding="utf-8") == "printed [M2] by mistake"


def test_a_launch_setting_wins_over_the_runtime_default_and_no_input_takes_its_name(at_measure):  # noqa: F811 — the imported fixture
    tree, _ = at_measure
    s = spec()
    plan = {"inputs": [{"env": "BUDGET_URL"}]}
    env = {"USERPROFILE": "C:/Users/owner", "BUDGET_URL": "https://example.test/live"}
    got = product.environment(s, plan, {"USERPROFILE": "D:/run/data"}, env, "auto")
    assert got["USERPROFILE"] == "D:/run/data" and got["BUDGET_URL"] == "https://example.test/live"
    measured = json.loads((tree / ".wiki/measure.json").read_text(encoding="utf-8"))
    served = {k: v for k, v in measured.items() if k != "serve"}
    app = program(tree)
    s["oneshot"]["intent"] = {"kinds": ["desktop"]}
    with patch.object(measure.oneshot_build, "tools", lambda o: None), \
            pytest.raises(ValueError, match="입력값이나 비밀값"):
        measure.checked({**served, "app": {**app, "run": {**app["run"], "env": {"BUDGET_URL": "{data}"}}}}, s, tree)


def test_a_stop_accepted_before_the_outcome_is_saved_holds(with_person):
    given()
    before = len(o().get("measures") or [])
    measure.HALTS["budget"] = measure.Halted()
    measure.stop("budget")
    record = {"n": before + 1, "head": o()["built_head"], "ts": 1, "folder": "m", "mode": "auto", "status": "passed"}
    try:
        assert measure.decided("budget", oneshot_spec.store("budget"), record,
                               measure.oneshot_needs.CHANGES.get("budget", 0)) is None
    finally:
        measure.HALTS.pop("budget", None)   # one process's tests share it: the next run starts unstopped
    assert len(o().get("measures") or []) == before and o()["stopped"]["reason"] == "measure"


def test_a_value_the_server_holds_under_a_credentials_name_is_masked(at_measure):  # noqa: F811 — the imported fixture
    tree, _ = at_measure
    given()
    plan = measure.planned(tree, spec(), "HEAD")
    with patch.dict(os.environ, {"UNRELATED_API_TOKEN": "tok-unrelated-123456"}):
        env, hide = measure.env_of(spec(), plan)
    assert "tok-unrelated-123456" not in measure.masked(f"env: {env.get('UNRELATED_API_TOKEN')}", hide)


def test_the_measurement_mode_is_never_a_design_secrets_name(at_measure):  # noqa: F811 — the imported fixture
    tree, _ = at_measure
    plan = json.loads((tree / ".wiki/measure.json").read_text(encoding="utf-8"))
    assert measure.oneshot_needs.STARTED.fullmatch("ONESHOT_MEASURE")   # refused at R1 for a new design
    with patch.object(measure.oneshot_needs, "secret_needs", lambda s: [("N9", {"env": "ONESHOT_MEASURE", "kind": "secret"})]), \
            pytest.raises(ValueError, match="ONESHOT_MEASURE"):   # a design saved before: its plan names the clash
        measure.checked(plan, spec(), tree)


def drained(captured, part):
    """Wait until the reader has shown `part`."""
    deadline = time.monotonic() + 10
    while part not in captured.text() and time.monotonic() < deadline:
        time.sleep(0.01)


def test_a_value_the_program_prints_with_windows_line_endings_is_masked():
    secret = "first-line-of-it\nsecond-line-of-it"
    captured = product.Captured({secret: "[M3]"})
    captured.writer.write(secret.replace("\n", "\r\n").encode() + b" done\r\n")
    assert captured.text(5) == "[M3] done\n"


def test_no_piece_of_a_value_survives_the_cut_that_trims_the_output():
    secret = "AAAA-first-line\nBBBB-second-line"
    for offset in range(0, 260, 11):
        with patch.object(product.Captured, "LIMIT", 64):
            captured = product.Captured({secret: "[M4]"})
            data = ("x" * offset + secret + "y" * 300).encode()
            for i in range(0, len(data), 7):   # chunks the cut can fall between
                captured.writer.write(data[i:i + 7])
            got = captured.text(5)
        assert not any(piece in got for piece in ("AAAA", "BBBB", "first-line", "second-line")), (offset, got)


def test_a_look_while_the_program_still_writes_leaves_out_a_half_written_value():
    secret = "CCCC-half-written-value"
    captured = product.Captured({secret: "[M5]"})
    captured.writer.write(b"started " + secret[:12].encode())
    drained(captured, "started")
    assert "CCCC" not in captured.text()   # the rest may still come
    captured.writer.write(secret[12:].encode() + b" ok")
    assert captured.text(5) == "started [M5] ok"


def test_a_value_the_program_was_cut_off_writing_is_never_shown():
    secret = "first-line-of-it\nsecond-line-of-it"
    captured = product.Captured({secret: "[M6]"})
    captured.writer.write(b"ready\nfirst-line-of-it\n")   # the job ends here
    assert captured.text(5) == "ready\n"


def test_a_value_cut_off_inside_a_character_is_never_shown():
    secret = "üñ-value-é"
    captured = product.Captured({secret: "[M8]"})
    captured.writer.write(b"start " + secret.encode()[:-1])   # half of `é`, then the job ends
    assert captured.text(5) == "start "


def test_a_value_half_written_in_json_escapes_is_never_shown():
    secret = "DDDDvalue"
    captured = product.Captured({secret: "[M7]"})
    escaped = "".join(f"\\u{ord(c):04x}" for c in secret)
    captured.writer.write(f'{{"key": "{escaped[:30]}'.encode())
    drained(captured, '{"key"')
    assert "\\u0044" not in captured.text()   # five of its letters, each written as six characters
    captured.writer.write(f'{escaped[30:]}"}}'.encode())
    assert captured.text(5) == '{"key": "[M7]"}'
