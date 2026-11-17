"""An agent's picture in the chat pane: `/api/image` (`docs/development.md`)."""

from unittest.mock import patch

from main import app as main_app
from main import channels as chat_channels
from test_main import _repo, client, no_machine_settings  # noqa: F401 — the fixture is autouse


def test_an_agents_picture_is_drawn_only_from_its_checkout_or_the_temporary_folder(tmp_path):
    """The before and after of a design an agent asks to approve: an image file, nothing else, from nowhere else."""

    repo, temp, outside = _repo(tmp_path), tmp_path / "temp", tmp_path / "outside"
    for folder in (repo / "shots", temp, outside):
        folder.mkdir(parents=True, exist_ok=True)
    png = b"\x89PNG\r\n\x1a\n"
    (repo / "shots/before.png").write_bytes(png)
    (temp / "after.png").write_bytes(png)
    (outside / "x.png").write_bytes(png)
    web = client()
    with patch.object(chat_channels, "repo_for", side_effect=lambda name: repo if name == "proj" else None), \
         patch.object(main_app.tempfile, "gettempdir", lambda: str(temp)):
        def got(path):
            return web.get("/api/image", params={"repo": "proj", "path": path})

        shown = got("shots/before.png")
        assert shown.status_code == 200 and shown.content == png and shown.headers["content-type"] == "image/png"
        assert got(str(repo / "shots" / "before.png")).status_code == 200   # the absolute path an agent writes
        assert got("file:///" + str(temp / "after.png").replace("\\", "/")).status_code == 200
        assert got(str(outside / "x.png")).status_code == 404
        assert got("../outside/x.png").status_code == 404
        assert got("a.txt").status_code == 404   # a file, but not a picture
        assert web.get("/api/image", params={"repo": "nope", "path": "shots/before.png"}).status_code == 400
