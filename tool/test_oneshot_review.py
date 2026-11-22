"""oneshot step 3c: the backend review, read-only, between agents and before any pull request.

See `docs/plans/oneshot/3-spec-backend.md` ("Backend review"). The review turn
(`oneshot.ask`) is a stand-in; Git runs in a temporary worktree.
"""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import (loop, oneshot, oneshot_build, oneshot_critique, oneshot_review, oneshot_sheet as sheet, oneshot_spec,
                  query, specs, work)
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
        "contract": ["test_budget.py"], "gate": "uv run pytest", "built_head": head, "working": "review",
        "profile": PROFILE, "measures": [approved(head)]})
    sent = []
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)):
        yield world, head, sent


def approved(head: str, n: int = 1) -> dict:
    """The owner's approval of `head`'s real measurement, without which no review runs (`oneshot_measure`)."""

    return {"n": n, "head": head, "status": "owner", "metrics": [], "notes": "",
            "owner": {"approve": True, "note": "들어 봤다", "ts": 0}}


def o() -> dict:
    return specs.load("budget", oneshot.BRANCH)["oneshot"]


def verdict(head, kind="approve", *findings):
    return "읽었다.\n" + fenced("oneshot-review", {"verdict": kind, "head": head, "findings": list(findings)})


P1 = {"location": "budget_app.py:2", "severity": "P1", "reason": "음수 금액을 받는다", "evidence": "add(-1) 이 -1",
      "area": "amount validation", "breaks": ["R1"], "repro": "add(-1) 을 부르면 -1 이 합계에 들어간다"}
P2 = {"location": "budget_app.py:1", "severity": "P2", "reason": "이름이 짧다", "evidence": "1행"}


def answers(*texts):
    heard, queue = [], list(texts)

    def ask(store, role, system, text, where=None, images=()):
        heard.append((role, text, where))
        return queue.pop(0), {"cost_usd": 0.1}, False

    return heard, patch.object(oneshot, "ask", ask)


def test_an_approval_of_the_head_read_moves_to_split_with_the_input_frozen(reviewed):
    tree, head, _ = reviewed
    heard, asking = answers(verdict(head, "approve", P2))
    started = []
    with asking, patch.object(oneshot_spec, "start", lambda *a, **fields: started.append((a, fields))):
        oneshot_review.review("budget")
    (args, fields), = started
    assert args == ("budget", "split") and not fields
    assert o()["stage"] == "split" and o()["working"] == "split" and o()["reviews"][-1]["findings"] == [P2]
    role, text, where = heard[0]
    assert role == "review" and where != tree and not where.exists()   # its own checkout, removed after
    assert head in text and "test_spending_adds_to_the_total" in text
    assert "지출을 적으면" in text   # the requirements travel in the prompt; the reviewer reads no record
    assert "budget_app.py" in next(oneshot_spec.store("budget").folder.glob("review-*.diff")).read_text(encoding="utf-8")
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
    ("refuse", [{**P1, "area": " "}], "area"),
])
def test_a_verdict_its_findings_do_not_justify_is_refused(reviewed, kind, findings, said):
    _, head, _ = reviewed
    with pytest.raises(ValueError, match=said):
        oneshot_review.verdict_of({"verdict": kind, "head": head, "findings": findings}, head)


def test_a_refusal_goes_to_the_build_each_time_until_the_round_cap_stops_the_run(reviewed):
    tree, head, sent = reviewed
    with patch.object(loop, "settings", lambda: {"rounds": 3, "review_effort": "high"}):
        for n in (1, 2):   # the second refusal used to stop the run: the repair loop ended after one round
            _, asking = answers(verdict(head, "refuse", P1))
            with asking:
                oneshot_review.review("budget")
            assert o()["stage"] == "build" and o()["refused"] == n and o()["attempts"] == 0 and len(sent) == n
            assert sent[-1][:2] == ("budget", "build") and "음수 금액을 받는다" in sent[-1][2] and "test_budget.py" in sent[-1][2]
            oneshot_spec.saved("budget", oneshot={"stage": "review", "working": "review"})
        _, asking = answers(verdict(head, "refuse", P1))
        with asking:
            oneshot_review.review("budget")
        assert o()["stopped"]["reason"] == "review" and len(sent) == 2 and len(o()["reviews"]) == 3
        resumed = []   # `[재개]` at the cap repairs the refused HEAD; reading it again would only repeat
        with patch.object(oneshot_build, "resume", resumed.append), \
             patch.object(oneshot_review, "begin", lambda repo: pytest.fail("the refused HEAD was read again")):
            oneshot_spec.resume("budget")
    assert resumed == ["budget"] and o()["stage"] == "build" and o()["working"] == "build" and o()["refused"] == 0
    assert "음수 금액을 받는다" in oneshot_review.build_turn(specs.load("budget", oneshot.BRANCH))


