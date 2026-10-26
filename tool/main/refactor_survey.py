"""refactor_survey — the read-only survey a purpose-mode refactoring starts with.

`docs/plans/refactor/7-purpose-modes.md` is the contract. A survey is a run
record of kind `survey` that `refactor.drive` walks like the old runs, so its
cancellation, restart recovery and resume are theirs. It reads a detached
linked worktree of its base commit, never the person's checkout: a dirty tree
or a branch switch there cannot change what it read, and a hub survey never
touches the files the server runs from. What it leaves is a record and
ordinary `정리됨` cards; starting one of those is the person's separate act.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from common import worktree_home

from .query import ROOT, config

MODES = ("preserve", "simplify", "rework")
KINDS = ("duplication", "coupling", "mixed_responsibility", "defensive", "test_contract", "performance",
         "naming", "dead_code")
LEVELS = ("high", "medium", "low")
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
# A spec is a change to make. One that only looks, maps or writes the structure down is the survey's own work.
LOOKING = re.compile(r"^\s*(survey|investigate|audit|explore|map|analy[sz]e|document|diagram|study|inspect)\b"
                     r"|조사|구조도|파악", re.I)
# Failure identifiers in the tail of other runners' output: vitest/jest, go test, cargo test. pytest's
# `FAILED`/`ERROR` lines are read whole by `_node`.
FAILED = re.compile(r"^\s*FAIL\s+(\S+)|^--- FAIL: (\S+)|^test (\S+) \.\.\. FAILED", re.M)
# A pytest node id: its path and its parameter id may hold spaces, ` - ` and brackets; its names may not.
# Any collected file (`docs/case1.txt::case1.txt` for a doctest), whose path holds neither `::` nor the
# `: ` of a message (`ImportError: no a.b`); a name holds neither space, `:` nor `[`.
NODE = re.compile(r"[^\s:](?:(?!::|: ).)*?\.\w+(?:::[^\s:\[]+)*(?:\[.*\])?")


def _sections(text: str) -> tuple[str, dict[str, str]]:
    head, *parts = re.split(r"^## (\w+)[ \t]*\r?\n", text, flags=re.M)
    return head.strip(), {parts[i]: parts[i + 1].strip() for i in range(0, len(parts), 2)}


INTRO, RULES = _sections((ROOT / "tool/prompts/refactor-modes.md").read_text(encoding="utf-8"))


def rules(mode: str) -> str:
    """The mode's rules as every survey and run turn reads them."""
    return f"{INTRO}\n\nSelected mode: `{mode}`.\n\n{RULES[mode]}"


MAP_SCHEMA = {"pipelines": [{"id": "<kebab-case id>", "name": "<plain name>", "entry": "<file or symbol it starts at>",
                             "paths": ["<repository-relative files or directories it owns>"],
                             "role": "<what it does, one sentence>"}],
              "shared": [{"path": "<a module several pipelines use>", "used_by": ["<pipeline id>"]}],
              "excluded": [{"path": "<generated, vendored or third-party path>", "reason": "<why>"}],
              "omm": {"used": True, "diverged": ["<where .omm and the code disagree>"]},
              "unchecked": ["<what you could not read or trace, and why>"]}
LEVEL = {"level": "high | medium | low", "why": "<the criterion and the evidence for this level>"}
PIPELINE_SCHEMA = {
    "id": "<the pipeline id>", "what": "<what this feature does, in plain words>",
    "inputs": "<what it takes in>", "outputs": "<what it gives out>",
    "links": [{"pipeline": "<another pipeline id, or external:<system>>", "how": "<how they connect>"}],
    "owns": "<the responsibilities this pipeline should own>",
    "misplaced": [{"what": "<a responsibility it wrongly holds>", "evidence": [{"path": "...", "symbol": "..."}]}],
    "debts": [{"kind": " | ".join(KINDS), "summary": "<the debt>",
               "evidence": [{"path": "<repository-relative file>", "symbol": "<function, class or block>"}],
               "scope": "<a countable scope: call sites, copies, files, callers>",
               "size": LEVEL, "difficulty": LEVEL, "risk": LEVEL, "if_left": "<the concrete problem if left as is>"}],
    "low_debt_reason": "<required when debts is empty: why the debt is low, e.g. clear boundaries, simple "
                       "dependency direction, maintained contract tests>",
    "change": "<what the selected mode would change here and the expected effect, or why nothing>",
    "checked": ["<what you read>"], "unchecked": ["<what you could not read or run, and why>"],
    "proposals": [{"title": "...", "problem": "...", "change": "...", "files": ["..."]}]}
SPEC_SCHEMA = {
    "key": "<kebab-case id of at most 40 characters, unique in this survey>", "title": "<the one improvement>",
    "pipeline": "<one pipeline id, or shared:<path> for a shared boundary change: a path from the map's `shared` "
                "or one this spec `creates`>",
    "files": ["<repository-relative files in the base commit the change touches>"],
    "creates": ["<repository-relative files the change creates, in folders that exist; omit when none>"],
    "boundary": "<the responsibility boundary this spec works inside>",
    "problem": "<the confirmed problem>", "evidence": [{"path": "...", "symbol": "..."}],
    "change": "<exactly what changes>", "keep": ["<behaviour and scope that stay unchanged>"],
    "verify": [{"method": "<how completion is checked>", "criterion": "<what counts as done>"}],
    "depends": ["<keys of specs that must merge first>"],
    "impact": "<effect on prerequisite specs and other pipelines>",
    "risk": LEVEL, "rollback": "<how to undo it>",
    "together": "<only when one spec must change several pipelines' shared code at once: why splitting breaks "
                "the intermediate state>",
    "tests": {"add": ["<new tests to add, never an edit of an existing test>"], "remove": [{"test": "<path or path::name>", "reason": "<exact duplicate, "
              "one-off script or temporary instrumentation>"}],
              "contract": [{"test": "<path or path::name>", "old": "<stale contract>", "new": "<current contract>",
                            "why": "<the current requirement or public contract it follows>"}]},
    "measure": {"metric": "<rework performance only>", "task": "<representative task>",
                "argv": ["<command run from the repository root>"], "repeat": 5},
    "hypothesis": {"claim": "<rework, when it cannot be measured>", "check": "<how it could be verified>"},
    "contract_change": {"impact": "<rework contract change or data migration only>", "compatibility": "...",
                        "migration": "...", "rollback": "..."}}

