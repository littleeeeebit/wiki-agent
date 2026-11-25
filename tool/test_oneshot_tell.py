"""The owner speaks to a run while it is built (`docs/plans/oneshot/14-owner-speaks-mid-build.md`)."""

import copy
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from main import oneshot, oneshot_build, oneshot_sheet as sheet, oneshot_spec, oneshot_tell, specs
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_spec import DESIGN, GLOSSARY, REQS, world  # noqa: F401 — `world` is a fixture


def o() -> dict:
    return specs.load("budget", oneshot.BRANCH)["oneshot"]


def at(stage: str, **more) -> None:
    for claim in oneshot_tell._claimed.values():   # the fixture's stand-in start runs no worker to take its hold
        claim["held"] and claim["held"]()
    oneshot_tell._claimed.clear()
    oneshot_spec.saved("budget", requirements=REQS, oneshot={
        "glossary": GLOSSARY, "stage": stage, "design": sheet.parsed(DESIGN), "design_head": "d" * 40, "working": None,
        **more})


def say(text: str) -> dict:
    return oneshot_tell.tell("budget", oneshot_tell.Tell(text=text))


def test_the_owners_words_wait_for_the_next_step_from_the_first_build_turn_to_r3(world):  # noqa: F811 — the imported fixture, requested by name
    for stage in ("design", "pr"):   # the design talk and R1 take the words before; the pull request is published after
        at(stage)
        with pytest.raises(HTTPException) as refused:
            say("모델을 바꿔")
        assert refused.value.status_code == 409
    at("build", working="build")   # a build turn the server holds, none attached here: the words wait
    say("  번역 모델을 더 큰 것으로 바꿔  ")
    assert [{k: w[k] for k in ("text", "stage")} for w in o()["tell_pending"]] == [
        {"text": "번역 모델을 더 큰 것으로 바꿔", "stage": "build"}]
    assert o()["told"][0]["into_turn"] is False and o()["working"] == "build"   # nothing else started while it runs
    store = oneshot_spec.store("budget")
    assert not oneshot_tell.diverted(store, "retell")
    assert oneshot.diverted(store, "measure") and o()["working"] == "retell"   # the next step turns aside first
    at("board", tell_pending=[{"text": "더 크게", "stage": "r2"}])   # said while the screens are designed: they wait
    assert not oneshot.diverted(store, "frontend")
    at("split", tell_pending=[{"text": "더 크게", "stage": "review"}])   # said in the backend's review: taken in first
    assert oneshot.diverted(store, "split") and o()["working"] == "retell"
    at("pr", tell_pending=[{"text": "더 크게", "stage": "r3"}])   # said at R3: never published past
    assert oneshot.diverted(store, "pr")
    at("measure")
    say("소리를 줄여")   # an idle run takes them in at once
    assert o()["working"] == "retell"
    at("measure")
    with patch.object(oneshot_spec, "start", side_effect=HTTPException(503, "서버가 종료 중이다")):
        say("소리를 줄여")   # a start refused stops the run with its reason: no `working` is left without a worker
    assert o()["working"] is None and o()["stopped"]["reason"] == "block"
    at("build", working="retell", stopped=None, tell_pending=[{"text": "더 크게", "stage": "build"}])
    with patch.object(oneshot_spec, "start", side_effect=HTTPException(409, "이미 돌고 있다")):
        assert oneshot.diverted(store, "build")   # a turn's handoff meets the rewrite running: it is left alone
    assert o()["working"] == "retell" and not o().get("stopped")


