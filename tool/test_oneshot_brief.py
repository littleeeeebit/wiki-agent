"""oneshot step 8a: the owner's brief written after R1's design turns, its checks, and its isolation.

See `docs/plans/oneshot/8-human-sheet.md`. Model turns (`oneshot.ask`) are stand-ins.
"""

import json
from unittest.mock import patch

import pytest

from main import oneshot, oneshot_brief as brief, oneshot_sheet as sheet, oneshot_spec
from test_oneshot_spec import DESIGN, GLOSSARY, REQS, answer, design_with, fenced, o, replies, world  # noqa: F401
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse

MARKER = "브리프표식칠칠"
ASSUMED = [*REQS, {"id": "R3", "text": "윈도우판도 만든다", "source": {"kind": "decision", "locator": "S2"},
                   "status": "assumed", "postconditions": []},
           {"id": "R4", "text": "다른 언어는 옮기지 않는다", "source": {"kind": "decision", "locator": "S2"},
            "status": "not_applicable", "postconditions": []}]


def good(**over) -> dict:
    value = {"sections": [
        {"name": "한 줄", "text": f"달마다 쓴 돈을 적고 합계를 보는 가계부입니다. {MARKER}"},
        {"name": "쓰는 장면", "text": "1. 금액을 적습니다. 2. 이번 달 합계가 늘어납니다. (R1)"},
        {"name": "빼기로 한 것", "text": "다른 언어는 옮기지 않습니다. (R4)"},
        {"name": "알아 두실 것", "text": "이 PC 에서만 돌아갑니다."},
        {"name": "다 됐다고 보는 기준", "text": "적은 금액이 합계에 바로 더해집니다."}],
        "confirm": [{"key": "R3", "text": "윈도우판도 함께 만듭니다. 작업이 늘어납니다."},
                    {"key": "A1", "text": "달은 달력 기준으로 나눕니다."}]}
    return {**value, **over}


def at_design(reqs=ASSUMED):
    oneshot_spec.saved("budget", requirements=reqs, oneshot={"glossary": GLOSSARY, "stage": "design"})


def test_the_brief_is_written_after_the_design_and_kept_out_of_every_agent_input(world):  # noqa: F811 — the imported fixture, by its name
    at_design()
    heard = []

    def ask(store, role, system, text, *rest):
        heard.append((role, text))
        return (answer() if role == "design" else fenced("oneshot-brief", good())), {"cost_usd": 0.1}, False

    with patch.object(oneshot, "ask", ask):
        oneshot_spec.design("budget")
    r1 = o()["brief"]["r1"]
    assert o()["stage"] == "r1" and o()["working"] is None and "failed" not in r1
    assert [c["key"] for c in r1["confirm"]] == ["R3", "A1"] and r1["lines"] == {"R3": ["R3"], "A1": ["A1"]}
    assert heard[1][0] == "brief" and "R3: 윈도우판도 만든다" in heard[1][1]
    spec = oneshot_spec.spec_of("budget")
    assert r1["source"] == brief.source(brief.snapshot(spec))
    # Nothing an agent or a file reads carries it: the design prompt, the sheet, the design files, the edit turn.
    assert MARKER not in oneshot_spec.design_text(spec) and MARKER not in sheet.sheet(spec)
    items = sheet.decidable(o()["design"], ASSUMED)
    oneshot_spec.decide("budget", oneshot_spec.Decisions(decisions={
        i: ({"edit": "응답을 sum 으로"} if i == "P1" else {"approve": True}) for i in items}))
    redone = design_with({("oneshot-api", 0, "response"): {"sum": "int"}, ("oneshot-tests", 0, "then"): "sum 이 3000원이다"})
    with patch.object(oneshot, "ask", lambda store, role, system, text, *r: (
            heard.append((role, text)) or (answer(redone) if role == "design" else fenced("oneshot-brief", good())),
            {"cost_usd": 0.1}, False)):
        oneshot_spec.redesign("budget", {"P1": "응답을 sum 으로"}, ["F1", "P1", "T1"])
    assert MARKER not in heard[2][1] and "응답을 sum 으로" in heard[2][1]   # the owner's own comment still goes
    assert set(o()["brief"]["r1"]["lines"]) == {"R3", "A1", "P1", "T1", "F1"}   # the marked items join the lines


