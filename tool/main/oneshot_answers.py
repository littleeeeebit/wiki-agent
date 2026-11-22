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
    # The lines committed since the refusal: an uncommitted change never counts, the clean-tree gate aside.
    if (since := oneshot_review.changed_lines(path, last["head"], "HEAD")) is None:
        raise ValueError("거절한 HEAD 와 지금 HEAD 를 비교하지 못했다")
    since = coded(path, since)
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
        target = (path / file).resolve()
        span = test_span(target.read_text(encoding="utf-8", errors="replace"), name.split("::")) \
            if name and target.is_relative_to(path.resolve()) and target.is_file() else None
        if not span or not any(lo <= span[1] and span[0] <= hi
                               for lo, hi in since.get(posixpath.normpath(file.replace("\\", "/")), [])):
            raise ValueError(f"{where}: `test` 는 이번 턴에 커밋으로 더하거나 고친 테스트다 (`경로::이름`, 클래스 안이면 "
                             f"`경로::클래스::이름`) — {a['test']}")
        out.append({"round": len(o["reviews"]), "head": last["head"], "area": " ".join(a["area"].split()),
                    **{k: a[k].strip() for k in ("location", "evidence", "test")}, "breaks": a["breaks"]})
    return out


COMMENT = ("#", "//", "/*", "*", "--")


def code(line: str) -> bool:
    return bool(s := line.strip()) and not s.startswith(COMMENT)


def coded(path: Path, changed: dict[str, list[tuple[int, int]]]) -> dict[str, list[tuple[int, int]]]:
    """`changed` (HEAD's side) without the lines that hold no code at HEAD: a blank line or a comment changes no
    behaviour, so it neither changes a test nor replaces a mechanism.
    ponytail: a line comment test; a docstring or block comment body still counts, a parser per language if not."""

    out = {}
    for file, ranges in changed.items():
        try:
            lines = (path / file).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if kept := [(i, i) for lo, hi in ranges for i in range(lo, min(hi, len(lines)) + 1) if code(lines[i - 1])]:
            out[file] = kept
    return out


def test_span(text: str, parts: list[str]) -> tuple[int, int] | None:
    """The 1-based lines of the test `parts` names (`[Class, …,] name`) in `text`: from its definition (`def`,
    `class`, `func`, `fn`, `function`, or a `test`/`it`/`describe` call titled with it), after each enclosing
    class's, to the next code line no deeper than it, a closing bracket included. `None` when a part is not
    defined: a mere mention is no test."""

    lines, at = text.splitlines(), 0
    for part in parts:
        name = re.escape(part.split('[')[0])   # a definition, or a test call titled with it; never a mere mention
        defined = re.compile(rf"^\s*(?:async\s+)?(?:def|class|func|fn|function)\s+{name}\b"
                             rf"|\b(?:test|it|describe)\s*\(\s*[\"'`]{name}[\"'`]")
        at = next((i for i in range(at, len(lines)) if defined.search(lines[i])), None)
        if at is None:
            return None
        start, at = at, at + 1
    depth = len(lines[start]) - len(lines[start].lstrip())
    end = next((j for j in range(start + 1, len(lines)) if code(lines[j])   # a comment does not end the body
                and len(lines[j]) - len(lines[j].lstrip()) <= depth), len(lines))
    if end < len(lines) and lines[end].lstrip()[:1] in "})]":
        end += 1
    return start + 1, end


def ran(found: list[tuple[str, str, str, str]], answers: list[dict]) -> str:
    """Why a pinning test an answer names did not run and pass in the build's test run (`oneshot_build.held`'s
    JUnit cases, `(classname, name, file, outcome)`), or empty: a name that only appears in the file is no test.
    A selector with classes (`path::Class::name`) matches only the case of that class."""

    bad = []
    for a in answers:
        file, _, rest = a["test"].partition("::")
        *classes, name = rest.split("::")
        file = posixpath.normpath(file.replace("\\", "/"))
        module = file.rsplit(".", 1)[0].replace("/", ".")
        owner = ".".join([module, *classes])
        kinds = [kind for cls, case, at, kind in found
                 if (case == name or case.startswith(name + "["))
                 and (cls == owner or classes and cls.endswith("." + ".".join(classes))
                      and posixpath.normpath(at.replace("\\", "/")) == file
                      or not classes and (posixpath.normpath(at.replace("\\", "/")) == file
                                          or cls.startswith(module + ".") or cls.replace("\\", "/") == file))]
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
    changed = oneshot_review.changed_lines(path, last["head"], "HEAD") if asked and last else None
    changed = coded(path, changed) if changed else changed
    defended = {oneshot_review.area_of(a) for a in answers}
    done = [area for area in asked if changed and area not in defended and any(
        oneshot_review.touched(f["location"], changed) for f in oneshot_review.blocking(last)
        if oneshot_review.area_of(f) == area)]
    return {"answers": [*(o.get("answers") or []), *answers], "replacing": None,
            "replaced": sorted({*(o.get("replaced") or []), *done})}
