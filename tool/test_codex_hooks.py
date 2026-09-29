"""Call the installed Codex command as a real process and verify the wiring."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

TOOL = Path(__file__).resolve().parent


def install(project, *extra):
    return subprocess.run(
        [sys.executable, str(TOOL / "apply.py"), "--agent", "codex",
         "--project", str(project), "--adapter", "example", *extra],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
    )


def connect(root: Path):
    project = root / "한글 project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / "README.md").write_text("# 시험 문서\n", encoding="utf-8")
    (project / ".wiki").mkdir()
    (project / ".wiki" / ".sync").write_text(str(time.time()), encoding="utf-8")
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text('{"model":"keep"}', encoding="utf-8")
    (project / ".codex").mkdir()
    other = {"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "echo keep"}]}]}}
    target = project / ".codex" / "hooks.json"
    target.write_text(json.dumps(other), encoding="utf-8")
    preview = install(project)
    assert preview.returncode == 0, preview.stderr
    assert json.loads(target.read_text(encoding="utf-8")) == other
    applied = install(project, "--write")
    assert applied.returncode == 0, applied.stderr
    assert (project / ".claude" / "settings.json").read_text(encoding="utf-8") == '{"model":"keep"}'
    before = target.read_bytes()
    assert not before.startswith(b"\xef\xbb\xbf")
    again = install(project, "--write")
    assert again.returncode == 0, again.stderr
    assert target.read_bytes() == before
    settings = json.loads(before)
    assert settings["hooks"]["SessionEnd"] == other["hooks"]["SessionEnd"]
    return project, settings


@pytest.fixture
def connected(tmp_path):
    return connect(tmp_path)


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    """One install shared by the hooks that only answer and write nothing."""

    return connect(tmp_path_factory.mktemp("installed"))


def run_hook(connected, event, payload, script=""):
    project, settings = connected
    commands = [h for group in settings["hooks"][event] for h in group["hooks"]
                if script in h["command"]]
    assert len(commands) == 1
    payload = {"cwd": str(project), "hook_event_name": event, "session_id": "test", **payload}
    command = commands[0]["command"]
    if os.name == "nt":
        # `shell=True` picks cmd.exe and misses Codex's PowerShell parse errors.
        command = [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", command]
    result = subprocess.run(
        command, shell=os.name != "nt", cwd=project,
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True, timeout=20,
        env={**os.environ, "PYTHONIOENCODING": "cp949", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr, result.stderr
    return json.loads(result.stdout.decode("utf-8") or "{}")


def test_installed_context_and_sync(connected):
    prompt = run_hook(connected, "UserPromptSubmit", {"prompt": "PR 리뷰 루프를 돌려라"})
    assert "operator/codex-review-loop" in prompt["hookSpecificOutput"]["additionalContext"]
    project, _ = connected
    row = json.loads((project / ".wiki" / "trajectory.jsonl").read_text(encoding="utf-8"))
    assert "operator/codex-review-loop" in row["injected"]
    run_hook(connected, "Stop", {}, "sync.py")
    catalog = json.loads((project / ".wiki" / "corpus.json").read_text(encoding="utf-8"))
    assert any(d["path"] == "README.md" for d in catalog["docs"])
    start = run_hook(connected, "SessionStart", {"source": "startup"})
    assert "README.md" in start["hookSpecificOutput"]["additionalContext"]


def test_question_policy_before_agent_decides_to_ask(connected):
    # An ordinary utterance: whether the agent will ask something later cannot
    # be read off what the person typed.
    payload = {"prompt": "현재 이 프로젝트의 진척도를 알려줘."}
    context = run_hook(connected, "UserPromptSubmit", payload)["hookSpecificOutput"]["additionalContext"]
    assert "operator/ask-with-arrow-key-options" in context
    assert "AskUserQuestion" in context and "default_mode_request_user_input" in context
    assert run_hook(connected, "PreToolUse", {"tool_name": "request_user_input", "tool_input": {}}) == {}
    # Claude has its own settings file and its own native tool. Codex's
    # experiment settings do not apply to it.
    from setup_agents import hook_shell
    project, _ = connected
    done = subprocess.run([sys.executable, str(TOOL / "apply.py"), "--project", str(project),
                           "--agent", "claude", "--write"], capture_output=True, timeout=20)
    assert done.returncode == 0, done.stderr
    settings = json.loads((project / ".claude/settings.json").read_text(encoding="utf-8"))
    for event in ("UserPromptSubmit", "PreToolUse"):
        for group in settings["hooks"][event]:
            for hook in group["hooks"]:
                result = subprocess.run([*hook_shell("claude"), hook["command"]],
                    input=json.dumps({**payload, "tool_name": "AskUserQuestion", "tool_input": {}}),
                    text=True, encoding="utf-8", capture_output=True, timeout=20, cwd=project)
                assert result.returncode == 0 and not result.stderr, result.stderr
                answer = json.loads(result.stdout or "{}")
                if event == "UserPromptSubmit":
                    assert "operator/ask-with-arrow-key-options" in answer["hookSpecificOutput"]["additionalContext"]
                else:
                    assert answer == {}, "Claude의 질문 도구를 위키 훅이 막으면 안 된다"


@pytest.mark.parametrize("command,description,blocked", [
    ("git status --short", "", False),
    ("git reset --hard", "", True),
    ("sed -i 's/a/b/' README.md", "", True),
    ("cat > README.md", "", True),
    # The rule inverted with `english_progress.py`: Korean in a description is
    # now what gets denied, and English is what passes. Codex reaches the same
    # hook through `codex_pretool.py`, so this is where that stays proven for
    # the other host.
    ("git status", "Check status", False),
    ("git status", "상태 확인", True),
])
def test_installed_pretool(installed, command, description, blocked):
    answer = run_hook(installed, "PreToolUse", {
        "tool_name": "Bash", "tool_input": {"command": command, "description": description},
    })
    assert (answer.get("hookSpecificOutput", {}).get("permissionDecision") == "deny") == blocked
    assert (installed[0] / "README.md").read_text(encoding="utf-8") == "# 시험 문서\n"


def test_installed_pretool_denies_the_async_tool_itself(installed):
    for tool in ("request_user_input_async", "functions.request_user_input_async"):
        for given in ({"questions": [{"title": "위치?"}]},
                      {"questions": [{"title": "위치?", "options": ["현재", "다른 곳"]}]}):
            answer = run_hook(installed, "PreToolUse", {
                "tool_name": tool, "tool_input": given,
            })
            output = answer.get("hookSpecificOutput", {})
            assert output.get("permissionDecision") == "deny"
            assert tool in output["permissionDecisionReason"]
    for tool, given in (
        ("request_user_input", {"questions": []}),
        ("Bash", {"command": "rg request_user_input_async tool"}),
    ):
        assert run_hook(installed, "PreToolUse", {
            "tool_name": tool, "tool_input": given,
        }) == {}


@pytest.mark.parametrize("message,active,blocked", [
    ("지금 이어서 하겠습니다.", False, True),
    ("지금 이어서 하겠습니다.", True, False),
    ("검증을 마쳤습니다.", False, False),
    ("결과가 나오면 이어서 하겠습니다.", False, False),
    (None, False, False),
])
def test_installed_stop(installed, message, active, blocked):
    answer = run_hook(installed, "Stop", {
        "last_assistant_message": message, "stop_hook_active": active,
        "transcript_path": None,
    }, "declared_continuation.py")
    assert (answer.get("decision") == "block") == blocked


@pytest.mark.skipif(os.name != "nt", reason="Git Bash 탐색은 Windows 에서만 돈다")
def test_git_bash_is_found_from_every_copy_of_git_exe(tmp_path, monkeypatch):
    """Git for Windows puts `git.exe` in three places, and one level up misses it.

    This went red on a machine with `mingw64/bin/git.exe` on PATH: one level
    up is `mingw64`, which has no `bin/bash.exe`. The install root is three
    levels up.
    """
    from setup_agents import hook_shell

    root = tmp_path / "Git"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "bash.exe").write_text("", encoding="utf-8")
    monkeypatch.delenv("CLAUDE_CODE_GIT_BASH_PATH", raising=False)

    for where in ("cmd/git.exe", "bin/git.exe", "mingw64/bin/git.exe"):
        copy = root / where
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text("", encoding="utf-8")
        monkeypatch.setattr(shutil, "which", lambda _name, _at=copy: str(_at))
        assert hook_shell("claude")[0] == str(root / "bin" / "bash.exe"), where