COMMON = ("Do not edit, create or delete anything; read only. Write in English. Never claim a precise score or "
          "improvement rate you did not measure. What you did not read is unchecked, never absent.")
MAP_PROMPT = (
    "Survey this repository for a refactoring. " + COMMON + " Trace from the entry points: routing, "
    "configuration, dynamic registration (decorators, plugins, reflection, string-keyed tables), scheduled and "
    "background work, external systems, and the tests of each feature. Group the code into pipelines, each one "
    "function from its input through processing to its output. Fold small helpers into the pipeline that owns "
    "them; list a module several pipelines use under `shared`. Exclude generated code and vendored or third-party "
    "libraries, each with its reason. `.omm/` documents, when given, are leads to check against the code, never "
    "the answer: record where they disagree. A string search that finds no reference does not make code dead. "
    "End with one fenced block tagged `refactor-map` holding JSON shaped like:\n" + json.dumps(MAP_SCHEMA))
PIPELINE_PROMPT = (
    "Explain one pipeline of a surveyed repository and report its debt for the selected mode. " + COMMON +
    " Explain in this order and in plain words, not as a file list: what it does; its inputs and outputs; how it "
    "connects to other pipelines; the responsibilities it should own and those it wrongly holds; each debt with "
    "exact file and symbol evidence; how large it is and why; what goes wrong if it is left; what the selected "
    "mode would change and the expected effect. Debt kinds are " + ", ".join(KINDS) + ". Rate size, difficulty "
    "and risk separately, each with its criterion and evidence. Length alone is not debt; short code that mixes "
    "the state of several pipelines can be large debt. Give a countable scope. Low debt needs a reason. "
    "Propose only changes the selected mode owns. End with one fenced block tagged `refactor-pipeline` holding "
    "JSON shaped like:\n" + json.dumps(PIPELINE_SCHEMA))
INTEGRATE_PROMPT = (
    "Turn a repository survey into executable refactoring specs for the selected mode. " + COMMON +
    " The pipeline reports are your evidence: weigh every debt they name for the selected mode, and turn each "
    "one worth doing into a spec, largest payoff first; leave one out only for a reason you state in "
    "`coverage.unchecked`. Missing test coverage is not such a reason: in `preserve` the spec names the "
    "characterization tests to add in `tests.add`, and they are written and frozen before any code moves."
    " Each one is a new test: the run refuses any edit inside an existing test, so a check that would extend "
    "one is a new test beside it."
    " A spec covers one improvement inside one pipeline; never bundle unrelated features. Never write a spec "
    "that only surveys, maps or documents structure. When a shared module change serves several pipelines, make "
    "it its own spec (`pipeline`: `shared:<path>`) that the pipeline specs depend on, unless splitting it would "
    "break the intermediate state; then keep it together and say why in `together`. A prerequisite another mode "
    "owns is not folded in: name it in `impact`. Test removals in `preserve` need an exact-duplicate, one-off or "
    "temporary reason; `contract` updates belong to `simplify`, or to a `rework` spec that is itself a "
    "`contract_change`. `measure` and `hypothesis` belong to `rework` performance specs; a spec with only a "
    "hypothesis cannot run until it can be measured. No spec is a valid answer: then give `nothing` with the "
    "reason. End with one fenced block tagged `refactor-survey` holding JSON shaped like:\n"
    + json.dumps({"coverage": {"scope": "<what the survey covered>", "unchecked": ["..."],
                               "excluded": [{"path": "...", "reason": "..."}]},
                  "specs": [SPEC_SCHEMA], "nothing": "<only with no specs: why>"}))


# -- Shapes ----------------------------------------------------------------------

def _text(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"`{what}` 이 비었다")
    return value.strip()


def _list(value, what: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"`{what}` 는 목록이어야 한다")
    return value


def _texts(value, what: str) -> list[str]:
    return [_text(v, f"{what}[{i}]") for i, v in enumerate(_list(value, what))]


def _at(root: Path, value, what: str) -> str:
    """A repository-relative path that exists in the base commit's tree, written
    as Git writes it: `./a` or `b/../a` would never match a changed file."""
    rel = _text(value, what).replace("\\", "/").strip("/")
    target, top = (root / rel).resolve(), root.resolve()
    if top not in target.parents or target.relative_to(top).parts[0].casefold() == ".git" or not target.exists():
        raise ValueError(f"`{what}`: `{rel}` 이 기준 커밋에 없다")
    return target.relative_to(top).as_posix()


def _new(root: Path, value, what: str) -> str:
    """A repository-relative path a spec creates: absent from the base commit, in a folder that is there."""
    rel = _text(value, what).replace("\\", "/").strip("/")
    target, top = (root / rel).resolve(), root.resolve()
    if top not in target.parents or target.relative_to(top).parts[0].casefold() == ".git":
        raise ValueError(f"`{what}`: `{rel}` 은 저장소 안의 경로가 아니다")
    if target.exists():
        raise ValueError(f"`{what}`: `{rel}` 은 기준 커밋에 이미 있다 — `files` 에 둔다")
    if not target.parent.is_dir():
        raise ValueError(f"`{what}`: `{rel}` 의 폴더가 기준 커밋에 없다")
    return target.relative_to(top).as_posix()


