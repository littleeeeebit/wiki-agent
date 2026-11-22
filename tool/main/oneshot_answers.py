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


HIDDEN = "\0"


def unquoted(text: str, marks: tuple[str, ...] = ("#", "//")) -> list[str]:
    """`text`'s lines with every string literal's content and every comment replaced by `HIDDEN`, columns kept.
    The state carries across lines, so a template literal (`` ` ``) or a block comment spans them.
    A block comment (`/* */`) counts only where `//` marks comments; `'` and `"` end at their line.
    ponytail: a regex literal reads as code."""

    out, quote, i = [], "", 0
    while i < len(text):
        c = text[i]
        if quote in ("/*", "line"):   # inside a comment
            end = quote == "/*" and text.startswith("*/", i)
            out.append("\n" if c == "\n" else HIDDEN * (2 if end else 1))
            quote = "" if end or (c == "\n" and quote == "line") else quote
            i += 2 if end else 1
        elif quote:
            if c == "\\" and i + 1 < len(text):
                out.append(HIDDEN + ("\n" if text[i + 1] == "\n" else HIDDEN))
                i += 2
                continue
            if c == quote:   # the closing quote stays visible, the content does not
                quote = ""
                out.append(c)
            elif c == "\n":
                quote = quote if quote == "`" else ""
                out.append(c)
            else:
                out.append(HIDDEN)
            i += 1
        elif "//" in marks and text.startswith("/*", i):
            quote, i = "/*", i + 2
            out.append(HIDDEN * 2)
        elif any(text.startswith(m, i) for m in marks):
            quote, i = "line", i + 1
            out.append(HIDDEN)
        else:
            quote = c if c in "\"'`" else ""
            out.append(c)
            i += 1
    return "".join(out).split("\n")


def code(line: str) -> bool:
    """A line of `unquoted` output that holds code: something beside blanks, strings and comments."""

    return bool(line.replace(HIDDEN, "").strip())


def committed(path: Path, rev: str, file: str) -> str:
    """`file` as `rev` holds it, or empty when it does not."""

    from . import specs

    done = specs.sh(["git", "show", f"{rev}:{file}"], path)
    return "" if done.returncode else done.stdout


def end_of(lines: list[str], start: int) -> int:
    """The 0-based end (exclusive) of the definition at `start` in `unquoted` lines: the next code line no deeper,
    a closing bracket included."""

    depth = len(lines[start]) - len(lines[start].lstrip())
    end = next((j for j in range(start + 1, len(lines)) if code(lines[j])   # a comment or a string does not end it
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
    lines = text.replace("\r\n", "\n").split("\n")
    seen, lo, hi, start, depth = unquoted("\n".join(lines), marks_of(file)), 0, len(lines), None, None
    for part in parts:
        name = re.escape(part.split('[')[0])
        defined = re.compile(rf"^\s*(?:async\s+)?(?:def|class|func|fn|function)\s+{name}\b"
                             rf"|\b(?:test|it|describe)\s*\(\s*[\"'`]{name}[\"'`]")
        # A match whose first word is code, not inside a string or a comment, on any line before.
        start = next((i for i in range(lo, hi) if any(seen[i][m.end() - len(m[0].lstrip())] != HIDDEN
                                                      for m in defined.finditer(lines[i]))
                      and (depth is None or len(lines[i]) - len(lines[i].lstrip()) == depth)), None)
        if start is None:
            return None
        lo, hi = start + 1, end_of(seen, start)
        body = next((lines[i] for i in range(lo, hi) if code(seen[i])), "")
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
    committed before it was saved, the one folder every pinned case's relative file fits against the contract
    files (an exact path is the root itself); the root itself when none or more than one fits."""

    if (root := o.get("runner_root")) is not None:
        return root
    roots = None
    for _, _, at in o.get("cases") or []:
        at = posixpath.normpath((at or "").replace("\\", "/"))
        if at == "." or Path(at).is_absolute():
            continue   # no file, or one `oneshot_build.source` reads without a root
        fits = {"" if c == at else c[:-len(at) - 1] for c in o.get("contract") or [] if c == at or c.endswith("/" + at)}
        roots = fits if roots is None else roots & fits
    return roots.pop() if roots and len(roots) == 1 else ""


def alone(path: Path, file: str, parts: list[str]) -> bool:
    """Whether `file` is the only file HEAD tracks, of its own name in any folder and language, that defines the
    test `parts` names: then a case of that name is that test, whatever prefix or root the runner gave it."""

    from . import specs

    stem = posixpath.splitext(posixpath.basename(file))[0]
    done = specs.sh(["git", "-c", "core.quotePath=false", "ls-files", "-z"], path, 60)
    return not done.returncode and not any(
        f != file and posixpath.splitext(posixpath.basename(f))[0] == stem
        and test_span(committed(path, "HEAD", f), parts, f) for f in done.stdout.split("\0") if f)


def ran(found: list[tuple[str, str, str, str]], answers: list[dict], root: str = "",
        path: Path | None = None) -> str:
    """Why a pinning test an answer names did not run and pass in the build's test run (`oneshot_build.held`'s
    JUnit cases, `(classname, name, file, outcome)`), or empty: a name that only appears in the file is no test.
    A case's class name ends in the file's module name and the selector's classes (`path::name` none). One that
    names its file counts only where that file, read as the contract gate reads it (`oneshot_build.source`, from
    the runner's `root`), is the answer's; one without a file only while the answer's file is the only file of
    its name that defines the test (`alone`), so no prefix or root is guessed."""

    bad = []
    for a in answers:
        file, _, rest = a["test"].partition("::")
        *classes, name = rest.split("::")
        file = posixpath.normpath(file.replace("\\", "/"))
        tail = [posixpath.splitext(posixpath.basename(file))[0], *classes]   # what ends this case's class name
        where = (lambda at: oneshot_build.source(path, at, root)) if path else (   # noqa: E731
            lambda at: posixpath.normpath(posixpath.join(root, at.replace("\\", "/"))))
        named = [(at, kind) for cls, case, at, kind in found
                 if (case == name or case.startswith(name + "[")) and cls.split(".")[-len(tail):] == tail]
        sole = path is not None and any(not at for at, _ in named) and alone(path, file, [*classes, name])
        kinds = [kind for at, kind in named if (where(at) == file if at else sole)]
        if not kinds or any(k != "passed" for k in kinds):
            bad.append(f"{a['test']}({'돌지 않음' if not kinds else ', '.join(sorted(set(kinds)))})")
    return ("답에 적은 고정 테스트가 통과하지 못했다: " + ", ".join(bad) + " — 같은 이름의 파일이 여럿이면 보고서의 사례가 "
            "그 파일(`file`)을 적게 한다 (pytest 는 `-o junit_family=xunit1`)") if bad else ""


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
