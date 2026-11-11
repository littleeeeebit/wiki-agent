"""oneshot step 4c: the critic that sees the screenshots, the stop rule, and R3.

See `docs/plans/oneshot/4-frontend.md` ("Critique", "Fix and repeat", "R3"). The critic's
turn (`oneshot.ask`) is a stand-in that records the images it was handed; the request
the real session sends with them is in `test_session_images.py`.
"""

import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import oneshot, oneshot_build, oneshot_critique as critique, oneshot_spec, specs
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_front_build import FEATURES, FREQUENCY, FRONT
from test_oneshot_review import git
from test_oneshot_spec import PROFILE, fenced, world  # noqa: F401 — `world` is a fixture
from test_session_images import PNG

HIGH = {"severity": "high", "route": "/", "viewport": "desktop", "state": "full", "what": "합계가 안 보인다",
        "fix": "합계를 맨 위에 크게 둔다"}
LOW = {**HIGH, "severity": "low", "what": "여백이 좁다", "fix": "여백을 16px 로"}


def o() -> dict:
    return specs.load("budget", oneshot.BRANCH)["oneshot"]


def look(n=1, findings=()):
    shots = critique.folder("budget", n)
    shots.mkdir(parents=True, exist_ok=True)
    for s in FRONT["screens"]:
        for state in critique.STATES:
            (shots / f"{s['id']}-desktop-{state}.png").write_bytes(PNG)
    return {"round": n, "head": "a" * 40, "findings": list(findings)}


@pytest.fixture
def judged(world):  # noqa: F811 — the imported fixture, requested by name
    oneshot_spec.saved("budget", oneshot={
        "stage": "critique", "front": FRONT, "features": FEATURES, "frequency": FREQUENCY, "first_screen": "오늘 쓴 돈",
        "r2": {"palette": "C1"}, "profile": {**PROFILE, "responsive": "desktop", "os_now": "windows", "os_later": "none"}, "looks": [look()],
        "tests_head": "b" * 40, "contract": ["test_budget.py"], "gate": "uv run pytest", "working": "critique",
        "front_built_head": git(world, "rev-parse", "HEAD")})
    sent = []
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)):
        yield world, sent


def critic(*answers):
    heard, queue = [], list(answers)

    def ask(store, role, system, text, where=None, images=()):
        heard.append((role, text, images))
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer, {"cost_usd": 0.2}, False

    return heard, patch.object(oneshot, "ask", ask)


def said(*findings):
    return "봤다.\n" + fenced("oneshot-critique", list(findings))


# -- The critic's block -------------------------------------------------------------------

@pytest.mark.parametrize("finding, why", [
    ({**HIGH, "severity": "P1"}, "severity"),
    ({**HIGH, "route": "/nowhere"}, "찍은 화면"),
    ({**HIGH, "viewport": "phone"}, "찍은 화면"),
    ({**HIGH, "fix": " "}, "fix"),
])
def test_a_critique_outside_its_schema_is_refused(judged, finding, why):
    with pytest.raises(ValueError, match=why):
        critique.critique_of([finding], specs.load("budget", oneshot.BRANCH))
    assert critique.critique_of([], specs.load("budget", oneshot.BRANCH)) == []


# -- The stop rule ---------------------------------------------------------------------------

def test_the_critic_sees_each_screenshot_and_two_clean_rounds_open_r3(judged):
    heard, asking = critic(said(), said())
    with asking:
        critique.critique("budget")
        assert o()["stage"] == "critique" and o()["working"] == "critique" and len(o()["critiques"]) == 1
        critique.critique("budget")   # nothing to fix: the same shots looked at again
    assert o()["stage"] == "r3" and [c["clean"] for c in o()["critiques"]] == [True, True]
    role, text, images = heard[0]
    assert role == "critic" and [p.name for p in images][:3] == ["S1-desktop-full.png", "S2-desktop-full.png",
                                                                 "S3-desktop-full.png"]
    assert len(images) == 12 and "S3-desktop-loading.png" in text


