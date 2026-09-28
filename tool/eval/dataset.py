"""`python tool/eval/dataset.py [--check] [--calibration [<out>]]`

Stage 10's evaluation set (`eval/jev/intents.json`, `jev-intents/1`): 120
intents, each worded in English only, over one synthetic corpus,
split by intent into calibration and held-out halves. This module is what
every stage 10 tool reads it through: validation, the corpus materialized
(papers registered as the product registers a local paper), a retrieved
chunk named as a label names it, and scoring against the labels.

`--check` validates the file and every label against its corpus.
`--calibration` derives stage 6's calibration split from the calibration
intents alone — route, source, passage, coverage and repair labels, in
`eval/jev/calibration.json`'s shape — for `tool/eval/policy.py --collect`.
The held-out intents never enter it.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.baseline import materialize  # noqa: E402
from search import HUB  # noqa: E402

SCHEMA = "jev-intents/1"
DATASET = HUB / "eval" / "jev" / "intents.json"
CALIBRATION = HUB / "eval" / "jev" / "calibration.json"
CATEGORIES = ("direct", "factual", "routing", "bridge", "multipart", "conflict", "memory", "paper",
              "unanswerable", "adversarial")
SPLITS = ("calibration", "held_out")
# Jev is built and measured in English: every input reaches it through the
# translator, and Korean left its decisions near 50% (version 3 of the set).
LANGUAGES = ("en",)
FAMILIES = ("hub", "documents", "memory", "papers")
REDIRECT = re.compile(r"\b(?:SYSTEM|AI agents?|Assistant|If you are an AI|Ignore the sentence|Disregard your rules|"
                      r"Instruction for assistants|You are now in developer mode|AI):?", re.I)


def sha(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def load(path: Path = DATASET) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    problems = invalid(data)
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems[:5]))
    return data


def invalid(data: dict) -> list[str]:
    """What makes a dataset unusable, as sentences; empty when it is not."""

    if data.get("schema") != SCHEMA:
        return [f"schema is not {SCHEMA}"]
    out = []
    intents = data["intents"]
    ids = [i["id"] for i in intents]
    if len(ids) != len(set(ids)):
        out.append("duplicate intent ids")
    counts: dict[tuple[str, str], int] = {}
    for i in intents:
        where = i["id"]
        if i["category"] not in CATEGORIES or i["split"] not in SPLITS:
            out.append(f"{where}: unknown category or split")
        if set(i["variants"]) != set(LANGUAGES) or not all(v.strip() for v in i["variants"].values()):
            out.append(f"{where}: needs exactly one English wording")
        if (i["abstain"] or i["direct"]) and i["evidence"]:
            out.append(f"{where}: an abstention or a direct answer carries no evidence")
        if not (i["abstain"] or i["direct"]) and not i["evidence"]:
            out.append(f"{where}: no evidence label")
        if not all(isinstance(g, list) and g and all(isinstance(a, str) for a in g) for g in i["evidence"]):
            out.append(f"{where}: an evidence group is a non-empty list of locators")
        if "bridged" in i and not 0 <= i["bridged"] < len(i["evidence"]):
            out.append(f"{where}: bridged names no group")
        counts[(i["category"], i["split"])] = counts.get((i["category"], i["split"]), 0) + 1
    declared = {(c, s): n for c, splits in data["categories"].items() for s, n in splits.items()}
    if declared != counts:
        out.append("the declared category counts are not the intents' counts")
    return out


# -- the corpus -------------------------------------------------------------

def sections(text: str) -> list[tuple[str, str]]:
    """`(heading path, body)` of a synthetic page, as the index cuts it at headings."""

    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    out, path, body = [], [], []
    for line in text.splitlines():
        m = re.match(r"(#+)\s+(.*)", line)
        if m:
            if body and any(b.strip() for b in body):
                out.append((" > ".join(path), "\n".join(body).strip()))
            path = path[:len(m.group(1)) - 1] + [m.group(2).strip()]
            body = []
        else:
            body.append(line)
    if any(b.strip() for b in body):
        out.append((" > ".join(path), "\n".join(body).strip()))
    return out


def pages(data: dict) -> dict[str, str]:
    """Every page by its label prefix: `hub/…`, `repo/…` and `papers/<file>`."""

    corpus = data["corpus"]
    return {**corpus["files"], **{f"papers/{name}": text for name, text in corpus.get("papers", {}).items()}}


def passage(data: dict, locator: str) -> tuple[str, str]:
    """`(heading, text)` a locator names: one section, or a whole page."""

    name, _, heading = locator.partition("#")
    found = sections(pages(data)[name])
    if heading:
        return next((h, b) for h, b in found if h.split(" > ")[-1] == heading)
    return found[0][0].split(" > ")[0], "\n\n".join(b for _h, b in found)


def unresolved(data: dict) -> list[str]:
    """Locators that name no page or no section of the corpus."""

    known = pages(data)
    out = []
    for i in data["intents"]:
        for loc in (a for g in i["evidence"] for a in g):
            name, _, heading = loc.partition("#")
            if name not in known or (heading and heading not in
                                     [h.split(" > ")[-1] for h, _b in sections(known[name])]):
                out.append(f"{i['id']}: {loc}")
    return out


@contextlib.contextmanager
def corpus(data: dict, scratch: Path):
    """The corpus written under `scratch`, papers registered in its repository
    as `knowledge.add_file` registers a local paper, and `label` for its
    chunks. The records and the index this makes under the user cache are
    removed on the way out: they are this run's, keyed by a scratch path
    nothing will ask for again."""

    from main import knowledge
    from search import sources

    import search

    hub, repo = materialize(scratch, {"kind": "synthetic", "files": data["corpus"]["files"]}, None)
    before = (search.HUB, knowledge.HUB)
    search.HUB = knowledge.HUB = hub
    folder = scratch / "papers"
    try:
        folder.mkdir()
        for name, text in data["corpus"].get("papers", {}).items():
            (folder / name).write_bytes(text.encode("utf-8"))
            record = knowledge.add_file(repo, folder / name)
            if record["status"] != "indexed":
                raise RuntimeError(f"paper {name} was not indexed: {record['status']}")

        def label(chunk: dict) -> str:
            heading = chunk["heading_path"][-1] if chunk.get("heading_path") else ""
            origin = (chunk.get("record") or chunk.get("source_record") or {}).get("origin")
            if origin:
                name = f"papers/{Path(origin).name}"
            else:
                path = Path(chunk["path"]).resolve()
                name = next((f"{n}/{path.relative_to(root.resolve()).as_posix()}"
                             for n, root in (("repo", repo), ("hub", hub)) if root.resolve() in path.parents),
                            path.as_posix())
            return f"{name}#{heading}"

        yield SimpleNamespace(hub=hub, repo=repo, label=label)
    finally:
        search.HUB, knowledge.HUB = before
        for root in (repo, hub):
            shutil.rmtree(sources.records_folder(root), ignore_errors=True)


def snapshot(data: dict) -> dict:
    """The corpus's identity: every page by label and hash, and one hash of them all."""

    listed = sorted((name, sha(text.encode("utf-8"))) for name, text in pages(data).items())
    return {"files": len(listed), "sha256": sha("\n".join(f"{n} {h}" for n, h in listed).encode()),
            "snapshot": listed}


