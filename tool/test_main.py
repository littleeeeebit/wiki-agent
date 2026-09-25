"""The program's main: the wiki query's two stages, the work pane's worktrees
and approvals, the translation switch, and the door only its own screen opens."""

import json
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from agent import chat_local, chat_session
from main import app as main_app
from main import channels as chat_channels
from main import query as chat
from main import specs, work
import translate
from agent.chat_session import ChatSession, Event


class Screen(TestClient):
    """A screen that knows its project: it names the one the server is on
    when it sends, unless a test names another."""

    def request(self, method, url, **kwargs):
        from urllib.parse import quote

        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("X-Project", quote(chat.project()))
        return super().request(method, url, headers=headers, **kwargs)


def client() -> TestClient:
    """The screen as the server sees it: same host, same origin."""
    return Screen(main_app.app, base_url="http://127.0.0.1:8787")


@pytest.fixture(autouse=True)
def no_machine_settings(tmp_path):
    with patch.object(chat_channels, "LOCAL", {}), patch.object(chat, "LOGS", tmp_path), \
         patch.object(chat, "_project", None), patch.object(chat, "_config", {}), \
         patch.object(chat, "_sessions", {}), patch.object(chat, "_busy", {}), \
         patch.object(main_app, "SWITCH", tmp_path / "main.json"), \
         patch.object(work, "LOGS", tmp_path / "work"), \
         patch.object(work, "_sessions", {}), patch.object(work, "_busy", {}), \
         patch.object(work, "_runs", {}), patch.object(specs, "SPECS", tmp_path / "specs"):
        yield


def test_explanation_isolated(tmp_path):
    calls = []
    source = '설치 13건 통과. 자동 실행은 미확인. `docs/setup.md:8`'

    class Rewrite:
        def __init__(self, repo, **kwargs):
            calls.append((Path(repo), kwargs))

        def say(self, text):
            assert json.loads(text) == {"source_answer": source}
            yield Event("done", "설정 검사 13개는 통과했습니다. 자동으로 실행되는지는 아직 모릅니다.")

        def close(self):
            calls.append("closed")

    with patch.object(chat_session, "ChatSession", Rewrite):
        assert list(chat_session.explain(source, "codex:test-model", "low"))[-1].kind == "done"
    repo, kwargs = calls[0]
    assert kwargs["isolated"] and kwargs["tools"] == ""
    assert kwargs["model"] == "codex:test-model" and kwargs["effort"] == "low"
    assert not repo.exists() and calls[-1] == "closed"
    assert "Task: faithfully explain" in kwargs["system"]
    assert "Task: answer from verifiable" not in kwargs["system"]
    assert all(c.preamble not in kwargs["system"] for c in chat_channels.CHANNELS)
    assert kwargs["system"].isascii() and chat_channels.ANSWER_PROMPT.isascii()
    assert all(c.preamble.isascii() for c in chat_channels.CHANNELS)


def test_stream_persists_both_and_keeps_original_on_rewrite_failure(tmp_path):
    class Original:
        def say(self, text):
            # Codex may send a finished response with no character deltas.
            yield Event("done", "정확한 원문 13건", {"session_id": "answer-session", "error": False})

    def rewrite(source, model, effort):
        assert source == "정확한 원문 13건"
        yield Event("delta", "쉬운 ")
        yield Event("done", "쉬운 설명 13건")

    with patch.object(chat, "LOGS", tmp_path), patch.object(chat, "session", return_value=Original()), \
         patch.object(chat, "hits_for", return_value=[]), patch.object(chat, "explain", rewrite):
        web = client()
        response = web.post("/api/say/wiki", json={"text": "검사 결과?"})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert [e["kind"] for e in events] == ["done", "simple_start", "simple_delta", "simple_done"]
        saved = web.get("/api/log/wiki").json()[-1]
        assert saved["text"] == "정확한 원문 13건" and saved["simple_text"] == "쉬운 설명 13건"
        assert saved["session_id"] == "answer-session"
        assert "wiki" not in chat._busy
        with patch.object(chat, "explain", return_value=iter([Event("error", "설명 호출 실패")])):
            response = web.post("/api/say/wiki", json={"text": "다시?"})
            assert '"kind": "simple_error"' in response.text
            saved = web.get("/api/log/wiki").json()[-1]
            assert saved["text"] == "정확한 원문 13건" and not saved["simple_text"]
            assert "설명 호출 실패" in saved["simple_error"]
            assert not saved.get("error")
        assert "wiki" not in chat._busy


def test_failed_original_never_rewritten(tmp_path):
    class Failed:
        def say(self, text):
            yield Event("delta", "미완성")
            yield Event("done", "모델 오류", {"error": True})

    with patch.object(chat, "LOGS", tmp_path), patch.object(chat, "session", return_value=Failed()), \
         patch.object(chat, "hits_for", return_value=[]), patch.object(chat, "explain") as rewrite:
        response = client().post("/api/say/wiki", json={"text": "질문"})
        assert '"kind": "error"' in response.text
        rewrite.assert_not_called()
        assert chat.recall("wiki")[-1]["error"] == "모델 오류"


# A Codex `app-server`: a resume it cannot do, a read session's thread, and
# two turns in one process, each reporting two model calls' usage.
APP_SERVER = '''import json, sys
read = lambda: json.loads(sys.stdin.readline())
say = lambda m: print(json.dumps(m), flush=True)
m = read(); say({"id": m["id"], "result": {}})
read()
m = read()
if m["method"] == "thread/resume":
    say({"id": m["id"], "error": {"code": -32600, "message": "no rollout found for thread id " + m["params"]["threadId"]}})
    m = read()
p = m["params"]
assert m["method"] == "thread/start", m
assert (p["sandbox"], p["approvalPolicy"], p["developerInstructions"]) == ("read-only", "never", "Find evidence."), p
say({"id": m["id"], "result": {"thread": {"id": "th-1"}}})
while True:
    m = read(); assert m["params"]["effort"] == "low"; say({"id": m["id"], "result": {"turn": {}}})
    for last in ({"inputTokens": 100, "cachedInputTokens": 60, "outputTokens": 5, "reasoningOutputTokens": 2},
                 {"inputTokens": 120, "cachedInputTokens": 100, "outputTokens": 7, "reasoningOutputTokens": 0}):
        say({"method": "thread/tokenUsage/updated", "params": {"threadId": "th-1", "turnId": "t",
             "tokenUsage": {"last": last, "total": {**last, "inputTokens": 99999}}}})
    say({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "최종 답변"}}})
    say({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
'''


