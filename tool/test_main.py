"""The program's main: the wiki query's two stages, the work pane's worktrees
and approvals, the translation switch, and the door only its own screen opens."""

import json
from pathlib import Path
import subprocess
import sys
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from agent import chat_local, chat_session
from main import app as main_app
from main import channels as chat_channels
from main import query as chat
from main import work
import translate
from agent.chat_session import ChatSession, Event


def client() -> TestClient:
    """The screen as the server sees it: same host, same origin."""
    return TestClient(main_app.app, base_url="http://127.0.0.1:8787")


@pytest.fixture(autouse=True)
def no_machine_settings(tmp_path):
    with patch.object(chat_channels, "LOCAL", {}), patch.object(chat, "LOGS", tmp_path), \
         patch.object(chat, "_project", None), patch.object(chat, "_config", {}), \
         patch.object(chat, "_sessions", {}), patch.object(chat, "_busy", {}), \
         patch.object(main_app, "SWITCH", tmp_path / "main.json"), \
         patch.object(work, "LOGS", tmp_path / "work"), \
         patch.object(work, "_sessions", {}), patch.object(work, "_busy", {}):
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


def test_codex_process_resume_and_isolated_command(tmp_path):
    commands = []
    real_popen = subprocess.Popen
    fixture = '''import json,sys
text = sys.stdin.read()
for event in [
    {"type":"thread.started","thread_id":"owned-session"},
    {"type":"item.completed","item":{"type":"agent_message","text":"중간 설명"}},
    {"type":"item.completed","item":{"type":"agent_message","text":"최종 답변"}},
    {"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":7}}
]: print(json.dumps(event), flush=True)
'''

    def spawn(command, **kwargs):
        commands.append(command)
        return real_popen([sys.executable, "-X", "utf8", "-c", fixture], **kwargs)

    with patch.object(chat_session.subprocess, "Popen", spawn), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]):
        session = ChatSession(tmp_path, model="codex:test-model", system="Find evidence.", effort="low")
        for _ in range(2):
            events = list(session.say("질문"))
            assert events[-1].text == "최종 답변" and events[-1].meta["tokens"]["in"] == 12
            assert not session.alive
        assert "resume" not in commands[0]
        assert commands[1][-3:] == ["resume", "owned-session", "-"]
        assert commands[0][commands[0].index("--sandbox") + 1] == "read-only"
        list(chat_session.explain("설치 성공, 자동 실행 미확인.", "codex:test-model", "low"))
        isolated = commands[-1]
        assert "--ignore-user-config" in isolated and "--ephemeral" in isolated
        assert "resume" not in isolated and "shell_tool" in isolated
        assert "Task: answer from verifiable" not in " ".join(isolated)
        assert all(c[c.index("--model") + 1] == "test-model" for c in commands)


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
        web.post("/api/config/diagnose", json={"repo": "a"}).raise_for_status()
        assert {c["repo"] for c in web.get("/api/channels").json()} == {"a"}
        a = chat.session("retro")
        a.session_id = "a-context"
        chat.remember("retro", "assistant", "a 회고", session_id="a-context", provider="claude")
        web.post("/api/config/progress", json={"repo": "b"}).raise_for_status()
        assert web.get("/api/log/retro").json() == []
        assert chat.session("retro") is not a
        chat.remember("retro", "assistant", "b 회고")
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        assert {c["repo"] for c in web.get("/api/channels").json()} == {"a"}
        assert chat.session("retro") is a and a.session_id == "a-context"
        assert [r["text"] for r in web.get("/api/log/retro").json()] == ["a 회고"]
        with patch.object(chat, "_busy", {"diagnose": object()}):
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


class Agent:
    """A write session that asks once and finishes."""

    made: list = []

    def __init__(self, path, model="", effort="", write=False):
        assert write
        self.id, self.session_id, self.alive = uuid.uuid4().hex, None, True
        self.is_codex = model.startswith("codex:")
        self.pending = {"r1"}
        Agent.made.append(self)

    def say(self, text):
        yield Event("approval", "Write · b.txt", {"id": "r1", "tool": "Write", "input": {}}, self.id)
        yield Event("done", "했다", {"session_id": "cli-1", "error": False}, self.id)

    def answer(self, rid, allow):
        if rid not in self.pending:
            return False
        self.pending.discard(rid)
        return True

    def reconfigure(self, model, effort):
        pass

    def close(self):
        self.alive = False