def test_a_brief_refused_twice_or_a_broken_turn_leaves_the_sheet_and_never_stops_the_run(world):  # noqa: F811 — the imported fixture, by its name
    at_design()
    with replies(answer(), "요약 없음", "요약 없음"):
        oneshot_spec.design("budget")
    r1 = o()["brief"]["r1"]
    assert "두 번 거부" in r1["failed"] and o()["stage"] == "r1" and not o()["working"] and not o().get("stopped")
    assert o()["spend"]["calls"] == 3

    at_design()

    def broken(store, role, *rest):
        if role == "brief":
            raise oneshot.Failed("끊겼다", {})
        return answer(), {"cost_usd": 0.1}, False

    with patch.object(oneshot, "ask", broken):
        oneshot_spec.design("budget")
    assert "요약 턴이 실패" in o()["brief"]["r1"]["failed"] and not o().get("stopped") and not o()["working"]


def test_a_brief_for_a_gate_that_changed_meanwhile_is_dropped(world):  # noqa: F811 — the imported fixture, by its name
    at_design()

    def ask(store, role, system, text, *rest):
        if role == "design":
            return answer(), {"cost_usd": 0.1}, False
        oneshot_spec.saved("budget", oneshot={"profile": {**o()["profile"], "visibility": "public"}})
        return fenced("oneshot-brief", good()), {"cost_usd": 0.1}, False

    with patch.object(oneshot, "ask", ask):
        oneshot_spec.design("budget")
    assert "r1" not in (o().get("brief") or {}) and not o()["working"]


def test_the_snapshot_changes_with_the_gate_and_not_with_spend_or_reading(world):  # noqa: F811 — the imported fixture, by its name
    at_design()
    oneshot_spec.saved("budget", oneshot={"stage": "r1", "design": sheet.parsed(DESIGN),
                                          "r1": {"round": 1, "decisions": {}, "marks": {}}})
    first = brief.source(brief.snapshot(oneshot_spec.spec_of("budget")))
    oneshot_spec.saved("budget", oneshot={"spend": {**o()["spend"], "usd": 9.0}, "reading": {"r1": [{"seconds": 3}]}})
    assert brief.source(brief.snapshot(oneshot_spec.spec_of("budget"))) == first
    oneshot_spec.saved("budget", oneshot={"profile": {**o()["profile"], "visibility": "public"}})
    assert brief.source(brief.snapshot(oneshot_spec.spec_of("budget"))) != first


def test_a_server_that_died_writing_the_brief_leaves_it_failed_and_the_sheet_resumable(world):  # noqa: F811 — the imported fixture, by its name
    at_design()
    oneshot_spec.saved("budget", oneshot={"stage": "r1", "design": sheet.parsed(DESIGN), "working": "brief",
                                          "inflight": {"role": "brief", "codex": False, "sent": 1.0},
                                          "r1": {"round": 1, "decisions": {}, "marks": {}}})
    oneshot_spec.recover()
    assert "다시 켜져" in o()["brief"]["r1"]["failed"] and o()["stopped"]["reason"] == "restart"
    assert o()["inflight"] is None and o()["spend"]["calls"] == 1
    oneshot_spec.resume("budget")
    assert not o().get("stopped") and o()["stage"] == "r1"


class Flaky:
    """The run's store, whose next `failures` saves of a charge with `inflight` cleared fail before writing."""

    def __init__(self, failures: list[int]):
        self.real, self.failures = oneshot_spec.store("budget"), failures
        self.key, self.folder = self.real.key, self.real.folder

    def get(self):
        return self.real.get()

    def put(self, **fields):
        if self.failures[0] and "spend" in fields and fields.get("inflight", "absent") is None:
            self.failures[0] -= 1
            raise OSError("비용을 저장하지 못했다")
        return self.real.put(**fields)

    def stop(self, reason, detail):   # the worker's guard; this save succeeds
        return self.real.stop(reason, detail)