def test_a_codex_focus_keeps_one_app_server_and_the_explanation_stays_isolated(tmp_path):
    commands = []
    real_popen = subprocess.Popen
    explaining = '''import json,sys
text = sys.stdin.read()
for event in [
    {"type":"thread.started","thread_id":"owned-session"},
    {"type":"item.completed","item":{"type":"agent_message","text":"쉬운 설명"}},
    {"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":7}}
]: print(json.dumps(event), flush=True)
'''

    def spawn(command, **kwargs):
        commands.append(command)
        fixture = APP_SERVER if "app-server" in command else explaining
        return real_popen([sys.executable, "-X", "utf8", "-c", fixture], **kwargs)

    with patch.object(chat_session.subprocess, "Popen", spawn), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
        session = ChatSession(tmp_path, model="codex:test-model", system="Find evidence.", effort="low")
        try:
            for _ in range(2):
                events = list(session.say("질문"))
                assert events[-1].text == "최종 답변"
                # The sum of each call's `last`, never the thread's running total.
                assert events[-1].meta["tokens"] == {"in": 220, "out": 12, "cache_read": 160, "reasoning": 2}
                assert session.alive
            assert commands == [["codex", "app-server", "--disable", "multi_agent"]]   # one process, two turns
        finally:
            session.close()

        # A thread it cannot resume starts afresh, and says so once.
        lost = ChatSession(tmp_path, model="codex:test-model", system="Find evidence.", effort="low",
                           resume="exec-thread")
        try:
            kinds = [(e.kind, e.text) for e in lost.say("질문")]
            assert kinds[0][0] == "context" and "exec-thread" in kinds[0][1]
            assert lost.session_id == "th-1"
            assert [e.kind for e in lost.say("질문")][0] != "context"
        finally:
            lost.close()

        list(chat_session.explain("설치 성공, 자동 실행 미확인.", "codex:test-model", "low"))
        isolated = commands[-1]
        assert isolated[:2] == ["codex", "exec"]
        assert "--ignore-user-config" in isolated and "--ephemeral" in isolated
        assert "resume" not in isolated and "shell_tool" in isolated
        assert "Task: answer from verifiable" not in " ".join(isolated)
        assert isolated[isolated.index("--model") + 1] == "test-model"


def test_provider_switch_and_config_validation(tmp_path):
    web = client()
    models = [{"id": "codex:test-model", "default_effort": "low",
               "efforts": [{"id": e} for e in ("", "low", "high", "max")]},
              {"id": "codex:another-model", "default_effort": "high",
               "efforts": [{"id": e} for e in ("", "low", "high")]}]
    with patch.object(chat_channels, "repo_for", return_value=tmp_path), \
         patch.object(chat_channels, "codex_models", return_value=models), \
         patch.object(chat, "_config", {}), patch.object(chat, "_sessions", {}):
        assert web.post("/api/config/wiki", json={"repo": "sample", "model": "codex"}).status_code == 400
        assert web.post("/api/config/wiki", json={"repo": "sample", "model": "codex:missing"}).status_code == 400
        assert web.post("/api/config/wiki", json={"repo": "sample", "model": "codex:another-model", "effort": "max"}).status_code == 400
        response = web.post("/api/config/wiki", json={"repo": "sample", "model": "codex:test-model", "effort": "max"})
        assert response.json()["switched"]
        response = web.post("/api/config/wiki", json={"repo": "sample", "model": "codex:test-model", "effort": "max"})
        assert response.status_code == 200 and not response.json()["kept"]
        response = web.post("/api/config/wiki", json={"repo": "sample", "model": "codex:another-model"})
        assert response.json()["kept"] and response.json()["effort"] == "high"
        # A Claude name typed by hand goes through; a shell-shaped one does not.
        assert web.post("/api/config/wiki", json={"repo": "sample", "model": "claude-opus-5-5"}).status_code == 200
        assert web.post("/api/config/wiki", json={"repo": "sample", "model": "opus; rm -rf"}).status_code == 400
        with patch.object(chat, "_busy", {"wiki": object()}):
            assert web.post("/api/reset/wiki").status_code == 409


def test_project_shared_sessions_and_records_isolated(tmp_path):
    repos = {name: tmp_path / name for name in ("a", "b")}
    for repo in repos.values():
        (repo / ".git").mkdir(parents=True)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=repos.get):
        web.post("/api/config/next", json={"repo": "a"}).raise_for_status()
        assert {c["repo"] for c in web.get("/api/channels").json()} == {"a"}
        a = chat.session("retro")
        a.session_id = "a-context"
        chat.remember("retro", "assistant", "a 회고", session_id="a-context", provider="claude")
        web.post("/api/config/next", json={"repo": "b"}).raise_for_status()
        assert web.get("/api/log/retro").json() == []
        assert chat.session("retro") is not a
        chat.remember("retro", "assistant", "b 회고")
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        assert {c["repo"] for c in web.get("/api/channels").json()} == {"a"}
        assert chat.session("retro") is a and a.session_id == "a-context"
        assert [r["text"] for r in web.get("/api/log/retro").json()] == ["a 회고"]
        with patch.object(chat, "_busy", {"next": object()}):
            assert web.post("/api/config/wiki", json={"repo": "b"}).status_code == 409
        assert chat.project() == "a"
        chat._project = None
        assert chat.project() == "a", "서버 재시작 때 프로젝트 선택을 복원해야 한다"
        chat._sessions.clear()
        assert chat.session("retro").session_id == "a-context"
        web.post("/api/reset/retro").raise_for_status()
        assert chat.session("retro") is not a
        assert chat.session("retro").session_id is None
        assert web.get("/api/log/retro").json()[0]["text"] == "a 회고"


def test_a_focus_is_told_how_to_search_its_own_repository(tmp_path):
    repos = {name: tmp_path / name for name in ("a", "b c")}
    for repo in repos.values():
        (repo / ".git").mkdir(parents=True)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=repos.get):
        web.post("/api/config/next", json={"repo": "a"}).raise_for_status()
        system = chat.session("retro").system
        assert system.startswith(chat_channels.ANSWER_PROMPT.strip())
        assert "the search command first" in system
        search = (chat_channels.WIKI / "tool/search").as_posix()
        assert f"'{search}' --project '{repos['a'].resolve().as_posix()}' '<query>'" in system, system
        web.post("/api/config/next", json={"repo": "b c"}).raise_for_status()
        spaced = chat.session("retro").system
        assert f"--project '{repos['b c'].resolve().as_posix()}'" in spaced