def retold(made, reqs, stage="build", tests_head="t" * 40, during=None, committed="h" * 40, pending=None, **more):
    at(stage, tests_head=tests_head, tell_pending=pending or [{"text": "번역 모델을 바꿔", "stage": stage}], attempts=3,
       working="retell", **more)
    sent, recorded, asked = [], [], []

    def turned(store, role, prompt, text, *a, **k):
        asked.append({**oneshot.role_config(role, store), "text": text})
        if during:
            say(during)
        return made, reqs, GLOSSARY, []
    with patch.object(oneshot, "turned", turned), patch.object(oneshot_spec, "design_text", lambda spec: ""), \
         patch.object(oneshot_spec, "design_committed",
                      lambda repo, message, spec: recorded.append(spec["requirements"]) or committed), \
         patch.object(oneshot_build, "send", lambda *a, held: sent.append(a) or held()), \
         patch.object(oneshot_build, "tests_text", lambda spec: "TESTS"), \
         patch.object(oneshot_build, "build_text", lambda spec: "BUILD"):
        oneshot_tell.retell("budget")
    return sent, recorded, asked


def changed_reqs() -> list:
    reqs = copy.deepcopy(REQS)
    reqs[0]["text"] += " 번역은 더 큰 모델로 한다."
    return reqs


def test_a_retold_design_is_committed_then_recorded_and_built_on_from_the_code_there_is(world):  # noqa: F811 — the imported fixture, requested by name
    sent, recorded, asked = retold(sheet.parsed(DESIGN), changed_reqs(), during="그리고 소리도 줄여",
                                   design_author={"model": "author-model", "effort": "high"})
    assert asked[0]["model"] == "author-model"   # the model that wrote the design rewrites it
    assert recorded[0][0]["text"].endswith("더 큰 모델로 한다.") and sent == [("budget", "tests", "TESTS")]
    assert o()["stage"] == "tests" and o()["retell_to"] == "build" and o()["design_head"] == "h" * 40
    assert o()["attempts"] == 0 and o()["retold"][0]["changed"] and o()["retold"][0]["words"] == ["번역 모델을 바꿔"]
    assert [w["text"] for w in o()["tell_pending"]] == ["그리고 소리도 줄여"]   # said during the turn: next
    assert specs.load("budget", oneshot.BRANCH)["requirements"][0]["text"].endswith("더 큰 모델로 한다.")


def test_a_design_that_could_not_be_committed_leaves_the_record_as_the_files_are(world):  # noqa: F811 — the imported fixture, requested by name
    sent, recorded, _ = retold(sheet.parsed(DESIGN), changed_reqs(), committed=None)
    assert recorded and not sent and specs.load("budget", oneshot.BRANCH)["requirements"] == REQS
    assert o()["design_head"] == "d" * 40 and o()["tell_pending"] and not o().get("retold")   # the words still wait
    real = oneshot_spec.saved

    def cut(repo, **fields):   # the one save after the commit fails: nothing of the rewrite is recorded
        if "retold" in (fields.get("oneshot") or {}):
            raise OSError("disk full")
        return real(repo, **fields)
    with patch.object(oneshot_spec, "saved", cut), pytest.raises(OSError):
        retold(sheet.parsed(DESIGN), changed_reqs())
    assert specs.load("budget", oneshot.BRANCH)["requirements"] == REQS and o()["design_head"] == "d" * 40


def test_a_design_commit_retried_after_it_landed_returns_head_past_an_untracked_app_file(tmp_path):
    import subprocess
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, text=True, check=True)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "t")
    (tmp_path / "core.md").write_text("design\n", encoding="utf-8")
    run("add", "core.md")
    run("commit", "-qm", "design")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")   # the build's own file, left untracked
    stopped = MagicMock()
    with patch.object(oneshot_spec, "store", lambda repo: stopped):
        assert oneshot_spec.commit("budget", tmp_path, ["core.md"], "again") == run("rev-parse", "HEAD").stdout.strip()
    assert not stopped.stop.called


