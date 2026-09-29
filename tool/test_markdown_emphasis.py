"""What the emphasis hook has to catch, and what it must never fire on.

The second half matters as much as the first. A hook that blocks correct prose
gets switched off, and a hook that is off enforces nothing — so every rule here
has a companion test proving the nearest correct shape still passes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from markdown_emphasis import findings, verdict  # noqa: E402

HOOK = HERE / "markdown_emphasis.py"


def git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)

CLEAN = """# 제목

규칙. 훅은 세션을 멈추지 않는다. `sys.stdout.reconfigure` 로 인코딩을 고정한다.

어겼을 때. 훅이 조용히 사라진다. 안 도는 것보다 나쁜 것은 안 도는데 도는 줄
아는 것이다.

## 표도 산문이 아니다

| 무엇 | 왜 |
| --- | --- |
| **굵게** | 표 셀의 굵게는 라벨 노릇을 한다 |

```python
print("**이 안은 안 센다**")
```

여기서 하나만 **정말 중요한 것**을 짚는다. 나머지는 문장으로 세운다.
"""


def blocked(text: str, path: str = "docs/x.md", tool: str = "Write") -> str | None:
    key = "content" if tool == "Write" else "new_string"
    answer = verdict({"tool_name": tool, "tool_input": {"file_path": path, key: text}})
    return None if answer is None else (
        answer["hookSpecificOutput"]["permissionDecisionReason"]
    )


def test_a_document_that_uses_emphasis_once_passes() -> None:
    assert blocked(CLEAN) is None


def test_a_bolded_paragraph_label_is_refused() -> None:
    """This repo writes `규칙.` and `어겼을 때.` plain. Bolding the label puts
    the emphasis on the scaffolding instead of the content."""

    why = blocked(CLEAN.replace("규칙. 훅은", "**규칙.** 훅은"))
    assert why and "문단 라벨" in why


def test_bold_opening_a_wrapped_line_is_not_a_label() -> None:
    """This fired a false positive once.

    A bold run at the head of a wrapped line is ordinary mid-sentence emphasis.
    Counting it as a label blocks correct prose, and a hook that blocks correct
    prose gets switched off.
    """

    text = (
        "# 제목\n\n"
        "`stdout` 만 고치면 절반이다. `stdin` 이 깨지면 발화가 트리거에 안 맞아\n"
        "**죽지도 않고 아무 일도 안 한다.** 트레이스백조차 안 남는다.\n"
    )
    assert blocked(text) is None


def test_two_bolds_in_one_paragraph_are_refused() -> None:
    text = CLEAN.replace(
        "여기서 하나만 **정말 중요한 것**을 짚는다.",
        "여기서 **이것**과 **저것**을 짚는다.",
    )
    why = blocked(text)
    assert why and "한 문단에 굵게가 둘 이상" in why


def test_bold_holding_a_line_break_is_refused() -> None:
    text = CLEAN.replace(
        "여기서 하나만 **정말 중요한 것**을 짚는다.",
        "여기서 하나만 **정말\n중요한 것**을 짚는다.",
    )
    why = blocked(text)
    assert why and "줄바꿈을 품은" in why


def test_a_code_span_cannot_hide_the_line_break(tmp_path: Path) -> None:
    """A newline folded into a code span is still a newline inside the bold.

    `code_inline` content has its newlines replaced by spaces, so the token
    stream shows none — which is how `**`+"`a\\nb`"+`**` passed as a
    single-line bold, and how two bolds from different lines were reported as
    one crowded line. The block's own map says how many lines it covers, and
    the difference is what a code span swallowed.
    """

    tick = chr(96)
    hidden = "**" + tick + "code\nspan" + tick + "**"
    why = blocked(hidden, tool="Edit", path="docs/nowhere.md")
    assert why and "줄바꿈을 품은" in why

    apart = "**one** " + tick + "code\nspan" + tick + " **two**"
    why = blocked(apart, tool="Edit", path="docs/nowhere.md")
    assert why and "한 문단에 굵게가 둘 이상" in why

    # And the ordinary shape still passes: one bold per paragraph, no folding.
    assert blocked("**one** here\n\nand **two** there\n") is None


def test_an_inline_tag_is_not_part_of_the_label(tmp_path: Path) -> None:
    """The label is what the reader sees, so a tag's source is not in it.

    Joining every child's content put `<span>` into the text `LABEL` matched
    against, and a label wrapped in inline HTML stopped being a label in both
    the hook and lint.
    """

    import markdown_emphasis

    labelled = "**<span>규칙.</span>** 훅은 세션을 멈추지 않는다.\n"
    assert markdown_emphasis.scan(labelled)[2] == ["규칙."]
    assert blocked(CLEAN.replace("규칙. 훅은", "**<span>규칙.</span>** 훅은"))

    # A tag *before* the bold is a different question, and one the parse
    # cannot answer: whether the reader sees anything there. Three rounds
    # went into answering it anyway and each answer was wrong somewhere --
    # every tag invisible refused the first row, every tag visible let the
    # second through, the void list called the third visible when it draws
    # nothing. The token stream is identical in all of them.
    for tag in ("<img src=x>", "<span>", "<input type=hidden>", "<meta charset=x>",
                "<br>", "<span/>"):
        after = f"{tag} **규칙.** 훅은 세션을 멈추지 않는다.\n"
        assert markdown_emphasis.scan(after)[2] == [], tag

    # The block is only unreadable from the tag onward. A label before one
    # is still a label.
    both = "**규칙.** 훅은 <span>안</span> 멈춘다.\n"
    assert markdown_emphasis.scan(both)[2] == ["규칙."]


def test_a_character_reference_is_not_a_line_break() -> None:
    """`&#10;` puts a newline in decoded content that the source never had.

    Reading every token's content for `\\n` caught it and refused a one-line
    bold. What carries raw source is `html_inline`; an image's alt text is
    decoded, so its own break tokens are what say it crossed a line.
    """

    import markdown_emphasis

    for one_line in ("**a&#10;b** 뒤.", "**![a&#10;b](x)** 뒤.",
                     "**a&#xA;b** 뒤.", "**a&NewLine;b** 뒤."):
        assert markdown_emphasis.scan(one_line)[1] == 0, one_line
        assert findings(one_line, whole=False) == [], one_line

    # And the real thing still counts.
    assert markdown_emphasis.scan("**![alt\ntext](x)** 뒤.")[1] == 1


def test_a_code_span_outside_the_bold_does_not_convict_it(tmp_path: Path) -> None:
    """Folding is owned only by a bold that holds every code span in the block.

    The block knows how many lines vanished but not into which span. Charging
    that to any bold merely holding a code span refused correct prose — one
    bold with a one-line span, beside an unrelated span that wrapped.
    """

    tick = chr(96)
    elsewhere = ("**" + tick + "one" + tick + "** and "
                 + tick + "multi\nline" + tick)
    assert findings(elsewhere, whole=False) == []

    # The same block with no other span: now the folding can only be its own.
    inside = "**" + tick + "multi\nline" + tick + "**"
    assert any("줄바꿈을 품은" in line for line in findings(inside, whole=False))

    # A tag written across lines keeps its own newline, so it is counted
    # directly rather than left in the residue a code span is charged with.
    # Both faces were findings: the bold holding such a tag was missed, and a
    # one-line bold beside such a tag was convicted of the tag's break.
    holding = "**<span\nclass=x>규칙</span>** 이다."
    assert any("줄바꿈을 품은" in line for line in findings(holding, whole=False))

    beside = ("**" + tick + "one" + tick + "** <span\nclass=x>text</span>")
    assert findings(beside, whole=False) == []

    # Any token still carrying a newline, not one named type. Hanging the
    # verdict on `html_inline` while `kept` subtracted every such token meant
    # a multi-line image inside a bold was subtracted and then judged by
    # nothing at all.
    picture = "**![alt\ntext](x)** 뒤."
    assert any("줄바꿈을 품은" in line for line in findings(picture, whole=False))
    assert findings("**" + tick + "one" + tick + "** ![alt\ntext](x)",
                    whole=False) == []

    # A code span nested in alt text is still a code span. Counting only the
    # top level while the newline check recursed left its folded line owned by
    # nobody: the bold around it went free, and a bold beside it was charged
    # with a fold that was never its own.
    nested = "**![" + tick + "a\nb" + tick + "](x)** 뒤."
    assert any("줄바꿈을 품은" in line for line in findings(nested, whole=False))

    apart = "![" + tick + "a\nb" + tick + "](x) **" + tick + "one" + tick + "**"
    assert findings(apart, whole=False) == []


def test_fragments_are_not_stitched_into_a_paragraph(tmp_path: Path) -> None:
    """Two edits are two fragments; whatever separates them is not in the call.

    Joining a `MultiEdit`'s edits with a newline — and a patch's hunks the same
    way — built a paragraph that exists in no file, so two bolds landing in two
    different paragraphs read as one crowded paragraph and correct prose was
    refused. Each fragment is judged by itself; what they add up to is
    `lint.loud_emphasis`'s job, reading the real file afterwards.
    """

    apart = verdict({"tool_name": "MultiEdit", "tool_input": {
        "file_path": "docs/nowhere.md",
        "edits": [{"new_string": "첫 번째 문단의 **하나**다."},
                  {"new_string": "두 번째 문단의 **둘**이다."}]}})
    assert apart is None

    together = blocked("한 문단에 **하나**와 **둘**이 있다.\n",
                       path="docs/nowhere.md", tool="Edit")
    assert together and "한 문단에 굵게가 둘 이상" in together

    hunks = (
        "*** Begin Patch\n*** Update File: docs/x.md\n"
        "@@\n-옛 줄\n+첫 번째 문단의 **하나**다.\n"
        "@@\n-다른 옛 줄\n+두 번째 문단의 **둘**이다.\n*** End Patch"
    )
    assert verdict({"tool_name": "apply_patch", "tool_input": {"input": hunks}}) is None

    one_hunk = (
        "*** Begin Patch\n*** Update File: docs/x.md\n"
        "@@\n-옛 줄\n+한 문단에 **하나**와 **둘**이 있다.\n*** End Patch"
    )
    assert verdict({"tool_name": "apply_patch", "tool_input": {"input": one_hunk}})


def test_density_over_the_limit_is_refused() -> None:
    body = "\n\n".join(f"{n} 번째 문단이고 여기 **강조**가 있다." for n in range(9))
    why = blocked(f"# 제목\n\n{body}\n")
    assert why and "상한" in why


def test_density_under_the_limit_passes() -> None:
    plain = "\n\n".join(f"{n} 번째 문단이고 강조가 없다." for n in range(30))
    why = blocked(f"# 제목\n\n{plain}\n\n여기 하나만 **정말 중요한 것**이 있다.\n")
    assert why is None


def test_fences_and_tables_are_not_counted() -> None:
    """Asterisks inside a fence and bold in a table cell do not move the ratio."""

    fence = "```\n" + "\n".join("**x**" for _ in range(40)) + "\n```"
    # A real table, delimiter row and all. Without that row GFM reads these
    # lines as an ordinary paragraph — the reader sees literal pipes — and the
    # bolds in it are bolds. The line-based check this replaced skipped any
    # line opening with `|`, so a table-shaped paragraph hid its emphasis.
    rows = "| a | b |\n| --- | --- |\n" + "\n".join(
        "| **a** | **b** |" for _ in range(40))
    plain = "\n\n".join(f"{n} 번째 문단." for n in range(12))
    assert blocked(f"# 제목\n\n{plain}\n\n{fence}\n\n{rows}\n") is None


def test_a_fragment_is_judged_only_on_what_needs_no_context() -> None:
    """When the result cannot be rebuilt, only the context-free checks run.

    Density needs a whole document and a label needs to know a block begins
    there. Guessing either from a fragment refuses correct prose — a bold
    opening a wrapped line is ordinary emphasis, and a review round found
    exactly that. What survives is what holds on any line by itself.
    """

    # `docs/nowhere.md` does not exist, so no result can be built.
    missing = "docs/nowhere.md"
    assert blocked("**규칙.** 한 줄이다.\n", path=missing, tool="Edit") is None

    why = blocked("여기 **이것**과 **저것**이 있다.\n", path=missing, tool="Edit")
    assert why and "한 문단에 굵게가 둘 이상" in why


def test_a_patch_is_judged_at_its_destination() -> None:
    """`*** Move to:` decides where the result lands, so it decides the check.

    Reading only the source header let a patch rename `notes.txt` into a `.md`
    and skip the extension test on the way. The check keyed on where the file
    came from rather than where it was going.
    """

    patch = (
        "*** Begin Patch\n*** Update File: notes.txt\n*** Move to: docs/x.md\n@@\n"
        "-old\n+여기 **이것**과 **저것**이 있다.\n*** End Patch"
    )
    assert verdict({"tool_name": "apply_patch", "tool_input": {"input": patch}})


def test_a_new_file_in_a_patch_is_a_whole_document() -> None:
    """`Add File` carries everything the file will hold, so it is judged whole."""

    patch = (
        "*** Begin Patch\n*** Add File: docs/n.md\n@@\n"
        "+# 제목\n+\n+**규칙.** 라벨을 굵게\n*** End Patch"
    )
    answer = verdict({"tool_name": "apply_patch", "tool_input": {"input": patch}})
    assert answer and "문단 라벨" in answer["hookSpecificOutput"]["permissionDecisionReason"]


def test_what_a_fragment_could_push_over_the_limit_is_left_to_lint(
    tmp_path: Path,
) -> None:
    """The hook stopped predicting what an edit would leave behind.

    Three rounds of review found faces of that prediction — a second hunk,
    `replace_all`, a four-backtick fence — each fix reimplementing more of
    `git apply` inside a style hook. `lint.loud_emphasis` reads the file
    afterwards instead, where there is nothing to guess about. The hook letting
    a fragment through is only defensible because that check exists, so this
    test holds both halves at once.
    """

    import lint

    noisy = "# 제목\n\n" + "\n\n".join(f"{n} 번째 **강조**." for n in range(9)) + "\n"
    assert blocked(noisy, tool="Edit") is None, "조각으로는 비율을 안 본다"

    (tmp_path / "loud.md").write_text(noisy, encoding="utf-8")
    (tmp_path / "quiet.md").write_text(CLEAN, encoding="utf-8")
    found = lint.loud_emphasis(tmp_path)

    assert [where for _kind, where in found if "loud.md" in where], "lint 가 못 잡았다"
    assert not [where for _kind, where in found if "quiet.md" in where]


def test_the_check_reaches_a_target_repository(tmp_path: Path) -> None:
    """Left only on the hub, nobody looks in an installed repository.

    The hook lets a fragment through because a check reading the file follows
    it — and if that check runs only on the hub, the enforcement is entirely
    absent in a target repository. `repo_lint` is where `sync`'s Stop hook
    runs, which is why it belongs there.
    """

    import lint
    import repo_lint

    target, hub = tmp_path / "target", tmp_path / "hub"
    (target / ".wiki").mkdir(parents=True)
    hub.mkdir()
    (target / "x.md").write_text(
        "# 제목\n\n" + "\n\n".join(f"{n} 번째 **강조**." for n in range(9)) + "\n",
        encoding="utf-8",
    )

    assert [k for k, _ in repo_lint.check(target) if k == "강조 과다"]
    # An empty hub: whatever it finds is the target's.
    assert [k for k, _ in lint.check(hub, None, [target])[2] if k == "강조 과다"]


def test_the_scan_follows_git_rather_than_a_hand_written_exclusion_list(
    tmp_path: Path,
) -> None:
    """A hand-written exclusion list is the hub's circumstances, not another
    repository's.

    Filtering `web/`, `artifacts/` and `raw/` by name dropped real documents
    wholesale in a target repository, and took `node_modules-guide.md` with
    them for sharing a prefix. "Is this file ours" is a question git already
    answers.
    """

    import lint

    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("vendor/\n", encoding="utf-8")

    noisy = "# 제목\n\n" + "\n\n".join(f"{n} 번째 **강조**." for n in range(9)) + "\n"
    for rel in ("web/docs/x.md", "artifacts/x.md", "raw/x.md",
                "node_modules-guide.md", "vendor/skip.md"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(noisy, encoding="utf-8")
    git(tmp_path, "add", "-A")

    seen = {where.split("`")[1] for _kind, where in lint.loud_emphasis(tmp_path)}
    assert "web/docs/x.md" in seen and "node_modules-guide.md" in seen
    assert "vendor/skip.md" not in seen, "무시 대상까지 봤다"


def test_a_byte_pinned_original_is_not_judged_on_style(tmp_path: Path) -> None:
    """An original pinned by hash cannot be fixed, so it is not judged on style.

    Where competition material writes `if __name__ == "__main__":` without
    backticks, CommonMark reads both `__name__` and `__main__` as emphasis.
    Adding backticks makes that repository's own record — "the same SHA-256
    before and after the move" — false. So the finding has no fix and would
    sit in the health check forever.

    What decides it is the hash that repository actually wrote down, not a
    name. Removing the pin and checking the same file comes back as a finding
    is part of this: had it been filtered by name, that step would stay green.
    """

    import hashlib

    import lint

    git(tmp_path, "init", "-q")

    verbatim = ('# 원문\r\n\r\n'
                'A. 실행 코드를 if __name__ == "__main__": 아래에 둡니다.\r\n')
    original = tmp_path / "archive" / "원문.md"
    original.parent.mkdir(parents=True)
    original.write_bytes(verbatim.encode("utf-8"))
    digest = hashlib.sha256(original.read_bytes()).hexdigest()

    noisy = "# 제목\n\n" + "\n\n".join(f"{n} 번째 **강조**." for n in range(9)) + "\n"
    (tmp_path / "mine.md").write_text(noisy, encoding="utf-8")
    index = tmp_path / "archive" / "README.md"
    index.write_text(f"# 보관\n\n| 파일 | 동일한 SHA-256 |\n| --- | --- |\n"
                     f"| 원문.md | `{digest}` |\n", encoding="utf-8")
    git(tmp_path, "add", "-A")

    seen = {where.split("`")[1] for _kind, where in lint.loud_emphasis(tmp_path)}
    assert "archive/원문.md" not in seen, "못 박은 원문에 고칠 수 없는 발견을 냈다"
    assert "mine.md" in seen, "우리가 쓴 문서까지 빠졌다"

    index.write_text("# 보관\n\n해시 기록을 지웠다.\n", encoding="utf-8")
    again = {where.split("`")[1] for _kind, where in lint.loud_emphasis(tmp_path)}
    assert "archive/원문.md" in again, "이름으로 걸렀다 — 해시가 기준이 아니다"


def test_a_new_file_is_seen_before_it_is_staged(tmp_path: Path) -> None:
    """A document invisible right after `Write` lives outside every check.

    Reading the index alone, a new document goes unchecked until `git add`,
    and fragment edits piling onto it in the meantime are seen by neither the
    hook nor lint. An upper-case extension is the same problem: the hook
    lowercases before deciding while the pathspec was case-sensitive.
    """

    import lint

    git(tmp_path, "init", "-q")
    noisy = "# 제목\n\n" + "\n\n".join(f"{n} 번째 **강조**." for n in range(9)) + "\n"
    (tmp_path / "new.md").write_text(noisy, encoding="utf-8")
    (tmp_path / "UPPER.MD").write_text(noisy, encoding="utf-8")

    assert lint.tracked_markdown(tmp_path) == ["UPPER.MD", "new.md"]


def test_a_file_deleted_from_the_worktree_is_not_a_finding(tmp_path: Path) -> None:
    """Reporting a file being deleted as unreadable turns an ordinary deletion
    into a blocked gate."""

    import lint

    git(tmp_path, "init", "-q")
    (tmp_path / "gone.md").write_text("# t\n", encoding="utf-8")
    git(tmp_path, "add", "gone.md")
    (tmp_path / "gone.md").unlink()

    assert lint.tracked_markdown(tmp_path) == []
    assert lint.loud_emphasis(tmp_path) == []


def test_the_counter_is_about_asterisks_and_says_so() -> None:
    """Counting `__` would mean implementing CommonMark's delimiter rules.

    One review round found the `foo__bar__baz` false positive, and the next
    clause of that rule is the next round. The habit this check exists to stop
    is written with `**`. `__` is only looked at where it is unambiguous — a
    label at the start of a block.
    """

    snake = "# t\n\n" + "\n\n".join(f"{n} 번째 foo__bar__baz." for n in range(9)) + "\n"
    assert findings(snake) == [], "an identifier was counted as emphasis"

    assert findings("__Rule.__ text"), "a `__` label at the start of a block must be caught"
    assert findings("**Rule.** text"), "a `**` label must still be caught"


def test_inline_code_is_not_counted() -> None:
    """An asterisk inside code is a character. Counting it rejects prose about
    markdown.

    The whole rule is that the opening backtick run and the closing run have
    to be the same length. The looser `` `+[^`\\n]*`+ `` looked equivalent and
    was not: across two backtick spans with a space between them it ate the
    opening run, the space and one following backtick, exposing the middle. A
    review found it, and I reproduced it with a string that had no space,
    reported no defect, and was wrong — the space was precisely where the
    loose pattern failed.
    """

    tick = chr(96)
    for name, text in {
        "spaces inside a double-backtick span":
            f"{tick * 2} {tick}**a**{tick} and {tick}**b**{tick} {tick * 2}",
        "no spaces inside it":
            f"{tick * 2}{tick}**a**{tick} and {tick}**b**{tick}{tick * 2}",
        "two single-backtick spans":
            f"{tick}**a**{tick} and {tick}**b**{tick}",
        "asterisks inside double-backtick spans":
            f"{tick * 2}**a**{tick * 2} and {tick * 2}**b**{tick * 2}",
    }.items():
        assert findings(text, whole=False) == [], name

    assert findings("**a** and **b**", whole=False), "outside code it still counts"
    assert findings(f"{tick}**a**{tick} and **b** and **c**", whole=False)


def test_a_markdown_file_that_cannot_be_read_is_a_finding(tmp_path: Path) -> None:
    """Skipping it leaves the gate green while "all of them were looked at"
    is false."""

    import lint

    (tmp_path / "bad.md").write_bytes(b"\xff\xfe**x**")
    found = lint.loud_emphasis(tmp_path)

    assert [where for _kind, where in found if "읽지 못했다" in where]


def test_only_a_real_closing_fence_closes_a_fence() -> None:
    """Three rounds each added one missing clause and each time the next input
    shape was still wrong: the marker character, then its length, then the info
    string. These are the whole CommonMark rule, held at once.
    """

    tick = chr(96)
    inside = "\n".join(["**code**"] * 4 + ["plain"] * 4)
    for name, text in {
        "info string on the closing line": (
            f"# t\n{tick * 3}text\n{tick * 3}python\n{inside}\n{tick * 3}\n"
        ),
        "a tilde run inside a backtick block": (
            f"# t\n\n{tick * 3}text\n~~~\n{inside}\n{tick * 3}\n"
        ),
        "a shorter run inside a longer one": (
            f"# t\n\n{tick * 4}\n{tick * 3}\n{inside}\n{tick * 4}\n"
        ),
        "a tilde fence": f"# t\n\n~~~\n{inside}\n~~~\n",
        "a fence indented three spaces": (
            f"# t\n\n   {tick * 3}\n{inside}\n   {tick * 3}\n"
        ),
    }.items():
        assert findings(text) == [], name


def test_only_a_markdown_write_is_judged() -> None:
    noisy = CLEAN.replace("규칙. 훅은", "**규칙.** 훅은")
    assert blocked(noisy, path="tool/x.py") is None
    assert blocked(noisy, path="README") is None
    assert blocked(noisy, tool="Read") is None
    assert blocked(noisy, tool="Bash") is None


def test_a_broken_payload_passes_rather_than_stopping_the_session() -> None:
    """`craft/hooks-fail-open`. A broken hook must never stop the work."""

    for payload in [{}, {"tool_name": "Write"}, {"tool_name": "Write", "tool_input": {}}]:
        assert verdict(payload) is None


def test_it_runs_as_a_process_and_answers_on_stdout() -> None:
    """A hook is called as a process. This also checks that a Korean verdict
    survives the pipe."""

    payload = json.dumps(
        {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "docs/x.md",
                "content": CLEAN.replace("규칙. 훅은", "**규칙.** 훅은"),
            },
        }
    )
    done = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        # The child writes Korean; a cp949 default in the parent kills the
        # reader thread quietly -- craft/hooks-fail-open, third face.
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert done.returncode == 0
    answer = json.loads(done.stdout)
    assert answer["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "문단 라벨" in answer["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_parser_answers_not_a_regex() -> None:
    """Six review rounds' worth of input shapes, each against CommonMark.

    Every one of these was a round of review: the hand-written scanner said one
    thing and CommonMark said another, and each fix bought exactly one shape.
    They are here together because the point is that a parse settles all of
    them at once — if this check ever goes back to matching source text, this
    is the test that goes red.
    """

    from markdown_it import MarkdownIt

    strict = MarkdownIt("commonmark")
    for source in [
        "\\`**a**\\` and \\`**b**\\`",          # escaped backticks are text
        "`**a\nb**`",                            # a code span crosses lines
        "** not bold ** and ** also not **",    # flanking rules
        "text __one__ and __two__",             # `__` is emphasis here
        "foo__bar__baz and qux__quux__corge",   # and not here
        "`` ` a ` and ` b ` ``",                # runs match by length
        "text **one** and **two**",             # the shape being enforced
    ]:
        bold = strict.render(source).count("<strong>")
        twice = any("둘 이상" in line for line in findings(source, whole=False))
        assert twice == (bold > 1), source


def test_the_parser_needs_no_undeclared_extra() -> None:
    """The check must work with exactly what requirements-hooks.txt installs.

    `gfm-like` turns on linkify, and linkify needs `linkify-it-py` — an extra
    of markdown-it-py that a plain requirement does not pull in. Where it is
    absent that preset builds fine and raises inside `parse`, past the `None`
    check and into the blanket except in `main`: the write goes through and
    nothing is said. This runs in a child with the module hidden.
    """

    child = """
import builtins, sys
real = builtins.__import__
def blocked(name, *a, **k):
    if name.startswith("linkify_it"):
        raise ModuleNotFoundError("No module named 'linkify_it'")
    return real(name, *a, **k)
builtins.__import__ = blocked
for name in [n for n in sys.modules if n.startswith("linkify_it")]:
    del sys.modules[name]
sys.path.insert(0, {here!r})
from markdown_emphasis import findings
table = "| a | b |\\n| --- | --- |\\n| **x** | **y** |\\n"
assert findings(table, whole=False) == [], findings(table, whole=False)
assert findings("text **one** and **two**", whole=False)
print("ok")
""".format(here=str(HERE))
    done = subprocess.run(
        [sys.executable, "-c", child],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"


def test_a_broken_parse_is_not_a_silent_pass() -> None:
    """Whatever the parser throws, the hook says so instead of going quiet.

    `main` swallows it and passes -- the fail-open contract, and right. But a
    check that fails and reports nothing reads exactly like a check that ran
    and found nothing, which is the shape this whole page exists to stop.
    """

    import markdown_emphasis

    def explode(*_a, **_k):
        raise RuntimeError("parse blew up")

    original = markdown_emphasis.findings
    markdown_emphasis.findings = explode
    try:
        answer = markdown_emphasis.verdict({
            "tool_name": "Write",
            "tool_input": {"file_path": "docs/x.md", "content": "**a** **b**\n"},
        })
    finally:
        markdown_emphasis.findings = original

    assert answer is not None
    assert "permissionDecision" not in json.dumps(answer)
    assert "RuntimeError" in answer["systemMessage"]


def test_the_official_install_provides_the_parser() -> None:
    """Declaring it in a dev-only file left the supported install without it.

    `docs/hooks-setup.md` installs one package list on the target machine's
    interpreter. A parser named anywhere else is a parser that machine does not
    get, and the hook then fails open there — wired, reported as enforced,
    never once firing. So one file says what a hooks install needs, and this
    holds its readers together: the file, `apply.NEEDED` which is what the
    install actually probes with, and the documents that name it.
    """

    import apply

    root = HERE.parent
    declared = {
        line.split("=")[0].split(">")[0].split("<")[0].split(";")[0].strip()
        for line in (root / "requirements-hooks.txt").read_text(
            encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "-"))
    }
    assert declared == set(apply.NEEDED), (declared, apply.NEEDED)
    assert "markdown-it-py" in declared, "강조 훅이 쓰는 파서가 설치 목록에 없다"

    # The chat and dev installs must inherit it rather than restate it.
    assert "-r requirements-hooks.txt" in (
        root / "requirements-chat.txt").read_text(encoding="utf-8")

    for doc in ("docs/hooks-setup.md", "README.md"):
        assert "requirements-hooks.txt" in (root / doc).read_text(
            encoding="utf-8"), f"{doc} 가 설치 목록을 안 가리킨다"


def test_wiring_refuses_an_interpreter_without_the_parser(tmp_path: Path) -> None:
    """The check sits where the wiring is written, not at one way in.

    There are two documented ways to install: `apply --write`, which README
    shows, and `setup_agents`. Putting the check in the second left the first
    wiring a hook that cannot run, on a machine that then reported it as
    enforced. `apply` is what both go through, and it probes the interpreter
    the hooks will actually run under rather than the one doing the install.
    """

    import os

    # The same interpreter with the parser taken away: a shadowing module
    # earlier on the path that refuses to import.
    (tmp_path / "markdown_it.py").write_text(
        'raise ImportError("hidden for this test")\n', encoding="utf-8")

    done = subprocess.run(
        [sys.executable, "-X", "utf8", str(HERE / "apply.py"),
         "--project", str(HERE.parent), "--check"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONPATH": str(tmp_path)}, check=False,
    )
    assert done.returncode == 2, done.stdout + done.stderr
    assert "markdown-it-py" in done.stdout
    assert "requirements-hooks.txt" in done.stdout
    # Paths with spaces are supported, so the printed command has to survive
    # being pasted as written -- quoted, and in a form PowerShell will run.
    assert f'& "{sys.executable}" -m pip install -r "' in done.stdout
    assert "PowerShell" in done.stdout


def test_a_missing_interpreter_is_answered_not_raised() -> None:
    """A wrong `--python` gets the explanation, not a traceback.

    `unusable()` handled a child that ran and failed but not a path that could
    not be run at all, so naming a nonexistent interpreter crashed the install
    with `FileNotFoundError` instead of saying what was wrong.
    """

    import apply

    assert apply.unusable(r"C:\definitely-missing-python.exe")

    done = subprocess.run(
        [sys.executable, "-X", "utf8", str(HERE / "apply.py"),
         "--project", str(HERE.parent), "--check",
         "--python", r"C:\definitely-missing-python.exe"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    assert done.returncode == 2, done.stdout + done.stderr
    assert "Traceback" not in done.stderr


def test_the_probe_covers_the_stdlib_and_the_version_floor() -> None:
    """"Everything the hooks use" is not just the pip list.

    Swapping the probe for that list dropped `tomllib` and the 3.11 floor with
    it, and a 3.10 interpreter passed as a wiring target — the hooks import
    `tomllib` and would have died on it at run time, silently.
    """

    import apply

    assert apply.unusable(sys.executable) == []

    older = Path(r"C:\Users\dasdk\AppData\Local\Programs\Python\Python310\python.exe")
    if not older.is_file():
        import pytest
        pytest.skip("3.11 미만 인터프리터가 이 기계에 없다")
    missing = apply.unusable(str(older))
    assert any("3.11" in name for name in missing), missing
    assert "tomllib" in missing, missing


def test_a_missing_parser_is_said_out_loud(monkeypatch, tmp_path) -> None:
    """Not running is reported by both callers, and never as "clean".

    A check that cannot run and says nothing reads exactly like a check that
    ran and found nothing. That is how a gate stays green with the rule off.
    """

    import lint
    import markdown_emphasis

    monkeypatch.setattr(markdown_emphasis, "parser", lambda: None)

    loud = "text **one** and **two**\n"
    assert findings(loud, whole=False) == []

    # The hook lets the write through -- it never stops the work -- and says so.
    answer = verdict({
        "tool_name": "Write",
        "tool_input": {"file_path": "docs/x.md", "content": loud},
    })
    assert answer is not None
    assert "permissionDecision" not in json.dumps(answer)
    assert "markdown-it-py" in answer["systemMessage"]

    # lint fails the gate instead.
    (tmp_path / "x.md").write_text(loud, encoding="utf-8")
    found = lint.loud_emphasis(tmp_path)
    assert len(found) == 1 and "markdown-it-py" in found[0][1]
