"""The implementer's first read, and the conflicts it hands back to the spec's author (`main.handoff`)."""

import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from main import handoff, specs
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_specs import made, repo as repository_fixture, spec_block

repo = repository_fixture


def block(name: str, value) -> str:
    return f"Stopping here.\n\n```{name}\n{json.dumps(value)}\n```\n"


ESCALATION = {"condition": "architecture change", "conflict": "The queue cannot commit inside the open transaction",
              "where": ["a.txt:1"], "question": "Where does the queue commit?"}


class Run:
    def __init__(self, person=False):
        self.events, self.chat, self.turn, self.person = [], SimpleNamespace(id="c", parent_id=None), "t", person
        self.halt = threading.Event()

    def put(self, event):
        self.events.append(event)


@pytest.fixture
def task(repo):
    sid = made(repo, spec_block(escalate=["The queue needs its own transaction"]))[0]["id"]
    specs.update("proj", sid, worktree=str(repo), author={"model": "opus", "effort": "high"})
    # The card owns the checkout as a started task's worktree would.
    with patch.object(specs, "owner", side_effect=lambda path: specs.load("proj", sid)):
        yield repo, sid


def test_checked_wants_a_task_and_ranked_documents_that_exist(repo):
    (repo / "docs").mkdir()
    (repo / "docs/backend.md").write_text("old\n" * 128 + "new\n" * 201, encoding="utf-8")
    good = {"task": {"kind": "fix", "what": " Fix  the reader "},
            "precedence": [{"ref": "docs/backend.md:129-329", "role": "current", "why": "in-app reader"},
                           {"ref": "docs/backend.md:1-128", "role": "legacy"}, {"ref": "docs/", "role": "reference"}],
            "escalate": [handoff.DEFAULT_ESCALATE[0], "Own transaction"]}
    shown = handoff.checked(repo, good)
    assert shown["task"] == {"kind": "fix", "what": "Fix the reader"}
    assert shown["escalate"] == ["Own transaction"], "the standing conditions are not stored twice"
    for bad, why in [({**good, "task": {"kind": "rewrite", "what": "x"}}, "`task`"),
                     ({**good, "precedence": []}, "`precedence`"),
                     ({**good, "precedence": [{"ref": "docs/backend.md:300-400", "role": "current"}]}, "줄 범위"),
                     ({**good, "precedence": [{"ref": "gone.md", "role": "current"}]}, "없다"),
                     ({**good, "precedence": [{"ref": "../x.md", "role": "current"}]}, "밖"),
                     ({**good, "precedence": [{"ref": "a.txt", "role": "main"}]}, "`role`")]:
        with pytest.raises(ValueError, match=why):
            handoff.checked(repo, bad)
    # Checked in another checkout (a survey's): only inside the repository is asked.
    assert handoff.checked(repo, {**good, "precedence": [{"ref": "gone.md", "role": "current"}]}, strict=False)


def test_text_puts_the_task_order_lengths_and_stop_conditions_first(repo):
    (repo / "b.md").write_text("x\n" * 329, encoding="utf-8")
    spec = {"task": {"kind": "fix", "what": "Fix the reader"}, "escalate": ["Own transaction"],
            "precedence": [{"ref": "b.md:180-329", "role": "current"}, {"ref": "b.md:1-128", "role": "legacy"}]}
    text = handoff.text(repo, spec)
    assert text.startswith("## Read this first\n\n**Task (fix):** Fix the reader")
    assert "1. `b.md` lines 180–329 (329 lines) — **current**" in text
    assert "2. `b.md` lines 1–128 (329 lines) — **legacy**" in text
    assert text.index(handoff.DEFAULT_ESCALATE[0]) < text.index("- Own transaction")
    assert handoff.text(repo, {"goal": "a spec from before the fields"}) == ""


def test_an_implementer_may_not_move_the_goal_or_the_handoff():
    spec = {"rev": 2, "goal": "Fix the reader", "out": []}
    assert handoff.narrowed(spec, {"goal": "Fix  the reader", "out": ["cache"], "done": ["x"]}, person=False) == ""
    assert "goal" in handoff.narrowed(spec, {"goal": "Rebuild the capture pipeline"}, person=False)
    assert "`precedence`" in handoff.narrowed(spec, {"goal": "Fix the reader", "precedence": []}, person=False)
    assert handoff.narrowed(spec, {"goal": "Rebuild the capture pipeline"}, person=True) == ""


def test_a_server_turn_moving_the_goal_is_told_to_escalate_then_stopped(task):
    repo, sid = task
    moved = {"rev": 1, "reason": "simpler", "goal": "Rebuild the capture pipeline", "out": [], "done": ["x"]}
    sent = []
    with patch.object(specs, "again", side_effect=lambda path, chat, text, spec: sent.append(text) or True):
        specs._check(repo, Run(), block("spec-update", moved))()
        specs._check(repo, Run(), block("spec-update", moved))()
    assert len(sent) == 1 and "`escalation`" in sent[0]
    spec = specs.load("proj", sid)
    assert spec["rev"] == 1 and spec["goal"] != moved["goal"] and "상위 판단" in spec["fault"]