def test_an_area_refused_again_and_again_is_remembered_and_its_mechanism_replaced_without_stopping(reviewed):
    _, head, sent = reviewed
    moved = {**P1, "location": "budget_app.py:9", "reason": "0 원을 받는다", "area": "Amount  Validation"}
    other = {**P1, "location": "budget_app.py:4", "reason": "합계가 넘친다", "area": "total sum"}
    heard = []
    with patch.object(loop, "settings", lambda: {"rounds": 10, "review_effort": "high"}):
        for n, found in enumerate(([P1], [moved, other], [moved]), 1):
            said, asking = answers(verdict(head, "refuse", *found))
            with asking:
                oneshot_review.review("budget")
            heard.append(said[0][1])
            oneshot_spec.saved("budget", oneshot={"stage": "review", "working": "review"})
    assert "Earlier refusals" not in heard[0] and "음수 금액을 받는다" in heard[1] and "합계가 넘친다" in heard[2]
    assert "replace the mechanism" not in sent[1][2] and "음수 금액을 받는다" in sent[1][2]   # the same area, twice
    assert "`amount validation` 3 times" in sent[2][2] and "replace the mechanism" in sent[2][2]
    assert "합계가 넘친다" not in sent[2][2].split("Earlier refusals")[1]   # another area's history is not this one's
    assert not o().get("stopped") and o()["refused"] == 3   # an agent turn, never a stop for the person


def test_a_refusal_from_before_areas_is_listed_without_one_and_counts_towards_nothing(reviewed):
    _, head, sent = reviewed
    legacy = {k: P1[k] for k in ("location", "severity", "reason", "evidence")}
    oneshot_spec.saved("budget", oneshot={"reviews": [{"verdict": "refuse", "head": "old", "findings": [legacy]}] * 2})
    said, asking = answers(verdict(head, "refuse", P1))
    with asking:
        oneshot_review.review("budget")
    assert '"area"' not in said[0][1].split("Earlier refusals")[1].split("```")[1]   # nothing empty to copy
    assert "without an `area` gets one of yours" in said[0][1]
    assert "replace the mechanism" not in sent[0][2] and not o().get("stopped")


def test_the_screen_reads_the_caps_and_the_worktree_it_follows_from_the_server(reviewed):
    tree, _, _ = reviewed
    with patch.object(loop, "settings", lambda: {"rounds": 5, "review_effort": "high"}):
        shown = oneshot_spec.view(specs.load("budget", oneshot.BRANCH))
    assert shown["limits"] == {"attempts": oneshot_build.MAX_ATTEMPTS, "refusals": 5,
                               "critiques": oneshot_critique.MAX_ROUNDS}
    assert shown["worktree"] and str(tree).replace("\\", "/") in shown["worktree"].replace("\\", "/")


@pytest.mark.parametrize("raised", [False, True])
def test_resume_routes_on_how_the_review_stopped_not_on_todays_cap(reviewed, raised):
    tree, head, sent = reviewed
    with patch.object(loop, "settings", lambda: {"rounds": 1, "review_effort": "high"}):
        _, asking = answers(verdict(head, "refuse", P1))
        with asking:
            oneshot_review.review("budget")
    assert o()["stopped"]["reason"] == "review" and o()["stopped"]["capped"] == head and not sent
    if raised:   # the cap stop restarted instead: no longer a cap stop, so the review reads the HEAD again
        oneshot_spec.saved("budget", oneshot={"working": "review"})
        oneshot_spec.recover()
        assert o()["stopped"]["reason"] == "restart" and o()["refused"] == 1
    routed = []
    with patch.object(loop, "settings", lambda: {"rounds": 12, "review_effort": "high"}), \
         patch.object(oneshot_build, "resume", lambda repo: routed.append("build")), \
         patch.object(oneshot_review, "begin", lambda repo: routed.append("review")):
        oneshot_spec.resume("budget")   # raising the cap after the stop changes nothing
    assert routed == (["review"] if raised else ["build"])


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

    def ask(store, role, system, text, where=None, images=()):
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

    def ask(store, role, system, text, where=None, images=()):
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

    def ask(store, role, system, text, where=None, images=()):
        work._queued[str(tree)] = work.Order(path=str(tree), text="다음 지시")   # queued while the review held it
        return verdict(head, kind, *([P1] if kind == "refuse" else [])), {"cost_usd": 0.1}, False

    def build(*a):   # the build turn takes the tree, as `send` does
        sent.append(a)
        query.hold(work._busy, query._lock, str(tree), "", kind="turn")

    handed = []
    try:
        with patch.object(oneshot, "ask", ask), patch.object(oneshot_build, "send", build), \
             patch.object(oneshot_spec, "handoff", lambda *a: handed.append(a)), \
             patch.object(work, "session", lambda *a: SimpleNamespace(id="c", parent_id=None)), \
             patch.object(work, "begin", lambda path, chat, text, release, run: (begun.append(text), release())):
            oneshot_review.review("budget")
        if kind == "approve":   # the split holds the tree next and sends the instruction when it lets go
            assert handed == [("budget", "split")] and not begun and work._queued[str(tree)].text == "다음 지시"
        else:   # the refusal's build turn goes first; the instruction waits for that turn's end
            assert sent and not begun and work._queued[str(tree)].text == "다음 지시"
    finally:
        work._queued.pop(str(tree), None)
        work._busy.pop(str(tree), None)


