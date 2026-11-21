"""oneshot step 4c: the critic that sees the screenshots, the stop rule, and R3.

See `docs/plans/oneshot/4-frontend.md` ("Critique", "Fix and repeat", "R3"). The critic's
turn (`oneshot.ask`) is a stand-in that records the images it was handed; the request
the real session sends with them is in `test_session_images.py`.
"""

import os
import re
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from main import oneshot, oneshot_brief as brief, oneshot_build, oneshot_critique as critique, oneshot_spec, specs
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


def on_r3(**fields) -> critique.Decisions:
    """An R3 submission made on the gate as it stands, carrying that gate's `source`."""

    return critique.Decisions(source=brief.source(brief.snapshot(oneshot_spec.spec_of("budget"), "r3")), **fields)


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

    def ask(store, role, system, text, where=None, images=(), tools=None):
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


def pc_look(n: int, build: str) -> dict:
    """A PC app's passed look on frozen `build`: each screen's window shot in each state."""

    shots = critique.folder("budget", n)
    shots.mkdir(parents=True, exist_ok=True)
    for s in FRONT["screens"]:
        for state in critique.STATES:
            (shots / f"{s['id']}-window-{state}.png").write_bytes(PNG)
    return {"round": n, "head": "a" * 40, "findings": [], "build": build}


def test_a_pc_apps_two_clean_rounds_on_one_build_wait_before_r3_with_its_owner_checks_pending(judged):
    owner = {"id": "O2", "kind": "desktop", "source": "owner", "requirement": "R1", "check": "캡처에 글이 있다"}
    screens = [{**{k: v for k, v in s.items() if k != "route"}, "surface": "window"} for s in FRONT["screens"]]
    oneshot_spec.saved("budget", oneshot={"intent": {"restated": "가계부", "kind": "desktop"}, "owed": [owner],
                                          "front": {**FRONT, "screens": screens}, "looks": [pc_look(1, "a" * 64)],
                                          "profile": {**o()["profile"], "window": "1280x800"}})
    heard, asking = critic(said(), said(), said())
    with asking:
        critique.critique("budget")
        oneshot_spec.saved("budget", oneshot={"looks": [*o()["looks"], pc_look(2, "b" * 64)]})   # rebuilt, same HEAD
        critique.critique("budget")
        assert o()["stage"] == "critique" and not o().get("stopped")   # the first build's clean round is cleared
        critique.critique("budget")
    assert [p.name for p in heard[0][2]][:1] == ["S1-window-full.png"] and o()["stage"] == "r3"
    assert o()["stopped"]["reason"] == "kind" and "O2" in o()["stopped"]["detail"] and not o().get("working")
    assert not o().get("r3") and [c.get("build") for c in o()["critiques"]] == ["a" * 64, "b" * 64, "b" * 64]
    assert "r3_sheet" not in critique.extra(oneshot_spec.spec_of("budget"))   # a held run opens no review state


def test_a_look_that_ran_none_of_the_pc_apps_owed_look_checks_goes_back_to_the_build(judged):
    owed = {"id": "O1", "kind": "desktop", "source": "look", "requirement": "R1", "check": "켜짐",
            "expect": {"id": "translate-on", "toggle": "On"}}
    screens = [{**{k: v for k, v in s.items() if k != "route"}, "surface": "window"} for s in FRONT["screens"]]
    oneshot_spec.saved("budget", oneshot={"intent": {"restated": "가계부", "kind": "desktop"}, "owed": [owed],
                                          "front": {**FRONT, "screens": screens}, "looks": [pc_look(1, "a" * 64)],
                                          "profile": {**o()["profile"], "window": "1280x800"}})   # 9e4a's look
    heard, asking = critic(said())
    with asking, patch.object(critique.review, "rebuild", lambda repo, store, why, stage: heard.append((why, stage))):
        critique.critique("budget")
    assert heard == [("PC 앱에서 확인할 O1 을 마지막 화면 검사가 돌리지 않았다", "build-front")]


def test_a_build_seen_before_never_brings_its_clean_rounds_back():
    rounds = [{"head": "h", "clean": True, "build": b} for b in ("A", "B", "A")]
    assert not critique.passed(rounds, "h", "A") and critique.passed([*rounds, rounds[-1]], "h", "A")


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
    assert o()["critique_base"] == 6 and o()["stopped"]["counted"] == critique.MAX_ROUNDS and o()["working"] is None


