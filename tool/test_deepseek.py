"""DeepSeek Flash runs on the Claude CLI pointed at DeepSeek's endpoint, at `max` unless told otherwise.

The provider is a stand-in that records the command and the environment it was started with; nothing calls a model.
"""

import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from agent import chat_busy, chat_session, deepseek
from agent.chat_session import ChatSession
from main import channels

FAKE = '''import json, os, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({"argv": sys.argv[2:], "env": dict(os.environ)}), encoding="utf-8")
sys.stdin.readline()
print(json.dumps({"type": "result", "is_error": False, "result": "ok", "session_id": "cli-1"}), flush=True)
sys.stdin.read()
'''


@pytest.fixture
def tree(tmp_path):
    """A repository beside a hub `.env` holding a fixture key."""
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=sk-fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path / "repo")], check=True)
    with patch.object(deepseek.settings, "HUB", tmp_path):
        yield tmp_path / "repo"


def started(session, tree):
    seen = tree.parent / "seen.json"
    real_popen = subprocess.Popen

    def spawn(command, **kwargs):
        return real_popen([sys.executable, "-X", "utf8", "-c", FAKE, str(seen), *command], **kwargs)

    with patch.object(chat_session.subprocess, "Popen", spawn), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]), \
         patch.object(chat_busy, "WAITS", (0,)):
        events = list(session.say("hi"))
    session.close()
    assert events[-1].kind == "done", events
    return json.loads(seen.read_text(encoding="utf-8"))


def flag(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


def test_deepseek_goes_to_its_endpoint_with_its_key_at_max(tree):
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-fixture", "CLAUDE_CODE_OAUTH_TOKEN": "oauth"}):
        run = started(ChatSession(tree, model="deepseek-flash"), tree)
    assert flag(run["argv"], "--model") == "deepseek-flash" and flag(run["argv"], "--effort") == "max"
    env = run["env"]
    assert env["ANTHROPIC_BASE_URL"] == deepseek.URL and env["ANTHROPIC_AUTH_TOKEN"] == "sk-fixture"
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek-flash"


def test_a_chosen_effort_wins_and_claude_stays_on_its_own_login(tree):
    run = started(ChatSession(tree, model="deepseek-flash", effort="low"), tree)
    assert flag(run["argv"], "--effort") == "low"
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-fixture"}):
        run = started(ChatSession(tree, model="opus"), tree)
    assert flag(run["argv"], "--effort") is None and "ANTHROPIC_BASE_URL" not in run["env"]
    assert run["env"]["ANTHROPIC_API_KEY"] == "sk-ant-fixture"


def test_no_key_refuses_instead_of_falling_back_to_claude(tree):
    (tree.parent / ".env").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        deepseek.environment("deepseek-flash", {})


def test_a_deepseek_turn_shows_no_claude_priced_cost():
    assert deepseek.cost("deepseek-flash", {"total_cost_usd": 0.03}) is None
    assert deepseek.cost("opus", {"total_cost_usd": 0.03}) == 0.03


def test_the_screen_offers_deepseek_with_its_three_efforts():
    assert deepseek.CHOICE in channels.MODELS
    assert channels.efforts_of("deepseek-flash") == {"", "low", "high", "max"}
