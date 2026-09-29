"""What the knowledge graph counts as an orphan."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import corpus  # noqa: E402
import repo_graph  # noqa: E402

PAGE = """---
scope: project
severity: preference
reads: {reads}
---

# 페이지

{body}
"""


def repo(docs: dict[str, str], reads: str = "[]", body: str = "") -> Path:
    """A throwaway repository. `docs` maps a repo-relative path to its body."""

    root = Path(tempfile.mkdtemp())
    (root / ".wiki").mkdir()
    for name, text in docs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.stem}\n\n{text}\n", encoding="utf-8")
    (root / ".wiki" / "page.md").write_text(
        PAGE.format(reads=reads, body=body), encoding="utf-8"
    )
    (root / ".wiki" / "corpus.json").write_text(
        json.dumps({"docs": corpus.collect(root, corpus.DEFAULT_ROOTS)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


def test_pointed_at_by_nobody_is_an_orphan():
    data = repo_graph.build(repo({"docs/a.md": "", "docs/b.md": ""}))
    assert data["counts"]["orphans"] == 2
    assert data["orphans"] == ["docs/a.md", "docs/b.md"]


def test_a_markdown_link_counts():
    data = repo_graph.build(repo({"docs/a.md": "[비](b.md) 를 보라", "docs/b.md": ""}))
    assert data["orphans"] == ["docs/a.md"]


def test_a_backticked_path_counts_too():
    # The shape these repositories actually use more. Not counting it inflates
    # the orphan count.
    data = repo_graph.build(repo({"docs/a.md": "`docs/b.md` 를 읽어라", "docs/b.md": ""}))
    assert data["orphans"] == ["docs/a.md"]


def test_a_relative_path_resolves_from_where_it_was_written():
    data = repo_graph.build(repo({
        "docs/sub/a.md": "[위](../b.md)",
        "docs/b.md": "",
    }))
    assert data["orphans"] == ["docs/sub/a.md"]


def test_pointing_at_a_missing_document_is_not_an_edge():
    data = repo_graph.build(repo({"docs/a.md": "[없다](docs/없다.md) `또한.md`"}))
    assert data["edges"] == []
    assert data["counts"]["orphans"] == 1


def test_an_external_link_does_not_count():
    body = "[깃헙](https://github.com/o/r/blob/main/docs/b.md)"
    data = repo_graph.build(repo({"docs/a.md": body, "docs/b.md": ""}))
    assert data["orphans"] == ["docs/a.md", "docs/b.md"]


def test_a_page_pointing_at_it_makes_it_a_cross_axis_edge():
    data = repo_graph.build(repo({"docs/a.md": ""}, reads="[docs/a.md]"))
    assert data["orphans"] == []
    crossing = [e for e in data["edges"] if e["kind"] == "reads"]
    assert crossing and all(e["cross"] for e in crossing)
    assert data["counts"]["read"] == 1


def test_pointing_at_itself_is_still_an_orphan():
    data = repo_graph.build(repo({"docs/a.md": "`docs/a.md` 자기 자신"}))
    assert data["orphans"] == ["docs/a.md"]


def test_a_different_hash_seed_produces_the_same_file():
    """This output is committed. Identical content in a shifting order makes
    the diff unreadable.

    Iterating a set directly really did change the edge order from run to
    run. Calling it twice inside one process cannot catch that: the string
    hash is fixed per process, so the same process always produces the same
    order. Hence two separate runs with different seeds.
    """

    root = repo({f"docs/{n}.md": "`docs/b.md` `docs/c.md` `docs/a.md`" for n in "abc"})
    runs = [
        subprocess.run(
            [sys.executable, str(HERE / "repo_graph.py"), "--repo", str(root)],
            capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"},
        ) and (root / ".wiki" / "graph.json").read_text(encoding="utf-8")
        for seed in ("1", "12345")
    ]
    assert runs[0] == runs[1]


def test_with_no_listing_nothing_is_built():
    root = Path(tempfile.mkdtemp())
    (root / ".wiki").mkdir()
    assert repo_graph.build(root) is None


def test_a_line_reference_counts():
    data = repo_graph.build(repo({
        "docs/a.md": "`docs/b.md:24` 와 `docs/c.md:3-9`", "docs/b.md": "", "docs/c.md": "",
    }))
    assert data["orphans"] == ["docs/a.md"]


def test_the_root_readme_is_the_way_in_not_an_orphan():
    data = repo_graph.build(repo({"README.md": "", "docs/README.md": ""}))
    assert data["orphans"] == ["docs/README.md"], "루트 README 만 입구다"