@pytest.mark.parametrize("why", ["codex", "failed"])
def test_a_critic_that_cannot_see_the_images_stops_with_critique_never_as_clean(judged, why):
    heard, asking = critic(oneshot.Failed("이 세션은 그림을 받지 못한다", {}))
    with asking, patch.object(oneshot, "codex_role", lambda role="", store=None: why == "codex" and role == "critic"):
        critique.critique("budget")
    assert o()["stopped"]["reason"] == "critique" and not o().get("critiques")
    assert "counted" not in o()["stopped"]   # no cap reached: the screen counts nothing against it
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
        critique.decide("budget", on_r3(decisions={"S1": {"approve": True}}))
    critique.decide("budget", on_r3(decisions={"S1": {"approve": True}, "S2": {"edit": "표로 바꿔라"},
                                                             "S3": {"approve": True}}))
    assert o()["stage"] == "build-front" and o()["critique_base"] == 1 and "표로 바꿔라" in sent[-1][2]
    assert o()["r3"]["next_marks"] == {"S2": "edited"}
    oneshot_spec.saved("budget", oneshot={"stage": "r3", "working": None})
    critique.decide("budget", on_r3(decisions={"S2": {"approve": True}}))
    assert o()["stage"] == "pr"


ON_S2 = {**LOW, "route": "/months", "what": "목록 글씨가 작다", "fix": "글씨를 키운다"}
R3_BRIEF = {"sections": [{"name": "한 줄", "text": "완성된 화면으로 지출을 적고 달별로 봅니다. 브리프표식삼삼"},
                         {"name": "화면", "text": "첫 화면은 처음보다 합계가 잘 보입니다. (S1)"}],
            "confirm": [{"key": "L1", "text": "달별 화면의 목록 글씨가 조금 작습니다."},
                        {"key": "L2", "text": "달별 화면이 비었을 때 안내가 작습니다."},
                        {"key": "L3", "text": "어느 화면인지 적히지 않은 발견입니다. 글꼴이 섞여 보입니다."}]}


def test_r3_opens_on_its_brief_one_line_per_finding_and_an_edited_line_reaches_the_builder_with_its_finding(judged):
    _, sent = judged
    harness = [{"severity": "low", "screen": "S2", "viewport": "desktop", "state": "empty", "what": "빈 화면 안내가 작다"},
               {"severity": "low", "screen": None, "viewport": "desktop", "state": "full", "what": "글꼴이 섞였다"}]
    oneshot_spec.saved("budget", oneshot={"looks": [look(1, harness)]})
    brief_block = fenced("oneshot-brief", R3_BRIEF)
    _, asking = critic(said(), said(ON_S2), brief_block, brief_block)
    with asking:
        critique.critique("budget")
        critique.critique("budget")
    shown = oneshot_spec.view(oneshot_spec.spec_of("budget"))
    r3 = shown["r3_brief"]
    assert o()["stage"] == "r3" and not o()["working"] and "failed" not in r3, r3
    assert r3["lines"] == {"L1": ["S2"], "L2": ["S2"], "L3": ["S1"]}
    assert r3["pictures"]["L2"] == ["/api/oneshot/spec/budget/look/1/S2-desktop-empty.png"]
    assert r3["pictures"]["화면"] == ["/api/oneshot/spec/budget/look/1/S1-desktop-full.png"] * 2
    snap = brief.snapshot(oneshot_spec.spec_of("budget"), "r3")
    for changed, said_why in [({0: "달별 화면은 /months 에 있습니다."}, "화면 경로 /months"),
                              ({1: "첫 화면입니다. (L9)"}, "없는 번호를 댔다: L9")]:
        value = {**R3_BRIEF, "sections": [{**s, "text": changed.get(n, s["text"])} for n, s in enumerate(R3_BRIEF["sections"])]}
        with pytest.raises(ValueError, match=re.escape(said_why)):
            brief.checked(value, snap, r3["lines"], 1)
    phone = {**R3_BRIEF, "sections": [*R3_BRIEF["sections"], {"name": "휴대폰에서", "text": "부드럽게 움직였습니다."}]}
    with pytest.raises(ValueError, match="휴대폰 회차가 없었다"):
        brief.checked(phone, snap, r3["lines"], 1)
    with pytest.raises(HTTPException) as refused:
        critique.decide("budget", critique.Decisions(source="0" * 64, lines={k: {"approve": True} for k in r3["lines"]}, rest=True))
    assert refused.value.status_code == 409 and o()["stage"] == "r3"
    critique.decide("budget", on_r3(lines={"L1": {"approve": True}, "L2": {"edit": "안내를 크게"}, "L3": {"approve": True}},
                                    rest=True))
    assert o()["fix"]["findings"] == [{"severity": "high", "screen": "S2", "viewport": "desktop", "state": "empty",
                                       "finding": "빈 화면 안내가 작다", "what": "안내를 크게"}]
    assert o()["reading"]["r3"][-1]["brief"] is True and o()["r3"]["next_marks"] == {"S2": "edited"}
    assert "브리프표식삼삼" not in sent[-1][2] and "빈 화면 안내가 작다" in sent[-1][2]   # the builder never reads the brief
    assert "브리프표식삼삼" not in critique.r3_sheet(oneshot_spec.spec_of("budget"), lambda n, name: name)
    oneshot_spec.saved("budget", oneshot={"stage": "r3", "working": None})   # the same gate, shown again
    critique.decide("budget", on_r3(lines={"L1": {"edit": "글씨를 키워라"}, "L2": {"edit": "안내를 크게"},
                                           "L3": {"approve": True}}, rest=True))
    assert [f["finding"] for f in o()["fix"]["findings"]] == ["목록 글씨가 작다", "빈 화면 안내가 작다"]
    assert "[확인 줄 L1] 글씨를 키워라" in o()["r3"]["decisions"]["S2"]["edit"]
    oneshot_spec.saved("budget", oneshot={"stage": "r3", "working": None})
    critique.decide("budget", on_r3(lines={k: {"approve": True} for k in r3["lines"]}, rest=True))
    assert o()["stage"] == "pr"
    assert "기기에서 확인한 지적" not in critique.r3_sheet(oneshot_spec.spec_of("budget"), lambda n, name: name)
    oneshot_spec.saved("budget", oneshot={"owed": [{"id": "O1", "kind": "android", "requirement": "R1", "severity": "P1",
                                                    "location": "a.kt:3", "reason": "상태바를 가린다", "check": "바 아래"}]})
    shown = critique.r3_sheet(oneshot_spec.spec_of("budget"), lambda n, name: name)
    last = [x for x in o()["looks"] if not any(f["severity"] == "high" for f in x["findings"])][-1]
    assert f"모두 에뮬레이터 {last['round']}회차 `{last['head'][:12]}`" in shown
    assert "- O1 [P1] R1 `a.kt:3`: 상태바를 가린다 — 확인: 바 아래" in shown


