"""Local OMM refresh uses source changes, preserves notes and never follows links."""

import subprocess

import pytest

from main.architecture import scan


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
