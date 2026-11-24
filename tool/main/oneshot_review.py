"""oneshot S4's backend review: a read-only reading of the build, between agents and before any pull request.

`docs/plans/oneshot/3-spec-backend.md` ("Backend review") is the design.
The input is frozen per HEAD: the design and the requirements go into the turn's prompt, the diff from the
base commit is written where `pr_diff` reads it, and a copy is kept in the run's folder as the record.
A refusal goes to the build and the new HEAD is reviewed again, up to the Review Loop's round cap; the
refusal that reaches it stops the run with `review`. `docs/plans/oneshot/12-review-converges-on-measurement.md`
adds what makes the rounds converge: the builder's answers reach the next review, which accepts or rebuts
them; an area the builder keeps answering, or one refused again after its one replacement, goes to the
owner (`conflict`); a finding whose fix belongs to a kind's later build waits for it (`later`); and the
review reads the real measurement the owner approved. Nothing here pushes.
"""

from __future__ import annotations

import json
import posixpath
import re
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent import read_tools

from . import oneshot, oneshot_build, oneshot_desktop, oneshot_front, oneshot_spec, runtime, specs, work  # noqa: F401 — `oneshot_front` registers `split`
from .oneshot_profile import environment, kinds_of, shape
from .query import ROOT, _lock as work_lock, hold

SEVERITIES = ("P0", "P1", "P2")
DEVICE_KINDS = ("android", "desktop")   # the kinds whose S7 runs the app on a device the host's tests cannot
# Where a kind's deferred check reads its evidence (`docs/plans/oneshot/9-kinds.md`, "Owed checks for
# `desktop`"): a PC app's `look` reads UI Automation in the owned session, its `owner` needs the owner's screen.
SOURCES = {"desktop": ("look", "owner")}
ROUNDS_PROMPT = (ROOT / "tool/prompts/oneshot-review-rounds.md").read_text(encoding="utf-8")
KEEPS = {"review": "리뷰어 쪽", "build": "빌더 쪽", "owner": "주인이 직접 정함"}
MAX_NOTE = 2_000
router = APIRouter()


def cap() -> int:
    """Refusals before the run stops: the Review Loop's round cap, so the review and its repair loop as a PR's do."""

    from . import loop   # it imports `specs`, which imports this module's neighbours

    return loop.settings()["rounds"]


def reviewer() -> dict:
    """The model the Review Loop settings name: an independent reader, not the builder's role."""

    from . import loop   # it imports `specs`, which imports this module's neighbours

    return {"model": loop.review_model(), "effort": loop.settings()["review_effort"]}


oneshot.ROLES["review"] = reviewer


def begin(repo: str) -> None:
    """After the build passed: one review worker; a start refused by a shutdown leaves it resumable. A run held
    at or before the review by its kind's stop starts none: its stop is put back."""

    if oneshot_build.stopped_for_kind(repo):
        return
    try:
        oneshot.start_worker(oneshot_spec.store(repo), "review", review, (repo,), {})
    except HTTPException:
        oneshot_spec.store(repo).stop("restart", "서버가 꺼지는 중이라 백엔드 리뷰를 시작하지 못했다")


class MovedError(ValueError):
    """The worktree is no longer the HEAD the build passed."""


@contextmanager
def seen_at(spec: dict, store):
    """`built_head` checked out detached, hooks off, for the block: the worktree's changes never reach the reviewer."""

    tree, seen = Path(spec["worktree"]), store.folder / "seen"   # Git makes the folders; a leftover goes first
    off = ["git", "-c", f"core.hooksPath={store.folder / 'no-hooks'}", "worktree"]
    gone = lambda: (specs.sh([*off, "remove", "--force", str(seen)], tree, 120), shutil.rmtree(seen, True),   # noqa: E731
                    specs.sh([*off, "prune"], tree, 60))
    gone()
    try:   # an add that fails or times out after registering the checkout is removed as well
        if (done := specs.sh([*off, "add", "--detach", str(seen), spec["oneshot"]["built_head"]], tree, 300)).returncode:
            raise ValueError(f"리뷰할 HEAD 를 따로 꺼내지 못했다 — {specs.said(done)}")
        yield seen
    finally:
        gone()


def frozen(spec: dict, seen: Path) -> tuple[str, str, int | None]:
    """`(head, how to read the diff, lines it adds)` for the approved `built_head`: the diff from the base commit,
    written for `pr_diff` in the reviewer's checkout `seen` and kept as the record. A moved tree is `MovedError`."""

    tree, store, head = Path(spec["worktree"]), oneshot_spec.store(spec["repo"]), spec["oneshot"]["built_head"]
    if why := moved(spec, tree):
        raise MovedError(why)
    done = specs.sh(["git", "diff", "--no-color", spec["start_head"], head], tree, 120)
    if done.returncode:
        raise ValueError(f"리뷰할 diff 를 읽지 못했다 — {(done.stderr or '').strip()[-300:]}")
    (store.folder / f"review-{head[:12]}.diff").write_text(done.stdout, encoding="utf-8", newline="\n")
    read_tools.save_diff(seen, done.stdout)
    how = ("read it with the `pr_diff` tool" if oneshot.codex_role("review", store) else
           f"read it from `{read_tools.diff_file(seen).resolve()}`; a line starting with ~ continues the line before it")
    try:
        counted = specs.sh(["git", "diff", "--numstat", spec["start_head"], head], tree, 120)
    except subprocess.TimeoutExpired:   # the count is for the card; the frozen patch is what the review reads
        counted = subprocess.CompletedProcess([], 1)
    added = None if counted.returncode else \
        sum(int(n) for n, *_ in (line.split("\t") for line in counted.stdout.splitlines()) if n.isdigit())
    return head, how, added   # `None`: Git could not count, and the card says it does not know


