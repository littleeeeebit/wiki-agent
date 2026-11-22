"""oneshot step 9c3b: several kinds' critique and phone round — the critic judges one kind a round, in the owner's
order, seeing only that kind's shots; each kind needs two clean rounds on the HEAD its look passed before the next is
judged; a fix sends the build back, and the next HEAD judges every kind again; Android's phone round then runs on
the Android app alone, and its pass waits before R3.

See `docs/plans/oneshot/9-kinds.md` ("S7 to R3 — each kind's loop, one R3"). The critic's turn and the phone's drive
are stand-ins.
"""

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from main import oneshot_build, oneshot_critique as critique, oneshot_device as device, \
    oneshot_phone as phone, oneshot_spec
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_critique import HIGH, critic, o, said
from test_oneshot_kinds_build import DEVICE, FEATURES, SCREENS, STATES
from test_oneshot_kinds import KINDS_REQS
from test_oneshot_review import git
from test_oneshot_spec import PROFILE, world  # noqa: F401 — `world` is a fixture
from test_session_images import PNG

KINDS = ["android", "webapp"]
VIEW = {"android": "phone", "webapp": "desktop"}


def look(head: str, n: int = 1, findings=()) -> dict:
    shots = critique.folder("budget", n)
    shots.mkdir(parents=True, exist_ok=True)
    for s in SCREENS:
        for state in critique.STATES:
            (shots / f"{s['id']}-{VIEW[s['kind']]}-{state}.png").write_bytes(PNG)
    return {"round": n, "head": head, "findings": list(findings)}


@pytest.fixture
def kinds(world):  # noqa: F811 — the imported fixture, requested by name
    head = git(world, "rev-parse", "HEAD")
    oneshot_spec.saved("budget", requirements=KINDS_REQS, oneshot={
        "intent": {"kind": "android", "kinds": KINDS}, "stage": "critique", "working": "critique",
        "front": {"screens": SCREENS, "states": STATES, "mocks": {"S2": "<p>기록</p>"}, "shots": 1,
                  "tokens": {"palettes": [{"id": "C1", "name": "초록", "colors": {
                      "primary": "#2f6f4e", "surface": "#ffffff", "text": "#111111", "accent": "#e0a000"}}],
                      "type": ["16"], "spacing": [8]}},
        "features": FEATURES, "frequency": {"read": "every", "history": "every"}, "r2": {"palette": "C1"},
        "first_screen": {"android": "만화를 읽으며 번역을 켠다", "webapp": "지난 번역을 찾아본다"},
        "profile": {**PROFILE, "responsive": "desktop", "device": "medium-phone"}, "design": {"modules": []},
        "looks": [look(head)], "front_built_head": head, "front_build": "b1", "device": DEVICE,
        "apks": {DEVICE["apk"]: "digest"},
        "tests_head": "b" * 40, "contract": ["test_budget.py"], "gate": "uv run pytest"})
    sent = []
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)):
        yield world, sent


def seen(images) -> list[str]:
    return sorted({p.name.split("-")[0] for p in images})


def test_each_kind_is_judged_in_turn_on_its_own_shots_and_the_phone_follows(kinds):
    heard, asking = critic(said(), said(), said(), said())
    with asking:
        for _ in range(4):
            critique.critique("budget")
    assert [seen(images) for _, _, images in heard] == [["S1"], ["S1"], ["S2"], ["S2"]]
    assert "만화를 읽으며 번역을 켠다" in heard[0][1] and "지난 번역을 찾아본다" in heard[2][1]
    assert [c["kind"] for c in o()["critiques"]] == ["android", "android", "webapp", "webapp"]
    assert o()["stage"] == phone.STAGE and o()["phone"]["status"] == "waiting"


