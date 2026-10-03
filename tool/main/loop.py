"""loop — a pull request's review rounds, from the first to `[머지]`.

`operator/codex-review-loop`, run between two sessions of this program instead
of two terminals. The review cell is independent of the work cell: ordinary
review is read-only, while Cloud review can execute and create verification
artifacts. The work cell is the implementation session from `work`. A round:
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

import translate
from agent import ChatSession
from common import worktree_home
from workspace import adopt, folder_for, remove, worktrees

from . import channels, connect, decisions, query, specs, verification, work
from .query import ROOT, _lock, current_repo, hold, project, streaming

REVIEW = ROOT / "raw" / "review"
PROMPT = (ROOT / "tool/prompts/review-round.md").read_text(encoding="utf-8")
# The criteria a round is judged by, per profile; a mixed change gets both, once each.
RUBRIC = {name: (ROOT / f"tool/prompts/review-{name}.md").read_text(encoding="utf-8").strip()
          for name in ("plan", "code")}

DEFAULTS = {"rounds": 12, "concurrent": 3, "review_model": "", "review_effort": "high"}
MORE = 4        # rounds a `[계속]` past the cap adds, to that spec only
POLL = 60.0     # seconds between reads of a pull request waiting to merge
# Never `READ_TOOLS`: its `Bash` is on `--allowedTools`, runs unasked, and one
# `python -c` writes anywhere. A prompt does not stop a write; the tool list does.
REVIEW_TOOLS = "Read,Glob,Grep"
READ_PROFILE = ChatSession.READ_PROFILE
CLOUD_PROFILE = ChatSession.VERIFICATION_PROFILE
CLOUD_TOOLS = ChatSession.VERIFICATION_TOOLS

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
    PREPARATION = "로컬 검증 준비"
    EXTERNAL = "외부 수정 대기"


router = APIRouter()


# -- Settings ---------------------------------------------------------------
# Three values beside the translation switch, in `raw/chat/main.json`. The
# settings modal of stage 6 takes them over.

def _file() -> Path:
    return query.LOGS / "main.json"


def settings(defaults: dict = DEFAULTS) -> dict:
    """The saved values of `defaults`' keys; a missing or mistyped one is its default."""

    try:
        saved = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    saved = saved if isinstance(saved, dict) else {}
    return {k: saved[k] if type(saved.get(k)) is type(v) else v for k, v in defaults.items()}


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
META = "finding-meta"


def bare(line: str) -> str:
    """A line without the markdown a model wraps a protocol line in."""

    return line.strip().strip("*`#> ").strip()


def parse(text: str, n: int, pr: int, head: str, known: set[str] = frozenset()) -> dict:
    """`{verdict, findings, counts, identity}`, or `ValueError` saying what is off.

    The first line names the round, the pull request and the head it read; the
    last is the verdict. A finding is a line opening `[P0|P1|P2] path:line`,
    and the lines under it are its body. A `finding-meta` block gives each
    finding its `meta`, checked against `known`, the ids this spec has; an
    A finding without metadata or with a limited entry has no identity;
    validated sibling identities remain usable."""

    metas = re.findall(FENCE.format(META), text, re.M | re.S)
    text = re.sub(FENCE.format(META) + r"[ \t]*\r?\n?", "", text, flags=re.M | re.S)
    if re.search(rf"^\s*```{META}", text, re.M):
        # Opened and never closed: an attempt at identity, not an answer without one.
        raise ValueError("`finding-meta` 블록이 닫히지 않았다")
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
    if metas:
        try:
            meta = json.loads(metas[-1])
        except ValueError as exc:
            raise ValueError(f"`finding-meta` 가 JSON 이 아니다 — {exc}") from exc
        for f, m in zip(findings, described(meta, len(findings), known)):
            if not m.get("limited"):
                f["meta"] = m
    counts = {g: sum(f["grade"] == g for f in findings) for g in ("P0", "P1", "P2")}
    identity = "limited" if any("meta" not in f for f in findings) else "full"
    return {"verdict": verdict, "findings": findings, "counts": counts, "said": last, "identity": identity}