def test_guidance_keeps_the_spec_and_resumes_the_implementer(task):
    repo, sid = task
    asked, sent = [], []
    with patch.object(handoff, "ask", side_effect=lambda path, author, system, text, run: asked.append((author, text))
                      or block("guidance", {"text": "Commit inside CacheStore.write."})), \
         patch.object(specs, "again", side_effect=lambda path, chat, text, spec: sent.append(text) or True):
        then = specs._check(repo, Run(), block("escalation", ESCALATION))
        then()
    assert asked[0][0]["model"] == "opus" and "The queue cannot commit" in asked[0][1]
    assert "Read this first" in asked[0][1], "the author sees the spec as the implementer has it"
    assert "Commit inside CacheStore.write." in sent[0] and "revision 1" in sent[0]
    spec = specs.load("proj", sid)
    assert spec["rev"] == 1 and spec["escalations"][0]["answer"]["kind"] == "guidance"


def test_the_author_revises_the_spec_and_its_handoff(task):
    repo, sid = task
    update = {"rev": 1, "reason": "The queue commits inside the transaction", "goal": "Queue inside the transaction",
              "out": [], "done": ["The queue test passes"], "task": {"kind": "change", "what": "Move the queue"},
              "precedence": [{"ref": "a.txt:1-1", "role": "current"}]}
    sent = []
    with patch.object(handoff, "ask", return_value=block("spec-update", update)), \
         patch.object(specs, "again", side_effect=lambda path, chat, text, spec: sent.append(text) or True):
        specs._check(repo, Run(), block("escalation", ESCALATION))()
    # The running session started on revision 1: the turn carries revision 2 whole.
    assert "revision 2" in sent[0] and "**Task (change):** Move the queue" in sent[0] and "Queue inside" in sent[0]
    spec = specs.load("proj", sid)
    assert (spec["rev"], spec["goal"], spec["task"]["kind"]) == (2, "Queue inside the transaction", "change")
    assert spec["precedence"] == [{"ref": "a.txt:1-1", "role": "current"}]


def test_a_split_cards_the_prerequisite_and_holds_the_task(task):
    repo, sid = task
    first = spec_block(slug="untangle-transaction", goal="Untangle the transaction boundary")
    with patch.object(handoff, "ask", return_value=block("split", {"reason": "it must land first", "spec": first})):
        assert specs._check(repo, Run(), block("escalation", ESCALATION)) is None
    before = specs.load("proj", "untangle-transaction")
    assert before["state"] == "정리됨" and before["author"]["model"] == "opus"
    assert "untangle-transaction" in specs.load("proj", sid)["fault"]


def test_the_person_decides_past_the_rounds_or_without_an_author(task):
    repo, sid = task
    with patch.object(handoff, "ask", return_value=block("guidance", {"text": "Go on."})) as ask, \
         patch.object(specs, "again", return_value=True):
        for _ in range(handoff.ROUNDS):
            assert specs._check(repo, Run(), block("escalation", ESCALATION)) is not None
        assert specs._check(repo, Run(), block("escalation", ESCALATION)) is None
    assert ask.call_count == handoff.ROUNDS
    spec = specs.load("proj", sid)
    assert "상위 판단 필요" in spec["fault"] and spec["escalations"][-1]["answer"]["kind"] == "person"
    specs.update("proj", sid, author={}, source={}, escalations=[], fault=None)
    with patch.object(handoff, "ask") as ask:
        assert specs._check(repo, Run(), block("escalation", ESCALATION)) is None
    assert not ask.called and "기록돼 있지 않다" in specs.load("proj", sid)["fault"]


def test_an_answer_outside_the_three_goes_to_the_person(task):
    repo, sid = task
    with patch.object(handoff, "ask", return_value="I think it is fine."):
        assert specs._check(repo, Run(), block("escalation", ESCALATION)) is None
    assert "하나로 답하지 않았다" in specs.load("proj", sid)["fault"]


def test_a_stop_at_the_revisions_save_owns_the_result(task):
    repo, sid = task
    run, loads, load = Run(), [], specs.load
    update = {"rev": 1, "reason": "r", "goal": "Another goal", "out": [], "done": ["x"]}

    def reloaded(*a):   # the Stop lands while the answer's spec is read back, before it is saved
        loads.append(a)
        if len(loads) == 2:
            run.halt.set()
        return load(*a)
    with patch.object(handoff, "ask", side_effect=lambda *a: loads.clear() or block("spec-update", update)), \
         patch.object(specs, "load", side_effect=reloaded), patch.object(specs, "again") as again:
        assert specs._check(repo, run, block("escalation", ESCALATION)) is None
    assert specs.load("proj", sid)["rev"] == 1 and not again.called