def review_text(spec: dict, head: str, how: str, rules: dict | None = None) -> str:
    o = spec["oneshot"]
    built = "core" if "modules" in o["design"] else "backend"
    return (f"Review HEAD `{head}` of this repository's {built} build.\n\nThe approved design:\n\n"
            f"{json.dumps(o['design'], ensure_ascii=False)}\n\nThe requirements:\n\n"
            f"{json.dumps(spec.get('requirements') or [], ensure_ascii=False)}\n\nThe environment profile and the stack:\n\n"
            f"{environment(o)}\n\nThe pinned contract tests, unchanged since "
            f"`{o['tests_head']}`: {', '.join(o['contract'])}.\n\nThe whole diff from the base commit "
            f"`{spec['start_head']}` to HEAD: {how}.\n" + measured_text(o, head) + earlier_text(o.get("reviews") or [])
            + answers_text(o) + rulings_text(o) + "\n" + ROUNDS_PROMPT.replace("{kinds}", ", ".join(kinds_of(o)))
            + rules_text(rules or {}))


def measured_text(o: dict, head: str) -> str:
    """The real measurement of this HEAD the owner approved (`oneshot_measure`), as the review reads it."""

    m = next((m for m in reversed(o.get("measures") or [])
              if m.get("head") == head and (m.get("owner") or {}).get("approve")), None)
    if not m:
        return ""
    shown = {"metrics": m.get("metrics"), "notes": m.get("notes", ""), "owner": m["owner"].get("note", "")}
    return ("\nThe owner ran this HEAD against the real services and approved the measurement: each metric with "
            "its threshold and whether it passed, the measuring script's notes, and what the owner wrote after "
            f"listening to the clips.\n\n```json\n{json.dumps(shown, ensure_ascii=False)}\n```\n")


def answers_text(o: dict) -> str:
    """What the builder answered with evidence instead of a change, round by round: the reviewer's memory of
    the other side, so an answered finding is rebutted or left out, never repeated in new words."""

    if not (given := o.get("answers")):
        return ""
    return ("\nThe builder's answers to earlier refusals, each with the requirement a change would break and the "
            f"test it committed to pin the behaviour it defends:\n\n```json\n{json.dumps(given, ensure_ascii=False)}\n```\n")


def rulings_text(o: dict) -> str:
    if not (rulings := o.get("rulings")):
        return ""
    lines = [f"- `{r.get('area') or a}`: {KEEPS[r['keep']]}" + (f" — {r['note']}" if r.get("note") else "")
             for a, r in rulings.items()]
    return ("\nThe owner ruled on these areas, where the builder and the review could not both be met. The "
            "ruling is part of the approved design:\n\n" + "\n".join(lines) + "\n")


def anchors(spec: dict) -> set[str]:
    """The ids a P1 may say it breaks: the design's items and the requirements they depend on, through the
    requirements those depend on. A requirement nothing of the design reaches is not this build's to meet."""

    items = [it for v in spec["oneshot"]["design"].values() if isinstance(v, list) for it in v if isinstance(it, dict)]
    under = {str(r["id"]): [str(d) for d in r.get("depends_on") or []] for r in spec.get("requirements") or []}
    reached, todo = set(), [str(d) for it in items for d in it.get("depends_on") or [] if str(d).startswith("R")]
    while todo:
        if (r := todo.pop()) not in reached:
            reached.add(r)
            todo += under.get(r, [])
    # `CAP` is the approved capability list itself: a permission no capability names breaks it, though no
    # item of the design does. Only a kind with platform permissions has one.
    platform = {"CAP"} if set(kinds_of(spec["oneshot"])) & set(DEVICE_KINDS) else set()
    return {str(it["id"]) for it in items if it.get("id")} | reached | platform


def waits(f: dict) -> bool:
    """A P0 or P1 finding that blocks nothing now: one only a device can show (`device`), or one whose fix lives
    in a kind's own later build (`later`)."""

    return "device" in f or "later" in f


def blocking(found: dict) -> list[dict]:
    return [f for f in found["findings"] if f["severity"] != "P2" and not waits(f)]


def narrowing(reviews: list[dict]) -> bool:
    """The last two reviews refused, the later with no fewer blocking findings: the repairs are not converging,
    so the next round reads what changed since rather than finding new cases in code it already read."""

    last = reviews[-2:]
    return len(last) == 2 and all(r["verdict"] == "refuse" for r in last) and len(blocking(last[1])) >= len(blocking(last[0]))