def test_a_review_that_fails_still_lets_the_tree_go_and_sends_the_queued_instruction(reviewed):
    tree, _, _ = reviewed
    begun = []

    def fails(*a):
        work._queued[str(tree)] = work.Order(path=str(tree), text="다음 지시")
        raise subprocess.TimeoutExpired(["git", "rev-parse", "HEAD"], 60)

    try:
        with patch.object(oneshot_review, "verdict", fails), \
             patch.object(work, "session", lambda *a: SimpleNamespace(id="c", parent_id=None)), \
             patch.object(work, "begin", lambda path, chat, text, release, run: (begun.append(text), release())), \
             pytest.raises(subprocess.TimeoutExpired):
            oneshot_review.review("budget")
        assert begun == ["다음 지시"] and str(tree) not in work._busy
    finally:
        work._queued.pop(str(tree), None)


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


def test_a_p1_names_what_it_breaks_and_a_p0_may_stand_without(reviewed):
    _, head, _ = reviewed
    rules = {"anchors": {"R1", "E1"}}
    bare = {k: v for k, v in P1.items() if k not in ("breaks", "repro")}
    with pytest.raises(ValueError, match="breaks"):   # breaking no approved request: a P2
        oneshot_review.verdict_of({"verdict": "refuse", "head": head, "findings": [bare]}, head, rules=rules)
    with pytest.raises(ValueError, match="R9"):   # an id the design does not hold
        oneshot_review.verdict_of({"verdict": "refuse", "head": head, "findings": [{**bare, "breaks": ["R9"]}]}, head,
                                  rules=rules)
    crash = {**bare, "severity": "P0"}
    assert oneshot_review.verdict_of({"verdict": "refuse", "head": head, "findings": [crash]}, head, rules=rules)[
        "findings"] == [crash]


def test_an_area_refused_twice_needs_a_repro_for_its_next_finding(reviewed):
    _, head, _ = reviewed
    rules = {"anchors": {"R1"}, "repeated": {"amount validation"}}
    plain = {k: v for k, v in P1.items() if k != "repro"}
    for f in (plain, {**plain, "severity": "P0"}):   # the bar rises for a P0 too
        with pytest.raises(ValueError, match="repro"):
            oneshot_review.verdict_of({"verdict": "refuse", "head": head, "findings": [f]}, head, rules=rules)
    assert oneshot_review.verdict_of({"verdict": "refuse", "head": head, "findings": [P1]}, head, rules=rules)


def test_while_repairs_do_not_converge_a_new_areas_p1_points_at_a_changed_line(reviewed):
    _, head, _ = reviewed
    rules = {"anchors": {"R1"}, "known": {"amount validation"}, "changed": {"budget_app.py": [(5, 9)]}, "since": "a" * 40}
    new = {**P1, "area": "total sum"}

    def judged(*findings):
        return oneshot_review.verdict_of({"verdict": "refuse", "head": head, "findings": list(findings)}, head, rules=rules)

    bare = {k: v for k, v in new.items() if k != "repro"}
    with pytest.raises(ValueError, match="바뀐 줄"):
        judged(bare)   # line 2, read last round and unchanged since, and nothing shows it on this HEAD
    assert judged(new)   # anchored and reproduced on this HEAD: a contract break is never held back
    assert judged({**bare, "location": "`budget_app.py:7`"})   # on a changed line
    assert judged({**new, "severity": "P0"})   # a P0 is never held back
    assert judged(P1)   # an earlier area is checked wherever the fix moved it


