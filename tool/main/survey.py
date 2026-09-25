"""survey — a repository's first wiki, from `[연결]` to a pull request.

With the switch on, `[연결]` goes on here once both hosts are tested. The
survey is a spec like any other: its worktree is `wiki-bootstrap`, its first
commit is the adapter `[연결]` wrote into the original, it ends in a
`done-report` the server checks with the gate before the pull request goes up,
and the review loop takes the pull request from there.

A turn per step, not one long turn: Claude says what a turn cost only when
the turn ends, so a limit can only be kept between turns. The time limit also
cuts a turn that is running, the way a person's `[멈춤]` does; what that turn
left is stashed and the survey goes on with what earlier turns committed.

Nothing in `sync`, `harvest` or the retrospective imports this module or its
settings. A repository surveyed and one not surveyed grow their wiki the same
way after; the survey only makes the first pages.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import time
from pathlib import Path

from fastapi import HTTPException

from workspace import create

from . import channels, loop, query, specs, work
from .connect import ADAPTER, RECORDS, git, keep, record, tracked

DEFAULTS = {"survey": False, "survey_tokens": 2_000_000, "survey_minutes": 90, "survey_model": "opus"}
RATES = RECORDS / "survey" / "rates.json"
BOOTSTRAP = "wiki-bootstrap"
PER_SECOND = 200.0     # tokens a second, until a survey on this machine measures it
TURN_TOKENS = 30_000   # a turn's own share: the system prompt, the instruction, tool calls
PAGE_TOKENS = 3_000    # one page written
PR_TOKENS = 300        # one merged pull request's body, read for the decisions
PER_TURN = 4           # module pages a turn

# Generated and locked files, and what is not text: not read, not counted.
SKIP = re.compile(
    r"(^|/)(node_modules|dist|build|vendor|target|\.venv|venv|__pycache__)/"
    r"|(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock|Cargo\.lock|go\.sum)$"
    r"|\.(lock|min\.js|min\.css|map|png|jpe?g|gif|ico|webp|pdf|zip|gz|woff2?|ttf|mp[34]|wav)$", re.I)

FRONT = "`scope: project`, `severity: preference`, `triggers: []`, `slots: []`, `sources: []`, `links: []`"
LEAD = (
    "Survey turn {i} of {n}, for this repository's first wiki. Write only under `.wiki/`. Never change or "
    "delete a file that already exists; the server reverts it. Read `README*` and `docs/`, never change them. "
    "Every page is Markdown with YAML front matter, in the shape the section \"The minimum shape of a page\" "
    "of `{schema}` gives, with `scope: project`. Commit what you wrote as one commit, then stop. Do not push.\n\n"
)
REPORT = ("The survey's writing is over{why}. Run each item of `done` in this worktree and end with the "
          "`done-report` block, as your instructions say. Change nothing.")


def settings() -> dict:
    return loop.settings(DEFAULTS)


def rates() -> dict:
    """How the estimate is scaled and how fast this machine goes, as the
    last complete survey measured them."""

    try:
        saved = json.loads(RATES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {"factor": float(saved.get("factor", 1.0)), "per_second": float(saved.get("per_second", PER_SECOND))}


def calibrate(raw: float, used: int, seconds: float) -> None:
    """ponytail: the last complete survey replaces the one before. Average
    them if surveys of different repositories come out far apart."""

    if raw <= 0 or used <= 0 or seconds <= 0:
        return
    RATES.parent.mkdir(parents=True, exist_ok=True)
    RATES.write_text(json.dumps({"factor": used / raw, "per_second": used / seconds, "ts": time.time()}) + "\n",
                     encoding="utf-8")


# -- What there is to read ---------------------------------------------------------

def files(path: Path) -> list[tuple[str, int]]:
    """Tracked files and their sizes, without the generated and the binary."""

    listed = git(path, "-c", "core.quotepath=off", "ls-files").stdout.splitlines()
    found = []
    for name in listed:
        if name and not SKIP.search(name):
            try:
                found.append((name, (path / name).stat().st_size))
            except OSError:
                pass
    return found


def modules(listed: list[tuple[str, int]]) -> list[str]:
    """The folders a module page is written for.

    ponytail: by folder name — top-level folders holding code, one level down
    when there is only one (`src/`). Grouping by imports would follow the
    code's own seams, if folder names turn out not to."""

    code = [f for f, _ in listed if "/" in f and not f.endswith(".md") and not f.startswith((".", "docs/"))]
    tops = sorted({f.split("/")[0] for f in code})
    if len(tops) == 1:
        tops = sorted({"/".join(f.split("/")[:2]) for f in code if f.count("/") >= 2}) or tops
    return tops


