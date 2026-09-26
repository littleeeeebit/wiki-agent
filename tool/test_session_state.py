"""`session_state.plans` finds open plans in series folders and skips `done/`."""

from pathlib import Path

import session_state

TABLE = "# plan\n\n## 단계\n\n| # | 단계 | 무엇 | 상태 |\n| --- | --- | --- | --- |\n| 1 | a | b | {state} |\n"


def write(root: Path, relative: str, state: str) -> None:
    path = root / "docs" / "plans" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TABLE.format(state=state), encoding="utf-8")


def test_series_folders_are_read_and_done_is_skipped(tmp_path):
    write(tmp_path, "loop/0-overview.md", "미착수")
    write(tmp_path, "done/old/0-overview.md", "미착수")
    write(tmp_path, "flat-plan.md", "미착수")

    found = [p.relative_to(tmp_path / "docs" / "plans").as_posix() for p, _ in session_state.plans(tmp_path)]

    assert "done/old/0-overview.md" not in found
    assert found == ["loop/0-overview.md", "flat-plan.md"]


def test_a_status_is_judged_by_its_first_word(tmp_path):
    """`완료 — 양 호스트 실측` is done; `미완료 — …` is not, so no prefix match."""

    path = tmp_path / "plan.md"
    rows = ["완료 — 양 호스트 실측", "취소 — 다른 계획으로", "미완료 — 외부 조건으로 차단됨", "미착수"]
    path.write_text(
        "# plan\n\n## 단계\n\n| # | 단계 | 무엇 | 상태 |\n| --- | --- | --- | --- |\n"
        + "".join(f"| {n} | s{n} | w{n} | {state} |\n" for n, state in enumerate(rows, 1)),
        encoding="utf-8",
    )

    assert session_state.open_steps(path) == ["3 w3 — 미완료 — 외부 조건으로 차단됨", "4 w4 — 미착수"]


def test_an_english_plan_reads_the_same(tmp_path):
    path = tmp_path / "plan.md"
    rows = ["Done — PR #16", "Cancelled — another plan", "Complete", "In progress", "Not started"]
    path.write_text(
        "# plan\n\n## Steps\n\n| # | Step | What | Status |\n| --- | --- | --- | --- |\n"
        + "".join(f"| {n} | s{n} | w{n} | {state} |\n" for n, state in enumerate(rows, 1)),
        encoding="utf-8",
    )

    assert session_state.open_steps(path) == ["4 w4 — In progress", "5 w5 — Not started"]


def test_inside_a_series_the_lowest_number_comes_first(tmp_path):
    """The overview and the next step to do, not the last two steps."""

    write(tmp_path, "a/0-overview.md", "미착수")
    write(tmp_path, "a/1-x.md", "완료")
    write(tmp_path, "a/2-y.md", "미착수")
    write(tmp_path, "a/7-z.md", "미착수")

    found = [p.relative_to(tmp_path / "docs" / "plans").as_posix() for p, _ in session_state.plans(tmp_path)]

    assert found == ["a/0-overview.md", "a/2-y.md"]


def test_english_plan_statuses_and_discovery(tmp_path):
    path = tmp_path / "docs/plans/jev/0-overview.md"
    path.parent.mkdir(parents=True)
    states = ["Complete — PR #1", "Cancelled — replaced", "Not completed", "Not started", ""]
    path.write_text(
        "# Plan\n\n## Steps\n\n| # | Stage | Deliverable | Status |\n| --- | --- | --- | --- |\n"
        + "".join(f"| {n} | stage | item{n} | {state} |\n" for n, state in enumerate(states, 1))
        + "\n## Notes\n\n| 6 | ignored | outside | Not started |\n",
        encoding="utf-8",
    )
    expected = ["3 item3 — Not completed", "4 item4 — Not started", "5 item5 — Not started"]
    assert session_state.open_steps(path) == expected
    assert session_state.plans(tmp_path) == [(path, expected)]