def test_a_round_after_two_that_did_not_converge_reads_the_changed_lines_and_records_it(reviewed):
    tree, head, sent = reviewed
    other = {**P1, "location": "budget_app.py:1", "area": "total sum"}
    oneshot_spec.saved("budget", oneshot={"reviews": [{"verdict": "refuse", "head": head, "findings": [P1]},
                                                      {"verdict": "refuse", "head": head, "findings": [P1, other]}]})
    (tree / "budget_app.py").write_text("def add(amount):\n    if amount < 0:\n        raise ValueError\n"
                                        "    return amount\n", encoding="utf-8")
    git(tree, "commit", "-am", "fix")
    later = git(tree, "rev-parse", "HEAD")
    oneshot_spec.saved("budget", oneshot={"built_head": later, "measures": [approved(head), approved(later, 2)]})
    said, asking = answers(verdict(later, "refuse", {**P1, "location": "budget_app.py:3"}))
    with asking, patch.object(loop, "settings", lambda: {"rounds": 10, "review_effort": "high"}):
        oneshot_review.review("budget")
    text = said[0][1]
    assert "not converging" in text and f"since `{head}`" in text and "budget_app.py: 2-3" in text
    assert "two rounds or more: `amount validation`." in text and "carries `repro`" in text   # `total sum` once
    assert "may name: A1, E1, F1, P1, P2, R1, R2, T1, T2." in text
    last = o()["reviews"][-1]
    assert last["focus"] == "narrow" and last["added"] == 4 and last["findings"][0]["breaks"] == ["R1"]


def test_a_round_that_converges_reads_the_whole_build_as_before(reviewed):
    tree, head, _ = reviewed
    oneshot_spec.saved("budget", oneshot={"reviews": [{"verdict": "refuse", "head": head, "findings": [P1, P1]},
                                                      {"verdict": "refuse", "head": head, "findings": [P1]}]})
    said, asking = answers(verdict(head, "refuse", P1))
    with asking, patch.object(loop, "settings", lambda: {"rounds": 10, "review_effort": "high"}):
        oneshot_review.review("budget")
    assert "not converging" not in said[0][1] and o()["reviews"][-1]["focus"] == "full"


def test_a_repro_is_a_sentence_never_an_empty_shape(reviewed):
    _, head, _ = reviewed
    for repro in ({"notes": ""}, "  ", ["a"]):
        with pytest.raises(ValueError, match="repro"):
            oneshot_review.verdict_of({"verdict": "refuse", "head": head, "findings": [{**P1, "repro": repro}]}, head,
                                      rules={"anchors": {"R1"}})


def test_the_changed_lines_read_every_path_git_writes_and_every_kind_of_hunk(world):  # noqa: F811
    for name in ("a b.py", "한글.py", "plain.py"):
        (world / name).write_text("one\ntwo\nthree\n", encoding="utf-8")
    git(world, "add", "-A")
    git(world, "commit", "-m", "base")
    since = git(world, "rev-parse", "HEAD")
    (world / "a b.py").write_text("one\nTWO\nthree\n", encoding="utf-8")
    (world / "한글.py").write_text("one\ntwo\nthree\n++counter;\n", encoding="utf-8")
    (world / "plain.py").write_text("two\nthree\n", encoding="utf-8")   # its first line deleted
    git(world, "commit", "-am", "repair")
    changed = oneshot_review.changed_lines(world, since, git(world, "rev-parse", "HEAD"))
    assert changed["a b.py"] == [(2, 2)] and changed["한글.py"] == [(4, 4)] and changed["plain.py"] == [(1, 1)]
    assert oneshot_review.touched("a b.py:2", changed) and oneshot_review.touched("`plain.py:1`", changed)
    assert not oneshot_review.touched("plain.py:2", changed)
    assert oneshot_review.touched("./a b.py:2", changed) and oneshot_review.canonical(r"a\b/../c.py") == "a/c.py"


def test_the_added_lines_count_a_line_that_starts_with_two_plus_signs(reviewed):
    tree, _, _ = reviewed
    (tree / "count.js").write_text("++counter;\n", encoding="utf-8")
    git(tree, "add", "count.js")
    git(tree, "commit", "-m", "count")
    oneshot_spec.saved("budget", oneshot={"built_head": git(tree, "rev-parse", "HEAD")})
    s = specs.load("budget", oneshot.BRANCH)
    with oneshot_review.seen_at(s, oneshot_spec.store("budget")) as seen:
        assert oneshot_review.frozen(s, seen)[2] == 3   # budget_app.py's two lines and this


def test_a_quoted_path_keeps_its_escapes_and_its_literal_hangul():
    assert oneshot_review.unquoted(r"a\t한글.py") == "a\t한글.py"
    assert oneshot_review.unquoted(r"\355\225\234.py") == "한.py" and oneshot_review.unquoted(r'q\"\\.py') == 'q"\\.py'


def test_a_requirement_reached_through_another_is_one_a_p1_may_name():
    spec = {"oneshot": {"design": {"modules": [{"id": "M1", "depends_on": ["R1"]}]}},
            "requirements": [{"id": "R1", "depends_on": ["R2"]}, {"id": "R2", "depends_on": ["R3"]}, {"id": "R3"},
                             {"id": "R9"}]}
    assert oneshot_review.anchors(spec) == {"M1", "R1", "R2", "R3"}   # R9: nothing of the design reaches it