def changed_lines(tree: Path, since: str, head: str) -> dict[str, list[tuple[int, int]]] | None:
    """Each file's lines `since..head` changed, as inclusive ranges on HEAD's side; `None` when Git cannot say."""

    try:
        done = specs.sh(["git", "-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff", "-U0",
                         "--src-prefix=a/", "--dst-prefix=b/", since, head], tree, 120)
    except subprocess.TimeoutExpired:   # the narrowing is optional: the frozen patch is read whole instead
        return None
    if done.returncode:
        return None
    ranges, path, header = {}, None, False
    for line in done.stdout.splitlines():
        if line.startswith("diff --git "):   # a file's headers until its first hunk: never a content line
            path, header = None, True
        elif header and line.startswith("+++ "):
            name = line[4:].rstrip("\t")   # Git ends a path holding a space with a tab
            if name.startswith('"') and name.endswith('"'):
                name = unquoted(name[1:-1])
            path = name[2:] if name.startswith("b/") else None   # `/dev/null`: the file is gone
        elif line.startswith("@@") and (m := re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)):
            header, start, count = False, int(m[1]), int(m[2] or 1)
            if path:   # a deletion (`count` 0) after line `start` touches it and the line that follows
                ranges.setdefault(path, []).append((max(start, 1), start + count - 1) if count else
                                                    (max(start, 1), start + 1))
    return ranges


ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def unquoted(text: str) -> str:
    """A path Git quoted: its C escapes and octal bytes read back to bytes, literal UTF-8 kept, then decoded."""

    out, i = bytearray(), 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            if text[i + 1] in ESCAPES:
                out.append(ESCAPES[text[i + 1]])
                i += 2
                continue
            if text[i + 1:i + 4].isdigit() and len(text[i + 1:i + 4]) == 3:
                out.append(int(text[i + 1:i + 4], 8) & 0xFF)
                i += 4
                continue
        out += text[i].encode("utf-8")
        i += 1
    return out.decode("utf-8", "replace")


def touched(location: str, changed: dict[str, list[tuple[int, int]]]) -> bool:
    m = re.match(r"\s*`?(.+?):(\d+)", location)
    return bool(m) and any(a <= int(m[2]) <= b for a, b in changed.get(canonical(m[1]), []))


def canonical(path: str) -> str:
    """A worktree-relative path in one spelling: `./a\\b/../c` is `a/c`, as Git writes it."""

    return posixpath.normpath(path.strip().replace("\\", "/"))


def ruled(spec: dict, tree: Path, head: str) -> dict:
    """What this round's verdict must meet, adapted to the rounds before it (`verdict_of`): the ids a P1 names,
    the areas refused twice that now need `repro`, and, while the repairs do not converge, the lines changed
    since the last review that a new area's P1 must point at. A P0 is never held back."""

    o = spec["oneshot"]
    reviews = o.get("reviews") or []
    rounds: dict[str, set[int]] = {}
    for f in refusals(reviews):
        rounds.setdefault(area_of(f), set()).add(f["round"])
    rounds.pop("", None)
    changed = changed_lines(tree, reviews[-1]["head"], head) if narrowing(reviews) else None
    # The answers to the last refusal: each is met by a rebuttal, or its finding is left out.
    answered = {area_of(a) for a in o.get("answers") or [] if a.get("round") == len(reviews)} - {""}
    ruled = {a for a, r in (o.get("rulings") or {}).items() if r.get("keep") == "build"}
    return {"anchors": anchors(spec), "known": set(rounds), "repeated": {a for a, n in rounds.items() if len(n) >= 2},
            "changed": changed, "since": reviews[-1]["head"] if changed is not None else None,
            "answered": answered, "ruled": ruled, "kinds": set(kinds_of(o))}


def rules_text(rules: dict) -> str:
    said = ""
    if rules.get("anchors"):
        said += f"\nThe ids a P1's `breaks` may name: {', '.join(sorted(rules['anchors']))}.\n"
    if rules.get("repeated"):
        said += ("\nThese areas were refused in two rounds or more: " + ", ".join(f"`{a}`" for a in sorted(rules["repeated"]))
                 + ". A P0 or P1 finding in one of them carries `repro`: the ordinary inputs or actions, in order, and "
                 "the wrong result they give, so the builder can turn it into a failing test.\n")
    if rules.get("changed") is not None:
        spans = "; ".join(f"{p}: " + ", ".join(f"{a}-{b}" for a, b in r) for p, r in sorted(rules["changed"].items()))
        said += (f"\nThe repairs are not converging: the last review found no fewer blocking findings than the one "
                 f"before. This round reads what changed since `{rules['since']}` first. A P1 in an area no earlier "
                 "refusal names must point at a line changed since then, or carry a `repro` that shows it on this "
                 "HEAD; one with neither is P2, unless it is a P0. "
                 f"The changed lines: {spans or 'none'}.\n")
    return said


STUCK = 3   # refusals of one area after which the build replaces its mechanism instead of patching the next case


def area_of(f: dict) -> str:
    return " ".join(str(f.get("area") or "").split()).casefold()


def refusals(reviews: list[dict]) -> list[dict]:
    """Every blocking finding of the refusals, oldest first, numbered by round."""

    return [{"round": n, **({"area": f["area"]} if area_of(f) else {}),   # a record from before areas has none
             **{k: f[k] for k in ("severity", "location", "reason")}}
            for n, r in enumerate(reviews, 1) if r["verdict"] == "refuse"
            for f in r["findings"] if f["severity"] != "P2" and not waits(f)]


def earlier_text(reviews: list[dict]) -> str:
    """The reviewer's memory: each earlier refusal, so a fixed one is checked, not found again in new words."""

    past = refusals(reviews)
    return (f"\nEarlier refusals of this build, oldest first; the builder has tried to fix each since:\n\n```json\n"
            f"{json.dumps(past, ensure_ascii=False)}\n```\n\nCheck each against this HEAD first. Leave out one the "
            "code now fixes; one that still holds, or that a fix moved elsewhere in the same mechanism, keeps that "
            "earlier finding's `area`, word for word; one listed without an `area` gets one of yours, the same "
            "for every finding of its mechanism.\n") if past else ""


