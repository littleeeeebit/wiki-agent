"""Legacy desktop context cannot choose the app's execution transport."""

import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

import apply
from common import host
from host_boundary import verdict


@pytest.fixture(autouse=True)
def managed_session(monkeypatch):
    monkeypatch.setenv("WIKI_AGENT_MANAGED", "1")


def test_other_hosts_keep_their_explicit_review_transport(monkeypatch):
    monkeypatch.delenv("WIKI_AGENT_MANAGED")
    assert verdict({"tool_name": "exec_command", "tool_input": {"cmd": "orca terminal list --json"}}) is None
    assert verdict({"tool_name": "Skill", "tool_input": {"skill": "orca-cli"}}) is None


@pytest.mark.parametrize("command", [
    "orca", "orca status", "git status; orca --help",
    "orca terminal list --json", "Get-Command orca -ErrorAction SilentlyContinue",
    'pwsh -Command "orca worktree current --json"', "which orca",
    '& "C:/tools/orca.exe" terminal send --enter', "$env:ORCA_CLI_COMMAND --help",
    'pwsh -Command "orca status"', 'powershell -Command "orca status"',
    "pwsh -NoProfile -Command 'orca status'", 'cmd /c "orca status"',
    'pwsh -Command "orca"', 'cmd /c "orca"',
])
def test_legacy_transport_is_redirected(command):
    for name, field in (("Bash", "command"), ("exec_command", "cmd"), ("exec", "code")):
        output = verdict({"tool_name": name, "tool_input": {field: command}})["hookSpecificOutput"]
        assert output["permissionDecision"] == "deny"
        assert "wiki-agent" in output["additionalContext"]


@pytest.mark.parametrize("command", ["git status --short", "gh pr view 11", "python -m pytest",
                                        "rg -n orca docs", "cat docs/research/orca-removal.md"])
def test_native_work_and_historical_evidence_are_allowed(command):
    assert verdict({"tool_name": "exec_command", "tool_input": {"cmd": command}}) is None


def test_environment_and_skill_overrides_do_not_modify_the_login_or_other_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text('[[skills.config]]\npath="/keep/SKILL.md"\nenabled=false\n', encoding="utf-8")
    roots = [home / "skills", tmp_path / ".agents/skills"]
    for root in roots:
        for name, text in (("legacy", "Run orca terminal list"), ("native", "Use git status")):
            path = root / name / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    env = host.environment({"CODEX_HOME": str(home), "ORCA_TERMINAL_HANDLE": "obsolete", "ORCA_CLI_COMMAND": "obsolete"})
    assert env["CODEX_HOME"] == str(home) and env["WIKI_AGENT_MANAGED"] == "1"
    assert not any(k.startswith("ORCA_") for k in env)
    config = host.skill_config(tmp_path, env)
    rows = tomllib.loads(config)["skills"]["config"]
    assert len(rows) == 3 and not any(row["enabled"] for row in rows)
    assert not any("native" in row["path"] for row in rows)
    assert (home / "config.toml").read_text(encoding="utf-8").startswith("[[skills.config]]")


def test_reinstall_retires_only_owned_keepalive_hooks(tmp_path):
    foreign = {"type": "command", "command": '"python" "D:/other/keepalive.py"'}
    old = {"type": "command", "command": f'"{sys.executable}" "{(apply.HERE / "keepalive.py").as_posix()}"'}
    settings = {"hooks": {"SessionEnd": [{"hooks": [old, foreign]}]}}
    apply.configure(settings, tmp_path, None, sys.executable, "claude")
    assert settings["hooks"]["SessionEnd"] == [{"hooks": [foreign]}]
    assert apply.configure(settings, tmp_path, None, sys.executable, "claude") == []


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_reinstall_removes_the_legacy_bridge_and_preserves_unrelated_hooks(tmp_path, agent):
    old = {"type": "command", "command": 'cmd /c "C:/user/.orca/agent-hooks/codex-hook.cmd"'}
    other = {"type": "command", "command": '"python" "C:/other/audit.py"'}
    settings = {"hooks": {"PreToolUse": [{"hooks": [old, other]}]}, "model": "keep"}
    apply.configure(settings, tmp_path, None, sys.executable, agent)
    hooks = settings["hooks"]["PreToolUse"][0]["hooks"]
    assert hooks == [other] and settings["model"] == "keep"


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_global_install_preserves_the_current_hosts_bridge(agent):
    bridge = {"type": "command", "command": 'cmd /c "C:/user/.orca/agent-hooks/codex-hook.cmd"'}
    settings = {"hooks": {"PreToolUse": [{"hooks": [bridge]}]}}
    apply.configure(settings, None, None, sys.executable, agent)
    assert any(bridge in group["hooks"] for group in settings["hooks"]["PreToolUse"])


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_project_install_preserves_transport_words_used_as_hook_data(tmp_path, agent):
    foreign = [
        {"type": "command", "command": '\"python\" \"C:/hooks/audit.py\" --label orca'},
        {"type": "command", "command": '\"python\" \"C:/hooks/audit.py\" --watch \"C:/user/.orca/agent-hooks/a.cmd\"'},
        {"type": "command", "command": '\"python\" \"C:/hooks/audit.py\" --label ORCA_AGENT_HOOK_PORT'},
        {"type": "command", "command": '\"C:/hooks/audit.exe\" --watch \"C:/user/.orca/agent-hooks/a.cmd\"'},
        {"type": "command", "command": '\"C:/hooks/audit.exe\" \"C:/user/.orca/agent-hooks/a.cmd\"'},
        {"type": "command", "command": '\"python\" -c \"print(\'C:/user/.orca/agent-hooks/a.cmd\')\"'},
        {"type": "command", "command": '\"python\" \"-cprint(\'C:/user/.orca/agent-hooks/a.cmd\')\"'},
    ]
    settings = {"hooks": {"PreToolUse": [{"hooks": list(foreign)}]}}
    apply.configure(settings, tmp_path, None, sys.executable, agent)
    assert all(any(hook in group["hooks"] for group in settings["hooks"]["PreToolUse"]) for hook in foreign)


def test_project_install_still_retires_a_positional_bridge_script(tmp_path):
    bridge = {"type": "command", "command": '\"python\" \"C:/user/.orca/agent-hooks/bridge.py\"'}
    settings = {"hooks": {"PreToolUse": [{"hooks": [bridge]}]}}
    apply.configure(settings, tmp_path, None, sys.executable, "codex")
    assert all(bridge not in group["hooks"] for group in settings["hooks"]["PreToolUse"])


def test_hook_entrypoint_runs_without_a_legacy_desktop(tmp_path):
    tool = Path(__file__).parent
    for script in ("host_boundary.py", "codex_pretool.py"):
        env = host.environment({"LOCALAPPDATA": str(tmp_path), "PYTHONIOENCODING": "cp949"})
        done = subprocess.run([sys.executable, str(tool / script)],
                              input=json.dumps({"tool_name": "exec_command", "tool_input": {"cmd": "orca terminal list"}}),
                              capture_output=True, text=True, encoding="utf-8", env=env, timeout=10)
        assert done.returncode == 0 and not done.stderr
        assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
        broken = subprocess.run([sys.executable, str(tool / script)], input="{",
                                capture_output=True, text=True, encoding="utf-8", env=env, timeout=10)
        assert broken.returncode == 0 and "JSONDecodeError" in broken.stderr
