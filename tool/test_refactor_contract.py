"""A contract refusal goes back to the worker with what to put back, before the card stops for a person.

ai-coding-wiki's schema-seed card removed two declared duplicate tests and rewrote a third: the imports and
the constant only they used were tidied away, and the contract refused the file. Ruff's F401 flags such an
import, so the worker could not keep it either, and the card stopped on every such edit. The contract still
frees nothing beyond the declared tests (PR #305: three review rounds found that no reading of a module
proves nothing else observes a statement); the worker is told what to restore and how to keep it past F401.
"""

import subprocess
from pathlib import Path

import pytest

from main import loop, refactor_run


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def card(repo: Path, old: str, new: str, *declared: str, file: str = "tests/test_seed.py") -> dict:
    """A card whose start holds `old` and whose head holds `new`, with `declared` tests in its contract."""
    (repo / file).parent.mkdir(parents=True, exist_ok=True)
    (repo / file).write_text(old, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "start")
    start = _git(repo, "rev-parse", "HEAD")
    (repo / file).write_text(new, encoding="utf-8")
    _git(repo, "commit", "-qam", "edit")
    return {"id": "c", "repo": "r", "start_head": start, "refactor": {"detail": {"tests": {
        "remove": [{"test": f"{file}::{d}", "reason": "duplicate"} for d in declared]}}}}


# The schema-seed shape: a cycle check and its import, a branch table, and a test nobody declared.
SEED = ('"""Seed invariants."""\nfrom graphlib import TopologicalSorter\nimport sqlalchemy as sa\n\n'
        "BRANCHES = {'algorithm': ('graph',)}\n\n\n"
        "def test_acyclic():\n    TopologicalSorter({}).prepare()\n\n\n"
        "def test_branches():\n    assert BRANCHES\n\n\n"
        "def test_milestone():\n    assert sa\n")


def test_what_the_declared_tests_leave_unused_stays_with_a_comment_for_the_linter(repo):
    kept = SEED.replace("import TopologicalSorter", "import TopologicalSorter  # noqa: F401")
    kept = kept[:kept.index("def test_acyclic")] + "def test_milestone():\n    assert sa\n"
    assert refactor_run.contract(repo, card(repo, SEED, kept, "test_acyclic", "test_branches")) == ""


def test_tidying_it_away_is_refused_and_sent_back_line_by_line(repo):
    # The real card also rewrote the docstring to describe the new tests.
    tidied = '"""Seed invariants, by parent."""\nimport sqlalchemy as sa\n\n\ndef test_milestone():\n    assert sa\n'
    spec = card(repo, SEED, tidied, "test_acyclic", "test_branches")
    refusal = refactor_run.contract(repo, spec)
    assert "기존 테스트 변경" in refusal
    text = refactor_run.restoring(repo, spec, refusal)
    assert all(f"start line {n} `{s}" in text for n, s in [
        (1, '"""Seed invariants."""'), (2, "from graphlib import TopologicalSorter"), (5, "BRANCHES = {")]), text
    assert "# noqa: F401" in text


@pytest.mark.parametrize("old, new, shown", [
    # Round 4, F1: `contract` compares `# type:` comments, so the turn must see them as well.
    ("VALUE = 1  # type: int\n\n\ndef test_b():\n    pass\n", "VALUE = 1  # type: str\n",
     "start line 1 `VALUE = 1  # type: int`"),
    # Round 4, F2: a statement `ast.unparse` cannot render within the recursion limit.
    ("TOTAL = " + " + ".join(["1"] * 450) + "\n\n\ndef test_b():\n    pass\n", '"""Rest."""\n',
     "start line 1 `TOTAL = 1 + 1"),
    # A form feed or `\x1c` in a comment is a line break to `str.splitlines`, not to the parser.
    ("A = 1  # a\x0cb\x1cc\nB = 2\n\n\ndef test_b():\n    pass\n", "A = 1  # a\x0cb\x1cc\n", "start line 2 `B = 2`"),
    # Round 5, F2: a coding cookie can decode ASCII bytes to a newline the parser counts.
    ("# -*- coding: utf-7 -*-\nA = 1+AAo-B = 2\nC = 3\n\n\ndef test_b():\n    pass\n",
     "# -*- coding: utf-7 -*-\nA = 1\nC = 3\n", "start line 3 `B = 2`"),
    # Round 5, F1: a new helper too deep to dump still leaves a turn that sends the file back whole.
    ("V = 1\n\n\ndef test_b():\n    pass\n", "def helper():\n    return " + " + ".join(["1"] * 1100) + "\n",
     "put back to its start text the statements you changed"),
])
def test_every_refused_file_gets_a_turn(repo, old, new, shown):
    spec = card(repo, old, new, "test_b")
    refusal = refactor_run.contract(repo, spec)
    assert "기존 테스트 변경" in refusal and shown in refactor_run.restoring(repo, spec, refusal)


