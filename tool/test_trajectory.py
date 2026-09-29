"""Only whether the injection record scores the previous turn correctly."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trajectory  # noqa: E402


def wiki() -> Path:
    return Path(tempfile.mkdtemp()) / ".wiki"


def say(root: Path, prompt: str, session: str = "s1", injected=None) -> None:
    trajectory.record(root, prompt, injected or [], 100, session)


def test_in_one_session_the_next_utterance_scores_the_turn_before():
    root = wiki()
    say(root, "주석 좀 고쳐줘")
    say(root, "아니, 그게 아니라 줄바꿈을 말한 거다")
    rows = trajectory.rows(root)
    assert len(rows) == 2, rows
    assert "prev" not in rows[0], rows[0]
    assert rows[1]["prev"] == "교정", rows[1]


def test_an_ordinary_next_utterance_is_ok():
    root = wiki()
    say(root, "주석 좀 고쳐줘")
    say(root, "이제 커밋해라")
    assert trajectory.rows(root)[1]["prev"] == "ok"


def test_a_different_session_does_not_score_the_previous_line():
    root = wiki()
    say(root, "주석 좀 고쳐줘", session="s1")
    say(root, "아니, 틀렸다", session="s2")
    assert "prev" not in trajectory.rows(root)[1]


def test_without_a_session_id_nothing_is_joined():
    # Some environments send no `session_id` in the hook input. Treating two
    # empty strings as equal joins separate sessions into one line and
    # manufactures a causality that was never there.
    root = wiki()
    say(root, "주석 좀 고쳐줘", session="")
    say(root, "아니, 틀렸다", session="")
    assert "prev" not in trajectory.rows(root)[1]


def test_resume_inside_a_long_instruction_is_not_a_request_to_resume():
    root = wiki()
    say(root, "첫 턴")
    say(root, "이어서 진행해라. " + "그리고 다음 항목도 처리하고 보고해라. " * 6)
    assert trajectory.rows(root)[1]["prev"] == "ok"

    short = wiki()
    say(short, "첫 턴")
    say(short, "이어서 해라")
    assert trajectory.rows(short)[1]["prev"] == "재개요구"


def test_a_turn_that_matched_nothing_is_recorded_too():
    # What was not carried is as much evidence as what was. It stays, as an
    # empty list.
    root = wiki()
    say(root, "아무 규칙도 안 걸리는 말")
    assert trajectory.rows(root)[0]["injected"] == []


def test_the_utterance_is_cut_and_its_real_length_is_kept():
    root = wiki()
    long = "가" * (trajectory.KEEP + 200)
    say(root, long)
    row = trajectory.rows(root)[0]
    assert len(row["utterance"]) == trajectory.KEEP
    assert row["chars"] == trajectory.KEEP + 200


def test_the_stream_stays_out_of_git_on_one_ignore_line():
    root = wiki()
    say(root, "첫 턴")
    say(root, "둘째 턴")
    lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines.count(trajectory.FILENAME) == 1, lines


def test_a_broken_line_does_not_hide_the_one_before_it():
    # Only the tail is read, so the first line can arrive cut in half.
    # Stopping at that fragment is not allowed.
    root = wiki()
    say(root, "첫 턴")
    with trajectory.path_for(root).open("a", encoding="utf-8") as handle:
        handle.write('{"at": "잘린 조\n')
    say(root, "아니, 틀렸다")
    assert trajectory.rows(root)[-1]["prev"] == "교정"


def test_an_unwritable_path_neither_kills_it_nor_passes_silently():
    # A hook never stops the session, whatever happens —
    # `craft/hooks-fail-open`. Failing silently, though, leaves it looking
    # like it runs when it does not, so the name of the failure comes back.
    assert trajectory.record(None, "아무 말", [], 0, "s1") is None
    blocked = Path(tempfile.mkdtemp()) / "파일"
    blocked.write_text("나는 폴더가 아니다", encoding="utf-8")
    assert trajectory.record(blocked / ".wiki", "아무 말", [], 0, "s1")
