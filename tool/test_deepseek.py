"""DeepSeek Flash runs on the Claude CLI pointed at DeepSeek's endpoint, at `max` unless told otherwise.

The provider is a stand-in that records the command and the environment it was started with; nothing calls a model.
"""

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import urllib.error
import urllib.request

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
    held = deepseek.Relay(deepseek.URL, tmp_path / ".env")
    with patch.object(deepseek.settings, "HUB", tmp_path), patch.object(deepseek, "_relay", held):
        yield tmp_path / "repo"
    held.close()


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
    assert flags["env"]["ANTHROPIC_BASE_URL"] == deepseek._relay.url and flags["env"]["CLAUDE_CODE_USE_BEDROCK"] == ""
    # A gateway's token, key or headers in someone's settings would beat the helper's key: each is blanked.
    assert all(flags["env"][k] == "" for k in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_CUSTOM_HEADERS"))
    assert flags["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek-flash"
    # The helper reads the relay's token: the hub's `.env`, and the key in it, are nowhere in the CLI's settings.
    assert str(deepseek._relay.folder / ".env") in flags["apiKeyHelper"] and str(tree.parent) not in flags["apiKeyHelper"]
    assert flags.get("disableAllHooks", False) is isolated
    assert not set(run["env"]) & set(credentials)   # every tool, hook and MCP server the CLI starts inherits this


def test_the_key_helper_gives_the_cli_the_relay_token_and_never_the_key(tree):
    helper = deepseek.flags("deepseek-flash", {})["apiKeyHelper"]
    done = subprocess.run(helper, shell=True, capture_output=True, text=True, encoding="utf-8", check=True)
    assert done.stdout.strip() == deepseek._relay.token != "sk-fixture"


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


@pytest.fixture
def upstream():
    """A stand-in for DeepSeek: it records what reached it and answers in two server-sent events."""

    got, cut = [], threading.Event()

    class Up(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": self.rfile.read(int(self.headers["Content-Length"]))})
            if self.path.startswith("/v1/echo"):   # an error that repeats the key it was sent, as a proxy might
                self.send_response(401)
                self.send_header("x-echo", self.headers["x-api-key"])
                self.send_header("proxy-authenticate", "Basic")
                self.end_headers()
                said = {"type": "error", "error": {"type": "authentication_error",
                                                   "message": f"bad key {self.headers['x-api-key']}"}}
                self.wfile.write(json.dumps(said).replace("sk-", "\\u0073k-").encode())   # escaped, as JSON may
                return
            if self.path.startswith("/v1/inband"):   # an error inside a 200 stream, the key split across two writes
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("connection", "close, request-id")
                self.send_header("request-id", "only-this-connection")
                self.end_headers()
                for part in (b"event: error\ndata: bad key sk-fix", b"ture\n\n"):
                    self.wfile.write(part)
                    self.wfile.flush()
                    time.sleep(0.2)
                return
            if self.path.startswith("/v1/stall"):   # accepted, then silent before any header
                self.connection.settimeout(10)
                if self.connection.recv(1) == b"":
                    cut.set()
                return
            if self.path.startswith("/v1/moved"):
                self.send_response(307)
                self.send_header("location", "http://elsewhere.invalid/v1/messages")
                self.end_headers()
                return
            if self.path.startswith("/v1/slow"):   # one event, then a pause that lasts until the relay hangs up
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"event: a\ndata: 1\n\n")
                self.wfile.flush()
                self.connection.settimeout(10)
                if self.connection.recv(1) == b"":
                    cut.set()
                return
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("set-cookie", "upstream=1")
            self.end_headers()
            for part in (b"event: a\ndata: 1\n\n", b"event: b\ndata: 2\n\n"):
                self.wfile.write(part)
                self.wfile.flush()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Up)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", got, cut
    server.shutdown()
    server.server_close()


def sent(url, headers, path="/v1/messages?beta=true"):
    request = urllib.request.Request(url + path, data=b'{"m": 1}', method="POST",
                                     headers={"content-type": "application/json", "anthropic-version": "1", **headers})
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:
            return answer.status, {k.lower() for k in answer.headers}, answer.read()
    except urllib.error.HTTPError as exc:
        return exc.code, set(), exc.read()


def test_the_relay_alone_holds_the_key_and_passes_on_only_what_deepseek_needs(tmp_path, upstream):
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=sk-fixture\n", encoding="utf-8")
    url, got, _ = upstream
    relay = deepseek.Relay(url, tmp_path / ".env")
    token = relay.token
    try:
        status, headers, body = sent(relay.url, {"x-api-key": token, "Authorization": f"Bearer {token}",
                                                 "x-gateway-auth": "g", "cookie": "c=1"})
        assert status == 200 and body == b"event: a\ndata: 1\n\nevent: b\ndata: 2\n\n" and "set-cookie" not in headers
        reached = got[-1]
        assert reached["path"] == "/v1/messages?beta=true" and reached["body"] == b'{"m": 1}'
        assert reached["headers"]["x-api-key"] == "sk-fixture" and reached["headers"]["authorization"] == "Bearer sk-fixture"
        # A credential or custom header a policy adds stays here, and so does the token.
        assert not {"x-gateway-auth", "cookie"} & set(reached["headers"]) and token not in json.dumps(reached["headers"])
        # Only the token, in every credential header the request carries, gets past.
        for wrong in ({}, {"Authorization": "Bearer gateway"}, {"x-api-key": token, "Authorization": "Bearer gateway"},
                      {"x-api-key": "sk-fixture"}, {"x-api-key": token, "Proxy-Authorization": "Basic x"}):
            assert sent(relay.url, wrong)[0] == 401, wrong
        assert sent(relay.url, {"x-api-key": token}, "/api/oauth/profile")[0] == 404
        assert raw(relay.url, [("x-api-key", token), ("x-api-key", "gateway")])[0] == 401   # a second value counts too
        assert raw(relay.url, [("x-api-key", token)], declared=(64 << 20) + 1)[0] == 413   # refused unread
        # Upstream's error keeps its status, never the key it echoed, nor a header outside the allowed ones.
        status, headers, body = sent(relay.url, {"x-api-key": token}, "/v1/echo")
        assert status == 401 and not {"x-echo", "proxy-authenticate"} & headers
        said = json.loads(body)["error"]["message"]
        assert "authentication_error" in said and "[key]" in said and "sk-fixture" not in said and b"sk-fixture" not in body
        # An error inside a 200 stream, the key split across writes: scrubbed all the same; a header the upstream
        # marks as its connection's own stays.
        status, headers, body = sent(relay.url, {"x-api-key": token}, "/v1/inband")
        assert status == 200 and body == b"event: error\ndata: bad key [key]\n\n" and "request-id" not in headers
        status, headers, body = sent(relay.url, {"x-api-key": token}, "/v1/moved")   # a redirect ends at the relay
        assert status == 502 and "location" not in headers
        (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
        assert sent(relay.url, {"x-api-key": token})[0] == 503
        assert len(got) == 4
        # Framing: a POST without a length, with two, or chunked, and a body that stops short, never go on.
        assert raw(relay.url, [("x-api-key", token)], declared=None, length=False)[0] == 400
        assert raw(relay.url, [("x-api-key", token), ("content-length", "2")])[0] == 400
        assert raw(relay.url, [("x-api-key", token), ("transfer-encoding", "chunked")])[0] == 400
        assert len(got) == 4
    finally:
        relay.close()
    assert not relay.folder.exists()


def raw(url, headers, declared=None, path="/v1/messages", length=True):
    """A request http.client sends as written: repeated headers, a declared length its body never fills, none."""

    host, port = url.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    try:
        conn.putrequest("POST", path)
        for name, value in headers:
            conn.putheader(name, value)
        if length:
            conn.putheader("content-length", str(declared if declared is not None else 2))
        conn.endheaders(b"" if declared is not None or not length else b"{}")
        answer = conn.getresponse()
        return answer.status, answer.read()
    finally:
        conn.close()


def test_a_caller_that_hangs_up_cuts_the_request_to_deepseek_and_close_waits_for_none(tmp_path, upstream):
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=sk-fixture\n", encoding="utf-8")
    url, _, cut = upstream
    relay = deepseek.Relay(url, tmp_path / ".env")
    host, port = relay.url.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.request("POST", "/v1/slow", body=b"{}", headers={"x-api-key": relay.token})
    answer = conn.getresponse()
    assert answer.status == 200 and answer.read1(64).startswith(b"event: a")
    answer.close()   # the person pressed Stop: the CLI is gone
    conn.close()
    assert cut.wait(5), "the relay must hang up on DeepSeek too"
    # The same while DeepSeek has accepted the request but sent no header yet.
    cut.clear()
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.putrequest("POST", "/v1/stall")
    conn.putheader("x-api-key", relay.token)
    conn.putheader("content-length", "2")
    conn.endheaders(b"{}")
    time.sleep(0.5)
    conn.close()
    assert cut.wait(5), "a stall before headers must not outlive the caller"
    # A connection that never finishes its headers holds a slot, and close cuts it too.
    silent = socket.create_connection((host, int(port)))
    silent.sendall(b"POST /v1/messages HTTP/1.1\r\n")
    # A request still in flight when the app closes: close cuts it rather than waiting out the read timeout.
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.request("POST", "/v1/slow", body=b"{}", headers={"x-api-key": relay.token})
    conn.getresponse().read1(64)
    began = time.monotonic()
    relay.close()
    conn.close()
    silent.close()
    assert time.monotonic() - began < 5 and not relay.folder.exists() and not relay.threads


def test_a_helper_path_a_shell_would_rewrite_refuses_deepseek(tmp_path):
    odd = tmp_path / "100% relay"   # a space is quoted; a percent sign would expand in the helper's shell
    with pytest.raises(RuntimeError, match="셸 문자"):
        deepseek.built({}, "http://127.0.0.1:1", odd / ".env")


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
    # A DeepSeek answer saved before DeepSeek had its own label, and a DeepSeek question whose answer never got
    # written (the process died first), are DeepSeek's all the same.
    legacy = [{"role": "user", "text": "asked first"}, {"role": "assistant", "text": "flash said", "model": "deepseek-flash",
                                                         "provider": "claude"},
              {"role": "user", "text": "asked flash, unanswered", "provider": "deepseek"},
              {"role": "user", "text": "asked claude", "provider": "claude"},
              {"role": "assistant", "text": "claude said", "provider": "claude"}]
    written = work.handoff(legacy, "claude")
    assert "asked claude" in written and "claude said" in written
    assert all(t not in written for t in ("asked first", "flash said", "unanswered"))


class Flash:
    """A DeepSeek session that answers each turn at once."""

    model, is_codex, id, parent_id = "deepseek-flash", False, "flash-1", None

    def say(self, text, halt=None):
        yield chat_session.Event("done", "flash answer", {})

    def stop(self, halt=None):
        pass


def test_planning_and_merge_maintenance_save_both_sides_of_a_turn_with_its_provider(tmp_path):
    from main import maintenance, planning, work

    rows = []
    keep = lambda path, role, text, **extra: rows.append({"role": role, "text": text, **extra})  # noqa: E731
    with patch.object(work, "remember", keep):
        planning.consume(tmp_path, work.Run(Flash()), "plan it")
        with patch.object(work, "session", lambda *a, **k: Flash()):
            maintenance.repair(tmp_path, {}, [("a.md", "broken link")])
    assert [(r["role"], r["provider"]) for r in rows] == [("user", "deepseek"), ("assistant", "deepseek")] * 2
    # What a later Claude refusal hands on from these saved rows: none of it.
    told = work.handoff(rows + [{"role": "user", "text": "asked claude", "provider": "claude"}], "claude")
    assert "flash answer" not in told and "plan it" not in told and "asked claude" in told


def test_a_refused_reviews_handoff_carries_only_its_own_providers_words(tmp_path):
    from main import loop

    spec = {"repo": "proj", "pr": {"number": 7}}
    rounds = [{"n": 1, "head": "a" * 40, "verdict": "머지 불가", "said": "codex words", "items": [{"head": "codex finding"}],
               "reviewer": {"model": "codex:gpt"}},
              {"n": 2, "head": "b" * 40, "verdict": "머지 불가", "said": "claude words", "items": [{"head": "claude finding"}],
               "reviewer": {"model": "opus"}}]
    with patch.object(loop, "folder", lambda *key: tmp_path), patch.object(loop, "counted", lambda s: rounds), \
         patch.object(loop.specs, "brief", lambda s: "the spec\n"):
        loop.refused_cell(spec, "claude")
    told = (tmp_path / "handoff.md").read_text(encoding="utf-8")
    assert told.startswith("<!-- provider: claude -->\n") and "claude words" in told and "claude finding" in told
    assert "codex words" not in told and "codex finding" not in told and "Round 1 at aaaaaaa" in told


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