def deferrable(spec: dict) -> dict[str, set[str]]:
    """Each kind of the run that runs on a device, with the requirements that apply to it: where a finding
    only that device can show may wait (`docs/plans/oneshot/9-kinds.md`, "The backend review and what only a
    device can show"). A web app has none."""

    kinds = kinds_of(spec["oneshot"])
    return {k: {r["id"] for r in spec.get("requirements") or [] if k in (r.get("kinds") or kinds)}
            for k in kinds if k in DEVICE_KINDS}


def deferred(f: dict, devices: dict[str, set[str]]) -> bool:
    """`f`'s `device` names a P0 or P1 finding's kind with a device, a requirement of that kind, a check, and,
    for a kind with `SOURCES`, where that check reads its evidence; a kind without them names none."""

    d = f["device"]
    return f["severity"] != "P2" and isinstance(d, dict) and d.get("kind") in devices \
        and d.get("requirement") in devices[d["kind"]] and isinstance(d.get("check"), str) and bool(d["check"].strip()) \
        and (d.get("source") in SOURCES[d["kind"]] if d["kind"] in SOURCES else "source" not in d) \
        and (not oneshot_desktop.postcondition(d.get("expect")) and not oneshot_desktop.action(d.get("act"))
             and "observe" not in d if d.get("source") == "look" else
             not oneshot_desktop.action(d.get("act")) and not oneshot_desktop.observed(d.get("observe"))
             and "expect" not in d if d.get("source") == "owner" else not {"expect", "act", "observe"} & set(d))


def verdict_of(value, head: str, devices: dict[str, set[str]] | None = None, rules: dict | None = None) -> dict:
    """An `oneshot-review` block, checked: a verdict on the HEAD read, findings that justify it, and `rules`
    (`ruled`) met. A P0 or P1 finding marked `device` waits for that kind's device (`devices`, from
    `deferrable`) and blocks nothing."""

    if not isinstance(value, dict) or value.get("verdict") not in ("approve", "refuse"):
        raise ValueError("`oneshot-review` 는 `verdict` 가 approve 나 refuse 인 객체다")
    if value.get("head") != head:
        raise ValueError(f"판정은 읽은 HEAD `{head}` 를 그대로 적어야 한다 — 받은 값 `{value.get('head')}`")
    findings = value.get("findings") or []
    if not isinstance(findings, list) or not all(
            isinstance(f, dict) and f.get("severity") in SEVERITIES
            and all(isinstance(f.get(k), str) and f[k].strip() for k in ("location", "reason", "evidence"))
            for f in findings):
        raise ValueError("`findings` 마다 `location`, `severity`(P0·P1·P2), `reason`, `evidence` 가 있어야 한다")
    if any("device" in f and "later" in f for f in findings):
        raise ValueError("발견 하나에 `device` 와 `later` 를 함께 붙이지 않는다")
    if any("later" in f and not later_ok(f, (rules or {}).get("kinds")) for f in findings):
        raise ValueError("`later` 는 P1 발견에만 붙고, 그 수정이 들어갈 이 실행의 종류 `kind` 와 그 종류의 빌드에서 할 일 "
                         "`what` 을 적는다 — 이 빌드의 코드로 고칠 수 있는 것은 미루지 못한다")
    if any("device" in f and not deferred(f, devices or {}) for f in findings):
        raise ValueError("`device` 는 P0·P1 발견에만 붙고, 기기가 있는 종류(" + (", ".join(devices or {}) or "이 실행엔 없다")
                         + "), 그 종류에 걸린 요구, 기기에서 볼 `check` 를 적는다 — PC 앱(`desktop`)은 `source` 로 "
                         "`look`(UI Automation 상태) 이나 `owner`(주인 화면에서만 보이는 캡처·합성) 를 적고, 둘 다 "
                         "아니면 미루지 못한다. `look` 은 결과를 낼 동작 `act`(action, 컨트롤의 `AutomationId` id, "
                         "set_value 의 value) 와 결과 `expect`(요소의 id 에 name, value, toggle 중 하나 이상) 를 적고, "
                         "주 창이 가진 창이면 둘에 `in: owned` 를 단다. `owner` 는 캡처를 시작할 동작 `act` 와 볼 것 "
                         "`observe`(앱이 캡처한 것을 내놓는 `report` 의 요소 id 나 데이터 폴더의 file 과 캡처하는 곳 from, 또는 캡처에 "
                         "보여야 할 것 `shows` 를 단 `window` 나 `composition`) 를 적는다. 안드로이드는 `source` 를 적지 않는다")
    serious = [f for f in findings if f["severity"] != "P2" and not waits(f)]
    blocking_all = [f for f in findings if f["severity"] != "P2"]   # one handed to a later build or a device too:
    if not all(area_of(f) for f in blocking_all):                   # the owner's rulings match it by its area
        raise ValueError("P0·P1 발견마다 그것이 걸린 동작 방식의 이름 `area` 를 적는다")
    for f in blocking_all if rules else ():
        held(f, rules)
    if value["verdict"] == "approve" and serious:
        raise ValueError("P0·P1 발견이 있는데 승인했다")
    if value["verdict"] == "refuse" and not serious:
        raise ValueError("거부에는 P0·P1 발견이 하나 이상 있어야 한다")
    return {"verdict": value["verdict"], "head": head,
            "findings": [{**{k: f[k] for k in ("location", "severity", "reason", "evidence")},
                          **({"area": " ".join(str(f["area"]).split())} if area_of(f) else {}),
                          **({k: f[k] for k in ("breaks", "repro", "rebuttal") if f.get(k)}),
                          **({"later": {k: str(f["later"][k]).strip() for k in ("kind", "what")}} if "later" in f else {}),
                          **({"device": {k: f["device"][k] for k in ("kind", "requirement", "check", "source", "act", "expect", "observe")
                                         if k in f["device"]}}
                             if "device" in f else {})} for f in findings]}