def page_of(module: str) -> str:
    return f".wiki/modules/{module.replace('/', '-')}.md"


def turns(path: Path, mods: list[str]) -> list[tuple[str, str]]:
    """`(label, instruction)` per turn, leaving out a page whose file is there."""

    hub = channels.WIKI
    out = []
    if not (path / ".wiki/project.md").exists():
        out.append(("구조 개요", "Write `.wiki/project.md`: one page on the structure of this repository — what "
                                 "it is for, the top-level layout and what each part does, how the parts talk, how "
                                 "it is built, run and tested. Cite paths; do not copy code. Front matter: "
                                 f"{FRONT}."))
    todo = [m for m in mods if not (path / page_of(m)).exists()]
    for k in range(0, len(todo), PER_TURN):
        batch = todo[k:k + PER_TURN]
        out.append((f"모듈 {', '.join(batch)}",
                    "Write one page per module below, at the path given: what it is for, its entry points, what "
                    "it depends on and what depends on it, and what would trip a newcomer — each claim with the "
                    f"`path:line` it rests on. Front matter: {FRONT}. These pages are never injected; search and "
                    "the map find them.\n\n" + "\n".join(f"- `{m}/` → `{page_of(m)}`" for m in batch)))
    out.append(("규칙 페이지",
                "Write the rule pages this repository needs as `.wiki/<name>.md`: gate commands, launchers, ports, "
                "invariants, things that broke before. One rule a page, with `triggers` for the utterances it "
                "guards. `severity: landmine` only where you can cite what actually went wrong in `sources:` — a "
                "commit, a pull request, a file; without that evidence it is `contract` or `preference`. Three to "
                "eight pages; fewer is fine."))
    out.append(("결정 기록",
                f'Run `"{sys.executable}" "{hub / "tool/harvest.py"}" --project . --write` here (with '
                "`--from commits` if it reads no pull requests). It writes `.wiki/decisions/`. Commit the records."))
    out.append(("adapter 슬롯",
                "Check `.wiki/adapter.toml` against the repository: `gate_cmd` is what must pass before a change "
                "is done here, `live_cmd` how to see the program run for real, `server_stop` how to stop what "
                "`live_cmd` starts. Correct what is wrong and fill what the repository establishes; leave empty "
                "what it does not. Commit the change."))
    return out


def estimate(path: Path) -> dict:
    """Tokens and seconds, before anything runs: a quarter of the bytes read,
    a share per merged pull request, per turn and per page — scaled by what
    the last survey measured."""

    listed = files(path)
    docs = sum(size for name, size in listed if name.endswith(".md"))
    code = sum(size for name, size in listed if not name.endswith(".md"))
    count = git(path, "rev-list", "--count", "HEAD")
    merged = specs.sh(["gh", "pr", "list", "--state", "merged", "--limit", "1000", "--json", "number"], path, 60)
    try:
        prs = len(json.loads(merged.stdout)) if not merged.returncode else 0
    except ValueError:
        prs = 0
    mods = modules(listed)
    plan = turns(path, mods)
    raw = (code + docs) / 4 + prs * PR_TOKENS + (len(plan) + 1) * TURN_TOKENS + len(plan) * PAGE_TOKENS
    rate, limit = rates(), settings()
    tokens = int(raw * rate["factor"])
    seconds = int(tokens / rate["per_second"])
    return {"files": len(listed), "code": code, "docs": docs,
            "commits": int(count.stdout.strip() or 0) if not count.returncode else 0, "prs": prs,
            "modules": len(mods), "turns": len(plan) + 1, "raw": int(raw), "tokens": tokens, "seconds": seconds,
            "limit": {"tokens": limit["survey_tokens"], "seconds": limit["survey_minutes"] * 60},
            "over": tokens > limit["survey_tokens"] or seconds > limit["survey_minutes"] * 60}


# -- The spec ---------------------------------------------------------------------------