def _owned(root: Path, value, what: str) -> list[str]:
    """A pipeline's path, or the base commit's files a glob in it matches: a map writes a family that way."""
    rel = _text(value, what).replace("\\", "/").strip("/")
    if not any(c in rel for c in "*?["):
        return [_at(root, rel, what)]
    found = [_at(root, p.relative_to(root).as_posix(), what) for p in sorted(root.glob(rel))
             if p.relative_to(root).parts[0].casefold() != ".git"]
    if not found:
        raise ValueError(f"`{what}`: `{rel}` 에 맞는 파일이 기준 커밋에 없다")
    return found


def _evidence(root: Path, value, what: str, least: int = 1) -> list[dict]:
    rows = _list(value, what)
    if len(rows) < least:
        raise ValueError(f"`{what}` 에 근거가 없다")
    return [{"path": _at(root, (r or {}).get("path") if isinstance(r, dict) else None, f"{what}[{i}].path"),
             "symbol": _text(r.get("symbol"), f"{what}[{i}].symbol")} for i, r in enumerate(rows)]


def _level(value, what: str) -> dict:
    if not isinstance(value, dict) or value.get("level") not in LEVELS:
        raise ValueError(f"`{what}.level` 은 {', '.join(LEVELS)} 중 하나다")
    return {"level": value["level"], "why": _text(value.get("why"), f"{what}.why")}


def mapped_shape(value, root: Path) -> dict:
    if not isinstance(value, dict):
        raise ValueError("refactor-map 은 JSON 객체여야 한다")
    pipelines, seen = [], set()
    for i, p in enumerate(_list(value.get("pipelines"), "pipelines")):
        what = f"pipelines[{i}]"
        if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not ID.fullmatch(p["id"]):
            raise ValueError(f"`{what}.id` 는 소문자·숫자·- 40자까지다")
        if p["id"] in seen:
            raise ValueError(f"파이프라인 `{p['id']}` 이 두 번 나왔다")
        seen.add(p["id"])
        paths = list(dict.fromkeys(rel for k, x in enumerate(_list(p.get("paths"), f"{what}.paths"))
                                   for rel in _owned(root, x, f"{what}.paths[{k}]")))
        if not paths:
            raise ValueError(f"`{what}.paths` 가 비었다")
        pipelines.append({"id": p["id"], "name": _text(p.get("name"), f"{what}.name"),
                          "entry": _text(p.get("entry"), f"{what}.entry"), "paths": paths,
                          "role": _text(p.get("role"), f"{what}.role")})
    if not pipelines:
        raise ValueError("파이프라인이 하나도 없다")
    shared = []
    for i, s in enumerate(_list(value.get("shared") or [], "shared")):
        used = _texts((s or {}).get("used_by") if isinstance(s, dict) else None, f"shared[{i}].used_by")
        if unknown := set(used) - seen:
            raise ValueError(f"`shared[{i}].used_by` 에 없는 파이프라인: {sorted(unknown)}")
        shared.append({"path": _at(root, s.get("path"), f"shared[{i}].path"), "used_by": used})
    omm = value.get("omm") or {}
    if not isinstance(omm, dict) or not isinstance(omm.get("used", False), bool):
        raise ValueError("`omm` 은 {used, diverged} 객체여야 한다")
    return {"pipelines": pipelines, "shared": shared, "excluded": _excluded(value.get("excluded") or []),
            "omm": {"used": omm.get("used", False), "diverged": _texts(omm.get("diverged") or [], "omm.diverged")},
            "unchecked": _texts(value.get("unchecked") or [], "unchecked")}


def _excluded(value) -> list[dict]:
    # An excluded path is named, not proven: generated output is often absent from the commit.
    return [{"path": _text((e or {}).get("path") if isinstance(e, dict) else None, f"excluded[{i}].path"),
             "reason": _text(e.get("reason"), f"excluded[{i}].reason")} for i, e in enumerate(_list(value, "excluded"))]


def pipeline_shape(value, pid: str, ids: set[str], root: Path) -> dict:
    if not isinstance(value, dict):
        raise ValueError("refactor-pipeline 은 JSON 객체여야 한다")
    links = []
    for i, link in enumerate(_list(value.get("links") or [], "links")):
        other = _text((link or {}).get("pipeline") if isinstance(link, dict) else None, f"links[{i}].pipeline")
        if other not in ids and not other.startswith("external:"):
            raise ValueError(f"`links[{i}].pipeline` `{other}` 은 지도에 없다 — 외부 시스템은 external:<이름>")
        links.append({"pipeline": other, "how": _text(link.get("how"), f"links[{i}].how")})
    debts = []
    for i, d in enumerate(_list(value.get("debts") or [], "debts")):
        what = f"debts[{i}]"
        if not isinstance(d, dict) or d.get("kind") not in KINDS:
            raise ValueError(f"`{what}.kind` 는 {', '.join(KINDS)} 중 하나다")
        debts.append({"kind": d["kind"], "summary": _text(d.get("summary"), f"{what}.summary"),
                      "evidence": _evidence(root, d.get("evidence"), f"{what}.evidence"),
                      "scope": _text(d.get("scope"), f"{what}.scope"),
                      **{k: _level(d.get(k), f"{what}.{k}") for k in ("size", "difficulty", "risk")},
                      "if_left": _text(d.get("if_left"), f"{what}.if_left")})
    shaped = {"id": pid, **{k: _text(value.get(k), k) for k in ("what", "inputs", "outputs", "owns", "change")},
              "links": links, "debts": debts,
              "misplaced": [{"what": _text((m or {}).get("what") if isinstance(m, dict) else None, f"misplaced[{i}].what"),
                             "evidence": _evidence(root, m.get("evidence"), f"misplaced[{i}].evidence")}
                            for i, m in enumerate(_list(value.get("misplaced") or [], "misplaced"))],
              "checked": _texts(value.get("checked"), "checked"),
              "unchecked": _texts(value.get("unchecked") or [], "unchecked")}
    if not debts:
        shaped["low_debt_reason"] = _text(value.get("low_debt_reason"), "low_debt_reason")
    return shaped