def later_ok(f: dict, kinds) -> bool:
    later = f["later"]
    return f["severity"] == "P1" and isinstance(later, dict) and later.get("kind") in (kinds or set()) \
        and isinstance(later.get("what"), str) and bool(later["what"].strip())


def held(f: dict, rules: dict) -> None:
    """`ValueError` when a blocking finding `f` does not meet this round's `rules`; the reviewer is asked again."""

    if area_of(f) in rules.get("ruled", ()) and f["severity"] == "P1":
        raise ValueError(f"주인이 `{f['area']}` 를 빌더 쪽으로 정했다 — 그 결정에 맞서는 발견은 P0 이 아니면 P2 로 적는다")
    rebuttal = f.get("rebuttal")
    if rebuttal is not None and not (isinstance(rebuttal, str) and rebuttal.strip()):
        raise ValueError("`rebuttal` 은 빌더의 증거가 왜 맞지 않는지 적은 비어 있지 않은 글이다")
    if area_of(f) in rules.get("answered", ()) and not rebuttal:
        raise ValueError(f"빌더가 증거로 답한 영역 `{f['area']}` 의 발견은 `rebuttal` 에 그 증거가 왜 맞지 않는지 읽은 줄이나 "
                         "실측을 들어 적는다 — 증거가 맞으면 그 발견은 뺀다")
    allowed, breaks = rules.get("anchors") or set(), f.get("breaks")
    if breaks is not None and not (isinstance(breaks, list) and all(isinstance(b, str) and b in allowed for b in breaks)):
        raise ValueError(f"`breaks` 는 입력에 적힌 설계 항목·요구 번호의 목록이다 — 받은 값 {breaks}")
    if allowed and f["severity"] == "P1" and not breaks:
        raise ValueError("P1 발견마다 그것이 깨뜨리는 설계 항목·요구 번호를 `breaks` 에 적는다 — 아무것도 "
                         "깨뜨리지 않으면 P2 다")
    repro = f.get("repro")
    if repro is not None and not (isinstance(repro, str) and repro.strip()):
        raise ValueError("`repro` 는 평범한 입력과 순서, 그 결과를 적은 비어 있지 않은 글이다")
    if area_of(f) in rules.get("repeated", ()) and not repro:
        raise ValueError(f"두 회차 넘게 거부된 영역 `{f['area']}` 의 발견은 `repro` 에 평범한 입력과 순서, 그 결과를 적는다")
    if rules.get("changed") is not None and f["severity"] == "P1" and area_of(f) not in rules.get("known", ()) \
            and not touched(f["location"], rules["changed"]) and not (breaks and repro):
        raise ValueError(f"수렴하지 않는 중이라, 처음 나온 영역의 P1 은 `{str(rules['since'])[:12]}` 뒤 바뀐 줄을 "
                         f"가리키거나, 이 HEAD 에서 재현하는 `repro` 를 적는다 — {f['location']} 는 바뀐 줄이 아니다")


def owing(found: dict) -> list[dict]:
    """The approving review's deferred findings, numbered `O<n>`: the spec's `owed` list, each a check its
    kind's S7 loop must pass on the device."""

    return [{"id": f"O{n}", **f["device"], **{k: f[k] for k in ("location", "severity", "reason", "evidence")},
             "head": found["head"]} for n, f in enumerate((f for f in found["findings"] if "device" in f), 1)]


def review(repo: str) -> None:
    def body(store, spec, tree):
        if not measured_text(spec["oneshot"], spec["oneshot"].get("built_head")):
            # No review without the owner's approval of this HEAD's real measurement (`oneshot_measure`): a run
            # that reached the review another way, one from before the measurement included, is measured first.
            oneshot_spec.saved(repo, oneshot={"stage": "measure", "attempts": 0})
            store.stop("measure", "이 빌드의 진짜 환경 실측을 주인이 아직 승인하지 않았다 — 실측을 마치고 승인하면 리뷰한다")
            return None
        found = verdict(store, spec, tree)
        return None if found is None else lambda: decided(repo, store, spec, found)

    holding(repo, "백엔드 리뷰", body)


def holding(repo: str, what: str, body, passed: str = "built_head", build: str = "build", again=None) -> None:
    """A stage that reads the worktree holds it, as a work turn does, so no other turn writes the tree it
    reads. `body(store, spec, tree)` runs on the HEAD the build passed (`passed` names it; a moved tree
    goes back to the `build` stage), on a clean tree, and returns the
    callable that saves its result, or `None` after stopping; the tree is checked again before that is
    called. A moved tree goes back to the build's checks, through `again(repo, store, why, build)` when
    the stage decides that against a stop itself. A callable the save returns runs once the tree
    is let go: the refusal's build turn, or the next stage's worker, which then holds the tree itself
    (it returns true). Otherwise the instruction the person queued for this worktree meanwhile goes, as after any turn."""

    store = oneshot_spec.store(repo)
    spec = oneshot_spec.spec_of(repo)
    tree = Path(spec["worktree"])
    try:
        release = hold(work._busy, work_lock, str(tree), "작업트리를 다른 턴이 쓰고 있다", kind="turn")
    except HTTPException as exc:   # whoever holds it sends the queued instruction when it lets go
        store.stop("block", f"{exc.detail} — 끝난 뒤 [재개] 로 {what}을 다시 한다")
        return
    passed_on = False
    try:
        following = None
        try:
            if why := moved(spec, tree, passed):
                (again or rebuild)(repo, store, why, build)
                return
            save = body(store, spec, tree)
            if save is None:
                return
            if why := moved(spec, tree, passed):
                (again or rebuild)(repo, store, f"{what} 동안 작업트리가 바뀌었다 — {why}", build)
                return
            following = save()
        finally:
            release()
        if callable(following):   # a save that only saves returns the spec
            passed_on = bool(following())
    finally:   # an exception included: the worker records the stop, and the instruction still goes
        if not passed_on:   # a server going down drops it, as `dispatch` drops one after a stopped turn
            work.dispatch(tree, SimpleNamespace(halt=runtime.stopping, turn=""))


