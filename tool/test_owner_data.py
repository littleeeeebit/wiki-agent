"""The owner's real data folders: refused in a build session on both hosts, and watched (plan 15a)."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import claude_pretool
import codex_pretool
import owner_data

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the folders and the watch are Windows'")


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A worktree of the repository `budget`, a Tauri app `com.example.budget` named `Budget`, and AppData
    folders of the test's own."""

    roaming, local = tmp_path / "AppData" / "Roaming", tmp_path / "AppData" / "Local"
    roaming.mkdir(parents=True)
    local.mkdir(parents=True)
    monkeypatch.setattr(owner_data, "bases", lambda: [roaming, local])
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("WIKI_AGENT_ROLE", "build")
    top = tmp_path / "budget"
    subprocess.run(["git", "init", "-q", str(top)], check=True)
    conf = top / "src-tauri" / "tauri.conf.json"
    conf.parent.mkdir(parents=True)
    conf.write_text(json.dumps({"identifier": "com.example.budget", "productName": "Budget"}), encoding="utf-8")
    ignored = top / "node_modules" / "x" / "tauri.conf.json"
    ignored.parent.mkdir(parents=True)
    ignored.write_text(json.dumps({"identifier": "not.this"}), encoding="utf-8")
    return top


def test_the_roots_are_the_repository_and_each_tauri_name_under_both_folders(tree, tmp_path):
    names = sorted(str(p.relative_to(tmp_path / "AppData")) for p in owner_data.roots(tree))
    assert names == sorted(f"{base}\\{name}" for base in ("Roaming", "Local")
                           for name in ("budget", "com.example.budget", "Budget"))


def shell(top: Path, command: str, host: str) -> dict | None:
    if host == "claude":
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(top)}
        return claude_pretool.verdict(payload)
    return codex_pretool.verdict({"tool_name": "exec_command", "tool_input": {"cmd": command}, "cwd": str(top)})


def denied(answer: dict | None) -> str:
    out = (answer or {}).get("hookSpecificOutput") or {}
    return out.get("permissionDecisionReason", "") if out.get("permissionDecision") == "deny" else ""


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_each_spelling_of_a_root_is_refused_and_a_variable_alone_is_not(tree, tmp_path, host):
    roaming = tmp_path / "AppData" / "Roaming"
    posix = "/" + roaming.as_posix().replace(":", "", 1)
    spellings = [f"cat '{roaming}\\budget\\db.sqlite'", f"cat {roaming.as_posix()}/Budget/x",
                 f"ls {posix[0:2].lower()}{posix[2:]}/com.example.budget",
                 "type %APPDATA%\\budget\\x", "cat $LOCALAPPDATA/budget/x", "Get-Content $env:APPDATA\\Budget\\x",
                 "Get-Content ${env:LOCALAPPDATA}/com.example.budget/x", f"cat {str(roaming).upper()}\\BUDGET\\x"]
    for command in spellings:
        reason = denied(shell(tree, command, host))
        assert "주인의 실제 데이터 폴더" in reason, command
    for command in ("echo $APPDATA", "ls %LOCALAPPDATA%", f"cat {roaming}\\budgetary\\x", "cat budget/x"):
        assert not denied(shell(tree, command, host)), command


def test_an_edit_inside_a_root_is_refused_only_in_a_build_session(tree, tmp_path, monkeypatch):
    target = tmp_path / "AppData" / "Local" / "Budget" / "settings.json"
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(target), "content": "{}"}, "cwd": str(tree)}
    assert "주인의 실제 데이터 폴더" in denied(claude_pretool.verdict(payload))
    patch = f"*** Begin Patch\n*** Add File: {target}\n+{{}}\n*** End Patch"
    assert "주인의 실제 데이터 폴더" in denied(codex_pretool.verdict(
        {"tool_name": "apply_patch", "tool_input": {"input": patch}, "cwd": str(tree)}))
    monkeypatch.delenv("WIKI_AGENT_ROLE")
    assert owner_data.verdict(payload) is None   # the owner's own sessions are never refused this


def test_the_watch_says_clean_changed_or_incomplete(tree, tmp_path):
    found = owner_data.roots(tree)
    db = found[0]
    db.mkdir()
    watch = owner_data.Watch(found)
    assert watch.result()["state"] == "clean"
    (db / "nested").mkdir()
    (db / "nested" / "data.db").write_text("x", encoding="utf-8")
    until(lambda: watch.result()["state"] == "changed")
    result = watch.close()
    assert any(p.endswith("data.db") for p in result["paths"]) and len(result["paths"]) <= owner_data.MOST
    assert "data.db" in owner_data.reason(result, found) and watch.close() == result   # a second close says it again
    assert owner_data.reason(owner_data.Watch(found).close(), found) == ""
    missing = [tmp_path / "gone" / "budget"]   # no folder to watch from: never taken for clean
    incomplete = owner_data.Watch(missing).close()
    assert incomplete["state"] == "incomplete" and "감시하지 못했다" in owner_data.reason(incomplete, missing)


@pytest.mark.parametrize("change", ["created", "replaced", "removed"])
def test_a_root_itself_created_replaced_or_removed_is_a_change(tree, change):
    found = owner_data.roots(tree)
    root = found[1]
    if change != "created":
        root.mkdir()
    watch = owner_data.Watch(found)
    if change == "created":
        root.mkdir()
    elif change == "replaced":
        shutil.rmtree(root)
        root.mkdir()
    else:
        root.rmdir()
    until(lambda: watch.result()["state"] == "changed")
    assert watch.close()["state"] == "changed"


def until(test, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not test():
        assert time.monotonic() < deadline, "never happened"
        time.sleep(0.05)