def spec_shape(s, mode: str, map_: dict, root: Path) -> dict:
    if not isinstance(s, dict) or not isinstance(s.get("key"), str) or not ID.fullmatch(s["key"]):
        raise ValueError(f"명세의 `key` `{s.get('key') if isinstance(s, dict) else s}` — 소문자·숫자·- 40자까지다")
    what = f"specs[{s['key']}]"
    title = _text(s.get("title"), f"{what}.title")
    if LOOKING.search(title):
        raise ValueError(f"`{what}` 은 조사·구조 파악만 하는 명세다 — 조사는 이미 끝났고, 명세는 실제 개선만 담는다")
    pipeline = _text(s.get("pipeline"), f"{what}.pipeline")
    creates = [_new(root, f, f"{what}.creates[{i}]")
               for i, f in enumerate(_list(s.get("creates") or [], f"{what}.creates"))]
    # A shared owner the spec extracts is new, so the map could not list it; its own `creates` vouches for it.
    shared = [x["path"] for x in map_["shared"]] + creates
    ids = [p["id"] for p in map_["pipelines"]]
    if pipeline not in ids and pipeline.removeprefix("shared:") not in shared:
        # The repair turn reads this; without the accepted values it only guesses again.
        raise ValueError(f"`{what}.pipeline` `{pipeline}` 은 지도의 파이프라인 하나나, 지도의 shared 또는 이 명세의 "
                         f"`creates` 경로를 담은 shared:<경로> 여야 한다 — 파이프라인: {', '.join(ids)}; "
                         f"shared: {', '.join(shared)}")
    tests = s.get("tests") or {}
    if not isinstance(tests, dict):
        raise ValueError(f"`{what}.tests` 는 객체여야 한다")
    shaped = {
        "key": s["key"], "mode": mode, "title": title, "pipeline": pipeline,
        "files": [_at(root, f, f"{what}.files[{i}]") for i, f in enumerate(_list(s.get("files"), f"{what}.files"))],
        "creates": creates,
        **{k: _text(s.get(k), f"{what}.{k}") for k in ("boundary", "problem", "change", "impact", "rollback")},
        "evidence": _evidence(root, s.get("evidence"), f"{what}.evidence"),
        "keep": _texts(s.get("keep"), f"{what}.keep"),
        "verify": [{"method": _text((v or {}).get("method") if isinstance(v, dict) else None, f"{what}.verify[{i}].method"),
                    "criterion": _text(v.get("criterion"), f"{what}.verify[{i}].criterion")}
                   for i, v in enumerate(_list(s.get("verify"), f"{what}.verify"))],
        "depends": _texts(s.get("depends") or [], f"{what}.depends"),
        "risk": _level(s.get("risk"), f"{what}.risk"),
        "tests": {"add": _texts(tests.get("add") or [], f"{what}.tests.add"),
                  "remove": [{"test": _text((r or {}).get("test") if isinstance(r, dict) else None, f"{what}.tests.remove[{i}].test"),
                              "reason": _text(r.get("reason"), f"{what}.tests.remove[{i}].reason")}
                             for i, r in enumerate(_list(tests.get("remove") or [], f"{what}.tests.remove"))],
                  "contract": [{k: _text((c or {}).get(k) if isinstance(c, dict) else None, f"{what}.tests.contract[{i}].{k}")
                                for k in ("test", "old", "new", "why")}
                               for i, c in enumerate(_list(tests.get("contract") or [], f"{what}.tests.contract"))]},
    }
    if not shaped["files"] or not shaped["keep"] or not shaped["verify"]:
        raise ValueError(f"`{what}` 에 files·keep·verify 가 모두 있어야 한다")
    if s.get("together"):
        shaped["together"] = _text(s["together"], f"{what}.together")
    for k, fields in (("measure", ("metric", "task")), ("hypothesis", ("claim", "check")),
                      ("contract_change", ("impact", "compatibility", "migration", "rollback"))):
        if s.get(k) is None:
            continue
        if mode != "rework":
            raise ValueError(f"`{what}.{k}` 는 rework 명세에만 있다")
        if not isinstance(s[k], dict):
            raise ValueError(f"`{what}.{k}` 는 객체여야 한다")
        shaped[k] = {f: _text(s[k].get(f), f"{what}.{k}.{f}") for f in fields}
    if "measure" in shaped:
        argv, repeat = s["measure"].get("argv"), s["measure"].get("repeat")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
            raise ValueError(f"`{what}.measure.argv` 는 비지 않은 문자열 목록이다")
        if type(repeat) is not int or not 1 <= repeat <= 50:
            raise ValueError(f"`{what}.measure.repeat` 는 1–50 정수다")
        shaped["measure"].update(argv=argv, repeat=repeat)
    if shaped["tests"]["contract"] and not (mode == "simplify" or "contract_change" in shaped):
        raise ValueError(f"`{what}.tests.contract` 는 simplify 명세나 rework 의 계약 변경 명세에만 있다")
    return shaped


def acyclic(graph: dict[str, list[str]]) -> None:
    """Refuse a dependency cycle among `graph`'s nodes; edges leaving it are not followed."""

    state: dict[str, str] = {}

    def visit(key: str) -> None:   # depth-first: a grey node reached again is a cycle
        if state.get(key) == "grey":
            raise ValueError(f"명세 의존이 돌아온다: `{key}`")
        if state.get(key) != "black":
            state[key] = "grey"
            for d in graph[key]:
                if d in graph:
                    visit(d)
            state[key] = "black"

    for key in graph:
        visit(key)


