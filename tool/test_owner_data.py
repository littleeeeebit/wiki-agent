"""The owner's real data folders: refused in a build session on both hosts, and watched (plan 15a)."""

import json
import os
import shutil
import subprocess
import threading
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
    assert "data.db" in owner_data.reason(result) and watch.close() == result   # a second close says it again
    assert owner_data.reason(owner_data.Watch(found).close()) == ""
    missing = [tmp_path / "gone" / "budget"]   # no folder to watch from: never taken for clean
    incomplete = owner_data.Watch(missing).close()
    assert incomplete["state"] == "incomplete" and "감시하지 못했다" in owner_data.reason(incomplete)


def test_a_write_finished_before_a_read_is_in_that_read(tree):
    found = owner_data.roots(tree)
    found[0].mkdir()
    for i in range(20):   # no wait between the write and the read: the read takes what was delivered first
        watch = owner_data.Watch(found)
        (found[0] / f"f{i}").write_text("x", encoding="utf-8")
        assert watch.settled()["state"] == "changed", i
        watch.close()


def test_a_failed_wait_is_never_taken_for_a_clean_window(tree, monkeypatch):
    real = owner_data._k32

    class Failing:   # the kernel, but every wait fails
        def __init__(self):
            self.k = real()

        def __getattr__(self, name):
            return getattr(self.k, name)

        def WaitForMultipleObjects(self, *args):  # noqa: N802 -- the kernel's own name
            return 0xFFFFFFFF   # WAIT_FAILED

    monkeypatch.setattr(owner_data, "_k32", Failing)
    watch = owner_data.Watch(owner_data.roots(tree))
    until(lambda: watch.result()["state"] == "incomplete")
    result = watch.close()
    assert result["state"] == "incomplete" and "감시가 멈췄다" in owner_data.reason(result)


def test_a_root_named_during_the_window_is_covered(tree, tmp_path):
    local, conf = tmp_path / "AppData" / "Local", tree / "src-tauri" / "tauri.conf.json"
    watch = owner_data.Watch(owner_data.roots(tree))
    conf.write_text(json.dumps({"identifier": "com.example.renamed"}), encoding="utf-8")   # the turn renames the app
    (local / "com.example.renamed").mkdir()   # and its program makes its data folder
    result = watch.settled(owner_data.roots(tree))
    assert result["state"] == "changed" and any(r.endswith("com.example.renamed") for r in result["roots"])
    watch.close()
    (local / "com.example.older").mkdir()   # a name whose folder was there before the window: never watched inside
    watch = owner_data.Watch(owner_data.roots(tree))
    conf.write_text(json.dumps({"identifier": "com.example.older"}), encoding="utf-8")
    assert watch.close(owner_data.roots(tree))["state"] == "incomplete"
    watch = owner_data.Watch(owner_data.roots(tree))   # a name whose folder never came: nothing to see
    conf.write_text(json.dumps({"identifier": "com.example.never"}), encoding="utf-8")
    assert watch.close(owner_data.roots(tree))["state"] == "clean"


def test_a_name_used_only_between_two_reads_is_never_taken_for_clean(tree, tmp_path):
    local, conf = tmp_path / "AppData" / "Local", tree / "src-tauri" / "tauri.conf.json"
    original = conf.read_text(encoding="utf-8")
    watch = owner_data.Watch(owner_data.roots(tree), tree)
    conf.write_text(json.dumps({"identifier": "com.example.trial"}), encoding="utf-8")   # a smoke test's name
    (local / "com.example.trial").mkdir()   # its program's data folder, made before or after the read
    # The read only has to come before the restore, as a build of the name takes longer than it does.
    until(lambda: any(r.endswith("com.example.trial") for r in watch.result()["roots"]))
    conf.write_text(original, encoding="utf-8")   # restored before the check reads anything
    result = watch.settled()
    assert result["state"] != "clean" and any(r.endswith("com.example.trial") for r in result["roots"])
    watch.close()
    older = local / "com.example.older"   # a name whose folder and settings were there before the window
    older.mkdir()
    (older / "settings.json").write_text("{}", encoding="utf-8")
    watch = owner_data.Watch(owner_data.roots(tree), tree)
    conf.write_text(json.dumps({"identifier": "com.example.older"}), encoding="utf-8")
    (older / "settings.json").write_text('{"x": 1}', encoding="utf-8")   # run under it before any watch of its own
    until(lambda: str(older) in watch.result()["roots"])
    conf.write_text(original, encoding="utf-8")
    result = watch.close()
    assert result["state"] == "incomplete" and "com.example.older" in owner_data.reason(result)


