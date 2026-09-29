"""Prove `apply`'s merge never erases settings that were already there."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from apply import (  # noqa: E402
    continuation_entry,
    hook_entry,
    merge,
    retire,
    runs,
    script_entry,
    stale,
    sync_entry,
)

PY = "C:/py.exe"
OTHER_HOOK = {
    "hooks": [{"type": "command", "command": "echo 남의-훅"}],
}
OTHER_PRE = {
    "matcher": "Bash",
    "hooks": [{"type": "command", "command": "echo 남의-사전훅"}],
}
OTHER_STOP = {
    "hooks": [{"type": "command", "command": "echo 남의-정지훅"}],
}
LIVED_IN = {
    "permissions": {
        "deny": ["Read(./secrets/**)", "Edit(./protected-data/**)"],
        "allow": ["Bash(npm *)"],
    },
    "hooks": {
        "UserPromptSubmit": [OTHER_HOOK],
        "PreToolUse": [OTHER_PRE],
        "PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "fmt"}]}],
    },
    "model": "opus",
}
SCRIPTS = {"english_progress.py": script_entry(PY, "english_progress.py", "확인")}


DENIES = ["Bash(sed -i*)", "Bash(git reset --hard*)"]
HOOK = hook_entry(PY, "proj")


def commands(settings: dict, event: str) -> list[str]:
    return [e["command"] for g in settings["hooks"][event] for e in g["hooks"]]


def test_an_empty_settings_file_gets_everything():
    fresh: dict = {}
    changes = merge(fresh, DENIES, HOOK, SCRIPTS)
    assert fresh["permissions"]["deny"] == DENIES
    assert len(fresh["hooks"]["UserPromptSubmit"]) == 1
    assert len(fresh["hooks"]["PreToolUse"]) == 1
    assert len(changes) == 4, changes


def test_lived_in_settings_keep_everything_and_a_second_run_changes_nothing():
    lived = json.loads(json.dumps(LIVED_IN))
    merge(lived, DENIES, HOOK, SCRIPTS)
    assert lived["permissions"]["deny"] == LIVED_IN["permissions"]["deny"] + DENIES
    assert lived["permissions"]["allow"] == ["Bash(npm *)"]
    assert OTHER_HOOK in lived["hooks"]["UserPromptSubmit"]
    assert OTHER_PRE in lived["hooks"]["PreToolUse"]
    assert lived["hooks"]["PostToolUse"] == LIVED_IN["hooks"]["PostToolUse"]
    assert lived["model"] == "opus"

    assert merge(lived, DENIES, HOOK, SCRIPTS) == []
    assert len(lived["hooks"]["UserPromptSubmit"]) == 2 and len(lived["hooks"]["PreToolUse"]) == 2

    moved = merge(lived, DENIES, hook_entry(PY, "다른프로젝트"), SCRIPTS)
    ours = [c for c in commands(lived, "UserPromptSubmit") if "inject.py" in c]
    assert moved == ["UserPromptSubmit 훅 명령 갱신: tool/inject.py"], moved
    assert len(ours) == 1 and "다른프로젝트" in ours[0], ours


def test_two_of_ours_on_stop_stand_beside_someone_elses():
    # The one place where two of this wiki's hooks sit on a single event.
    # `put_hook` recognises its own by the script path in the command rather
    # than by name, so these two must not overwrite each other — and somebody
    # else's Stop hook has to survive alongside them.
    both: dict = {"hooks": {"Stop": [OTHER_STOP]}}
    merge(both, [], HOOK, SCRIPTS, None, sync_entry(PY, "proj"), continuation_entry(PY))
    stop = commands(both, "Stop")
    assert any("sync.py" in c for c in stop), stop
    assert any("declared_continuation.py" in c for c in stop), stop
    assert OTHER_STOP in both["hooks"]["Stop"]
    assert merge(both, [], HOOK, SCRIPTS, None, sync_entry(PY, "proj"), continuation_entry(PY)) == []


def test_a_renamed_hook_is_retired_only_when_its_replacement_is_wired():
    # Without this the old entry stayed in `settings.json`, and deleting the
    # script failed every tool call with "can't open file" until the shim
    # went back.
    old = {"hooks": {"PreToolUse": [script_entry(PY, "korean_progress.py", "옛것")]}}
    merge(old, [], HOOK, SCRIPTS)
    left = commands(old, "PreToolUse")
    assert not any("korean_progress.py" in c for c in left), left
    assert any("english_progress.py" in c for c in left), left
    # Twice has to give the same answer: an upgrade reads an existing install.
    merge(old, [], HOOK, SCRIPTS)
    assert commands(old, "PreToolUse") == left

    # No replacement wired means no removal. Removing it would turn the
    # enforcement off without saying so.
    alone = {"hooks": {"PreToolUse": [script_entry(PY, "korean_progress.py", "옛것")]}}
    assert retire(alone, "korean_progress.py", "english_progress.py") == []
    assert len(alone["hooks"]["PreToolUse"]) == 1

    # The person's own hook that merely names the retired script. Matching on
    # the bare filename deleted it, which is the loss this whole file exists
    # to prevent — and `put_hook` would have overwritten it on the way in.
    theirs = {"hooks": {"PreToolUse": [{"hooks": [{
        "type": "command", "command": "python audit.py --watch korean_progress.py"}]}]}}
    merge(theirs, [], HOOK, SCRIPTS)
    assert "python audit.py --watch korean_progress.py" in commands(theirs, "PreToolUse")


# Ownership is not whether the name appears in the command string. Two review
# rounds went to that answer: the bare filename was defeated first, then the
# `tool/` prefix — `custom-tool/` ends in `tool/`.
OWNERSHIP = [
    ("python audit.py --watch korean_progress.py", "korean_progress.py", False,
     "someone else's hook that only names it"),
    ('"py" "C:/repo/custom-tool/korean_progress.py"', "korean_progress.py", False,
     "a directory ending in tool"),
    # Comparing only the parent directory's name called this ours — the third
    # round in the same place. It needs a real file on disk to reproduce.
    ('"py" "{theirs}/korean_progress.py"', "korean_progress.py", False,
     "someone else's script in a real tool/"),
    ('"py" "{here}/korean_progress.py"', "korean_progress.py", True,
     "our directory, a script already deleted"),
    ('& "py" "{here}/declared_continuation.py" --codex', "declared_continuation.py", True,
     "the & and argument Codex adds"),
    ('"py" "Z:/team/tool/inject.py" --adapter x', "inject.py", False,
     "a hook on a dropped drive: a missing path is not ours"),
    ('"python" "tool/inject.py"', "inject.py", False, "a target project's relative hook"),
    ('"python" "tool/korean_progress.py"', "korean_progress.py", False,
     "relative, even under a retired name"),
    # Somebody else's hook passing our path as data. Scanning every quoted
    # argument makes this ours, and `put_hook` overwrites their `audit.py` hook.
    ('"C:/Python/python.exe" "C:/project/audit.py" --watch "{here}/inject.py"', "inject.py", False,
     "someone else's hook taking our path as an argument"),
    ('"py" "{here}/inject.py" --adapter x', "sync.py", False, "another script"),
]


@pytest.mark.parametrize("command, script, ours", [case[:3] for case in OWNERSHIP],
                         ids=[case[3] for case in OWNERSHIP])
def test_ownership_is_read_off_the_script_path(tmp_path, command, script, ours):
    theirs = tmp_path / "tool"
    theirs.mkdir()
    (theirs / "korean_progress.py").write_text("# 남의 것\n", encoding="utf-8")
    command = command.format(here=HERE.as_posix(), theirs=theirs.as_posix())
    assert runs(command, script) == ours


def test_ownership_does_not_depend_on_the_working_directory(tmp_path, monkeypatch):
    # From the hub root, `tool/inject.py` resolved to `HERE/inject.py` and
    # somebody else's hook became ours.
    for where in (HERE.parent, tmp_path):
        monkeypatch.chdir(where)
        assert runs('"python" "tool/inject.py"', "inject.py") is False, where


def test_a_hook_at_a_missing_path_is_reported_never_removed():
    # It may be where the wiki used to live, or somebody else's hook on a
    # share that is offline for a minute, and the command alone cannot tell
    # those apart. Choosing to delete destroys a colleague's settings over one
    # network blip.
    ghost = {"hooks": {"PreToolUse": [{"hooks": [
        {"type": "command", "command": '"py" "Z:/team/tool/inject.py"'}]}]}}
    assert len(stale(ghost)) == 1
    assert len(ghost["hooks"]["PreToolUse"][0]["hooks"]) == 1
    assert stale({"hooks": {"UserPromptSubmit": [HOOK]}}) == []
    # What runs is `audit.py`. The missing path is only what that hook
    # watches, so "this hook points at a file that is not there" would not be
    # true.
    watcher = {"hooks": {"PreToolUse": [{"hooks": [{
        "type": "command", "command": '"py" "C:/project/audit.py" --watch "Z:/archive/tool/inject.py"'}]}]}}
    assert stale(watcher) == []