def test_an_answer_given_as_the_asking_turn_closes_goes_in_once_it_lets_go_and_only_an_asked_stop_does(world):  # noqa: F811 — the imported fixture, requested by name
    store = oneshot_spec.store("budget")
    asked = {"reason": "asked", "detail": "묻는다", "state": "build"}
    at("build", stopped=asked, inflight={"turn": "t"}, tell_pending=[{"text": "세금을 더해", "stage": "build"}])
    store.put(inflight=None)
    oneshot_tell.answered(store)   # words said before the question never answer it
    assert o()["working"] is None and o()["stopped"]["reason"] == "asked"
    store.put(inflight={"turn": "t"})   # the question's turn has not let go yet
    say("세금 포함")
    assert o()["working"] is None and o()["tell_pending"]   # busy: only queued
    store.put(inflight=None)
    oneshot_tell.answered(store)   # `Turn.finish`, once its mark is gone
    assert o()["working"] == "retell"
    at("build", stopped={**asked, "reason": "block"}, tell_pending=o()["tell_pending"])
    oneshot_tell.answered(store)   # any other stop waits for `[지금 반영]`
    assert o()["working"] is None
    from main import work
    at("build", stopped=asked)
    tree = str(specs.load("budget", oneshot.BRANCH)["worktree"])
    with patch.dict(work._busy, {tree: object()}):   # a person's ordinary turn has the tree
        say("세금 포함")
        work.dispatch(Path(tree), MagicMock(halt=threading.Event()))   # it let go, and another took it at once
    assert o()["working"] is None   # the answer stays registered for that one's release
    queued = object()
    with patch.dict(work._queued, {tree: queued}), patch.object(work, "session", side_effect=AssertionError("queued")):
        work.dispatch(Path(tree), MagicMock(halt=threading.Event()))   # that one let go too
        assert o()["working"] == "retell" and work._queued[tree] is queued   # the rewrite goes first; the order waits
        work.dispatch(Path(tree), MagicMock(halt=threading.Event()))   # a late callback of an earlier release
        assert work._queued[tree] is queued   # the rewrite holds the tree from its start: nothing comes between
    dispatched = []
    with patch.object(work, "dispatch", lambda path, ended: dispatched.append(path)), \
         patch.object(oneshot_tell, "rewritten", lambda repo: ("build", "BUILD")), \
         patch.object(oneshot_build, "send", lambda *a, held: held()):   # a follow-up refused before any turn ran
        oneshot_tell.retell("budget")
    assert dispatched == [Path(tree)] and tree not in work._busy   # the order queued meanwhile still goes


def test_a_rewrite_claims_its_tree_from_its_start_to_its_follow_up_and_gives_it_back_on_any_failure(world):  # noqa: F811 — the imported fixture, requested by name
    from main import work
    store, tree = oneshot_spec.store("budget"), str(specs.load("budget", oneshot.BRANCH)["worktree"])
    at("measure")
    with patch.object(oneshot_spec, "start", side_effect=OSError("disk")):   # the `working` save fails
        oneshot_tell.start(store)
    assert tree not in work._busy and not oneshot_tell._claimed and o()["stopped"]["reason"] == "block"
    at("measure")
    oneshot_tell.start(store)
    assert tree in work._busy
    with patch.object(oneshot_spec, "spec_of", side_effect=OSError("disk")), pytest.raises(OSError):
        oneshot_tell.retell("budget")   # the worker's first read fails
    assert tree not in work._busy and not oneshot_tell._claimed
    at("build", working="retell")
    order = MagicMock()

    def send(repo: str, stage: str, text: str, held) -> None:   # the follow-up gets the rewrite's hold itself
        assert tree in work._busy   # a late callback, or a person's new turn, finds the tree held: none comes between
        work.dispatch(Path(tree), MagicMock(halt=threading.Event()))
        assert work._queued[tree] is order
        held()

    with patch.dict(work._queued, {tree: order}), patch.object(work, "session"), patch.object(work, "begin") as begin, \
         patch.object(oneshot_tell, "rewritten", lambda repo: ("build", "BUILD")), patch.object(oneshot_build, "send", send):
        oneshot_tell.retell("budget")
        assert begin.call_args[0][0] == Path(tree) and tree not in work._queued   # the order goes after it, once
    begin.call_args[0][3]()
    assert tree not in work._busy and not oneshot_tell._claimed
    at("build", tell_pending=[{"text": "더 크게", "stage": "build"}])   # said while the rewrite ran
    with patch.object(oneshot_tell, "rewritten", lambda repo: ("build", "BUILD")), \
         patch.object(oneshot_build, "resolve_tools", lambda *a: True), \
         patch.object(work, "session", side_effect=AssertionError("the old step went")):
        oneshot_tell.retell("budget")   # the real follow-up hands the tree on to the next rewrite
    assert o()["working"] == "retell" and oneshot_tell._claimed["budget"]["held"] and tree in work._busy
    with patch.object(oneshot_tell, "rewritten", lambda repo: None):
        oneshot_tell.retell("budget")   # the next rewrite takes over that claim and its hold
    assert tree not in work._busy and not oneshot_tell._claimed