def described(meta, count: int, known: set[str]) -> list[dict]:
    """The `finding-meta` entries in finding order, or `ValueError`: one per
    finding by ordinal. Full entries name a component/invariant and an
    `existing_id` only ever given by the server; limited entries carry only
    the ordinal and their flag."""

    if not isinstance(meta, list) or not all(isinstance(m, dict) for m in meta):
        raise ValueError("`finding-meta` 는 객체의 목록이어야 한다")
    ordinals = [m.get("ordinal") for m in meta]
    if not all(type(o) is int for o in ordinals) or sorted(ordinals) != list(range(1, count + 1)):
        raise ValueError(f"`finding-meta` 의 ordinal 이 발견 1–{count} 과 하나씩 맞지 않는다")
    for m in meta:
        if "limited" in m:
            if m["limited"] is not True or set(m) != {"ordinal", "limited"}:
                raise ValueError("limited 항목은 ordinal 과 limited: true 만 받는다")
            continue
        if not all(isinstance(m.get(k), str) and m[k].strip() for k in ("component", "invariant")):
            raise ValueError("`finding-meta` 항목마다 `component` 와 `invariant` 가 있어야 한다")
        if m.get("existing_id") is not None and (not isinstance(m["existing_id"], str)
                                                 or m["existing_id"] not in known):
            raise ValueError(f"`finding-meta` 의 `existing_id` {m['existing_id']!r} 는 이 명세가 준 id 가 아니다")
    return sorted(meta, key=lambda m: m["ordinal"])


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
            and i.get("action") in ("fixed", "not-reproduced", "disagree")
            and isinstance(i.get("id"), (str, type(None)))]


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
    Only the fallback now: a finding with a server id is matched by it (`one`)."""

    ka, kb = _key(a), _key(b)
    return ka[0] == kb[0] and (ka[1] == kb[1] or (bool(ka[2]) and ka[2] == kb[2]))


def one(a: dict, b: dict) -> bool:
    """Two dispositions of one finding: by id when both carry one — two ids
    are two findings, whatever their words — else by `same`."""

    if a.get("id") and b.get("id"):
        return a["id"] == b["id"]
    return same(a["finding"], b["finding"])


def disputed(before: list[dict] | None, now: list[dict] | None) -> str:
    """A finding the fixing side disagreed with two rounds in a row, or ``""``."""

    old = [d for d in before or [] if d["action"] == "disagree"]
    return next((d["finding"] for d in now or [] if d["action"] == "disagree"
                 and any(one(d, o) for o in old)), "")


# -- Finding identity ------------------------------------------------------------
# The server owns a finding's id. A reviewer names an existing one or none;
# `described` refused anything else before a round is kept.

def issues(spec: dict) -> dict[str, dict]:
    """Every id this spec's counted rounds carry, with its latest item. A
    stale round has no items: it neither makes nor repeats a finding."""

    return {f["id"]: f for r in counted(spec) for f in r.get("items") or [] if f.get("id")}


def seen(spec: dict) -> dict[str, list[int]]:
    """The counted rounds each id was raised in — what recurrence is read from."""

    out: dict[str, list[int]] = {}
    for r in counted(spec):
        for fid in dict.fromkeys(f["id"] for f in r.get("items") or [] if f.get("id")):
            out.setdefault(fid, []).append(r["n"])
    return out


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def identified(spec: dict, findings: list[dict]) -> list[dict]:
    """The round's findings as it keeps them, each with its id: the validated
    `existing_id` first, then an exact component and invariant, else a new
    id — with `possible` naming a known finding of the same component, to be
    settled by the reviewer next round. Equal counts never make two one. A
    finding without meta has no id."""

    known = issues(spec)
    out = []
    for f in findings:
        item = {k: f[k] for k in ("grade", "file", "line", "head", "body")}
        m = f.get("meta")
        if m is None:
            out.append({**item, "id": None})
            continue
        key = (_norm(m["component"]), _norm(m["invariant"]))
        fid = m.get("existing_id") or next(
            (i for i, k in known.items() if (_norm(k["component"]), _norm(k["invariant"])) == key), None)
        possible = None
        if fid is None:
            fid = f"F{len(known) + 1}"
            possible = next((i for i, k in known.items() if _norm(k["component"]) == key[0]), None)
        item = {**item, "id": fid, "component": m["component"].strip(), "invariant": m["invariant"].strip(),
                "trigger": str(m.get("trigger") or ""), "evidence": str(m.get("evidence") or ""), "possible": possible}
        known[fid] = item
        out.append(item)
    return out


def vouched(disposition: list[dict] | None, items: list[dict]) -> list[dict] | None:
    """The fixing side's disposition with each `id` kept only when this round
    gave it — to one of the findings the entry's text names, when it names
    any. `same` is loose (a shared file and line is enough), so two findings
    at one place are both named. A made-up or misplaced id is dropped, and
    `same` decides instead: an id never makes two findings one."""

    if disposition is None:
        return None
    given = {f["id"] for f in items if f.get("id")}
    out = []
    for d in disposition:
        named = {f.get("id") for f in items if same(d["finding"], f["head"])}
        ok = d.get("id") in given and (not named or d.get("id") in named)
        out.append({**d, "id": d.get("id") if ok else None})
    return out


def settled(items: list[dict], disposition: list[dict] | None) -> list[dict]:
    """Each kept finding with the action the fixing side gave it, or `None`."""

    def action(f: dict):
        mine = {"id": f.get("id"), "finding": f["head"]}
        return next((d["action"] for d in disposition or [] if one(d, mine)), None)

    return [{**f, "disposition": action(f)} if f["grade"] != "P2" else f for f in items]


# -- The profile a round is reviewed under -------------------------------------------

def changed(path: Path, base_oid: str, head: str) -> list[str] | None:
    """Every path the pull request changes, both names of a rename; `None`
    when it cannot be read."""

    if not base_oid:
        return None
    done = specs.sh(["git", "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames", base_oid, head], path)
    return None if done.returncode else [f for f in done.stdout.splitlines() if f]


def effective(spec: dict, paths: list[str] | None) -> str:
    """The profile this round is reviewed under. A plan whose change leaves its
    artifact root, holds anything but Markdown, or cannot be read is mixed:
    code never passes on the plan criteria alone."""

    asked = specs.profile_of(spec)
    if asked["review_profile"] != "plan":
        return asked["review_profile"]
    root = (asked["artifact_root"] or "").rstrip("/") + "/"   # no root: nothing is inside, so mixed
    return "plan" if paths and all(p.startswith(root) and p.endswith(".md") for p in paths) else "mixed"


# -- The instruction ----------------------------------------------------------

def kept_rounds(spec: dict) -> list[dict]:
    """Every round, the stale ones too. A spec `[시작]` made has no `rounds`
    until its first round: read as `spec["rounds"]`, round 1 broke the loop."""

    return spec.get("rounds") or []


def counted(spec: dict) -> list[dict]:
    return [r for r in kept_rounds(spec) if not r.get("stale")]


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


def profiled(spec: dict, profile: str, head: str, base: str, base_oid: str) -> list[str]:
    """The instruction's part that says what the round is judged by: the
    profile and its version, the immutable head and base, and for a plan its
    requirements and sources, then the criteria — both, once each, when mixed."""

    asked = specs.profile_of(spec)
    out = ["", "## Review profile", "",
           f"- Profile `{profile}`, version {asked['review_profile_version']}. Reviewed head `{head}`, base `{base}`"
           + (f" at merge base `{base_oid}`." if base_oid else ".")]
    if asked["review_profile"] != profile:
        out.append(f"- The spec asked for `{asked['review_profile']}` with artifact root `{asked['artifact_root']}`; "
                   "the change reaches past it, so the code criteria apply too.")
    planned = spec.get("planning") or {}
    if profile != "code" and planned.get("outline"):
        # A planner's plan: its own requirement ids and the sources its research kept.
        out += ["", "## Requirements", "", f"- R0: {spec['goal']}"]
        out += [f"- {r['id']}: {r['text']}" for r in planned["outline"]["requirements"]]
        out += ["", "## Source manifest", ""]
        out += [f"- {s['id']}: {s['title']} · {s['url']} · retrieved {s['retrieved']} · {s['locator']}"
                for s in planned.get("source_manifest") or []] or ["(none kept)"]
    elif profile != "code":
        out += ["", "## Requirements", "", f"- R0: {spec['goal']}"]
        out += [f"- R{i}: {d}" for i, d in enumerate(spec["done"], 1)]
        grounds = spec.get("grounds") or {}
        cited = [*grounds.get("pages", []), *grounds.get("files", []),
                 *(e["cite"] for e in grounds.get("evidence", []))]
        out += ["", "## Source manifest", "", *(f"- `{c}`" for c in cited or ["(none cited)"])]
    if profile != "code" and asked["artifact_root"]:
        out.append(f"- The plan's documents: `{asked['artifact_root']}/`")
    for name in ("plan", "code"):
        if profile in (name, "mixed"):
            out += ["", RUBRIC[name]]
    return out


def known_findings(spec: dict) -> list[str]:
    rounds = seen(spec)
    out = ["", "## Known findings", ""]
    for fid, f in issues(spec).items():
        out.append(f"- `{fid}` · {f['grade']} · {f['component']} — {f['invariant']} · raised in rounds "
                   + ", ".join(map(str, rounds.get(fid, []))) + (f" · last disposition `{f['disposition']}`"
                                                                   if f.get("disposition") else ""))
        if f.get("possible"):
            out.append(f"  - possibly `{f['possible']}` again: if it is, name that id as `existing_id` this round")
    if len(out) == 3:
        out.append("(none with an id yet)")
    return out


def instruction(spec: dict, path: Path, n: int, head: str, base: str, codex: bool,
                profile: str | None = None, base_oid: str = "") -> str:
    """What `codex-review-loop` says an instruction must carry, with the
    result going to the final answer instead of a file. `profile` is the one
    `effective` gave this round; by default the spec's own."""

    profile = profile or specs.profile_of(spec)["review_profile"]
    pr = spec["pr"]["number"]
    rounds = counted(spec)
    last = rounds[-1] if rounds else None
    gated = spec.get("gate") or {}
    out = [
        f"Round {n} · PR #{pr} · {head[:7]}", "",
        f"Review pull request #{pr} as it stands at head `{head}` against base `{base}`. Your final "
        "answer is the result, and its first line is the line above, exactly. Write no file.", "",
        "## Allowed", "",
        ("- reading and searching source; executing verification commands and creating test scripts, "
         "fixtures and receipts in the verification artifact directory. Use the dedicated test environment."
         if verification.cloud(spec) else
         "- reading and searching source files with your read-only tools. The server supplies the PR diff "
         "and check receipts; shell commands and tests are not reviewer tools."), "",
        "## Forbidden", "",
        "- `checkout`, `switch`, `stash`, `merge`, `rebase`, `reset`, `cherry-pick`, `commit`, `push` · "
        + ("changing tracked implementation files · accessing production data · reading or printing `.env`/secrets"
         if verification.cloud(spec) else "installing packages · editing any file"), "",
        "## What changed", "",
        f"- `git diff --shortstat {base}...{head[:7]}`: {shortstat(path, base, head)}",
    ]
    if last:
        out.append(f"- Since round {last['n']}: `{last['head'][:7]}..{head[:7]}`")
    out += profiled(spec, profile, head, base, base_oid)
    out += known_findings(spec)
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
        what = "the checks this change maps to" if gated.get("selection") == "mapped" else "the gate"
        out.append(f"- The server ran {what} `{gated['cmd']}` in the worktree at `{head[:7]}`: "
                   f"{'passed' if gated['ok'] else 'failed — ' + gated['reason']}. Last line: `{tail[0]}`")
    else:
        out.append("- Nothing at this head yet.")
    out += ["", "## Deferred P2", ""]
    out += [f"- {d}" for d in spec.get("deferred") or []] or ["(none)"]
    out += ["", "Do not report a P2 listed here again without new grounds or a change of grade."]
    if verification.cloud(spec):
        out += ["", "## Cloud implementation: local verification", "",
                "The server executed the repository's configured checks locally. Review their receipts "
                "against the major flows and API contracts. A command exit alone is not behavior evidence. "
                "You must also execute verification commands and may create test scripts, fixtures, browser "
                "checks and receipts. Write them under the artifact directory below, not the tracked source. "
                "Execution permissions are preconfigured; do not request escalations. Do not commit or push. "
                "Do not edit implementation code or read/print `.env` secrets. A refusal returns to the cloud "
                "implementer, never to a local implementation session. Check carried-over results' impact reasons.",
                "", f"Artifact directory: `{folder(spec['repo'], pr) / 'verification' / head}`", "", "```json",
                json.dumps({"handoff": spec.get("cloud_handoff"), "verification": spec.get("local_verification")},
                           ensure_ascii=False, indent=2), "```"]
    if not shrinking(rounds):
        out += ["", "## Sort the findings by family first", "",
                "The P0 and P1 have not gone down for two rounds. Before this round's findings, pair the "
                "findings so far into a table, one row each: finding → the repair made → what came back next "
                "round. Sort them into the families `operator/codex-review-loop` names — the wrong model, "
                "where it measures, the claim is wrong, knew and skipped — and report by family, the cause "
                "first."]
    # Both reviewers have read/search tools, not a shell or GitHub connector.
    diff = specs.sh(["gh", "pr", "diff", str(pr)], path, 120)
    out += ["", f"## `gh pr diff {pr}`", "", "```diff",
            diff.stdout.rstrip() if not diff.returncode else f"(failed: {specs.said(diff)})", "```"]
    out += ["", "## Report", "",
            "First line as above. Then one block per finding, opening `[P0|P1|P2] path:line — what / when / "
            "why`, with trigger, defect, impact and reproducible evidence under it, graded by the criteria "
            "under `Review profile`. Then the `finding-meta` block, one entry per finding; `existing_id` only "
            "from `Known findings`. With nothing wrong, the one line `새 발견 없음` and no block. The last line is "
            "exactly `머지 허용`, or `머지 불가 — <reason>`."]
    return "\n".join(out) + "\n"


