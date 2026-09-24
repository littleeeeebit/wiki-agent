"""The two stages' data boundary, error preservation, and Codex events from a
real child process."""

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

import chat
import chat_channels
from agent import chat_local, chat_session
import mirror
import translate
from agent.chat_session import ChatSession, Event


@pytest.fixture(autouse=True)
def no_machine_settings(tmp_path):
    with patch.object(chat_channels, "LOCAL", {}), patch.object(chat, "LOGS", tmp_path), \
         patch.object(chat, "_project", None), patch.object(chat, "_config", {}), \
         patch.object(chat, "_sessions", {}), patch.object(chat, "_busy", set()):
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
        client = TestClient(chat.app)
        response = client.post("/api/say/wiki", json={"text": "검사 결과?"})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert [e["kind"] for e in events] == ["done", "simple_start", "simple_delta", "simple_done"]
        saved = client.get("/api/log/wiki").json()[-1]
        assert saved["text"] == "정확한 원문 13건" and saved["simple_text"] == "쉬운 설명 13건"
        assert saved["session_id"] == "answer-session"
        assert "wiki" not in chat._busy
        with patch.object(chat, "explain", return_value=iter([Event("error", "설명 호출 실패")])):
            response = client.post("/api/say/wiki", json={"text": "다시?"})
            assert '"kind": "simple_error"' in response.text
            saved = client.get("/api/log/wiki").json()[-1]
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
        response = TestClient(chat.app).post("/api/say/wiki", json={"text": "질문"})
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
    client = TestClient(chat.app)
    models = [{"id": "codex:test-model", "default_effort": "low",
               "efforts": [{"id": e} for e in ("", "low", "high", "max")]},
              {"id": "codex:another-model", "default_effort": "high",
               "efforts": [{"id": e} for e in ("", "low", "high")]}]
    with patch.object(chat_channels, "repo_for", return_value=tmp_path), \
         patch.object(chat_channels, "codex_models", return_value=models), \
         patch.object(chat, "_config", {}), patch.object(chat, "_sessions", {}):
        assert client.post("/api/config/wiki", json={"repo": "sample", "model": "codex"}).status_code == 400
        assert client.post("/api/config/wiki", json={"repo": "sample", "model": "codex:missing"}).status_code == 400
        assert client.post("/api/config/wiki", json={"repo": "sample", "model": "codex:another-model", "effort": "max"}).status_code == 400
        response = client.post("/api/config/wiki", json={"repo": "sample", "model": "codex:test-model", "effort": "max"})
        assert response.json()["switched"]
        response = client.post("/api/config/wiki", json={"repo": "sample", "model": "codex:test-model", "effort": "max"})
        assert response.status_code == 200 and not response.json()["kept"]
        response = client.post("/api/config/wiki", json={"repo": "sample", "model": "codex:another-model"})
        assert response.json()["kept"] and response.json()["effort"] == "high"
        # A Claude name typed by hand goes through; a shell-shaped one does not.
        assert client.post("/api/config/wiki", json={"repo": "sample", "model": "claude-opus-5-5"}).status_code == 200
        assert client.post("/api/config/wiki", json={"repo": "sample", "model": "opus; rm -rf"}).status_code == 400
        with patch.object(chat, "_busy", {"wiki"}):
            assert client.post("/api/reset/wiki").status_code == 409


def test_project_shared_sessions_and_records_isolated(tmp_path):
    repos = {name: tmp_path / name for name in ("a", "b")}
    for repo in repos.values():
        (repo / ".git").mkdir(parents=True)
    client = TestClient(chat.app)
    with patch.object(chat_channels, "repo_for", side_effect=repos.get):
        client.post("/api/config/diagnose", json={"repo": "a"}).raise_for_status()
        assert {c["repo"] for c in client.get("/api/channels").json()} == {"a"}
        a = chat.session("retro")
        a.session_id = "a-context"
        chat.remember("retro", "assistant", "a 회고", session_id="a-context", provider="claude")
        client.post("/api/config/progress", json={"repo": "b"}).raise_for_status()
        assert client.get("/api/log/retro").json() == []
        assert chat.session("retro") is not a
        chat.remember("retro", "assistant", "b 회고")
        client.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        assert {c["repo"] for c in client.get("/api/channels").json()} == {"a"}
        assert chat.session("retro") is a and a.session_id == "a-context"
        assert [r["text"] for r in client.get("/api/log/retro").json()] == ["a 회고"]
        with patch.object(chat, "_busy", {"diagnose"}):
            assert client.post("/api/config/wiki", json={"repo": "b"}).status_code == 409
        assert chat.project() == "a"
        chat._project = None
        assert chat.project() == "a", "서버 재시작 때 프로젝트 선택을 복원해야 한다"
        chat._sessions.clear()
        assert chat.session("retro").session_id == "a-context"
        client.post("/api/reset/retro").raise_for_status()
        assert chat.session("retro") is not a
        assert chat.session("retro").session_id is None
        assert client.get("/api/log/retro").json()[0]["text"] == "a 회고"


