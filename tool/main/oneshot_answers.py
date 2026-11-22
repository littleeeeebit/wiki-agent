"""oneshot step 12: what a build turn sent back by the review or the measurement must end with.

`docs/plans/oneshot/12-review-converges-on-measurement.md` is the design. The turn ends on a new commit, never
on a HEAD already refused (`unchanged`). A finding it does not change it answers with evidence, in one
`oneshot-answers` block (`answered`), naming a test committed in that turn which pins the behaviour it defends;
that test must run and pass in the build's own test run (`ran`). A build that passes records its answers and the
mechanisms it replaced (`passed`).
"""

from __future__ import annotations

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
        # The test's code as committed now against the refused HEAD: an uncommitted change never counts, and a
        # comment, a blank line or a neighbour's change leaves its body as it was.
        now = body_of(committed(path, "HEAD", file), name.split("::")) if name else None
        if not now or now == body_of(committed(path, last["head"], file), name.split("::")):
            raise ValueError(f"{where}: `test` 는 이번 턴에 커밋으로 더하거나 고친 테스트다 (`경로::이름`, 클래스 안이면 "
                             f"`경로::클래스::이름`) — {a['test']}")
        out.append({"round": len(o["reviews"]), "head": last["head"], "area": " ".join(a["area"].split()),
                    **{k: a[k].strip() for k in ("location", "evidence", "test")}, "breaks": a["breaks"]})
    return out


COMMENT = ("#", "//", "/*", "*")


def code(line: str) -> bool:
    """A line that holds code: not blank, not a line comment.
    ponytail: a docstring or a block comment's inner line still counts; a parser per language if that matters."""

    return bool(s := line.strip()) and not s.startswith(COMMENT)


def committed(path: Path, rev: str, file: str) -> str:
    """`file` as `rev` holds it, or empty when it does not."""

    from . import specs

    done = specs.sh(["git", "show", f"{rev}:{file}"], path)
    return "" if done.returncode else done.stdout


def body_of(text: str, parts: list[str]) -> str | None:
    """The code lines of the test `parts` names, stripped, or `None` when it is not defined there."""

    if not (span := test_span(text, parts)):
        return None
    return "\n".join(line.strip() for line in text.splitlines()[span[0] - 1:span[1]] if code(line))


def end_of(lines: list[str], start: int) -> int:
    """The 0-based end (exclusive) of the definition at `start`: the next code line no deeper, a closing bracket
    included."""

    depth = len(lines[start]) - len(lines[start].lstrip())
    end = next((j for j in range(start + 1, len(lines)) if code(lines[j])   # a comment does not end the body
                and len(lines[j]) - len(lines[j].lstrip()) <= depth), len(lines))
    return end + 1 if end < len(lines) and lines[end].lstrip()[:1] in "})]" else end


def test_span(text: str, parts: list[str]) -> tuple[int, int] | None:
    """The 1-based lines of the test `parts` names (`[Class, …,] name`) in `text`: its definition (`def`, `class`,
    `func`, `fn`, `function`, or a `test`/`it`/`describe` call titled with it, on a code line) and its body, each
    part found inside the one before it. `None` when a part is not defined there: a mention, or a test a class
    only inherits, is none."""

    lines, lo, hi, start = text.splitlines(), 0, len(text.splitlines()), None
    for part in parts:
        name = re.escape(part.split('[')[0])
        defined = re.compile(rf"^\s*(?:async\s+)?(?:def|class|func|fn|function)\s+{name}\b"
                             rf"|\b(?:test|it|describe)\s*\(\s*[\"'`]{name}[\"'`]")
        start = next((i for i in range(lo, hi) if code(lines[i]) and defined.search(lines[i])), None)
        if start is None:
            return None
        lo, hi = start + 1, end_of(lines, start)
    return start + 1, hi