def fixing(n: int, findings: list[dict], said: str) -> str:
    """The work cell's turn: what `codex-review-loop` says the receiving side holds."""

    listed = "\n\n".join(f["head"] + (f"\nid: `{f['id']}`" if f.get("id") else "")
                         + (f"\n{f['body']}" if f["body"] else "") for f in findings)
    return (
        f"Review round {n} refused the merge (`{said}`) and found the following. For each finding: "
        "reproduce it first. Fix it where it "
        "points, and count separately the other places the same rule applies to. If you do not agree, "
        "say why with evidence rather than changing the code. Commit and push the task branch. "
        "The server also synchronizes the branch before the next review.\n\n"
        "End the answer with a fenced block whose info string is `disposition`, holding a JSON list with "
        "one entry per finding, in order: `{\"finding\": \"<its first line, copied>\", \"id\": \"<its id, "
        "when it has one, else null>\", \"action\": \"fixed\" | \"not-reproduced\" | \"disagree\", "
        "\"evidence\": \"<what you ran and saw>\"}`.\n\n"
        + (listed or "(no P0 or P1 — the verdict above is the whole of it)"))


# -- The review cell -----------------------------------------------------------

_cells: dict[tuple[str, int], ChatSession] = {}   # (repo, pr) -> its review cell
_review_runs: dict[tuple[str, str], work.Run] = {}


def review_model(chosen: str | None = None) -> str:
    """`chosen`, by default the model in the settings, else Codex's default."""

    chosen = settings()["review_model"] if chosen is None else chosen
    if chosen:
        return chosen
    models = channels.codex_models()
    return next((m["id"] for m in models if m.get("is_default")), models[0]["id"])


def folder(repo: str, pr: int) -> Path:
    return REVIEW / repo / str(pr)


def cell(spec: dict, path: Path) -> ChatSession:
    """The pull request's review cell, made once and kept while the pull
    request lives. Its CLI session id is kept beside the rounds, so a server
    started again goes on in the same conversation.

    Read once per round: a model or effort changed in the settings applies
    from the next round, never inside one, and each round keeps the one its
    verdict ran on (`reviewer`). A change of CLI is a new cell — the old
    session id is the other CLI's. Never an implementation session: ordinary
    review has read tools; Cloud verification has execution and artifact tools.
    A spec that names its own `reviewer` — a plan's — is reviewed by that
    model instead of the settings'."""

    key = (spec["repo"], spec["pr"]["number"])
    verify_dir = folder(*key) / "verification" / spec["pr"]["head"] if verification.cloud(spec) else None
    tools_profile = CLOUD_PROFILE if verify_dir is not None else READ_PROFILE
    named = spec.get("reviewer") or {}
    # Outside the lock: listing Codex's models starts Codex.
    model = review_model(named["model"]) if named.get("model") else review_model()
    effort = named.get("effort") or settings()["review_effort"]
    repo = channels.repo_for(spec["repo"]) if verify_dir is not None else None
    local = verification.local(repo) if repo is not None else {}
    env = {"WIKI_VERIFICATION_HEAD": spec["pr"]["head"],
           "WIKI_VERIFICATION_ENVIRONMENT": local.get("environment_id", ""),
           "WIKI_VERIFICATION_SCOPE": local.get("test_scope", ""),
           "WIKI_VERIFICATION_BROWSER": local.get("browser_tool", "")} if verify_dir is not None else {}
    with _lock:
        chat = _cells.get(key)
    if (chat is not None and chat.is_codex == model.startswith("codex:")
            and getattr(chat, "verification", None) == verify_dir
            and all(chat._env.get(k) == v for k, v in env.items())):
        chat.reconfigure(model, effort)
        return chat
    if chat is not None:
        close_cell(*key)
    context = (f"\nCloud verification mode: execute checks and create verification files under "
               f"`{folder(*key) / 'verification'}` using the per-head artifact directory in each round's instructions. "
               "This mode never authorizes editing tracked "
               "implementation, committing, pushing, or accessing production. Return the verdict as the final "
               "answer. Do not read or print .env or secrets; tests may load the prepared environment."
               if verify_dir is not None else "")
    chat = ChatSession(path, tools=CLOUD_TOOLS if verify_dir is not None else REVIEW_TOOLS,
                       system=context + "\n" + PROMPT, model=model, effort=effort,
                       **({"verification": verify_dir, "env": env} if verify_dir is not None else {}))
    try:
        saved = json.loads((folder(*key) / "session.json").read_text(encoding="utf-8"))
        if (saved.get("provider") == ("codex" if chat.is_codex else "claude")
                and (saved.get("tools_profile") == tools_profile if verify_dir is not None or chat.is_codex else True)):
            chat.session_id = saved.get("session_id")
    except (OSError, ValueError, AttributeError):
        pass
    with _lock:
        _cells[key] = chat
    return chat


def reviewer(chat: ChatSession) -> dict:
    """Who gave a verdict: the review cell's own session, model and effort."""

    return {"session_id": chat.session_id, "cell": chat.id, "model": chat.model, "effort": chat.effort,
            "tools": chat.tools, "mode": "cloud-verification" if getattr(chat, "verification", None) else "read-only"}


def close_cell(repo: str, pr: int) -> None:
    with _lock:
        chat = _cells.pop((repo, pr), None)
    if chat:
        chat.close()