def test_legacy_records_remain_visible_but_not_in_handoff(tmp_path):
    (tmp_path / "retro.jsonl").write_text(
        json.dumps({"role": "assistant", "text": "소속 모름"}) + "\n", encoding="utf-8")
    client = TestClient(chat.app)
    assert client.get("/api/log/retro").json() == []
    assert client.get("/api/log/retro?legacy=true").json()[0]["text"] == "소속 모름"
    assert "소속 모름" not in client.post("/api/handoff/retro").json()["text"]
    import chat_post
    (tmp_path / ".git").mkdir()
    with patch.object(chat_post, "LOGS", tmp_path), patch.object(chat_channels, "repo_for", return_value=tmp_path):
        chat_post.post("retro", "프로젝트 회고", project=tmp_path)
        assert client.get("/api/log/retro").json()[0]["text"] == "프로젝트 회고"


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
        response = TestClient(chat.app).get("/api/options").json()
        assert response["models"] == chat_channels.MODELS and "offline" in response["codex_error"]


# -- translation -------------------------------------------------------------


def test_translate_api_guards_its_own_budget():
    """One cache and one bill, shared with the hooks.

    A screen that posts a whole document burns what the hooks were going to
    spend, and nothing downstream of here would notice.
    """

    client = TestClient(chat.app)
    assert client.post(
        "/api/translate", json={"texts": ["x"], "direction": "ko->ko"}
    ).status_code == 400
    assert client.post(
        "/api/translate", json={"texts": ["x"] * (chat.TRANSLATE_MAX + 1)}
    ).status_code == 413
    assert client.post(
        "/api/translate", json={"texts": ["x" * (chat.TRANSLATE_CHARS + 1)]}
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
    with patch.object(chat.translate, "translate", fake):
        answer = TestClient(chat.app).post(
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


# -- the Korean mirror --------------------------------------------------------


def test_mirror_only_points_at_a_repo_it_listed():
    """That the screen named a path is not a reason to open it.

    Running on the same machine does not make it one. Only what this server
    itself offered gets accepted.
    """

    client = TestClient(chat.app)
    listing = client.get("/api/mirror/repos").json()
    assert set(listing["hosts"]) == {"claude", "codex"}

    assert client.post(
        "/api/mirror/point", json={"host": "claude", "project": "C:\\nowhere"}
    ).status_code == 404
    assert client.post(
        "/api/mirror/point", json={"host": "없는호스트", "project": "C:\\tmp"}
    ).status_code == 404

    offered = listing["hosts"]["claude"]
    if offered:
        answer = client.post(
            "/api/mirror/point",
            json={"host": "claude", "project": offered[0]["path"]},
        )
        assert answer.status_code == 200
        assert answer.json()["project"] == offered[0]["path"]


def test_a_deleted_worktree_moves_the_mirror_instead_of_stalling_it(tmp_path):
    """The one thing the mirror does without being asked.

    Deleting a worktree leaves its log behind, so "is there a log" goes on
    saying yes forever. What the screen is pointed at has to be a directory
    that is still there, or it sits showing the last thing a checkout that no
    longer exists ever said.
    """

    gone, live = tmp_path / "gone", tmp_path / "live"
    live.mkdir()
    station = mirror.Station(host="claude", poll=mirror.POLL)
    station.point("claude", gone)          # never created
    stalled = station.now()[0]

    with patch.object(chat, "_station", station), \
         patch.object(mirror, "checkouts", lambda host: [{"path": str(live)}]):
        _, _, _, project = chat._pointed()

    assert project == str(live)
    assert station.now()[0] != stalled     # a new feed, so the screen clears


def test_the_mirror_never_tails_a_checkout_that_is_gone(tmp_path):
    """`session_of` is the guard, so the terminal tail gets it too."""

    gone, live = tmp_path / "gone", tmp_path / "live"
    live.mkdir()
    with patch.object(mirror.sessions, "FINDERS",
                      {"claude": lambda project: project / "log.jsonl"}):
        pick = mirror.session_of("claude")
        assert pick(live) == live / "log.jsonl"
        assert pick(gone) is None
