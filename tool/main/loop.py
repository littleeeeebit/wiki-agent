"""loop — a pull request's review rounds, from the first to `[머지]`.

`operator/codex-review-loop`, run between two sessions of this program instead
of two terminals. The review cell is a read-only session in the pull request's
worktree; the work cell is that worktree's write session from `work`. A round:
the server writes the instruction into the hub, the review cell answers, the
server parses the answer, and a refusal goes to the work cell as one turn,
then through the gate and up. It ends at `머지 가능`; a person presses
`[머지]`. Otherwise it stops only for a reason in `Why`.

A loop carries its repository: its spec names the project, the path is found
from that name each round, and nothing here reads the selected project. What
reaches the screen is read by a person and stays Korean; what the cells read
is English.
"""

from __future__ import annotations

import enum
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import ChatSession
from common import worktree_home
from workspace import adopt, folder_for, remove, worktrees

from . import channels, query, specs, work
from .query import ROOT, _lock, current_repo, hold, project, streaming

REVIEW = ROOT / "raw" / "review"
PROMPT = (ROOT / "tool/prompts/review-round.md").read_text(encoding="utf-8")

DEFAULTS = {"rounds": 12, "concurrent": 3, "review_model": ""}
MORE = 4        # rounds a `[계속]` past the cap adds, to that spec only
POLL = 60.0     # seconds between reads of a pull request waiting to merge
# Never `READ_TOOLS`: its `Bash` is on `--allowedTools`, runs unasked, and one
# `python -c` writes anywhere. A prompt does not stop a write; the tool list does.
REVIEW_TOOLS = "Read,Glob,Grep"

LOOPING = re.compile(r"리뷰 대기|리뷰 R\d+|고치는 중 R\d+")


class Why(str, enum.Enum):
    """Every reason a loop stops. The table of the stage 4 plan, and nothing
    else: a stop for a reason not here is a `ValueError`."""

    CAP = "라운드 상한"
    GATE = "게이트"
    DISPUTE = "반론"
    FORMAT = "라운드 형식"
    PERSON = "사람이 멈춤"
    RESTART = "서버 재시작"
    NO_REPO = "저장소 없음"
    NO_WORKTREE = "작업트리 없음"
    WRONG_BASE = "검토하지 않은 base 에 머지됨"
    LEFT_QUEUE = "머지 대기에서 빠짐"


router = APIRouter()


# -- Settings ---------------------------------------------------------------
# Three values beside the translation switch, in `raw/chat/main.json`. The
# settings modal of stage 6 takes them over.

def _file() -> Path:
    return query.LOGS / "main.json"


def settings() -> dict:
    try:
        saved = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    saved = saved if isinstance(saved, dict) else {}
    return {k: saved[k] if type(saved.get(k)) is type(v) else v for k, v in DEFAULTS.items()}


