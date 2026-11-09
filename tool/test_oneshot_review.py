"""oneshot step 3c: the backend review, read-only, between agents and before any pull request.

See `docs/plans/oneshot/3-spec-backend.md` ("Backend review"). The review turn
(`oneshot.ask`) is a stand-in; Git runs in a temporary worktree.
"""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from agent import read_tools
from main import loop, oneshot, oneshot_build, oneshot_review, oneshot_sheet as sheet, oneshot_spec, query, specs, work
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_spec import DESIGN, GLOSSARY, PROFILE, REQS, fenced, world  # noqa: F401 — `world` is a fixture


def git(tree, *args):
    return subprocess.run(["git", *args], cwd=tree, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def reviewed(world):  # noqa: F811 — the imported fixture, requested by name
    base = git(world, "rev-parse", "HEAD")
    (world / "budget_app.py").write_text("def add(amount):\n    return amount\n", encoding="utf-8")
    git(world, "add", "budget_app.py")
    git(world, "commit", "-m", "build")
    head = git(world, "rev-parse", "HEAD")
    oneshot_spec.saved("budget", start_head=base, requirements=REQS, oneshot={
        "glossary": GLOSSARY, "stage": "review", "design": sheet.parsed(DESIGN), "tests_head": base,
        "contract": ["test_budget.py"], "built_head": head, "working": "review",
        "profile": {**PROFILE, "python_env": "uv"}})
    sent = []
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)):
        yield world, head, sent


def o() -> dict:
    return specs.load("budget", oneshot.BRANCH)["oneshot"]


def verdict(head, kind="approve", *findings):
    return "읽었다.\n" + fenced("oneshot-review", {"verdict": kind, "head": head, "findings": list(findings)})


P1 = {"location": "budget_app.py:2", "severity": "P1", "reason": "음수 금액을 받는다", "evidence": "add(-1) 이 -1"}
P2 = {"location": "budget_app.py:1", "severity": "P2", "reason": "이름이 짧다", "evidence": "1행"}


def answers(*texts):
    heard, queue = [], list(texts)

    def ask(store, role, system, text, where=None):
        heard.append((role, text, where))
        return queue.pop(0), {"cost_usd": 0.1}, False

    return heard, patch.object(oneshot, "ask", ask)


def test_an_approval_of_the_head_read_moves_to_split_with_the_input_frozen(reviewed):
    tree, head, _ = reviewed
    heard, asking = answers(verdict(head, "approve", P2))
    with asking:
        oneshot_review.review("budget")
    assert o()["stage"] == "split" and o()["reviews"][-1]["findings"] == [P2] and o()["working"] is None
    role, text, where = heard[0]
    assert role == "review" and where == tree and head in text and "test_spending_adds_to_the_total" in text
    assert "지출을 적으면" in text   # the requirements travel in the prompt; the reviewer reads no record
    assert "budget_app.py" in read_tools.diff_file(tree).read_text(encoding="utf-8")
    assert (oneshot_spec.store("budget").folder / f"review-{head[:12]}.diff").exists()


def test_a_verdict_on_another_head_counts_as_none(reviewed):
    tree, head, _ = reviewed
    _, asking = answers(verdict("0" * 40), verdict("0" * 40))
    with asking:
        oneshot_review.review("budget")
    assert o()["stage"] == "review" and o()["stopped"]["reason"] == "block" and "HEAD" in o()["stopped"]["detail"]


@pytest.mark.parametrize("kind, findings, said", [
    ("approve", [P1], "승인했다"),
    ("refuse", [P2], "하나 이상"),
    ("refuse", [{**P1, "severity": "P4"}], "severity"),
])
def test_a_verdict_its_findings_do_not_justify_is_refused(reviewed, kind, findings, said):
    _, head, _ = reviewed
    with pytest.raises(ValueError, match=said):
        oneshot_review.verdict_of({"verdict": kind, "head": head, "findings": findings}, head)


def test_a_refusal_goes_to_the_build_once_and_a_second_stops_the_run(reviewed):
    tree, head, sent = reviewed
    _, asking = answers(verdict(head, "refuse", P1))
    with asking:
        oneshot_review.review("budget")
    assert o()["stage"] == "build" and o()["refused"] == 1 and o()["attempts"] == 0
    assert sent[-1][:2] == ("budget", "build") and "음수 금액을 받는다" in sent[-1][2] and "test_budget.py" in sent[-1][2]
    oneshot_spec.saved("budget", oneshot={"stage": "review", "working": "review"})
    _, asking = answers(verdict(head, "refuse", P1))
    with asking:
        oneshot_review.review("budget")
    assert o()["stopped"]["reason"] == "review" and len(sent) == 1 and len(o()["reviews"]) == 2


