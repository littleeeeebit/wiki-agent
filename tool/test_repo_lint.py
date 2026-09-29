"""Prove `repo_lint` actually goes red.

A green with zero findings is not evidence that a check ran, so every check
here is given something to find.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import repo_lint  # noqa: E402

PAGE = """---
scope: {scope}
severity: {severity}
triggers: {triggers}
reads: {reads}
---

# 프로젝트 페이지

규칙. 한 줄.
"""


def repo(docs: list[str] | None = None, listed: list[str] | None = None,
         severity: str = "preference", triggers: str = "[]",
         reads: str = "[]", scope: str = "project") -> Path:
    """One throwaway repository, with `.wiki/` and `docs/` shaped as needed."""

    root = Path(tempfile.mkdtemp())
    (root / ".wiki").mkdir()
    (root / "docs").mkdir()
    for name in docs or []:
        (root / "docs" / name).write_text("# 문서\n", encoding="utf-8")

    (root / ".wiki" / "page.md").write_text(
        PAGE.format(scope=scope, severity=severity, triggers=triggers, reads=reads),
        encoding="utf-8",
    )
    index = root / ".wiki" / "corpus.json"
    index.write_text(
        json.dumps({"docs": [{"path": f"docs/{n}"} for n in (listed or docs or [])]}),
        encoding="utf-8",
    )
    # The listing is made to look newer than the documents, so that "behind"
    # is not the default state every test starts from.
    later = time.time() + 60
    os.utime(index, (later, later))
    return root


def kinds(root: Path) -> set[str]:
    return {kind for kind, _message in repo_lint.check(root)}


def test_a_clean_repository_says_nothing():
    # The defaults are part of "clean". A `preference` page is never injected,
    # so demanding triggers from it manufactures a defect. `project` scope is
    # exactly what belongs in a repository: the gate command, the launcher and
    # the port differ per repository. And none of the hub's findings (slot
    # conflicts, orphans, broken links) may show here: that is why the two
    # checks were separated — a line this session can do nothing about,
    # printed every time.
    assert kinds(repo(docs=["a.md", "b.md"])) == set()


def test_a_listing_pointing_at_a_missing_document_is_reported_and_its_fix_runs():
    root = repo(docs=["a.md"], listed=["a.md", "사라진.md"])
    message = dict(repo_lint.check(root))["낡은 목록"]
    # The finding names the command; run it as printed. It once lacked a
    # required argument and failed for whoever followed it.
    command = re.search(r"`python (tool/corpus\.py [^`]+)`", message)[1].split()
    subprocess.run([sys.executable, *command], cwd=Path(__file__).resolve().parent.parent, check=True,
                   capture_output=True)
    assert kinds(root) == set()


def test_a_document_edited_after_the_listing_is_reported():
    root = repo(docs=[f"{n}.md" for n in range(5)])
    later = time.time() + 600
    for path in (root / "docs").glob("*.md"):
        os.utime(path, (later, later))
    assert "낡은 목록" in kinds(root)


def test_being_slightly_behind_is_not_reported():
    # Firing on every single document edit means firing every turn, and a
    # warning that fires every turn is a warning nobody reads.
    root = repo(docs=[f"{n}.md" for n in range(5)])
    later = time.time() + 600
    for path in sorted((root / "docs").glob("*.md"))[:2]:
        os.utime(path, (later, later))
    assert "낡은 목록" not in kinds(root)


def test_reads_pointing_at_a_missing_file_is_reported():
    assert "끊어진 포인터" in kinds(repo(docs=["a.md"], reads="[docs/없다.md]"))


def test_reads_pointing_at_a_real_file_is_not_reported():
    assert "끊어진 포인터" not in kinds(repo(docs=["a.md"], reads="[docs/a.md]"))


def test_an_injectable_severity_with_no_triggers_is_reported():
    assert "안 실리는 규칙" in kinds(repo(docs=["a.md"], severity="contract"))


def test_a_hub_scope_page_inside_a_repository_is_reported():
    # Both places are called "the wiki", and a session wrote a
    # repository-independent rule in here because of it.
    assert "범위가 어긋난 페이지" in kinds(repo(docs=["a.md"], scope="craft"))
    assert "범위가 어긋난 페이지" in kinds(repo(docs=["a.md"], scope="operator"))
