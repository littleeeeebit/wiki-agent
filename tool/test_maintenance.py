"""Merge preparation in the hub: its wiki indexes stay out of version control."""

import subprocess

from main import maintenance, query


def test_the_hub_merge_preparation_publishes_no_wiki_index(tmp_path):
    worktree = tmp_path / "pr"
    (worktree / "docs").mkdir(parents=True)
    (worktree / "docs/feature.md").write_text("# Feature\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(worktree)], check=True)
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(worktree), "-c", "user.email=e@e", "-c", "user.name=e", "commit", "-qm", "c"],
                   check=True)
    assert maintenance.indexes(worktree, query.ROOT) == []
    assert not (worktree / ".wiki").exists(), "nothing written for `add -f` to pick up"
    assert maintenance.indexes(worktree) == [".wiki/corpus.json", ".wiki/graph.json"], "a target still publishes"