@pytest.mark.parametrize("kinds, cap", [(["android"], True), (["web", "desktop"], True), (["web"], False)])
def test_a_device_kind_anchors_an_unapproved_permission_at_its_capability_list(kinds, cap):
    spec = {"oneshot": {"intent": {"kinds": kinds}, "design": {"modules": []}}, "requirements": []}
    assert ("CAP" in oneshot_review.anchors(spec)) is cap   # an empty capability list is still citable


def test_a_changed_lines_diff_that_times_out_reads_the_whole_patch(tmp_path):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired(["git", "diff"], 120)

    with patch.object(specs, "sh", slow):
        assert oneshot_review.changed_lines(tmp_path, "a", "b") is None


def test_a_line_count_git_cannot_make_is_left_unknown(reviewed):
    _, head, _ = reviewed
    real = specs.sh

    for failed in (SimpleNamespace(returncode=1, stdout="", stderr="no"), subprocess.TimeoutExpired(["git"], 120)):
        def numstat(argv, *a, failed=failed, **k):
            if "--numstat" not in argv:
                return real(argv, *a, **k)
            if isinstance(failed, Exception):
                raise failed
            return failed

        oneshot_spec.saved("budget", oneshot={"stage": "review", "working": "review", "stopped": None})
        _, asking = answers(verdict(head, "refuse", P1))
        with asking, patch.object(specs, "sh", numstat), \
             patch.object(loop, "settings", lambda: {"rounds": 10, "review_effort": "high"}):
            oneshot_review.review("budget")
        assert "added" not in o()["reviews"][-1] and o()["stage"] == "build"   # read, refused, never stopped


ANSWER = {"area": "amount validation", "location": "budget_app.py:2", "evidence": "음수는 환불이다 — R1",
          "test": "test_budget.py::test_a_refund_is_negative", "breaks": ["R1"]}


def test_an_answered_area_is_rebutted_or_left_out_and_two_answered_rounds_go_to_the_owner(reviewed):
    _, head, sent = reviewed
    past = [{"verdict": "refuse", "head": f"old{n}", "findings": [P1]} for n in (1, 2)]
    oneshot_spec.saved("budget", oneshot={"reviews": past, "answers": [
        {**ANSWER, "round": n, "head": f"old{n}"} for n in (1, 2)]})
    rebutted = {**P1, "rebuttal": "환불 경로는 R1 의 지출이 아니다 — budget_app.py:2 가 둘을 섞는다"}
    heard, asking = answers(verdict(head, "refuse", P1), verdict(head, "refuse", rebutted))
    with asking, patch.object(loop, "settings", lambda: {"rounds": 10, "review_effort": "high"}):
        oneshot_review.review("budget")
    assert "음수는 환불이다" in heard[0][1] and "rebuttal" in heard[1][1]   # read, and asked again without one
    assert o()["stage"] == "conflict" and not sent and not o().get("stopped")
    (c,) = o()["conflicts"]
    assert c["why"] == "answered" and len(c["answers"]) == 2
    with pytest.raises(HTTPException, match="직접 정하려면"):
        oneshot_review.rule("budget", oneshot_review.Rulings(head=head, rulings={c["key"]: {"keep": "owner"}}))
    begun = []
    with patch.object(oneshot_review, "begin", begun.append):
        oneshot_review.rule("budget", oneshot_review.Rulings(head=head, rulings={c["key"]: {"keep": "build"}}))
    assert begun == ["budget"] and o()["stage"] == "review" and o()["rulings"][c["key"]]["keep"] == "build"
    heard, asking = answers(verdict(head, "refuse", rebutted), verdict(head, "approve", {**P1, "severity": "P2"}))
    with asking:
        oneshot_review.review("budget")
    assert "빌더 쪽" in heard[0][1] and "P2" in heard[1][1] and o()["stage"] == "split"


def test_a_build_resumed_at_the_cap_replaces_a_stuck_mechanism_once_and_remembers_it(reviewed):
    tree, head, _ = reviewed
    oneshot_spec.saved("budget", oneshot={"stage": "build", "reviews": [
        {"verdict": "refuse", "head": h, "findings": [P1]} for h in ("old1", "old2", head)]})
    assert "replace the mechanism" in oneshot_review.build_turn(specs.load("budget", oneshot.BRANCH))
    assert o()["replacing"] == ["amount validation"] and not o().get("replaced")
    # A turn that never ran (a crash, a refused turn) is told again: nothing counts as replaced before a pass.
    assert "replace the mechanism" in oneshot_review.build_turn(specs.load("budget", oneshot.BRANCH))
    from main import oneshot_answers
    (tree / "budget_app.py").write_text("def add(amount):\n    return max(amount, 0)\n", encoding="utf-8")
    git(tree, "commit", "-am", "the mechanism replaced")   # the build that passes changed the refused line
    oneshot_spec.saved("budget", oneshot=oneshot_answers.passed(tree, o(), []))
    assert o()["replaced"] == ["amount validation"] and not o().get("replacing")
    assert "replace the mechanism" not in oneshot_review.build_turn(specs.load("budget", oneshot.BRANCH))