def gate(repo: Path) -> str:
    """The first done item, which the server runs: the repository's own gate
    when it has one, then the hub's lint over it and `repo_lint`. The wiring
    is judged in the original checkout, not in a worktree."""

    python, hub = sys.executable, channels.WIKI / "tool"
    checks = [f'"{python}" "{hub / "lint.py"}" --check --repo .',
              f'"{python}" "{hub / "repo_lint.py"}" --repo . --no-wiring']
    own = specs.gate_of(repo)
    return " && ".join([own, *checks] if own else checks)


def progress(name: str, **fields) -> None:
    keep(name, survey={**(record(name).get("survey") or {}), **fields})


def start(repo: Path) -> str | None:
    """The survey spec, its worktree with the original's uncommitted adapter
    as the first commit, and its turns on their own thread. Its id, or `None`
    when it could not start — the record says why."""

    name = repo.name
    try:
        with specs._files:
            sid = specs.unique(repo, BOOTSTRAP)
            path = create(repo, sid)
    except (ValueError, RuntimeError, OSError) as exc:
        progress(name, state="실패", reason=f"작업트리를 만들지 못했다 — {exc}")
        return None
    handover = (repo / ADAPTER).is_file() and not tracked(repo)
    if handover:
        (path / ".wiki").mkdir(exist_ok=True)
        shutil.copyfile(repo / ADAPTER, path / ADAPTER)
        git(path, "add", "-f", "--", ADAPTER)
        made = git(path, "commit", "-qm", "wiki: adapter")
        if made.returncode:
            progress(name, sid=sid, state="실패", reason=f"adapter 를 커밋하지 못했다 — {specs.said(made)}")
            return None
    first = git(path, "rev-parse", "HEAD").stdout.strip()
    limits, guessed = settings(), estimate(path)
    now = time.time()
    specs.save({
        "id": sid, "repo": name, "rev": 1, "goal": "이 저장소의 위키 초기화",
        "out": ["이미 있는 파일 고치기", "코드 고치기"], "done": [gate(repo)],
        "grounds": {"pages": [], "files": [], "rules": []},
        "decisions": [{"what": "첫 위키를 전수조사로 채운다",
                       "why": "[연결] 에서 조사를 켰다. 이미 있는 파일은 건드리지 않았다", "rejected": ""}],
        "source": {"focus": "connect", "plan": None}, "state": "작업 중", "stopped": None,
        "worktree": str(path), "pr": None, "report": None, "gate": None, "fault": None,
        "cell": {"model": limits["survey_model"], "effort": ""},
        "survey": {"handover": handover, "first": first},
        "history": [{"ts": now, "state": "정리됨"}, {"ts": now, "state": "작업 중"}],
    })
    progress(name, sid=sid, state="도는 중", turn=0, turns=guessed["turns"], label="", tokens=0,
             limit=limits["survey_tokens"], why="", reason="")
    threading.Thread(target=drive, args=(repo, sid, first, guessed["raw"], limits), daemon=True).start()
    return sid


# -- The turns --------------------------------------------------------------------------

def spent(tokens: dict | None) -> int:
    """What a turn counts against the limit: new input, output and cache
    writes. Cache reads repeat the context on every call and would swamp it."""

    tokens = tokens or {}
    return sum(int(tokens.get(k) or 0) for k in ("in", "out", "cache_write"))


def turn(path: Path, spec: dict, text: str, deadline: float | None) -> tuple[str, int, str]:
    """One turn of the worktree's write session, waited out.

    `(end, tokens, why)`: `end` is `done`, `cut` (the deadline stopped it) or
    `halted` (a person stopped it, or it failed). Writes still wait on a
    person."""

    while True:
        try:
            release = query.hold(work._busy, query._lock, str(path), "", kind="turn")
            break
        except HTTPException:
            # A person's turn there ends first — unless the limit ends before it.
            if deadline is not None and time.time() >= deadline:
                return "cut", 0, "시간 한도"
            time.sleep(1)
    chosen = spec.get("cell") or {}
    try:
        run = work.begin(path, work.session(path, chosen.get("model", ""), chosen.get("effort", "")), text, release)
    except BaseException:
        release()
        raise
    cut = False
    while not run.done:
        with run.wake:
            run.wake.wait(1)
        if deadline is not None and not cut and not run.done and time.time() >= deadline:
            cut = True
            run.halt.set()
            run.chat.stop(run.halt)
    tokens = sum(spent(e["meta"].get("tokens")) for e in run.events if e["kind"] == "done")
    ends = [e for e in run.events if e["kind"] in ("done", "error")]
    if cut:
        return "cut", tokens, "시간 한도"
    if run.halt.is_set() or not ends or ends[-1]["kind"] == "error" or ends[-1]["meta"].get("error"):
        return "halted", tokens, "사람이 멈춤" if run.halt.is_set() else (ends[-1]["text"] if ends else "답이 없다")
    return "done", tokens, ""


