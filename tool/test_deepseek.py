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
from main import channels, query

FAKE = '''import json, os, pathlib, sys
checked = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "DEEPSEEK_API_KEY")
env = {k: os.environ[k] for k in checked if k in os.environ}   # only what the tests read
pathlib.Path(sys.argv[1]).write_text(json.dumps({"argv": sys.argv[2:], "env": env}), encoding="utf-8")
sys.stdin.readline()
print(json.dumps({"type": "result", "is_error": False, "result": "ok", "session_id": "cli-1"}), flush=True)
sys.stdin.read()
'''


@pytest.fixture
def tree(tmp_path):
    """A repository beside a hub `.env` holding a fixture key."""
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=sk-fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path / "repo")], check=True)
    with patch.object(deepseek.settings, "HUB", tmp_path), patch.object(deepseek, "verify_route", lambda given: None):
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


@pytest.mark.parametrize("isolated", [False, True])
def test_deepseek_goes_to_its_endpoint_by_flag_settings_with_no_credential_in_the_environment(tree, isolated):
    credentials = {"ANTHROPIC_API_KEY": "sk-ant-fixture", "ANTHROPIC_AUTH_TOKEN": "other", "CLAUDE_CODE_OAUTH_TOKEN": "o",
                   "DEEPSEEK_API_KEY": "sk-fixture"}
    with patch.dict("os.environ", credentials):
        run = started(ChatSession(tree, model="deepseek-flash", isolated=isolated), tree)
    assert flag(run["argv"], "--model") == "deepseek-flash" and flag(run["argv"], "--effort") == "max"
    flags = json.loads(flag(run["argv"], "--settings"))
    # Flag settings outrank the user's, the project's and the local ones: none of them can move the endpoint.
    assert flags["env"]["ANTHROPIC_BASE_URL"] == deepseek.URL and flags["env"]["CLAUDE_CODE_USE_BEDROCK"] == ""
    # A gateway's token, key or headers in someone's settings would beat the helper's key: each is blanked.
    assert all(flags["env"][k] == "" for k in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_CUSTOM_HEADERS"))
    assert flags["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek-flash"
    assert str(tree.parent / ".env") in flags["apiKeyHelper"] and "sk-fixture" not in flags["apiKeyHelper"]
    assert flags.get("disableAllHooks", False) is isolated
    assert not set(run["env"]) & set(credentials)   # every tool, hook and MCP server the CLI starts inherits this


def test_the_key_helper_prints_the_key_of_the_file_it_is_given(tree):
    helper = deepseek.flags("deepseek-flash", {})["apiKeyHelper"]
    done = subprocess.run(helper, shell=True, capture_output=True, text=True, encoding="utf-8", check=True)
    assert done.stdout.strip() == "sk-fixture"


def test_a_chosen_effort_wins_and_claude_stays_on_its_own_login(tree):
    run = started(ChatSession(tree, model="deepseek-flash", effort="low"), tree)
    assert flag(run["argv"], "--effort") == "low"
    fixtures = {"ANTHROPIC_API_KEY": "sk-ant-fixture", "ANTHROPIC_AUTH_TOKEN": "t", "CLAUDE_CODE_OAUTH_TOKEN": "o",
                "DEEPSEEK_API_KEY": "d"}   # every captured variable a fixture: nothing real is written to disk
    with patch.dict("os.environ", fixtures):
        run = started(ChatSession(tree, model="opus"), tree)
    assert flag(run["argv"], "--effort") is None and "apiKeyHelper" not in json.loads(flag(run["argv"], "--settings"))
    assert run["env"] == fixtures


def test_no_key_refuses_instead_of_falling_back_to_claude(tree):
    (tree.parent / ".env").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        deepseek.flags("deepseek-flash", {})


ROUTED = '''import json, os, subprocess, sys, urllib.request
flags = json.loads(sys.argv[sys.argv.index("--settings") + 1])
how = os.environ["FAKE_ROUTE"]
if how != "away":   # "away": the turn went where policy sent it, never to the stand-in
    key = subprocess.run(flags["apiKeyHelper"], shell=True, capture_output=True, text=True).stdout.strip()
    token = key if how == "follows" else "gateway-token"
    request = urllib.request.Request(flags["env"]["ANTHROPIC_BASE_URL"] + "/v1/messages", data=b"{}", method="POST",
                                     headers={"Authorization": "Bearer " + token, "x-api-key": token})
    try:
        urllib.request.urlopen(request, timeout=10)
    except Exception:
        pass
'''


@pytest.mark.parametrize("how", ["follows", "gateway", "away"])
def test_the_launch_checks_the_cli_really_follows_the_route(tmp_path, how):
    fake = tmp_path / "cli.py"
    fake.write_text(ROUTED, encoding="utf-8")
    with patch("agent.chat_local.cli_command", lambda name: [sys.executable, str(fake)]), \
         patch.dict(deepseek._verified, {"until": 0.0}), patch.dict("os.environ", {"FAKE_ROUTE": how}):
        if how == "follows":
            deepseek.verify_route({})
            assert deepseek._verified["until"] > 0
        else:   # a policy above `--settings` moved the endpoint, or put its own credential first
            with pytest.raises(RuntimeError, match="DeepSeek 경로를 따르지 않는다"):
                deepseek.verify_route({})


def test_a_helper_path_a_shell_would_rewrite_refuses_deepseek(tree, tmp_path):
    odd = tmp_path / "100% hub"   # a space is quoted; a percent sign would expand in the helper's shell
    odd.mkdir()
    (odd / ".env").write_text("DEEPSEEK_API_KEY=sk-fixture\n", encoding="utf-8")
    with patch.object(deepseek.settings, "HUB", odd), pytest.raises(RuntimeError, match="셸 문자"):
        deepseek.flags("deepseek-flash", {})


def test_saved_turns_and_handoffs_stay_with_their_provider():
    from main import work

    rows = [{"role": "assistant", "session_id": "ds", "model": "deepseek-flash", "provider": "claude"}]
    assert query.resumable(rows, "claude") is None and query.resumable(rows, "deepseek") == "ds"
    refused = [{"role": "context", "handoff": "- Asked: earlier", "provider": "claude"}]
    assert "earlier" in work.started_from(refused, "claude") and work.started_from(refused, "deepseek") == ""
    # A Claude refusal after a DeepSeek turn: the handoff it writes holds only what Claude was asked and said.
    mixed = [{"role": "user", "text": "asked deepseek"}, {"role": "assistant", "text": "deepseek said", "provider": "deepseek"},
             {"role": "user", "text": "asked claude"}, {"role": "assistant", "text": "claude said", "provider": "claude"}]
    written = work.handoff(mixed, "claude")
    assert "asked claude" in written and "claude said" in written and "deepseek" not in written


def test_another_provider_is_a_new_conversation():
    session = ChatSession(".", model="opus")
    session.session_id, session.quota = "claude-thread", [{"name": "5h"}]
    session.reconfigure("deepseek-flash", None)
    assert session.session_id is None and session.quota == []
    session.session_id = "deepseek-thread"
    session.reconfigure("deepseek-flash", "low")   # the same provider keeps its conversation
    assert session.session_id == "deepseek-thread"
    session.reconfigure(None, None)   # the default model is Claude's
    assert session.session_id is None
    rows = [{"role": "assistant", "session_id": "ds", "model": "deepseek-flash"}]
    assert query.resumable(rows, "deepseek") == "ds" and query.resumable(rows, "claude") is None


def test_a_deepseek_turn_shows_no_claude_priced_cost():
    assert deepseek.cost("deepseek-flash", {"total_cost_usd": 0.03}) is None
    assert deepseek.cost("opus", {"total_cost_usd": 0.03}) == 0.03


def test_the_screen_offers_deepseek_with_its_three_efforts():
    assert deepseek.CHOICE in channels.MODELS
    assert channels.efforts_of("deepseek-flash") == {"", "low", "high", "max"}