def refused_lines(path: Path, since: str) -> dict[str, list[tuple[int, int]]]:
    """The lines of commit `since` that the commits to HEAD changed with code, on `since`'s side, where a finding's
    location points: a hunk only of comments and blank lines is none, and a pure insertion marks the lines on
    both sides of it."""

    from . import oneshot_review, specs

    done = specs.sh(["git", "-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff", "-U0",
                     "--src-prefix=a/", "--dst-prefix=b/", since, "HEAD"], path, 120)
    out, file, hunk = {}, None, None

    def close():
        if file and hunk and hunk[2]:
            a, b = hunk[0], hunk[1]
            out.setdefault(file, []).append((a, a + b - 1) if b else (a, a + 1))
    for line in done.stdout.splitlines() if not done.returncode else ():
        if line.startswith("diff --git "):
            close()
            file, hunk = None, None
        elif line.startswith("--- ") and hunk is None:
            name = line[4:].rstrip("\t").strip('"')
            file = oneshot_review.canonical(name[2:]) if name.startswith("a/") else None   # `/dev/null`: new file
        elif m := re.match(r"@@ -(\d+)(?:,(\d+))? \+\S+ @@", line):
            close()
            hunk = [int(m[1]), int(m[2] if m[2] is not None else 1), False]
        elif hunk and line[:1] in "+-" and code(line[1:]):
            hunk[2] = True   # the hunk changes code, on one side or the other
    close()
    return out


def ran(found: list[tuple[str, str, str, str]], answers: list[dict], root: str = "") -> str:
    """Why a pinning test an answer names did not run and pass in the build's test run (`oneshot_build.held`'s
    JUnit cases, `(classname, name, file, outcome)`), or empty: a name that only appears in the file is no test.
    A selector with classes (`path::Class::name`) matches only the case of that class. The answer names a
    worktree path; the report names files and modules from the runner's `root`, as the contract tests do."""

    bad = []
    for a in answers:
        file, _, rest = a["test"].partition("::")
        *classes, name = rest.split("::")
        file = posixpath.normpath(file.replace("\\", "/"))
        local = posixpath.relpath(file, root) if root else file   # the file as the runner names it
        module = local.rsplit(".", 1)[0].replace("/", ".")
        owner = ".".join([module, *classes])
        same = lambda at: at and posixpath.normpath(posixpath.join(root, at.replace("\\", "/"))) == file   # noqa: E731
        kinds = [kind for cls, case, at, kind in found
                 if (case == name or case.startswith(name + "["))
                 and (cls == owner or classes and cls.endswith("." + ".".join(classes)) and same(at)
                      or not classes and (same(at) or cls.startswith(module + ".")
                                          or posixpath.normpath(posixpath.join(root, cls.replace("\\", "/"))) == file))]
        if not kinds or any(k != "passed" for k in kinds):
            bad.append(f"{a['test']}({'돌지 않음' if not kinds else ', '.join(sorted(set(kinds)))})")
    return "답에 적은 고정 테스트가 통과하지 못했다: " + ", ".join(bad) if bad else ""


def passed(path: Path, o: dict, answers: list[dict]) -> dict:
    """What a build that passed its checks records: its answers, and each mechanism its turn was told to replace
    (`oneshot_review.replacing`) that it did replace: an area it did not answer instead, whose refused lines its
    commits changed. One it left as it was is not replaced; a later refusal asks for it again."""

    from . import oneshot_review   # it imports `oneshot_build`, which imports this module

    asked, last = o.get("replacing") or [], next((r for r in reversed(o.get("reviews") or [])
                                                    if r["verdict"] == "refuse"), None)
    changed = refused_lines(path, last["head"]) if asked and last else None
    defended = {oneshot_review.area_of(a) for a in answers}
    done = [area for area in asked if changed and area not in defended and any(
        oneshot_review.touched(f["location"], changed) for f in oneshot_review.blocking(last)
        if oneshot_review.area_of(f) == area)]
    return {"answers": [*(o.get("answers") or []), *answers], "replacing": None,
            "replaced": sorted({*(o.get("replaced") or []), *done})}
