"""Focused checks split from tool/test_loop.py."""

import json
import pytest
from main import loop
from test_loop import meta


def test_the_parser_checks_the_round_the_head_and_the_last_line():
    head = "abcdef0123456789abcdef0123456789abcdef01"
    good = ("# Round 3 · PR #12 · abcdef0\n\n[P1] tool/x.py:14 — 틀렸다\n재현: 이렇게\n"
            "[P2] tool/y.py:2 — 이름\n\n**머지 불가 — P1 하나**")
    parsed = loop.parse(good, 3, 12, head)
    assert parsed["verdict"] == "deny" and parsed["counts"] == {"P0": 0, "P1": 1, "P2": 1}
    assert parsed["findings"][0]["file"] == "tool/x.py" and parsed["findings"][0]["body"] == "재현: 이렇게"
    assert loop.parse("Round 1 · PR #12 · abcdef0\n새 발견 없음\n머지 허용", 1, 12, head)["verdict"] == "allow"
    for text, why in ((good.replace("Round 3", "Round 2"), "라운드 번호"),
                      (good.replace("abcdef0", "1234567"), "머리 커밋"),
                      (good.replace("PR #12", "PR #13"), "PR 번호"),
                      (good.rsplit("\n", 1)[0], "마지막 줄"),
                      ("여기 결과다\n" + good, "첫 줄"),
                      (good.replace("머지 불가", "머지해도 된다"), "마지막 줄")):
        with pytest.raises(ValueError, match=why):
            loop.parse(text, 3, 12, head)


def test_finding_meta_is_checked_against_the_ids_the_spec_has():
    head = "abcdef0123456789abcdef0123456789abcdef01"

    def text(block):
        return f"Round 1 · PR #12 · abcdef0\n[P1] a.py:1 — x\n[P2] b.py:2 — y\n{block}\n머지 불가 — P1"

    parsed = loop.parse(text(meta(("loop.merge", "gate on head"), ("ui", "label", "F1"))), 1, 12, head, {"F1"})
    assert parsed["identity"] == "full" and parsed["findings"][1]["meta"]["existing_id"] == "F1"
    assert parsed["findings"][1]["body"] == "", "블록은 발견의 본문이 아니다"
    for block, why in ((meta(("a", "b"), ("c", "d", "F2")), "F2"), (meta(("a", "b"), ("c", "d", 1)), "existing_id"),
                       (meta(("a", "b")), "ordinal"), ("```finding-meta\n{not json\n```", "JSON"),
                       (meta(("a", " "), ("c", "d")), "invariant"),
                       ("```finding-meta\n{not json", "닫히지 않았다")):
        with pytest.raises(ValueError, match=why):
            loop.parse(text(block), 1, 12, head, {"F1"})
    legacy = loop.parse(text(""), 1, 12, head)
    assert legacy["identity"] == "limited" and "meta" not in legacy["findings"][0], "블록 없는 답도 읽는다"
    entries = [{"ordinal": 1, "component": "loop.merge", "invariant": "gate on head", "existing_id": "F1"},
               {"ordinal": 2, "limited": True}]
    partial = loop.parse(text("```finding-meta\n" + json.dumps(entries) + "\n```"), 1, 12, head, {"F1"})
    assert partial["identity"] == "limited" and partial["findings"][0]["meta"]["existing_id"] == "F1"
    assert "meta" not in partial["findings"][1]
    for entry in ({"ordinal": 2, "limited": False}, {"ordinal": 2, "limited": 1},
                  {"ordinal": 2, "limited": True, "existing_id": "F1"}):
        with pytest.raises(ValueError, match="limited"):
            loop.parse(text("```finding-meta\n" + json.dumps([entries[0], entry]) + "\n```"), 1, 12, head, {"F1"})
    assert loop.parse("Round 1 · PR #12 · abcdef0\n새 발견 없음\n머지 허용", 1, 12, head)["identity"] == "full"


def test_ids_follow_the_validated_id_then_the_invariant_and_flag_a_near_miss():
    def found(*metas):
        return [{"grade": "P1", "file": "a.py", "line": i, "head": f"[P1] a.py:{i} — x", "body": "", "meta": m}
                for i, m in enumerate(metas)]

    def m(component, invariant, existing=None):
        return {"component": component, "invariant": invariant, "existing_id": existing}

    spec = {"rounds": []}
    spec["rounds"].append({"n": 1, "items": loop.identified(spec, found(m("loop.merge", "Final gate matches HEAD")))})
    again = loop.identified(spec, found(m("Loop.merge", " final gate  matches head"), m("loop.merge", "base read now"),
                                        m("ui", "other words", "F1")))
    assert [f["id"] for f in again] == ["F1", "F2", "F1"], "같은 불변식은 같은 id, 검증된 existing_id 가 먼저"
    assert again[1]["possible"] == "F1" and again[0]["possible"] is None
    spec["rounds"].append({"n": 2, "items": again})
    assert loop.seen(spec) == {"F1": [1, 2], "F2": [2]}
    unnamed = loop.identified(spec, [{**found(m("a", "b"))[0], "meta": None}])
    assert unnamed[0]["id"] is None, "meta 없는 발견은 id 를 지어내지 않는다"


def test_a_disposition_id_counts_only_when_this_round_gave_it_to_that_finding():
    """PR #56 round 1: an id the fixing side made up, repeated over two
    rounds, stopped the loop for a dispute between unrelated findings."""

    def said(finding, fid):
        return loop.disposed("```disposition\n" + json.dumps(
            [{"finding": finding, "id": fid, "action": "disagree"}]) + "\n```")

    items = [{"id": "F1", "grade": "P1", "head": "[P1] a.py:1 — issue A"},
             {"id": "F2", "grade": "P1", "head": "[P1] b.py:70 — issue B"}]
    before = loop.vouched(said("[P1] a.py:1 — issue A", "F999"), items[:1])
    now = loop.vouched(said("[P1] b.py:70 — unrelated issue B", "F999"), items[1:])
    assert before[0]["id"] is None and now[0]["id"] is None, "준 적 없는 id 는 버린다"
    assert loop.disputed(before, now) == "", "다른 발견은 반론 반복이 아니다"
    assert loop.vouched(said("[P1] b.py:70 — issue B", "F1"), items)[0]["id"] is None, "다른 발견의 id"
    kept = loop.vouched(said("[P1] a.py:1 — reworded", "F1"), items)
    assert kept[0]["id"] == "F1" and loop.vouched(None, items) is None
    assert loop.disputed(kept, loop.vouched(said("[P1] a.py:9 — moved", "F1"), items)) == "[P1] a.py:9 — moved"
    # Round 2: two findings at one place. F2's own id stands, and a first
    # disagreement with F2 is not a repeat of the one with F1.
    shared = [{"id": "F1", "grade": "P1", "head": "[P1] a.py:1 — validate input"},
              {"id": "F2", "grade": "P1", "head": "[P1] a.py:1 — close connection"}]
    again = loop.vouched(said("[P1] a.py:1 — close connection", "F2"), shared)
    assert again[0]["id"] == "F2"
    assert loop.disputed(loop.vouched(said("[P1] a.py:1 — validate input", "F1"), shared), again) == ""
    misplaced = loop.vouched(said("[P1] a.py:1 — validate input", "F2"), shared)
    assert misplaced[0]["id"] is None
    completed = loop.settled(shared, misplaced)
    assert [f["disposition"] for f in completed] == ["disagree", None]
    ambiguous = loop.vouched(said("[P1] a.py:1 — reworded", None), shared)
    assert all(f["disposition"] is None for f in loop.settled(shared, ambiguous))