def survey_shape(value, mode: str, map_: dict, root: Path, known: frozenset = frozenset()) -> dict:
    """An integration (or recheck) block; `known` are keys outside it a spec may still depend on."""
    if not isinstance(value, dict) or not isinstance(value.get("coverage"), dict):
        raise ValueError("refactor-survey 는 coverage 가 있는 JSON 객체여야 한다")
    # Every refusal at once, the specs' own and their keys' and dependencies': one repair fixes them all.
    raw, shaped, refused = _list(value.get("specs") or [], "specs"), [], []
    for s in raw:
        try:
            shaped.append(spec_shape(s, mode, map_, root))
        except ValueError as exc:
            refused.append(str(exc))
    # A refused spec's key and dependencies still make the graph, so a cycle shows on the first refusal;
    # a key or dependency that is not text is already among the refusals and stays out of it.
    graph = [(s["key"], [d for d in s.get("depends") or [] if isinstance(d, str)]) for s in raw
             if isinstance(s, dict) and isinstance(s.get("key"), str) and isinstance(s.get("depends") or [], list)]
    keys = [k for k, _ in graph]
    if duplicated := sorted({k for k in keys if keys.count(k) > 1}):
        refused.append(f"명세 `key` 가 겹친다: {duplicated}")
    for key, depends in graph:
        if unknown := set(depends) - set(keys) - known:
            refused.append(f"`specs[{key}].depends` 에 없는 명세: {sorted(unknown)}")
    try:
        acyclic(dict(graph))
    except ValueError as exc:
        refused.append(str(exc))
    if refused:
        raise ValueError("\n".join(refused))
    coverage = value["coverage"]
    result = {"coverage": {"scope": _text(coverage.get("scope"), "coverage.scope"),
                           "unchecked": _texts(coverage.get("unchecked") or [], "coverage.unchecked"),
                           "excluded": _excluded(coverage.get("excluded") or [])},
              "specs": shaped}
    if not shaped:
        result["nothing"] = _text(value.get("nothing"), "nothing")
    return result


def failures(verdict: dict) -> list[str] | None:
    """The failing tests a gate's tail names, or None when it failed without naming any."""
    if verdict.get("code") == 0:
        return []
    tail = verdict.get("tail") or ""
    # Each line stripped, as `unexplained` reads it: an indented `--- FAIL:` (a go subtest) is named too.
    found = {next(g for g in m.groups() if g) for line in tail.splitlines() if (m := FAILED.match(line.strip()))}
    for line in tail.splitlines():
        if (ids := _node(line)) is not None:
            if len(ids) > 1:
                return None   # a node id that could end in two places: two failures could read as one
            found |= ids
    return sorted(found) or None


def _node(line: str) -> set[str] | None:
    """The node ids a `FAILED`/`ERROR` line could name, indented or not: its text before a ` - ` or its
    end that reads as a whole node id. None for any other line — unittest's closing `FAILED (failures=1)`,
    a wrapper's `ERROR conda.cli...` log — which `unexplained` then compares as text."""
    kind, _, body = line.strip().partition(" ")
    if kind not in ("FAILED", "ERROR") or not body or body.startswith("("):
        return None
    ends = [i for i in range(len(body)) if body.startswith(" - ", i)] + [len(body)]
    return {body[:i] for i in ends if NODE.fullmatch(body[:i])} or None


SIGNAL = re.compile(r"\b(error|errors|fatal|fail|failed|failure|failures|exception|traceback|panic)\b", re.I)


def unexplained(tail: str, baseline: str, trees: tuple) -> list[str]:
    """The failure-looking lines of a failing run that no failure name covers and the
    baseline run did not print, compared with both worktree paths blanked and, but on a `FAILED`/`ERROR`
    line that names no node (`docs/case1.txt` is not `docs/case2.txt`), digits too."""
    def plain(line: str) -> str:
        line = line.strip()
        for tree in trees:
            line = line.replace(str(tree), "<tree>").replace(Path(tree).as_posix(), "<tree>")
        return line if line.startswith(("FAILED", "ERROR")) else re.sub(r"\d+", "#", line)
    printed = {plain(line) for line in baseline.splitlines()}
    # pytest's closing count line is judged by its counts alone, before any digit-blind match: passed,
    # skipped and warning counts move with any added test, failed and error counts may not grow.
    summaries = [_counted(line) for line in baseline.splitlines() if SUMMARY.match(line.strip())]
    odd = []
    for line in (line.strip() for line in tail.splitlines()):
        if SUMMARY.match(line):
            if (counts := _counted(line)) and (not summaries or any(n > summaries[-1].get(k, 0)
                                                                    for k, n in counts.items())):
                odd.append(line)
        elif SIGNAL.search(line) and not FAILED.search(line) and _node(line) is None and plain(line) not in printed:
            odd.append(line)
    return odd


# Exactly pytest's count list, its time and its rule (none with `-q`): any other sentence that mentions
# counts stays an ordinary line for the failure-looking comparison.
_COUNT = r"\d+ (?:failed|passed|skipped|deselected|xfailed|xpassed|warnings?|errors?|rerun)"
SUMMARY = re.compile(rf"^(?:=+ )?{_COUNT}(?:, {_COUNT})* in [\d.]+s(?: \(\d+:\d\d:\d\d\))?(?: =+)?$")


def _counted(line: str) -> dict:
    """The failed and error counts of a pytest summary line."""
    return {kind.rstrip("s"): int(n) for n, kind in re.findall(r"(\d+) (failed|errors?)\b", line)}


# -- The phases --------------------------------------------------------------------

def place(repo: Path, rid: str) -> Path:
    return worktree_home(repo) / f"refactor-survey-{rid}"


def checkout(w, run: dict) -> Path:
    """The detached linked worktree of the base commit every survey turn reads."""

    path = place(w.repo, w.rid)
    if not path.exists():
        refactor.git(w.repo, "worktree", "add", "--detach", str(path), run["base"])
    if refactor.git(path, "rev-parse", "HEAD") != run["base"]:
        raise refactor.Stop("worktree", f"`{path}` 이 조사 기준 커밋이 아니다 — 치운 뒤 [재개] 하라")
    return path


