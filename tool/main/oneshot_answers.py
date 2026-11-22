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


class Again(str):
    """A refusal that goes back to the builder without counting against `oneshot_build.MAX_ATTEMPTS`: a turn
    that ended without a commit is a bug of the turn, never a stop (owner decision)."""


def unchanged(path: Path, o: dict) -> str:
    """Why the build ended on a HEAD the review refused, or one whose measurement failed or the owner refused, as
    an `Again`, or empty. Any such HEAD, the last or an earlier one gone back to, is never read again: the turn
    commits its fix, or the tests that pin what it answered with evidence."""

    now = oneshot_build.head(path)
    if now in {r["head"] for r in o.get("reviews") or [] if r["verdict"] == "refuse"}:
        return Again(f"리뷰가 거절한 HEAD `{now[:12]}` 에서 커밋이 하나도 늘지 않았다 — 고친 코드를 커밋하거나, 고치지 않고 "
                     "증거로 답한 발견마다 그 동작을 고정하는 테스트를 커밋하고 `oneshot-answers` 에 적는다")
    if now in {m["head"] for m in o.get("measures") or []
               if m["status"] == "failed" or (m.get("owner") or {}).get("approve") is False}:
        return Again(f"실측이 실패했거나 주인이 거절한 HEAD `{now[:12]}` 에서 커밋이 하나도 늘지 않았다 — 원인을 고쳐 커밋한다")
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
    anchors, out, changed = oneshot_review.anchors(spec), [], None
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
        # The test's file is one this turn's commits changed: an uncommitted file never counts. That the test is
        # new is read after the gate, from the test run itself (`ran`), never from parsing its source.
        if not name or oneshot_review.canonical(file) not in (changed := changed or changed_files(path, last["head"])) \
                or not committed(path, "HEAD", file):   # changed, and still there: a deleted file pins nothing
            raise ValueError(f"{where}: `test` 는 이번 턴에 커밋으로 새로 더한 테스트다 (`경로::이름`, 클래스 안이면 "
                             f"`경로::클래스::이름`) — 그 파일이 이번 턴 커밋에서 바뀌지 않았거나 HEAD 에 없다: {a['test']}")
        out.append({"round": len(o["reviews"]), "head": last["head"], "area": " ".join(a["area"].split()),
                    **{k: a[k].strip() for k in ("location", "evidence", "test")}, "breaks": a["breaks"]})
    return out


def committed(path: Path, rev: str, file: str) -> str:
    """`file` as `rev` holds it, or empty when it does not or holds a folder there."""

    from . import specs

    done = specs.sh(["git", "cat-file", "blob", f"{rev}:{file}"], path)
    return "" if done.returncode else done.stdout


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


def runner_root(o: dict, path: Path | None = None) -> str:
    """The folder the test runner names files from: saved at the contract commit (`runner_root`), or, for a run
    committed before it was saved, the one folder, of every folder HEAD tracks a file in, from which every
    pinned case's relative file is a contract file (`../` included, as `oneshot_build.source` reads it); the
    root itself when none or more than one fits."""

    from . import specs

    if (root := o.get("runner_root")) is not None:
        return root
    contract = set(o.get("contract") or [])
    done = specs.sh(["git", "-c", "core.quotePath=false", "ls-files", "-z"], path, 60) if path else None
    tracked = [*contract, *(done.stdout.split("\0") if done and not done.returncode else [])]
    folders = {""} | {"/".join(f.split("/")[:n]) for f in tracked if f for n in range(1, f.count("/") + 1)}
    roots = None
    for _, _, at in o.get("cases") or []:
        at = (at or "").replace("\\", "/")
        if not at or Path(at).is_absolute():
            continue   # no file, or one `oneshot_build.source` reads without a root
        fits = {r for r in folders if posixpath.normpath(posixpath.join(r, at)) in contract}
        roots = fits if roots is None else roots & fits
    return roots.pop() if roots and len(roots) == 1 else ""


def ran(found: list[tuple[str, str, str, str]], answers: list[dict], o: dict, path: Path | None = None) -> str:
    """Why a pinning test an answer names did not run and pass as a new test in the build's test run
    (`oneshot_build.held`'s JUnit cases, `(classname, name, file, outcome)`), or empty. A case counts where its
    name is the selector's, its file, where the report names one, is the answer's file (read as the contract gate
    reads it, `oneshot_build.source`, from the runner's root), and the refused build's run did not report it: a
    test that ran before is not new. A class the selector names must be a word of the case's class name. The
    run's cases go on `o` as `run_cases`, which `passed` saves for the next round. Where either run left a
    case's file out, its class name and name alone say it ran before. ponytail: a report without files is
    matched by name and classes, and a run from before `run_cases` was saved has no newness check."""

    root, before = runner_root(o, path), o.get("run_cases")

    def where(at: str) -> str:   # a report's file as a worktree path, every spelling of one file alike
        return oneshot_build.source(path or Path.cwd(), at, root) if at else ""

    o["run_cases"] = sorted({tuple(c[:3]) for c in found})   # as reported; both runs are read through `where`
    old = {(c[0], c[1], where(c[2])) for c in before or []}
    bare = {(c[0], c[1]) for c in before or []}   # a case without a file on either side is old by its names alone
    unfiled = {(c[0], c[1]) for c in before or [] if not c[2]}
    bad = []
    for a in answers:
        file, _, rest = a["test"].partition("::")
        *classes, name = rest.split("::")
        file = posixpath.normpath(file.replace("\\", "/"))
        named = [((c[0], c[1], where(c[2])) in old or (c[0], c[1]) in (unfiled if c[2] else bare), c[3]) for c in found
                 if (c[1] == name or c[1].startswith(name + "[")) and (not c[2] or where(c[2]) == file)
                 and set(classes) <= set(re.split(r"\W+", c[0]))]   # a class it names is a word of the class name
        kinds = [kind for was, kind in named if not was]
        if not kinds or any(k != "passed" for k in kinds):
            why = "이전 실행에도 있던 테스트" if named and not kinds else "돌지 않음" if not kinds else ", ".join(sorted(set(kinds)))
            bad.append(f"{a['test']}({why})")
    return "답에 적은 고정 테스트가 새 테스트로 통과하지 못했다: " + ", ".join(bad) if bad else ""


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
    return {"answers": [*(o.get("answers") or []), *answers], "replacing": None, "run_cases": o.get("run_cases"),
            "replaced": sorted({*(o.get("replaced") or []), *done})}