def test_a_work_log_answer_carries_its_questions_to_the_design_model_within_their_own_bound(world):  # noqa: F811 — the imported fixture, requested by name
    at("build")

    def asked(chapter, answer):
        return {"header": chapter, "question": "이 부분은 어떤 모델을 쓸까?", "answer": answer, "picked": [1 if answer == "그대로" else 0],
                "options": [{"label": "새 모델", "note": "무손실 중간 파일을 거친다", "preview": "clip_start_ms"},
                            {"label": "그대로"}]}
    long = "가" * oneshot_tell.MAX_TELL   # the answer keeps its whole budget: the questions are bounded on their own
    steers = []
    turn = MagicMock(spec=oneshot_build.Turn, repo="budget", done=False, wake=MagicMock())
    turn.chat = MagicMock(steer=lambda text: steers.append(text) or True)
    with patch.object(oneshot_tell.work, "attached", lambda tree: turn):   # a build turn runs: it is told at once
        oneshot_tell.tell("budget", oneshot_tell.Tell(text=long, asked=[asked("재생", "새 모델"), asked("녹음", "그대로")]))
    assert o()["tell_pending"][-1]["asked"][0]["header"] == "재생" and "이 부분은" in steers[0] and "재생" in steers[0]
    # which option was chosen, by its place: labels can collide, a joined answer can read as two
    assert [[x["chosen"] for x in a["options"]] for a in o()["tell_pending"][-1]["asked"]] == [[True, False], [False, True]]
    with pytest.raises(HTTPException) as refused:
        oneshot_tell.tell("budget", oneshot_tell.Tell(text="네", asked=[asked("x" * oneshot_tell.MAX_ASKED, "네")]))
    assert refused.value.status_code == 400 and len(o()["tell_pending"]) == 1
    _, _, prompts = retold(sheet.parsed(DESIGN), REQS, pending=o()["tell_pending"])
    assert all(s in prompts[0]["text"] for s in ("재생", "무손실 중간 파일을 거친다", "clip_start_ms"))
    built = oneshot_tell.told(o(), "")   # every build and tests turn after reads the answer with its questions
    assert "이 부분은 어떤 모델을 쓸까?" in built and "clip_start_ms" in built


def test_words_said_while_the_screens_are_designed_wait_out_a_backend_rewrite(world):  # noqa: F811 — the imported fixture, requested by name
    sent, _, asked = retold(sheet.parsed(DESIGN), REQS, stage="split", pending=[
        {"text": "검토 때 한 말", "stage": "review"}, {"text": "화면 이야기", "stage": "split"}])
    assert "검토 때 한 말" in asked[0]["text"] and "화면 이야기" not in asked[0]["text"]
    assert [w["text"] for w in o()["tell_pending"]] == ["화면 이야기"] and sent[0][:2] == ("budget", "build")
    store = oneshot_spec.store("budget")
    assert not oneshot.diverted(store, "build")   # across the backend's rebuild it still waits
    at("build-front", tell_pending=o()["tell_pending"])
    assert oneshot.diverted(store, "build-front") and o()["working"] == "retell"   # the frontend build takes it


def test_a_handoff_a_rewrite_took_yields_and_a_turn_holding_the_tree_is_not_idle(world):  # noqa: F811 — the imported fixture, requested by name
    from main import work
    store = oneshot_spec.store("budget")
    at("build", working="retell")   # the rewrite has taken the words already: the old turn's handoff yields
    assert oneshot.diverted(store, "build") and o()["working"] == "retell"
    at("build")   # the tests turn saved its result but still holds the worktree
    tree = specs.load("budget", oneshot.BRANCH)["worktree"]
    with patch.dict(work._busy, {str(tree): object()}):
        say("소리를 줄여")
    assert o()["working"] is None and o()["tell_pending"]   # its handoff meets the words
    assert oneshot.diverted(store, "build") and o()["working"] == "retell"