class Kernel:
    """kernel32, with every read of one folder's watch delivering an overflow (`overflow`), or held until `go`
    is set, as a delivery that comes late."""

    def __init__(self, k, folder: Path, overflow: bool):
        self.k, self.folder, self.handles, self.overflow, self.go = k, str(folder), set(), overflow, threading.Event()

    def __getattr__(self, name):
        return getattr(self.k, name)

    def CreateFileW(self, path, *rest):
        handle = self.k.CreateFileW(path, *rest)
        if path == self.folder:
            self.handles.add(handle)
        return handle

    def GetOverlappedResult(self, handle, overlapped, size, wait):
        if not wait and handle in self.handles and not self.overflow:
            self.go.wait(30)
        ok = self.k.GetOverlappedResult(handle, overlapped, size, wait)
        if ok and not wait and handle in self.handles and self.overflow:
            size._obj.value = 0
        return ok


def kernel(tree, monkeypatch, overflow: bool) -> Kernel:
    held = Kernel(owner_data._k32(), tree, overflow)
    monkeypatch.setattr(owner_data, "_k32", lambda: held)
    return held


def test_a_config_changed_while_the_trees_notices_were_lost_is_incomplete(tree, tmp_path, monkeypatch):
    conf = tree / "src-tauri" / "tauri.conf.json"
    kernel(tree, monkeypatch, overflow=True)
    (tmp_path / "AppData" / "Local" / "budget").mkdir()
    watch = owner_data.Watch(owner_data.roots(tree), tree)
    time.sleep(0.05)   # the config was written before the window
    (tree / "build.log").write_text("x", encoding="utf-8")   # a build's own write, lost with the rest
    assert watch.settled()["state"] == "clean"   # no config changed: an overflow alone refuses nothing
    conf.write_text(conf.read_text(encoding="utf-8") + "\n", encoding="utf-8")   # the same names, as now
    result = watch.settled()
    assert result["state"] == "incomplete" and "다 알 수 없다" in owner_data.reason(result)
    (tmp_path / "AppData" / "Local" / "budget" / "cache.bin").write_text("x", encoding="utf-8")
    assert watch.close()["state"] == "changed"   # the watch went on


def test_a_name_given_and_taken_back_before_its_notice_is_read_is_incomplete(tree, monkeypatch):
    conf = tree / "src-tauri" / "tauri.conf.json"
    original, held = conf.read_text(encoding="utf-8"), kernel(tree, monkeypatch, overflow=False)
    watch = owner_data.Watch(owner_data.roots(tree), tree)
    time.sleep(0.05)   # the config was written before the window
    conf.write_text(json.dumps({"identifier": "com.example.trial"}), encoding="utf-8")
    conf.write_text(original, encoding="utf-8")   # restored before the notice is read
    held.go.set()
    result = watch.close()
    assert result["state"] == "incomplete" and "다 알 수 없다" in owner_data.reason(result)
    # A config made and removed before its notice is read, in a folder nobody watched for it.
    held.go.clear()
    watch = owner_data.Watch(owner_data.roots(tree), tree)
    other = tree / "second" / "tauri.conf.json"
    other.parent.mkdir()
    other.write_text(json.dumps({"identifier": "com.example.second"}), encoding="utf-8")
    other.unlink()
    held.go.set()
    assert watch.close()["state"] == "incomplete"


def test_an_ordinary_config_edit_beside_another_programs_folder_stays_clean(tree, tmp_path):
    local, conf = tmp_path / "AppData" / "Local", tree / "src-tauri" / "tauri.conf.json"
    watch = owner_data.Watch(owner_data.roots(tree), tree)
    value = json.loads(conf.read_text(encoding="utf-8"))
    conf.write_text(json.dumps({**value, "app": {"windows": [{"width": 900}]}}), encoding="utf-8")   # same names
    (local / "elsewhere").mkdir()   # another program's folder: no name this tree ever gave
    assert watch.close()["state"] == "clean"


def test_off_windows_nothing_is_watched_and_nothing_is_claimed(monkeypatch):
    monkeypatch.setattr(owner_data, "_k32", lambda: pytest.fail("the Windows watch was started"))
    with monkeypatch.context() as m:
        m.setattr(owner_data.os, "name", "posix")
        watch = owner_data.Watch([])
    assert watch.settled()["state"] == "clean" and watch.close()["state"] == "clean"


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
