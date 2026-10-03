"""Cross-client invalidations contain identifiers, never record contents."""

import asyncio
import json
from unittest.mock import patch
from types import SimpleNamespace

import pytest

from main import loop, query, work


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


@pytest.mark.parametrize("after, cursor, expected", [(None, 1, []), (-1, -1, [0, 1]),
                                                     (0, 0, [1]), (999, 1, [])])
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