def store(**changes) -> None:
    """Merged into what is there: the translation switch lives in the same file."""

    try:
        saved = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    saved = {**(saved if isinstance(saved, dict) else {}), **changes}
    _file().parent.mkdir(parents=True, exist_ok=True)
    temporary = _file().with_suffix(".tmp")
    temporary.write_text(json.dumps(saved, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(_file())


# -- A round's result --------------------------------------------------------

FIRST = re.compile(r"Round (\d+)\s*[·|—-]\s*PR #(\d+)\s*[·|—-]\s*([0-9a-f]{7,40})\b")
FINDING = re.compile(r"^\[(P0|P1|P2)\] (\S+):(\d+)")
FENCE = r"^```{}[ \t]*\r?\n(.*?)^```"


def bare(line: str) -> str:
    """A line without the markdown a model wraps a protocol line in."""

    return line.strip().strip("*`#> ").strip()


def parse(text: str, n: int, pr: int, head: str) -> dict:
    """`{verdict, findings, counts}`, or `ValueError` saying what is off.

    The first line names the round, the pull request and the head it read; the
    last is the verdict. A finding is a line opening `[P0|P1|P2] path:line`,
    and the lines under it are its body."""

    lines = [line.rstrip() for line in text.strip().splitlines()]
    if not lines:
        raise ValueError("빈 답이다")
    first = FIRST.match(bare(lines[0]))
    if not first:
        raise ValueError("첫 줄이 `Round <n> · PR #<번호> · <머리 커밋>` 이 아니다")
    if int(first[1]) != n:
        raise ValueError(f"라운드 번호가 {first[1]} 다 — {n} 이어야 한다")
    if int(first[2]) != pr:
        raise ValueError(f"PR 번호가 #{first[2]} 다 — #{pr} 이어야 한다")
    if not head.startswith(first[3]):
        raise ValueError(f"머리 커밋이 {first[3]} 다 — {head[:7]} 이어야 한다")
    last = bare(lines[-1])
    if last == "머지 허용":
        verdict = "allow"
    elif last.startswith("머지 불가"):
        verdict = "deny"
    else:
        raise ValueError("마지막 줄이 `머지 허용` 이나 `머지 불가` 가 아니다")
    findings: list[dict] = []
    for line in lines[1:-1]:
        found = FINDING.match(line.strip())
        if found:
            findings.append({"grade": found[1], "file": found[2], "line": int(found[3]),
                             "head": line.strip(), "body": []})
        elif findings:
            findings[-1]["body"].append(line)
    for f in findings:
        f["body"] = "\n".join(f["body"]).strip()
    counts = {g: sum(f["grade"] == g for f in findings) for g in ("P0", "P1", "P2")}
    return {"verdict": verdict, "findings": findings, "counts": counts, "said": last}


def block(name: str, text: str):
    """The JSON of the last fenced block named `name`, or `None`."""

    found = re.findall(FENCE.format(re.escape(name)), text, re.M | re.S)
    try:
        return json.loads(found[-1]) if found else None
    except ValueError:
        return None


def disposed(text: str) -> list[dict] | None:
    """The work cell's `disposition` block, entries that have their shape."""

    items = block("disposition", text)
    if not isinstance(items, list):
        return None
    return [i for i in items if isinstance(i, dict) and isinstance(i.get("finding"), str)
            and i.get("action") in ("fixed", "not-reproduced", "disagree")]


def _key(text: str) -> tuple[str, str, str]:
    """A finding as `(path, line, what)`: the fixing side copies its first
    line, more or less as written."""

    text = re.sub(r"^\s*\[P\d\]\s*", "", text)
    place = re.search(r"(\S+):(\d+)", text)
    what = text[place.end():] if place else text
    what = " ".join(what.strip(" —-:").split()).lower()
    return (place[1], place[2], what) if place else ("", "", what)


def same(a: str, b: str) -> bool:
    """Two mentions of one finding: the same file, and the same line or the
    same words.

    ponytail: a reviewer that rewords a finding and moves its line gets past
    this, and the loop goes on to its cap instead of stopping at the dispute.
    Match on a finding id if the round format ever carries one."""

    ka, kb = _key(a), _key(b)
    return ka[0] == kb[0] and (ka[1] == kb[1] or (bool(ka[2]) and ka[2] == kb[2]))


def disputed(before: list[dict] | None, now: list[dict] | None) -> str:
    """A finding the fixing side disagreed with two rounds in a row, or ``""``."""

    old = [d["finding"] for d in before or [] if d["action"] == "disagree"]
    return next((d["finding"] for d in now or [] if d["action"] == "disagree"
                 and any(same(d["finding"], o) for o in old)), "")


# -- The instruction ----------------------------------------------------------

def counted(spec: dict) -> list[dict]:
    return [r for r in spec.get("rounds") or [] if not r.get("stale")]


def cap(spec: dict) -> int:
    return settings()["rounds"] + spec.get("extra", 0)


def shrinking(rounds: list[dict]) -> bool:
    """Did the P0 and P1 go down at least once over the last two rounds? Too
    few rounds to tell is yes."""

    c = [r["findings"]["P0"] + r["findings"]["P1"] for r in rounds[-3:]]
    return len(c) < 3 or c[2] < c[1] or c[1] < c[0]


def shortstat(path: Path, base: str, head: str) -> str:
    fetched = specs.sh(["git", "fetch", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}"], path, 120)
    if fetched.returncode:
        return f"(could not fetch `{base}`: {specs.said(fetched)})"
    done = specs.sh(["git", "diff", "--shortstat", f"origin/{base}...{head}"], path)
    return (done.stdout.strip() or "(no change)") if not done.returncode else f"(failed: {specs.said(done)})"


def instruction(spec: dict, path: Path, n: int, head: str, base: str, codex: bool) -> str:
    """What `codex-review-loop` says an instruction must carry, with the
    result going to the final answer instead of a file."""

    pr = spec["pr"]["number"]
    rounds = counted(spec)
    last = rounds[-1] if rounds else None
    gated = spec.get("gate") or {}
    out = [
        f"Round {n} · PR #{pr} · {head[:7]}", "",
        f"Review pull request #{pr} as it stands at head `{head}` against base `{base}`. Your final "
        "answer is the result, and its first line is the line above, exactly. Write no file.", "",
        "## Allowed", "",
        "- reading files · `git log`, `git show`, `git diff` · `gh pr view`, `gh pr diff` · running the tests, "
        "when your tools let you", "",
        "## Forbidden", "",
        "- `checkout`, `switch`, `stash`, `merge`, `rebase`, `reset`, `cherry-pick`, `commit`, `push` · "
        "installing packages · editing any file", "",
        "## What changed", "",
        f"- `git diff --shortstat {base}...{head[:7]}`: {shortstat(path, base, head)}",
    ]
    if last:
        out.append(f"- Since round {last['n']}: `{last['head'][:7]}..{head[:7]}`")
    out += ["", "## What became of the last round's findings", ""]
    if last is None:
        out.append("(first round)")
    elif last.get("disposition") is None:
        out.append("(the fixing side gave no disposition)")
    else:
        out += ["The fixing side's disposition, as it gave it:", "", "```json",
                json.dumps(last["disposition"], ensure_ascii=False, indent=2), "```"]
    for ruling in spec.get("rulings") or []:
        out.append(f"- A person settled a disputed finding: {ruling}")
    out += ["", "## Already run", ""]
    if gated.get("head") == head:
        tail = (gated.get("tail") or "").splitlines()[-1:] or [""]
        out.append(f"- The server ran the gate `{gated['cmd']}` in the worktree at `{head[:7]}`: "
                   f"{'passed' if gated['ok'] else 'failed — ' + gated['reason']}. Last line: `{tail[0]}`")
    else:
        out.append("- Nothing at this head yet.")
    out += ["", "## Deferred P2", ""]
    out += [f"- {d}" for d in spec.get("deferred") or []] or ["(none)"]
    out += ["", "Do not report a P2 listed here again without new grounds or a change of grade."]
    if not shrinking(rounds):
        out += ["", "## Sort the findings by family first", "",
                "The P0 and P1 have not gone down for two rounds. Before this round's findings, pair the "
                "findings so far into a table, one row each: finding → the repair made → what came back next "
                "round. Sort them into the families `operator/codex-review-loop` names — the wrong model, "
                "where it measures, the claim is wrong, knew and skipped — and report by family, the cause "
                "first."]
    if not codex:
        # A Claude cell has no shell to run `gh pr diff` with.
        diff = specs.sh(["gh", "pr", "diff", str(pr)], path, 120)
        out += ["", f"## `gh pr diff {pr}`", "", "```diff",
                diff.stdout.rstrip() if not diff.returncode else f"(failed: {specs.said(diff)})", "```"]
    out += ["", "## Report", "",
            "First line as above. Then one block per finding, opening `[P0|P1|P2] path:line — what / when / "
            "why`, with trigger, defect, impact and reproducible evidence under it. With nothing wrong, the "
            "one line `새 발견 없음`. The last line is exactly `머지 허용`, or `머지 불가 — <reason>`."]
    return "\n".join(out) + "\n"


def fixing(n: int, findings: list[dict], said: str) -> str:
    """The work cell's turn: what `codex-review-loop` says the receiving side holds."""

    listed = "\n\n".join(f["head"] + (f"\n{f['body']}" if f["body"] else "") for f in findings)
    return (
        f"Review round {n} refused the merge (`{said}`) and found the following. For each finding: "
        "reproduce it first. Fix it where it "
        "points, and count separately the other places the same rule applies to. If you do not agree, "
        "say why with evidence rather than changing the code. Commit what you change; do not push.\n\n"
        "End the answer with a fenced block whose info string is `disposition`, holding a JSON list with "
        "one entry per finding, in order: `{\"finding\": \"<its first line, copied>\", \"action\": "
        "\"fixed\" | \"not-reproduced\" | \"disagree\", \"evidence\": \"<what you ran and saw>\"}`.\n\n"
        + (listed or "(no P0 or P1 — the verdict above is the whole of it)"))


# -- The review cell -----------------------------------------------------------

_cells: dict[tuple[str, int], ChatSession] = {}   # (repo, pr) -> its review cell


def review_model() -> str:
    """The model chosen in the settings, else Codex's default."""

    chosen = settings()["review_model"]
    if chosen:
        return chosen
    models = channels.codex_models()
    return next((m["id"] for m in models if m.get("is_default")), models[0]["id"])


def folder(repo: str, pr: int) -> Path:
    return REVIEW / repo / str(pr)


def cell(spec: dict, path: Path) -> ChatSession:
    """The pull request's review cell, made once and kept while the pull
    request lives. Its CLI session id is kept beside the rounds, so a server
    started again goes on in the same conversation."""

    key = (spec["repo"], spec["pr"]["number"])
    with _lock:
        chat = _cells.get(key)
    if chat is not None:
        return chat
    # Outside the lock: listing Codex's models starts Codex.
    chat = ChatSession(path, tools=REVIEW_TOOLS, system=PROMPT, model=review_model(), effort="high")
    try:
        saved = json.loads((folder(*key) / "session.json").read_text(encoding="utf-8"))
        if saved.get("provider") == ("codex" if chat.is_codex else "claude"):
            chat.session_id = saved.get("session_id")
    except (OSError, ValueError, AttributeError):
        pass
    with _lock:
        _cells[key] = chat
    return chat


def close_cell(repo: str, pr: int) -> None:
    with _lock:
        chat = _cells.pop((repo, pr), None)
    if chat:
        chat.close()


def close_all() -> None:
    with _lock:
        alive = list(_cells.values())
        _cells.clear()
        loops = list(_loops.values())
    for loop in loops:
        loop.stop()
    for chat in alive:
        chat.close()


# -- One loop -------------------------------------------------------------------

class Loop:
    """One spec's loop, on its own thread. `halt` is its stop, read between
    every step and handed to whatever it waits on."""

    def __init__(self, repo: str, sid: str) -> None:
        self.repo, self.sid = repo, sid
        self.halt = threading.Event()
        self.chat: ChatSession | None = None   # the review cell's turn, while one runs
        self.run: work.Run | None = None        # the work cell's turn, while one runs
        self.thread: threading.Thread | None = None

    def stop(self) -> None:
        self.halt.set()   # first: whatever starts after this sees it
        chat, run = self.chat, self.run
        if chat is not None:
            chat.stop(self.halt)
        if run is not None and not run.done:
            run.halt.set()
            run.chat.stop(run.halt)


_loops: dict[tuple[str, str], Loop] = {}
_seats = threading.Condition()
_seated = 0


def kick(repo: str, sid: str) -> None:
    """Put the spec in `리뷰 대기` and start its loop, unless one runs. A loop
    that was stopped is waited for first, so two never drive one spec."""

    key = (repo, sid)
    with _lock:
        old = _loops.get(key)
    if old is not None and not old.halt.is_set():
        return
    if old is not None and old.thread is not None:
        old.thread.join(30)
        if old.thread.is_alive():
            raise HTTPException(409, "앞 루프가 아직 멈추는 중이다. 잠시 뒤에 다시")
    with specs._files:
        spec = specs.load(repo, sid)
        if spec is None:
            raise HTTPException(404, "그런 명세가 없다")
        if spec["state"] != "리뷰 대기" or spec.get("stopped"):
            specs.save(specs.moved(spec, "리뷰 대기", stopped=None))
    loop = Loop(repo, sid)
    with _lock:
        if _loops.get(key) not in (None, old):
            return
        _loops[key] = loop
    loop.thread = threading.Thread(target=drive, args=(loop,), daemon=True)
    loop.thread.start()


def drive(loop: Loop) -> None:
    global _seated
    try:
        # A seat: at most `concurrent` loops run rounds at once. Read each
        # time, so a changed setting applies to the next loop that waits.
        with _seats:
            while _seated >= settings()["concurrent"]:
                if loop.halt.is_set():
                    return
                _seats.wait(1)
            _seated += 1
        try:
            while step(loop):
                pass
        finally:
            with _seats:
                _seated -= 1
                _seats.notify_all()
    except Exception as exc:  # a loop that broke still owes the spec a reason
        stop(loop, loop.repo, loop.sid, Why.FORMAT, f"루프가 깨졌다 — {type(exc).__name__}: {exc}")
    finally:
        with _lock:
            if _loops.get((loop.repo, loop.sid)) is loop:
                del _loops[(loop.repo, loop.sid)]


def change(loop: Loop, state: str | None = None, **fields) -> dict | None:
    """Move the spec as the loop. `None` when the loop no longer owns it: a
    person stopped it, or it left the loop some other way (merged on GitHub).
    Checked under the files' lock, so a stop that landed first is never
    written over."""

    with specs._files:
        if loop.halt.is_set():
            return None
        spec = specs.load(loop.repo, loop.sid)
        if spec is None or not (LOOPING.fullmatch(spec["state"]) or (state and spec["state"] == state)):
            return None
        if state and state != spec["state"]:
            specs.moved(spec, state, **fields)
        else:
            spec.update(fields)
        specs.save(spec)
        return spec


WAITING = re.compile(r"머지 대기")


def stop(loop: Loop | None, repo: str, sid: str, why: Why, detail: str = "", source: re.Pattern = LOOPING) -> bool:
    """`멈춤`, for a reason of the table, from a state `source` matches —
    running states unless said otherwise. Always `False`, for `return stop(…)`.

    The state is read under the files' lock, where it is written: a caller
    that saw a running state earlier may have lost the race to the loop, and a
    stop written over `머지 가능` threw the allowed round away."""

    if not isinstance(why, Why):
        raise ValueError(f"표에 없는 멈춤 이유다: {why!r}")
    with specs._files:
        if loop is not None and loop.halt.is_set():
            return False
        spec = specs.load(repo, sid)
        if spec is not None and source.fullmatch(spec["state"]):
            specs.save(specs.moved(spec, "멈춤", stopped={"reason": why.value, "detail": detail}))
    return False


def gh_json(repo: Path, args: list[str]) -> dict:
    done = specs.sh(["gh", *args], repo, 60)
    if done.returncode:
        raise RuntimeError(f"gh {args[0]} {args[1]} 실패 — {specs.said(done)}")
    return json.loads(done.stdout)


def pr_head(repo: Path, n: int) -> tuple[str, str]:
    view = gh_json(repo, ["pr", "view", str(n), "--json", "headRefOid,baseRefName"])
    return view["headRefOid"], view["baseRefName"]


def wait_hold(loop: Loop, path: Path):
    """The worktree, once nobody else holds it — a person's turn ends first."""

    while not loop.halt.is_set():
        try:
            return hold(work._busy, _lock, str(path), "", kind="turn")
        except HTTPException:
            loop.halt.wait(1)
    return None


def ask(loop: Loop, chat: ChatSession, text: str) -> str:
    """One turn of the review cell: its final answer, or `RuntimeError`."""

    final, failed = "", ""
    loop.chat = chat
    try:
        for ev in chat.say(text, loop.halt):
            if ev.kind == "done":
                final = ev.text
                failed = (final or "완료된 답이 없다") if ev.meta.get("error") else ""
                if ev.meta.get("session_id"):
                    kept = folder(loop.repo, specs.load(loop.repo, loop.sid)["pr"]["number"]) / "session.json"
                    kept.parent.mkdir(parents=True, exist_ok=True)
                    kept.write_text(json.dumps({"session_id": ev.meta["session_id"],
                                                "provider": "codex" if chat.is_codex else "claude"}) + "\n",
                                    encoding="utf-8")
            elif ev.kind == "error":
                failed = ev.text
    finally:
        loop.chat = None
    if failed or not final.strip():
        raise RuntimeError(failed or "빈 답이다")
    return final


def told(loop: Loop, spec: dict, path: Path, text: str) -> str | None:
    """One turn of the work cell, as a person's turn would run: every write it
    asks for waits on a person. Its final answer; `None` when stopped.

    While it waits on an approval the spec says so to the screens, once each
    time that changes."""

    release = wait_hold(loop, path)
    if release is None:
        return None
    try:
        chosen = spec.get("cell") or {}
        run = work.begin(path, work.session(path, chosen.get("model", ""), chosen.get("effort", "")), text, release)
    except BaseException:
        release()
        raise
    loop.run, asked = run, False
    try:
        while not run.done:
            with run.wake:
                run.wake.wait(1)
            now = work.waiting(str(path))
            if now != asked:
                asked = now
                specs.publish(specs.load(loop.repo, loop.sid) or spec)
    finally:
        loop.run = None
    if loop.halt.is_set():
        return None
    end = next((e for e in reversed(run.events) if e["kind"] in ("done", "error")), None)
    return end["text"] if end and end["kind"] == "done" else ""


def shipped(loop: Loop, spec: dict, repo: Path, path: Path, head: str) -> bool:
    """The head that goes to review passed the gate here, and is up.

    A push made elsewhere to the branch is taken in by fast-forward first, so
    the files the review cell reads are the head it reviews. Nothing more to
    do when the worktree is clean at the pull request's head and the gate
    already passed there. Otherwise the gate runs; a failure goes to the work
    cell once with the output's tail, and two failures in a row stop. Commits
    the pull request lacks are pushed once the gate passes."""

    cmd, branch = spec["done"][0], specs.branch_of(spec)
    for attempt in (1, 2):
        release = wait_hold(loop, path)
        if release is None:
            return False
        try:
            specs.sh(["git", "fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"], path, 120)
            local = specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip()
            if local != head and not specs.sh(["git", "merge-base", "--is-ancestor", local, head], path).returncode:
                specs.sh(["git", "merge", "--ff-only", head], path, 60)
                local = specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip()
            clean = not specs.sh(["git", "status", "--porcelain"], path).stdout.strip()
            gated = spec.get("gate") or {}
            if local == head and clean and gated.get("head") == head and gated.get("ok"):
                return True
            verdict = specs.judge(path, cmd, loop.halt)
            pushed = None
            if verdict["ok"] and verdict["head"] != head and not loop.halt.is_set():
                pushed = specs.sh(["git", "push", "origin", branch], path, 120)
        finally:
            release()
        spec = change(loop, gate=verdict)
        if spec is None:
            return False
        if verdict["ok"]:
            if pushed is not None and pushed.returncode:
                return stop(loop, loop.repo, loop.sid, Why.GATE, f"push 실패 — {specs.said(pushed)}")
            return True
        if attempt == 2:
            return stop(loop, loop.repo, loop.sid, Why.GATE, f"게이트가 두 번 연속 실패했다 — {verdict['reason']}")
        text = (f"The server ran the gate `{cmd}` in this worktree and it failed: {verdict['reason']}. The end "
                f"of its output:\n\n```\n{verdict['tail']}\n```\n\nFix it and commit. Do not push.")
        if told(loop, spec, path, text) is None:
            return False
        spec = specs.load(loop.repo, loop.sid) or spec
    return False


def step(loop: Loop) -> bool:
    """One round. `True` when another follows."""

    spec = specs.load(loop.repo, loop.sid)
    if spec is None or loop.halt.is_set() or not LOOPING.fullmatch(spec["state"]):
        return False
    repo = channels.repo_for(spec["repo"])
    if repo is None:
        return stop(loop, loop.repo, loop.sid, Why.NO_REPO, f"`{spec['repo']}` 가 작업 공간에 없다")
    path = Path(spec.get("worktree") or "")
    try:
        listed = {row["path"] for row in worktrees(repo)}
    except ValueError as exc:
        return stop(loop, loop.repo, loop.sid, Why.NO_REPO, str(exc))
    if not spec.get("worktree") or path.resolve() not in listed:
        return stop(loop, loop.repo, loop.sid, Why.NO_WORKTREE, f"`{path.name}` 가 `{repo.name}` 의 작업트리 목록에 없다")
    rounds, pr = counted(spec), spec["pr"]["number"]
    n = len(rounds) + 1
    if n > cap(spec):
        return stop(loop, loop.repo, loop.sid, Why.CAP, f"{cap(spec)} 라운드를 다 돌았다")

    head, base = pr_head(repo, pr)
    if not shipped(loop, spec, repo, path, head):
        return False
    head, base = pr_head(repo, pr)
    spec = change(loop, f"리뷰 R{n}")
    if spec is None:
        return False
    chat = cell(spec, path)
    kept = folder(spec["repo"], pr)
    kept.mkdir(parents=True, exist_ok=True)
    order = kept / f"round-{n}.md"
    order.write_text(instruction(spec, path, n, head, base, chat.is_codex), encoding="utf-8")

    parsed, why, ask_for = None, "", f"Read `{order}` and review."
    for _ in range(2):
        try:
            answer = ask(loop, chat, ask_for)
        except RuntimeError as exc:
            if loop.halt.is_set():
                return False
            why = f"리뷰 셀이 답하지 못했다 — {exc}"
            continue
        (kept / f"round-{n}-result.md").write_text(answer, encoding="utf-8")
        try:
            parsed = parse(answer, n, pr, head)
            break
        except ValueError as exc:
            why = str(exc)
            ask_for = (f"Your answer to round {n} could not be read: {why}. Answer round {n} again, in the "
                       f"shape the instruction `{order}` gives: first line `Round {n} · PR #{pr} · {head[:7]}`, "
                       "last line `머지 허용` or `머지 불가 — <reason>`.")
    if parsed is None:
        return stop(loop, loop.repo, loop.sid, Why.FORMAT, why)

    record = {"n": n, "head": head, "base": base, "findings": parsed["counts"], "verdict": parsed["verdict"],
              "gate": {k: (spec.get("gate") or {}).get(k) for k in ("ok", "cmd", "head")},
              "disposition": None, "ts": time.time()}
    moved = pr_head(repo, pr)
    if moved != (head, base):
        # Someone pushed or changed the base while it was read: the verdict
        # is about code that is no longer the pull request. Kept, not counted.
        for name in (f"round-{n}.md", f"round-{n}-result.md"):
            if (kept / name).exists():
                (kept / name).replace(kept / name.replace(f"round-{n}", f"round-{n}-stale-{head[:7]}"))
        return change(loop, rounds=[*spec["rounds"], {**record, "stale": True}]) is not None

    deferred = list(spec.get("deferred") or [])
    for f in parsed["findings"]:
        if f["grade"] == "P2" and not any(same(f["head"], d) for d in deferred):
            deferred.append(f["head"])
    spec = change(loop, rounds=[*spec["rounds"], record], deferred=deferred)
    if spec is None:
        return False
    if parsed["verdict"] == "allow":
        kept_p2 = pick(loop, chat, deferred)
        comment = ("리뷰에서 남긴 P2 — 따로 할 만한 것\n\n" + "\n".join(f"- {p}" for p in kept_p2)) if kept_p2 else ""
        change(loop, "머지 가능", p2=kept_p2, p2_comment=comment)
        return False

    spec = change(loop, f"고치는 중 R{n}")
    if spec is None:
        return False
    serious = [f for f in parsed["findings"] if f["grade"] != "P2"]
    answer = told(loop, spec, path, fixing(n, serious, parsed["said"]))
    if answer is None:
        return False
    disposition = disposed(answer)
    spec = change(loop, rounds=[*spec["rounds"][:-1], {**spec["rounds"][-1], "disposition": disposition}])
    if spec is None:
        return False
    before = rounds[-1].get("disposition") if rounds else None
    stuck = disputed(before, disposition)
    if stuck:
        return stop(loop, loop.repo, loop.sid, Why.DISPUTE, f"두 라운드 연속 반대 — {stuck}")
    return shipped(loop, spec, repo, path, head)


def pick(loop: Loop, chat: ChatSession, deferred: list[str]) -> list[str]:
    """The deferred P2 worth a follow-up, as the review cell picks them."""

    if not deferred:
        return []
    try:
        answer = ask(loop, chat, (
            "The review allowed the merge. Go through the deferred P2 below once and keep only what is worth "
            "doing as a follow-up; drop minor style and taste. End with the `p2-keep` block.\n\n"
            + "\n".join(f"- {d}" for d in deferred)))
    except RuntimeError:
        return []
    kept = block("p2-keep", answer)
    return [k.strip() for k in kept if isinstance(k, str) and k.strip()] if isinstance(kept, list) else []


def recover() -> None:
    """At start-up: a loop that was running when the server went down stopped
    with it. It does not start again by itself — nobody knows what changed
    meanwhile — and one `[계속]` takes it on."""

    for repo in specs.SPECS.glob("*"):
        if repo.is_dir():
            for spec in specs.listing(repo.name):
                if LOOPING.fullmatch(spec["state"]):
                    stop(None, repo.name, spec["id"], Why.RESTART)


# -- Merging -----------------------------------------------------------------------

_landing = threading.Lock()   # one reading of the merge table at a time


def in_queue(repo: Path, n: int) -> bool:
    """`gh pr view --json` has no `isInMergeQueue`; GraphQL does."""

    query_text = ("query($owner:String!,$name:String!,$n:Int!){repository(owner:$owner,name:$name)"
                  "{pullRequest(number:$n){isInMergeQueue}}}")
    found = gh_json(repo, ["api", "graphql", "-F", "owner={owner}", "-F", "name={repo}", "-F", f"n={n}",
                           "-f", f"query={query_text}"])
    return bool(found["data"]["repository"]["pullRequest"]["isInMergeQueue"])


def comment(repo: Path, n: int, body: str) -> str:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
        fh.write(body)
    try:
        done = specs.sh(["gh", "pr", "comment", str(n), "--body-file", fh.name], repo, 60)
    finally:
        os.unlink(fh.name)
    return "" if not done.returncode else specs.said(done)


def landed(repo: Path, spec: dict) -> None:
    """What became of a pull request `[머지]` handed to GitHub. The exit code
    of `gh pr merge` is not a merge: with a merge queue it only enables
    auto-merge or queues the pull request. One `state` and one condition
    split every case; a read that fails changes nothing and is tried again."""

    with _landing:
        spec = specs.load(repo.name, spec["id"])
        if spec is None or spec["state"] != "머지 대기":
            return
        n = spec["pr"]["number"]
        allowed = specs.approved(spec) or {"base": spec["pr"].get("base"), "head": spec["pr"].get("head", "")}
        try:
            view = gh_json(repo, ["pr", "view", str(n), "--json", "state,mergeCommit,baseRefName,autoMergeRequest"])
            state = view["state"]
            queued = state == "OPEN" and (bool(view.get("autoMergeRequest")) or in_queue(repo, n))
        except (RuntimeError, ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired):
            return
        commit = (view.get("mergeCommit") or {}).get("oid", "")
        if state == "MERGED" and view.get("baseRefName") != allowed["base"]:
            # Kept whole — worktree, branches, review cell — so a person can undo it.
            specs.update(repo.name, spec["id"], merge={"commit": commit, "base": view.get("baseRefName")})
            stop(None, repo.name, spec["id"], Why.WRONG_BASE,
                 f"리뷰는 `{allowed['base']}` 를 봤는데 `{view.get('baseRefName')}` 에 머지됐다", WAITING)
            comment(repo, n, f"이 PR 은 리뷰가 허용한 base `{allowed['base']}` 가 아니라 `{view.get('baseRefName')}` 에 "
                             "머지됐다. 작업트리와 브랜치는 그대로 두었다.")
        elif state == "MERGED":
            finish(repo, spec, view["baseRefName"], commit,
                   f"PR #{n} 머지됨 — 라운드 {len(counted(spec))}, 남은 P2 {len(spec.get('p2') or [])}")
        elif state == "OPEN" and not queued:
            stop(None, repo.name, spec["id"], Why.LEFT_QUEUE, "PR 이 열려 있는데 대기열에도 없고 자동 머지도 꺼졌다",
                 WAITING)
        elif state == "CLOSED":
            stop(None, repo.name, spec["id"], Why.LEFT_QUEUE, "PR 이 닫혔다", WAITING)


def forward(repo: Path, base: str) -> str:
    """The original checkout moves only when it stands on the base, clean,
    and only by fast-forward. It is the second of the two writes the server
    makes to an original checkout."""

    specs.sh(["git", "fetch", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}"], repo, 120)
    on = specs.sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
    dirty = specs.sh(["git", "status", "--porcelain"], repo)
    if on != base or dirty.returncode or dirty.stdout.strip():
        return f"원본이 뒤처짐 — 원본이 `{base}` 에 깨끗이 서 있지 않다"
    done = specs.sh(["git", "merge", "--ff-only", f"origin/{base}"], repo, 60)
    return f"원본을 `origin/{base}` 로 앞으로 옮겼다" if not done.returncode else f"원본이 뒤처짐 — {specs.said(done)}"


def cleared(repo: Path, path: Path) -> str:
    try:
        release = hold(work._busy, _lock, str(path), "", kind="turn")
    except HTTPException:
        return "작업트리가 쓰이고 있어 남겼다"
    try:
        work.forget(path)
        return remove(repo, path)
    except (ValueError, RuntimeError) as exc:
        return f"작업트리를 지우지 못했다 — {exc}"
    finally:
        release()


def pruned(repo: Path, branch: str, approved: str) -> str:
    """The remote branch goes only while it still stands on the merged
    commit. Deleted unconditionally, a push made after the merge was lost."""

    done = specs.sh(["git", "push", f"--force-with-lease=refs/heads/{branch}:{approved}", "origin",
                     "--delete", branch], repo, 120)
    if not done.returncode:
        return f"원격 브랜치 `{branch}` 를 지웠다"
    if "remote ref does not exist" in (done.stderr or ""):
        return f"원격 브랜치 `{branch}` 는 이미 없다"
    return f"원격 브랜치에 새 커밋 — 남김 ({specs.said(done)})"


def finish(repo: Path, spec: dict, base: str, commit: str, text: str) -> None:
    """After a merge: the P2 comment, `머지됨` and its result row, then the
    cleanup — only ever from `머지됨`."""

    n, allowed = spec["pr"]["number"], specs.approved(spec)
    notes = []
    if spec.get("p2_comment"):
        failed = comment(repo, n, spec["p2_comment"])
        notes.append(f"P2 코멘트를 달지 못했다 — {failed}" if failed else "P2 코멘트를 달았다")
    with specs._files:
        spec = specs.load(repo.name, spec["id"])
        specs.save(specs.moved(spec, "머지됨", stopped=None,
                               merge={"commit": commit, "base": base}))
    specs.told(repo, spec, text)
    notes.append(forward(repo, base))
    if spec.get("worktree"):
        notes.append(cleared(repo, Path(spec["worktree"])))
    notes.append(pruned(repo, specs.branch_of(spec), allowed["head"] if allowed else spec["pr"].get("head", "")))
    close_cell(spec["repo"], n)
    specs.update(repo.name, spec["id"], cleanup=notes)


def poll() -> None:
    """The pull requests in `머지 대기`, read again every minute."""

    while True:
        time.sleep(POLL)
        for repo in specs.SPECS.glob("*"):
            path = channels.repo_for(repo.name)
            if path is None:
                continue
            for spec in specs.listing(repo.name):
                if spec["state"] == "머지 대기":
                    try:
                        landed(path, spec)
                    except Exception:   # the next minute tries again
                        pass


# -- The screen ----------------------------------------------------------------------

def mine(sid: str) -> tuple[Path, dict]:
    repo = current_repo()
    spec = specs.load(repo.name, sid)
    if spec is None:
        raise HTTPException(404, "그런 명세가 없다")
    return repo, spec


class Merge(BaseModel):
    head: str


@router.post("/api/specs/{sid}/merge")
def merge(sid: str, body: Merge) -> dict:
    """`[머지]`, bound to the head the review allowed — not the screen's.

    The screen's head is compared with it first: a commit pushed after the
    allow and a list read again would otherwise make an unreviewed head the
    screen's. Then `--match-head-commit` lets GitHub refuse atomically a push
    between this check and the merge. The base is only checked; GitHub cannot
    bind it, and `landed` catches a base that moved in the seconds between."""

    repo, spec = mine(sid)
    if spec["state"] != "머지 가능":
        raise HTTPException(409, f"머지할 수 있는 상태가 아니다 — {spec['state']}")
    allowed = specs.approved(spec)
    if allowed is None:
        raise HTTPException(409, "리뷰가 허용한 라운드가 없다")
    n = spec["pr"]["number"]
    if body.head != allowed["head"]:
        kick(repo.name, sid)
        raise HTTPException(409, "리뷰 뒤 새 커밋 — 새 라운드를 받는다")
    try:
        _, base = pr_head(repo, n)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(502, str(exc)) from exc
    if base != allowed["base"]:
        kick(repo.name, sid)
        raise HTTPException(409, "리뷰 뒤 base 변경 — 새 라운드를 받는다")
    done = specs.sh(["gh", "pr", "merge", str(n), "--squash", "--match-head-commit", allowed["head"]], repo, 120)
    if done.returncode:
        try:
            moved = pr_head(repo, n)[0] != allowed["head"]
        except (RuntimeError, ValueError):
            moved = False
        if moved:
            kick(repo.name, sid)
            raise HTTPException(409, "리뷰 뒤 새 커밋 — GitHub 이 머지를 거절했다. 새 라운드를 받는다")
        raise HTTPException(409, f"머지하지 못했다 — {specs.said(done)}")
    with specs._files:
        spec = specs.load(repo.name, sid)
        specs.save(specs.moved(spec, "머지 대기"))
    landed(repo, spec)
    return specs.view(repo, specs.load(repo.name, sid))


class Settle(BaseModel):
    choice: Literal["accept", "reopen"]


@router.post("/api/specs/{sid}/settle")
def settle(sid: str, body: Settle) -> dict:
    """The end of a merge into a base the review did not see. `accept` takes
    that merge and cleans up; `reopen` sends the same branch to the base it
    was reviewed for, as a new pull request with a new review."""

    repo, spec = mine(sid)
    if spec["state"] != "멈춤" or (spec.get("stopped") or {}).get("reason") != Why.WRONG_BASE.value:
        raise HTTPException(409, "검토하지 않은 base 에 머지된 명세만 이렇게 끝낸다")
    n, actual = spec["pr"]["number"], (spec.get("merge") or {}).get("base") or ""
    if body.choice == "accept":
        finish(repo, spec, actual, (spec.get("merge") or {}).get("commit", ""),
               f"검토하지 않은 base `{actual}` 로 머지됨 — 받아들임")
        return specs.view(repo, specs.load(repo.name, sid))
    allowed = specs.approved(spec)
    with specs._files:
        spec = specs.load(repo.name, sid)
        # Rounds and their allow belong to one pull request.
        spec["history"].append({"ts": time.time(), "pr": n, "rounds": spec.get("rounds") or [],
                                "closed": Why.WRONG_BASE.value})
        specs.save(specs.moved(spec, "작업 중", pr=None, stopped=None, rounds=[], extra=0, deferred=[],
                               rulings=[], p2=[], p2_comment="", merge=None,
                               base=allowed["base"] if allowed else spec["pr"]["base"],
                               branch=specs.branch_of(spec)))
    close_cell(repo.name, n)
    return specs.view(repo, specs.load(repo.name, sid))


class Resume(BaseModel):
    note: str = ""


def proceed(repo: Path, spec: dict, note: str) -> dict:
    """`[계속]`, as the table says for each reason."""

    sid = spec["id"]
    try:
        why = Why((spec.get("stopped") or {}).get("reason"))
    except ValueError as exc:
        raise HTTPException(409, "멈춘 이유를 모른다") from exc
    if why is Why.WRONG_BASE:
        raise HTTPException(409, "이어 가지 않는다 — [받아들임] 이나 [다시 PR] 로 끝낸다")
    if why is Why.DISPUTE:
        if not note:
            raise HTTPException(400, "그 발견에 정한 것을 적어야 잇는다")
        specs.update(repo.name, sid, rulings=[*(spec.get("rulings") or []), note])
    elif why is Why.CAP:
        specs.update(repo.name, sid, extra=spec.get("extra", 0) + MORE)
    elif why is Why.NO_REPO and channels.repo_for(spec["repo"]) is None:
        raise HTTPException(409, f"`{spec['repo']}` 가 아직 작업 공간에 없다")
    elif why is Why.NO_WORKTREE:
        view = gh_or_502(repo, ["pr", "view", str(spec["pr"]["number"]), "--json", "headRefName,headRefOid"])
        path = adopted(repo, view["headRefName"], view["headRefOid"])
        specs.update(repo.name, sid, worktree=str(path), pr={**spec["pr"], "branch": view["headRefName"]})
    elif why is Why.LEFT_QUEUE:
        view = gh_or_502(repo, ["pr", "view", str(spec["pr"]["number"]), "--json", "state,headRefOid,baseRefName"])
        allowed = specs.approved(spec)
        if view["state"] == "CLOSED":
            raise HTTPException(409, "GitHub 에서 PR 을 다시 열어야 한다")
        with specs._files:
            fresh = specs.load(repo.name, sid)
            if view["state"] == "MERGED":
                specs.save(specs.moved(fresh, "머지 대기", stopped=None))
            elif allowed and (view["headRefOid"], view["baseRefName"]) == (allowed["head"], allowed["base"]):
                specs.save(specs.moved(fresh, "머지 가능", stopped=None))
                return specs.view(repo, fresh)
        if view["state"] == "MERGED":
            landed(repo, fresh)
            return specs.view(repo, specs.load(repo.name, sid))
    kick(repo.name, sid)
    return specs.view(repo, specs.load(repo.name, sid))


def gh_or_502(repo: Path, args: list[str]) -> dict:
    try:
        return gh_json(repo, args)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(502, str(exc)) from exc


def adopted(repo: Path, branch: str, oid: str) -> Path:
    """`workspace.adopt`, held like making a worktree: the switch waits."""

    release = hold(work._busy, _lock, str(worktree_home(repo) / folder_for(branch)), "그 작업트리를 다른 요청이 쓰고 있다")
    try:
        return adopt(repo, branch, oid)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    finally:
        release()


@router.post("/api/specs/{sid}/resume")
def resume(sid: str, body: Resume) -> dict:
    repo, spec = mine(sid)
    if spec["state"] != "멈춤":
        raise HTTPException(409, "멈춘 명세만 잇는다")
    return proceed(repo, spec, body.note.strip())


@router.post("/api/specs/{sid}/halt")
def halt(sid: str) -> dict:
    """`[멈춤]` of a running loop: the turn it waits on is stopped too."""

    repo, spec = mine(sid)
    if not LOOPING.fullmatch(spec["state"]):
        raise HTTPException(409, "도는 루프가 아니다")
    with _lock:
        loop = _loops.get((repo.name, sid))
    if loop is not None:
        loop.stop()
    stop(None, repo.name, sid, Why.PERSON)
    spec = specs.load(repo.name, sid)
    if spec["state"] != "멈춤":
        # The loop got to its end first; that end stands.
        raise HTTPException(409, f"루프가 먼저 끝났다 — {spec['state']}")
    return specs.view(repo, spec)


def refusal(row: dict, spec: dict | None) -> str:
    """Why a pull request cannot go into a loop from the list, or ``""``."""

    if row.get("isCrossRepository"):
        return "포크 — 푸시할 곳이 없다"
    if spec is None:
        return ""
    reason = (spec.get("stopped") or {}).get("reason")
    if spec["state"] != "멈춤":
        return f"이미 {spec['state']}"
    if reason == Why.WRONG_BASE.value:
        return "명세에서 [받아들임] 이나 [다시 PR] 로 끝낸다"
    if reason == Why.DISPUTE.value:
        return "반론 — 명세에서 정한 것을 적고 잇는다"
    return ""


def by_pr(name: str) -> dict[int, dict]:
    return {s["pr"]["number"]: s for s in specs.listing(name) if s.get("pr")}


@router.get("/api/prs")
def prs() -> dict:
    """The selected project's open pull requests, and which can go into a loop."""

    with _lock:
        name, repo = project(), current_repo()
    done = specs.sh(["gh", "pr", "list", "--state", "open", "--json",
                     "number,title,headRefName,headRefOid,headRepositoryOwner,isCrossRepository,url"], repo, 60)
    if done.returncode:
        return {"project": name, "rows": [], "error": specs.said(done)}
    known = by_pr(name)
    rows = []
    for row in json.loads(done.stdout):
        spec = known.get(row["number"])
        why = refusal(row, spec)
        rows.append({"number": row["number"], "title": row["title"], "branch": row["headRefName"],
                     "head": row["headRefOid"], "url": row["url"], "fork": bool(row.get("isCrossRepository")),
                     "spec": spec["id"] if spec else None, "state": spec["state"] if spec else None,
                     "why": why, "pickable": not why})
    return {"project": name, "rows": rows}


def minimal(repo: Path, view: dict, path: Path, gate: str) -> dict:
    """A spec for a pull request that came without one: its title is the
    goal, its `변경 이유` the decisions, the gate the only done item."""

    reasons = re.search(r"^## 변경 이유[ \t]*$(.*?)(?=^## |\Z)", view.get("body") or "", re.M | re.S)
    decided = [{"what": " ".join(line[2:].split()), "why": "", "rejected": ""}
               for line in (reasons[1].splitlines() if reasons else []) if line.startswith("- ")]
    now = time.time()
    return {"id": path.name, "repo": repo.name, "rev": 1, "goal": " ".join(view["title"].split()), "out": [],
            "done": [gate], "grounds": {"pages": [], "files": [], "rules": []}, "decisions": decided,
            "source": {"focus": "pr", "turn": now, "plan": None}, "state": "리뷰 대기", "stopped": None,
            "worktree": str(path), "pr": {"number": view["number"], "url": view["url"],
                                          "base": view["baseRefName"], "head": view["headRefOid"],
                                          "branch": view["headRefName"]},
            "report": None, "gate": None, "fault": None, "rounds": [],
            "history": [{"ts": now, "state": "리뷰 대기"}]}


def take(repo: Path, n: int) -> str:
    """One pull request into a loop: a stopped spec goes on; one without a
    spec gets its worktree from the pull request's branch, and a spec."""

    spec = by_pr(repo.name).get(n)
    if spec is not None:
        why = refusal({}, spec)
        if why:
            raise HTTPException(409, why)
        return proceed(repo, spec, "")["id"]
    view = gh_or_502(repo, ["pr", "view", str(n), "--json",
                            "number,title,body,headRefName,headRefOid,baseRefName,isCrossRepository,url"])
    if view.get("isCrossRepository"):
        raise HTTPException(409, "포크 — 푸시할 곳이 없다")
    gate = specs.gate_of(repo)
    if not gate:
        raise HTTPException(409, "연결 먼저 — 이 저장소의 `.wiki/adapter.toml` 에 `gate_cmd` 가 없다")
    sid = folder_for(view["headRefName"])
    if sid and specs.file_of(repo.name, sid).exists():
        raise HTTPException(409, f"같은 이름의 명세 `{sid}` 가 이미 있다")
    path = adopted(repo, view["headRefName"], view["headRefOid"])
    with specs._files:
        specs.save(minimal(repo, view, path, gate))
    kick(repo.name, path.name)
    return path.name


class Pick(BaseModel):
    prs: list[int]


@router.post("/api/loops")
def start(body: Pick) -> dict:
    """`리뷰 루프 (N)`: each pull request picked, on its own."""

    with _lock:
        repo = current_repo()
    out = []
    for n in body.prs:
        try:
            out.append({"number": n, "id": take(repo, n)})
        except HTTPException as exc:
            out.append({"number": n, "error": exc.detail})
    return {"results": out}


@router.get("/api/loops")
def loops() -> dict:
    """Every project's loops and running turns, for the rail's other-projects group."""

    found = [specs.summary(s) for repo in specs.SPECS.glob("*") if repo.is_dir()
             for s in specs.listing(repo.name)
             if LOOPING.fullmatch(s["state"]) or s["state"] in ("머지 가능", "머지 대기", "멈춤")]
    with _lock:
        turns = [{"path": path, "repo": Path(path).parent.name.removesuffix("-worktrees")}
                 for path, run in work._runs.items() if not run.done]
    return {"loops": found, "turns": turns}


@router.get("/api/loops/events")
def events(after: int | None = None) -> StreamingResponse:
    """The server's own changes, as they come. From now, unless `after` says."""

    with work.feed.wake:
        start_at = len(work.feed.events) - 1 if after is None else after
    return streaming(work.tail(work.feed, start_at))


@router.get("/api/specs/{sid}/rounds/{n}")
def round_file(sid: str, n: int, what: Literal["order", "result"] = "result") -> dict:
    repo, spec = mine(sid)
    if not spec.get("pr"):
        raise HTTPException(404, "PR 이 없다")
    file = folder(repo.name, spec["pr"]["number"]) / (f"round-{n}.md" if what == "order" else f"round-{n}-result.md")
    if not file.is_file():
        raise HTTPException(404, "그 라운드 파일이 없다")
    return {"path": str(file), "text": file.read_text(encoding="utf-8")}


class Settings(BaseModel):
    rounds: int
    concurrent: int
    review_model: str = ""


@router.get("/api/loop/settings")
def get_settings() -> dict:
    return settings()


@router.post("/api/loop/settings")
def set_settings(body: Settings) -> dict:
    if not 1 <= body.rounds <= 50 or not 1 <= body.concurrent <= 10:
        raise HTTPException(400, "라운드 상한은 1–50, 동시 실행은 1–10")
    model = body.review_model.strip()
    if model and not model.startswith("codex:") and not channels.CLAUDE_MODEL.fullmatch(model):
        raise HTTPException(400, "그런 모델 이름은 받지 않는다")
    store(rounds=body.rounds, concurrent=body.concurrent, review_model=model)
    with _seats:
        _seats.notify_all()   # more seats may be free now
    return settings()
