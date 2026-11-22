"""oneshot step 12: what a build turn sent back by the review or the measurement must end with.

`docs/plans/oneshot/12-review-converges-on-measurement.md` is the design. The turn ends on a new commit, never
on a HEAD already refused (`unchanged`). A finding it does not change it answers with evidence, in one
`oneshot-answers` block (`answered`), naming a test committed in that turn which pins the behaviour it defends;
that test must run and pass in the build's own test run (`ran`). A build that passes records its answers and the
mechanisms it replaced (`passed`).
"""

from __future__ import annotations

import ast
import json
import posixpath
import re
from pathlib import Path

from . import oneshot_build


def unchanged(path: Path, o: dict) -> str:
    """Why the build ended on a HEAD the review refused, or one whose measurement failed or the owner refused, or
    empty. Any such HEAD, the last or an earlier one gone back to, is never read again: the turn commits its fix,
    or the tests that pin what it answered with evidence."""

    now = oneshot_build.head(path)
    if now in {r["head"] for r in o.get("reviews") or [] if r["verdict"] == "refuse"}:
        return (f"리뷰가 거절한 HEAD `{now[:12]}` 에서 커밋이 하나도 늘지 않았다 — 고친 코드를 커밋하거나, 고치지 않고 "
                "증거로 답한 발견마다 그 동작을 고정하는 테스트를 커밋하고 `oneshot-answers` 에 적는다")
    if now in {m["head"] for m in o.get("measures") or []
               if m["status"] == "failed" or (m.get("owner") or {}).get("approve") is False}:
        return f"실측이 실패했거나 주인이 거절한 HEAD `{now[:12]}` 에서 커밋이 하나도 늘지 않았다 — 원인을 고쳐 커밋한다"
    return ""


ANSWER = re.compile(r"^```oneshot-answers[ \t]*\r?\n(.*?)^```[ \t]*$", re.M | re.S)


def answered(path: Path, spec: dict, final: str) -> list[dict]:
    """The turn's `oneshot-answers`: each finding of the standing refusal it answered with evidence instead of a
    change, the requirement a change would break, and the test committed since that refusal which pins the
    behaviour it defends. `ValueError` says what is wrong; a turn sent back by nothing answers nothing."""

    from . import oneshot_review   # it imports `oneshot_build`, which imports this module

    o = spec["oneshot"]
    found = ANSWER.findall(final)
    if not found:
        return []
    last = (o.get("reviews") or [None])[-1]
    if len(found) > 1 or o.get("fix") != "review" or not last or last["verdict"] != "refuse":
        raise ValueError("`oneshot-answers` 는 리뷰가 거절해 돌아온 빌드 턴에 하나만 적는다")
    try:
        value = json.loads(found[0])
    except ValueError as exc:
        raise ValueError(f"`oneshot-answers` 가 JSON 이 아니다 — {exc}") from None
    areas = {oneshot_review.area_of(f) for f in oneshot_review.blocking(last)}
    anchors, out = oneshot_review.anchors(spec), []
    for n, a in enumerate(value if isinstance(value, list) else [None]):
        where = f"`oneshot-answers`[{n}]"
        if not isinstance(a, dict) or not all(isinstance(a.get(k), str) and a[k].strip()
                                               for k in ("area", "location", "evidence", "test")):
            raise ValueError(f"{where}: area, location, evidence, breaks, test 를 적는다")
        if oneshot_review.area_of(a) not in areas:
            raise ValueError(f"{where}: `{a['area']}` 는 이번 거절의 영역이 아니다 — 발견의 `area` 를 그대로 적는다")
        if not isinstance(a.get("breaks"), list) or not a["breaks"] or not all(isinstance(b, str) and b in anchors
                                                                              for b in a["breaks"]):
            raise ValueError(f"{where}: `breaks` 는 고치면 깨지는 설계 항목·요구 번호의 목록이다")
        file, _, name = a["test"].partition("::")
        file = posixpath.normpath(file.replace("\\", "/"))
        # A test committed since the refusal that the refused HEAD did not define: an uncommitted file never
        # counts, and no edit of comments, spacing or nesting of an old test makes it new.
        parts = name.split("::")
        if not name or not test_span(committed(path, "HEAD", file), parts, file) \
                or test_span(committed(path, last["head"], file), parts, file):
            raise ValueError(f"{where}: `test` 는 이번 턴에 커밋으로 새로 더한 테스트다 (`경로::이름`, 클래스 안이면 "
                             f"`경로::클래스::이름`) — {a['test']}")
        out.append({"round": len(o["reviews"]), "head": last["head"], "area": " ".join(a["area"].split()),
                    **{k: a[k].strip() for k in ("location", "evidence", "test")}, "breaks": a["breaks"]})
    return out


