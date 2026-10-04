"""Local OMM refresh uses source changes, preserves notes and never follows links."""

import subprocess

import pytest

from fastapi.testclient import TestClient

from main import app, channels, query
from main.architecture import scan
from common import errorlog


def test_source_changes_refresh_omm_and_preserve_maintainer_context(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    main = tmp_path / "tool/main"
    agent = tmp_path / "tool/agent"
    main.mkdir(parents=True)
    agent.mkdir()
    (agent / "__init__.py").write_text("", encoding="utf-8")
    source = main / "app.py"
    source.write_text("import agent\n", encoding="utf-8")
    first = scan(tmp_path)
    output = tmp_path / ".omm/overall-architecture/tool/diagram.mmd"
    assert 'main -->|"imports"| agent' in output.read_text(encoding="utf-8")
    context = output.parent / "context.md"
    context.write_text("Maintain this context.\n", encoding="utf-8")
    noted = scan(tmp_path)
    assert noted["revision"] != first["revision"]
    assert next(node for node in noted["nodes"] if node["path"] == "overall-architecture/tool")["context"] == "Maintain this context.\n"
    before = output.stat().st_mtime_ns
    assert scan(tmp_path) == noted and output.stat().st_mtime_ns == before
    source.write_text("print('changed')\n", encoding="utf-8")
    second = scan(tmp_path)
    assert second["revision"] != first["revision"]
    assert '-->' not in output.read_text(encoding="utf-8")
    assert context.read_text(encoding="utf-8") == "Maintain this context.\n"
    source.unlink()
    third = scan(tmp_path)
    assert third["files"] == 1 and third["revision"] != second["revision"]
    assert not (tmp_path / ".omm/overall-architecture/tool/main/description.md").exists()
    assert not output.read_bytes().startswith(b"\xef\xbb\xbf")


def test_omm_output_cannot_escape_through_a_directory_link(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    target = tmp_path / "outside"
    target.mkdir()
    try:
        (tmp_path / ".omm").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("The host does not permit symbolic links")
    with pytest.raises(ValueError, match="local directory"):
        scan(tmp_path)
    assert not list(target.iterdir())


def test_selected_repository_add_refresh_and_existing_documents(tmp_path, monkeypatch):
    for name in ("first", "second"):
        root = tmp_path / name
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "client").mkdir()
        (root / "server").mkdir()
        (root / "client/app.py").write_text("import server.api\n", encoding="utf-8")
        (root / "server/api.py").write_text("print('server')\n", encoding="utf-8")
    monkeypatch.setattr(channels, "WORKSPACE", tmp_path)
    monkeypatch.setattr(query, "_project", "first")
    monkeypatch.setattr(errorlog, "record", lambda *args, **kwargs: None)
    web = TestClient(app.app, base_url="http://127.0.0.1:8787")
    headers = {"X-Project": "first"}
    first = tmp_path / "first"
    missing = web.get("/api/architecture", headers=headers)
    assert missing.status_code == 200 and missing.json()["installed"] is False
    assert not (first / ".omm").exists()
    added = web.post("/api/architecture", headers=headers)
    assert added.status_code == 200
    data = added.json()
    assert data["repo"] == "first" and data["installed"] and data["files"] == 2
    assert 'client -->|"imports"| server' in data["nodes"][0]["diagram"]
    assert (first / ".omm/generated.json").exists()
    assert not (tmp_path / "second/.omm").exists()
    context = first / ".omm/overall-architecture/context.md"
    context.write_text("Preserve this note.\n", encoding="utf-8")
    (first / "client/app.py").write_text("print('updated')\n", encoding="utf-8")
    refreshed = web.get("/api/architecture", headers=headers).json()
    assert refreshed["revision"] != data["revision"]
    assert '-->' not in refreshed["nodes"][0]["diagram"]
    assert refreshed["nodes"][0]["context"] == "Preserve this note.\n"
    assert web.post("/api/architecture", headers=headers).json() == refreshed
    assert web.post("/api/architecture").status_code == 400

    monkeypatch.setattr(query, "_project", "second")
    assert web.post("/api/architecture", headers=headers).status_code == 409
    assert web.get("/api/architecture", headers=headers).status_code == 409
    assert not (tmp_path / "second/.omm").exists()
    custom = tmp_path / "second/.omm/custom"
    custom.mkdir(parents=True)
    diagram = custom / "diagram.mmd"
    diagram.write_text("graph LR\n A --> B\n", encoding="utf-8")
    (custom / "note.md").write_text("Handwritten context.\n", encoding="utf-8")
    before = diagram.stat().st_mtime_ns
    second_headers = {"X-Project": "second"}
    existing = web.get("/api/architecture", headers=second_headers).json()
    assert existing["repo"] == "second" and existing["installed"] and existing["files"] is None
    assert existing["nodes"][0]["diagram"] == "graph LR\n A --> B\n"
    assert existing["nodes"][0]["note"] == "Handwritten context.\n"
    assert web.post("/api/architecture", headers=second_headers).json() == existing
    assert diagram.stat().st_mtime_ns == before
    assert not (custom.parent / "generated.json").exists()