def approval(repo: str, what: str, then, passed: str = "built_head", build: str = "build") -> None:
    """An owner's approval holds the worktree as a turn does, on the HEAD the build passed (`passed`): a
    tree another turn holds refuses it (409, the decisions kept), and a moved one goes back to the `build`
    stage's checks before anything is written. `then(store, spec, tree)` returns true when what follows
    sends the instruction queued for the worktree itself."""

    spec, store = oneshot_spec.spec_of(repo), oneshot_spec.store(repo)
    tree = oneshot_spec.own_tree(spec)
    if not tree:
        store.stop("folder", f"작업트리가 `{oneshot.BRANCH}` 브랜치의 그 작업트리가 아니다: {spec['worktree']}")
        return
    release = hold(work._busy, work_lock, str(tree), "작업트리를 다른 턴이 쓰고 있다 — 끝난 뒤 다시 승인하라",
                   kind="turn")
    passed_on = False
    try:
        if why := moved(spec, tree, passed):
            rebuild(repo, store, f"{what} 전에 작업트리가 바뀌었다 — {why}", build)
        else:
            passed_on = bool(then(store, spec, tree))
    finally:
        release()
        if not passed_on:
            work.dispatch(tree, SimpleNamespace(halt=runtime.stopping, turn=""))


def moved(spec: dict, tree: Path, passed: str = "built_head") -> str:
    """Empty when the worktree is the HEAD the build passed (`passed` names it), with nothing uncommitted,
    or how it is not."""

    built = spec["oneshot"].get(passed)
    head = specs.sh(["git", "rev-parse", "HEAD"], tree).stdout.strip()
    if head != built:
        return f"HEAD `{head[:12]}` 가 빌드를 통과한 `{str(built)[:12]}` 가 아니다"
    return "작업트리에 커밋하지 않은 변경이 있다" if specs.dirty(tree) else ""


def decided(repo: str, store, spec: dict, found: dict):
    """The verdict applied; what follows once the tree is free: the split's worker, or the refusal's build turn."""

    o = spec["oneshot"]
    reviews = [*(o.get("reviews") or []), {**found, "ts": time.time()}]
    if found["verdict"] == "approve":
        # `working` names the split before its worker starts: a server that dies in between leaves it for `recover`.
        oneshot_spec.saved(repo, oneshot={"stage": "split", "reviews": reviews, "owed": owing(found),
                                          "later": later_of(o, found), "working": "split"})
        return lambda: oneshot_spec.handoff(repo, "split") or True   # the split holds the tree itself
    refused = int(o.get("refused") or 0) + 1
    if conflicts := conflicted(o, found):   # a decision only the owner can make: no build turn, no round counted
        oneshot_spec.saved(repo, oneshot={"stage": "conflict", "reviews": reviews, "refused": refused - 1,
                                          "conflicts": conflicts, "attempts": 0, "working": None,
                                          "later": later_of(o, found)})
        return None
    if refused >= cap():
        detail = f"백엔드 리뷰가 {refused}번 거부했다 — " + \
            " / ".join(f"[{f['severity']}] {f['location']} {f['reason']}" for f in found["findings"][:5])
        with oneshot._lock:   # one write: the stop names the HEAD it refused at the cap (`capped`), so a server
            # dying in between never leaves a refusal behind a `restart`, and a later cap change moves nothing
            store.put(reviews=reviews, refused=refused, working=None, later=later_of(o, found), stopped={
                "reason": "review", "detail": detail, "state": store.get()[store.state], "capped": found["head"]})
        return None
    # `working` names the build before it is sent: a server that dies in between leaves it for `recover`.
    return to_build(repo, o, reviews, found, refused=refused)


def to_build(repo: str, o: dict, reviews: list[dict], found: dict, **fields):
    """The refusal's build turn, sent once the tree is free, in one write with `fields`. An area refused `STUCK`
    rounds gets its mechanism replaced in this turn; once a build passes, a later refusal of it goes to the owner."""

    spec = oneshot_spec.saved(repo, oneshot={"stage": "build", "reviews": reviews, "attempts": 0, "working": "build",
                                             "fix": "review", "later": later_of(o, found), **fields})
    text = fixing(spec, found)
    replacing(repo, spec["oneshot"], found)
    return lambda: oneshot_build.send(repo, "build", text)


def replacing(repo: str, o: dict, found: dict) -> None:
    """The areas the build turn for `found`, the last of `o`'s reviews, is told to replace, on either path (under
    the cap, or `[재개]` after it): replaced once a build passes. `send` records `refusal_due` as what it may answer."""

    serious = [f for f in blocking(found) if not ruled_out(o, f)]
    new = stuck_areas((o.get("reviews") or [])[:-1], serious, o.get("replaced") or [])
    sent = {"head": found["head"], "areas": sorted({area_of(f) for f in serious})}
    if new != (o.get("replacing") or []) or sent != o.get("refusal_due"):
        oneshot_spec.saved(repo, oneshot={"replacing": new or None, "refusal_due": sent})


