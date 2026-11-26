"""The owner speaks to a run while it is built (`docs/plans/oneshot/14-owner-speaks-mid-build.md`)."""

import copy
import os
import subprocess
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
    tree = str(specs.load("budget", oneshot.BRANCH)["worktree"])
    oneshot_build.Turn.alive.discard(LIVE[0])
    if token := (more.get("inflight") or {}).get("turn"):   # a turn in flight is one still ending in the tree: its
        oneshot_tell.work._runs[tree] = MagicMock(spec=oneshot_build.Turn, over=False, done=True, repo="budget",
                                                  wake=threading.Lock(), halt=threading.Event())   # run is done, its
        oneshot_build.Turn.alive.add(token)   # marks not cleared yet
        LIVE[0] = token
    else:
        oneshot_tell.work._runs.pop(tree, None)


LIVE = [""]   # the stand-in turn `at` keeps alive


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
    unreadable = MagicMock(get=MagicMock(side_effect=OSError("disk")))
    for store_of in (MagicMock(side_effect=OSError("disk")), MagicMock(return_value=unreadable)):
        held = oneshot_tell.hold(work._busy, oneshot_tell.work_lock, tree, "", kind="turn")
        with patch.object(oneshot_spec, "store", store_of), patch.object(oneshot_build, "resolve_tools", lambda *a: True), \
             pytest.raises(OSError):   # the run folder, or the run under the lock, cannot be read
            oneshot_build.send("budget", "build", "BUILD", held=held)
        assert tree not in work._busy   # the hold handed on goes with it


def test_a_choice_answers_only_the_turn_that_asked_and_the_words_are_kept_before_the_turn_hears_them(world):  # noqa: F811 — the imported fixture, requested by name
    choice = {"question": "세금은?", "answer": "포함", "picked": [0], "options": [{"label": "포함"}, {"label": "제외"}]}
    for busy in ({}, {"inflight": {"turn": "x"}}):   # idle and parked, or the tree still held
        at("build", stopped={"reason": "asked", "detail": "묻는다", "state": "build", "turn": "t1"}, **busy)
        with pytest.raises(HTTPException) as refused:   # an earlier turn's question, from a window left open
            oneshot_tell.tell("budget", oneshot_tell.Tell(text="포함", asked=[choice], turn="t0"))
        assert refused.value.status_code == 409 and not o().get("tell_pending") and o()["stopped"]["reason"] == "asked"
    oneshot_tell.tell("budget", oneshot_tell.Tell(text="포함", asked=[choice], turn="t1"))   # the one that asked
    assert o()["tell_pending"][-1]["answers"] == "t1" and o()["stopped"]["reason"] == "asked"   # waits for the tree
    with pytest.raises(HTTPException) as twice:   # a second choice for it meanwhile, from either window
        oneshot_tell.tell("budget", oneshot_tell.Tell(text="제외", asked=[choice], turn="t1"))
    assert twice.value.status_code == 409 and len(o()["tell_pending"]) == 1
    oneshot_spec.saved("budget", oneshot={"stopped": None, "working": "retell"})   # answered: the rewrite took it
    with pytest.raises(HTTPException) as again:   # the same form sent twice, or from a second window
        oneshot_tell.tell("budget", oneshot_tell.Tell(text="포함", asked=[choice], turn="t1"))
    assert again.value.status_code == 409 and len(o()["tell_pending"]) == 1
    oneshot_spec.saved("budget", oneshot={"stopped": {"reason": "asked", "detail": "묻는다", "state": "build",
                                                      "turn": "t1"}, "working": None})
    heard = []
    with patch.object(oneshot_tell, "steered", lambda *a: heard.append(a) or True), \
         patch.object(oneshot_spec, "saved", side_effect=OSError("disk")), pytest.raises(OSError):
        say("모델을 바꿔")   # not kept for the rewrite: the running turn never hears it either
    assert not heard
    with patch.object(oneshot_tell, "steered", lambda *a: True):
        say("모델을 바꿔")
    assert o()["told"][-1]["into_turn"] is True and o()["tell_pending"][-1]["text"] == "모델을 바꿔"
    real, saves = oneshot_spec.saved, []

    def mark_fails(repo: str, **fields):   # the words are kept; only the mark that the turn heard them fails
        saves.append(fields)
        if len(saves) > 1:
            raise OSError("disk")
        return real(repo, **fields)
    with patch.object(oneshot_tell, "steered", lambda *a: True), patch.object(oneshot_spec, "saved", mark_fails):
        say("소리를 줄여")   # accepted: no error the window would answer with a second copy
    assert [w["text"] for w in o()["tell_pending"]].count("소리를 줄여") == 1