def close_all() -> None:
    with _lock:
        alive = list(_cells.values())
        _cells.clear()
        _review_runs.clear()
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
        spec = specs.load(repo, sid)
        if spec is not None and LOOPING.fullmatch(spec["state"]):
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
            # A fault is the work stage's; in the loop it only misleads.
            specs.save(specs.moved(spec, "리뷰 대기", stopped=None, fault=None))
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
    spec = specs.load(loop.repo, loop.sid)
    path = Path(spec["worktree"])
    repo = channels.repo_for(loop.repo)
    redaction = verification.redaction(verification.local(repo), path) if verification.cloud(spec) else {}
    release = wait_hold(loop, path)
    if release is None:
        raise RuntimeError("사람이 멈춤")
    run = work.Run(chat)
    run.halt = loop.halt
    with _lock:
        _review_runs[(loop.repo, loop.sid)] = run
    work.feed.put({"kind": "review", "repo": loop.repo, "id": loop.sid, "turn": run.turn})
    loop.chat = chat
    try:
        for ev in chat.say(text, loop.halt):
            payload = {"kind": "tool" if ev.kind == "context" else ev.kind, "text": ev.text, "meta": ev.meta,
                       "session_id": chat.id, "parent_id": None}
            if verification.cloud(spec):
                payload = verification.sanitize(payload, redaction)
            run.put(payload)
            if ev.kind == "done":
                final = ev.text
                failed = (final or "완료된 답이 없다") if ev.meta.get("error") else ""
                if ev.meta.get("session_id"):
                    kept = folder(loop.repo, specs.load(loop.repo, loop.sid)["pr"]["number"]) / "session.json"
                    kept.parent.mkdir(parents=True, exist_ok=True)
                    kept.write_text(json.dumps({"session_id": ev.meta["session_id"],
                                                "tools_profile": CLOUD_PROFILE if verification.cloud(spec) else READ_PROFILE,
                                                "provider": "codex" if chat.is_codex else "claude"}) + "\n",
                                    encoding="utf-8")
            elif ev.kind == "error":
                failed = ev.text
    finally:
        loop.chat = None
        try:
            kept = folder(loop.repo, spec["pr"]["number"])
            kept.mkdir(parents=True, exist_ok=True)
            row = {"role": "assistant", "turn": run.turn, "text": verification.redact(final, redaction) if redaction else final,
                   "steps": work.steps(run.events), "error": verification.redact(failed, redaction) if redaction else failed}
            with (kept / "progress.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        finally:
            release()
            run.finish()
    if failed or not final.strip():
        raise RuntimeError(failed or "빈 답이다")
    return final


def told(loop: Loop, spec: dict, path: Path, text: str) -> str | None:
    """One turn of the work cell, as a person's turn would run: every write it
    asks for waits on a person. Its final answer; `None` when stopped.

    While it waits on an approval the spec says so to the screens, once each
    time that changes. A plan's documents are revised by a read-only session
    whose files the server writes (`planning.revise`), never by a write session."""

    if spec.get("planning"):
        from . import planning  # `planning` imports this module

        return planning.revise(loop, spec, path, text)
    release = wait_hold(loop, path)
    if release is None:
        return None
    try:
        chosen = spec.get("cell") or {}
        run = work.Run(work.session(path, chosen.get("model", ""), chosen.get("effort", "")))
        # Seen by `stop` before its thread starts. `stop` sets the halt, then
        # reads `run`: either it saw this run and set the run's halt, which
        # the turn reads before it sends anything, or this sees the halt and
        # the turn never starts.
        loop.run = run
        if loop.halt.is_set():
            loop.run = None
            release()
            return None
        work.begin(path, run.chat, text, release, run)
    except BaseException:
        loop.run = None
        release()
        raise
    asked = False
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


def reusable(spec: dict, head: str, chosen: dict) -> bool:
    """A passing round result for this very identity — head, merge base and
    commands. A spec from before `validation` has only `gate`, and that was
    the whole gate: it stands for its head."""

    last = (spec.get("validation") or {}).get("round")
    if last:
        return bool(last.get("ok")) and (last.get("head"), last.get("base_oid"), last.get("commands")) == \
            (head, chosen["base_oid"], chosen["commands"])
    gated = spec.get("gate") or {}
    return gated.get("head") == head and bool(gated.get("ok"))


def repair(cmd: str, verdict: dict) -> str:
    return (f"The server ran the gate `{cmd}` in this worktree and it failed: {verdict['reason']}. The end "
            f"of its output:\n\n```\n{verdict['tail']}\n```\n\nFix it, commit, and push the task branch.")


def shipped(loop: Loop, spec: dict, repo: Path, path: Path, head: str, base: str) -> bool:
    """The head that goes to review passed its round checks here, and is up.

    A push made elsewhere to the branch is taken in by fast-forward first, so
    the files the review cell reads are the head it reviews. Nothing more to
    do when the worktree is clean at the pull request's head and the same
    checks already passed there (`reusable`). Otherwise the checks this change
    maps to run (`specs.selected`) — not the whole gate, which runs once on
    the allowed head (`finalized`). A failure goes to the work cell once with
    the output's tail, and two in a row stop. Commits the pull request lacks
    are pushed once the checks pass."""

    branch = specs.branch_of(spec)
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
            chosen = specs.selected(repo, path, base, specs.required(repo, spec))
            if local == head and clean and reusable(spec, head, chosen):
                return True
            verdict, record = specs.rounded(repo, path, spec, base, loop.halt, chosen=chosen)
            pushed = None
            if verdict["ok"] and verdict["head"] != head and not loop.halt.is_set():
                pushed = specs.sh(["git", "push", "origin", branch], path, 120)
        finally:
            release()
        if change(loop, gate=verdict) is None:
            return False
        spec = specs.validate(loop.repo, loop.sid, round=record)
        if verdict["ok"]:
            if pushed is not None and pushed.returncode:
                return stop(loop, loop.repo, loop.sid, Why.GATE, f"push 실패 — {specs.said(pushed)}")
            return True
        if attempt == 2:
            return stop(loop, loop.repo, loop.sid, Why.GATE, f"게이트가 두 번 연속 실패했다 — {verdict['reason']}")
        if told(loop, spec, path, repair(verdict["cmd"], verdict)) is None:
            return False
        spec = specs.load(loop.repo, loop.sid) or spec
    return False


def finalized(loop: Loop, spec: dict, repo: Path, path: Path, head: str, base: str) -> dict | None:
    """The full gate, once, on the head the review allowed — before the spec
    may become mergeable. `None` when the loop was stopped.

    While it runs, `validation.phase` is `final_running` and the final record
    already stands, failed and unfinished: a stop, a crash or a restart
    leaves exactly that, never an `ok`. The record is bound to the head, the
    merge base and `specs.digest`; `merge` checks all three."""

    cmd = specs.required(repo, spec)
    release = wait_hold(loop, path)
    if release is None:
        return None
    try:
        record = {"head": head, "base_oid": specs.current_merge_base(path, base, head), "command": cmd,
                  "environment_digest": specs.digest(repo, path, cmd), "ok": False, "code": None,
                  "reason": "끝나지 않았다", "finished_at": None}
        specs.validate(loop.repo, loop.sid, phase="final_running", final=record)
        settings = verification.redaction(verification.local(repo), path) if verification.cloud(spec) else {}
        verdict = specs.judge(path, [cmd], loop.halt)
        if verification.cloud(spec):
            verdict = {**verdict, **{k: verification.redact(verdict[k], settings) for k in ("reason", "tail")}}
    finally:
        release()
    ok = verdict["ok"] and verdict["head"] == head
    reason = verdict["reason"] if not verdict["ok"] else "" if ok else "작업트리가 허용된 커밋에 있지 않다"
    record = {**record, "ok": ok, "code": verdict.get("code"), "reason": reason, "finished_at": time.time()}
    specs.validate(loop.repo, loop.sid, phase=None, final=record)
    return None if loop.halt.is_set() else {**record, "tail": verdict["tail"]}


def allowed(loop: Loop, spec: dict, repo: Path, path: Path, chat: ChatSession, n: int, head: str, base: str) -> bool:
    """A round allowed `head`: the final gate runs, and only its pass makes
    the spec mergeable. A failure goes to the work cell as a repair; its new
    commit gets its round checks and a new review. No new commit stops. A
    final result that already stands for this identity is not run again."""

    stands = not specs.proven(spec, head, specs.current_merge_base(path, base, head),
                              specs.digest(repo, path, specs.required(repo, spec)))
    final = spec["validation"]["final"] if stands else finalized(loop, spec, repo, path, head, base)
    if final is None:
        return False
    if final["ok"]:
        deferred = spec.get("deferred") or []
        kept_p2 = pick(loop, chat, deferred)
        if verification.cloud(spec):
            fresh = specs.load(loop.repo, loop.sid)
            problem = verification.proven(repo, path, fresh, head, specs.current_merge_base(path, base, head))
            if problem or pr_head(repo, spec["pr"]["number"]) != (head, base):
                return cloud_stop(loop, repo, fresh, head, problem or "검증 뒤 PR 커밋·base 가 바뀌었다")
        if verification.cloud(spec) or verification.enabled(repo):
            try:
                spec = verification.publish(repo, specs.load(loop.repo, loop.sid), head, base)
            except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
                if verification.cloud(spec):
                    return cloud_stop(loop, repo, spec, head, str(exc))
                return stop(loop, loop.repo, loop.sid, Why.GATE, str(exc))
        # `p2` stays as the cell wrote it — the next candidates read it; the
        # comment goes up on GitHub, read by a person, in Korean.
        shown = translate.translate(kept_p2, translate.EN_KO, time.monotonic() + specs.TRANSLATE_SECONDS)
        comment = ("리뷰에서 남긴 P2 — 따로 할 만한 것\n\n" + "\n".join(f"- {p}" for p in shown)) if kept_p2 else ""
        change(loop, "머지 가능", p2=kept_p2, p2_comment=comment)
        return False
    if verification.cloud(spec):
        verification.return_to_cloud(repo, spec, head, final["reason"] + "\n" + final["tail"], ["offline/final"])
        return stop(loop, loop.repo, loop.sid, Why.EXTERNAL, "최종 게이트 실패 — 클라우드 수정 대기")
    if spec.get("implementation_environment") == "external":
        return stop(loop, loop.repo, loop.sid, Why.EXTERNAL, "최종 게이트 실패 — 외부에서 수정 후 다음 라운드를 시작한다")
    spec = change(loop, f"고치는 중 R{n}")
    if spec is None or told(loop, spec, path, repair(final["command"], final)) is None:
        return False
    if specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip() == head:
        return stop(loop, loop.repo, loop.sid, Why.GATE, f"최종 게이트 실패, 고친 커밋이 없다 — {final['reason']}")
    return True


def cloud_stop(loop: Loop, repo: Path, spec: dict, head: str, reason: str) -> bool:
    spec = specs.load(loop.repo, loop.sid) or spec
    state = (spec.get("local_verification") or {}).get("state")
    verification.pending(repo, spec, head, reason, state if state in ("reanalysis", "unstable") else "waiting_environment")
    return stop(loop, loop.repo, loop.sid, Why.PREPARATION, "로컬 검증 준비 대기 — " + verification.redact(reason, verification.local(repo)))


def sync_review(spec: dict, path: Path, head: str) -> None:
    """Move a clean review checkout to the remote head, preserving local branches."""

    branch = specs.branch_of(spec)
    fetched = specs.sh(["git", "fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"], path, 120)
    if fetched.returncode:
        raise ValueError("PR 커밋을 가져오지 못했다")
    dirty = specs.sh(["git", "status", "--porcelain"], path)
    if dirty.returncode or dirty.stdout.strip():
        raise ValueError("로컬 리뷰 폴더에 커밋 안 된 변경이 있다")
    local = specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip()
    if local == head:
        return
    detached = specs.sh(["git", "symbolic-ref", "-q", "HEAD"], path).returncode == 1
    if detached or spec.get("implementation_environment", "local") != "local":
        synced = specs.sh(["git", "checkout", "--detach", head], path, 60)
    else:
        if specs.sh(["git", "merge-base", "--is-ancestor", local, head], path).returncode:
            raise ValueError("리뷰 폴더와 PR 커밋이 갈라졌다 — 사용자 확인 필요")
        synced = specs.sh(["git", "merge", "--ff-only", head], path, 60)
    if synced.returncode or specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip() != head:
        raise ValueError("리뷰 폴더를 PR 커밋으로 옮기지 못했다")


def external_shipped(loop: Loop, spec: dict, repo: Path, path: Path, head: str, base: str) -> bool:
    release = wait_hold(loop, path)
    if release is None:
        return False
    try:
        sync_review(spec, path, head)
        chosen = specs.selected(repo, path, base, specs.required(repo, spec))
        if reusable(spec, head, chosen):
            return True
        verdict, record = specs.rounded(repo, path, spec, base, loop.halt, chosen=chosen)
        specs.validate(loop.repo, loop.sid, round=record)
        if change(loop, gate=verdict) is None:
            return False
        if not verdict["ok"]:
            return stop(loop, loop.repo, loop.sid, Why.EXTERNAL, verdict["reason"] + "\n" + verdict["tail"])
        return True
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return stop(loop, loop.repo, loop.sid, Why.GATE, str(exc))
    finally:
        release()


def cloud_shipped(loop: Loop, spec: dict, repo: Path, path: Path, head: str, base: str) -> bool:
    """Synchronize and verify a cloud commit without dispatching a local fixer."""

    release = wait_hold(loop, path)
    if release is None:
        return False
    try:
        sync_review(spec, path, head)
        spec = verification.deliver(repo, spec, head)
        if (spec.get("local_verification") or {}).get("delivery"):
            return stop(loop, loop.repo, loop.sid, Why.GATE, spec["local_verification"]["reason"])
        view = gh_json(repo, ["pr", "view", str(spec["pr"]["number"]), "--json", "body,headRefOid,baseRefName"])
        if (view["headRefOid"], view["baseRefName"]) != (head, base):
            raise ValueError("인계를 읽는 동안 PR 이 바뀌었다 — 다시 시작한다")
        transfer = verification.handoff(view["body"], head)
        # The handoff already comes from the PR cloud can read. Preserve its
        # public commit and commands; runtime observation payloads are redacted.
        spec = specs.update(loop.repo, loop.sid, cloud_handoff=transfer,
                            pr={**spec["pr"], "head": head, "base": base})
        record = spec.get("local_verification") or {}
        if record.get("needs_research") and not record.get("research_note"):
            raise ValueError("재분석 원인·근거·다음 실험을 적고 로컬 검증을 재개한다")
        verification.pending(repo, spec, head, "로컬 검증을 시작한다", "running")
        verification.protection(repo, base)
        base_oid = specs.current_merge_base(path, base, head)
        if not base_oid:
            raise ValueError("검증할 base 를 읽지 못했다")
        spec = verification.execute(repo, spec, path, head, base_oid, loop.halt)
        if loop.halt.is_set():
            return False
        if spec["local_verification"]["state"] != "runtime_passed":
            why = Why.EXTERNAL if spec["local_verification"]["state"] == "waiting_cloud" else Why.PREPARATION
            return stop(loop, loop.repo, loop.sid, why, spec["local_verification"]["reason"])
        chosen = specs.selected(repo, path, base, specs.required(repo, spec))
        if not reusable(spec, head, chosen):
            settings = verification.redaction(verification.local(repo), path)
            verdict, check = specs.rounded(repo, path, spec, base, loop.halt, chosen=chosen)
            verdict = {**verdict, **{k: verification.redact(verdict[k], settings) for k in ("reason", "tail")}}
            specs.validate(loop.repo, loop.sid, round=check)
            spec = change(loop, gate=verdict)
            if spec is None:
                return False
            if not verdict["ok"]:
                verification.return_to_cloud(repo, spec, head, verdict["reason"] + "\n" + verdict["tail"], ["offline/round"])
                return stop(loop, loop.repo, loop.sid, Why.EXTERNAL, "게이트 실패 — 클라우드 수정 대기")
        if pr_head(repo, spec["pr"]["number"]) != (head, base):
            raise ValueError("로컬 검증 중 PR 커밋·base 가 바뀌었다")
        return True
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return cloud_stop(loop, repo, spec, head, str(exc))
    finally:
        release()


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
    if spec.get("workspace_mode") == "branch" and \
            specs.sh(["git", "branch", "--show-current"], path).stdout.strip() != specs.branch_of(spec):
        return stop(loop, loop.repo, loop.sid, Why.NO_WORKTREE, "작업 브랜치를 다시 연 뒤 리뷰를 계속해라")
    rounds, pr = counted(spec), spec["pr"]["number"]
    n = len(rounds) + 1
    head, base = pr_head(repo, pr)
    ship = cloud_shipped if verification.cloud(spec) else external_shipped if spec.get("implementation_environment") == "external" else shipped
    if not ship(loop, spec, repo, path, head, base):
        return False
    spec = specs.load(loop.repo, loop.sid)
    head, base = pr_head(repo, pr)
    if (spec.get("gate") or {}).get("head") != head:
        # A remote push after synchronization needs its own checks before review.
        return True
    if verification.cloud(spec):
        problem = verification.proven(repo, path, spec, head, specs.current_merge_base(path, base, head))
        if problem:
            return cloud_stop(loop, repo, spec, head, problem)
    last = rounds[-1] if rounds else None
    again = spec.get("review_again", False)
    if (not again and last and last["verdict"] == "allow" and (last["head"], last["base"]) == (head, base)
            and (not verification.cloud(spec) or last.get("local_verification_digest") == verification.evidence_identity(spec))):
        # Allowed already, with no final gate that stands: one from before
        # `validation`, one a restart cut, or one that failed and was resumed.
        # The review is not asked again; only the final gate runs.
        spec = change(loop, f"리뷰 R{last['n']}")
        return spec is not None and allowed(loop, spec, repo, path, cell(spec, path), last["n"], head, base)
    if n > cap(spec):
        return stop(loop, loop.repo, loop.sid, Why.CAP, f"{cap(spec)} 라운드를 다 돌았다")
    spec = change(loop, f"리뷰 R{n}", review_again=False)
    if spec is None:
        return False
    chat = cell(spec, path)
    kept = folder(spec["repo"], pr)
    kept.mkdir(parents=True, exist_ok=True)
    order = kept / f"round-{n}.md"
    # The changed paths decide the criteria before the review, never after.
    base_oid = specs.current_merge_base(path, base, head)
    profile = effective(spec, changed(path, base_oid, head))
    review_instruction = instruction(spec, path, n, head, base, chat.is_codex, profile, base_oid)
    order.write_text(review_instruction, encoding="utf-8")

    settings = verification.redaction(verification.local(repo), path) if verification.cloud(spec) else {}
    # The round record lives outside the worktree. Supply it directly rather
    # than widening the reviewer's filesystem access to raw session storage.
    parsed, why, ask_for = None, "", f"Review the instruction below (recorded at `{order}`):\n\n{review_instruction}"
    for _ in range(2):
        try:
            answer = ask(loop, chat, ask_for)
        except RuntimeError as exc:
            if loop.halt.is_set():
                return False
            detail = verification.redact(str(exc), settings) if verification.cloud(spec) else str(exc)
            why = f"리뷰 셀이 답하지 못했다 — {detail}"
            continue
        if verification.cloud(spec):
            integrity = verification.checkout_proven(path, head)
            if integrity:
                return cloud_stop(loop, repo, spec, head, integrity)
        try:
            parsed = parse(answer, n, pr, head, set(issues(spec)))
            if verification.cloud(spec):
                parsed["said"] = verification.redact(parsed["said"], settings)
                for finding in parsed["findings"]:
                    # Only grade/line, ordinal and a validated existing id
                    # are protocol metadata; model-authored names are free text.
                    start = FINDING.match(finding["head"]).end()
                    finding["file"] = verification.redact(finding["file"], settings)
                    finding["head"] = (f"[{finding['grade']}] {finding['file']}:{finding['line']}"
                                       + verification.redact(finding["head"][start:], settings))
                    finding["body"] = verification.redact(finding["body"], settings)
                    if "meta" in finding:
                        limited = False
                        meta = {k: v for k, v in finding["meta"].items()
                                if k in ("ordinal", "existing_id", "component", "invariant", "trigger", "evidence")}
                        for key in ("component", "invariant", "trigger", "evidence"):
                            value = str(meta.get(key) or "")
                            meta[key] = verification.redact(value, settings)
                            if key in ("component", "invariant") and meta[key] != value and not meta.get("existing_id"):
                                limited = True
                        if limited:
                            # Reject only this unsafe new identity; safe sibling
                            # ids must still count toward recurrence escalation.
                            parsed["identity"] = "limited"
                            finding.pop("meta")
                        else:
                            finding["meta"] = meta
                lines = [f"Round {n} · PR #{pr} · {head[:7]}"]
                for finding in parsed["findings"]:
                    lines += [finding["head"], finding["body"]]
                if any("meta" in f for f in parsed["findings"]):
                    metadata = [f.get("meta", {"ordinal": i, "limited": True})
                                for i, f in enumerate(parsed["findings"], 1)]
                    lines += ["```finding-meta", json.dumps(metadata, ensure_ascii=False), "```"]
                if not parsed["findings"]:
                    lines.append("새 발견 없음")
                answer = "\n".join([*lines, parsed["said"]])
            (kept / f"round-{n}-result.md").write_text(answer, encoding="utf-8")
            break
        except ValueError as exc:
            (kept / f"round-{n}-result.md").write_text(
                verification.redact(answer, settings) if verification.cloud(spec) else answer, encoding="utf-8")
            why = verification.redact(str(exc), settings) if verification.cloud(spec) else str(exc)
            ask_for = (f"Your answer to round {n} could not be read: {why}. Answer round {n} again, in the "
                       f"shape the instruction `{order}` gives: first line `Round {n} · PR #{pr} · {head[:7]}`, "
                       "a `finding-meta` block naming only ids listed under `Known findings`, last line "
                       "`머지 허용` or `머지 불가 — <reason>`.")
    if parsed is None:
        return stop(loop, loop.repo, loop.sid, Why.FORMAT, why)

    record = {"n": n, "head": head, "base": base, "base_oid": base_oid, "findings": parsed["counts"],
              "verdict": parsed["verdict"], "profile": profile,
              "profile_version": specs.profile_of(spec)["review_profile_version"], "identity": parsed["identity"],
              "reviewer": reviewer(chat),
              "gate": {k: (spec.get("gate") or {}).get(k) for k in ("ok", "cmd", "head")},
              "disposition": None, "ts": time.time()}
    if verification.cloud(spec):
        record["local_verification_digest"] = verification.evidence_identity(spec)
    moved = pr_head(repo, pr)
    if moved != (head, base):
        # Someone pushed or changed the base while it was read: the verdict
        # is about code that is no longer the pull request. Kept, not counted,
        # and without items: it neither raises nor resolves a finding.
        for name in (f"round-{n}.md", f"round-{n}-result.md"):
            if (kept / name).exists():
                (kept / name).replace(kept / name.replace(f"round-{n}", f"round-{n}-stale-{head[:7]}"))
        return change(loop, rounds=[*kept_rounds(spec), {**record, "stale": True}]) is not None

    deferred = list(spec.get("deferred") or [])
    for f in parsed["findings"]:
        if f["grade"] == "P2" and not any(same(f["head"], d) for d in deferred):
            deferred.append(f["head"])
    record["items"] = identified(spec, parsed["findings"])
    spec = change(loop, rounds=[*kept_rounds(spec), record], deferred=deferred)
    if spec is None:
        return False
    if parsed["verdict"] == "allow":
        return allowed(loop, spec, repo, path, chat, n, head, base)

    if verification.cloud(spec):
        serious = [f for f in record["items"] if f["grade"] != "P2"]
        names = [f["id"] or f"review/{f['file']}:{f['line']}" for f in serious] or ["review/verdict"]
        details = "\n\n".join(f"[{f['grade']}] {f['file']}:{f['line']} — {f['head']}\n{f['body']}" for f in serious)
        verification.return_to_cloud(repo, spec, head, parsed["said"] + "\n\n" + details, names)
        return stop(loop, loop.repo, loop.sid, Why.EXTERNAL, "로컬 리뷰 거절 — 클라우드 수정 대기")

    if spec.get("implementation_environment") == "external":
        details = "\n\n".join(f["head"] + "\n" + f["body"] for f in record["items"])
        return stop(loop, loop.repo, loop.sid, Why.EXTERNAL, parsed["said"] + "\n\n" + details)

    spec = change(loop, f"고치는 중 R{n}")
    if spec is None:
        return False
    serious = [f for f in record["items"] if f["grade"] != "P2"]
    # Jev may gather the context the findings touch first. The verdict, the
    # cap and the merge conditions above are settled; this only shapes the turn.
    against = [d["finding"] for d in (rounds[-1].get("disposition") or [] if rounds else [])
               if d["action"] == "disagree"]
    text = decisions.fix_turn(loop, spec, repo, path, n, head, serious,
                              [f["head"] for f in serious if any(same(f["head"], a) for a in against)],
                              fixing(n, serious, parsed["said"]))
    if text is None:
        # Refused at the execution boundary three times over: the worktree went, or its
        # state kept moving under the loop. Stopped with the reason, never sent regardless.
        if loop.halt.is_set():
            return False
        gone = not path.is_dir()
        return stop(loop, loop.repo, loop.sid, Why.NO_WORKTREE if gone else Why.FORMAT,
                    f"R{n} 수정 턴을 보내지 않았다 — 제안이 실행 직전 확인을 넘지 못했다")
    answer = told(loop, spec, path, text)
    if answer is None:
        return False
    # The implementation turn may revise the requirements and invalidate prior rounds.
    spec = specs.load(loop.repo, loop.sid) or spec
    latest = spec["rounds"][-1]
    disposition = vouched(disposed(answer), latest.get("items") or [])
    spec = change(loop, rounds=[*spec["rounds"][:-1], {**latest, "disposition": disposition,
                                                       "items": settled(latest.get("items") or [], disposition)}])
    if spec is None:
        return False
    before = rounds[-1].get("disposition") if rounds else None
    stuck = disputed(before, disposition)
    if stuck:
        return stop(loop, loop.repo, loop.sid, Why.DISPUTE, f"두 라운드 연속 반대 — {stuck}")
    return shipped(loop, spec, repo, path, head, base)


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
    meanwhile — and one `[계속]` takes it on.

    A final gate it cut off keeps the failed, unfinished record `finalized`
    wrote first; only the phase goes. `[계속]` runs the final gate again,
    after the round checks confirm the head is the one allowed."""

    for repo in specs.SPECS.glob("*"):
        if repo.is_dir():
            for spec in specs.listing(repo.name):
                if (spec.get("validation") or {}).get("phase") == "final_running":
                    specs.validate(repo.name, spec["id"], phase=None)
                if LOOPING.fullmatch(spec["state"]):
                    if verification.cloud(spec) and (spec.get("local_verification") or {}).get("state") == "running":
                        verification.keep(spec, state="interrupted", reason="서버 재시작 — 사용자가 재개한다")
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
        elif queued:
            # What the rail's second line says of a `머지 대기`: who holds it.
            how = "자동 머지 — 검사 대기" if view.get("autoMergeRequest") else "대기열"
            if spec.get("queued") != how:
                specs.update(repo.name, spec["id"], queued=how)
        elif state == "OPEN":
            stop(None, repo.name, spec["id"], Why.LEFT_QUEUE, "PR 이 열려 있는데 대기열에도 없고 자동 머지도 꺼졌다",
                 WAITING)
        elif state == "CLOSED":
            stop(None, repo.name, spec["id"], Why.LEFT_QUEUE, "PR 이 닫혔다", WAITING)


def forward(repo: Path, base: str, *, task_branch: str = "") -> str:
    """Fast-forward a clean base, optionally returning from the merged task.

    Never switch an unrelated branch or interrupt another turn. The local
    task branch remains intact, including commits added after publication.
    """

    try:
        release = hold(work._busy, _lock, str(repo), "", kind="turn")
    except HTTPException:
        return "원본이 뒤처짐 — 다른 작업이 저장소를 쓰고 있다"
    try:
        on = specs.sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
        dirty = specs.sh(["git", "status", "--porcelain"], repo)
        if on not in {base, task_branch} or dirty.returncode or dirty.stdout.strip():
            return f"원본이 뒤처짐 — 원본이 `{base}` 에 깨끗이 서 있지 않다"
        fetched = specs.sh(["git", "fetch", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}"], repo, 120)
        if fetched.returncode:
            return f"원본이 뒤처짐 — {specs.said(fetched)}"
        if on != base:
            switched = specs.sh(["git", "switch", base], repo)
            if switched.returncode:
                return f"원본이 뒤처짐 — {specs.said(switched)}"
            work.forget(repo)
        done = specs.sh(["git", "merge", "--ff-only", f"origin/{base}"], repo, 60)
        return f"원본을 `origin/{base}` 로 앞으로 옮겼다" if not done.returncode else f"원본이 뒤처짐 — {specs.said(done)}"
    finally:
        release()


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
    if (spec.get("survey") or {}).get("handover"):
        if spec.get("workspace_mode") == "branch":
            notes.append(forward(repo, base, task_branch=specs.branch_of(spec)))
        # The original's adapter is uncommitted and would block the
        # fast-forward. The handover moves it aside and fast-forwards
        # itself; when it stops, the fast-forward is skipped too.
        handed = connect.handover(repo, n)
        notes.append(handed["reason"] if handed["ok"] else f"adapter 넘기기 대기 — {handed['reason']}")
    else:
        notes.append(forward(repo, base, task_branch=specs.branch_of(spec)
                             if spec.get("workspace_mode") == "branch" else ""))
    if spec.get("workspace_mode") == "branch":
        notes.append("원본 저장소와 작업 브랜치를 남겼다")
    elif spec.get("worktree"):
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
                if (verification.cloud(spec) and spec["state"] == "머지 가능"
                        and (spec.get("local_verification") or {}).get("state") == "verified"):
                    allowed = specs.approved(spec)
                    if allowed:
                        tree = Path(spec.get("worktree") or path)
                        try:
                            head, base = pr_head(path, spec["pr"]["number"])
                            problem = verification.merge_proven(path, tree, spec, head,
                                                          specs.current_merge_base(tree, base, head))
                            if problem:
                                verification.pending(path, spec, head, problem, "waiting_review")
                        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                            verification.pending(path, spec, allowed["head"], "현재 검증 상태를 확인하지 못했다", "waiting_review")
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
    bind it, and `landed` catches a base that moved in the seconds between.

    The full gate must have passed on that same head, merge base and
    environment (`specs.proven`); a targeted round result never counts. A
    spec without one goes back to the loop, which runs only the final gate."""

    repo, spec = mine(sid)
    if spec["state"] != "머지 가능":
        raise HTTPException(409, f"머지할 수 있는 상태가 아니다 — {spec['state']}")
    allowed = specs.approved(spec)
    if allowed is None:
        raise HTTPException(409, "리뷰가 허용한 라운드가 없다")
    n = spec["pr"]["number"]
    if body.head != allowed["head"]:
        if not verification.cloud(spec):
            kick(repo.name, sid)
        raise HTTPException(409, "리뷰 뒤 새 커밋 — 새 라운드를 받는다")
    path = Path(spec.get("worktree") or repo)
    if verification.cloud(spec):
        try:
            current_head, current_base = pr_head(repo, n)
            problem = verification.merge_proven(repo, path, spec, current_head,
                                          specs.current_merge_base(path, current_base, current_head))
            if current_head != allowed["head"] or current_base != allowed["base"]:
                problem = "로컬 리뷰 뒤 PR 커밋·base 가 바뀌었다"
            if problem:
                verification.pending(repo, spec, current_head, problem, "waiting_review")
                raise HTTPException(409, problem + " — 로컬 리뷰를 다시 시작한다")
            verification.protection(repo, current_base)
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(409, str(exc)) from exc
    unproven = specs.proven(spec, allowed["head"], specs.current_merge_base(path, allowed["base"], allowed["head"]),
                            specs.digest(repo, path, specs.required(repo, spec)))
    if unproven:
        if verification.cloud(spec):
            verification.pending(repo, spec, allowed["head"], unproven, "waiting_review")
        else:
            kick(repo.name, sid)
        raise HTTPException(409, f"{unproven} — 최종 게이트를 다시 돌린다")
    try:
        _, base = pr_head(repo, n)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(502, str(exc)) from exc
    if base != allowed["base"]:
        if verification.cloud(spec):
            verification.pending(repo, spec, allowed["head"], "base 변경", "waiting_review")
        else:
            kick(repo.name, sid)
        raise HTTPException(409, "리뷰 뒤 base 변경 — 새 라운드를 받는다")
    done = specs.sh(["gh", "pr", "merge", str(n), "--squash", "--match-head-commit", allowed["head"]], repo, 120)
    if done.returncode:
        try:
            moved = pr_head(repo, n)[0] != allowed["head"]
        except (RuntimeError, ValueError):
            moved = False
        if moved:
            if not verification.cloud(spec):
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
    if verification.cloud(spec):
        record = spec.get("local_verification") or {}
        if record.get("needs_research") or record.get("state") in ("reanalysis", "unstable"):
            if not note:
                raise HTTPException(400, "재분석 원인·근거·다음 실험을 적어야 재개한다")
            note = verification.redact(note, verification.local(repo))
            spec = verification.keep(spec, research_note=note, needs_research=False, research=[*record.get("research", []),
                                     {"head": record.get("head"), "note": note, "ts": time.time(),
                                      "failure_attempts": list(range(len(record.get("failure_attempts", []))))}])
    if why is Why.DISPUTE:
        if not note:
            raise HTTPException(400, "그 발견에 정한 것을 적어야 잇는다")
        specs.update(repo.name, sid, rulings=[*(spec.get("rulings") or []), note])
    elif why is Why.CAP:
        specs.update(repo.name, sid, extra=spec.get("extra", 0) + MORE)
    elif why is Why.NO_REPO and channels.repo_for(spec["repo"]) is None:
        raise HTTPException(409, f"`{spec['repo']}` 가 아직 작업 공간에 없다")
    elif why is Why.NO_WORKTREE:
        if spec.get("workspace_mode") == "branch":
            specs.activate(sid)
        else:
            view = gh_or_502(repo, ["pr", "view", str(spec["pr"]["number"]), "--json", "headRefName,headRefOid"])
            path = adopted(repo, view["headRefName"], view["headRefOid"],
                           detached=spec.get("implementation_environment", "local") != "local")
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


def adopted(repo: Path, branch: str, oid: str, detached: bool = False) -> Path:
    """`workspace.adopt`, held like making a worktree: the switch waits."""

    release = hold(work._busy, _lock, str(worktree_home(repo) / folder_for(branch)), "그 작업트리를 다른 요청이 쓰고 있다")
    try:
        return adopt(repo, branch, oid, detached=detached)
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


@router.post("/api/specs/{sid}/review")
def review_again(sid: str) -> dict:
    """Explicitly request another independent round, including a remote PR head."""

    repo, spec = mine(sid)
    if LOOPING.fullmatch(spec["state"]):
        raise HTTPException(409, "리뷰가 이미 돌고 있다")
    if not spec.get("pr") or spec["state"] != "머지 가능" and not spec["state"].startswith("PR #"):
        raise HTTPException(409, "이 상태에서는 새 리뷰를 시작하지 않는다")
    if spec.get("plan_commit") == "asked":
        raise HTTPException(409, "계획 행 커밋이 아직이다")
    with specs._files:
        extra = spec.get("extra", 0)
        if len(counted(spec)) >= cap(spec):
            extra += MORE
        specs.update(repo.name, sid, review_again=True, extra=extra)
    kick(repo.name, sid)
    return specs.view(repo, specs.load(repo.name, sid))


@router.post("/api/specs/{sid}/halt")
def halt(sid: str) -> dict:
    """`[멈춤]` of a running loop: the turn it waits on is stopped too."""

    repo, spec = mine(sid)
    if not LOOPING.fullmatch(spec["state"]):
        raise HTTPException(409, "도는 루프가 아니다")
    halt_loop(repo.name, sid)
    spec = specs.load(repo.name, sid)
    if spec["state"] != "멈춤":
        # The loop got to its end first; that end stands.
        raise HTTPException(409, f"루프가 먼저 끝났다 — {spec['state']}")
    return specs.view(repo, spec)


def halt_loop(repo: str, sid: str) -> None:
    """Stop the loop of `sid` in `repo` — the repository named, never the
    one selected now — and put the spec in `멈춤` by a person."""

    with _lock:
        loop = _loops.get((repo, sid))
    if loop is not None:
        loop.stop()
    stop(None, repo, sid, Why.PERSON)


def stranded(spec: dict) -> bool:
    """A pull request that is up but never went into review: the plan row's
    turn after it failed, so nothing kicked the loop, and neither `[계속]`
    (not `멈춤`) nor the list (`이미 PR #n`) could. Not while the plan row is
    still owed: a round started now would review the head without it, and
    the agent tab's next turn is what commits, pushes and kicks it."""

    return (spec["state"].startswith("PR #") and bool(spec.get("fault"))
            and spec.get("plan_commit") != "asked")


def refusal(row: dict, spec: dict | None) -> str:
    """Why a pull request cannot go into a loop from the list, or ``""``."""

    if row.get("isCrossRepository"):
        return "포크 — 푸시할 곳이 없다"
    if spec is None:
        return ""
    reason = (spec.get("stopped") or {}).get("reason")
    if stranded(spec):
        return ""
    if spec["state"] == "머지 가능":
        return ""
    if spec["state"].startswith("PR #") and spec.get("plan_commit") == "asked":
        return "계획 행 커밋이 아직이다 — 에이전트 탭에서 행을 고쳐 커밋하게 하면 리뷰로 간다"
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
            # Adopted from GitHub, nobody named a profile: code, never guessed from the files.
            "review_profile": "code", "review_profile_version": specs.PROFILE_VERSION, "artifact_root": None,
            "history": [{"ts": now, "state": "리뷰 대기"}]}