def test_a_published_draft_takes_the_owners_words_before_the_old_measurement_plan(world):  # noqa: F811 — the imported fixture, requested by name
    from main import oneshot_measure, oneshot_publish
    at("measure", working="publish", built_head="b" * 40, tell_pending=[{"text": "입력 하나를 빼", "stage": "measure"}])
    with patch.object(oneshot_publish, "published", lambda repo: {"number": 1}), \
         patch.object(oneshot_measure, "measuring", side_effect=AssertionError("the old plan was checked first")):
        oneshot_publish.publish("budget")   # the draft's handoff reaches the measurement's start, which asks first
    assert o()["working"] == "retell"


def test_words_a_parked_run_holds_wait_for_the_owners_apply_and_a_hard_stop_is_never_cleared(world):  # noqa: F811 — the imported fixture, requested by name
    from main import work
    at("measure")
    tree = specs.load("budget", oneshot.BRANCH)["worktree"]
    with patch.dict(work._busy, {str(tree): object()}):   # the owner's measurement still holds the tree
        say("소리를 줄여")
        with pytest.raises(HTTPException):
            oneshot_tell.apply("budget")
    assert o()["working"] is None and o()["tell_pending"]   # it let go: the run is parked with the words shown
    oneshot_tell.apply("budget")   # `[지금 반영]`
    assert o()["working"] == "retell"
    at("review", stopped={"reason": "fake", "detail": "고정된 테스트가 가짜다", "state": "review"},
       tell_pending=[{"text": "소리를 줄여", "stage": "review"}])
    say("소리를 더 줄여")
    with pytest.raises(HTTPException) as refused:
        oneshot_tell.apply("budget")
    assert "가짜" in refused.value.detail and o()["stopped"]["reason"] == "fake" and o()["working"] is None


def test_only_a_parked_run_takes_the_words_in_at_once_and_a_stale_measure_start_never_diverts(world):  # noqa: F811 — the imported fixture, requested by name
    from main import oneshot_measure
    at("build")   # between a tests turn and its build: the build's start meets the words, never a rival rewrite
    say("소리를 줄여")
    assert o()["working"] is None and o()["tell_pending"]
    at("build", working="build", tell_pending=o()["tell_pending"])   # an earlier screen's start, a build running
    with pytest.raises(HTTPException) as refused:
        oneshot_measure.start("budget", oneshot_measure.Start(head="b" * 40))
    assert refused.value.status_code == 409 and o()["working"] == "build"
    at("build", stopped={"reason": "block", "detail": "x", "state": "build"})   # a stopped run is parked
    say("소리를 줄여")
    assert o()["working"] == "retell"


def test_a_send_that_holds_the_tree_takes_words_said_by_then_first_and_a_rewrite_never_shares_the_tree(world):  # noqa: F811 — the imported fixture, requested by name
    from main import work
    at("build")
    started = []

    def tools(*a):   # the owner speaks after the send's first check, before it holds the tree: queued only
        say("소리를 줄여")
        return True
    with patch.object(oneshot_build, "resolve_tools", tools), \
         patch.object(oneshot_spec, "start", lambda repo, name: started.append(name)), \
         patch.object(work, "session", side_effect=AssertionError("the old prompt went")):
        oneshot_build.send("budget", "build", "BUILD")
    tree = specs.load("budget", oneshot.BRANCH)["worktree"]
    # the send let the tree go; the rewrite holds it from its start, so nothing comes between
    assert started == ["retell"] and list(oneshot_tell._claimed) == ["budget"]
    at("build", working="retell", tell_pending=[{"text": "소리를 줄여", "stage": "build"}])
    with patch.dict(work._busy, {str(tree): object()}), \
         patch.object(oneshot, "turned", side_effect=AssertionError("the design turn ran in a held tree")):
        oneshot_tell.retell("budget")   # another turn has the tree: the words stay, and the run stops parked
    assert o()["working"] is None and o()["tell_pending"] and o()["stopped"]["reason"] == "block"
    oneshot_tell.apply("budget")   # once that turn let go, `[지금 반영]` tries again
    assert o()["working"] == "retell"


