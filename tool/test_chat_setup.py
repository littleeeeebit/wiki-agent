"""The boundaries: another checkout, this user's CLI, a failed sign-in, and
the order the install happens in."""

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from main import app as main_app
from main import channels as chat_channels
from main import query
from agent import chat_local
import setup_chat


def test_projects_are_local_and_include_wiki_outside_workspace(tmp_path):
    workspace = tmp_path / "팀원 프로젝트"
    wiki = tmp_path / "이름 바꾼 위키"
    wiki.mkdir()
    (wiki / "adapters").mkdir()
    project = workspace / "같은 이름"
    project.mkdir(parents=True)
    (project / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    (project / ".wiki").mkdir()
    (project / ".wiki/adapter.toml").write_text("agents=[]", encoding="utf-8")
    with patch.object(chat_channels, "WIKI", wiki), patch.object(chat_channels, "WORKSPACE", workspace):
        assert {p["id"] for p in chat_channels.projects()} == {wiki.name, project.name}
        assert chat_channels.repo_for(wiki.name) == wiki
        assert chat_channels.repo_for(project.name) == project
        assert chat_channels.repo_for("../이름 바꾼 위키") is None
        assert chat_channels.repo_for(str(project)) is None
        assert next(p for p in chat_channels.projects() if p["id"] == project.name)["wired"]


def _npm_shim(shim, entry):
    """A shim npm writes carries the real entry point's path verbatim."""
    target = shim.parent / entry
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")
    tail = '"%_prog%"  ' if target.suffix == ".js" else ""
    shim.write_text(rf'... & {tail}"%dp0%\{entry}" %*'.replace("/", "\\"), encoding="utf-8")
    return target


def test_current_user_cli_and_npm_shim_avoid_shell(tmp_path):
    home = tmp_path / "다른 팀원"
    home.mkdir()
    script = _npm_shim(home / "codex.cmd", "node_modules/@openai/codex/bin/codex.js")
    # A package shipping a native binary (claude) runs that binary directly,
    # without node.
    native = _npm_shim(home / "claude.cmd", "node_modules/@anthropic-ai/claude-code/bin/claude.exe")
    # `npm.cmd` carries several paths. The real entry point is the last one.
    npm_cli = _npm_shim(home / "npm.cmd", "node_modules/npm/bin/npm-cli.js")
    (home / "npm.cmd").write_text(r'SET "NPM_PREFIX_JS=%~dp0\node_modules\npm\bin\npm-prefix.js"'
                                  '\n' r'SET "NPM_CLI_JS=%~dp0\node_modules\npm\bin\npm-cli.js"',
                                  encoding="utf-8")
    which = {name: str(home / f"{name}.cmd") for name in ("codex", "claude", "npm")}
    with patch.object(chat_local.shutil, "which", side_effect=lambda name: which.get(name, sys.executable)):
        assert chat_local.cli_command("codex") == [sys.executable, str(script)]
        assert chat_local.cli_command("claude") == [str(native)]
        assert chat_local.cli_command("npm") == [sys.executable, str(npm_cli)]
    with patch.object(chat_local.shutil, "which", return_value=None):
        with pytest.raises(FileNotFoundError):
            chat_local.cli_command("claude")


def test_workspace_cannot_be_the_wiki_itself(tmp_path, monkeypatch):
    """There are no projects under the wiki. Left alone, the list quietly
    shrinks to the wiki alone."""
    root = tmp_path.resolve()
    with patch.object(setup_chat, "ROOT", root):
        with pytest.raises(ValueError, match="위키 자신"):
            setup_chat.install(["codex"], root)
    monkeypatch.setattr(sys, "argv", ["main"])
    with patch.object(chat_channels, "WORKSPACE", chat_channels.WIKI):
        with pytest.raises(SystemExit) as stop:
            main_app.main()
    assert stop.value.code == 2


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_login_uses_official_commands_and_never_changes_user_environment(agent, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", "team-member-owned-config")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "team-member-owned-claude")
    calls = []
    codes = iter([1, 0, 0])

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert "env" not in kwargs  # inherits the OS user and the CLI's own account path
        assert "shell" not in kwargs
        return subprocess.CompletedProcess(command, next(codes))

    with patch.object(setup_chat, "cli_command", side_effect=lambda name: [name]), \
         patch.object(setup_chat.subprocess, "run", run):
        setup_chat.login(agent)
    assert [c[0] for c in calls] == [
        [agent, *setup_chat.AUTH[agent][0]], [agent, *setup_chat.AUTH[agent][1]],
        [agent, *setup_chat.AUTH[agent][0]],
    ]
    assert "stdout" not in calls[1][1] and "stdin" not in calls[1][1]


def test_existing_login_is_preserved_and_failed_login_is_not_success():
    with patch.object(setup_chat, "logged_in", return_value=True), patch.object(setup_chat.subprocess, "run") as run:
        setup_chat.login("claude")
        run.assert_not_called()
    with patch.object(setup_chat, "logged_in", return_value=False), \
         patch.object(setup_chat, "cli_command", return_value=["codex"]), \
         patch.object(setup_chat.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
        with pytest.raises(ValueError, match="로그인이 확인되지"):
            setup_chat.login("codex")


def test_install_preserves_settings_on_failure_and_saves_no_credentials(tmp_path):
    root = tmp_path / "새 위키 checkout"
    root.mkdir()
    config = root / ".chat-local.json"
    config.write_text('{"workspace":"..","model":"previous"}\n', encoding="utf-8")
    before = config.read_bytes()
    calls = []
    models = [{"id": "codex:available-model", "is_default": True}]
    with patch.object(setup_chat, "ROOT", root), patch.object(setup_chat, "SETTINGS", config), \
         patch.object(chat_local, "SETTINGS", config), \
         patch.object(setup_chat, "cli_command", side_effect=lambda name: [name]), \
         patch.object(setup_chat, "environment_python", return_value=Path(sys.executable)), \
         patch.object(setup_chat.venv.EnvBuilder, "create"), \
         patch.object(setup_chat.subprocess, "check_output", return_value="v22.12.0"), \
         patch.object(setup_chat.subprocess, "run", side_effect=lambda args, **kw: (calls.append(args), subprocess.CompletedProcess(args, 0))[1]), \
         patch.object(chat_channels, "codex_models", return_value=models), \
         patch.object(setup_chat, "login", side_effect=ValueError("login failed")):
        with pytest.raises(ValueError, match="login failed"):
            setup_chat.install(["codex"], tmp_path)
        assert config.read_bytes() == before
        with patch.object(setup_chat, "login"):
            setup_chat.install(["codex"], tmp_path)
    assert json.loads(config.read_text(encoding="utf-8")) == {"workspace": "..", "model": "codex:available-model"}
    assert calls[1:4] == [[sys.executable, "-m", "pip", "install", "-r", str(root / "requirements-chat.txt")],
                         ["npm", "ci"], ["npm", "run", "build"]]


def test_codex_only_install_defaults_to_actual_model():
    model = {"id": "codex:my-model", "default_effort": "medium", "efforts": [{"id": "medium"}]}
    with patch.object(chat_channels, "LOCAL", {"model": model["id"]}), \
         patch.object(chat_channels, "codex_models", return_value=[model]), patch.object(query, "_config", {}):
        cfg = query.config("next")
        assert cfg["model"] == model["id"] and cfg["effort"] == "medium"


def test_login_processes_use_each_members_own_profile(tmp_path, monkeypatch):
    fixture = tmp_path / "fake_cli.py"
    fixture.write_text('''import os,sys
from pathlib import Path
agent = sys.argv[1]
profile = Path(os.environ["CODEX_HOME" if agent == "codex" else "CLAUDE_CONFIG_DIR"])
state = profile / (agent + "-fixture-login")
if sys.argv[-1] == "status":
    raise SystemExit(0 if state.exists() else 1)
assert sys.argv[-1] == "login"
state.write_text("fixture only", encoding="utf-8")
''', encoding="utf-8")
    for name in ("팀원 하나", "팀원 둘"):
        profile = tmp_path / name
        profile.mkdir()
        monkeypatch.setenv("CODEX_HOME", str(profile))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(profile))
        with patch.object(setup_chat, "ROOT", tmp_path), \
             patch.object(setup_chat, "cli_command", side_effect=lambda agent: [sys.executable, str(fixture), agent]):
            for agent in ("codex", "claude"):
                assert not setup_chat.logged_in(agent)
                setup_chat.login(agent)
                assert setup_chat.logged_in(agent)
        assert {p.name for p in profile.iterdir()} == {"codex-fixture-login", "claude-fixture-login"}