def take(repo: Path, n: int, environment: str = "local") -> str:
    """One pull request into a loop: a stopped spec goes on; one without a
    spec gets its worktree from the pull request's branch, and a spec."""

    spec = by_pr(repo.name).get(n)
    if spec is not None:
        why = refusal({}, spec)
        if why:
            raise HTTPException(409, why)
        view = gh_or_502(repo, ["pr", "view", str(n), "--json", "body"])
        if verification.cloud(spec) or "```cloud-handoff" in (view.get("body") or ""):
            environment = "claude-cloud"
        if environment != "local" and environment != spec.get("implementation_environment", "local"):
            if counted(spec):
                raise HTTPException(409, "이미 시작한 리뷰의 구현 환경을 바꾸지 않는다")
            spec = specs.update(repo.name, spec["id"], implementation_environment=environment)
        if spec["state"].startswith("PR #"):
            kick(repo.name, spec["id"])
            return spec["id"]
        if spec["state"] == "머지 가능":
            specs.update(repo.name, spec["id"], review_again=True,
                         extra=spec.get("extra", 0) + (MORE if len(counted(spec)) >= cap(spec) else 0))
            kick(repo.name, spec["id"])
            return spec["id"]
        return proceed(repo, spec, "")["id"]
    view = gh_or_502(repo, ["pr", "view", str(n), "--json",
                            "number,title,body,headRefName,headRefOid,baseRefName,isCrossRepository,url"])
    if view.get("isCrossRepository"):
        raise HTTPException(409, "포크 — 푸시할 곳이 없다")
    if "```cloud-handoff" in (view.get("body") or ""):
        environment = "claude-cloud"
    gate = specs.gate_of(repo)
    if not gate:
        raise HTTPException(409, "연결 먼저 — 이 저장소의 `.wiki/adapter.toml` 에 `gate_cmd` 가 없다")
    sid = folder_for(view["headRefName"])
    if sid and specs.file_of(repo.name, sid).exists():
        raise HTTPException(409, f"같은 이름의 명세 `{sid}` 가 이미 있다")
    path = adopted(repo, view["headRefName"], view["headRefOid"], detached=environment != "local")
    with specs._files:
        specs.save({**minimal(repo, view, path, gate), "implementation_environment": environment})
    kick(repo.name, path.name)
    return path.name