HASH = {".py", ".pyi", ".rb", ".sh", ".ps1", ".pl", ".r", ".yaml", ".yml", ".toml"}
SLASH = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".swift", ".c", ".cc", ".cpp",
         ".h", ".hpp", ".cs", ".dart", ".scala", ".php"}


def marks_of(file: str) -> tuple[str, ...]:
    """The line comment marks of `file`'s language: Python's `//` is floor division, never a comment."""

    suffix = posixpath.splitext(file)[1].lower()
    return ("#",) if suffix in HASH else ("//",) if suffix in SLASH else ("#", "//")


def stripped(line: str, marks: tuple[str, ...] = ("#", "//")) -> str:
    """`line` without its trailing line comment (a mark outside a string), right-stripped; empty for a line that is
    only a comment. ponytail: a block comment's inner line or a docstring still counts as code."""

    quote, i = "", 0
    while i < len(line):
        c = line[i]
        if quote:
            quote = "" if c == quote else quote
            i += 2 if c == "\\" else 1
            continue
        if c in "\"'`":
            quote = c
        elif any(line.startswith(m, i) for m in marks):
            line = line[:i]
            break
        i += 1
    block = ("/*", "*") if "//" in marks else ()
    return "" if line.lstrip().startswith(block) else line.rstrip()


def quoted(line: str, at: int) -> bool:
    """Whether `line[at]` lies inside a string literal of that line."""

    quote, i = "", 0
    while i < at:
        if quote and line[i] == "\\":
            i += 2
            continue
        quote = ("" if line[i] == quote else quote) if quote else (line[i] if line[i] in "\"'`" else "")
        i += 1
    return bool(quote)


def code(line: str, marks: tuple[str, ...] = ("#", "//")) -> bool:
    """A line that holds code: not blank, not only a comment."""

    return bool(stripped(line, marks).strip())


def committed(path: Path, rev: str, file: str) -> str:
    """`file` as `rev` holds it, or empty when it does not."""

    from . import specs

    done = specs.sh(["git", "show", f"{rev}:{file}"], path)
    return "" if done.returncode else done.stdout


def end_of(lines: list[str], start: int, marks: tuple[str, ...]) -> int:
    """The 0-based end (exclusive) of the definition at `start`: the next code line no deeper, a closing bracket
    included."""

    depth = len(lines[start]) - len(lines[start].lstrip())
    end = next((j for j in range(start + 1, len(lines)) if code(lines[j], marks)   # a comment does not end it
                and len(lines[j]) - len(lines[j].lstrip()) <= depth), len(lines))
    return end + 1 if end < len(lines) and lines[end].lstrip()[:1] in "})]" else end


PYTHON = (".py", ".pyi")


def test_span(text: str, parts: list[str], file: str = "") -> tuple[int, int] | None:
    """The 1-based lines of the test `parts` names (`[Class, …,] name`) in `text`: its definition (`def`, `class`,
    `func`, `fn`, `function`, or a `test`/`it`/`describe` call titled with it, on a code line) and its body, each
    part defined directly in the body of the one before it. `None` when a part is not defined there: a mention,
    a test a class only inherits, or a same-named function nested deeper, is none. Python is read by its syntax
    tree; other languages line by line."""

    if file.lower().endswith(PYTHON) or not file:
        node = None
        try:
            nodes, node = ast.parse(text).body, None
        except (SyntaxError, ValueError):
            nodes = None   # not Python, or not parsable: read line by line below
        for part in parts if nodes is not None else ():
            node = next((n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                         and n.name == part.split("[")[0]), None)
            if node is None:
                return None
            nodes = node.body
        if node is not None:
            return node.lineno, node.end_lineno
    lines, lo, hi, start, depth = text.splitlines(), 0, len(text.splitlines()), None, None
    marks = marks_of(file)
    for part in parts:
        name = re.escape(part.split('[')[0])
        defined = re.compile(rf"^\s*(?:async\s+)?(?:def|class|func|fn|function)\s+{name}\b"
                             rf"|\b(?:test|it|describe)\s*\(\s*[\"'`]{name}[\"'`]")
        start = next((i for i in range(lo, hi) if any(not quoted(s, m.start()) for s in [stripped(lines[i], marks)]
                                                      for m in defined.finditer(s))   # a mention in a string is none
                      and (depth is None or len(lines[i]) - len(lines[i].lstrip()) == depth)), None)
        if start is None:
            return None
        lo, hi = start + 1, end_of(lines, start, marks)
        body = next((lines[i] for i in range(lo, hi) if code(lines[i], marks)), "")
        depth = len(body) - len(body.lstrip())   # the next part is a direct member, at the body's own indent
    return start + 1, hi