def test_a_kinds_finding_names_its_screen_by_that_kinds_place():
    one ={"oneshot": {"front": {"screens": [{k: v for k, v in s.items() if k != "kind"} for s in SCREENS
                                             if s["kind"] == "android"]}, "profile": {"device": "medium-phone"}}}
    with pytest.raises(ValueError, match="찍은 화면"):   # the web app's route is not the Android critic's to judge
        critique.critique_of([HIGH], one)
    assert critique.critique_of([{**HIGH, "route": "S1", "viewport": "phone"}], one)[0]["route"] == "S1"


def test_a_fix_for_one_kind_judges_every_kind_again_on_the_new_head(kinds):
    tree, sent = kinds
    _, asking = critic(said(), said(), said(HIGH))
    with asking:
        for _ in range(3):
            critique.critique("budget")
    assert o()["stage"] == "build-front" and o()["fix"]["findings"][0]["kind"] == "webapp"
    assert "합계가 안 보인다" in sent[-1][2]
    git(tree, "commit", "--allow-empty", "-q", "-m", "fix")
    head = git(tree, "rev-parse", "HEAD")
    oneshot_spec.saved("budget", oneshot={"stage": "critique", "working": "critique", "front_built_head": head,
                                          "looks": [*o()["looks"], look(head, 2)]})
    assert critique.critic_kind(o()) == "android"   # Android passed on the old HEAD only: judged again first
    heard, asking = critic(said())
    with asking:
        critique.critique("budget")
    assert seen(heard[0][2]) == ["S1"]


def test_the_phone_round_runs_on_the_android_app_alone_and_its_pass_opens_r3(kinds, tmp_path):
    oneshot_spec.saved("budget", oneshot={"stage": phone.STAGE, "working": phone.STAGE,
                                          "phone": {"serial": "R5CT", "status": "running"}})
    given, briefed = [], []

    @contextmanager
    def server(repo):
        yield tmp_path

    def drive(repo, home, spec, frozen, stopped):
        given.append(spec)   # a pass names the HEAD, APKs and accepted build it ran, as the round's own does
        return {"status": "passed", "round": 1, "model": "SM-G991N", "findings": [], "detail": "",
                "head": spec["oneshot"].get("front_built_head"), "apks": spec["oneshot"].get("apks"),
                "front_build": spec["oneshot"].get("front_build")}

    with patch.object(device, "built", lambda tree, d: ""), \
            patch.object(device, "frozen", lambda tree, d, into: {DEVICE["apk"]: "digest"}), \
            patch.object(phone, "phone_server", server), patch.object(phone, "drive", drive), \
            patch.object(oneshot_spec, "briefing", lambda repo, gate, fields: briefed.append((gate, fields))):
        phone.phone_round("budget")
    one = given[0]
    assert [s["id"] for s in one["oneshot"]["front"]["screens"]] == ["S1"]
    assert [r["id"] for r in one["requirements"]] == ["R1", "R2"]
    (gate, fields), = briefed
    assert gate == "r3" and fields["stage"] == "r3" and fields["phone"]["status"] == "passed"


def test_one_kinds_cap_restarts_only_its_own_count(kinds):
    """A kind's cap restarts its own count; a kind that passed on this HEAD is not judged again on `[재개]`."""

    head = o()["looks"][-1]["head"]
    oneshot_spec.saved("budget", oneshot={"critiques": [
        *[{"look": 1, "head": head, "kind": "android", "clean": True, "findings": []}] * 2,
        *[{"look": 1, "head": head, "kind": "webapp", "clean": False, "findings": []}] * (critique.MAX_ROUNDS - 1)]})
    _, asking = critic(said(HIGH))
    with asking:
        critique.critique("budget")
    assert o()["stopped"]["reason"] == "critique" and o()["stopped"]["counted"] == critique.MAX_ROUNDS
    assert o()["stopped"]["detail"].startswith("웹앱") and not o().get("critique_base")
    assert critique.critic_kind(o()) == "webapp" and critique.counted(o(), o()["critiques"], "webapp") == []