# Every shape PR #305's review rounds raised against freeing statements by reading the module stays refused.
@pytest.mark.parametrize("old, new", [
    # A fixture asked for by parameter name.
    ("import pytest\n\n\n@pytest.fixture\ndef db():\n    return 1\n\n\ndef test_a(db):\n    assert db\n\n\n"
     "def test_b():\n    assert True\n",
     "import pytest\n\n\n@pytest.fixture\ndef db():\n    return 2\n\n\ndef test_a(db):\n    assert db\n"),
    # An autouse fixture reaches every test without being named.
    ("import pytest\n\n\n@pytest.fixture(autouse=True)\ndef env():\n    pass\n\n\ndef test_a():\n    pass\n\n\n"
     "def test_b():\n    pass\n",
     "import pytest\n\n\ndef test_a():\n    pass\n"),
    # A fixture named only as text.
    ("import pytest\n\n\n@pytest.fixture\ndef db():\n    return 1\n\n\n"
     "@pytest.mark.usefixtures('db')\ndef test_a():\n    pass\n\n\ndef test_b():\n    pass\n",
     "import pytest\n\n\n@pytest.mark.usefixtures('db')\ndef test_a():\n    pass\n"),
    # `pytestmark` marks every test in the module.
    ("import pytest\n\npytestmark = pytest.mark.slow\n\n\ndef test_a():\n    pass\n\n\ndef test_b():\n    pass\n",
     "import pytest\n\n\ndef test_a():\n    pass\n"),
    # #305 round 1, F1: a helper the head still defines may be imported by another file, so changing it is held.
    ("def helper():\n    return True\n\n\ndef test_b():\n    assert helper()\n",
     "def helper():\n    return False\n"),
    # F2: a helper's `global` names a test it could replace; deleting the helper frees that test nothing.
    ("def helper():\n    global test_a\n\n\ndef test_b():\n    helper()\n\n\ndef test_a():\n    assert False\n",
     "def test_a():\n    assert True\n"),
    # F3: an import of `pytestmark`, or of a test class pytest collects, is read by pytest itself.
    ("from marks import pytestmark\n\n\ndef test_b():\n    assert pytestmark\n\n\ndef test_a():\n    assert False\n",
     "from marks import pytestmark\npytestmark = 1\n\n\ndef test_a():\n    assert False\n"),
    ("from support import TestKept\n\n\ndef test_b():\n    assert TestKept\n\n\ndef test_a():\n    pass\n",
     "def test_a():\n    pass\n"),
    # A project import runs that module; only the standard library's go.
    ("import support\n\n\ndef test_b():\n    assert support\n\n\ndef test_a():\n    pass\n", "def test_a():\n    pass\n"),
    # F4: a decorated helper may be a fixture registered under another name.
    ("import pytest\n\n\n@pytest.fixture(name='db')\ndef helper():\n    return False\n\n\n"
     "def test_b():\n    assert helper\n\n\ndef test_a(db):\n    assert db\n",
     "import pytest\n\n\ndef test_a(db):\n    assert db\n"),
    # A module docstring holding a doctest is a test `--doctest-modules` collects.
    ('""">>> 1\n1\n"""\n\n\ndef test_b():\n    pass\n', '""">>> 1\n2\n"""\n'),
    # Round 2, F2: an attribute, a name hashed into a set, or a default that is not a literal may run code.
    ("import os\n\nX = os.sep\n\n\ndef test_b():\n    assert X\n\n\ndef test_a():\n    assert os\n",
     "import os\n\n\ndef test_a():\n    assert os\n"),
    ("P = 1\nX = {P}\n\n\ndef test_b():\n    assert X\n\n\ndef test_a():\n    assert P\n",
     "P = 1\n\n\ndef test_a():\n    assert P\n"),
    ("def helper(a=print):\n    return a\n\n\ndef test_b():\n    assert helper()\n\n\ndef test_a():\n    pass\n",
     "def test_a():\n    pass\n"),
    # Round 2, F3: being in the standard library does not make an import inert; `this` prints.
    ("import this\n\n\ndef test_b():\n    assert this\n\n\ndef test_a():\n    pass\n", "def test_a():\n    pass\n"),
    # Round 2, F4: a module that reads its own `__doc__` uses the docstring as data.
    ('"""old"""\n\n\ndef test_b():\n    pass\n\n\ndef test_a():\n    assert __doc__ == "new"\n',
     '"""new"""\n\n\ndef test_a():\n    assert __doc__ == "new"\n'),
    # F5: an assignment that calls something may change what an undeclared test reads.
    ("import os\n\nREMOVED = os.environ.pop('SKIP', None)\n\n\ndef test_b():\n    assert REMOVED\n\n\n"
     "def test_a():\n    assert os.environ.get('SKIP')\n",
     "import os\n\n\ndef test_a():\n    assert os.environ.get('SKIP')\n"),
])
def test_nothing_beyond_the_declared_tests_goes(repo, old, new):
    assert "기존 테스트 변경" in refactor_run.contract(repo, card(repo, old, new, "test_b"))