def test_a_work_log_answer_carries_its_questions_to_the_design_model_within_their_own_bound(world):  # noqa: F811 — the imported fixture, requested by name
    at("build", stopped={"reason": "asked", "detail": "묻는다", "state": "build", "turn": "t1"}, inflight={"turn": "x"})

    def asked(chapter, answer):
        return {"header": chapter, "question": "이 부분은 어떤 모델을 쓸까?", "answer": answer, "picked": [1 if answer == "그대로" else 0],
                "options": [{"label": "새 모델", "note": "무손실 중간 파일을 거친다", "preview": "clip_start_ms"},
                            {"label": "그대로"}]}
    long = "가" * oneshot_tell.MAX_TELL   # the answer keeps its whole budget: the questions are bounded on their own
    steers = []
    turn = MagicMock(spec=oneshot_build.Turn, repo="budget", done=False, wake=MagicMock())
    turn.chat = MagicMock(steer=lambda text: steers.append(text) or True)
    with patch.object(oneshot_tell.work, "attached", lambda tree: turn):   # a build turn runs: it is told at once
        oneshot_tell.tell("budget", oneshot_tell.Tell(text=long, asked=[asked("재생", "새 모델"), asked("녹음", "그대로")],
                                                      turn="t1"))
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
    words = o()["tell_pending"]   # a measurement repair stopped with words said while it ran: only `[재개]` sends it on
    at("measure", fix="measure", built_head="c" * 40, tell_pending=words,
       stopped={"reason": "block", "detail": "x", "state": "measure"})
    with pytest.raises(HTTPException) as refused:
        oneshot_measure.start("budget", oneshot_measure.Start(head="c" * 40))
    assert refused.value.status_code == 409 and "[재개]" in refused.value.detail
    assert o()["working"] is None and o()["tell_pending"] == words and o()["fix"] == "measure"
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