# -- scoring ----------------------------------------------------------------

def family(locator: str) -> str:
    return ("hub" if locator.startswith("hub/") else "memory" if locator.startswith("repo/.wiki/memory/")
            else "papers" if locator.startswith("papers/") else "documents")


def needed(intent: dict) -> list[str]:
    """The source families without which a group cannot be met: every alternative of it lies there."""

    return sorted({family(g[0]) for g in intent["evidence"] if len({family(a) for a in g}) == 1})


def met(group: list[str], labels: list[str]) -> bool:
    """Whether any alternative of `group` is among `labels`: `file` by any of its sections."""

    files = {x.split("#")[0] for x in labels}
    return any((alt in labels) if "#" in alt else (alt in files) for alt in group)


def recall(intent: dict, labels: list[str]) -> float | None:
    groups = intent["evidence"]
    return sum(met(g, labels) for g in groups) / len(groups) if groups else None


def variants(data: dict, split: str | None = None, languages=LANGUAGES, ids=None) -> list[dict]:
    """One row per wording: `{"key", "intent", "language", "text"}`."""

    return [{"key": f"{i['id']}:{lang}", "intent": i, "language": lang, "text": i["variants"][lang]}
            for i in data["intents"] if (split is None or i["split"] == split) and (not ids or i["id"] in ids)
            for lang in languages]


# -- stage 6's calibration split, derived ----------------------------------------

STOP = {"the", "and", "which", "what", "does", "how", "who", "when", "where", "are", "for", "per", "did", "that",
         "this", "with", "from", "long", "many", "much", "say", "about", "under", "its", "our", "each", "one"}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP}


def distractor(data: dict, question: str, exclude: set[str]) -> tuple[str, str, str]:
    """The section outside `exclude` sharing the most words with `question`: a hard negative."""

    best = None
    for name, text in sorted(pages(data).items()):
        if name in exclude:
            continue
        for heading, body in sections(text):
            score = len(words(question) & words(heading + " " + body))
            if best is None or score > best[0]:
                best = (score, name, heading, body)
    return best[1], best[2], best[3]