def test_a_head_other_than_the_one_the_build_passed_is_not_reviewed(reviewed):
    tree, _, _ = reviewed
    (tree / "late.py").write_text("x = 1\n", encoding="utf-8")
    git(tree, "add", "late.py")
    git(tree, "commit", "-m", "late")
    heard, asking = answers()
    with asking:
        oneshot_review.review("budget")
    assert o()["stage"] == "build" and o()["stopped"]["reason"] == "gate" and not heard


@pytest.mark.parametrize("change", ["commit", "edit"])
def test_a_tree_changed_while_the_review_read_it_goes_back_to_the_builds_checks(reviewed, change):
    tree, head, sent = reviewed

    def ask(store, role, system, text, where=None):
        (tree / "late.py").write_text("x = 1\n", encoding="utf-8")
        if change == "commit":
            git(tree, "add", "late.py")
            git(tree, "commit", "-m", "late")
        return verdict(head), {"cost_usd": 0.1}, False

    with patch.object(oneshot, "ask", ask):
        oneshot_review.review("budget")
    assert o()["stage"] == "build" and o()["stopped"]["reason"] == "gate" and "바뀌었다" in o()["stopped"]["detail"]
    assert not sent


def test_the_review_holds_its_worktree_until_the_verdict_is_saved(reviewed):
    tree, head, sent = reviewed
    held = []

    def ask(store, role, system, text, where=None):
        with pytest.raises(HTTPException):
            query.hold(work._busy, query._lock, str(tree), "busy")
        return verdict(head, "refuse", P1), {"cost_usd": 0.1}, False

    with patch.object(oneshot, "ask", ask),          patch.object(oneshot_build, "send", lambda *a: held.append(str(tree) in work._busy)):
        oneshot_review.review("budget")
    assert held == [False]   # released before the build turn takes the tree


@pytest.mark.parametrize("kind", ["approve", "refuse"])
def test_an_instruction_queued_while_the_review_held_the_tree_goes_once_after_it(reviewed, kind):
    tree, head, sent = reviewed
    begun = []

    def ask(store, role, system, text, where=None):
        work._queued[str(tree)] = work.Order(path=str(tree), text="다음 지시")   # queued while the review held it
        return verdict(head, kind, *([P1] if kind == "refuse" else [])), {"cost_usd": 0.1}, False

    def build(*a):   # the build turn takes the tree, as `send` does
        sent.append(a)
        query.hold(work._busy, query._lock, str(tree), "", kind="turn")

    try:
        with patch.object(oneshot, "ask", ask), patch.object(oneshot_build, "send", build), \
             patch.object(work, "session", lambda *a: SimpleNamespace(id="c", parent_id=None)), \
             patch.object(work, "begin", lambda path, chat, text, release, run: (begun.append(text), release())):
            oneshot_review.review("budget")
        if kind == "approve":
            assert begun == ["다음 지시"] and str(tree) not in work._queued
        else:   # the refusal's build turn goes first; the instruction waits for that turn's end
            assert sent and not begun and work._queued[str(tree)].text == "다음 지시"
    finally:
        work._queued.pop(str(tree), None)
        work._busy.pop(str(tree), None)


def test_a_busy_worktree_stops_the_review_before_it_reads(reviewed):
    tree, _, _ = reviewed
    release = query.hold(work._busy, query._lock, str(tree), "busy")
    heard, asking = answers()
    try:
        with asking:
            oneshot_review.review("budget")
    finally:
        release()
    assert o()["stopped"]["reason"] == "block" and not heard


def test_a_server_that_dies_before_the_refusals_build_turn_resumes_it_with_the_findings(reviewed):
    tree, head, sent = reviewed
    seen = []
    _, asking = answers(verdict(head, "refuse", P1))
    with asking, patch.object(oneshot_build, "send", lambda *a: seen.append(o()["working"])):
        oneshot_review.review("budget")   # the build turn never left: the server went down
    assert seen == ["build"]
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart"
    oneshot_spec.resume("budget")
    assert sent[-1][:2] == ("budget", "build") and "음수 금액을 받는다" in sent[-1][2]


def test_resuming_a_stopped_review_counts_refusals_afresh(reviewed):
    oneshot_spec.store("budget").stop("review", "두 번 거부")
    oneshot_spec.saved("budget", oneshot={"refused": 2})
    begun = []
    with patch.object(oneshot_review, "begin", begun.append):
        oneshot_spec.resume("budget")
    assert begun == ["budget"] and o()["refused"] == 0


def test_the_review_role_reads_the_review_loops_model():
    with patch.object(loop, "review_model", lambda: "codex:gpt-6.1-sol"), \
         patch.object(loop, "settings", lambda: {"review_effort": "high"}):
        assert oneshot.role_config("review") == {"model": "codex:gpt-6.1-sol", "effort": "high"}
        assert oneshot.codex_role("review") is True