def test_two_findings_the_harness_words_alike_reach_the_builder_apart_by_where_they_were_seen():
    seen = [("desktop", "full"), ("phone", "empty")]
    spec = {"oneshot": {"front": {"screens": [{"id": "S1", "route": "/", "primary": True}]},
                        "looks": [{"round": 1, "findings": [{"severity": "medium", "screen": "S1", "viewport": v,
                                                             "state": s, "what": "가로로 넘친다"} for v, s in seen]}]}}
    asked = [critique.asked_fixes(spec, critique.Decisions(lines={k: {"edit": "고쳐라"} if k == edited else {"approve": True}
                                                                  for k in ("L1", "L2")}), True, {"S1": "고쳐라"})
             for edited in ("L1", "L2")]
    assert [(f["viewport"], f["state"]) for fixes in asked for f in fixes] == seen


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
    critique.decide("budget", on_r3(decisions={s["id"]: {"approve": True} for s in FRONT["screens"]}))
    assert o()["stage"] == "build-front" and o()["stopped"]["reason"] == "gate"


@pytest.mark.parametrize("cut", ["restart", "block"])
def test_fixes_whose_turn_was_never_sent_go_again_on_resume(judged, cut):
    _, sent = judged
    at_r3()
    critique.decide("budget", on_r3(decisions={"S1": {"approve": True}, "S2": {"edit": "R3_ONLY_EDIT 표로"},
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
    everything = on_r3(decisions={s["id"]: {"approve": True} for s in FRONT["screens"]})
    git(tree, "checkout", "-q", "-b", "elsewhere")
    critique.decide("budget", everything)
    assert o()["stage"] == "r3" and o()["stopped"]["reason"] == "folder"
    git(tree, "checkout", "-q", oneshot.BRANCH)
    oneshot_spec.resume("budget")
    assert o()["stage"] == "r3" and not o()["stopped"] and o()["r3"]["decisions"]
    critique.decide("budget", on_r3(decisions={}))
    assert o()["stage"] == "pr"