def test_a_module_another_file_names_frees_nothing_beyond_its_declared_tests(repo):
    # Round 2, F1: another file imports the helper, and may fall back quietly once it is gone.
    (repo / "tests").mkdir()
    (repo / "tests/test_other.py").write_text("from tests.test_seed import helper\n", encoding="utf-8")
    old = "def helper():\n    return True\n\n\ndef test_b():\n    assert helper()\n"
    assert "기존 테스트 변경" in refactor_run.contract(repo, card(repo, old, '"""Rest."""\n', "test_b"))


def test_a_conftest_frees_nothing_beyond_its_declared_tests(repo):
    old = "import pytest\n\n\n@pytest.fixture\ndef db():\n    return 1\n\n\ndef test_b(db):\n    pass\n"
    new = "import pytest\n"
    reason = refactor_run.contract(repo, card(repo, old, new, "test_b", file="tests/conftest.py"))
    assert "기존 테스트 변경" in reason, "other files ask for a conftest's fixtures"


def test_a_refusal_goes_back_naming_what_to_put_back(repo):
    new = SEED.replace("def test_milestone():\n    assert sa\n", "def test_milestone():\n    assert not sa\n")
    spec = card(repo, SEED, new, "test_acyclic")
    refusal = refactor_run.contract(repo, spec)
    assert "기존 테스트 변경" in refusal
    text = refactor_run.restoring(repo, spec, refusal)
    assert "`test_milestone`" in text and "`test_acyclic`" in text and spec["start_head"][:12] in text
    assert loop.contract_repair(repo, spec, {"cmd": "테스트 계약", "reason": refusal}) == text
    assert loop.contract_repair(repo, spec, {"cmd": "pytest", "reason": "x"}) == ""


@pytest.mark.parametrize("old, new, declared, wrong", [
    # Round 6, F1: a class declared whole frees its methods, so none is to be put back.
    ("import os\n\n\nclass TestAllowed:\n    def test_a(self):\n        assert 1\n",
     "class TestAllowed:\n    def test_a(self):\n        assert 2\n", "TestAllowed", "TestAllowed::test_a"),
    # Round 6, F2: an unchanged `@overload` group is not named because its names repeat.
    ("import os\nfrom typing import overload\n\n\n@overload\ndef helper(x: int) -> int: ...\n\n\n"
     "@overload\ndef helper(x: str) -> str: ...\n\n\ndef helper(x):\n    return x\n\n\ndef test_b():\n    pass\n",
     "from typing import overload\n\n\n@overload\ndef helper(x: int) -> int: ...\n\n\n"
     "@overload\ndef helper(x: str) -> str: ...\n\n\ndef helper(x):\n    return x\n", "test_b", "`helper`"),
])
def test_only_what_the_contract_refused_is_named(repo, old, new, declared, wrong):
    spec = card(repo, old, new, declared)
    text = refactor_run.restoring(repo, spec, refactor_run.contract(repo, spec))
    assert "start line 1 `import os`" in text and wrong not in text


def test_a_deleted_test_file_stays_the_persons_to_declare(repo):
    # #252: which tests pytest collects from a file is not settled by its syntax, so no turn undoes the deletion.
    spec = card(repo, SEED, "", "test_acyclic")
    _git(repo, "rm", "-q", "tests/test_seed.py")
    _git(repo, "commit", "-qm", "drop")
    refusal = refactor_run.contract(repo, spec)
    assert "삭제" in refusal and refactor_run.restoring(repo, spec, refusal) == ""


def test_the_worker_is_asked_to_put_it_back_before_the_card_stops(monkeypatch, tmp_path):
    saved, sent, failed = {"contract_repairs": 0}, [], []
    spec = {"id": "c", "repo": "r", "start_head": "0" * 40}
    monkeypatch.setattr(refactor_run, "verified", lambda *a: "테스트 계약 밖의 변경 — `tests/t.py` 의 기존 테스트 변경")
    monkeypatch.setattr(refactor_run, "restoring", lambda *a: "put it back")
    monkeypatch.setattr(refactor_run.specs, "load", lambda repo, sid: {**spec, **saved})
    monkeypatch.setattr(refactor_run.specs, "update", lambda repo, sid, **f: saved.update(f))
    monkeypatch.setattr(refactor_run.specs, "note", lambda run, text: None)
    monkeypatch.setattr(refactor_run.specs, "again", lambda path, chat, text, s: sent.append(text) or True)
    monkeypatch.setattr(refactor_run.specs, "failed", lambda run, s, why: failed.append(why))
    run = type("Run", (), {"chat": None})()
    for _ in range(refactor_run.REPAIRS):
        turn = refactor_run.published(tmp_path, tmp_path, run, {**spec, **saved})
        assert callable(turn) and turn() is True
    assert sent == ["put it back"] * refactor_run.REPAIRS and not failed
    assert refactor_run.published(tmp_path, tmp_path, run, {**spec, **saved}) is None
    assert failed, "a refusal the worker did not clear stops the card for a person"
