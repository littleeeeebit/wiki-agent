"""Fresh Git index validation and immutable review prompt statistics."""

import json
import os
import subprocess
from unittest.mock import patch

import pytest

from main import loop, specs, verification
from test_loop import git


def test_manifest_batches_literal_paths_without_reusing_the_index(tmp_path):
    git(tmp_path, "init")
    refs = ["api contract 한글.md", "api[1].md", "./other.md"]
    for ref in refs:
        (tmp_path / ref).write_text("contract\n", encoding="utf-8")
    manifest = {"version": 1, "contracts": refs, "flows": [{"id": "health", "title": "Health",
                "kind": "command", "command": "python check.py", "paths": ["*.py"],
                "assertions": [{"id": "healthy", "expected": "healthy"}]}]}
    (tmp_path / verification.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    git(tmp_path, "add", ".")
    with patch.object(specs, "sh", wraps=specs.sh) as queries:
        parsed, digest = verification.manifest(tmp_path)
        assert parsed.contracts == refs and digest
        assert queries.call_count == 1
        assert queries.call_args.args[0] == ["git", "ls-files", "-z"]
    git(tmp_path, "rm", "--cached", "--", "other.md")
    with pytest.raises(ValueError, match="other.md"):
        verification.manifest(tmp_path)
    manifest["contracts"] = ["../outside.md"]
    (tmp_path / verification.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="저장소 밖"):
        verification.manifest(tmp_path)
    git(tmp_path, "add", "--", verification.MANIFEST)
    git(tmp_path, "rm", "--cached", "--", verification.MANIFEST)
    with pytest.raises(ValueError, match="verification.json"):
        verification.manifest(tmp_path)


def test_review_git_reads_batch_checkout_proof_and_pin_prompt_statistics(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "first.txt").write_text("first\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "first")
    base = git(tmp_path, "rev-parse", "HEAD")
    with patch.object(specs, "sh", wraps=specs.sh) as queries:
        assert verification.checkout_proven(tmp_path, base) == ""
        assert queries.call_count == 1
    (tmp_path / "second.txt").write_text("second\n", encoding="utf-8")
    assert "소스 폴더에 변경" in verification.checkout_proven(tmp_path, base)
    git(tmp_path, "add", ".")
    assert "소스 폴더에 변경" in verification.checkout_proven(tmp_path, base)
    git(tmp_path, "commit", "-m", "second")
    head = git(tmp_path, "rev-parse", "HEAD")
    assert "커밋을 바꿨다" in verification.checkout_proven(tmp_path, base)
    git(tmp_path, "checkout", "--detach", head)
    assert verification.checkout_proven(tmp_path, head) == ""
    with patch.object(specs, "sh", wraps=specs.sh) as queries:
        assert "1 file changed" in loop.shortstat(tmp_path, "main", head, base)
        assert queries.call_count == 1
        assert queries.call_args.args[0] == ["git", "diff", "--shortstat", base, head]
    assert "could not fetch" in loop.shortstat(tmp_path, "missing", head)
    git(tmp_path, "mv", "second.txt", "# branch.oid fake 한글.txt")
    assert "소스 폴더에 변경" in verification.checkout_proven(tmp_path, head)
    for code, output in ((1, ""), (0, "# branch.head main\0")):
        with patch.object(specs, "sh", return_value=subprocess.CompletedProcess([], code, output, "")):
            assert verification.checkout_proven(tmp_path, head)


@pytest.mark.parametrize("reference", ["alias/api.md", "alias/../api.md"])
def test_manifest_rejects_untracked_directory_alias_to_a_tracked_contract(tmp_path, reference):
    git(tmp_path, "init")
    target = tmp_path / "contracts"
    target.mkdir()
    (target / "api.md").write_text("contract\n", encoding="utf-8")
    (tmp_path / "api.md").write_text("tracked root contract\n", encoding="utf-8")
    manifest = {"version": 1, "contracts": [reference], "flows": [{"id": "health", "title": "Health",
                "kind": "command", "command": "python check.py", "paths": ["*.py"],
                "assertions": [{"id": "healthy", "expected": "healthy"}]}]}
    (tmp_path / verification.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    git(tmp_path, "add", ".")
    if ".." in reference:
        target = tmp_path / "real" / "deep"
        target.mkdir(parents=True)
        (target.parent / "api.md").write_text("untracked contract\n", encoding="utf-8")
    alias = tmp_path / "alias"
    try:
        os.symlink(target, alias, target_is_directory=True)
    except OSError:
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(alias))
        except (ImportError, OSError):
            pytest.skip("Directory links unavailable")
    try:
        assert (tmp_path / reference).is_file()
        with pytest.raises(ValueError, match=reference):
            verification.manifest(tmp_path)
    finally:
        alias.unlink() if alias.is_symlink() else alias.rmdir()