def calibration(data: dict) -> dict:
    """Stage 6's calibration split from the calibration intents' English wordings.

    Each evidence intent gives a case with its evidence and one hard negative
    (every requirement covered), and a case with part of it missing (not
    covered) labelled with the repair that finds it: the linked page for a
    bridge (`bridge`), another ask for a multi-part question (`subqueries`),
    a family not yet searched for evidence that lives in one (`sources`).
    A superseded or disagreeing page is labelled a conflict; a page that
    addresses the reader, a redirect. Disagreeing pages get no missing case:
    either alone still answers, only without the disagreement. A direct intent is one route case; an
    unanswerable one, a case whose nearest passage covers nothing."""

    cases = []

    def item(locator: str, useful: bool, conflict: bool = False) -> dict:
        heading, text = passage(data, locator)
        return {"heading": heading, "text": text, "useful": useful, "conflict": conflict,
                "redirect": bool(REDIRECT.search(text))}

    for i in (i for i in data["intents"] if i["split"] == "calibration"):
        q = i["variants"]["en"]
        base = {"query": q, "current_state": ""}
        if i["direct"]:
            cases.append({"id": f"{i['id']}", **base, "retrieve": False})
            continue
        if i["abstain"]:
            name, heading, body = distractor(data, q, set())
            cases.append({"id": i["id"], **base, "retrieve": True,
                          "passages": [{"heading": heading, "text": body, "useful": False, "conflict": False,
                                        "redirect": bool(REDIRECT.search(body))}],
                          "requirements": [{"text": q, "covered": False}]})
            continue
        want = needed(i)
        gold = [g[0] for g in i["evidence"]]
        old = [loc for loc in data["corpus"]["files"] if i.get("conflict") and len(gold) == 1
               and loc.startswith("repo/.wiki/decisions/") and superseded_by(data, loc) == gold[0]]
        disagree = i.get("conflict") and len(gold) > 1
        passages = ([item(g, True, bool(disagree)) for g in gold] + [item(o, True, True) for o in old])
        every = {a.split("#")[0] for g in i["evidence"] for a in g} | set(old)
        name, heading, body = distractor(data, q, every)
        negative = {"heading": heading, "text": body, "useful": False, "conflict": False,
                    "redirect": bool(REDIRECT.search(body))}
        sources = {f: f in want for f in FAMILIES}
        cases.append({"id": f"{i['id']}", **base, "retrieve": True, "sources": sources,
                      "passages": passages + [negative], "requirements": [{"text": q, "covered": True}]})
        if len(gold) > 1 and not disagree:
            kept =[p for n, p in enumerate(passages[:len(gold)]) if n != i.get("bridged", len(gold) - 1)]
            choice = "bridge" if "bridged" in i else "subqueries"
            options = ["bridge", "subqueries", "external"] if choice == "subqueries" else ["sources", "bridge",
                                                                                            "external"]
            repair = {"options": options, "choice": choice,
                      **({"rest": [f for f in FAMILIES if f not in want]} if "sources" in options else {})}
            cases.append({"id": f"{i['id']}-missing", **base, "retrieve": True, "passages": kept + [negative],
                          "requirements": [{"text": q, "covered": False}], "repair": repair})
        elif want and want[0] != "documents":
            cases.append({"id": f"{i['id']}-missing", **base, "retrieve": True, "passages": [negative],
                          "requirements": [{"text": q, "covered": False}],
                          "repair": {"options": ["sources", "bridge", "external"], "rest": want, "choice": "sources"}})
    return {"schema": "jev-calibration/1", "id": "calibration", "version": 2, "split": "calibration",
            "derived_from": {"path": "eval/jev/intents.json", "version": data["version"],
                             "sha256": sha(DATASET.read_bytes()) if DATASET.exists() else None},
            "description": "Stage 6's calibration split, derived by `tool/eval/dataset.py --calibration` from the "
                           "calibration half of stage 10's intents (English wordings only; the held-out half never "
                           "enters it). Replaces stage 6's 35 synthetic cases. Labels follow the intents' labels.",
            "cases": cases}


def superseded_by(data: dict, locator: str) -> str | None:
    """The decision page whose front matter supersedes the one at `locator`."""

    stem = Path(locator).stem
    for name, text in data["corpus"]["files"].items():
        m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
        if m and re.search(rf"supersedes:\s*\[[^\]]*\b{re.escape(stem)}\b", m.group(1)):
            return name
    return None


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/dataset.py", description="Stage 10's evaluation set")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--calibration", type=Path, nargs="?", const=CALIBRATION, default=None)
    args = parser.parse_args(argv)
    data = load(args.dataset)
    if args.check:
        missing = unresolved(data)
        print("\n".join(missing) or f"{len(data['intents'])} intents, labels resolve; "
                                    f"reviewed by {data['labels']['reviewed_by'] or 'nobody yet'}")
        if missing:
            return 1
    if args.calibration:
        out = calibration(data)
        args.calibration.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{args.calibration}: {len(out['cases'])} cases, "
              f"{sum('repair' in c for c in out['cases'])} with a repair label")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
