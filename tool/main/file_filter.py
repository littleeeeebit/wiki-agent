"""`work.files`: which repository files a write session's first turn loads.

Code builds the candidates (`candidates`): the spec's grounds files, the
search index's hits for its goal, and the files merged specs with
overlapping grounds changed, at most `decisions.MAX_FILES`, each existing
at the revision the session starts from. Jev answers one relevance Noul per
candidate, all in one request over the spec's state (`judge`). It never
names a file: listing is generation, judging each one is not.

A file Jev judges not needed (at or under the policy's `no`, provisional
0.2) is dropped; a yes or a doubt keeps it. Kept files are loaded into the
first turn, cut to `LINES` lines and `CHARS` characters each and `TOTAL` in
all; dropped ones are listed by path under "Available, not loaded", so a
wrong drop costs the agent one read, not the task (`render`).

No key, mode off, a normalization that failed, or a result with no answers
(any status but decided or uncertain) loads the full list and records
`fallback` with its reason: never a silent drop. Every work start keeps one
`action-record/1` row with what was offered, kept and dropped, each
probability, and the threshold used.

Later turns are not filtered; the agent opens what it needs itself.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path

import decision
import search
from common.budget import ACTION, Budget
from session_state import run as git

from . import decisions
from .query import ROOT

POINT = "work.files"
ARTIFACT = Path("eval") / "jev" / "file-policy.json"
PROMPT = ("Does the agent need to read the file {path} (candidate {id} in the state) to carry out the "
          "specification in the state? Answer yes when the change the specification asks for, or a value, "
          "rule or decision it depends on, is likely in that file; no when the file has nothing to do with it.")
VERSION = hashlib.sha256(PROMPT.encode()).hexdigest()[:16]
LINES, CHARS, TOTAL = 200, 8000, 60000
SEARCH_K, SEARCH_SECONDS = 20, 5.0
CITED = re.compile(r":\d+(?:-\d+)?$")
MERGED = "머지됨"
READY = ("decided", "uncertain")


# -- the candidates -----------------------------------------------------------

class Tree:
    """The files of `root` at `rev`, read from git: a fresh task worktree's
    HEAD at work start, a merged spec's base commit in a replay."""

    def __init__(self, root: Path, rev: str = "HEAD"):
        self.root, self.rev = root, rev
        self.files = set(git(root, "-c", "core.quotepath=off", "ls-tree", "-r", "--name-only", rev).splitlines())

    def read(self, path: str) -> str | None:
        """The file's text, `None` when git cannot show it or it is binary."""

        text = git(self.root, "show", f"{self.rev}:{path}") if path in self.files else ""
        return text if text and "\x00" not in text[:8000] else None


def grounded(spec: dict) -> list[str]:
    """The spec's grounds files as paths, their `:line` cites cut off."""

    return list(dict.fromkeys(CITED.sub("", f).strip() for f in spec["grounds"].get("files", []) if f.strip()))


def searched(goal: str, repo: Path) -> list[str]:
    """Repository paths of the search index's hits for `goal`; none when the
    daemon gives no answer — it starts for next time."""

    out = []
    for hit in search.ask(goal, repo, SEARCH_SECONDS, k=SEARCH_K) or []:
        path = (hit.get("locator") or {}).get("path")
        try:
            inside = Path(hit.get("path") or "").resolve().is_relative_to(repo.resolve())
        except (OSError, ValueError):
            inside = False
        if isinstance(path, str) and inside:
            out.append(path)
    return list(dict.fromkeys(out))


def merged_at(spec: dict) -> float:
    return max((h.get("ts") or 0 for h in spec.get("history") or [] if h.get("state") == MERGED), default=0.0)


def merged(repo_name: str, before: float | None = None, exclude: str = "") -> list[dict]:
    """The repository's merged specs with a merge commit, latest first; with
    `before`, only those merged before then — a replay must not see the future."""

    from .specs import SPECS  # `specs` imports `decisions`, which imports this module

    out = []
    for file in (SPECS / repo_name).glob("*.json"):
        try:
            s = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        when = merged_at(s)
        if s.get("state") == MERGED and (s.get("merge") or {}).get("commit") and s.get("id") != exclude \
                and (before is None or when < before):
            out.append(s)
    return sorted(out, key=merged_at, reverse=True)


def candidates(spec: dict, repo: Path, tree: Tree, *, hits: list[str] | None = None,
               before: float | None = None) -> list[dict]:
    """`[{path, via}]`: grounds files, then search hits, then files merged
    specs with overlapping grounds changed — present in `tree`, at most
    `MAX_FILES`. `hits` stands in for the search (`searched`)."""

    ground = grounded(spec)
    found: dict[str, list[str]] = {}

    def add(path: str, via: str) -> None:
        if path in tree.files and via not in found.setdefault(path, []):
            found[path].append(via)

    for p in ground:
        add(p, "grounds")
    for p in searched(spec["goal"], repo) if hits is None else hits:
        add(p, "search")
    for s in merged(spec["repo"], before, spec["id"]) if ground else []:
        if set(grounded(s)) & set(ground):
            commit = s["merge"]["commit"]
            for p in git(repo, "-c", "core.quotepath=off", "diff", "--name-only", f"{commit}^1", commit).splitlines():
                add(p, f"merged:{s['id']}")
    return [{"path": p, "via": via} for p, via in list(found.items())[:decisions.MAX_FILES]]


# -- the judgement ------------------------------------------------------------