class Pick(BaseModel):
    prs: list[int]
    implementation_environment: Literal["local", "claude-cloud", "external"] = "local"


@router.post("/api/loops")
def start(body: Pick) -> dict:
    """`리뷰 루프 (N)`: each pull request picked, on its own."""

    with _lock:
        repo = current_repo()
    out = []
    for n in body.prs:
        try:
            out.append({"number": n, "id": take(repo, n, body.implementation_environment)})
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
        latest = len(work.feed.events) - 1
        # A restarted server has a new buffer. Never wait for a cursor from
        # the old process to catch up; clients also refresh their snapshot.
        start_at = latest if after is None else max(-1, min(after, latest))
    response = streaming(work.tail(work.feed, start_at))
    response.headers["X-Feed-Cursor"] = str(start_at)
    return response


@router.get("/api/specs/{sid}/rounds/{n}")
def round_file(sid: str, n: int, what: Literal["order", "result"] = "result") -> dict:
    repo, spec = mine(sid)
    if not spec.get("pr"):
        raise HTTPException(404, "PR 이 없다")
    file = folder(repo.name, spec["pr"]["number"]) / (f"round-{n}.md" if what == "order" else f"round-{n}-result.md")
    if not file.is_file():
        raise HTTPException(404, "그 라운드 파일이 없다")
    return {"path": str(file), "text": file.read_text(encoding="utf-8")}