def settle(path: Path, first: str) -> list[str]:
    """After a turn: what it left uncommitted is committed, and what it
    changed outside its bounds — a file that was there before (the adapter
    aside), anything outside `.wiki/` — is put back and committed. The paths
    put back."""

    git(path, "add", "-A")
    # A repository may ignore `.wiki/*` and keep only what it lists; the
    # pages are forced in, or `modules/` would silently never reach the PR.
    git(path, "add", "-f", "--", ":(glob).wiki/**/*.md")
    if git(path, "diff", "--cached", "--quiet").returncode:
        git(path, "commit", "-qm", "wiki: 조사 턴이 남긴 것")
    existed = set(git(path, "-c", "core.quotepath=off", "ls-tree", "-r", "--name-only", first).stdout.splitlines())
    changed = git(path, "-c", "core.quotepath=off", "diff", "--name-only", first, "HEAD").stdout.splitlines()
    bad = [f for f in changed if f != ADAPTER and (f in existed or not f.startswith(".wiki/"))]
    if not bad:
        return []
    back = [f for f in bad if f in existed]
    gone = [f for f in bad if f not in existed]
    if back:
        git(path, "checkout", first, "--", *back)
    if gone:
        git(path, "rm", "-q", "-f", "--", *gone)
    git(path, "commit", "-qm", "wiki: 조사 범위 밖의 변경을 되돌림")
    return bad


def drive(repo: Path, sid: str, first: str, raw: float, limits: dict) -> None:
    """The survey's turns in order, the limits read between them; then the
    report turn, whatever stopped the writing. Its end is where the gate
    runs and the pull request goes up (`specs.check`)."""

    name, started = repo.name, time.time()
    deadline = started + limits["survey_minutes"] * 60
    used, why = 0, ""
    try:
        spec = specs.load(name, sid)
        path = Path(spec["worktree"])
        plan = turns(path, modules(files(path)))
        for i, (label, text) in enumerate(plan, 1):
            if used >= limits["survey_tokens"]:
                why = "토큰 한도"
                break
            if time.time() >= deadline:
                why = "시간 한도"
                break
            progress(name, turn=i, turns=len(plan) + 1, label=label, tokens=used)
            lead = LEAD.format(i=i, n=len(plan), schema=channels.WIKI / "SCHEMA.md")
            settled = git(path, "rev-parse", "HEAD").stdout.strip()
            end, tokens, said = turn(path, spec, lead + text, deadline)
            used += tokens
            if end == "halted":
                progress(name, state="멈춤", reason=said, tokens=used)
                return
            if end == "cut":
                # What the cut turn committed goes into the stash with the rest.
                git(path, "reset", "--soft", settled)
                git(path, "stash", "push", "-u", "-m", "wiki: 시간 한도에 끊긴 조사 턴")
                why = said
                break
            reverted = settle(path, first)
            if reverted:
                progress(name, reverted=[*(record(name).get("survey") or {}).get("reverted", []), *reverted])
        progress(name, turn=len(plan) + 1, label="완료 보고", tokens=used, why=why)
        limit = {"토큰 한도": "token", "시간 한도": "time"}.get(why, "")
        end, tokens, said = turn(path, spec, REPORT.format(why=f", stopped at the {limit} limit" if why else ""), None)
        used += tokens
        if not why and end == "done":
            calibrate(raw, used, time.time() - started)
        progress(name, state="끝" if end == "done" else "멈춤", reason=said, tokens=used)
    except Exception as exc:  # a survey that broke still owes the row a reason
        progress(name, state="실패", reason=f"{type(exc).__name__}: {exc}", tokens=used)