def test_the_search_command_runs_as_printed_in_both_shells(tmp_path):
    """Each line of the note, pasted into its shell, reaches Python with the
    path intact: a space, `&`, `$` and a quote in the repository's path. A
    quoted Python needs `&` in PowerShell (review round 1); a bare or double-
    quoted `&` or `$` was read by the shell (round 2). The query goes in as
    the note says to write it: `$QUOKKAHOME` and a quote stay text (round 3);
    expanded, the variable is empty and nothing is found."""

    import os
    import shutil

    repo = tmp_path / "R&D $Ops it's"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "deploy.md").write_text("# Deploy\n\n## Order\n\nset $QUOKKAHOME first.\n",
                                             encoding="utf-8")
    query = {"In Bash": "it'\\''s $QUOKKAHOME", "In PowerShell": "it''s $QUOKKAHOME"}
    note = chat.search_note(repo)
    lines = {line.split(":", 1)[0]: line.split(": ", 1)[1].replace("[--k 8]", "--k 1")
             for line in note.splitlines() if line.startswith("In ")}
    # Claude's Bash on Windows is Git Bash; a bare `bash` there may be WSL's.
    bash = shutil.which("bash")
    if sys.platform == "win32" and shutil.which("git"):
        found = Path(shutil.which("git")).resolve().parents[1] / "bin" / "bash.exe"
        bash = str(found) if found.exists() else None
    shells = {"In Bash": [bash, "-c"], "In PowerShell": [shutil.which("pwsh"), "-NoProfile", "-Command"]}
    ran = 0
    for label, shell in shells.items():
        if not shell[0]:
            continue
        done = subprocess.run([*shell, lines[label].replace("<query>", query[label])],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=dict(os.environ, WIKI_SEARCH="off"), timeout=120)
        assert "## docs/deploy.md:3" in done.stdout, (label, lines[label], done.stdout, done.stderr)
        ran += 1
    if not ran:
        pytest.skip("neither bash nor pwsh on this machine")


def test_legacy_records_remain_visible(tmp_path):
    (tmp_path / "retro.jsonl").write_text(
        json.dumps({"role": "assistant", "text": "소속 모름"}) + "\n", encoding="utf-8")
    web = client()
    assert web.get("/api/log/retro").json() == []
    assert web.get("/api/log/retro?legacy=true").json()[0]["text"] == "소속 모름"
    import chat_post
    (tmp_path / ".git").mkdir()
    with patch.object(chat_post, "LOGS", tmp_path), patch.object(chat_channels, "repo_for", return_value=tmp_path):
        chat_post.post("retro", "프로젝트 회고", project=tmp_path)
        assert web.get("/api/log/retro").json()[0]["text"] == "프로젝트 회고"


def test_model_discovery_and_failure_reporting():
    real_popen = subprocess.Popen
    fixture = '''import json,sys
for line in sys.stdin:
    req = json.loads(line)
    if req.get("method") == "initialize":
        print(json.dumps({"id":req["id"],"result":{}}), flush=True)
    elif req.get("method") == "model/list":
        cursor = req["params"].get("cursor")
        model = {"model":"second" if cursor else "first", "displayName":"Example model",
                 "defaultReasoningEffort":"medium",
                 "supportedReasoningEfforts":[{"reasoningEffort":"medium"},{"reasoningEffort":"ultra"}]}
        print(json.dumps({"method":"notice"}), flush=True)
        print(json.dumps({"id":req["id"],"result":{"data":[model],"nextCursor":None if cursor else "page2"}}), flush=True)
'''

    def spawn(command, **kwargs):
        assert command == ["codex", "app-server"]
        return real_popen([sys.executable, "-X", "utf8", "-c", fixture], **kwargs)

    chat_channels.codex_models.cache_clear()
    try:
        with patch.object(chat_local.subprocess, "Popen", spawn), \
             patch.object(chat_local, "cli_command", side_effect=lambda name: [name]):
            models = chat_channels.codex_models()
            assert [m["id"] for m in models] == ["codex:first", "codex:second"]
            assert models[0]["efforts"][-1]["id"] == "ultra"
    finally:
        chat_channels.codex_models.cache_clear()
    with patch.object(chat_channels, "codex_models", side_effect=RuntimeError("offline")):
        response = client().get("/api/options").json()
        assert response["models"] == chat_channels.MODELS and "offline" in response["codex_error"]


# -- translation -------------------------------------------------------------


def test_translate_api_guards_its_own_budget():
    """One cache and one bill, shared with the hooks.

    A screen that posts a whole document burns what the hooks were going to
    spend, and nothing downstream of here would notice.
    """

    web = client()
    assert web.post(
        "/api/translate", json={"texts": ["x"], "direction": "ko->ko"}
    ).status_code == 400
    assert web.post(
        "/api/translate", json={"texts": ["x"] * (main_app.TRANSLATE_MAX + 1)}
    ).status_code == 413
    assert web.post(
        "/api/translate", json={"texts": ["x" * (main_app.TRANSLATE_CHARS + 1)]}
    ).status_code == 413


def test_map_words_go_but_identifiers_stay():
    """The map renders a headline and one rule line. Nothing else.

    Carry a slug, a path or a config value along and the links break while
    `graph.json`'s keys quietly differ on screen only. The translator lifts
    those out before the request; this checks the door in front of it hands
    the text over intact, because a door that mangles it first leaves the
    protection nothing to protect. No network — the translator is faked.
    """

    seen: list[str] = []

    def fake(texts, direction=translate.EN_KO, deadline=None):
        seen.extend(texts)
        return [f"[ko]{t}" for t in texts]

    line = "Rule. `tool/lint.py` and [[hooks-fail-open]] decide `{review_dir}`."
    with patch.object(main_app.translate, "translate", fake):
        answer = client().post(
            "/api/translate", json={"texts": ["Emphasis is scarce", line]}
        ).json()

    assert seen == ["Emphasis is scarce", line]
    assert answer["texts"][0] == "[ko]Emphasis is scarce"
    # `translate.protect` does the real work, and `test_translate.py` keeps it
    # honest. What is checked here is that these spans are the ones it lifts.
    kept = translate.protect(line, translate.glossary()[0])[1]
    assert "tool/lint.py" in " ".join(kept)
    assert "[[hooks-fail-open]]" in " ".join(kept)
    assert "{review_dir}" in " ".join(kept)