def removed(repo: Path, rid: str) -> None:
    if place(repo, rid).exists():
        specs.sh(["git", "worktree", "remove", "--force", str(place(repo, rid))], repo, 120)


def baselined(w, run: dict) -> dict:
    """The base commit and the gate's result on it, before any model reads."""

    if not run.get("base"):
        run = w.note(base=refactor.git(w.repo, "rev-parse", "HEAD"),
                     branch=refactor.git(w.repo, "branch", "--show-current"))
    path = checkout(w, run)
    if run.get("baseline") is None:
        cfg = specs.verify.policy(path, project=w.repo)
        cmd = cfg["local"] if cfg else specs.gate_of(w.repo, path)
        verdict = specs.judge(path, [cmd], w.halt)
        if w.halt.is_set():
            raise refactor.Stop("cancelled", "기준 게이트를 끊었다")
        found = failures(verdict)
        run = w.note(baseline={"cmd": cmd, "ok": verdict["ok"], "code": verdict["code"], "reason": verdict["reason"],
                               "tail": verdict["tail"], "failures": found or [], "parsed": found is not None,
                               "head": verdict["head"]})

    def clean() -> bool:
        seen = specs.status(path)
        return not seen.returncode and not seen.stdout.strip() \
            and specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip() == run["base"]

    # Checked on every pass, a resume too: the survey reads the base commit as it was, never what the gate wrote.
    if not clean():
        removed(w.repo, w.rid)
        path = checkout(w, run)
        if not clean():
            raise refactor.Stop("worktree", f"기준 게이트가 바꾼 `{path}` 를 기준 커밋으로 되돌리지 못했다 — 치운 뒤 [재개] 하라")
    return w.note(phase="recheck" if run.get("recheck") else "map")


REPAIR = ("Your `{tag}` block was refused: {reason}\n\nThe refused block:\n```{tag}\n{block}```\n\n"
          "Return the whole `{tag}` block again with that fixed. Change only what the refusal names; read the "
          "repository only where the fix needs it.")


def asked(w, run: dict, phase: str, text: str, system: str, tag: str, shape):
    """One read-only turn in the survey's worktree on the chosen model; its block, shaped. A refused block
    goes back to the same model once with the reason — a long turn is not lost to one bad field; a second
    refusal raises its `ValueError`."""

    answer = refactor.block(tag, said(w, run, phase, text, system))
    try:
        return shape(json.loads(answer))
    except ValueError as exc:
        again = REPAIR.format(tag=tag, reason=exc, block=answer)
    return shape(json.loads(refactor.block(tag, said(w, run, f"{phase}:repair", again, system))))


def said(w, run: dict, phase: str, text: str, system: str) -> str:
    """One read-only turn on the chosen model, refused when another model answered; all it said."""

    path = checkout(w, run)
    final = refactor.turn(w, run, f"{rules(run['mode'])}\n\n{text}", system, path)
    # The session `turn` ran is still registered for its path; Claude names the model it attached at init.
    seen = str(getattr(work._runs[str(path)].chat, "model_name", "") or "")
    want, got = (run["role"].get("model") or "").removeprefix("codex:").lower(), seen.lower()
    run = refactor.load(w.repo.name, w.rid)
    w.note(models=[*run.get("models", []), {"phase": phase, "requested": run["role"].get("model") or "",
                                             "reported": seen}])
    if want and got and want not in got and got.split("[")[0] not in want:
        raise refactor.Stop("model", f"`{run['role']['model']}` 을 골랐는데 `{seen}` 이 답했다 — 다른 모델로 대신하지 않는다")
    return final


def omm(w, run: dict, path: Path) -> str:
    """What the map turn is told about `.omm/`: where it is, what it holds, how stale it is."""

    folder = path / ".omm" if (path / ".omm").is_dir() else w.repo / ".omm" if (w.repo / ".omm").is_dir() else None
    if folder is None:
        return "There is no `.omm/` structure document: analyse the code alone and set `omm.used` to false."
    nodes = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_dir())[:200]
    last = specs.sh(["git", "log", "-1", "--format=%H", "--", ".omm"], w.repo).stdout.strip()
    changed = specs.sh(["git", "diff", "--name-only", last, run["base"], "--", ".", ":(exclude).omm"],
                       w.repo).stdout.split() if last else []
    since = (f"Files changed in code since `.omm` was last committed ({last[:12]}): "
             + (", ".join(changed[:300]) or "none")) if last else "`.omm` is not committed; its age is unknown."
    return (f"A `.omm/` structure document is at `{folder}` (read it with absolute paths). Nodes: "
            f"{', '.join(nodes) or 'none'}.\n{since}\nCheck it against the code; the code wins.")


def mapped(w, run: dict) -> dict:
    path = checkout(w, run)
    rows = refactor.hotspots(path, 30)
    table = "\n".join(f"- {r['path']}: {r['lines']} lines, {r['dup']} duplicated, longest block {r['block']}, "
                      f"{r['churn']} commits in 180 days" for r in rows) or "(nothing over the caps)"
    gate = run["baseline"]
    text = (f"Base commit: {run['base']}.\n\n{omm(w, run, path)}\n\n"
            f"Reference measurements (size is not debt by itself):\n{table}\n\n"
            f"Gate on the base commit: `{gate['cmd']}` exited {gate['code']}"
            + (f"; failing: {', '.join(gate['failures'])}" if gate["failures"] else "") + ".")
    try:
        shaped = asked(w, run, "map", text, MAP_PROMPT, "refactor-map", lambda v: mapped_shape(v, path))
    except ValueError as exc:
        raise refactor.Stop("format", f"refactor-map 이 맞지 않다 — {exc}") from exc
    return w.note(map=shaped, pipelines={}, phase="pipelines")