def test_no_review_runs_without_the_owner_approving_this_heads_measurement(reviewed):
    _, head, _ = reviewed
    oneshot_spec.saved("budget", oneshot={"measures": [{**approved(head), "owner": {"approve": False, "note": "끊긴다"}}]})
    heard, asking = answers(verdict(head, "approve"))
    with asking:
        oneshot_review.review("budget")
    assert not heard and o()["stage"] == "measure" and o()["stopped"]["reason"] == "measure"
    s = specs.load("budget", oneshot.BRANCH)
    assert "원샷" in loop.oneshot_profile.in_run(s)   # nor through Review Loop, which would read PR #1 unmeasured


def test_the_reviewer_reads_only_the_approved_head_never_a_commit_made_since(reviewed):
    tree, head, _ = reviewed
    (tree / "later.py").write_text("x = 1\n", encoding="utf-8")
    git(tree, "add", "later.py")
    git(tree, "commit", "-m", "after the approval")
    heard, asking = answers(verdict(head, "approve"))
    with asking:   # past the hold's first look, as if the commit landed in between
        with patch.object(oneshot_review, "moved", side_effect=["", oneshot_review.moved(
                specs.load("budget", oneshot.BRANCH), tree)]):
            oneshot_review.review("budget")
    assert not heard and o()["stage"] == "build" and o()["stopped"]["reason"] == "gate"


def test_the_reviewer_reads_the_committed_head_while_the_worktree_changes(reviewed):
    tree, head, _ = reviewed
    committed, read = (tree / "budget_app.py").read_text(encoding="utf-8"), []

    def ask(store, role, system, text, where=None, images=()):
        (tree / "budget_app.py").write_text("changed = True\n", encoding="utf-8")   # restored before the turn ends
        try:
            read.append((git(where, "rev-parse", "HEAD"), (where / "budget_app.py").read_text(encoding="utf-8")))
            return verdict(head, "approve"), {"cost_usd": 0.1}, False
        finally:
            (tree / "budget_app.py").write_text(committed, encoding="utf-8")

    with patch.object(oneshot, "ask", ask), patch.object(oneshot_spec, "start", lambda *a, **k: None):
        oneshot_review.review("budget")
    assert read == [(head, committed)] and o()["stage"] == "split"


def test_a_finding_handed_to_a_later_build_names_its_area_so_a_ruling_matches_it(reviewed):
    _, head, _ = reviewed
    nameless = {k: v for k, v in P1.items() if k != "area"} | {"later": {"kind": "webapp", "what": "x"}}
    with pytest.raises(ValueError, match="area"):
        oneshot_review.verdict_of({"verdict": "approve", "head": head, "findings": [nameless]}, head,
                                  rules={"ruled": {"amount validation"}, "kinds": {"webapp"}})


def test_review_loops_own_paths_leave_an_unfinished_oneshot_alone(reviewed):
    oneshot_spec.saved("budget", pr={"number": 1, "url": "u"}, state="PR #1", oneshot={"stage": "pr", "measures": []})
    s = specs.load("budget", oneshot.BRANCH)
    kicked = []
    with patch.object(loop, "mine", lambda sid: (SimpleNamespace(name="budget"), s)), \
            patch.object(loop, "_loops", {}), patch.object(loop.threading, "Thread", lambda *a, **k: kicked.append(a)):
        with pytest.raises(HTTPException, match="원샷"):
            loop.review_again(s["id"])
        loop.rereview("budget", s)
        loop.kick("budget", s["id"])
    assert not kicked and not specs.load("budget", oneshot.BRANCH).get("review_again")


def test_a_ruling_for_the_builder_never_drops_a_p0_and_later_work_survives_every_verdict(reviewed):
    _, head, sent = reviewed
    p0 = {**P1, "severity": "P0", "reason": "합계가 깨진다"}
    o_ = {"rulings": {"amount validation": {"keep": "build"}}}
    assert oneshot_review.ruled_out(o_, P1) and not oneshot_review.ruled_out(o_, p0)
    handed = {**P1, "later": {"kind": "webapp", "what": "x"}}   # a ruled P1 is not passed on to a later build either
    with pytest.raises(ValueError, match="빌더 쪽"):
        oneshot_review.verdict_of({"verdict": "approve", "head": head, "findings": [handed]}, head,
                                  rules={"ruled": {"amount validation"}, "kinds": {"webapp"}})
    kept = {"kind": "webapp", "what": "자막 창", "location": "x", "reason": "y"}
    oneshot_spec.saved("budget", oneshot={"later": [kept]})
    _, asking = answers(verdict(head, "approve", P2))
    with asking, patch.object(oneshot_spec, "start", lambda *a, **f: None):
        oneshot_review.review("budget")
    assert o()["stage"] == "split" and o()["later"] == [kept]


