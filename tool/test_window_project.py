"""Each window works in its own repository (docs/plans/window-project): the server answers every
request for the `X-Project` it names, and nothing outside a request reads a window's pick."""

import ast
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch

from agent.chat_session import Event
from main import channels as chat_channels
from main import query as chat
from main import work
from test_main import Agent, Answering, _made, _two_projects, client, until, no_machine_settings  # noqa: F401 — the fixture is autouse


class Live:
    """A focus's live conversation that records being closed."""

    def __init__(self):
        self.alive, self.closed = True, False

    def close(self):
        self.closed = True

    def reconfigure(self, model, effort):
        pass


def test_two_windows_on_two_projects_work_side_by_side(tmp_path):
    """One selection for the server let another window move it from A to B: a screen still showing
    A was refused with 409, and before that its question went into B's conversation. Each request
    now answers for the `X-Project` it names. The wiki focus answering in A leaves it free in B,
    and a switch in one window closes nothing the other uses."""

    repos = _two_projects(tmp_path)
    web = client()
    go, asked = threading.Event(), []
    answers = {str(repos["a"]): Answering(go), str(repos["b"]): Answering()}

    def ask(name):
        return web.post("/api/say/wiki", json={"text": f"{name} 에 묻는다"}, headers={"X-Project": name})

    with (patch.object(chat_channels, "repo_for", side_effect=repos.get),
          patch.object(work, "ChatSession", Agent),
          patch.object(chat, "session", side_effect=lambda cid: answers[str(chat.current_repo())]),
          patch.object(chat, "hits_for", return_value=[]),
          patch.object(chat, "explain", side_effect=lambda *a, **k: iter([Event("done", "쉬운")]))):
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        path_a = _made()
        web.post("/api/config/wiki", json={"repo": "b"}, headers={"X-Project": "a"}).raise_for_status()
        path_b = _made()   # the default is the last window's pick
        first = threading.Thread(target=lambda: asked.append(ask("a")))
        first.start()
        until(lambda: (str(repos["a"]), "wiki") in chat._busy)
        assert ask("b").status_code == 200, "A's answer held the same focus in B"
        for name, path in (("a", path_a), ("b", path_b)):
            started = web.post("/api/work/say", json={"path": path, "text": "x"}, headers={"X-Project": name})
            assert started.status_code == 200
        assert web.post("/api/work/say", json={"path": path_b, "text": "x"},
                        headers={"X-Project": "a"}).status_code == 404, "a path of B is not one of A's"
        go.set()
        first.join(10)
        assert asked[0].status_code == 200
        for name in ("a", "b"):
            rows = web.get("/api/log/wiki", headers={"X-Project": name}).json()
            assert [r["text"] for r in rows if r["role"] == "user"] == [f"{name} 에 묻는다"]
        assert web.get("/api/log/wiki", headers={"X-Project": "gone"}).status_code == 409

        chat._sessions.update({(str(repos["a"]), "wiki"): (in_a := Live()), (str(repos["b"]), "wiki"): Live()})
        web.post("/api/config/wiki", json={"repo": "a"}, headers={"X-Project": "b"}).raise_for_status()
        web.post("/api/config/wiki", json={"repo": "b"}, headers={"X-Project": "a"}).raise_for_status()
        assert not in_a.closed and not any(s.closed for s in chat._sessions.values())


def test_an_idle_conversation_ends_but_an_answering_one_never_does(tmp_path):
    """A switch no longer ends the conversations of the project it leaves, so idle ones end by
    time instead, when someone asks. Their `session_id` stays with them, as after a switch."""

    repos = _two_projects(tmp_path)
    idle, answering, recent = Live(), Live(), Live()

    def key(cid):
        return (str(repos["a"]), cid)

    with patch.object(chat_channels, "repo_for", side_effect=repos.get), patch.object(chat, "IDLE_SECONDS", 60):
        client().post("/api/config/wiki", json={"repo": "b"}).raise_for_status()
        chat._sessions.update({key("retro"): idle, key("next"): answering, key("refactor"): recent})
        now = time.monotonic()
        chat._used.update({key("retro"): now - 120, key("next"): now - 120, key("refactor"): now - 10})
        chat._busy[key("next")] = object()
        chat.session("wiki")
        assert idle.closed and not answering.closed and not recent.closed
        assert chat._sessions[key("retro")] is idle, "closed, not dropped: the next question resumes it"


def test_the_jev_shadow_records_into_its_turns_repository_after_a_switch(tmp_path):
    """The shadow's record runs on a thread with no request context, and a switch does not wait
    for it. It names the turn's repository, so a switch while Jev decides moves nothing."""

    repos = _two_projects(tmp_path)
    web = client()
    deciding, switched = threading.Event(), threading.Event()

    def prepare(text, repo, state, cfg, audiences=None):
        deciding.set()
        switched.wait(10)
        return {"status": "supported", "evidence": [], "trace": []}

    with patch.object(chat_channels, "repo_for", side_effect=repos.get), patch.object(chat, "prepare", prepare):
        web.post("/api/config/wiki", json={"repo": "a"}).raise_for_status()
        chat.shadow("wiki", "q", repos["a"], "", chat.decision.config())
        assert deciding.wait(10)
        web.post("/api/config/wiki", json={"repo": "b"}).raise_for_status()
        switched.set()
        log = tmp_path / "wiki.jsonl"
        until(lambda: log.exists() and '"retrieval"' in log.read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        assert [r["repo"] for r in rows if r["role"] == "retrieval"] == [str(repos["a"])]
        assert not [r for r in chat.recall("wiki", include_context=True) if r["role"] == "retrieval"]


# Functions outside a route that read the selected project, each reviewed in
# docs/plans/window-project/1-audit.md: reached from routes only, or from the question's thread,
# which copies its request's context and so reads that request's `X-Project`.
SELECTION_READERS = {
    "query.current_repo", "query.session_key", "query.config", "query.session",
    "query.remember", "query.recall", "query.hits_for", "loop.mine", "planning.begun", "work.ours",
}


def test_only_routes_and_reviewed_helpers_read_the_selected_project():
    """With the project per window, a thread that read the default would act on whichever
    repository a window picked last. A nested function is its own reader, since a route may
    start it on a thread."""

    found = set()

    def visit(node, where, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                name = getattr(child, "name", "<lambda>")
                route = any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                            and d.func.attr in ("get", "post", "put", "delete", "patch", "websocket")
                            for d in getattr(child, "decorator_list", []))
                visit(child, where, None if route else f"{where}.{name}")
                continue
            if isinstance(child, ast.Call) and scope is not None:
                fn = child.func
                if (fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)) in ("project", "current_repo"):
                    found.add(scope)
            visit(child, where, scope)

    for path in sorted(Path(chat.__file__).parent.glob("*.py")):
        visit(ast.parse(path.read_text(encoding="utf-8")), path.stem, f"{path.stem}.<module>")
    assert found == SELECTION_READERS