def changed_files(path: Path, since: str) -> dict[str, list[tuple[int, int]]]:
    """Every file the commits from `since` to HEAD changed, each byte counted, in `oneshot_review.touched`'s shape
    (all its lines). An area counts as replaced once its finding's file is no longer what the refusal read: the
    review, not a line diff, judges whether the mechanism changed; this only rules out a turn that left it alone.
    A comment edit in that file counts as well."""

    from . import oneshot_review, specs

    done = specs.sh(["git", "-c", "core.quotePath=false", "diff", "--no-renames", "--name-only", "-z", since, "HEAD"],
                    path, 120)
    files = done.stdout.split("\0") if not done.returncode else []
    return {oneshot_review.canonical(f): [(1, 10 ** 9)] for f in files if f}


def runner_root(o: dict) -> str:
    """The folder the test runner names files from: saved at the contract commit (`runner_root`), or, for a run
    committed before it was saved, read off a pinned case's file against the contract file it is."""

    if (root := o.get("runner_root")) is not None:
        return root
    for _, _, at in o.get("cases") or []:
        at = posixpath.normpath((at or "").replace("\\", "/"))
        for c in o.get("contract") or []:
            if at != "." and c != at and c.endswith("/" + at):
                return c[:-len(at) - 1]
    return ""   # no file in the old report: the root itself, and `ran` asks for a report that names files


def ran(found: list[tuple[str, str, str, str]], answers: list[dict], root: str = "") -> str:
    """Why a pinning test an answer names did not run and pass in the build's test run (`oneshot_build.held`'s
    JUnit cases, `(classname, name, file, outcome)`), or empty: a name that only appears in the file is no test.
    A case that names its file is the selector's only where that file is the answer's, its class name ending in
    the module's own name and the selector's classes (`path::name` none); one without a file only where its class
    name is exactly the module and classes, read from the runner's `root`, as the contract tests are. No prefix
    or suffix is guessed: a runner that adds one names the file (pytest `-o junit_family=xunit1`)."""

    bad = []
    for a in answers:
        file, _, rest = a["test"].partition("::")
        *classes, name = rest.split("::")
        file = posixpath.normpath(file.replace("\\", "/"))
        local = posixpath.relpath(file, root) if root else file   # the file as the runner names it
        module = local.rsplit(".", 1)[0].replace("/", ".")
        owner = ".".join([module, *classes])
        same = lambda at: at and posixpath.normpath(posixpath.join(root, at.replace("\\", "/"))) == file   # noqa: E731
        tail = [module.rsplit(".", 1)[-1], *classes]   # what ends the class name of exactly this case
        kinds = [kind for cls, case, at, kind in found
                 if (case == name or case.startswith(name + "["))
                 and (same(at) and cls.split(".")[-len(tail):] == tail if at else cls == owner
                      or not classes and posixpath.normpath(posixpath.join(root, cls.replace("\\", "/"))) == file)]
        if not kinds or any(k != "passed" for k in kinds):
            bad.append(f"{a['test']}({'돌지 않음' if not kinds else ', '.join(sorted(set(kinds)))})")
    return ("답에 적은 고정 테스트가 통과하지 못했다: " + ", ".join(bad) + " — 보고서의 사례가 그 파일(`file`)을 적게 하면 "
            "파일로 맞춘다 (pytest 는 `-o junit_family=xunit1`)") if bad else ""


def passed(path: Path, o: dict, answers: list[dict]) -> dict:
    """What a build that passed its checks records: its answers, and each mechanism its turn was told to replace
    (`oneshot_review.replacing`) that it did replace: an area it did not answer instead, whose finding's file its
    commits changed (`changed_files`). One it left as it was is not replaced; a later refusal asks for it again."""

    from . import oneshot_review   # it imports `oneshot_build`, which imports this module

    asked, last = o.get("replacing") or [], next((r for r in reversed(o.get("reviews") or [])
                                                    if r["verdict"] == "refuse"), None)
    changed = changed_files(path, last["head"]) if asked and last else None
    defended = {oneshot_review.area_of(a) for a in answers}
    done = [area for area in asked if changed and area not in defended and any(
        oneshot_review.touched(f["location"], changed) for f in oneshot_review.blocking(last)
        if oneshot_review.area_of(f) == area)]
    return {"answers": [*(o.get("answers") or []), *answers], "replacing": None,
            "replaced": sorted({*(o.get("replaced") or []), *done})}