def later_of(o: dict, found: dict) -> list[dict]:
    """The work left to a kind's own later build: what earlier verdicts left there, and this one's findings that
    carry `later`, each once; that build's turn reads them all."""

    out = list(o.get("later") or [])
    for f in found["findings"]:
        item = {"kind": f["later"]["kind"], "what": f["later"]["what"], "location": f["location"], "reason": f["reason"]} \
            if "later" in f else None
        if item and item not in out:
            out.append(item)
    return out


def later_text(o: dict, kinds: list[str] | None = None) -> str:
    """The work the backend review left to these kinds' builds, appended to that build's turn."""

    items = [x for x in o.get("later") or [] if kinds is None or x["kind"] in kinds]
    if not items:
        return ""
    return ("\n\n## Left to this build by the backend review\n\nThe backend review found that these belong to a "
            "kind's own build rather than the core. Build each one, and say in your answer where:\n\n"
            + "\n".join(f"- ({x['kind']}, `{x['location']}`) {x['what']} — {x['reason']}" for x in items) + "\n")


def ruled_out(o: dict, f: dict) -> bool:
    """`f` is a P1 in an area the owner ruled for the builder: nothing is left to fix there. A P0 still is."""

    return f["severity"] == "P1" and (o.get("rulings") or {}).get(area_of(f), {}).get("keep") == "build"


def conflicted(o: dict, found: dict) -> list[dict]:
    """The areas of this refusal only the owner can settle: one refused again after the build replaced its
    mechanism once, or one the builder answered in two rounds with a requirement a change would break. An
    area the owner ruled on is never asked again."""

    replaced, answers, rulings = set(o.get("replaced") or []), o.get("answers") or [], o.get("rulings") or {}
    out: list[dict] = []
    for f in blocking(found):
        a = area_of(f)
        if not a or a in rulings or any(c["key"] == a for c in out):
            continue
        rounds = {x["round"] for x in answers if area_of(x) == a and x.get("breaks")}
        why = "replaced" if a in replaced else "answered" if len(rounds) >= 2 else ""
        if why:
            out.append({"key": a, "area": f["area"], "why": why,
                        "findings": [{k: g[k] for k in ("location", "severity", "reason", "evidence", "breaks") if k in g}
                                     for g in blocking(found) if area_of(g) == a],
                        "answers": [x for x in answers if area_of(x) == a][-3:]})
    return out


def stuck_areas(past_reviews: list[dict], serious: list[dict], replaced) -> list[str]:
    """The areas of `serious` refused `STUCK` rounds or more, this one included, whose mechanism the build has
    not replaced yet. Each is replaced once; one refused after that goes to the owner (`conflicted`)."""

    areas = {area_of(f) for f in serious} - {""} - set(replaced)
    past = [f for f in refusals(past_reviews) if area_of(f) in areas]
    rounds = {a: 1 + len({f["round"] for f in past if area_of(f) == a}) for a in areas}
    return sorted(a for a, n in rounds.items() if n >= STUCK)


def verdict(store, spec: dict, tree: Path) -> dict | None:
    """The checked verdict on the frozen HEAD, or `None` after stopping."""

    try:
        with seen_at(spec, store) as seen:   # the reviewer reads its own checkout of the approved HEAD
            head, how, added = frozen(spec, seen)
            rules = ruled(spec, tree, head)
            found = oneshot.turned(store, "review", oneshot.prompt("review", shape(spec["oneshot"])),
                                   review_text(spec, head, how, rules), "oneshot-review",
                                   lambda value: verdict_of(value, head, deferrable(spec), rules), seen)
    except MovedError as exc:   # the approved HEAD is no longer the tree: the build checks it again
        rebuild(spec["repo"], store, f"리뷰 직전에 작업트리가 바뀌었다 — {exc}")
        return None
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        store.stop("review", str(exc))
        return None
    return None if found is None else {**found, **({"added": added} if added is not None else {}),
                                       "focus": "narrow" if rules["changed"] is not None else "full"}


def rebuild(repo: str, store, why: str, build: str = "build") -> None:
    """The tree is not what the build passed: `[재개]` sends the `build` stage's turn, whose checks run again."""

    oneshot_spec.saved(repo, oneshot={"stage": build, "attempts": 0})
    store.stop("gate", f"{why} — [재개] 로 빌드 검사부터 다시 한다")


def fixing(spec: dict, found: dict) -> str:
    o = spec["oneshot"]
    # Those waiting for a device or a later build, and those the owner ruled for the builder, ask nothing now.
    serious = [f for f in blocking(found) if not ruled_out(o, f)]
    return (f"An independent read-only review refused HEAD `{found['head']}`. Fix every finding below "
            "where it points, without changing the pinned contract tests. A finding you disagree with gets an "
            "entry in your `oneshot-answers` block instead of a change, naming the test you commit to pin the "
            "behaviour you defend. Either way this turn ends on a new commit: a HEAD the review already refused "
            "is never read again.\n\n```json\n"
            f"{json.dumps(serious, ensure_ascii=False, indent=2)}\n```\n\n"
            + rulings_text(o) + recurring(o.get("reviews") or [], found, serious, o.get("replaced") or [])
            + oneshot_build.build_text(spec))