def test_a_head_refused_in_an_earlier_round_is_never_built_on_again(reviewed):
    tree, head, _ = reviewed
    from main import oneshot_answers
    rounds = {"reviews": [{"verdict": "refuse", "head": head, "findings": [P1]},
                          {"verdict": "refuse", "head": "newer", "findings": [P1]}]}
    assert "커밋이 하나도 늘지 않았다" in oneshot_answers.unchanged(tree, rounds)   # reset back to the older one
    failed = {"measures": [{**approved(head), "status": "failed", "owner": None}]}
    assert "실측이 실패했거나" in oneshot_answers.unchanged(tree, failed)
    assert oneshot_answers.ran([("test_budget", "test_pin", "test_budget.py", "passed")],
                               [{"test": "test_budget.py::test_pin"}]) == ""
    assert "돌지 않음" in oneshot_answers.ran([], [{"test": "test_budget.py::test_pin"}])
    assert "failed" in oneshot_answers.ran([("test_budget", "test_pin", "", "failed")], [{"test": "test_budget.py::test_pin"}])
    two = [("test_budget.TestA", "test_pin", "test_budget.py", "failed"), ("test_budget.TestB", "test_pin", "test_budget.py", "passed")]
    assert oneshot_answers.ran(two, [{"test": "test_budget.py::TestB::test_pin"}]) == ""   # the class it names
    assert "failed" in oneshot_answers.ran(two, [{"test": "test_budget.py::TestA::test_pin"}])


def test_an_answer_names_a_test_its_turn_added_or_changed_never_one_beside_a_comment(reviewed):
    tree, head, _ = reviewed
    from main import oneshot_answers
    (tree / "test_pin.py").write_text("def test_old():\n    assert True\n\n\nclass TestRefund:\n"
                                      "    def test_value(self):\n        assert True\n", encoding="utf-8")
    git(tree, "add", "test_pin.py")
    git(tree, "commit", "-m", "pins")
    refused = git(tree, "rev-parse", "HEAD")
    oneshot_spec.saved("budget", oneshot={"fix": "review", "reviews": [{"verdict": "refuse", "head": refused,
                                                                         "findings": [P1]}]})
    (tree / "test_pin.py").write_text("# a note\ndef test_old():\n    assert True\n\n\nclass TestRefund:\n"
                                      "    def test_value(self):\n        assert 1 == 1\n", encoding="utf-8")
    git(tree, "commit", "-am", "note and a changed pin")

    def answer(test):
        return "```oneshot-answers\n" + __import__("json").dumps([{
            "area": "amount validation", "location": "budget_app.py:2", "evidence": "환불", "breaks": ["R1"],
            "test": test}]) + "\n```\n"
    s = specs.load("budget", oneshot.BRANCH)
    with pytest.raises(ValueError, match="더하거나 고친"):   # only a comment above it changed
        oneshot_answers.answered(tree, s, answer("test_pin.py::test_old"))
    assert oneshot_answers.answered(tree, s, answer("test_pin.py::TestRefund::test_value"))[0]["test"].endswith("test_value")


def test_a_replacement_counts_only_where_the_turn_changed_the_refused_lines_and_did_not_answer(reviewed):
    tree, head, _ = reviewed
    from main import oneshot_answers
    o_ = {"replacing": ["amount validation"], "reviews": [{"verdict": "refuse", "head": head, "findings": [P1]}]}
    assert oneshot_answers.passed(tree, o_, [])["replaced"] == []   # nothing changed at budget_app.py:2
    (tree / "budget_app.py").write_text("def add(amount):\n    return max(amount, 0)\n", encoding="utf-8")
    git(tree, "commit", "-am", "new mechanism")
    assert oneshot_answers.passed(tree, o_, [{"area": "Amount validation"}])["replaced"] == []   # answered instead
    assert oneshot_answers.passed(tree, o_, [])["replaced"] == ["amount validation"]


def test_a_comment_or_blank_line_neither_replaces_a_mechanism_nor_changes_a_test(reviewed):
    tree, head, _ = reviewed
    from main import oneshot_answers
    (tree / "budget_app.py").write_text("def add(amount):\n    # same mechanism\n\n    return amount\n", encoding="utf-8")
    git(tree, "commit", "-am", "a comment only")
    o_ = {"replacing": ["amount validation"], "reviews": [{"verdict": "refuse", "head": head, "findings": [P1]}]}
    assert oneshot_answers.passed(tree, o_, [])["replaced"] == []


