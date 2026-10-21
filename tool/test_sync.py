"""Cross-client invalidations contain identifiers, never record contents."""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import pytest

from main import loop, query, work


def refused_once(refused: list):
    """`Path.replace` refusing its first call, as a Windows reader holding the target does."""

    replace = Path.replace

    def busy(self, target):
        if not refused:
            refused.append(target)
            raise PermissionError(5, "Access is denied")
        return replace(self, target)

    return patch.object(Path, "replace", busy)


def test_conversation_append_and_clear_announce_after_persistence(tmp_path):
    repo = tmp_path / "fixture"
    repo.mkdir()
    feed = work.Feed()
    with patch.object(work, "feed", feed), patch.object(query, "LOGS", tmp_path / "logs"), \
         patch.object(query, "current_repo", return_value=repo), \
         patch.object(query, "config", return_value={}), \
         patch.object(query, "_sessions", {}), patch.object(query, "_busy", {}):
        query.remember("wiki", "user", "private question")
        assert query.recall("wiki")[-1]["text"] == "private question"
        assert feed.events == [{"kind": "conversation", "cid": "wiki", "seq": 0}]
        query.reset("wiki", query.Clear(keep="delete"))
        assert query.recall("wiki") == []
        assert feed.events[-1] == {"kind": "conversation", "cid": "wiki", "seq": 1}
        assert "private question" not in json.dumps(feed.events)


def test_a_conversation_read_while_it_is_cleared_does_not_fail_the_clear(tmp_path):
    # On Windows a reader holding the log open (a `GET /api/log`) refuses the swap for a moment.
    refused = []
    repo = tmp_path / "fixture"
    repo.mkdir()
    with patch.object(work, "feed", work.Feed()), patch.object(query, "LOGS", tmp_path / "logs"), \
         patch.object(query, "current_repo", return_value=repo), \
         patch.object(query, "config", return_value={}), \
         patch.object(query, "_sessions", {}), patch.object(query, "_busy", {}):
        query.remember("wiki", "user", "private question")
        with refused_once(refused):
            query.reset("wiki", query.Clear(keep="delete"))
        assert refused and query.recall("wiki") == []


@pytest.mark.parametrize("after, cursor, expected", [(None, 1, []), (-1, -1, [0, 1]),
                                                     (0, 0, [1]), (999, -1, [0, 1])])
def test_feed_replay_and_cursor_after_server_restart(after, cursor, expected):
    feed = work.Feed()
    feed.put({"kind": "conversation", "cid": "wiki"})
    feed.put({"kind": "sync"})
    feed.done = True
    with patch.object(work, "feed", feed):
        response = loop.events(after)
        assert response.headers["X-Feed-Cursor"] == str(cursor)

        async def collect():
            return [json.loads(chunk.removeprefix("data: ").strip())["seq"]
                    async for chunk in response.body_iterator]

        assert asyncio.run(collect()) == expected


@pytest.mark.parametrize("after", [0, 1, 99])
def test_notice_replay_belongs_to_its_feed_generation(after):
    old = work.Feed()
    restarted = work.Feed()
    restarted.done = True
    with patch.object(work, "feed", restarted):
        work.notice("Question pending", "fixture")
        restarted.put({"kind": "sync"})

        async def collect(response):
            return [json.loads(chunk.removeprefix("data: ").strip())
                    async for chunk in response.body_iterator]

        response = loop.events(after, old.generation)
        assert response.headers["X-Feed-Generation"] == restarted.generation
        assert response.headers["X-Feed-Cursor"] == "-1"
        assert asyncio.run(collect(response)) == restarted.events
        # Reconnecting within the same generation resumes, rather than replaying.
        assert asyncio.run(collect(loop.events(0, restarted.generation))) == restarted.events[1:]


def test_queue_cancel_and_session_permissions_invalidate_state(tmp_path):
    chat = SimpleNamespace(id="fixture", parent_id=None, answer=lambda *args: True, clear_rules=lambda: None)
    run = work.Run(chat)
    path = str(tmp_path)
    feed = work.Feed()
    with patch.object(work, "feed", feed), patch.object(work, "_queued", {}), \
         patch.object(work, "_sessions", {path: chat}), patch.object(work, "_runs", {path: run}), \
         patch.object(work, "ours", return_value=tmp_path), patch.object(work, "current_repo", return_value=tmp_path):
        work.queue(work.Queued(path=path, turn=run.turn, text="private queued instruction"))
        work.unqueue(work.Where(path=path))
        work.answer(work.Answer(path=path, session_id=chat.id, id="permission", allow=True, scope="session"))
        work.forget_rules(work.Rules(path=path, session_id=chat.id))
    assert feed.events == [{"kind": "work-state", "path": path, "seq": i} for i in range(4)]
    assert run.events[-1]["kind"] == "answered"
