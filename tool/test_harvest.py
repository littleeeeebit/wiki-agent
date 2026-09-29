"""Harvesting does not wobble on line endings and never overwrites a record."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.stdout.reconfigure(encoding="utf-8")

from harvest import NO_WHAT, NO_WHY, record, triggers_for  # noqa: E402 -- the reconfigure above runs first

BODY = """## 왜 셀을 지우면 안 됐나

`defer` 대조군이 혼합 문서입니다.

## 무엇을 바꿨나

`_replay` 가 `defer` 대조군만 다르게 비교합니다.
"""

PR = {"number": 95, "mergedAt": "2026-09-06T00:00:00Z", "title": "제목",
      "headRefName": "feat/branch"}


def _record(body: str) -> str:
    return record({**PR, "body": body}, 0)[1]   # a spent deadline: no translation


def test_a_crlf_body_produces_the_same_record_as_an_lf_one() -> None:
    """GitHub returns CRLF, and the paragraph split is on `\\n\\n`.

    Without normalisation the whole body is one block: `왜` is always empty
    and `무엇` swallows a truncated copy of everything. PR #95 came out that
    way.
    """

    lf = _record(BODY)
    crlf = _record(BODY.replace("\n", "\r\n"))
    assert lf == crlf, "줄끝만 다른 같은 본문이 다른 기록을 냈다"


def test_a_body_that_has_a_reason_is_not_recorded_as_having_none() -> None:
    for body in (BODY, BODY.replace("\n", "\r\n")):
        assert NO_WHY not in _record(body)
        assert "혼합 문서" in _record(body)


def test_a_body_with_genuinely_no_reason_is_recorded_as_having_none() -> None:
    """The other half: it must not invent a reason that is not there."""
    assert NO_WHY in _record("한 문단뿐인 본문.")


def _what(body: str) -> str:
    line = next(ln for ln in _record(body).splitlines() if ln.startswith("What."))
    return line[len("What."):].strip()


def test_a_markdown_heading_does_not_land_inside_the_what() -> None:
    """Records 131 to 135 on 2026-09-10 all read `무엇. ## 결론`.

    Almost every PR body here opens with a markdown heading, so taking the
    first block as it stands puts the heading in the record.
    """

    for body in (BODY, BODY.replace("\n", "\r\n")):
        what = _what(body)
        assert not what.startswith("#"), f"제목이 그대로 들어갔다: {what!r}"
        assert "대조군" in what, f"제목 뒤의 산문이 안 왔다: {what!r}"


def test_prose_is_found_with_no_blank_line_between_heading_and_body() -> None:
    """Why only the heading *line* is stripped, not the block.

    With no blank line the heading and the body are one block, and dropping
    the block would take the body with it.
    """

    assert _what("## 결론\n붙어 있는 본문입니다.\n\n둘째 문단.") == "붙어 있는 본문입니다."


def test_a_body_of_nothing_but_headings_records_no_summary() -> None:
    """With no prose left after the headings are stripped, it invents none."""
    assert NO_WHAT in _record("## 제목뿐\n\n### 또 제목뿐")


def test_the_record_is_written_in_english_and_its_domain_read_from_the_original() -> None:
    """The wiki is read in English; the triggers match what a person types."""

    def english(texts, direction, deadline):
        assert direction == "ko->en"
        return [f"EN({t})" if t else t for t in texts]

    with patch("translate.translate", english):
        _, text = record({**PR, "title": "화면 공유가 멈춘다", "body": BODY}, 0)
    assert 'title: "EN(화면 공유가 멈춘다)"' in text and "# EN(화면 공유가 멈춘다)" in text
    assert "What. EN(" in text and "Why. EN(" in text and "Source. PR #95 · `feat/branch`" in text
    assert "domain: vision" in text and '"화면"' in text, "도메인과 트리거는 한국어 원문에서"


def test_a_short_ascii_marker_does_not_match_inside_a_word() -> None:
    """`ci` in `de-ci-sion`, `turn` in `re-turn`, `frame` in `frame-work`.

    A domain brings triggers with it and `inject.py` injects anything with
    triggers like a rule, so a misclassification surfaces that record in every
    unrelated session. Record 131 of 2026-09-10 came out that way; it was taken
    down by hand and the markers were left as they were.
    """

    for branch, title in (("chore/decision-records-126-130", "결정 기록을 캡니다"),
                          ("fix/early-return", "이른 반환을 고칩니다"),
                          ("feat/framework-upgrade", "의존성을 올립니다")):
        domain, triggers = triggers_for(title, branch)
        assert domain == "", f"{branch} 가 {domain!r} 로 걸렸다"
        assert triggers == []


def test_a_korean_marker_still_matches_inside_a_compound() -> None:
    """Deliberate, and the limit of the narrowing above.

    A Korean marker routinely appears as a compound with a particle attached,
    which a regex word boundary does not see. Requiring one would drop markers
    that really are standing as words, so containment is kept here.
    """

    assert triggers_for("프레임워크를 올립니다", "chore/deps")[0] == "vision"


def test_a_marker_standing_as_a_word_still_matches() -> None:
    """Getting stricter must not start missing the real positives."""
    assert triggers_for("CI 를 붙입니다", "feat/ci-pipeline")[0] == "infra"
    assert triggers_for("프레임 동기화", "feat/frame-sync")[0] == "vision"
    assert triggers_for("턴 조립", "feat/turn-assembly")[0] == "api"
    assert triggers_for("스키마를 올립니다", "chore/migration")[0] == "infra"
    assert triggers_for("런처를 고칩니다", "fix/launcher")[0] == "infra"


def test_a_handwritten_record_is_seen_with_the_number_only_in_its_name() -> None:
    """Records written by hand follow the naming rule without a `pr:` line.

    Reading only `pr:` misses them, and on 2026-09-17 records 013, 015 and 016
    were overwritten through that gap.
    """

    import sync

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / ".wiki" / "decisions"
        out.mkdir(parents=True)
        (out / "2026-09-17-013-docs-run-archive.md").write_text(
            '---\nscope: project\ntitle: "손으로 쓴 전문"\n---\n', encoding="utf-8")
        (out / "2026-09-17-012-feat-x.md").write_text("---\npr: 12\n---\n", encoding="utf-8")
        (out / "2026-09-18-run-colab-1789.md").write_text("---\nscope: project\n---\n",
                                                          encoding="utf-8")
        assert sync.recorded(Path(tmp)) == {12, 13}, "이름의 번호와 pr: 줄을 둘 다 읽어야 한다"


def test_an_existing_decision_file_is_never_overwritten() -> None:
    """The last stop if the number check is ever wrong again.

    A harvested record is a PR body squeezed into shape; what sits at the same
    path may be the full text somebody wrote by hand.
    """

    import sync

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        out = repo / ".wiki" / "decisions"
        out.mkdir(parents=True)
        kept = out / "2026-09-17-013-docs-run-archive.md"
        kept.write_text("사람이 쓴 전문\n", encoding="utf-8")
        pr = {"number": 13, "title": "docs: archive", "body": "본문",
              "mergedAt": "2026-09-17T00:00:00Z", "headRefName": "docs/run-archive"}
        with patch.object(sync.harvest, "prs", return_value=[pr]), \
                patch.object(sync, "recorded", return_value=set()):
            written = sync.new_decisions(repo, 10)
        assert written == [], "있는 파일을 덮으려 했다"
        assert kept.read_text(encoding="utf-8") == "사람이 쓴 전문\n"
