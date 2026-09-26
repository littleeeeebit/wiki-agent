"""Public copies must keep runtime data outside version control."""

from pathlib import Path
import subprocess

from test_lint import build, kinds

ROOT = Path(__file__).resolve().parents[1]


def test_private_runtime_paths_are_ignored():
    paths = ["raw/chat/progress.jsonl", "raw/census-example.jsonl", "raw/corrections.jsonl",
             ".wiki/decisions/example.md", ".wiki/adapter.toml", ".claude/settings.json",
             ".codex/auth.json", ".chat-local.json", ".env", "graph.json",
             "adapters/local-project.toml", "artifacts/result.json", "raw/eval/jev/repo-smoke-result.json"]
    result = subprocess.run(["git", "check-ignore", "--no-index", "--", *paths], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", check=True)
    assert set(result.stdout.splitlines()) == set(paths)
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    placeholders = {"raw/.gitkeep", ".wiki/.gitignore", ".wiki/decisions/.gitkeep"}
    assert not any(p.startswith(("raw/", ".wiki/", ".claude/", ".codex/", "artifacts/"))
                   for p in tracked if p not in placeholders)
    assert "adapters/example.toml" in tracked


def test_withheld_evidence_preserves_severity_without_hiding_other_errors(tmp_path):
    for marker, exempt in [("", False), ("\nsources_withheld: true", True),
                           ("\nsources_withheld: false", False), ('\nsources_withheld: "true"', False)]:
        build(tmp_path, {"craft/example": {"severity": "landmine", "sources": "[]" + marker,
                                           "links": "[missing]"}})
        found = kinds(tmp_path)
        assert ("근거 없는 landmine" not in found) == exempt
        assert "끊어진 링크" in found