# -- the door ----------------------------------------------------------------


def test_only_this_screen_gets_in():
    """This server approves writes. Another site's page reaches 127.0.0.1 too.

    A cross-site `fetch` carries its own `Origin`, and a rebinding attack
    carries its own domain in `Host`. Both are refused before any route runs.
    """

    web = client()
    assert web.get("/api/switch").status_code == 200
    assert web.post("/api/switch", json={"translate": True},
                    headers={"Origin": "http://127.0.0.1:8787"}).status_code == 200
    assert web.post("/api/switch", json={"translate": False},
                    headers={"Origin": "https://example.com"}).status_code == 403
    rebound = TestClient(main_app.app, base_url="http://example.com:8787")
    assert rebound.get("/api/switch").status_code == 403
    assert web.get("/api/switch").json()["translate"] is True


def test_translation_off_sends_nothing():
    """Off is no request at all, and it is checked where the request would leave."""

    web = client()
    web.post("/api/switch", json={"translate": False}).raise_for_status()
    with patch.object(main_app.translate, "translate", side_effect=AssertionError("요청이 나갔다")):
        answer = web.post("/api/translate", json={"texts": ["Hello"]}).json()
    assert answer == {"texts": ["Hello"], "off": True}
    assert web.get("/api/switch").json()["translate"] is False


# -- the work pane -----------------------------------------------------------


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "a"], check=True)
    return repo


def _made(task: str = "t1") -> str:
    """A worktree of the selected project, as a spec's `[시작]` makes one —
    the screen has no way of its own to make one any more."""

    from workspace import create

    return str(create(chat.current_repo(), task))


class Agent:
    """A write session that asks once and finishes."""

    made: list = []

    def __init__(self, path, model="", effort="", write=False, system=""):
        assert write
        self.id, self.session_id, self.alive, self.parent_id = uuid.uuid4().hex, None, True, None
        self.is_codex = model.startswith("codex:")
        self.pending, self.rules = {"r1"}, []
        Agent.made.append(self)

    def say(self, text, halt=None):
        yield Event("approval", "Write · b.txt", {"id": "r1", "tool": "Write", "input": {}}, self.id)
        yield Event("done", "했다", {"session_id": "cli-1", "error": False}, self.id)

    def answer(self, rid, allow, scope="once"):
        if rid not in self.pending:
            return False
        self.pending.discard(rid)
        if scope == "session":
            self.rules.append({"kind": "file", "tool": "Write"})
        return True

    def clear_rules(self):
        self.rules = []

    def reconfigure(self, model, effort):
        pass

    def close(self):
        self.alive = False


class Slow(Agent):
    """Asks, then waits until let go or stopped: a turn that outlives its response."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.go, self.stopped = threading.Event(), False

    def say(self, text, halt=None):
        self.alive = True
        yield Event("tool", "Read · a.txt", {}, self.id)
        yield Event("approval", "Write · b.txt", {"id": "r1", "tool": "Write", "input": {}}, self.id)
        self.go.wait(10)
        self.go.clear()
        if self.stopped:
            self.stopped = False
            yield Event("error", "프로세스가 닫혔다.", {}, self.id)
            return
        yield Event("done", "했다", {"session_id": "cli-1", "error": False}, self.id)

    def stop(self, halt):
        self.stopped, self.alive = True, False
        self.go.set()


def settled(path: str):
    """The path's run once its turn has ended."""

    run = work._runs[path]
    with run.wake:
        assert run.wake.wait_for(lambda: run.done, 10)
    return run


def parse(stream: str) -> list[dict]:
    return [json.loads(line[6:]) for line in stream.splitlines() if line.startswith("data: ")]