def test_a_grant_that_cannot_be_cleared_never_keeps_the_tree_and_opens_nothing_once_its_turn_is_gone(world):  # noqa: F811 — the imported fixture, requested by name
    import guard_grant
    from main import work
    at("build")
    tree, real, broken = str(specs.load("budget", oneshot.BRANCH)["worktree"]), oneshot_spec.saved, [True]

    def saved(repo: str, **fields):
        if fields.get("adapter") is False and broken[0]:
            raise OSError("disk")
        return real(repo, **fields)

    def granted() -> bool:
        return bool(specs.load("budget", oneshot.BRANCH).get("adapter"))

    def live() -> bool:   # what the guard opens to whatever turn runs there now
        return guard_grant.live(specs.load("budget", oneshot.BRANCH))

    with patch.dict(oneshot_build.GRANTS, {"build": lambda spec: True}), patch.object(oneshot_spec, "saved", saved), \
         patch.object(oneshot_build, "resolve_tools", lambda *a: True), patch.object(work, "session"):
        with patch.object(work, "begin") as begin:
            oneshot_build.send("budget", "build", "BUILD")
        turn = begin.call_args[0][4]
        assert live()   # the turn's own grant, while it is the run's turn
        oneshot_build.admitted(Path(tree), turn)   # never refused to itself
        begin.call_args[0][3]()   # the turn ends, and clearing its grant fails: the tree still goes
        assert tree not in work._busy and live()   # until its end clears its mark, no other turn starts there
        order = MagicMock()
        with patch.dict(work._runs, {tree: turn}):   # as `work.begin` registers it
            with pytest.raises(HTTPException) as refused:   # still ending: known in memory, nothing read
                work.begin(Path(tree), MagicMock(), "다른 일", lambda: None, work.Run(MagicMock()))
            assert refused.value.status_code == 409
            with patch.dict(work._queued, {tree: order}):
                work.dispatch(Path(tree), MagicMock(halt=threading.Event()))
                assert work._queued[tree] is order   # a queued order waits for that end, which dispatches it
            turn.finish()   # its end clears its mark, and its heartbeat stops
            assert tree not in work._busy and granted() and not live() and turn.over   # opening nothing to any turn
            oneshot_build.admitted(Path(tree), work.Run(MagicMock()))   # any turn may start again
            # Its end failed to clear the mark too: the grant opens on, and its turn is gone.
            stale = {**(o().get("inflight") or {}), "role": "build", "codex": False, "turn": turn.token}
            real("budget", oneshot={"inflight": stale, "working": None})
            spent = o().get("spend")
            assert live()
            with pytest.raises(HTTPException):   # clearing fails: refused, never opened to another turn
                oneshot_build.admitted(Path(tree), work.Run(MagicMock()))
            assert live()
            broken[0] = False   # the next turn to come clears both, in one save: no restart needed
            oneshot_build.admitted(Path(tree), work.Run(MagicMock()))
            assert not granted() and not live() and not o().get("inflight") and o().get("spend") != spent
            real("budget", adapter=turn.token, oneshot={"inflight": stale})
            for broken_read in (patch.object(oneshot_build.json, "loads", side_effect=ValueError("mid-write")),
                                patch.object(oneshot_build.os, "scandir", side_effect=PermissionError("listing"))):
                with broken_read, pytest.raises(HTTPException):   # a record or folder that cannot be read
                    oneshot_build.admitted(Path(tree), work.Run(MagicMock()))   # never opens the tree
                assert live()
            oneshot_spec.store("budget").stop("asked", "묻는다", turn="t1")
            oneshot_build.unstuck("budget")   # `[재개]` and the owner's answer clear them too, holding the tree
            assert not live() and not o().get("inflight") and tree not in work._busy
            # A build turn that ended on an error, its end's save failing too: its stage is still `working`.
            real("budget", adapter=turn.token, oneshot={"inflight": stale, "working": "build", "stopped": None})
            oneshot_build.admitted(Path(tree), work.Run(MagicMock()))
            assert not live() and not o().get("inflight") and o()["working"] is None
            assert o()["stopped"]["reason"] == "block"   # a stop `[재개]` takes on
            with patch.object(work, "dispatch") as dispatched, patch.object(oneshot_build, "hold") as taken:
                oneshot_build.unstuck("budget")   # nothing left to clear: the tree stays free for the run's follow-up
            assert not taken.called and not dispatched.called
            real("budget", adapter=turn.token, oneshot={"inflight": stale, "working": None})
            with patch.object(oneshot_build, "hold") as taken:   # only that ended turn's own marks, with no hold
                oneshot_build.unstuck("budget")
            assert not taken.called and not o().get("inflight") and not live()
            fresh = {**stale, "turn": "next"}   # a newer turn's marks, its turn alive (starting, running or closing)
            real("budget", adapter="next", oneshot={"inflight": fresh, "working": None})
            spent = o().get("spend")
            with patch.object(oneshot_build.Turn, "alive", {"next"}), patch.dict(work._runs):
                work._runs.pop(tree)   # its conversation reset meanwhile: it still charges itself, once
                oneshot_build.unstuck("budget")
                oneshot_build.admitted(Path(tree), work.Run(MagicMock()))   # admission revokes the grant only
            assert o()["inflight"]["turn"] == "next" and not live() and o().get("spend") == spent
            with patch.dict(work._runs), patch.object(oneshot_build, "hold") as taken:
                work._runs.pop(tree)   # its end has run (or it never had one), whatever the registry says
                oneshot_build.unstuck("budget")
            assert not o().get("inflight") and not taken.called and tree not in work._busy
            real("budget", adapter=False, oneshot={"inflight": None})
            stopped = MagicMock(spec=work.Run, halt=threading.Event())
            stopped.halt.set()   # the tree's latest turn was stopped with an order waiting on it
            with patch.dict(work._runs, {tree: stopped}), patch.dict(work._queued, {tree: order}), \
                 patch.object(work, "begin") as started:
                work.dispatch(Path(tree), MagicMock(halt=threading.Event()))   # an older turn's dispatcher, late
                assert tree not in work._queued and not started.called   # the stop drops it, whoever dispatches
            # The release hooks looked before a newer build turn let the tree go, and that turn is still ending:
            # read again as the order is taken, under the queue's lock, so the order is never taken to be refused.
            still = MagicMock(spec=oneshot_build.Turn, over=False, done=True, halt=threading.Event())
            with patch.dict(work._runs, {tree: still}), patch.dict(work._queued, {tree: order}), \
                 patch.object(work, "RELEASED", []), patch.object(work, "begin") as started:
                work.dispatch(Path(tree), MagicMock(halt=threading.Event()))
                assert work._queued[tree] is order and tree not in work._busy and not started.called
            broken[0] = True
        with patch.object(work, "begin", side_effect=RuntimeError("refused")):
            oneshot_build.send("budget", "build", "BUILD")   # refused at launch, its clearing failing too
        assert tree not in work._busy and not o().get("inflight") and o()["stopped"]["reason"] == "block"
        assert granted() and not live()
        broken[0] = False
        with patch.object(work, "begin") as begin:
            oneshot_build.send("budget", "tests", "TESTS")   # a turn with no grant: the one left is cleared first
        assert not granted()
        begin.call_args[0][3]()
        begin.call_args[0][4].finish()
    assert tree not in work._busy
    # a survey's grant opens the adapter to every turn of its spec; a malformed record opens nothing
    assert guard_grant.live({"adapter": True}) and not guard_grant.live({"adapter": "t", "oneshot": []})


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