def test_a_test_span_is_the_definition_and_its_whole_body():
    from main.oneshot_answers import test_span
    assert test_span('LABEL = "test_old"\n', ["test_old"]) is None   # a mention is no test
    assert test_span("def test_old():\n# a note\n    assert x\nok = 1\n", ["test_old"]) == (1, 3)
    assert test_span("class TestA:\n    def test_b(self):\n        pass\n", ["TestA", "test_b"]) == (2, 3)
    assert test_span("it('adds one', () => {\n  expect(1)\n})\n", ["adds one"]) == (1, 3)
    inherited = "class TestTarget(Base):\n    pass\n\nclass TestOther:\n    def test_value(self):\n        pass\n"
    assert test_span(inherited, ["TestTarget", "test_value"]) is None   # found only inside the class named
    assert test_span("// test('value', () => {\n  run()\n", ["value"]) is None   # a comment defines nothing
    assert test_span("test('value', () => {\n  --count;\n  check()\n})\n", ["value"]) == (1, 4)


def test_an_answers_test_must_change_its_own_code_and_a_runner_root_names_it(reviewed):
    from main.oneshot_answers import body_of, ran
    before = "# about it\ndef test_old():\n    assert True\n"
    assert body_of(before, ["test_old"]) == body_of(before.replace("# about it\n", ""), ["test_old"])
    assert body_of(before, ["test_old"]) != body_of(before.replace("True", "1 == 1"), ["test_old"])
    case = [("test_budget", "test_x", "test_budget.py", "passed")]   # the runner reports from `web`
    assert ran(case, [{"test": "web/test_budget.py::test_x"}], "web") == ""
    assert "돌지 않음" in ran(case, [{"test": "web/test_budget.py::test_x"}])


def test_a_removed_comment_or_an_insertion_elsewhere_replaces_no_mechanism(reviewed):
    tree, _, _ = reviewed
    from main import oneshot_answers
    (tree / "budget_app.py").write_text("def add(amount):\n    # the same\n    return amount\n", encoding="utf-8")
    git(tree, "commit", "-am", "a comment")
    refused = git(tree, "rev-parse", "HEAD")
    finding = {**P1, "location": "budget_app.py:3"}
    o_ = {"replacing": ["amount validation"], "reviews": [{"verdict": "refuse", "head": refused, "findings": [finding]}]}
    (tree / "budget_app.py").write_text("def add(amount):\n    return amount\n", encoding="utf-8")
    git(tree, "commit", "-am", "the comment removed")
    assert oneshot_answers.passed(tree, o_, [])["replaced"] == []
    (tree / "budget_app.py").write_text("LIMIT = 1\ndef add(amount):\n    return amount\n", encoding="utf-8")
    git(tree, "commit", "-am", "code above it")
    assert oneshot_answers.passed(tree, o_, [])["replaced"] == []
    (tree / "budget_app.py").write_text("LIMIT = 1\ndef add(amount):\n    return max(amount, 0)\n", encoding="utf-8")
    git(tree, "commit", "-am", "the refused line replaced")
    assert oneshot_answers.passed(tree, o_, [])["replaced"] == ["amount validation"]


def test_an_owner_ruling_for_the_review_goes_to_the_build_with_their_words(reviewed):
    _, head, sent = reviewed
    oneshot_spec.saved("budget", oneshot={"stage": "conflict", "working": None, "reviews": [
        {"verdict": "refuse", "head": head, "findings": [P1]}], "conflicts": [
        {"key": "amount validation", "area": "amount validation", "why": "replaced", "findings": [P1], "answers": []}]})
    oneshot_review.rule("budget", oneshot_review.Rulings(head=head, rulings={
        "amount validation": {"keep": "owner", "note": "음수는 따로 환불 버튼으로 받는다"}}))
    assert o()["stage"] == "build" and o()["fix"] == "review"
    assert "음수는 따로 환불 버튼으로 받는다" in sent[-1][2] and "음수 금액을 받는다" in sent[-1][2]


def test_a_finding_for_a_later_kind_build_does_not_block_and_reaches_that_build(reviewed):
    _, head, sent = reviewed
    later = {**P1, "area": "overlay", "reason": "자막 창이 없다", "later": {"kind": "desktop", "what": "자막 창을 만든다"}}
    with pytest.raises(ValueError, match="later"):   # a kind this run never builds
        oneshot_review.verdict_of({"verdict": "refuse", "head": head, "findings": [P1, {**later, "later": {
            "kind": "android", "what": "x"}}]}, head, rules={"kinds": {"webapp"}})
    _, asking = answers(verdict(head, "refuse", P1, {**later, "later": {"kind": "webapp", "what": "자막 창을 만든다"}}))
    with asking, patch.object(loop, "settings", lambda: {"rounds": 10, "review_effort": "high"}):
        oneshot_review.review("budget")
    assert "자막 창이 없다" not in sent[-1][2].split("```json")[1].split("```")[0]
    assert o()["later"][0]["kind"] == "webapp"
    assert "자막 창을 만든다" in oneshot_review.later_text(o(), ["webapp"]) and not oneshot_review.later_text(o(), ["android"])