def test_work_opens_only_its_own_worktrees(tmp_path):
    """A path from the screen is opened only when `workspace` listed it."""

    repo = _repo(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"):
        path = _made()
        assert [r["name"] for r in web.get("/api/worktrees").json()["rows"]] == ["t1"]
        for other in (str(repo), str(tmp_path / "elsewhere")):
            assert web.post("/api/work/say", json={"path": other, "text": "x"}).status_code == 404
            assert web.get("/api/work/log", params={"path": other}).status_code == 404
            assert web.get("/api/file", params={"repo": other, "path": "a.txt"}).status_code == 404
        assert web.get("/api/file", params={"repo": path, "path": "a.txt"}).json()["lines"] == ["a"]
        assert web.get("/api/file", params={"repo": path, "path": "../proj/a.txt"}).status_code == 404
        assert "지웠다" in web.post("/api/worktrees/remove", json={"path": path}).json()["text"]
        assert web.get("/api/worktrees").json()["rows"] == []


def test_an_approval_goes_only_to_the_session_that_asked(tmp_path):
    """Answered with the asking session's id, once. A reset makes a new id."""

    repo = _repo(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"), patch.object(work, "ChatSession", Agent):
        path = _made()
        stream = web.post("/api/work/say", json={"path": path, "text": "b.txt 를 써라"}).text
        events = [json.loads(line[6:]) for line in stream.splitlines() if line.startswith("data: ")]
        assert [e["kind"] for e in events] == ["approval", "done"]
        asked = events[0]
        assert asked["session_id"] == Agent.made[-1].id and asked["meta"]["id"] == "r1"

        wrong = {"path": path, "session_id": "someone-else", "id": "r1", "allow": True}
        assert web.post("/api/work/answer", json=wrong).status_code == 409
        right = {**wrong, "session_id": asked["session_id"]}
        assert web.post("/api/work/answer", json=right).status_code == 200
        assert web.post("/api/work/answer", json=right).status_code == 409

        log = web.get("/api/work/log", params={"path": path}).json()
        assert [r["role"] for r in log["rows"]] == ["user", "assistant"]
        assert log["session_id"] == asked["session_id"]

        # A new process resumes the CLI's conversation from the record.
        work._sessions.clear()
        web.post("/api/work/say", json={"path": path, "text": "다음"}).raise_for_status()
        assert Agent.made[-1].session_id == "cli-1"

        web.post("/api/work/reset", json={"path": path}).raise_for_status()
        assert web.post("/api/work/answer", json=right).status_code == 409


def test_the_draft_and_the_bare_worktree_are_gone(tmp_path):
    """Loop stage 6: work starts from a spec's `[시작]` or a review loop. The
    answer-to-draft route and the `새 작업` box's route are not there to call."""

    web = client()
    with patch.object(chat_channels, "repo_for", return_value=tmp_path):
        assert web.post("/api/draft", json={"question": "x"}).status_code in (404, 405)
        assert web.post("/api/worktrees", json={"task": "t1"}).status_code in (404, 405)
    assert not hasattr(chat, "WRITERS") and not hasattr(work, "make")


def test_the_shell_s_pipe_neither_blocks_the_server_nor_outlives_it():
    """Started as the Tauri shell starts it: stdin is a pipe the shell holds.

    Reading that pipe in a thread once blocked every `CreateProcess` on
    Windows — `/api/switch` answered and every route that runs `git` hung.
    Closing the pipe must bring the server down.
    """

    import socket
    import time
    import urllib.request

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    main_dir = Path(__file__).resolve().parent / "main"
    server = subprocess.Popen([sys.executable, str(main_dir), "--port", str(port), "--exit-with-stdin"],
                              stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/switch", timeout=1):
                    break
            except OSError:
                assert time.monotonic() < deadline, "서버가 안 떴다"
                time.sleep(0.2)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/channels", timeout=20) as reply:
            assert json.loads(reply.read())
        server.stdin.close()
        assert server.wait(timeout=20) == 0
    finally:
        server.kill()


def test_a_body_that_never_starts_holds_nothing(tmp_path):
    """The client can leave right after the headers, and the body never runs.

    A hold taken before the body had no `finally` to release it: that
    worktree, or that focus, answered 409 to everything until a restart.
    """

    repo = _repo(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"), patch.object(work, "ChatSession", Agent):
        path = _made()
        work.say(work.Order(path=path, text="x"))
        chat.say("wiki", chat.Say(text="x"))
        settled(path)   # a work turn runs on without its body, and lets go when it ends
        assert not work._busy and not chat._busy
        assert web.post("/api/work/say", json={"path": path, "text": "y"}).status_code == 200


def test_a_new_task_under_an_old_name_starts_fresh(tmp_path):
    """Removing `t1` and making `t1` again gives the same path. The old
    conversation and the old CLI session must not come with it."""

    repo = _repo(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"), patch.object(work, "ChatSession", Agent):
        path = _made()
        web.post("/api/work/say", json={"path": path, "text": "old"}).raise_for_status()
        web.post("/api/worktrees/remove", json={"path": path}).raise_for_status()
        again = _made()
        assert again == path
        assert web.get("/api/work/log", params={"path": again}).json()["rows"] == []
        web.post("/api/work/say", json={"path": again, "text": "new"}).raise_for_status()
        assert Agent.made[-1].session_id is None


def test_a_switch_waits_for_a_short_request_and_not_for_a_turn(tmp_path):
    """Making, removing and resetting read the project partway through, and a
    switch waits for them. A turn took its repository with its hold, and its
    session keeps its approvals reachable; a switch does not wait for it."""

    repos = {name: tmp_path / name for name in ("a", "b")}
    for repo in repos.values():
        (repo / ".git").mkdir(parents=True)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=repos.get):
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        with patch.object(work, "_busy", {str(repos["a"] / "x"): chat.Held("short")}):
            assert web.post("/api/config/wiki", json={"repo": "b"}).status_code == 409
            assert web.post("/api/config/wiki", json={"repo": "a", "effort": "high"}).status_code == 200
        with patch.object(work, "_busy", {str(repos["a"] / "x"): chat.Held("turn")}):
            web.post("/api/config/wiki", json={"repo": "b"}).raise_for_status()


def test_an_accepted_instruction_holds_its_worktree_before_its_body_starts(tmp_path):
    """Between accepting a request and the first byte of its body, the project
    switched away and the worktree could be removed. Held from acceptance, and
    released when the body is dropped unstarted."""

    import gc

    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    repos = {name: _repo(tmp_path / name) for name in ("a", "b")}
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=repos.get), \
         patch.object(work, "ChatSession", Slow):
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        path = _made()
        waiting = work.say(work.Order(path=path, text="x"))
        assert work._busy[path].kind == "turn", "도는 턴은 전환을 막지 않는다"
        assert web.post("/api/worktrees/remove", json={"path": path}).status_code == 409
        assert web.post("/api/work/reset", json={"path": path}).status_code == 409
        asking = chat.say("next", chat.Say(text="x"))
        assert web.post("/api/config/wiki", json={"repo": "b"}).status_code == 409
        del waiting, asking
        gc.collect()
        assert not chat._busy
        # The work turn is not its body: it holds until it ends.
        assert path in work._busy
        Agent.made[-1].go.set()
        settled(path)
        assert not work._busy
        web.post("/api/config/wiki", json={"repo": "b"}).raise_for_status()


def test_a_late_release_does_not_free_the_next_hold():
    """The body's `finally` and its collection both release. The second one
    can come after a new request took the key, and must leave it held."""

    import threading

    busy, lock = {}, threading.Lock()
    first = chat.hold(busy, lock, "k", "busy")
    first()
    second = chat.hold(busy, lock, "k", "busy")
    first()
    assert "k" in busy
    second()
    assert not busy


def test_a_collected_body_releases_even_inside_the_lock():
    """The release rides on garbage collection, and collection runs wherever
    an allocation triggers it — including inside `with _lock:` on the same
    thread. With a plain lock that release waited on itself: a flaky hang in
    `configure` that only showed up under a mutation run."""

    import gc
    import threading

    def body():
        yield ""

    def collect_inside_the_lock():
        for module in (chat, work):
            release = chat.hold(module._busy, module._lock, "k", "busy")
            events = body()
            response = chat.held(events, release)
            with module._lock:
                del response, events
                gc.collect()
            assert "k" not in module._busy

    worker = threading.Thread(target=collect_inside_the_lock, daemon=True)
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), "해제가 자기 잠금을 기다린다"


def test_a_focus_whose_settings_fail_is_not_left_held():
    """`config` answers 503 when Codex cannot list its models. Raised after
    the hold, nothing was left to release it and the focus stayed busy."""

    from fastapi import HTTPException

    with patch.object(chat, "config", side_effect=HTTPException(503, "Codex 모델 목록 없음")):
        with pytest.raises(HTTPException):
            chat.say("wiki", chat.Say(text="질문"))
    assert not chat._busy


def _two_projects(tmp_path):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    return {name: _repo(tmp_path / name) for name in ("a", "b")}


def test_a_removal_holds_its_worktree_until_it_is_done(tmp_path):
    """Checked and let go, a new instruction was accepted while the worktree
    was being deleted under it."""

    import threading

    repos = _two_projects(tmp_path)
    web = client()
    gate, real = threading.Event(), work.remove

    def slow(*args):
        gate.wait(10)
        return real(*args)

    with (patch.object(chat_channels, "repo_for", side_effect=repos.get),
          patch.object(work, "ChatSession", Agent), patch.object(work, "remove", slow)):
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        path = _made()
        removing = threading.Thread(target=lambda: work.clear(work.Where(path=path)))
        removing.start()
        try:
            for _ in range(100):
                if path in work._busy:
                    break
                threading.Event().wait(0.05)
            assert web.post("/api/work/say", json={"path": path, "text": "x"}).status_code == 409
            assert web.post("/api/work/reset", json={"path": path}).status_code == 409
            assert web.post("/api/config/wiki", json={"repo": "b"}).status_code == 409
        finally:
            gate.set()
            removing.join(10)
        assert not work._busy


def test_an_instruction_keeps_the_repository_it_was_held_in(tmp_path):
    """A switch no longer waits for a turn. The repository is taken with the
    hold, so a switch landing between the hold and the path check does not
    send the check to the new project's list — that was a 404 for a turn
    accepted a moment before."""

    import threading

    repos = _two_projects(tmp_path)
    web = client()
    gate, real, checked = threading.Event(), work.ours, []

    def slow(path, repo=None):
        gate.wait(10)
        checked.append(repo)
        return real(path, repo)

    with (patch.object(chat_channels, "repo_for", side_effect=repos.get),
          patch.object(work, "ChatSession", Agent), patch.object(work, "ours", slow)):
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        path = _made()
        sending = threading.Thread(target=lambda: work.say(work.Order(path=path, text="x")))
        sending.start()
        try:
            for _ in range(100):
                if path in work._busy:
                    break
                threading.Event().wait(0.05)
            web.post("/api/config/wiki", json={"repo": "b"}).raise_for_status()
        finally:
            gate.set()
            sending.join(10)
        settled(path)
        assert checked == [repos["a"]]
        assert [r["role"] for r in work.recall(Path(path))] == ["user", "assistant"]
        # A new instruction to the old project's worktree is refused now.
        assert web.post("/api/work/say", json={"path": path, "text": "y"}).status_code == 404


def test_a_failed_switch_leaves_the_project_where_it_was(tmp_path):
    """The new project's settings can fail (Codex cannot list its models).
    The selection had already moved by then, on the server and on disk."""

    from fastapi import HTTPException

    repos = _two_projects(tmp_path)
    web = client()
    real = chat.config

    def failing(cid, name=None):
        if (name or chat.project()) == "b":
            # While the new project is being tried, every reader still sees
            # the old one — a listing once got `b` here from a switch that
            # then failed.
            listed = work.listing()
            assert chat.project() == "a" and listed["repo"] == str(repos["a"]) and listed["project"] == "a"
            raise HTTPException(503, "Codex 모델 목록 없음")
        return real(cid, name)

    with (patch.object(chat_channels, "repo_for", side_effect=repos.get),
          patch.object(chat, "config", failing)):
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        assert web.post("/api/config/wiki", json={"repo": "b"}).status_code == 503
        assert chat.project() == "a"
        chat._project = None
        assert chat.project() == "a", "디스크의 선택도 그대로여야 한다"


def test_a_switch_answers_with_the_project_and_the_list_names_its_own(tmp_path):
    """The screen follows the switch from its answer, and drops a list that is
    not of the project it shows. If the next request failed, the picker stayed
    on the old project while the server answered for the new one."""

    repos = _two_projects(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=repos.get):
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        assert web.post("/api/config/wiki", json={"repo": "b"}).json()["repo"] == "b"
        assert web.get("/api/worktrees").json()["project"] == "b"


def test_a_screen_that_missed_a_switch_writes_nothing_into_the_new_project(tmp_path):
    """Another window moved the server from A to B. A screen still showing A
    sent a question and it went into B's conversation. Each request now says
    which project its screen shows, and a mismatch is refused before any
    record, session or worktree is touched."""

    from urllib.parse import unquote

    repos = _two_projects(tmp_path)
    web = client()
    showing_a = {"X-Project": "a"}
    with (patch.object(chat_channels, "repo_for", side_effect=repos.get),
          patch.object(work, "ChatSession", Agent)):
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        path = _made()
        web.post("/api/config/wiki", json={"repo": "b"}).raise_for_status()   # the other window

        asked = web.post("/api/say/wiki", json={"text": "A 에 묻는다"}, headers=showing_a)
        assert asked.status_code == 409 and unquote(asked.headers["X-Project-Moved"]) == "b"
        assert web.get("/api/log/wiki").json() == []
        assert web.get("/api/worktrees", headers=showing_a).status_code == 409
        assert web.post("/api/work/say", json={"path": path, "text": "x"}, headers=showing_a).status_code == 409
        assert not work._busy and not chat._busy
        assert web.get("/api/worktrees", headers={"X-Project": "b"}).json()["rows"] == []
        # The switch itself names its project in the body.
        assert web.post("/api/config/wiki", json={"repo": "a"}, headers={"X-Project": "b"}).status_code == 200


def test_a_write_that_names_no_project_is_refused(tmp_path):
    """Phase 6, review 11: a question sent before the first channel list came back
    carried no project, and the check that stops a stale screen let it
    through into whichever project the server was on."""

    bare = TestClient(main_app.app, base_url="http://127.0.0.1:8787")
    with patch.object(chat, "session", side_effect=AssertionError("reached a session")):
        assert bare.post("/api/say/wiki", json={"text": "묻는다"}).status_code == 400
    assert bare.post("/api/switch", json={"translate": False}).status_code == 400
    assert bare.get("/api/switch").json()["translate"] is True   # reading needs no project
    assert bare.get("/api/log/wiki").json() == []


def test_a_stale_screen_cannot_switch_or_configure(tmp_path):
    """The model change travels on the same route as the switch. Left out of
    the check, a screen still showing A changed its focus's model and the
    server went back to A without a word."""

    repos = _two_projects(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=repos.get):
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        web.post("/api/config/wiki", json={"repo": "b"}).raise_for_status()   # the other window
        stale = web.post("/api/config/wiki", json={"repo": "a", "model": "haiku", "effort": ""},
                         headers={"X-Project": "a"})
        assert stale.status_code == 409 and chat.project() == "b"
        assert web.post("/api/config/wiki", json={"repo": "a"}, headers={"X-Project": "b"}).status_code == 200
        assert chat.project() == "a"


def test_a_turn_outlives_its_response(tmp_path):
    """Dropped unread, the response takes nothing with it. The turn ends when
    the CLI does, and only then is it on record and the worktree let go."""

    import gc

    repo = _repo(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"), patch.object(work, "ChatSession", Slow):
        path = _made()
        work.say(work.Order(path=path, text="x"))
        gc.collect()
        assert path in work._busy
        Agent.made[-1].go.set()
        settled(path)
        assert not work._busy
        rows = web.get("/api/work/log", params={"path": path}).json()["rows"]
        assert [(r["role"], r["text"]) for r in rows] == [("user", "x"), ("assistant", "했다")]


def test_a_screen_reattaches_after_the_last_event_it_saw(tmp_path):
    """`after=k` gives k+1 to the end, whether the turn still runs or has
    ended. Two screens on one turn see the same events in the same order."""

    repo = _repo(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"), patch.object(work, "ChatSession", Slow):
        path = _made()
        work.say(work.Order(path=path, text="x"))
        running = web.get("/api/work/log", params={"path": path}).json()["running"]
        assert running["session_id"] == Agent.made[-1].id and running["seq"] == 1

        seen: dict[str, list] = {}

        def watch(name, after):
            params = {"path": path, "turn": running["turn"], "after": after}
            seen[name] = parse(web.get("/api/work/events", params=params).text)

        screens = [threading.Thread(target=watch, args=(name, after))
                   for name, after in (("first", -1), ("second", -1), ("late", 0))]
        for screen in screens:
            screen.start()
        Agent.made[-1].go.set()
        for screen in screens:
            screen.join(10)

        assert [e["kind"] for e in seen["first"]] == ["tool", "approval", "done"]
        assert [e["seq"] for e in seen["first"]] == [0, 1, 2]
        assert {e["turn"] for e in seen["first"]} == {running["turn"]}
        assert seen["second"] == seen["first"] and seen["late"] == seen["first"][1:]
        ended = web.get("/api/work/events", params={"path": path, "turn": running["turn"], "after": 1})
        assert parse(ended.text) == seen["first"][2:]
        assert web.get("/api/work/log", params={"path": path}).json()["running"] is None
        other = {"path": path, "turn": "another", "after": -1}
        assert web.get("/api/work/events", params=other).status_code == 410


def test_a_stopped_turn_ends_and_the_next_goes_on_in_the_same_session(tmp_path):
    repo = _repo(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"), patch.object(work, "ChatSession", Slow):
        path = _made()
        work.say(work.Order(path=path, text="x"))
        agent = Agent.made[-1]
        turn = web.get("/api/work/log", params={"path": path}).json()["running"]["turn"]
        assert web.post("/api/work/stop", json={"path": path, "turn": "another"}).status_code == 409
        web.post("/api/work/stop", json={"path": path, "turn": turn}).raise_for_status()
        run = settled(path)
        assert (run.events[-1]["kind"], run.events[-1]["text"]) == ("error", "사람이 멈춤")
        assert not work._busy and not agent.alive
        assert web.get("/api/work/log", params={"path": path}).json()["rows"][-1]["error"] == "사람이 멈춤"

        work.say(work.Order(path=path, text="이어서"))
        assert Agent.made[-1] is agent and work._runs[path].session_id == agent.id
        agent.go.set()
        settled(path)


def test_a_turn_is_reachable_after_the_project_moves(tmp_path):
    """Its session was made for a path `ours` took. A path with no session is 404."""

    repos = _two_projects(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=repos.get), \
         patch.object(work, "ChatSession", Agent):
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        path = _made()
        said = parse(web.post("/api/work/say", json={"path": path, "text": "x"}).text)
        web.post("/api/config/wiki", json={"repo": "b"}).raise_for_status()
        again = web.get("/api/work/events", params={"path": path, "turn": said[0]["turn"]})
        assert again.status_code == 200 and parse(again.text) == said
        nowhere = {"path": str(tmp_path / "nowhere"), "turn": said[0]["turn"]}
        assert web.get("/api/work/events", params=nowhere).status_code == 404
        assert web.post("/api/work/stop", json=nowhere).status_code == 404


class Asker(Slow):
    """Two writes asked of a person, one outside the worktree refused unasked,
    and one still waiting when the turn is stopped."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending, self.heard = {"r1", "r2", "r4"}, threading.Event()

    def say(self, text, halt=None):
        self.alive = True
        yield Event("tool", "Read · a.txt", {}, self.id)
        for rid in ("r1", "r2"):
            yield Event("approval", f"Write · {rid}.txt", {"id": rid, "tool": "Write", "input": {"content": "x"}}, self.id)
            self.heard.wait(10)
            self.heard.clear()
        yield Event("approval", "Edit · ../elsewhere.txt",
                    {"id": "r3", "tool": "Edit", "input": {}, "answer": "deny", "by": "outside"}, self.id)
        yield Event("approval", "Bash · pytest -q", {"id": "r4", "tool": "Bash", "input": {}}, self.id)
        self.go.wait(10)
        yield Event("error", "프로세스가 닫혔다.", {}, self.id)

    def answer(self, rid, allow, scope="once"):
        ok = super().answer(rid, allow, scope)
        self.heard.set()
        return ok


def until(test) -> None:
    import time

    for _ in range(200):
        if test():
            return
        time.sleep(0.05)
    raise AssertionError("기다린 일이 오지 않았다")


def asked(path: str, rid: str) -> bool:
    return any(e["kind"] == "approval" and e["meta"]["id"] == rid for e in list(work._runs[path].events))


def test_the_record_keeps_every_approval_and_who_answered_it(tmp_path):
    repo = _repo(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"), patch.object(work, "ChatSession", Asker):
        path = _made()
        work.say(work.Order(path=path, text="x"))
        agent = Agent.made[-1]

        def answer(rid, allow):
            web.post("/api/work/answer", json={"path": path, "session_id": agent.id,
                                               "id": rid, "allow": allow}).raise_for_status()

        until(lambda: asked(path, "r1"))
        answer("r1", True)
        until(lambda: asked(path, "r2"))
        answer("r2", False)
        until(lambda: asked(path, "r4"))
        web.post("/api/work/stop", json={"path": path, "turn": work._runs[path].turn}).raise_for_status()
        run = settled(path)

        assert [e["meta"] for e in run.events if e["kind"] == "answered"] == [
            {"id": "r1", "allow": True, "by": "person"}, {"id": "r2", "allow": False, "by": "person"}]
        row = web.get("/api/work/log", params={"path": path}).json()["rows"][-1]
        assert row["steps"] == [
            {"kind": "tool", "text": "Read · a.txt"},
            {"kind": "approval", "tool": "Write", "text": "Write · r1.txt", "answer": "allow", "by": "person"},
            {"kind": "approval", "tool": "Write", "text": "Write · r2.txt", "answer": "deny", "by": "person"},
            {"kind": "approval", "tool": "Edit", "text": "Edit · ../elsewhere.txt", "answer": "deny", "by": "outside"},
            {"kind": "approval", "tool": "Bash", "text": "Bash · pytest -q", "answer": "none", "by": "person"},
        ]
        assert "tools" not in row

        # A row from before `steps` reads the same way.
        work.remember(Path(path), "assistant", "옛 답", tools=["Read · a.txt"])
        old = web.get("/api/work/log", params={"path": path}).json()["rows"][-1]
        assert old["steps"] == [{"kind": "tool", "text": "Read · a.txt"}] and "tools" not in old


def test_a_session_rule_is_shown_and_cleared_by_its_own_session(tmp_path):
    repo = _repo(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"), patch.object(work, "ChatSession", Slow):
        path = _made()
        work.say(work.Order(path=path, text="x"))
        agent = Agent.made[-1]
        until(lambda: asked(path, "r1"))
        ask = {"path": path, "session_id": agent.id, "id": "r1", "allow": True}
        assert web.post("/api/work/answer", json={**ask, "scope": "forever"}).status_code == 422
        web.post("/api/work/answer", json={**ask, "scope": "session"}).raise_for_status()
        agent.go.set()
        settled(path)

        assert web.get("/api/work/log", params={"path": path}).json()["rules"] == [{"kind": "file", "tool": "Write"}]
        clear = {"path": path, "session_id": "another"}
        assert web.post("/api/work/rules/clear", json=clear).status_code == 409
        web.post("/api/work/rules/clear", json={**clear, "session_id": agent.id}).raise_for_status()
        assert web.get("/api/work/log", params={"path": path}).json()["rules"] == []


class Starting(Agent):
    """Stopped while its process starts: the start breaks, not the turn."""

    started = threading.Event()

    def say(self, text, halt=None):
        Starting.started.set()
        halt.wait(10)
        raise RuntimeError("Codex 가 닫혔다: thread/resume")
        yield

    def stop(self, halt):
        pass   # no process yet to kill


def test_a_stop_during_start_up_is_recorded_as_a_stop(tmp_path):
    repo = _repo(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"), patch.object(work, "ChatSession", Starting):
        path = _made()
        work.say(work.Order(path=path, text="x"))
        assert Starting.started.wait(10)
        web.post("/api/work/stop", json={"path": path, "turn": work._runs[path].turn}).raise_for_status()
        run = settled(path)
        assert (run.events[-1]["kind"], run.events[-1]["text"]) == ("error", "사람이 멈춤")
        assert web.get("/api/work/log", params={"path": path}).json()["rows"][-1]["error"] == "사람이 멈춤"


# -- the map ------------------------------------------------------------------


def _documented(repo: Path, name: str) -> None:
    """A repository with two linked documents, a knowledge page, a module page
    and a committed `.wiki/graph.json` of the kind `sync` writes."""

    (repo / "docs").mkdir()
    (repo / "docs" / "a.md").write_text("# A\n\nSee [b](b.md).\n", encoding="utf-8")
    (repo / "docs" / "b.md").write_text("# B\n", encoding="utf-8")
    (repo / ".wiki" / "modules").mkdir(parents=True)
    (repo / ".wiki" / f"{name}-rule.md").write_text(
        f"---\nscope: {name}\nseverity: landmine\ntriggers: [\"x\"]\nreads: [docs/a.md]\n---\n# {name} rule\n",
        encoding="utf-8")
    (repo / ".wiki" / "modules" / "core.md").write_text("# core\n\n`docs/b.md`\n", encoding="utf-8")
    (repo / ".wiki" / "graph.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "docs"], check=True)


def test_the_map_of_a_repository_writes_nothing_into_it(tmp_path):
    """The server builds the map in memory. `repo_graph.write` would have
    written the original checkout's `.wiki/graph.json`."""

    repos = _two_projects(tmp_path)
    _documented(repos["a"], "a")
    web = client()

    def status() -> str:
        return subprocess.run(["git", "-C", str(repos["a"]), "status", "--porcelain", "--ignored"],
                              capture_output=True, text=True, check=True).stdout

    before, stamp = status(), (repos["a"] / ".wiki" / "graph.json").stat().st_mtime_ns
    with patch.object(chat_channels, "repo_for", side_effect=repos.get):
        data = web.get("/api/graph", params={"repo": "a"}).json()
        assert web.get("/api/graph", params={"repo": "nope"}).status_code == 404
    assert status() == before and (repos["a"] / ".wiki" / "graph.json").stat().st_mtime_ns == stamp

    mine = {n["id"]: n for n in data["layers"]["repo"]["nodes"]}
    assert {"docs/a.md", "docs/b.md", ".wiki/a-rule.md", ".wiki/modules/core.md"} <= set(mine)
    assert mine[".wiki/a-rule.md"]["injected"] and not mine[".wiki/modules/core.md"]["injected"]
    edges = {(e["a"], e["b"]) for e in data["layers"]["repo"]["edges"]}
    assert {("docs/a.md", "docs/b.md"), (".wiki/a-rule.md", "docs/a.md"),
            (".wiki/modules/core.md", "docs/b.md")} <= edges
    assert data["metrics"]["pages"] == len(mine) and data["metrics"]["orphans"] == 0


def test_the_hub_layer_carries_no_other_repository_s_pages(tmp_path):
    repos = _two_projects(tmp_path)
    for name in ("a", "b"):
        _documented(repos[name], name)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=repos.get):
        data = web.get("/api/graph", params={"repo": "a"}).json()
    hub = data["layers"]["hub"]["nodes"]
    assert hub and all(n["scope"] in ("operator", "craft") for n in hub)
    assert all(set(n["status"]) == {repos["a"].name} for n in hub)
    assert not any("b-rule" in n["id"] for n in data["layers"]["repo"]["nodes"])