@pytest.mark.parametrize("answered", [True, False])   # a finished turn, and a broken one
def test_a_charge_that_could_not_be_saved_is_settled_once_before_the_run_goes_idle(world, answered):  # noqa: F811
    at_design()
    oneshot_spec.saved("budget", oneshot={"stage": "r1", "design": sheet.parsed(DESIGN), "working": "brief",
                                          "r1": {"round": 1, "decisions": {}, "marks": {}}})

    def ask(*a):
        if not answered:
            raise oneshot.Failed("끊겼다", {"cost_usd": 0.2})
        return fenced("oneshot-brief", good()), {"cost_usd": 0.1}, False

    with patch.object(oneshot, "ask", ask):
        brief.write(Flaky([1]), lambda: oneshot_spec.spec_of("budget"), lambda **f: oneshot_spec.saved("budget", oneshot=f))
    assert "비용을 저장하지 못했다" in o()["brief"]["r1"]["failed"]
    assert o()["spend"]["calls"] == 1 and o()["spend"]["usd"] == (0.1 if answered else 0.2)
    assert o().get("inflight") is None and not o()["working"] and not o().get("stopped")
    oneshot_spec.recover()   # nothing left in flight to charge again
    assert o()["spend"]["calls"] == 1 and not o().get("stopped")
    items = sheet.decidable(o()["design"], ASSUMED)
    oneshot_spec.decide("budget", oneshot_spec.Decisions(decisions={i: {"approve": True} for i in items}))
    assert o()["stage"] == "tests" and o()["spend"]["calls"] == 1


@pytest.mark.parametrize("repair", ["resume", "restart"])
def test_a_run_stopped_after_both_charge_saves_failed_settles_its_turn_once_and_resumes(world, repair):  # noqa: F811
    at_design()
    store = Flaky([2])

    def run():
        oneshot_spec.saved("budget", oneshot={"stage": "r1", "design": sheet.parsed(DESIGN),
                                              "r1": {"round": 1, "decisions": {}, "marks": {}}})
        brief.write(store, lambda: oneshot_spec.spec_of("budget"), store.put)

    with replies(fenced("oneshot-brief", good())):
        oneshot.start_worker(store, "brief", run, (), {})
        oneshot._workers[store.key].join(10)
    assert o()["stopped"]["reason"] == "block" and o()["inflight"]["role"] == "brief" and o()["spend"]["calls"] == 0
    if repair == "resume":
        oneshot_spec.resume("budget")
    else:
        oneshot_spec.recover()
        assert o()["inflight"] is None and o()["stopped"]["reason"] == "block"
        oneshot_spec.resume("budget")
    assert o().get("inflight") is None and o()["spend"]["calls"] == 1 and not o().get("stopped") and o()["stage"] == "r1"
    oneshot_spec.recover()   # nothing charged twice
    assert o()["spend"]["calls"] == 1


def snap():
    return {"sentence": "가계부", "restated": "가계부를 만든다.", "requirements": ASSUMED, "glossary": GLOSSARY,
            "stack": [], "design": sheet.parsed(DESIGN), "profile": {}, "round": 1, "marks": {}}


@pytest.mark.parametrize("value, said", [
    (good(confirm=[{"key": "R3", "text": "윈도우판도 만듭니다."}]), "빠졌다: A1"),
    (good(confirm=[*good()["confirm"], {"key": "R1", "text": "합계."}]), "정하지 않은 키다: R1"),
    (good(confirm=[*good()["confirm"], {"key": "A1", "text": "다시."}]), "두 번 나온다: A1"),
    (good(sections=good()["sections"][1:]), "빠진 섹션이 있다: 한 줄"),
    (good(sections=good()["sections"][::-1]), "이 순서로"),
    (good(sections=[s for s in good()["sections"] if s["name"] != "빼기로 한 것"]), "빠진 요구가 있다: R4"),
    (good(sections=[{**s, "text": s["text"] + " (Z9, P7)"} if s["name"] == "한 줄" else s for s in good()["sections"]]),
     "없는 번호를 댔다: P7"),
    (good(confirm=[{"key": "R3", "text": "`POST /spendings` 를 부릅니다."}, good()["confirm"][1]]), "엔드포인트 경로"),
    (good(confirm=[{"key": "R3", "text": "지출을 /spendings 경로로 보냅니다."}, good()["confirm"][1]]), "엔드포인트 경로 /spendings"),
    (good(sections=[{**s, "text": "요약입니다. (S999)"} if s["name"] == "한 줄" else s for s in good()["sections"]]),
     "없는 번호를 댔다: S999"),
    (good(confirm=[{"key": "R3", "text": "test_spending_adds 로 봅니다."}, good()["confirm"][1]]), "테스트 이름"),
    (good(sections=[{**s, "text": "가" * 1_600} if s["name"] == "쓰는 장면" else s for s in good()["sections"]]), "너무 길다"),
    ({"sections": "한 줄", "confirm": []}, "객체다"),
])
def test_each_brief_check_refuses(value, said):
    with pytest.raises(ValueError) as refused:
        brief.checked(value, snap(), {"R3": ["R3"], "A1": ["A1"]}, 1)
    assert said in str(refused.value)