def test_a_high_finding_goes_to_the_builder_and_a_clean_round_after_a_fix_counts(judged):
    _, sent = judged
    _, asking = critic(said(HIGH, LOW), said(LOW), said())
    with asking:
        critique.critique("budget")
        assert o()["stage"] == "build-front" and sent[-1][:2] == ("budget", "build-front")
        assert sent[-1][2].index("합계가 안 보인다") < sent[-1][2].index("여백이 좁다")   # high first
        oneshot_spec.saved("budget", oneshot={"stage": "critique", "looks": [*o()["looks"], look(2)]})
        critique.critique("budget")   # clean, with a low finding: fixed once more
        assert o()["stage"] == "build-front" and "여백이 좁다" in sent[-1][2]
        oneshot_spec.saved("budget", oneshot={"stage": "critique", "looks": [*o()["looks"], look(3)]})
        critique.critique("budget")
    assert o()["stage"] == "r3" and [c["clean"] for c in o()["critiques"]] == [False, True, True]


def test_a_mechanical_high_finding_overrides_a_clean_critique(judged):
    _, sent = judged
    mechanical = {"severity": "high", "screen": "S1", "viewport": "desktop", "state": "full", "what": "콘솔 오류 x"}
    oneshot_spec.saved("budget", oneshot={"looks": [look(1, [mechanical])]})
    _, asking = critic(said())
    with asking:
        critique.critique("budget")
    assert o()["critiques"][-1]["clean"] is False and "콘솔 오류 x" in sent[-1][2]


def test_the_cap_stops_with_critique_and_a_resume_counts_afresh(judged):
    oneshot_spec.saved("budget", oneshot={"critiques": [{"look": 1, "head": "a", "findings": [HIGH], "clean": False}] * 5})
    _, asking = critic(said(HIGH))
    with asking:
        critique.critique("budget")
    assert o()["stopped"]["reason"] == "critique" and "합계가 안 보인다" in o()["stopped"]["detail"]
    assert o()["critique_base"] == 6


@pytest.mark.parametrize("why", ["codex", "failed"])
def test_a_critic_that_cannot_see_the_images_stops_with_critique_never_as_clean(judged, why):
    heard, asking = critic(oneshot.Failed("이 세션은 그림을 받지 못한다", {}))
    with asking, patch.object(oneshot, "codex_role", lambda role="", store=None: why == "codex" and role == "critic"):
        critique.critique("budget")
    assert o()["stopped"]["reason"] == "critique" and not o().get("critiques")
    assert (heard == []) is (why == "codex")


# -- R3 --------------------------------------------------------------------------------------

def at_r3():
    oneshot_spec.saved("budget", oneshot={"stage": "r3", "working": None, "stopped": None, "r3": {"round": 1, "decisions": {}, "marks": {}},
                                          "critiques": [{"look": 1, "head": "a" * 40, "findings": [LOW], "clean": True}]})


def test_the_r3_sheet_shows_the_final_first_screen_the_placement_and_what_was_left(judged):
    at_r3()
    shown = oneshot_spec.view(specs.load("budget", oneshot.BRANCH))
    assert shown["r3_items"] == ["S1", "S2", "S3"]
    sheet = shown["r3_sheet"]
    assert "/api/oneshot/spec/budget/look/1/S1-desktop-full.png" in sheet and "| 드물게 쓴다 | 내보내기 (`export`) | S3 |" in sheet
    assert "여백이 좁다" in sheet
    assert str(critique.look_shot("budget", 1, "S1-desktop-error.png").path).endswith("S1-desktop-error.png")
    with pytest.raises(HTTPException):
        critique.look_shot("budget", 1, "../../spec.json")


def test_r3_approval_moves_to_the_pull_request_and_an_edit_runs_the_loop_again(judged):
    _, sent = judged
    at_r3()
    with pytest.raises(HTTPException, match="정하지 않은"):
        critique.decide("budget", critique.Decisions(decisions={"S1": {"approve": True}}))
    critique.decide("budget", critique.Decisions(decisions={"S1": {"approve": True}, "S2": {"edit": "표로 바꿔라"},
                                                             "S3": {"approve": True}}))
    assert o()["stage"] == "build-front" and o()["critique_base"] == 1 and "표로 바꿔라" in sent[-1][2]
    assert o()["r3"]["next_marks"] == {"S2": "edited"}
    oneshot_spec.saved("budget", oneshot={"stage": "r3", "working": None})
    critique.decide("budget", critique.Decisions(decisions={"S2": {"approve": True}}))
    assert o()["stage"] == "pr"


