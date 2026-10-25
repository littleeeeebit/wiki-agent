"""OMM reads are passive; generation is explicit or prepared on a PR branch."""

import subprocess
import sys
import threading

import pytest
import yaml
from fastapi.testclient import TestClient

from main import app, architecture, channels, query
from common import errorlog


NODES = [{"path": "overall-architecture", "description": "HTTP requests enter server/api.py.",
          "diagram": 'graph LR\n    server["Request handler\\nserver/api.py"]\n',
          "context": "Generated context."},
         {"path": "overall-architecture/server", "description": "Handles HTTP and persists replies."}]


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Stand-in OMM CLI; the live scan separately verifies the installed CLI."""
    script = tmp_path / "omm_cli.py"
    script.write_text('''import json, os, shutil, sys, yaml
from pathlib import Path
if sys.argv[1] == "validate":
    print(os.environ.get("FAKE_OMM_VALIDATE", ""))
    sys.exit(1 if os.environ.get("FAKE_OMM_VALIDATE") else 0)
if sys.argv[1] == "delete":
    shutil.rmtree(Path(".omm") / sys.argv[2])
    sys.exit(0)
assert sys.argv[1] == "write" and sys.argv[4] == "-"
field = sys.argv[3]
target = Path(".omm") / sys.argv[2] / ("diagram.mmd" if field == "diagram" else field + ".md")
target.parent.mkdir(parents=True, exist_ok=True)
content = sys.stdin.read()
meta_path = target.parent / "meta.yaml"
meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"update_count": 0, "children": []}
if field == "diagram" and target.exists():
    meta["prev_diagram"] = target.read_text(encoding="utf-8")
meta.update(update_count=meta["update_count"] + 1, last_field=field)
target.write_text(content, encoding="utf-8")
meta_path.write_text(json.dumps(meta), encoding="utf-8")
if "/" in sys.argv[2]:
    parent_path = target.parent.parent / "meta.yaml"
    parent = yaml.safe_load(parent_path.read_text(encoding="utf-8"))
    parent["children"] = sorted(set(parent["children"]) | {target.parent.name})
    parent_path.write_text(json.dumps(parent), encoding="utf-8")
''', encoding="utf-8")
    monkeypatch.setattr(architecture, "cli_command", lambda name: [sys.executable, "-X", "utf8", str(script)])
    calls = []

    def analyze(root, model, halt, rejected=""):
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


def test_native_field_metadata_history_and_new_children_are_published(tmp_path, cli):
    architecture.write_nodes(tmp_path, NODES, threading.Event())
    metadata = tmp_path / ".omm/overall-architecture/meta.yaml"
    before = yaml.safe_load(metadata.read_text(encoding="utf-8"))
    replacement = [{"path": "overall-architecture", "description": NODES[0]["description"],
                    "diagram": 'graph TD\n api["New API"]\n'},
                   {"path": "overall-architecture/api", "description": "New endpoint"}]
    architecture.write_nodes(tmp_path, replacement, threading.Event())
    after = yaml.safe_load(metadata.read_text(encoding="utf-8"))
    assert after["update_count"] == before["update_count"] + 1
    assert after["last_field"] == "diagram" and after["prev_diagram"] == NODES[0]["diagram"]
    assert "api" in after["children"]
    assert "server" not in after["children"]
    assert not (metadata.parent / "server").exists()
    assert (metadata.parent / "api/meta.yaml").is_file()
    history = metadata.read_bytes()
    architecture.write_nodes(tmp_path, replacement, threading.Event())
    assert metadata.read_bytes() == history


def test_existing_non_document_assets_are_not_rewritten_or_decoded(tmp_path, cli):
    architecture.write_nodes(tmp_path, NODES, threading.Event())
    asset = tmp_path / ".omm/diagram-preview.png"
    asset.write_bytes(b"\xff\x00\xfe")
    architecture.write_nodes(tmp_path, NODES, threading.Event())
    assert asset.read_bytes() == b"\xff\x00\xfe"


@pytest.mark.parametrize("removed", [False, True])
def test_concurrent_native_notes_and_history_abort_publication_without_mutation(tmp_path, cli, monkeypatch, removed):
    architecture.write_nodes(tmp_path, NODES, threading.Event())
    note = tmp_path / ".omm/overall-architecture/server/note.md"
    metadata = note.parent / "meta.yaml"
    native_run = subprocess.run
    before = yaml.safe_load(metadata.read_text(encoding="utf-8"))
    diagram = tmp_path / ".omm/overall-architecture/diagram.mmd"
    prior_diagram = diagram.read_bytes()
    concurrent = {}

    def run(args, **kwargs):
        if args[-1] == "validate":
            note.write_text("New maintainer note", encoding="utf-8")
            content = {**before, "update_count": before["update_count"] + 1, "last_field": "note"}
            metadata.write_text(yaml.safe_dump(content), encoding="utf-8")
            concurrent["metadata"] = metadata.read_bytes()
        return native_run(args, **kwargs)

    monkeypatch.setattr(architecture.subprocess, "run", run)
    replacement = [{"path": "overall-architecture", "description": "Replacement",
                    "diagram": 'graph TD\n api["API"]\n'},
                   {"path": "overall-architecture/api", "description": "New API"}]
    with pytest.raises(ValueError, match="changed during staging"):
        architecture.write_nodes(tmp_path, replacement if removed else NODES, threading.Event())
    assert note.read_text(encoding="utf-8") == "New maintainer note"
    assert metadata.read_bytes() == concurrent["metadata"] and diagram.read_bytes() == prior_diagram
    parent = yaml.safe_load((note.parent.parent / "meta.yaml").read_text(encoding="utf-8"))
    assert parent["children"] == ["server"] and not (note.parent.parent / "api").exists()


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
    metadata = yaml.safe_load((tmp_path / ".omm/overall-architecture/meta.yaml").read_text(encoding="utf-8"))
    assert metadata["children"] == ["api", "server"]


@pytest.mark.parametrize("diagram,children,reason", [
    ('graph LR\n graph["Broken node"]\n', [{"path": "overall-architecture/graph", "description": "Bad ID"}], "reserved"),
    ('graph LR\n missing["Undescribed node"]\n', [], "described child"),
])
def test_invalid_hierarchy_is_rejected_before_any_cli_publication(tmp_path, cli, diagram, children, reason):
    with pytest.raises(ValueError, match=reason):
        architecture.write_nodes(tmp_path, [{"path": "overall-architecture", "description": "Invalid",
                                            "diagram": diagram}, *children], threading.Event())
    assert not (tmp_path / ".omm").exists()


def test_a_rejected_answer_gets_one_retry_told_why(tmp_path, cli, monkeypatch):
    undescribed = [{"path": "overall-architecture", "description": "Invalid",
                    "diagram": 'graph LR\n missing["Undescribed node"]\n'}]
    reasons = []

    def analyze(root, model, halt, rejected=""):
        reasons.append(rejected)
        return NODES if rejected else undescribed

    monkeypatch.setattr(architecture, "analyze", analyze)
    assert architecture.scan(tmp_path)["installed"]
    assert reasons[0] == "" and "missing" in reasons[1]
    monkeypatch.setattr(architecture, "analyze", lambda *_: undescribed)
    with pytest.raises(architecture.Invalid, match="described child"):
        architecture.scan(tmp_path)


@pytest.mark.parametrize("said,model", [
    ("x:\n  ✗ invalid (1 error)\n  error [balanced-brackets] line 8: Unbalanced brackets", True),
    ("error: EISDIR: illegal operation on a directory, read", False),
])
def test_only_rule_coded_validator_errors_blame_the_model(tmp_path, cli, monkeypatch, said, model):
    monkeypatch.setenv("FAKE_OMM_VALIDATE", said)
    with pytest.raises(ValueError, match="Invalid architecture diagram") as raised:
        architecture.scan(tmp_path)
    assert isinstance(raised.value, architecture.Invalid) is model
    assert len(cli) == (2 if model else 1) and not (tmp_path / ".omm").exists()


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


def test_a_merge_refresh_reads_only_what_the_pull_request_changed(tmp_path, cli, monkeypatch):
    # User's choice: a whole-repository analysis on every merge cost too much.
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.test")
    git("config", "user.name", "t")
    (root / "server").mkdir()
    (root / "server/api.py").write_text("def handle():\n    return 1\n", encoding="utf-8")
    architecture.scan(root)
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    asked, answers = [], [{"nodes": [], "removed": []}]

    def answered(where, model, effort, halt, ask, system="architecture-scan"):
        asked.append((model, effort, ask, system))
        return answers.pop(0)
    monkeypatch.setattr(architecture, "answered", answered)
    assert not architecture.changed(root, base, model="m") and not asked, "nothing changed: no turn"
    (root / ".env").write_text("API_KEY=PRIVATE_SENTINEL\n", encoding="utf-8")
    (root / "server/task handler.py").write_text("def run():\n    pass\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "a secret and a spaced name")
    base = git("rev-parse", "HEAD")
    (root / ".env").unlink()
    (root / "server/task handler.py").write_text("def run():\n    return 3\n", encoding="utf-8")
    (root / "server/api.py").write_text("def handle():\n    return 2\n", encoding="utf-8")
    git("commit", "-qam", "change")
    assert not architecture.changed(root, base, model="m", effort="low"), "an empty answer writes nothing"
    model, effort, ask, system = asked[0]
    assert (model, effort, system) == ("m", "low", "architecture-update") and "return 2" in ask
    assert "\nserver/task handler.py\n" in ask, "one changed file, one line"
    # Review of #237: a deleted `.env` reached the model as removed lines.
    assert "PRIVATE_SENTINEL" not in ask and ".env" not in ask, "a file the source tools refuse stays out"
    # The overall element names server/api.py; its `server` component is the one its diagram labels with it.
    assert NODES[0]["description"] in ask and "Handles HTTP and persists replies." in ask
    server = root / ".omm/overall-architecture/server/description.md"
    for wrong in ({"nodes": []}, {"nodes": [{"path": "overall-architecture/server", "description": "A."},
                                            {"path": "overall-architecture/server", "description": "B."}], "removed": []}):
        answers[:] = [wrong, wrong]
        with pytest.raises(architecture.Invalid):
            architecture.changed(root, base, model="m")
        assert "rejected" in asked[-1][2], "a missing key or a repeated element is asked again, told why"
    answers[:] = [{"nodes": [{"path": "overall-architecture/server"}], "removed": "server"},
                  {"nodes": [{"path": "overall-architecture/server", "description": "Handles HTTP; returns 2."}],
                   "removed": []}]
    assert architecture.changed(root, base, model="m")
    assert server.read_text(encoding="utf-8") == "Handles HTTP; returns 2." and "rejected" in asked[-1][2], "told why"
    overall = (root / ".omm/overall-architecture/description.md").read_text(encoding="utf-8")
    assert overall == NODES[0]["description"], "what the answer left out stays"


def test_a_merge_refresh_shows_in_full_only_the_elements_naming_a_changed_file():
    nodes = [{"path": "overall-architecture", "description": "Requests enter server/api.py.",
              "diagram": 'graph TD\n    server["Server\\nserver/api.py"]\n    billing["Billing\\nbilling/charge.py"]\n'},
             {"path": "overall-architecture/server", "description": "Handles HTTP."},
             {"path": "overall-architecture/billing", "description": "Charges cards."},
             {"path": "overall-architecture/tools", "description": "Helpers under tools/."}]
    assert architecture.touched(nodes, ["server/api.py"]) == {"overall-architecture", "overall-architecture/server"}
    assert architecture.touched(nodes, ["tools/new.py"]) == {"overall-architecture/tools"}, "its folder named"
    # Review of #237: a folder prefix picked every element naming a sibling file (51 of 86 on this repo).
    siblings = [{"path": "a", "description": "Uses tool/main/architecture.py."},
                {"path": "b", "description": "Uses tool/main/loop.py and mytool/main/architecture.py."}]
    assert architecture.touched(siblings, ["tool/main/architecture.py"]) == {"a"}


def test_the_refresh_model_is_chosen_in_the_settings(tmp_path, monkeypatch):
    from main import loop
    from test_main import client
    monkeypatch.setattr(query, "LOGS", tmp_path)
    web = client()
    assert web.get("/api/architecture/settings").json() == {"omm_model": "", "omm_effort": ""}, "none: skipped"
    assert web.post("/api/architecture/settings", json={"omm_model": "bad name!"}).status_code == 400
    saved = web.post("/api/architecture/settings", json={"omm_model": " haiku ", "omm_effort": "low"}).json()
    assert saved == {"omm_model": "haiku", "omm_effort": "low"} == architecture.settings()
    assert loop.settings({"omm_model": ""})["omm_model"] == "haiku"