@router.get("/api/specs/{sid}/review/log")
def review_log(sid: str) -> dict:
    repo, spec = mine(sid)
    if not spec.get("pr"):
        return {"rows": [], "running": None}
    with _lock:
        run = _review_runs.get((repo.name, sid))
    active = run is not None and not run.done
    try:
        lines = (folder(repo.name, spec["pr"]["number"]) / "progress.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
            if not active or row.get("turn") != run.turn:
                rows.append(row)
        except ValueError:
            continue
    return {"rows": rows, "running": {"turn": run.turn, "session_id": run.session_id} if active else None}


@router.get("/api/specs/{sid}/review/events")
def review_events(sid: str, turn: str, after: int = -1) -> StreamingResponse:
    repo, _spec = mine(sid)
    with _lock:
        run = _review_runs.get((repo.name, sid))
    if run is None or run.turn != turn:
        raise HTTPException(410, "그 리뷰 턴은 이제 없다")
    return streaming(work.tail(run, after))


@router.get("/api/specs/{sid}/cloud-instructions")
def cloud_instructions(sid: str) -> dict:
    repo, spec = mine(sid)
    if not verification.cloud(spec) or not spec.get("pr"):
        raise HTTPException(409, "클라우드 구현의 PR 만 인계한다")
    head, _base = pr_head(repo, spec["pr"]["number"])
    return {"text": (
        f"Prepare PR #{spec['pr']['number']} for local review. Current remote head: {head}.\n\n"
        "Commit verification.json with this repository's complete major-flow checklist, API/data contracts, "
        "and runnable verification commands as described in docs/local-verification.md of wiki-agent. "
        "Do not commit .env, credentials, or private datasets.\n\n"
        "Add exactly one fenced cloud-handoff JSON block to the PR body, with version: 1, "
        "implementation: claude-code-cloud, head: the full current commit, summary, run (instructions), "
        "checks (only checks actually completed), and unverified (behavior requiring local execution). "
        "Refresh head after every correction. Do not invent completed checks.\n\n"
        "Read the local review's failure evidence, fix it in cloud, push the correction and update the "
        "handoff. The person then starts the next local review round."
    )}


class Settings(BaseModel):
    rounds: int
    concurrent: int
    review_model: str = ""
    review_effort: str = "high"


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
    # Checked against the model it will run on: an effort that model does not
    # take was saved, and every review round then failed on it.
    try:
        allowed = channels.efforts_of(review_model(model))
    except Exception as exc:
        raise HTTPException(503, f"Codex 모델 목록 확인 실패: {exc}") from exc
    if body.review_effort not in allowed:
        raise HTTPException(400, "이 모델이 지원하지 않는 추론 강도")
    store(rounds=body.rounds, concurrent=body.concurrent, review_model=model, review_effort=body.review_effort)
    with _seats:
        _seats.notify_all()   # more seats may be free now
    return settings()