def explained(w, run: dict) -> dict:
    path, done = checkout(w, run), dict(run.get("pipelines") or {})
    ids = {p["id"] for p in run["map"]["pipelines"]}
    for p in run["map"]["pipelines"]:
        if p["id"] in done:   # a retry keeps what finished
            continue
        text = (f"The repository map:\n{json.dumps(run['map'], ensure_ascii=False)}\n\n"
                f"Explain pipeline `{p['id']}` ({p['name']}).")
        try:
            done[p["id"]] = asked(w, run, f"pipeline:{p['id']}", text, PIPELINE_PROMPT, "refactor-pipeline",
                                  lambda v, pid=p["id"]: pipeline_shape(v, pid, ids, path))
        except ValueError as exc:
            raise refactor.Stop("format", f"`{p['id']}` 의 refactor-pipeline 이 맞지 않다 — {exc}") from exc
        run = w.note(pipelines=done)
    return w.note(phase="integrate")


def integrated(w, run: dict) -> dict:
    path = checkout(w, run)
    text = (f"The repository map:\n{json.dumps(run['map'], ensure_ascii=False)}\n\nPipeline reports:\n"
            + json.dumps(list(run["pipelines"].values()), ensure_ascii=False))
    try:
        survey = asked(w, run, "integrate", text, INTEGRATE_PROMPT, "refactor-survey",
                       lambda v: survey_shape(v, run["mode"], run["map"], path))
    except ValueError as exc:
        raise refactor.Stop("format", f"refactor-survey 가 맞지 않다 — {exc}") from exc
    run = w.note(survey=survey)
    cards = carded(w, run)
    removed(w.repo, w.rid)
    return w.note(phase="done", state="done", specs=cards)


def carded(w, run: dict) -> list[str]:
    """The survey's specs as ordinary unstarted cards, written once: a resume
    finds them by id. A completed survey retires older surveys' unstarted cards."""

    repo = w.repo
    sid = {s["key"]: f"{s['key']}-{w.rid}" for s in run["survey"]["specs"]}
    source = {"focus": "refactor", "turn": time.time(), "session": "", "plan": None,
              "refactor_mode": run["mode"], "refactor_generation": run.get("generation", 0)}
    for s in run["survey"]["specs"]:
        with specs._files:
            if specs.load(repo.name, sid[s["key"]]) is None:
                fresh(repo, sid[s["key"]], made(repo, w.rid, run["base"], s, [sid[d] for d in s["depends"]]), source)
    for spec in specs.listing(repo.name):
        held = spec.get("refactor") or {}
        if held.get("survey") and held["survey"] != w.rid and spec["state"] == "정리됨":
            with specs._files:
                if specs.load(repo.name, spec["id"]) is not None:
                    specs.aside(repo.name, spec["id"])
    return list(sid.values())


def made(repo: Path, survey: str, base: str, s: dict, depends: list[str]) -> dict:
    """A survey spec's card fields: the ordinary ones, and the `refactor` block a run reads."""

    block = {"goal": f"{s['title']}. {s['change']}", "out": [*s["keep"], "Changes owned by the other refactoring modes"],
             "done": [f"{v['method']}: {v['criterion']}" for v in s["verify"]],
             "grounds": {"files": sorted({e["path"] for e in s["evidence"]})},
             # `preserve` is reviewed against its frozen characterization tests.
             **({"review": {"criteria": ["refactor"], "evidence": ["differential"]}} if s["mode"] == "preserve" else {})}
    # The roles fixed at the survey's start: the card's work cell and review cell run on them, never on
    # whatever the screen has chosen then.
    roles = refactor.load(repo.name, survey)["roles"]
    return {**specs.fields(repo, block, specs.gate_of(repo)),
            "cell": {**roles["worker"], "fast": False}, "reviewer": roles["reviewer"],
            "refactor": {"mode": s["mode"], "survey": survey, "base": base, "pipeline": s["pipeline"],
                         "files": s["files"], "evidence": sorted({e["path"] for e in s["evidence"]}),
                         "depends": depends, "detail": s, "roles": roles}}


def kept(spec: dict, fields: dict) -> dict:
    """A card remade from its survey (refined, split or rechecked) keeps the work and review models
    chosen on it since, and their history: the survey record holds only the models it started with."""

    was = spec["refactor"]
    roles = {**fields["refactor"]["roles"], "worker": was["roles"]["worker"], "reviewer": was["roles"]["reviewer"]}
    return {**fields, "cell": {**roles["worker"], "fast": False}, "reviewer": roles["reviewer"],
            "refactor": {**fields["refactor"], "roles": roles,
                         **({"role_changes": was["role_changes"]} if was.get("role_changes") else {})}}


def fresh(repo: Path, sid: str, fields: dict, source: dict) -> None:
    specs.save({"id": sid, "repo": repo.name, "rev": 1, **fields, "source": source, "state": "정리됨",
                "stopped": None, "worktree": None, "pr": None, "report": None, "gate": None, "fault": None,
                "history": [{"ts": time.time(), "state": "정리됨"}]})


