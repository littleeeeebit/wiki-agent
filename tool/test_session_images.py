"""A turn's images: sent beside the text on the Claude path, resent on a retry, refused where they cannot go.

See `docs/plans/oneshot/4-frontend.md` ("Critique"). The provider is a stand-in CLI
that records each stream-json message it receives; nothing calls a model.
"""

import base64
import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from agent import chat_busy, chat_session
from agent.chat_session import ChatSession

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
# Busy the first time, answered the second: the capacity retry sends the same request again.
FAKE = '''import json, pathlib, sys
log = pathlib.Path(sys.argv[2]).with_name("sent.jsonl")
line = sys.stdin.readline()
with log.open("a", encoding="utf-8") as f:
    f.write(line)
first = len(log.read_text(encoding="utf-8").splitlines()) == 1
print(json.dumps({"type": "result", "is_error": first, "result": "overloaded" if first else "saw it",
                  "session_id": "cli-1"}), flush=True)
sys.stdin.read()
'''


@pytest.fixture
def tree(tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    return repo


def said(session, tree, images):
    real_popen, events = subprocess.Popen, []

    def spawn(command, **kwargs):
        return real_popen([sys.executable, "-X", "utf8", "-c", FAKE, str(tree), str(tree.parent / "x.txt")], **kwargs)

    with patch.object(chat_session.subprocess, "Popen", spawn), \
         patch.object(chat_session, "cli_command", side_effect=lambda name: [name]), \
         patch.object(chat_busy, "WAITS", (0,)):
        events = list(session.say("look at these", images=images))
    session.close()
    log = tree.parent / "sent.jsonl"
    return events, [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def test_each_image_is_a_base64_block_beside_the_text_and_a_retry_resends_them(tree, tmp_path):
    shots = [tmp_path / "S1-desktop-full.png", tmp_path / "S2-desktop-full.png"]
    for shot in shots:
        shot.write_bytes(PNG)
    events, sent = said(ChatSession(tree), tree, shots)
    assert events[-1].kind == "done" and events[-1].text == "saw it"
    assert len(sent) == 2 and sent[0] == sent[1]   # the refused attempt and the retry carry the same images
    content = sent[1]["message"]["content"]
    assert [b["type"] for b in content] == ["image", "image", "text"] and content[-1]["text"] == "look at these"
    assert content[0]["source"] == {"type": "base64", "media_type": "image/png", "data": base64.b64encode(PNG).decode()}


def test_a_codex_session_refuses_a_turn_with_images_instead_of_dropping_them(tree, tmp_path):
    shot = tmp_path / "S1.png"
    shot.write_bytes(PNG)
    events, sent = said(ChatSession(tree, model="codex:m"), tree, [shot])
    assert [e.kind for e in events] == ["error"] and "그림" in events[0].text and sent == []