def test_work_opens_only_its_own_worktrees(tmp_path):
    """A path from the screen is opened only when `workspace` listed it."""

    repo = _repo(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"):
        path = web.post("/api/worktrees", json={"task": "t1"}).json()["path"]
        assert [r["name"] for r in web.get("/api/worktrees").json()["rows"]] == ["t1"]
        assert web.post("/api/worktrees", json={"task": "../x"}).status_code == 400
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
        path = web.post("/api/worktrees", json={"task": "t1"}).json()["path"]
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


def test_a_draft_carries_the_grounds_and_leaves_the_task_to_a_person(tmp_path):
    web = client()
    with patch.object(chat_channels, "repo_for", return_value=tmp_path), \
         patch.object(chat, "active_page", return_value=("", [])), \
         patch.object(chat, "decisions", return_value=[("결정", "이유")]):
        text = web.post("/api/draft", json={
            "question": "왜 막히나?", "answer": "`tool/lint.py:12` 와 `docs/a.md:3–5` 를 보라. `tool/lint.py:12`",
            "hits": ["hooks-fail-open"]}).json()["text"]
        assert text.count("- `tool/lint.py:12`") == 1 and "- `docs/a.md:3–5`" in text
        assert "- hooks-fail-open" in text and "- 결정 — 이유" in text
        assert text.rstrip().endswith("(사람이 한 줄 적는다)")
        retro = web.post("/api/draft", json={"question": "교정 3회 · 규칙 · 새 후보", "target": "wiki"}).json()["text"]
        assert chat.WRITERS["wiki"] in retro and "교정 3회 · 규칙 · 새 후보" in retro
        assert web.post("/api/draft", json={"target": "anything"}).status_code == 400


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
        path = web.post("/api/worktrees", json={"task": "t1"}).json()["path"]
        work.say(work.Order(path=path, text="x"))
        chat.say("wiki", chat.Say(text="x"))
        assert not work._busy and not chat._busy
        assert web.post("/api/work/say", json={"path": path, "text": "y"}).status_code == 200


def test_a_new_task_under_an_old_name_starts_fresh(tmp_path):
    """Removing `t1` and making `t1` again gives the same path. The old
    conversation and the old CLI session must not come with it."""

    repo = _repo(tmp_path)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(chat, "_project", "proj"), patch.object(work, "ChatSession", Agent):
        path = web.post("/api/worktrees", json={"task": "t1"}).json()["path"]
        web.post("/api/work/say", json={"path": path, "text": "old"}).raise_for_status()
        web.post("/api/worktrees/remove", json={"path": path}).raise_for_status()
        again = web.post("/api/worktrees", json={"task": "t1"}).json()["path"]
        assert again == path
        assert web.get("/api/work/log", params={"path": again}).json()["rows"] == []
        web.post("/api/work/say", json={"path": again, "text": "new"}).raise_for_status()
        assert Agent.made[-1].session_id is None


def test_the_project_stays_while_an_agent_runs(tmp_path):
    """An approval waiting in a worktree belongs to the project that has it."""

    repos = {name: tmp_path / name for name in ("a", "b")}
    for repo in repos.values():
        (repo / ".git").mkdir(parents=True)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=repos.get):
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        with patch.object(work, "_busy", {str(repos["a"] / "x"): object()}):
            assert web.post("/api/config/wiki", json={"repo": "b"}).status_code == 409
            assert web.post("/api/config/wiki", json={"repo": "a", "effort": "high"}).status_code == 200
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
         patch.object(work, "ChatSession", Agent):
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        path = web.post("/api/worktrees", json={"task": "t1"}).json()["path"]
        waiting = work.say(work.Order(path=path, text="x"))
        assert web.post("/api/config/wiki", json={"repo": "b"}).status_code == 409
        assert web.post("/api/worktrees/remove", json={"path": path}).status_code == 409
        assert web.post("/api/work/reset", json={"path": path}).status_code == 409
        asking = chat.say("progress", chat.Say(text="x"))
        assert web.post("/api/config/wiki", json={"repo": "b"}).status_code == 409
        del waiting, asking
        gc.collect()
        assert not work._busy and not chat._busy
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
