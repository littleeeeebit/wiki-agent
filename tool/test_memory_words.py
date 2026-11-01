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


def test_every_listed_word_has_its_plain_replacement():
    assert all(WORDS.values()) and not any(stem in plain for stem in WORDS for plain in WORDS.values())


def test_the_sweep_lists_what_saved_memories_hold_and_exits_on_it(tmp_path):
    (tmp_path / "clean.md").write_text("A form the check let through.\n", encoding="utf-8")
    run = [sys.executable, "-X", "utf8", str(HERE / "memory_words.py"), str(tmp_path)]
    assert subprocess.run(run, capture_output=True).returncode == 0
    (tmp_path / "loaded.md").write_text(LOADED, encoding="utf-8")
    swept = subprocess.run(run, capture_output=True, text=True, encoding="utf-8")
    assert swept.returncode == 1 and "loaded.md" in swept.stdout and "clean.md" not in swept.stdout