def state_of(spec: dict, offered: list[dict]) -> dict:
    return {"task": "An agent is about to start work on this specification; the server loads into its first turn "
                    "only the candidate files it needs to read.",
            "specification": {"goal": spec["goal"], "acceptance_criteria": spec["done"][1:],
                              "out_of_scope": spec["out"], "decisions": [d["what"] for d in spec["decisions"]]},
            "candidates": [{"id": f"f{i}", "path": c["path"], "source": ", ".join(c["via"])}
                           for i, c in enumerate(offered, 1)]}


def policy(cfg: decision.Config) -> decision.Policy:
    return decision.policy(cfg.model, ROOT / ARTIFACT, prompt_version=VERSION)


def judge(spec: dict, offered: list[dict], cfg: decision.Config, *, cancel=None, occasion: str = "") -> dict:
    """The record of one work start's filter. `kept` and `dropped` are
    `[{path, p}]`; on `fallback` every candidate is kept with `p` `None`."""

    rec = {"schema_version": decisions.RECORD, "id": uuid.uuid4().hex, "ts": time.time(), "point": POINT,
           "occasion": occasion, "mode": cfg.mode, "prompt_version": VERSION, "offered": offered,
           "threshold": None, "status": "fallback", "reason": "", "jev": None}
    keep = [{"path": c["path"], "p": None} for c in offered]
    if not offered:
        return {**rec, "status": "empty", "kept": [], "dropped": []}
    if not cfg.key:
        return {**rec, "reason": "no_key", "kept": keep, "dropped": []}
    if cfg.mode == "off":
        return {**rec, "reason": "mode_off", "kept": keep, "dropped": []}
    pol = policy(cfg)
    rec ={**rec, "threshold": pol.rules["relevance"]["no"], "policy": pol.record()}
    budget = Budget(**ACTION, cancel=cancel)
    try:
        state, version = decisions.normalized(state_of(spec, offered), decisions.knowledge.NORMALIZE_SECONDS)
        if state is None:
            return {**rec, "reason": "normalization_failed", "kept": keep, "dropped": []}
        ids = [c["id"] for c in state["candidates"]]
        questions = {i: {"decision": "relevance", "candidate": i,
                         "question": decision.noul(PROMPT.format(path=decisions.HANGUL.sub("…", c["path"]), id=i))}
                     for i, c in zip(ids, offered)}
        req = decision.request("relevance", state, questions, allowed=ids, model=cfg.model, prompt_version=VERSION,
                               policy_version=pol.version, normalization_version=version, budget=budget)
        res = decision.checked(req, decision.decide(req, decisions.transport(cfg), budget, [], pol))
    except Exception as exc:  # noqa: BLE001 — a broken filter loads everything; it never drops silently
        return {**rec, "reason": f"error:{type(exc).__name__}", "kept": keep, "dropped": []}
    rec["jev"] = {"request_id": req["request_id"], "status": res["status"], "reason": res["reason_code"],
                  "model": res["model"], "usage": res["usage"], "elapsed_ms": res["elapsed_ms"]}
    if res["status"] not in READY:
        return {**rec, "reason": f"{res['status']}:{res['reason_code']}", "kept": keep, "dropped": []}
    judged = [({"path": c["path"], "p": res["answers"][i]}, res["verdicts"][i]) for i, c in zip(ids, offered)]
    return {**rec, "status": "filtered", "kept": [f for f, v in judged if v != "no"],
            "dropped": [f for f, v in judged if v == "no"]}


# -- the first turn -----------------------------------------------------------

def fenced(text: str) -> str:
    ticks = "`" * max(3, 1 + max((len(m) for m in re.findall(r"`+", text)), default=0))
    return f"{ticks}\n{text}\n{ticks}"


def render(kept: list[str], dropped: list[str], tree: Tree) -> str:
    """The first turn's file section: kept files loaded, cut; dropped ones by path."""

    if not kept and not dropped:
        return ""
    parts, unloaded, left = [], [], TOTAL
    for path in kept:
        text = tree.read(path)
        lines = (text or "").splitlines()
        shown = "\n".join(lines[:LINES])[:CHARS]
        if text is None or len(shown) > left:
            unloaded.append(path)
            continue
        left -= len(shown)
        cut = "" if shown == text else f", cut to {shown.count(chr(10)) + 1} of {len(lines)} lines"
        parts.append(f"### `{path}`{cut}\n{fenced(shown)}")
    out = []
    if parts or unloaded:
        out.append("Files the server judged this spec needs, loaded from the starting revision:\n\n" + "\n\n".join(parts))
    if unloaded:
        out.append("Also needed, not loaded (binary or over the size limit):\n"
                   + "\n".join(f"- `{p}`" for p in unloaded))
    if dropped:
        out.append("Available, not loaded — open any of these if the work turns out to need it:\n"
                   + "\n".join(f"- `{p}`" for p in dropped))
    return "\n\n" + "\n\n".join(out)


def first(spec: dict, repo: Path, path: Path, cfg: decision.Config, *, cancel=None, occasion: str = "",
          run=None) -> str:
    """The file section of the work start in `path`, once it is recorded."""

    tree = Tree(path)
    offered = candidates(spec, repo, tree)
    rec = judge(spec, offered, cfg, cancel=cancel, occasion=occasion)
    decisions.keep((spec["repo"], spec["id"]), rec)
    if run is not None and offered:
        decisions.note(run, f"Jev · 파일 {len(offered)}개 중 {len(rec['kept'])}개를 싣는다" if rec["status"] == "filtered"
                       else f"파일 거르기 안 됨({rec['reason']}) — {len(offered)}개 모두 싣는다")
    return render([f["path"] for f in rec["kept"]], [f["path"] for f in rec["dropped"]], tree)