def recurring(reviews: list[dict], found: dict, serious: list[dict], replaced=()) -> str:
    """The earlier refusals in the areas this one names; an area refused `STUCK` times or more, and not yet
    replaced (`replaced`), gets its mechanism replaced by the same build turn, once."""

    areas = {area_of(f) for f in serious} - {""}
    past = [f for f in refusals(reviews[:-1]) if area_of(f) in areas]   # the last review is `found` itself
    if not past:
        return ""
    rounds = {a: 1 + len({f["round"] for f in past if area_of(f) == a}) for a in areas}
    stuck = sorted(a for a, n in rounds.items() if n >= STUCK and a not in set(replaced))
    said = (f"Earlier refusals in the same areas, which earlier fixes did not end:\n\n```json\n"
            f"{json.dumps(past, ensure_ascii=False, indent=2)}\n```\n\n")
    if stuck:
        said += ("The review has refused " + ", ".join(f"`{a}` {rounds[a]} times" for a in stuck) + ": each fix of one "
                 "case has led to the next. Do not add one more special case. Read that area's findings together, "
                 "name the assumption they share, and replace the mechanism with one that meets the requirements "
                 "without it, removing the special cases the old one gathered; then check every finding of the "
                 "area, earlier ones included, against the new mechanism. Say in your answer what you replaced "
                 "and why.\n\n")
    return said


def build_turn(spec: dict) -> str:
    """A resumed build's turn: what sent the build back, again, while it stands: the real measurement's failure
    (`oneshot_measure.fixing`) or the review's refusal of the built HEAD."""

    o = spec["oneshot"]
    if o.get("fix") == "measure":
        from . import oneshot_measure   # it imports this module
        if text := oneshot_measure.fixing(spec):
            return text
    if not (last := standing(o)):
        return oneshot_build.build_text(spec)
    text = fixing(spec, last)
    replacing(spec["repo"], o, last)
    return text


def standing(o: dict) -> dict | None:
    """The last review, while it is a refusal of the built HEAD."""

    last = (o.get("reviews") or [None])[-1]
    return last if last and last["verdict"] == "refuse" and last["head"] == o.get("built_head") else None


def capped(o: dict) -> bool:
    """Stopped at the cap with the built HEAD refused: `[재개]` repairs it, a second reading would only repeat.
    Read off the stop itself, never today's cap: any other stop of the review reads the HEAD again."""

    last = standing(o)
    return last is not None and (o.get("stopped") or {}).get("capped") == last["head"]


def resume(repo: str) -> None:
    """`[재개]` in `review`, the built HEAD not refused at the cap: the review run again on the current HEAD."""

    oneshot_spec.saved(repo, oneshot={"refused": 0})
    begin(repo)


def limits(spec: dict) -> dict:
    """What the screen counts a stage's tries against: the server's own caps, never a copy on the screen."""

    from . import oneshot_critique   # it imports this module

    return {"limits": {"attempts": oneshot_build.MAX_ATTEMPTS, "refusals": cap(),
                       "critiques": oneshot_critique.MAX_ROUNDS}}


oneshot_spec.VIEWS.append(limits)


class Rulings(BaseModel):
    rulings: dict[str, dict]   # by conflict `key`: `keep` (`review`, `build`, `owner`) and the owner's `note`
    head: str = ""   # the refused HEAD the owner saw the board for; any other is refused with 409


@router.post("/api/oneshot/spec/{repo}/conflict")
def rule(repo: str, body: Rulings) -> dict:
    """The owner settles each area the builder and the review could not both meet. A ruling for the builder
    leaves nothing to fix there and the review may no longer refuse it; one for the review, or the owner's own
    words, goes to the build. When nothing is left to fix, the same HEAD is reviewed again under the rulings."""

    with oneshot._lock:
        spec = oneshot_spec.spec_of(repo)
        o = spec["oneshot"]
        oneshot_spec.idle(o, "conflict")
        reviews, conflicts = o.get("reviews") or [], o.get("conflicts") or []
        if not reviews or body.head != reviews[-1]["head"]:
            raise HTTPException(409, "보드가 그 사이 바뀌었다 — 다시 불러와 새 보드에서 정하라")
        rulings = dict(o.get("rulings") or {})
        for c in conflicts:
            got = body.rulings.get(c["key"]) or {}
            keep, note = got.get("keep"), str(got.get("note") or "").strip()
            if keep not in KEEPS:
                raise HTTPException(400, f"`{c['area']}` 를 아직 정하지 않았다 — 셋 중 하나를 고른다")
            if keep == "owner" and not note:
                raise HTTPException(400, f"`{c['area']}` 를 직접 정하려면 어떻게 할지 적는다")
            if len(note) > MAX_NOTE:
                raise HTTPException(400, f"`{c['area']}` 의 글은 {MAX_NOTE}자까지다")
            rulings[c["key"]] = {"area": c["area"], "keep": keep, "note": note, "round": len(reviews)}
        # One write: the rulings, the cleared board and the next stage, so no stop in between leaves an empty board.
        o, last = {**o, "rulings": rulings}, reviews[-1]
        left = [f for f in blocking(last) if not ruled_out(o, f)]
        if left:
            then = to_build(repo, o, reviews, last, rulings=rulings, conflicts=None)
        else:   # every finding ruled for the builder: the rulings are new input, so the same HEAD is read again
            oneshot_spec.saved(repo, oneshot={"rulings": rulings, "conflicts": None, "stage": "review", "attempts": 0,
                                              "working": "review"})
            then = lambda: begin(repo)   # noqa: E731
    then()
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


oneshot_spec.SHEETS.add("conflict")
oneshot_spec.WAITS.add("conflict")