def test_a_build_send_that_finds_a_rewrite_started_before_it_held_the_tree_yields(world):  # noqa: F811 — the imported fixture, requested by name
    from main import work
    at("build", working="retell")
    with patch.object(oneshot, "diverted", lambda store, what: False), \
         patch.object(oneshot_build, "resolve_tools", lambda *a: True), \
         patch.object(work, "session", side_effect=AssertionError("a second agent turn")):
        oneshot_build.send("budget", "build", "BUILD")
    tree = specs.load("budget", oneshot.BRANCH)["worktree"]
    assert o()["working"] == "retell" and not o().get("stopped") and str(tree) not in work._busy


def test_a_measurement_the_owner_spoke_into_hands_on_only_once_the_tree_is_let_go(world):  # noqa: F811 — the imported fixture, requested by name
    from main import oneshot_measure
    at("measure", working="measure", tell_pending=[{"text": "소리를 줄여", "stage": "measure"}])
    halt = oneshot_measure.Halted()
    halt.told = True
    halt.set()
    started = []
    with patch.dict(oneshot_measure.HALTS, {"budget": halt}), \
         patch.object(oneshot_spec, "start", lambda repo, name: started.append(name) or oneshot_spec.saved(
             repo, oneshot={"working": name})):
        handoff = oneshot_measure.decided("budget", oneshot_spec.store("budget"), {}, 0)
        assert callable(handoff) and not started   # `holding` still holds the tree here
        assert handoff() is True and started == ["retell"]


def test_a_result_waiting_for_the_owners_verdict_is_set_aside_by_a_rewrite(world):  # noqa: F811 — the imported fixture, requested by name
    retold(sheet.parsed(DESIGN), REQS, stage="measure", measures=[{"n": 1, "head": "h", "mode": "owner", "status": "owner"}])
    assert o()["measures"][-1]["status"] == "stale" and "다시 잰다" in o()["measures"][-1]["error"]


def test_words_that_change_no_item_go_straight_to_the_build_and_a_late_run_to_the_frontend_build(world):  # noqa: F811 — the imported fixture, requested by name
    sent, recorded, _ = retold(sheet.parsed(DESIGN), REQS)
    assert not recorded and sent == [("budget", "build", "BUILD")] and o()["retold"][0]["changed"] == []
    with patch("main.oneshot_front_build.front_text", lambda spec: "FRONT"), \
         patch("main.oneshot_review.later_text", lambda o, kinds: ""):
        sent, _, _ = retold(sheet.parsed(DESIGN), REQS, stage="pc")
    assert sent[0][:2] == ("budget", "build-front") and sent[0][2].startswith("FRONT")
    assert "번역 모델을 바꿔" in sent[0][2] and sent[0][2].count(oneshot_tell.OWNER) == 1   # the words, told once
    sent, _, _ = retold(sheet.parsed(DESIGN), REQS, stage="tests", retell_to="build-front")
    assert sent[0][:2] == ("budget", "tests") and o()["retell_to"] == "build-front"   # the frontend's, kept


def test_a_retold_tests_turn_may_find_a_planned_test_passing_but_never_skipped(world):  # noqa: F811 — the imported fixture, requested by name
    at("tests")
    spec = specs.load("budget", oneshot.BRANCH)
    found = [("test_budget.py", t["test_name"], "", "passed") for t in oneshot_build.planned(spec)]
    assert oneshot_build.red(spec, found) and not oneshot_build.red(spec, found, again=True)
    skipped = [(*found[0][:3], "skipped"), *found[1:]]
    assert "건너뛴" in oneshot_build.red(spec, skipped, again=True)