def test_whatever_halts_the_turn_ends_the_authors_turn(tmp_path):
    class Chat:
        def __init__(self, *a, **k):
            self.stopped = threading.Event()

        def say(self, text, halt):
            self.stopped.wait(5)
            yield SimpleNamespace(kind="error", text="stopped", meta={})

        def stop(self, halt):
            self.stopped.set()

        def close(self):
            pass
    run = Run()
    threading.Timer(0.2, run.halt.set).start()   # the review loop's Stop sets the halt and nothing else
    with patch("agent.ChatSession", Chat), pytest.raises(RuntimeError):
        handoff.ask(tmp_path, {"model": "opus"}, "", "", run)


def test_a_oneshot_escalation_that_goes_nowhere_leaves_the_stage_resumable(task):
    repo, sid = task
    specs.update("proj", sid, oneshot={"stage": "tests", "working": "tests"})
    run = Run()
    with patch("main.oneshot_spec.store") as store:
        assert specs._check(repo, run, block("escalation", {"conflict": "x"})) is None
    assert store.return_value.stop.call_args[0][0] == "block" and run.checked


def test_a_rewritten_goal_carries_the_task_and_a_promoted_range_is_checked_again(task, repo):
    repo, sid = task
    with patch.object(specs, "again", return_value=True):
        specs._check(repo, Run(person=True), block("spec-update", {"rev": 1, "reason": "owner", "goal": "Replace the login",
                                                                  "out": [], "done": ["x"]}))
    assert specs.load("proj", sid)["task"] == {"kind": "fix", "what": "Replace the login"}
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    kept = [{"ref": "a.txt:1-3", "role": "legacy"}]
    good = {"task": {"kind": "fix", "what": "x"}}
    assert handoff.checked(repo, {**good, "precedence": kept}, kept=kept)
    with pytest.raises(ValueError, match="줄 범위"):
        handoff.checked(repo, {**good, "precedence": [{"ref": "a.txt:1-3", "role": "current"}]}, kept=kept)


def test_a_stop_while_the_author_answers_applies_nothing_and_sends_nothing(task):
    repo, sid = task
    run = Run()
    with patch.object(handoff, "ask", side_effect=lambda *a: run.halt.set() or block("guidance", {"text": "Go."})), \
         patch.object(specs, "again") as again:
        assert specs._check(repo, run, block("escalation", ESCALATION)) is None
    assert not again.called and "사람이 멈춤" in specs.load("proj", sid)["fault"]
    assert specs.load("proj", sid)["escalations"][-1]["answer"]["kind"] == "stopped"


def test_a_review_loop_halts_before_any_answer_or_refusal_takes_effect(task):
    repo, sid = task
    specs.update("proj", sid, state="고치는 중 R1")
    update = {"rev": 1, "reason": "r", "goal": "Another goal", "out": [], "done": ["x"]}
    with patch.object(handoff, "ask", return_value=block("spec-update", update)), \
         patch("main.loop.asked") as asked, patch.object(specs, "again") as again:
        assert specs._check(repo, Run(), block("escalation", ESCALATION)) is None
        assert specs.load("proj", sid)["rev"] == 1 and asked.call_count == 1
        # The repair's own refused revision: the loop keeps its retries, nothing else sends one.
        assert specs._check(repo, Run(), block("spec-update", update))() is None
    assert asked.call_count == 2 and not again.called and specs.load("proj", sid)["rev"] == 1


def test_a_persons_turn_moves_the_task_with_the_goal(task):
    repo, sid = task
    change = {"rev": 1, "reason": "owner", "goal": "Another goal", "out": [], "done": ["x"],
              "task": {"kind": "change", "what": "Do the other thing"}}
    with patch.object(specs, "again", return_value=True):
        specs._check(repo, Run(person=True), block("spec-update", change))
    spec = specs.load("proj", sid)
    assert (spec["rev"], spec["goal"], spec["task"]["what"]) == (2, "Another goal", "Do the other thing")


def test_moved_entries_are_checked_in_the_authors_tree_and_standing_ones_never_again(task, tmp_path):
    repo, sid = task
    (tmp_path / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    spec = specs.load("proj", sid)
    specs.revise(repo, spec, {"rev": 1, "reason": "r", "goal": spec["goal"], "out": [], "done": ["x"],
                              "precedence": [{"ref": "a.txt:1-2", "role": "current"}]}, author=True, checkout=tmp_path)
    (tmp_path / "a.txt").write_text("", encoding="utf-8")   # the standing range no longer fits
    spec = specs.load("proj", sid)
    specs.revise(repo, spec, {"rev": 2, "reason": "r", "goal": spec["goal"], "out": [], "done": ["y"],
                              "task": {"kind": "fix", "what": "Reworded"}}, author=True, checkout=tmp_path)
    assert specs.load("proj", sid)["precedence"] == [{"ref": "a.txt:1-2", "role": "current"}]


def test_a_role_that_is_no_word_is_a_validation_error(repo):
    with pytest.raises(ValueError, match="`role`"):
        handoff.checked(repo, {"task": {"kind": "fix", "what": "x"}, "precedence": [{"ref": "a.txt", "role": []}]})


def test_a_card_on_the_cli_default_names_the_model_it_ran_on():
    from main import query
    assert query.author({"model": "", "effort": ""}, {"model": "claude-sonnet-4-6"})["model"] == "claude-sonnet-4-6"
