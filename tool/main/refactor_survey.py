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

from .query import ROOT

MODES = ("preserve", "simplify", "rework")
KINDS = ("duplication", "coupling", "mixed_responsibility", "defensive", "test_contract", "performance",
         "naming", "dead_code")
LEVELS = ("high", "medium", "low")
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
# A spec is a change to make. One that only looks, maps or writes the structure down is the survey's own work.
LOOKING = re.compile(r"^\s*(survey|investigate|audit|explore|map|analy[sz]e|document|diagram|study|inspect)\b"
                     r"|조사|구조도|파악", re.I)
# Failure identifiers in the tail of common runners' output: pytest, vitest/jest, go test, cargo test.
# A pytest parameter id may hold spaces: `name[case one]` is read to its closing bracket before ` - `.
FAILED = re.compile(r"^(?:FAILED|ERROR) (\S+?\[.*?\](?= - |\s*$)|\S+)|^\s*FAIL\s+(\S+)|^--- FAIL: (\S+)"
                    r"|^test (\S+) \.\.\. FAILED", re.M)


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
    "key": "<kebab-case id, unique in this survey>", "title": "<the one improvement>",
    "pipeline": "<one pipeline id, or shared:<path> for a shared boundary change>",
    "files": ["<repository-relative files the change touches>"],
    "boundary": "<the responsibility boundary this spec works inside>",
    "problem": "<the confirmed problem>", "evidence": [{"path": "...", "symbol": "..."}],
    "change": "<exactly what changes>", "keep": ["<behaviour and scope that stay unchanged>"],
    "verify": [{"method": "<how completion is checked>", "criterion": "<what counts as done>"}],
    "depends": ["<keys of specs that must merge first>"],
    "impact": "<effect on prerequisite specs and other pipelines>",
    "risk": LEVEL, "rollback": "<how to undo it>",
    "together": "<only when one spec must change several pipelines' shared code at once: why splitting breaks "
                "the intermediate state>",
    "tests": {"add": ["<tests to add>"], "remove": [{"test": "<path or path::name>", "reason": "<exact duplicate, "
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
        paths = [_at(root, x, f"{what}.paths[{k}]") for k, x in enumerate(_list(p.get("paths"), f"{what}.paths"))]
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
        raise ValueError("명세의 `key` 는 소문자·숫자·- 40자까지다")
    what = f"specs[{s['key']}]"
    title = _text(s.get("title"), f"{what}.title")
    if LOOKING.search(title):
        raise ValueError(f"`{what}` 은 조사·구조 파악만 하는 명세다 — 조사는 이미 끝났고, 명세는 실제 개선만 담는다")
    pipeline = _text(s.get("pipeline"), f"{what}.pipeline")
    shared = {x["path"] for x in map_["shared"]}
    if pipeline not in {p["id"] for p in map_["pipelines"]} and pipeline.removeprefix("shared:") not in shared:
        raise ValueError(f"`{what}.pipeline` `{pipeline}` 은 지도의 파이프라인 하나나 shared:<경로> 여야 한다")
    tests = s.get("tests") or {}
    if not isinstance(tests, dict):
        raise ValueError(f"`{what}.tests` 는 객체여야 한다")
    shaped = {
        "key": s["key"], "mode": mode, "title": title, "pipeline": pipeline,
        "files": [_at(root, f, f"{what}.files[{i}]") for i, f in enumerate(_list(s.get("files"), f"{what}.files"))],
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
    shaped = [spec_shape(s, mode, map_, root) for s in _list(value.get("specs") or [], "specs")]
    keys = [s["key"] for s in shaped]
    if len(set(keys)) != len(keys):
        raise ValueError("명세 `key` 가 겹친다")
    for s in shaped:
        if unknown := set(s["depends"]) - set(keys) - known:
            raise ValueError(f"`specs[{s['key']}].depends` 에 없는 명세: {sorted(unknown)}")
    acyclic({s["key"]: s["depends"] for s in shaped})
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
    found = sorted({next(g for g in m.groups() if g) for m in FAILED.finditer(verdict.get("tail") or "")})
    if any("[" in name and not name.endswith("]") for name in found):
        return None   # a parameter id cut short could be any of its cases
    return found or None


SIGNAL = re.compile(r"\b(error|errors|fatal|fail|failed|failure|failures|exception|traceback|panic)\b", re.I)


def unexplained(tail: str, baseline: str, trees: tuple) -> list[str]:
    """The failure-looking lines of a failing run that no failure name covers and the
    baseline run did not print, compared with digits and both worktree paths blanked."""
    def plain(line: str) -> str:
        for tree in trees:
            line = line.replace(str(tree), "<tree>").replace(Path(tree).as_posix(), "<tree>")
        return re.sub(r"\d+", "#", line.strip())
    printed = {plain(line) for line in baseline.splitlines()}
    # pytest's closing count line: passed, skipped and warning counts move with any added test, so it
    # stands explained when it counts no more failures and errors than the baseline's did.
    summaries = [_counted(line) for line in baseline.splitlines() if SUMMARY.match(line.strip())]
    return [line.strip() for line in tail.splitlines()
            if SIGNAL.search(line) and not FAILED.search(line) and plain(line) not in printed
            and not (summaries and SUMMARY.match(line.strip())
                     and all(n <= summaries[-1].get(k, 0) for k, n in _counted(line).items()))]


SUMMARY = re.compile(r"^=+ .*\b(passed|failed|errors?|skipped|warnings?|xfailed|xpassed|deselected)\b.* in [\d.]+s\b.*=+$")


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


def asked(w, run: dict, phase: str, text: str, system: str, tag: str):
    """One read-only turn in the survey's worktree on the chosen model; its block."""

    path = checkout(w, run)
    final = refactor.turn(w, run, f"{rules(run['mode'])}\n\n{text}", system, write=False, where=path)
    # The session `turn` ran is still registered for its path; Claude names the model it attached at init.
    seen = str(getattr(work._runs[str(path)].chat, "model_name", "") or "")
    want, got = (run["role"].get("model") or "").removeprefix("codex:").lower(), seen.lower()
    run = refactor.load(w.repo.name, w.rid)
    w.note(models=[*run.get("models", []), {"phase": phase, "requested": run["role"].get("model") or "",
                                             "reported": seen}])
    if want and got and want not in got and got.split("[")[0] not in want:
        raise refactor.Stop("model", f"`{run['role']['model']}` 을 골랐는데 `{seen}` 이 답했다 — 다른 모델로 대신하지 않는다")
    return refactor.fenced(tag, final)


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
        shaped = mapped_shape(asked(w, run, "map", text, MAP_PROMPT, "refactor-map"), path)
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
            done[p["id"]] = pipeline_shape(asked(w, run, f"pipeline:{p['id']}", text, PIPELINE_PROMPT,
                                                 "refactor-pipeline"), p["id"], ids, path)
        except ValueError as exc:
            raise refactor.Stop("format", f"`{p['id']}` 의 refactor-pipeline 이 맞지 않다 — {exc}") from exc
        run = w.note(pipelines=done)
    return w.note(phase="integrate")


def integrated(w, run: dict) -> dict:
    path = checkout(w, run)
    text = (f"The repository map:\n{json.dumps(run['map'], ensure_ascii=False)}\n\nPipeline reports:\n"
            + json.dumps(list(run["pipelines"].values()), ensure_ascii=False))
    try:
        survey = survey_shape(asked(w, run, "integrate", text, INTEGRATE_PROMPT, "refactor-survey"),
                              run["mode"], run["map"], path)
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
    return {**specs.fields(repo, block, specs.gate_of(repo)),
            "refactor": {"mode": s["mode"], "survey": survey, "base": base, "pipeline": s["pipeline"],
                         "files": s["files"], "evidence": sorted({e["path"] for e in s["evidence"]}),
                         "depends": depends, "detail": s}}


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
        if r["mode"] != source.get("refactor_mode") \
                or spec["source"].get("refactor_generation", 0) != source.get("refactor_generation", 0):
            raise ValueError("명세 모드가 상단에서 고른 모드와 다르다 — 지금 모드로 다시 조사해라")
        given = {k: v for k, v in block.items() if k not in ("id", "split_from")}
        if block.get("id"):
            given["key"] = r["detail"]["key"]   # refining keeps the card's key
        detail = spec_shape(given, r["mode"], refactor.load(repo.name, r["survey"])["map"], repo)
        for dep in detail["depends"]:
            if not ((specs.load(repo.name, dep) or {}).get("refactor") or {}).get("survey") or dep == block.get("id"):
                raise ValueError(f"`depends` 의 `{dep}` 은 다른 조사 명세의 id 여야 한다")
        fields = made(repo, r["survey"], r["base"], detail, detail["depends"])
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
    try:
        # The card's prerequisites are other specs of its survey, not in this one-spec answer.
        others = frozenset([*old["detail"]["depends"], *(s["key"] for s in (parent.get("survey") or {}).get("specs") or [])])
        survey = survey_shape(asked(w, run, "recheck", text, RECHECK_PROMPT, "refactor-survey"), run["mode"],
                              parent["map"], path, others)
        if len(survey["specs"]) > 1 or survey["specs"] and survey["specs"][0]["key"] != old["detail"]["key"]:
            raise ValueError(f"key `{old['detail']['key']}` 의 명세 하나만 돌려줘야 한다")
    except ValueError as exc:
        raise refactor.Stop("format", f"다시 확인한 refactor-survey 가 맞지 않다 — {exc}") from exc
    with specs._files:
        spec = specs.load(w.repo.name, run["recheck"])
        if spec is None or spec["state"] != "정리됨":
            raise refactor.Stop("spec_gone", f"`{run['recheck']}` 명세가 없거나 이미 시작했다")
        if survey["specs"]:
            specs.save({**spec, **made(w.repo, w.rid, run["base"], survey["specs"][0], old["depends"]),
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