def refined(repo: Path, block, source: dict) -> str:
    """A refactoring conversation's `spec` block: `id` refines that card in
    place, a new revision; `split_from` adds only the part split off it. No
    card comes from a conversation alone: each belongs to a survey."""

    if not isinstance(block, dict) or not isinstance(target := block.get("id") or block.get("split_from"), str):
        raise ValueError("조사에서 나온 명세를 `id` 로 다듬거나 `split_from` 으로 나눈다 — 조사 없이 새 명세를 만들지 않는다")
    with specs._files:
        spec = specs.load(repo.name, target)
        if spec is None or not (spec.get("refactor") or {}).get("survey"):
            raise ValueError(f"`{target}` 은 조사에서 나온 명세가 아니다")
        if block.get("id") and spec["state"] != "정리됨":
            raise ValueError("시작한 명세는 대화에서 바꾸지 않는다")
        r = spec["refactor"]
        if not r.get("roles"):
            raise ValueError(f"`{target}` 은 조사·구현·리뷰 모델을 고정하기 전의 명세다 — 모델을 골라 새로 조사해라")
        if r["mode"] != source.get("refactor_mode") \
                or spec["source"].get("refactor_generation", 0) != source.get("refactor_generation", 0):
            raise ValueError("명세 모드가 상단에서 고른 모드와 다르다 — 지금 모드로 다시 조사해라")
        # The block was written by the conversation's model: only a card of the survey pinned on it may take it.
        talk, role = config("refactor", repo.name), r["roles"]["survey"]
        if (talk.get("model"), talk.get("effort") or "") != (role["model"], role["effort"]):
            raise ValueError(f"`{target}` 은 조사 모델 `{role['model']}` 의 명세다 — 지금 대화는 `{talk.get('model')}` "
                             "로 고정됐다. 그 조사의 카드는 대화에서 다듬지 않는다")
        given = {k: v for k, v in block.items() if k not in ("id", "split_from")}
        if block.get("id"):
            given["key"] = r["detail"]["key"]   # refining keeps the card's key
        detail = spec_shape(given, r["mode"], refactor.load(repo.name, r["survey"])["map"], repo)
        for dep in detail["depends"]:
            if not ((specs.load(repo.name, dep) or {}).get("refactor") or {}).get("survey") or dep == block.get("id"):
                raise ValueError(f"`depends` 의 `{dep}` 은 다른 조사 명세의 id 여야 한다")
        fields = kept(spec, made(repo, r["survey"], r["base"], detail, detail["depends"]))
        sid = target if block.get("id") else f"{detail['key']}-{r['survey']}"
        # Every survey card, whatever survey it came from: a recheck gives a card a new survey id, not new edges.
        graph = {s["id"]: s["refactor"]["depends"] for s in specs.listing(repo.name)
                 if (s.get("refactor") or {}).get("survey")}
        acyclic({**graph, sid: detail["depends"]})   # a refinement may not close a loop the survey refused
        if block.get("id"):
            specs.save({**spec, **fields, "rev": spec["rev"] + 1})
            return target
        if specs.load(repo.name, sid) is not None:
            raise ValueError(f"`{sid}` 가 이미 있다 — 나눈 명세는 다른 key 를 쓴다")
        fresh(repo, sid, fields, {**source, "plan": None})
        return sid


RECHECK_PROMPT = (
    "Recheck one refactoring spec against the current code. " + COMMON + " It was written on an older commit and "
    "the files listed changed since. Read its pipeline again here. If the problem still holds, return the spec "
    "rewritten for this code with the same key; if it no longer holds, or now belongs to another mode, return no "
    "spec and say why in `nothing`. End with one fenced block tagged `refactor-survey` holding JSON shaped like:\n"
    + json.dumps({"coverage": {"scope": "<what you reread>", "unchecked": ["..."], "excluded": []},
                  "specs": [SPEC_SCHEMA], "nothing": "<only with no spec: why>"}))


def rechecked(w, run: dict) -> dict:
    """One stale card read again on the current commit: rewritten in place, or set aside."""

    spec = specs.load(w.repo.name, run["recheck"])
    if spec is None or spec["state"] != "정리됨":
        raise refactor.Stop("spec_gone", f"`{run['recheck']}` 명세가 없거나 이미 시작했다")
    old, path = spec["refactor"], checkout(w, run)
    parent = refactor.load(w.repo.name, old["survey"]) or {}
    changed = specs.sh(["git", "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames", old["base"],
                        run["base"]], w.repo).stdout.splitlines()
    text = (f"The repository map:\n{json.dumps(parent['map'], ensure_ascii=False)}\n\nThe pipeline report from the "
            f"older survey:\n{json.dumps((parent.get('pipelines') or {}).get(old['pipeline']), ensure_ascii=False)}"
            f"\n\nThe spec:\n{json.dumps(old['detail'], ensure_ascii=False)}\n\nFiles changed since its base "
            f"{old['base']}: {', '.join(changed[:300]) or 'none'}")
    # The card's prerequisites are other specs of its survey, not in this one-spec answer.
    others = frozenset([*old["detail"]["depends"], *(s["key"] for s in (parent.get("survey") or {}).get("specs") or [])])

    def shape(value):
        survey = survey_shape(value, run["mode"], parent["map"], path, others)
        if len(survey["specs"]) > 1 or survey["specs"] and survey["specs"][0]["key"] != old["detail"]["key"]:
            raise ValueError(f"key `{old['detail']['key']}` 의 명세 하나만 돌려줘야 한다")
        return survey

    try:
        survey = asked(w, run, "recheck", text, RECHECK_PROMPT, "refactor-survey", shape)
    except ValueError as exc:
        raise refactor.Stop("format", f"다시 확인한 refactor-survey 가 맞지 않다 — {exc}") from exc
    with specs._files:
        spec = specs.load(w.repo.name, run["recheck"])
        if spec is None or spec["state"] != "정리됨":
            raise refactor.Stop("spec_gone", f"`{run['recheck']}` 명세가 없거나 이미 시작했다")
        if survey["specs"]:
            specs.save({**spec, **kept(spec, made(w.repo, w.rid, run["base"], survey["specs"][0], old["depends"])),
                        "rev": spec["rev"] + 1})
        else:
            specs.aside(w.repo.name, spec["id"])
    removed(w.repo, w.rid)
    return w.note(map=parent["map"], pipelines={k: v for k, v in (parent.get("pipelines") or {}).items()
                                                if k == old["pipeline"]},
                  survey=survey, phase="done", state="done", specs=[spec["id"]] if survey["specs"] else [])


PHASES = {"baseline": baselined, "map": mapped, "pipelines": explained, "integrate": integrated, "recheck": rechecked}

# Last: `refactor` builds its phase table from `PHASES` at import, and `specs` leads back to `refactor`.
from . import refactor, specs, work  # noqa: E402