def test_a_record_that_holds_nothing_refuses_only_its_own_tree(tmp_path):
    """realtime-screen-translator: a power loss zeroed one repository's record, and every turn anywhere was
    refused as a record that could not be read. Its own tree stays closed (its turn would carry no secrets'
    mask, PR #455 round 1); a record with content that does not parse, a trailing NUL included, still refuses."""
    tree = tmp_path / "tree"
    (tmp_path / "specs" / "ok").mkdir(parents=True)
    (tmp_path / "specs" / "ok" / f"{oneshot.BRANCH}.json").write_text(
        f'{{"worktree": "{tree.as_posix()}"}}', encoding="utf-8")
    zeroed = tmp_path / "specs" / "zeroed" / f"{oneshot.BRANCH}.json"
    zeroed.parent.mkdir()
    # Its own tree: the layout, in the volume's case rules (round 2), and a checkout of it on the oneshot branch.
    moved = tmp_path / "moved" / "zeroed"
    moved.mkdir(parents=True)
    for args in (["init", "-q", "-b", oneshot.BRANCH], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                                                         "--allow-empty", "-m", "x"]):
        subprocess.run(["git", *args], cwd=moved, check=True, capture_output=True)
    own = [tmp_path / "zeroed-worktrees" / oneshot.BRANCH, moved,
           *([tmp_path / "Zeroed-worktrees" / oneshot.BRANCH.upper()] if os.name == "nt" else [])]
    with patch.object(specs, "SPECS", tmp_path / "specs"):
        for nothing in (b"\0" * 4096, b"", b" \r\n"):
            zeroed.write_bytes(nothing)
            assert oneshot_build.record(tmp_path / "elsewhere") is None
            assert oneshot_build.record(tree)[0] == "ok"
            for mine in own:
                with pytest.raises(HTTPException) as refused:
                    oneshot_build.record(mine)
                assert refused.value.status_code == 409 and "zeroed" in refused.value.detail
        for broken in (b'{"worktree": ', b'{"worktree": "x"}\0'):
            zeroed.write_bytes(broken)
            with pytest.raises(ValueError):
                oneshot_build.record(tmp_path / "elsewhere")   # every record is read for a tree no run owns
