"""What the memory-word hook denies, and the nearest writes it must let through."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from memory_words import WORDS, verdict  # noqa: E402

MEMORY = "C:/Users/someone/.claude/projects/C--repo/memory/note.md"
LOADED = "The guard let a " + "by" + "pass through."   # split so this file stays searchable clean


def denied(path: str, text: str, tool: str = "Write") -> bool:
    key = {"Write": "content", "Edit": "new_string"}.get(tool)
    given = {"file_path": path, key: text} if key else {"input": f"*** Begin Patch\n*** Add File: {path}\n+{text}\n"}
    answer = verdict({"tool_name": tool, "tool_input": given})
    return bool(answer) and answer["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("tool", ["Write", "Edit", "functions.apply_patch"])
def test_a_memory_write_with_a_listed_word_is_denied_on_every_editing_tool(tool):
    path = MEMORY if tool != "functions.apply_patch" else "/home/someone/.codex/memories/note.md"
    assert denied(path, LOADED, tool)


@pytest.mark.parametrize("text", [LOADED.upper(), "Two " + "pay" + "loads were sent.", "[[docker-" + "pay" + "load-routes]]"])
def test_inflections_case_and_links_are_the_same_word(text):
    assert denied(MEMORY, text)


@pytest.mark.parametrize("path,text", [
    ("C:/repo/docs/guard.md", LOADED),                              # not a memory
    (MEMORY, "The guard let a form through; the input was outside/target.txt."),
    (MEMORY, "Use dependency " + "in" + "jection for the client."),  # the plain technical sense
])
def test_the_nearest_correct_writes_pass(path, text):
    assert not denied(path, text)


def test_the_destination_decides_not_the_spelling():
    """PR #276 R1: a relative path is read against the call's `cwd`, and `..` is resolved."""

    def call(path):
        return verdict({"cwd": "C:/Users/someone", "tool_name": "Write",
                        "tool_input": {"file_path": path, "content": LOADED}})

    assert call(".codex/memories/note.md") and call(".claude/projects/C--repo/memory/note.md")
    assert call("docs/../.codex/memories/note.md")
    assert not call("C:/Users/someone/.codex/memories/../ordinary.md")


def test_home_notation_is_the_home_folder_not_a_folder_under_cwd(tmp_path, monkeypatch):
    """PR #276 R2: `~` from a working directory inside memory lands in the home folder."""

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    inside = str(tmp_path / ".codex" / "memories")
    for path, loaded in (("~/ordinary.md", False), ("~/.codex/memories/note.md", True)):
        call = {"cwd": inside, "tool_name": "Write", "tool_input": {"file_path": path, "content": LOADED}}
        assert bool(verdict(call)) is loaded
    # R3: `~` in `cwd` is the home folder too.
    call = {"cwd": "~/.codex", "tool_name": "Write", "tool_input": {"file_path": "memories/note.md", "content": LOADED}}
    assert verdict(call)


def test_the_dependency_exception_covers_only_its_own_word_and_phrase():
    assert not denied(MEMORY, "Use dependency " + "in" + "jection.")
    assert denied(MEMORY, "Use dependency " + "by" + "pass.")
    assert denied(MEMORY, "Use nondependency " + "in" + "jection.")


def test_a_file_moved_into_memory_is_checked_for_what_it_keeps(tmp_path):
    """PR #276 R1: a move carries its unchanged lines, which the added fragments never show."""

    (tmp_path / "note.md").write_text("A kept line: " + "pay" + "load.\nOther.\n", encoding="utf-8")
    target = str(tmp_path / ".codex" / "memories" / "note.md")

    def move(hunk):
        body = f"*** Begin Patch\n*** Update File: note.md\n*** Move to: {target}\n{hunk}*** End Patch\n"
        answer = verdict({"cwd": str(tmp_path), "tool_name": "apply_patch", "tool_input": {"input": body}})
        return bool(answer)

    assert move("") and move("@@\n Other.\n+Plain note.\n")
    # R3: a CRLF patch names the same source, so it gets the same answer.
    crlf = f"*** Begin Patch\r\n*** Update File: note.md\r\n*** Move to: {target}\r\n@@\r\n Other.\r\n+Plain.\r\n"
    assert verdict({"cwd": str(tmp_path), "tool_name": "apply_patch", "tool_input": {"input": crlf}})
    assert not move("@@\n-A kept line: " + "pay" + "load.\n+A kept line: the body.\n")
    # R2: unreadable, the source is its context lines, and a removed twin does not erase a kept one.
    (tmp_path / "note.md").unlink()
    assert move("@@\n A kept line: " + "pay" + "load.\n-A kept line: " + "pay" + "load.\n")


def test_codex_reaches_the_memory_check_past_an_informational_answer(monkeypatch):
    """PR #276 R1: with no Markdown parser the emphasis check only informs, which ended the chain."""

    import codex_pretool
    import markdown_emphasis

    monkeypatch.setattr(markdown_emphasis, "parser", lambda: None)
    body = f"*** Begin Patch\n*** Add File: {MEMORY}\n+{LOADED}\n*** End Patch\n"
    answer = codex_pretool.verdict({"tool_name": "functions.apply_patch", "tool_input": {"input": body}})
    assert answer["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_every_listed_word_has_its_plain_replacement():
    assert all(WORDS.values()) and not any(stem in plain for stem in WORDS for plain in WORDS.values())


def test_the_sweep_lists_what_saved_memories_hold_and_exits_on_it(tmp_path):
    (tmp_path / "clean.md").write_text("A form the check let through.\n", encoding="utf-8")
    run = [sys.executable, "-X", "utf8", str(HERE / "memory_words.py"), str(tmp_path)]
    assert subprocess.run(run, capture_output=True).returncode == 0
    (tmp_path / "loaded.md").write_text(LOADED, encoding="utf-8")
    swept = subprocess.run(run, capture_output=True, text=True, encoding="utf-8")
    assert swept.returncode == 1 and "loaded.md" in swept.stdout and "clean.md" not in swept.stdout
