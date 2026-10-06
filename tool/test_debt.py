"""The debt metrics and the ratchet the final gate runs."""

import json
import subprocess
from pathlib import Path

import debt
from main import specs

BODY = "\n".join(f"    total = total + value_{i} * weight_{i}" for i in range(8))


def _lines(n: int, name: str) -> str:
    """`n` distinct lines: a repeated line would itself be a duplicated block."""

    return "".join(f"{name}{i} = {i}\n" for i in range(n))


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                   capture_output=True)


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _write(tmp_path, files)
    return tmp_path


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def _commit_as_base(root: Path) -> None:
    _git(root, "add", "-A", "-f")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")


def test_duplicates_are_shared_blocks_not_imports_comments_or_punctuation(tmp_path):
    noise = "\n".join(["import os", "# same comment", "}", ")", "    pass"] * 4)
    root = _repo(tmp_path, {"a.py": f"def a():\n{BODY}\n", "b.ts": f"function b() {{\n{BODY}\n}}\n",
                            "c.py": noise + "\n", "d.py": noise + "\n", "notes.md": BODY})
    m = debt.measure(root)
    assert m["a.py"]["dup"] == m["b.ts"]["dup"] == 8
    assert m["c.py"]["dup"] == m["d.py"]["dup"] == 0
    assert "notes.md" not in m, "documents are not code"
    names = [f"    Type{i}," for i in range(6)]
    assert debt.duplicated({"a.ts": ["import {", *names, '} from "./one";'],
                            "b.py": ["from x import (", *names, ")"]}) == {"a.ts": 0, "b.py": 0}
    assert debt.duplicated({"a.ts": ["import { // types", *names, '} from "./one";'],
                            "b.py": ["from x import (  # noqa: F401", *names, ")"]}) == {"a.ts": 0, "b.py": 0}, \
        "a comment after the opener still opens the import"
    body = [f"    total += value{i};" for i in range(6)]
    code = {"a.cs": ["using (var c = Open()) {", *body, "}"], "b.ts": ['import("./x").then(m => {', *body, "});"]}
    assert debt.duplicated(code) == {"a.cs": 6, "b.ts": 6}, "a block opened by `using` or `import(` is code"


def test_longest_block_is_a_body_not_its_container():
    lines = ["class Big:", "    def small(self):", "        return 1", "", "    def large(self):",
             *["        x = 1"] * 5, "    y = 2"]
    assert debt.longest_block(lines) == 6
    assert debt.longest_block(["function f() {", "  a();", "  b();", "}"]) == 3


def test_check_holds_entries_caps_and_new_reasons(tmp_path):
    root = _repo(tmp_path, {"big.py": _lines(900, "x"), "small.py": "y = 2\n"})
    assert debt.check(root) == ([], ""), "a repository without the ratchet passes"
    ratchet = debt.init(root)
    assert set(ratchet["files"]) == {"big.py"}
    _commit_as_base(root)
    assert debt.check(root, "main") == ([], "")

    _write(root, {"big.py": _lines(901, "x"), "new.py": _lines(801, "z"), "test_new.py": _lines(1000, "t")})
    problems, _ = debt.check(root, "main")
    assert problems == ["big.py: 901줄 — 기준 900줄을 넘었다", "new.py: 801줄 — 기준 800줄을 넘었다"]

    _write(root, {"new.py": "z = 3\n", "small.py": f"def s():\n{BODY}\n", "other.py": f"def o():\n{BODY}\n"})
    problems, _ = debt.check(root, "main")
    assert "small.py: 중복 8줄 — 기준 0줄을 넘었다" in problems, "a new copy fails both of its files"

    _write(root, {"small.py": "y = 2\n", "other.py": "w = 4\n"})
    file = root / debt.RATCHET
    data = json.loads(file.read_text(encoding="utf-8"))
    data["files"]["big.py"]["lines"] = 901
    debt.write(file, data)
    assert debt.check(root, "main")[0] == ["big.py: 기준을 올렸는데 새 reason 이 없다"]
    data["files"]["big.py"]["reason"] = "the parser table is generated in place"
    data["new_file_max_lines"] = 900
    debt.write(file, data)
    assert debt.check(root, "main")[0] == [f"{debt.RATCHET}: 상한이나 exclude 를 넓혔는데 새 최상위 reason 이 없다"]
    data["reason"] = "agreed cap for this repository"
    debt.write(file, data)
    assert debt.check(root, "main") == ([], "")
    assert debt.check(root, "gone")[1], "an unreadable base is said, not passed silently"

    file.write_text('{"version": 1}', encoding="utf-8")
    try:
        debt.check(root)
        raise AssertionError("a malformed ratchet must not pass")
    except ValueError:
        pass


def test_tighten_only_lowers_and_drops_what_the_caps_cover(tmp_path):
    root = _repo(tmp_path, {"big.py": _lines(1200, "x"), "mid.py": _lines(1000, "y"), "gone.py": _lines(900, "z")})
    debt.init(root)
    _write(root, {"big.py": _lines(1000, "x"), "mid.py": _lines(700, "y")})
    (root / "gone.py").unlink()
    assert debt.tighten(root) == [debt.RATCHET]
    files = json.loads((root / debt.RATCHET).read_text(encoding="utf-8"))["files"]
    assert {k: v["lines"] for k, v in files.items()} == {"big.py": 1000}
    assert debt.tighten(root) == [debt.RATCHET], "a retried preparation still owns the lowered file"
    _commit_as_base(root)
    _write(root, {"big.py": _lines(1100, "x")})
    assert debt.tighten(root) == [], "growth never raises an entry"


def test_final_gate_runs_the_ratchet_and_keeps_shell_out_of_the_base(tmp_path, monkeypatch):
    monkeypatch.setattr(specs, "gate_of", lambda repo: "git --version")
    cmd = specs.required(tmp_path, {"base": "main", "done": ["x"]})
    assert cmd.startswith("git --version && ") and cmd.endswith(" check --base main")
    assert debt.command("main & del x") == debt.command("")
    root = _repo(tmp_path, {"a.py": "a = 1\n"})
    assert subprocess.run(cmd, shell=True, cwd=root, capture_output=True).returncode == 0
    debt.init(root)
    _write(root, {"a.py": _lines(801, "a")})
    done = subprocess.run(debt.command(), shell=True, cwd=root, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 1 and "래칫 실패" in done.stdout