# -- The tree the critic judged, the hand-overs, the shots it sees ---------------------------

@pytest.mark.parametrize("change", ["commit", "dirty"])
def test_a_tree_changed_after_the_shots_is_neither_judged_nor_approved(judged, change):
    tree, _ = judged

    def changed():
        (tree / "later.txt").write_text("x", encoding="utf-8")
        if change == "commit":
            git(tree, "add", "later.txt")
            git(tree, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-m", "later")

    changed()
    heard, asking = critic(said())
    with asking:
        critique.critique("budget")
    assert heard == [] and o()["stage"] == "build-front" and o()["stopped"]["reason"] == "gate"
    if change == "commit":
        git(tree, "reset", "-q", "--hard", "HEAD~1")
    else:
        (tree / "later.txt").unlink()
    at_r3()
    changed()
    critique.decide("budget", critique.Decisions(decisions={s["id"]: {"approve": True} for s in FRONT["screens"]}))
    assert o()["stage"] == "build-front" and o()["stopped"]["reason"] == "gate"


@pytest.mark.parametrize("cut", ["restart", "block"])
def test_fixes_whose_turn_was_never_sent_go_again_on_resume(judged, cut):
    _, sent = judged
    at_r3()
    critique.decide("budget", critique.Decisions(decisions={"S1": {"approve": True}, "S2": {"edit": "R3_ONLY_EDIT 표로"},
                                                             "S3": {"approve": True}}))
    assert o()["working"] == "build-front" and o()["fix"]["findings"][0]["what"] == "R3_ONLY_EDIT 표로"
    if cut == "restart":   # the server died before the turn went out
        oneshot_spec.recover()
    else:                  # the session could not be opened
        oneshot_spec.store("budget").stop("block", "작업 턴을 보내지 못했다")
    assert o()["stopped"]["reason"] == cut and not o()["working"]
    oneshot_spec.resume("budget")
    assert sent[-1][1] == "build-front" and "R3_ONLY_EDIT 표로" in sent[-1][2]


def test_a_critique_s_fixes_are_saved_before_their_turn_and_kept_until_a_build_passes(judged):
    _, sent = judged
    _, asking = critic(said(HIGH))
    with asking:
        critique.critique("budget")
    assert o()["working"] == "build-front" and o()["fix"]["findings"] == [HIGH]
    assert "합계가 안 보인다" in critique.front_turn(specs.load("budget", oneshot.BRANCH))


def test_a_round_with_a_shot_missing_is_never_judged(judged):
    (critique.folder("budget", 1) / "S2-desktop-error.png").unlink()
    heard, asking = critic(said(), said())
    with asking:
        critique.critique("budget")
    assert heard == [] and o()["stage"] == "build-front" and o()["stopped"]["reason"] == "gate"
    assert "S2-desktop-error.png" in o()["stopped"]["detail"] and not o().get("critiques")


def linked(link, target):
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_a_round_folder_that_links_outside_the_run_is_never_served_nor_shown(judged, tmp_path):
    outside = tmp_path / "private"
    outside.mkdir()
    for s in FRONT["screens"]:
        for state in critique.STATES:
            (outside / f"{s['id']}-desktop-{state}.png").write_bytes(PNG)
    linked(critique.folder("budget", 2), outside)
    with pytest.raises(HTTPException):
        critique.look_shot("budget", 2, "S1-desktop-full.png")
    spec = specs.load("budget", oneshot.BRANCH)
    assert critique.pictures(spec, 2)[0] == [] and critique.pictures(spec, 1)[1] == []


def test_an_r3_approval_the_worktree_refused_is_resumed_and_approved_again(judged):
    tree, _ = judged
    at_r3()
    everything = critique.Decisions(decisions={s["id"]: {"approve": True} for s in FRONT["screens"]})
    git(tree, "checkout", "-q", "-b", "elsewhere")
    critique.decide("budget", everything)
    assert o()["stage"] == "r3" and o()["stopped"]["reason"] == "folder"
    git(tree, "checkout", "-q", oneshot.BRANCH)
    oneshot_spec.resume("budget")
    assert o()["stage"] == "r3" and not o()["stopped"] and o()["r3"]["decisions"]
    critique.decide("budget", critique.Decisions(decisions={}))
    assert o()["stage"] == "pr"
