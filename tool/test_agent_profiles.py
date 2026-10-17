"""Focused checks split from tool/test_agent.py."""

import json
import subprocess
from unittest.mock import patch
import pytest
from agent import ChatSession, chat_session, read_tools
from test_agent import tree  # noqa: F401 -- isolated Git fixture

# ruff: noqa: F811 -- pytest injects the imported fixture by name


@pytest.mark.parametrize("model", ["opus", "codex:test-model"])
@pytest.mark.parametrize("fast", [False, True])
def test_native_fast_mode_is_explicit_and_reconfiguration_preserves_the_session(tmp_path, model, fast):
    session = ChatSession(tmp_path, model=model, fast=fast, resume="existing-thread")
    commands = []

    def spawn(command, **kwargs):
        commands.append(command)
        raise OSError("Captured launch without starting a real model")

    with patch.object(chat_session.subprocess, "Popen", spawn), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
        with pytest.raises(OSError):
            session.ensure()
    if model.startswith("codex:"):
        assert f'service_tier="{"fast" if fast else "default"}"' in commands[0]
        calls = []
        session._call = lambda method, params: calls.append((method, params)) or {"thread": {"id": "existing-thread"}}
        session._send = lambda request: True
        session._open_thread()
        assert calls[-1][1]["serviceTier"] == ("priority" if fast else "default")
    else:
        command = commands[0]
        assert json.loads(command[command.index("--settings") + 1])["fastMode"] is fast
    session.reconfigure(model, "high", not fast)
    assert session.fast is not fast and session.session_id == "existing-thread" and session.effort == "high"


def test_source_tools_read_tracked_text_but_not_private_ignored_or_outside_files(tree, tmp_path):
    (tree / "source.py").write_text("first\nneedle\nlast\n", encoding="utf-8")
    (tree / ".env").write_text("private", encoding="utf-8")
    (tree / "private.txt").write_text("private", encoding="utf-8")
    subprocess.run(["git", "-C", str(tree), "add", "source.py", ".env"], check=True)
    assert "2: needle" in read_tools.call(tree, "repo_read", {"path": "source.py", "offset": 2, "limit": 1})
    assert read_tools.call(tree, "repo_glob", {"pattern": "*"}) == "source.py"
    assert read_tools.call(tree, "repo_grep", {"text": "needle"}) == "source.py:2: needle"
    for path in (".env", "private.txt", "../elsewhere.txt", str(tmp_path / "private.txt")):
        with pytest.raises(ValueError):
            read_tools.call(tree, "repo_read", {"path": path})
    for tool, args in (("shell", {}), ("repo_read", {"path": "source.py", "limit": 501}),
                       ("repo_grep", {"text": ""}), ("repo_read", {"path": "source.py", "offset": True})):
        with pytest.raises(ValueError):
            read_tools.call(tree, tool, args)
    with patch.object(read_tools.Path, "resolve", side_effect=[tree.resolve(), tmp_path / "private.txt"]):
        # A tracked symlink/junction cannot escape the worktree.
        with pytest.raises(ValueError):
            read_tools.call(tree, "repo_read", {"path": "source.py"})


def test_the_saved_pr_diff_reads_back_whole_lines_deleted_files_and_carriage_returns(tree):
    with pytest.raises(ValueError):                       # no round has saved one yet
        read_tools.call(tree, "pr_diff", {})
    gone = "diff --git a/old.py b/old.py\ndeleted file mode 100644\n--- a/old.py\n+++ /dev/null\n" \
           "@@ -1,2 +0,0 @@\n-removed\r\n-" + "z" * 4_500 + "\n"
    kept = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    spans = read_tools.save_diff(tree, gone + kept)
    assert spans == [(1, 9), (10, 15)]                    # 4,501 characters wrap to three lines
    out = read_tools.call(tree, "pr_diff", {"offset": 6, "limit": 5})
    assert "6: -removed\r" in out and f"7: -{'z' * 1999}" in out and f"9: ~{'z' * 502}" in out
    assert "10: diff --git a/a.py b/a.py" in out and "(전체 15줄; 다음 offset=11)" in out
    assert "wiki-review" not in read_tools.call(tree, "repo_glob", {"pattern": "*"})   # never in the source tree