def test_a_whole_brief_past_the_ceiling_is_refused_and_a_phone_name_in_parentheses_is_no_id():
    long = good(sections=[{**s, "text": "가" * 1_400} for s in good()["sections"]])
    with pytest.raises(ValueError, match="4000자 안으로"):
        brief.checked(long, snap(), {"R3": ["R3"], "A1": ["A1"]}, 1)
    phone = good(sections=[{**s, "text": "갤럭시에서 씁니다. (갤럭시 S21)"} if s["name"] == "한 줄" else s
                           for s in good()["sections"]])
    assert brief.checked(phone, snap(), {"R3": ["R3"], "A1": ["A1"]}, 1)["sections"][0]["text"].endswith("S21)")
    paper = good(sections=[{**s, "text": "용지에 맞춰 출력합니다. (A4 용지)"} if s["name"] == "한 줄" else s
                           for s in good()["sections"]])
    assert brief.checked(paper, snap(), {"R3": ["R3"], "A1": ["A1"]}, 1)   # a paper size in words, not item A4


def test_the_writer_runs_with_no_tools_on_either_provider(world):  # noqa: F811 — the imported fixture, by its name
    opened = []

    class Chat:
        def __init__(self, where, **kwargs):
            opened.append(kwargs)
            self.is_codex = kwargs["model"].startswith("codex:")

        def say(self, *a):
            yield type("E", (), {"kind": "done", "text": fenced("oneshot-brief", good()), "meta": {"cost_usd": 0.1}})()

        def close(self):
            opened.append("closed")

    store = oneshot_spec.store("budget")
    for model in ("codex:gpt-x", "opus"):
        with patch.object(oneshot, "ChatSession", Chat), \
             patch.object(oneshot, "role_config", lambda role="", store=None, m=model: {"model": m, "effort": "high"}):
            assert "failed" not in brief.asked(store, snap(), {"R3": ["R3"], "A1": ["A1"]})
    assert [(k["tools"], k["isolated"]) for k in opened if k != "closed"] == [("", True), ("", True)]
    assert opened.count("closed") == 2


def test_a_failure_reading_the_spec_for_the_brief_leaves_it_failed_and_never_stops_the_run(world):  # noqa: F811
    at_design()
    oneshot_spec.saved("budget", oneshot={"stage": "r1", "design": sheet.parsed(DESIGN), "working": "brief",
                                          "r1": {"round": 1, "decisions": {}, "marks": {}}})
    reads = []

    def flaky():
        reads.append(1)
        if len(reads) == 1:
            raise OSError("잠깐 읽지 못했다")
        return oneshot_spec.spec_of("budget")

    brief.write(oneshot_spec.store("budget"), flaky, lambda **f: oneshot_spec.saved("budget", oneshot=f))
    assert "잠깐 읽지 못했다" in o()["brief"]["r1"]["failed"] and not o()["working"] and not o().get("stopped")


def test_translationese_sends_the_first_answer_back_and_is_only_noted_on_the_second():
    awkward = good(confirm=[{"key": "R3", "text": "윈도우판에 대해 만듭니다."}, good()["confirm"][1]])
    with pytest.raises(ValueError, match="번역체"):
        brief.checked(awkward, snap(), {"R3": ["R3"], "A1": ["A1"]}, 0)
    assert brief.checked(awkward, snap(), {"R3": ["R3"], "A1": ["A1"]}, 1)["warnings"]


def test_the_brief_runs_on_the_review_model():
    assert oneshot.GROUPS["brief"] == "review"
    store = type("S", (), {"get": lambda self: {"models": {"review": {"model": "codex:gpt-x", "effort": "high"}}}})()
    assert oneshot.role_config("brief", store)["model"] == "codex:gpt-x"
    empty = type("S", (), {"get": lambda self: {"models": {"review": {"model": "", "effort": ""}}}})()
    with patch.dict(oneshot.ROLES, {"review": lambda: {"model": "review-default", "effort": "high"}}):
        assert oneshot.role_config("brief", empty)["model"] == "review-default"
    assert json.loads(json.dumps(brief.lines(snap()))) == {"R3": ["R3"], "A1": ["A1"]}
