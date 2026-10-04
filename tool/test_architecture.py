"""OMM reads are passive; CLI generation is explicit or follows one merged PR."""

import subprocess
import sys
import threading

import pytest
from fastapi.testclient import TestClient

from main import app, architecture, channels, query, specs, work
from common import errorlog


NODES = [{"path": "overall-architecture", "description": "HTTP requests enter server/api.py.",
          "diagram": 'graph LR\n    server["Request handler\\nserver/api.py"]\n',
          "context": "Generated context."},
         {"path": "overall-architecture/server", "description": "Handles HTTP and persists replies."}]


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Stand-in OMM CLI; the live scan separately verifies the installed CLI."""
    script = tmp_path / "omm_cli.py"
    script.write_text('''import sys
from pathlib import Path
if sys.argv[1] == "validate":
    sys.exit(0)
assert sys.argv[1] == "write" and sys.argv[4] == "-"
field = sys.argv[3]
target = Path(".omm") / sys.argv[2] / ("diagram.mmd" if field == "diagram" else field + ".md")
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(sys.stdin.read(), encoding="utf-8")
''', encoding="utf-8")
    monkeypatch.setattr(architecture, "cli_command", lambda name: [sys.executable, "-X", "utf8", str(script)])
    calls = []

    def analyze(root, model, halt):
        calls.append(root)
        return NODES

    monkeypatch.setattr(architecture, "analyze", analyze)
    return calls


def test_scan_preserves_context_and_reads_do_not_regenerate(tmp_path, cli):
    root = tmp_path / "repo"
    root.mkdir()
    result = architecture.scan(root)
    context = root / ".omm/overall-architecture/context.md"
    context.write_text("Maintainer context.\n", encoding="utf-8")
    assert result["installed"] and len(result["nodes"]) == 2
    before = (root / ".omm/overall-architecture/diagram.mmd").stat().st_mtime_ns
    architecture.scan(root)
    assert context.read_text(encoding="utf-8") == "Maintainer context.\n"
    assert (root / ".omm/overall-architecture/diagram.mmd").stat().st_mtime_ns == before
    assert not context.read_bytes().startswith(b"\xef\xbb\xbf")
    for _ in range(3):
        assert architecture.refresh(root)["installed"]
    assert cli == [root, root]


@pytest.mark.parametrize("path", ["../escape", "/absolute", "overall-architecture/../../escape"])
def test_model_output_cannot_escape_omm(tmp_path, cli, monkeypatch, path):
    monkeypatch.setattr(architecture, "analyze", lambda *_: [{"path": path, "description": "Bad path"}])
    with pytest.raises(ValueError, match="Invalid"):
        architecture.scan(tmp_path)
    assert not (tmp_path / ".omm").exists()


def test_omm_output_cannot_follow_a_directory_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / ".omm").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("The host does not permit symbolic links")
    with pytest.raises(ValueError, match="local directory"):
        architecture.write_nodes(tmp_path, NODES, threading.Event())
    assert not list(outside.iterdir())


def test_removed_generated_elements_keep_notes(tmp_path, cli, monkeypatch):
    architecture.scan(tmp_path)
    note = tmp_path / ".omm/overall-architecture/server/note.md"
    note.write_text("Retain operational evidence", encoding="utf-8")
    new = [{"path": "overall-architecture", "description": "New entry point",
            "diagram": 'graph LR\n api["API\\napi.py"]\n'},
           {"path": "overall-architecture/api", "description": "The replacement entry"}]
    monkeypatch.setattr(architecture, "analyze", lambda *_: new)
    architecture.scan(tmp_path)
    assert note.read_text(encoding="utf-8") == "Retain operational evidence"
    assert not (note.parent / "description.md").exists()
    assert (tmp_path / ".omm/analysis.json").is_file()


@pytest.mark.parametrize("diagram,children,reason", [
    ('graph LR\n graph["Broken node"]\n', [{"path": "overall-architecture/graph", "description": "Bad ID"}], "reserved"),
    ('graph LR\n missing["Undescribed node"]\n', [], "described child"),
])
def test_invalid_hierarchy_is_rejected_before_any_cli_publication(tmp_path, cli, diagram, children, reason):
    with pytest.raises(ValueError, match=reason):
        architecture.write_nodes(tmp_path, [{"path": "overall-architecture", "description": "Invalid",
                                            "diagram": diagram}, *children], threading.Event())
    assert not (tmp_path / ".omm").exists()


def test_selected_repository_add_is_explicit_and_reads_are_passive(tmp_path, cli, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for root in (first, second):
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.setattr(channels, "WORKSPACE", tmp_path)
    monkeypatch.setattr(query, "_project", "first")
    monkeypatch.setattr(errorlog, "record", lambda *args, **kwargs: None)
    web = TestClient(app.app, base_url="http://127.0.0.1:8787")
    headers = {"X-Project": "first"}
    assert web.get("/api/architecture", headers=headers).json()["installed"] is False
    assert not cli and not (first / ".omm").exists()
    assert web.post("/api/architecture", headers=headers).status_code == 200
    assert cli == [first]
    snapshot = web.get("/api/architecture", headers=headers).json()
    (first / "changed.py").write_text("print('unmerged edit')\n", encoding="utf-8")
    assert web.get("/api/architecture", headers=headers).json() == snapshot
    assert web.post("/api/architecture", headers=headers).json() == snapshot
    assert cli == [first] and not (second / ".omm").exists()
    assert web.post("/api/architecture").status_code == 400
    monkeypatch.setattr(query, "_project", "second")
    assert web.post("/api/architecture", headers=headers).status_code == 409
    assert web.get("/api/architecture", headers=headers).status_code == 409


@pytest.mark.parametrize("blocked", ["none", "busy", "dirty", "task-branch", "unmerged"])
def test_merge_refresh_runs_once_and_defers_unsafe_checkouts(tmp_path, monkeypatch, blocked):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit",
                    "-q", "--allow-empty", "-m", "base"], check=True)
    (root / ".omm").mkdir()
    records = tmp_path / "records/repo"
    records.mkdir(parents=True)
    spec = {"repo": "repo", "id": "task", "state": "머지됨", "cleanup_complete": True,
            "merge": {"base": "main"}, "cell": {"model": "codex:fixture"}}
    monkeypatch.setattr(specs, "SPECS", records.parent)
    monkeypatch.setattr(channels, "repo_for", lambda _: root)
    monkeypatch.setattr(specs, "listing", lambda _: [spec])
    monkeypatch.setattr(specs, "update", lambda repo, sid, **fields: spec.update(fields))
    architecture.after_merge(root, spec)
    architecture.after_merge(root, spec)
    assert spec["architecture_refresh"] == {"state": "pending"}
    if blocked == "unmerged":
        spec["state"] = "PR #7"
    if blocked == "task-branch":
        subprocess.run(["git", "-C", str(root), "switch", "-q", "-c", "another-task"], check=True)
    if blocked == "dirty":
        (root / "unpublished.txt").write_text("Preserve me", encoding="utf-8")
    if blocked == "busy":
        monkeypatch.setitem(work._busy, str(root), {"kind": "turn"})
    stop = threading.Event()
    native_wait = stop.wait
    monkeypatch.setattr(stop, "wait", lambda timeout: native_wait(0.01))
    calls = []

    def scan(path, **kwargs):
        calls.append((path, kwargs["model"]))

    monkeypatch.setattr(architecture, "scan", scan)
    thread = threading.Thread(target=architecture.watch, args=(stop,))
    thread.start()
    native_wait(0.1)
    stop.set()
    thread.join(5)
    assert not thread.is_alive()
    if blocked == "none":
        assert calls == [(root, "codex:fixture")]
        assert spec["architecture_refresh"] == {"state": "complete"}
    else:
        assert not calls and spec["architecture_refresh"]["state"] == "pending"
