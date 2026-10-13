"""`python tool/eval/korean.py --check [--pairs <file>]`

Reliability PR 9's paired experiment (`docs/plans/reliability/9-korean.md`,
v1): a reviewed English intent of v8 beside its Korean wording, and each file
its evidence names beside a Korean twin at the same path. Four arms on the
product path — A en/EN, B ko/EN, C en/KO, D ko/KO — run in
`compare.py --experiment pairs`. This module is the set, the KO corpus, the
checks that need no host, and the report's pair gates and attribution.

`--check` validates the pair file against its dataset: shapes, eligibility,
heading maps and fact units, the seven groups of twelve, and every label on
the English original it stands for. The output
overlay check is `tool/eval/overlay.py`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import dataset  # noqa: E402
from search import HUB, evidence  # noqa: E402

SCHEMA = "jev-korean-pairs/1"
PAIRS = HUB / "eval" / "jev" / "korean" / "pairs.json"
# The live groups in allocation order: the scarce types first (v1, Pairs).
GROUPS = ("conflict", "pasted", "analysis", "names", "multipart", "negation", "numbers")
PER_GROUP = 12
# Each arm's question language and corpus. All four run Jev, the graph lane and claim verification.
ARMS = {"A": ("en", "EN"), "B": ("ko", "EN"), "C": ("en", "KO"), "D": ("ko", "KO")}
# Long enough for a whole twin section: the fact pass is not a turn, and what it normalizes is cached for the arms.
FACT_SECONDS = 120.0
HANGUL = re.compile(r"[가-힣]")
# A sentence ends at its punctuation; a soft line break inside it is not an end.
SENTENCE = re.compile(r"(?<=[.!?])\s+")
# Lines that are a block of their own (a heading, a table row) or start one (a list item, a quote).
OWN_LINE, STARTS = re.compile(r"#|\|"), re.compile(r"[-*+]\s|\d+[.)]\s|>")
# Polarity, after NegEx (Chapman 2001; docs/research/korean-set-labels.md):
# a cue, the phrases that hold one but negate nothing, and what ends a cue's clause.
# A cue governs up to five words after it, or three before it (`deals are not tracked`);
# a comma, colon or point inside a number ends nothing.
CUE = re.compile(r"\b(?:not|no|never|none|nothing|neither|nor|without|cannot|unless)\b|n't\b", re.I)
PSEUDO = re.compile(r"\b(?:no|not) (?:more|less|later|earlier) than\b|\bnot only\b|\bnot exceed|"
                    r"\bwithout (?:delay|fail)\b|\bno matter\b|\bno doubt\b", re.I)
CLAUSE = re.compile(r"[;!?\n]|[,.:](?!\d)|\b(?:but|although|though|however|whereas|while|which|except)\b", re.I)
BEFORE, AFTER = 5, 3


# -- the set --------------------------------------------------------------------

def load(path: Path = PAIRS) -> tuple[dict, dict]:
    """The pair file and the dataset it pairs, both valid and the set whole
    (seven groups of twelve: the frozen cohort), or `ValueError`."""

    pairs = json.loads(path.read_text(encoding="utf-8"))
    data = dataset.load(HUB / pairs["dataset"]["path"])
    problems = invalid(pairs, data) or allocation(pairs, data)
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems[:5]))
    return pairs, data


def files(intent: dict) -> set[str]:
    return {a.split("#")[0] for g in intent["evidence"] for a in g}


def phrases(groups) -> bool:
    return isinstance(groups, list) and all(isinstance(g, list) and g and all(isinstance(p, str) and p for p in g)
                                            for g in groups)


def strings(items) -> bool:
    return isinstance(items, list) and all(isinstance(s, str) and s for s in items)


def invalid(pairs: dict, data: dict) -> list[str]:
    """What makes the pair file unusable with `data`, as sentences; empty when nothing does."""

    if pairs.get("schema") != SCHEMA:
        return [f"schema is not {SCHEMA}"]
    out = []
    if dataset.sha((HUB / pairs["dataset"]["path"]).read_bytes()) != pairs["dataset"]["sha256"]:
        out.append("the dataset is not the one the pairs were made on")
    intents = {i["id"]: i for i in data["intents"]}
    seen: set[str] = set()
    for p in pairs["pairs"]:
        where, i = p.get("intent"), intents.get(p.get("intent"))
        if i is None:
            out.append(f"{where}: no such intent")
            continue
        if where in seen:
            out.append(f"{where}: in two pairs")
        seen.add(where)
        if p.get("group") not in GROUPS:
            out.append(f"{where}: group is one of {GROUPS}")
        # Recall and coverage apply to every pair, so a missing value is a failure, never a normal N/A.
        if not i["evidence"] or not i["parts"] or i.get("fault"):
            out.append(f"{where}: a pair's intent has evidence and parts, and no fault")
        if not isinstance(p.get("input", {}).get("ko"), str) or not HANGUL.search(p["input"]["ko"]):
            out.append(f"{where}: input.ko is a Korean wording")
        m = p.get("meaning") or {}
        if set(m) - {"asked"} != {"require", "forbid", "literals"} or not phrases(m["require"]) or \
                not phrases(m.get("asked", [])) or not all(strings(m[k]) for k in ("forbid", "literals")):
            out.append(f"{where}: meaning is {{require: [[...]], asked?: [[...]], forbid: [...], literals: [...]}}")
        if p.get("review") not in (pairs["labels"].get("reviews") or {}):
            out.append(f"{where}: no recorded review")
        out += [f"{where}: {page} has no twin" for page in sorted(files(i) - set(pairs["twins"]))]
    known = dataset.pages(data)
    for page, twin in sorted(pairs["twins"].items()):
        if page not in known:
            out.append(f"{page}: no such page")
            continue
        out += [f"{page}: {problem}" for problem in mapped(known[page], twin)]
    return out


def sentences(text: str) -> list[str]:
    """`text`'s sentences: a blank line, a heading, a table row, a list item
    or a quote ends one; a soft line break inside a paragraph does not."""

    blocks, piece = [], []
    for line in text.split("\n"):
        s = line.strip()
        if not s or OWN_LINE.match(s) or STARTS.match(s):
            blocks.append(" ".join(piece))
            piece = []
            if OWN_LINE.match(s):
                blocks.append(s)
                continue
        if s:
            piece.append(s)
    blocks.append(" ".join(piece))
    return [s.strip() for b in blocks for s in SENTENCE.split(b) if s.strip()]


def mapped(english: str, twin: dict) -> list[str]:
    """What is wrong with one twin: its sections are the English page's, in
    order, each heading mapped; and each fact unit names a section and a
    sentence the twin has."""

    if not isinstance(twin.get("text"), str) or not HANGUL.search(twin["text"]):
        return ["text is a Korean twin"]
    heads = twin.get("headings") or {}
    en, ko = dataset.sections(english), dataset.sections(twin["text"])
    want = [" > ".join(heads.get(h, f"<{h} unmapped>") for h in path.split(" > ")) for path, _b in en]
    out = [] if want == [path for path, _b in ko] else [f"sections {[p for p, _b in ko]} are not {want}"]
    # A chunk is named by its page and last heading, so a fact's section must be the only one of its name.
    for sections in (en, ko):
        leaves = [path.split(" > ")[-1] for path, _b in sections]
        out += [f"two sections named {name!r}" for name in sorted({x for x in leaves if leaves.count(x) > 1})]
    body = {path.split(" > ")[-1]: b for path, b in ko}
    ids = [f.get("id") for f in twin.get("facts", [])]
    if len(ids) != len(set(ids)) or not all(isinstance(x, str) and x for x in ids):
        out.append("fact ids are distinct names")
    for f in twin.get("facts", []):
        said = sentences(body.get(heads.get(f.get("section"), ""), ""))
        if not said or not isinstance(f.get("sentence"), int) or not 0 <= f["sentence"] < len(said):
            out.append(f"fact {f.get('id')}: names no sentence of a twin section")
        if not phrases(f.get("require")) or not strings(f.get("literals")) or not (f["require"] or f["literals"]):
            out.append(f"fact {f.get('id')}: require [[...]] and literals [...], not both empty")
    if not twin.get("facts"):
        out.append("no fact unit")
    return out


def allocation(pairs: dict, data: dict) -> list[str]:
    """Whether the set fills seven groups of twelve. Which group an intent
    with several traits goes to, and whether a held-out one was passed over
    for a calibration one, is the set review's, by the order of `GROUPS`
    (v1, Pairs); `--check` prints each group's calibration count for it."""

    counts = {g: sum(p["group"] == g for p in pairs["pairs"]) for g in GROUPS}
    return [f"{g}: {n} pairs, not {PER_GROUP}" for g, n in counts.items() if n != PER_GROUP]


def calibration(pairs: dict, data: dict) -> dict[str, int]:
    split = {i["id"]: i["split"] for i in data["intents"]}
    return {g: sum(split[p["intent"]] == "calibration" for p in pairs["pairs"] if p["group"] == g) for g in GROUPS}


def ko_corpus(data: dict, pairs: dict) -> dict:
    """The KO corpus's dataset: `data` with every twin in place of its page.
    Every other page stays English, so it measures a mixed repository."""

    out = json.loads(json.dumps(data))
    for page, twin in pairs["twins"].items():
        if page.startswith("papers/"):
            out["corpus"]["papers"][page.removeprefix("papers/")] = twin["text"]
        else:
            out["corpus"]["files"][page] = twin["text"]
    return out


def relabel(pairs: dict):
    """A KO chunk's label as the English labels name it: a twin's Korean heading mapped back."""

    back = {page: {ko: en for en, ko in t["headings"].items()} for page, t in pairs["twins"].items()}

    def label(name: str) -> str:
        page, _, heading = name.partition("#")
        return f"{page}#{back.get(page, {}).get(heading, heading)}"

    return label


# -- running the pairs (`compare.py --experiment pairs`) ------------------------------------

def options(args, opts: dict, pairs: dict, data: dict) -> dict:
    """A pairs run's options from `compare.py`'s arguments: both corpora by
    hash, and which arm read which (v1, The English control). With `--plan`
    (a report's JSON) it repeats that report's base run, refused when it
    would measure anything else."""

    from eval import compare

    made = json.loads(args.plan.read_text(encoding="utf-8")) if args.plan else None
    plan = made["pairs"]["repetition"]["plan"] if made else None
    opts = {**opts, "pairs": {"path": compare.named(args.pairs), "version": pairs["version"],
                              "sha256": compare.sha(args.pairs.read_bytes())},
            "arms": args.arms, "inputs": {a: list(ARMS[a]) for a in args.arms},
            "corpora": {"EN": dataset.snapshot(data)["sha256"], "KO": dataset.snapshot(ko_corpus(data, pairs))["sha256"]},
            "level": "answer", "reps": [1, 2] if plan else [0], "plan": plan, "regrade": 1,
            "cache": args.cache, "method": args.method, "k": args.k, "model": args.model, "grader": args.grader}
    if made:
        base = made["runs"]["pairs"]
        if wrong := differ(opts, base["options"]):
            raise SystemExit(f"--plan's run measured something else ({', '.join(wrong)})")
        opts["base"] = base["folder"]
    return opts


def differ(opts: dict, base: dict) -> list[str]:
    """The options in which a repetition run is not its base run's measurement:
    every one but the repetitions, the plan and the base itself."""

    return sorted(k for k in set(opts) | set(base) if k not in ("reps", "plan", "base") and opts.get(k) != base.get(k))


def units(pairs: dict, data: dict, opts: dict) -> list[dict]:
    """Each pair through each arm, all on the product path: the question in
    the arm's language, read against the arm's corpus. A repetition run
    (`plan`) runs repetitions 1 and 2 of A and the planned arms."""

    intents = {i["id"]: i for i in data["intents"]}
    plan = opts.get("plan")
    return [{"key": f"{p['intent']}:{arm}:{rep}", "intent": intents[p["intent"]], "language": lang,
             "text": p["input"]["ko"] if lang == "ko" else intents[p["intent"]]["variants"]["en"],
             "arm": arm, "rep": rep, "corpus": corpus, "jev": True, "graph": True}
            for p in sorted(pairs["pairs"], key=lambda p: p["intent"])
            if (not opts["ids"] or p["intent"] in opts["ids"]) and (plan is None or p["intent"] in plan)
            for arm, (lang, corpus) in ARMS.items()
            if arm in opts["arms"] and (plan is None or arm == "A" or arm in plan[p["intent"]])
            for rep in opts["reps"]]


def digest(text: str | None) -> str | None:
    return evidence.digest(text)[:16] if text is not None else None


def run(folder: Path, opts: dict, data: dict, ceiling, record: dict) -> dict:
    """The EN corpus's units, then the KO corpus's, each on its own index.
    Before the KO units, once per run, every twin's fact units are checked
    against the English their sections normalize to (`facts.json`, with the
    digest of each English text it checked): what the KO arms read is then
    the cached English that was checked, and each row records the digests it
    read so the report can tell. A private source's English lives in its
    index's evidence store, which goes with each batch's scratch, so the run
    keeps it (`private.json`) and puts it back in every later batch's store —
    a repetition's from its base run's — instead of normalizing it afresh."""

    from eval import compare
    from main import knowledge

    import search

    pairs = json.loads((HUB / opts["pairs"]["path"]).read_text(encoding="utf-8"))
    todo = units(pairs, data, opts)
    done = {r["key"] for r in compare.rows(folder, "pairs")}
    checked, kept = folder / "facts.json", folder / "private.json"
    base = Path(opts["base"]) if opts.get("base") else None
    private: dict[str, dict] = {}
    for source in (base / "private.json" if base else None, kept):
        if source and source.exists():
            private |= json.loads(source.read_text(encoding="utf-8"))["english"]

    def ready(c, index) -> None:
        store = search.evidence_store(c.repo)
        try:
            for x in index.chunks:
                if x["visibility"] == "private":
                    texts = (x["text"], " > ".join(x["heading_path"]))
                    store.keep_english(x["source_id"], [(t, private[t]) for t in texts if t in private])
        finally:
            store.close()
        if name != "KO" or opts["reps"] != [0] or checked.exists():
            return
        made: dict[str, str | None] = {}

        def english(chunks: list[dict]) -> list[dict]:
            owners = [(x["source_id"],) if x["visibility"] == "private" else () for x in chunks]
            out = knowledge.english([x["text"] for x in chunks], FACT_SECONDS, owners, project=c.repo)
            made.update({digest(x["text"]): digest(o["text"]) for x, o in zip(chunks, out)})
            return out

        found = facts(pairs, index.chunks, c.label, english)
        compare.write(checked, {"at": compare.now(), "facts": found, "english": made})

    real = knowledge.english

    def keeping(texts, seconds, owners=None, project=None, cancel=None):
        out = real(texts, seconds, owners, project, cancel)
        private.update({t: o for t, o, w in zip(texts, out, owners or [()] * len(texts))
                        if w and o["status"] == "translated"})
        return out

    last = {"left": 0}
    for name, corpus in (("EN", data), ("KO", ko_corpus(data, pairs))):
        mine = [u for u in todo if u["corpus"] == name]
        first = name == "KO" and opts["reps"] == [0] and not checked.exists()
        if ceiling.over() or (not first and all(u["key"] in done for u in mine)):
            continue
        try:
            with patch.object(knowledge, "english", keeping):
                last = compare.measure(folder, opts, data, corpus, ceiling, record, "pairs", mine,
                                       rename=relabel(pairs) if name == "KO" else None, before=ready,
                                       health=f"graph_health_{name}")
        finally:
            # This run's own private texts, beside its rows, never in the translator's shared cache.
            if private:
                compare.write(kept, {"english": private})
    return last


@contextlib.contextmanager
def normalizing():
    """Every English normalization `knowledge` asks for meanwhile, as `(text, outcome)`."""

    from main import knowledge

    seen: list[tuple[str, dict]] = []
    real = knowledge.english

    def english(texts, seconds, owners=None, project=None, cancel=None):
        out = real(texts, seconds, owners, project, cancel)
        seen.extend(zip(texts, out))
        return out

    with patch.object(knowledge, "english", english):
        yield seen


def translation(text: str, seen: list[tuple[str, dict]], asked: int | None = None) -> dict:
    """How a pairs row's question and evidence came to English: the question's
    outcome — status, cached or fresh, the translator's model and prompt
    version — and how many evidence texts were cached, fresh or failed.
    `read` lists every English text read, from priming to the grade, as
    `[text digest, English digest]`; a text read as two Englishes is there twice.
    The question is the measured `prepare`'s, the last of the first `asked`
    normalizations: a warm run primes first, and the answer may normalize more."""

    question = next((o for t, o in reversed(seen[:asked]) if t == text), None)
    rest = [(t, o) for t, o in seen if t != text and o["status"] != "original_english"]
    return {"question": {k: question.get(k) for k in ("status", "reason", "cached", "model", "prompt_version",
                                                      "version")} if question else None,
            "evidence": {"cached": sum(bool(o.get("cached")) for _t, o in rest),
                         "fresh": sum(o["status"] == "translated" and not o.get("cached") for _t, o in rest),
                         "failed": sum(o["status"] not in evidence.USABLE for _t, o in rest),
                         # A failed read has no English (None): kept, and sorted beside the text's others.
                         "read": sorted({(digest(t), digest(o.get("text"))) for t, o in rest},
                                        key=lambda p: (p[0], p[1] or ""))}}


# -- what is checked without a host -----------------------------------------------------

def lost(text: str, require: list[list[str]], literals: list[str]) -> list[str]:
    """What a unit lost in `text`: empty when one sentence meets every
    `require` group (any phrase, any case) and holds every literal exactly.
    A negation and what it negates in two sentences do not pass; a literal
    counts only as a whole token (`30` is not in `300`), and one found only
    in a hyphenated word is uncertain (`status`). The closest sentence is
    named: fewest definite losses, then fewest uncertain ones."""

    from eval.meaning import literal_problem

    best = None
    for said in sentences(text) or [""]:
        missing = missed(said, require) + [p for x in literals if (p := literal_problem(x, said))]
        if not missing:
            return []
        if best is None or rank(missing) < rank(best):
            best = missing
    return best


def cued(phrase: str) -> bool:
    return bool(CUE.search(PSEUDO.sub(" ", phrase)))


def denied(text: str, i: int, j: int) -> bool:
    """Whether a negation cue governs `text[i:j]`: one in the same clause, at
    most `BEFORE` words before it or `AFTER` words after it."""

    before = PSEUDO.sub(" ", CLAUSE.split(text[:i])[-1]).split()[-BEFORE:]
    after = PSEUDO.sub(" ", CLAUSE.split(text[j:])[0]).split()[:AFTER]
    return bool(CUE.search(" ".join(before)) or CUE.search(" ".join(after)))


def missed(text: str, require: list[list[str]]) -> list[str]:
    """The `require` groups `text` does not meet. Phrases match as
    `meaning.at` does (a number whole).
    A phrase a cue governs does not meet its group there in two cases:
    when the label has no negation in it (`does not have alt text` is not `have alt text`),
    or when it is the positive wording in a group of negations
    (`does not stay on` is not `no longer switch off`).
    A label that names the negation holds it in its own phrases (`never autoplay`),
    and what it negates (`sound`) is not checked for one."""

    from eval.meaning import at

    positive = not any(cued(p) for g in require for p in g)
    guard = lambda g, p: positive or (not cued(p) and any(cued(q) for q in g))  # noqa: E731
    return [f"none of {g}" for g in require
            if not any(not (guard(g, p) and denied(text, i, i + len(p))) for p in g for i in at(p, text))]


def check(meaning: dict, text: str) -> list[str]:
    """A question's meaning labels against its English: `missed`, any
    `forbid` phrase, and each literal as a whole token. `asked` groups are
    the asked part and are met on the last line only, where a question that
    pastes something first asks it: the pasted line's `Shared carts` is not
    the asked line's `private cart`."""

    from eval.meaning import at, literal_problem

    asked = text.strip().rsplit("\n", 1)[-1]
    return missed(text, meaning["require"]) + [f"asked {m}" for m in missed(asked, meaning.get("asked", []))] + \
        [f"has {p!r}" for p in meaning["forbid"] if at(p, text)] + \
        [p for x in meaning.get("literals", []) if (p := literal_problem(x, text))]


def rank(missing: list[str]) -> tuple[int, int]:
    unsure = sum(m.startswith("uncertain") for m in missing)
    return len(missing) - unsure, unsure


def status(missing: list[str]) -> str:
    """A check's outcome from what it found missing: `held`, `lost` when
    anything is definitely lost, else `uncertain` — never a translation defect."""

    return "held" if not missing else "uncertain" if all(m.startswith("uncertain") for m in missing) else "lost"


def joined(chunks: list[dict], outcomes: list[dict]) -> str:
    """A section's English from its chunks in line order. Chunks hold whole
    blocks, apart from a block too long for one, which the index cuts at its
    lines (`partial`): the next part of that block, starting on the very next
    line, goes on as the same paragraph; anything else starts a new one."""

    out, last = "", None
    for c, o in zip(chunks, outcomes):
        goes_on = last is not None and last.get("completeness") == c.get("completeness") == "partial" \
            and c.get("line") is not None and c.get("line") == (last.get("end_line") or -2) + 1
        out += ("\n" if goes_on else "\n\n" if last is not None else "") + o["text"]
        last = c
    return out


def facts(pairs: dict, chunks: list[dict], label, english) -> list[dict]:
    """Every twin's fact units against the English its section normalizes to,
    whether or not retrieval ever reaches it. `chunks` are the KO index's,
    `label` names one as `dataset.corpus` does, and `english(chunks)`
    normalizes their texts as the run's `judge` does (the same cache)."""

    by_section = defaultdict(list)
    for c in chunks:
        by_section[label(c)].append(c)
    out = []
    for page, twin in sorted(pairs["twins"].items()):
        for f in twin["facts"]:
            got = sorted(by_section.get(f"{page}#{twin['headings'][f['section']]}", []),
                         key=lambda c: c.get("line") or 0)
            outcomes = english(got) if got else []
            failed = [o.get("reason") or o["status"] for o in outcomes if o["status"] not in evidence.USABLE]
            if not got or failed:
                outcome, missing = "not_normalized", failed or ["no chunk of this section"]
            else:
                missing = lost(joined(got, outcomes), f["require"], f["literals"])
                outcome = status(missing)
            out.append({"page": page, "id": f["id"], "section": f["section"], "sentence": f["sentence"],
                        "status": outcome, "missing": missing})
    return out


def baseline(pairs: dict, data: dict) -> list[str]:
    """Labels that fail on the English originals: each pair's meaning on its
    intent's English question, and each fact unit on its English section. A
    label that fails there would count a faithful translation as a loss."""

    intents, known = {i["id"]: i for i in data["intents"]}, dataset.pages(data)
    out = [f"{p['intent']}: {m}" for p in pairs["pairs"]
           for m in check(p["meaning"], intents[p["intent"]]["variants"]["en"])]
    for page, twin in sorted(pairs["twins"].items()):
        body = {path.split(" > ")[-1]: b for path, b in dataset.sections(known[page])}
        out += [f"{page} fact {f['id']}: {m}" for f in twin["facts"]
                for m in lost(body.get(f["section"], ""), f["require"], f["literals"])]
    return out


def question(meaning: dict, row: dict) -> dict:
    """The meaning labels against a Korean question's normalized English as
    the row recorded it. Not normalized is its own outcome, never a pass;
    a literal found only in a hyphenated word is uncertain."""

    normalized = (row.get("normalization") or {}).get("query")
    if normalized not in evidence.USABLE or not row.get("question_en"):
        return {"status": "not_normalized", "normalization": normalized}
    wrong = check(meaning, row["question_en"])
    return {"status": status(wrong), "missing": wrong}


# -- the paired report --------------------------------------------------------------------

def scored(row: dict | None, intent: dict, k: int) -> dict:
    """A row's recall and coverage under the frozen cohort's rules: a missing
    row is missing; a failed answer covers nothing; an answer whose grade is
    missing is unscorable; a retrieval that failed recalls nothing, whatever
    evidence its fallback kept (that stays in the row, for diagnosis)."""

    from eval import report

    if row is None:
        return {"missing": True}
    got = report.values(row, intent, k)
    failed = row["status"] in ("unavailable", "exhausted", "cancelled")
    out = {"recall": 0.0 if failed else got["recall"], "claims": got.get("claims"), "failed_retrieval": failed}
    if "answer_error" in row:
        out["coverage"] = 0.0
    elif "coverage" in got:
        out["coverage"] = got["coverage"]
    else:
        out["unscorable"] = True
    return out


def signature(row: dict, s: dict) -> tuple:
    """What an arm is compared with A on (v1, Repetition and attribution)."""

    a = row.get("answer") or {}
    return (s.get("recall"), s.get("coverage"), a.get("status"), a.get("verified"), tuple(row.get("transitions") or []))


class Pairs:
    """One pairs run read for its gates: the first repetition of every pair
    and arm, scored once, and the checks that need no host."""

    def __init__(self, rows: list[dict], data: dict, pairs: dict, fact_rows: list[dict] | None, k: int,
                 checked: dict | None = None):
        self.k, self.pairs, self.facts, self.checked = k, pairs, fact_rows, checked or {}
        self.intents = {i["id"]: i for i in data["intents"]}
        self.by_pair = {p["intent"]: p for p in pairs["pairs"]}
        self.ids = sorted(self.by_pair)
        self.first = {(r["intent"], r["arm"]): r for r in rows if r["rep"] == 0}
        self.score = {(i, a): scored(self.first.get((i, a)), self.intents[i], k) for i in self.ids for a in ARMS}
        self.questions = {f"{i}:{a}": question(self.by_pair[i]["meaning"], self.first[(i, a)])
                          for i in self.ids for a in "BD" if (i, a) in self.first}
        self.pages = defaultdict(set)
        for f in fact_rows or []:
            if f["status"] != "held":
                self.pages[f["page"]].add(f["status"])

    def drifted(self, row: dict | None) -> bool:
        """Whether a row read English, for a text the fact pass checked, other
        than what it checked. A failed read gave no English to the row: it is
        counted as failed, not as drift."""

        read = (((row or {}).get("translation") or {}).get("evidence") or {}).get("read") or []
        return any(e is not None and t in self.checked and self.checked[t] != e for t, e in read)

    def missing(self) -> list[str]:
        return sorted(f"{i}:{a}" for (i, a), s in self.score.items() if s.get("missing"))

    def settled(self, i: str, arm: str) -> str | None:
        """Attribution's first two checks, which need no repetition: A failed
        (step 1), or the arm's question or a twin it read lost or never got
        its meaning (step 2; a translation that failed is apart from a defect)."""

        a = self.score[(i, "A")]
        if a.get("missing") or a.get("unscorable") or a["failed_retrieval"] or "answer_error" in self.first[(i, "A")] \
                or a["recall"] == 0 or a["coverage"] == 0:
            return "controller_or_baseline"
        found = self.meaning(i, arm)
        return "translation_defect" if "lost" in found else "translation_failure" if "not_normalized" in found else None

    def meaning(self, i: str, arm: str) -> set[str]:
        """The outcomes of every meaning check the arm's run depends on: its
        question's (B, D) and those of the twins its intent's evidence names (C, D)."""

        found = set()
        if arm in "BD":
            found.add(self.questions.get(f"{i}:{arm}", {}).get("status"))
        if arm in "CD":
            for page in files(self.intents[i]):
                found |= self.pages.get(page, set())
        return found

    def divergence(self) -> dict:
        """Divergent pairs, with the arms that differ from A at repetition 0
        or fail a meaning check, and boundary pairs (A's recall or coverage partial)."""

        divergent, boundary = {}, []
        for i in self.ids:
            a = self.first.get((i, "A"))
            arms = [x for x in "BCD" if a is None or (i, x) not in self.first
                    or signature(self.first[(i, x)], self.score[(i, x)]) != signature(a, self.score[(i, "A")])
                    or self.settled(i, x) in ("translation_defect", "translation_failure")]
            if arms:
                divergent[i] = arms
            s = self.score[(i, "A")]
            if any(v is not None and 0 < v < 1 for v in (s.get("recall"), s.get("coverage"))):
                boundary.append(i)
        return {"divergent": divergent, "boundary": boundary}

    def repetition(self, rows: list[dict]) -> dict:
        """What two more runs would take: per candidate pair, the arms that
        need them (A's are shared), and their cost at the base run's mean
        host dollars per row. An estimate; repetition needs its own approval."""

        flagged = self.divergence()
        plan = {}
        for i in sorted(set(flagged["divergent"]) | set(flagged["boundary"])):
            arms = [x for x in flagged["divergent"].get(i, "BCD") if self.settled(i, x) is None]
            if arms:
                plan[i] = arms
        n = sum(2 * (1 + len(arms)) for arms in plan.values())
        spent = [(r.get("cost") or {}).get("host_usd") or 0.0 for r in rows]
        per_row = sum(spent) / len(spent) if spent else None
        return {**flagged, "plan": plan, "rows": n, "host_usd": round(n * per_row, 2) if per_row is not None else None}

    def attribution(self, repeats: list[dict]) -> dict:
        """Each divergent arm's cause, in the order v1 fixes: A failed; meaning
        lost or never normalized; not repeated or a repetition missing; a
        difference in one or two of three runs; a difference in all three with
        different evidence in all three; anything else unresolved. The causes
        after the second assume the meaning was kept, so an arm whose run read
        English other than was checked, or whose meaning check is uncertain,
        stays unresolved with that reason."""

        runs = {(r["intent"], r["arm"], r["rep"]): r for r in repeats}
        runs |= {(i, a, 0): r for (i, a), r in self.first.items()}
        out = {}
        for i, arms in sorted(self.divergence()["divergent"].items()):
            for x in arms:
                cause = self.settled(i, x)
                if cause is None and any(self.drifted(runs.get((i, a, n))) for a in ("A", x) for n in range(3)):
                    # A run that read other English than was checked is not the same measurement.
                    cause = "unresolved_normalization_changed"
                if cause is None and "uncertain" in self.meaning(i, x):
                    # Every later cause assumes the meaning was kept; until a person settles it, nothing is.
                    cause = "unresolved_meaning_uncertain"
                if cause is None:
                    got = [(runs.get((i, "A", n)), runs.get((i, x, n))) for n in range(3)]
                    if not any(runs.get((i, x, n)) for n in (1, 2)):
                        cause = "unresolved_not_repeated"
                    elif not all(a and b for a, b in got):
                        cause = "unresolved_missing_repetition"
                    else:
                        intent = self.intents[i]
                        differs = [signature(b, scored(b, intent, self.k)) != signature(a, scored(a, intent, self.k))
                                   for a, b in got]
                        apart = [b["evidence"][:self.k] != a["evidence"][:self.k] for a, b in got]
                        cause = ("variation" if 0 < sum(differs) < 3 else
                                 ("question_wording" if x == "B" else "evidence_or_index") if all(differs) and all(apart)
                                 else "unresolved")
                out[f"{i}:{x}"] = cause
        return out

    def gates(self, cfg: dict) -> tuple[list[dict], dict]:
        """The four frozen gates and what each read."""

        from eval import report

        B, seed, conf = cfg["resamples"], cfg["seed"], cfg["confidence"]
        missing = self.missing()
        unscorable = sorted(f"{i}:{a}" for (i, a), s in self.score.items() if s.get("unscorable"))

        def diff(arm: str, metric: str) -> dict | None:
            groups = [((self.score[(i, "A")][metric], 1.0), (self.score[(i, arm)][metric], 1.0)) for i in self.ids
                      if self.score[(i, "A")].get(metric) is not None and self.score[(i, arm)].get(metric) is not None]
            return report.boot(groups, lambda g: (report.ratio([x[1] for x in g]) or 0)
                               - (report.ratio([x[0] for x in g]) or 0), B, seed, conf) if groups else None

        differences = {f"{m}_{a}_minus_A": diff(a, m) for m in ("recall", "coverage") for a in "BCD"}
        targets = {g["id"]: g.get("target") for g in cfg["gates"]}

        def lower_end(metric: str) -> str:
            # The cohort is all of it or the gate is not settled: a missing row never shrinks it.
            if missing or (metric == "coverage" and unscorable):
                return "inconclusive"
            ds = [differences[f"{metric}_{a}_minus_A"] for a in "BCD"]
            if any(d is None or d["low"] is None for d in ds):
                return "inconclusive"
            return "pass" if all(d["low"] >= targets[f"paired_{metric}"] for d in ds) else "fail"

        checks = list(self.questions.values()) + list(self.facts or [])
        drifted = sorted(r["key"] for r in self.first.values() if self.drifted(r))
        literals = ("fail" if any(c["status"] == "lost" for c in checks) else
                    "inconclusive" if self.facts is None or missing or drifted
                    or any(c["status"] != "held" for c in checks)
                    else "pass")
        fabricated, unsupported, ungraded = [], [], []
        for (i, a), r in sorted(self.first.items()):
            answer = r.get("answer") or {}
            if answer.get("fabricated"):
                fabricated.append(r["key"])
            if answer.get("verified"):
                claims = self.score[(i, a)].get("claims")
                (ungraded if claims is None else unsupported if claims[0] else []).append(r["key"])
        integrity = "fail" if fabricated or unsupported else "inconclusive" if ungraded or missing else "pass"
        verdicts = {"literals": literals, "integrity": integrity, "paired_recall": lower_end("recall"),
                    "paired_coverage": lower_end("coverage")}
        lows = {m: [d["low"] for a in "BCD" if (d := differences[f"{m}_{a}_minus_A"]) and d["low"] is not None]
                for m in ("recall", "coverage")}
        # What each gate's verdict read: losses, violations, the lowest of the three lower ends.
        values = {"literals": sum(c["status"] == "lost" for c in checks),
                  "integrity": len(fabricated) + len(unsupported),
                  "paired_recall": min(lows["recall"], default=None),
                  "paired_coverage": min(lows["coverage"], default=None)}
        reviewed = all(labels["reviewed_by"] for labels in (cfg["dataset_labels"], self.pairs["labels"]))
        out = []
        for g in cfg["gates"]:
            verdict = verdicts[g["id"]]
            if verdict == "pass" and not reviewed and g["id"] != "integrity":
                verdict = "provisional"
            out.append({"id": g["id"], "label": g["label"], "target": g.get("target"), "value": values[g["id"]],
                        "verdict": verdict})
        return out, {"cohort": len(self.ids) * len(ARMS), "missing": missing, "unscorable": unscorable,
                     "failed_retrieval": sorted(f"{i}:{a}" for (i, a), s in self.score.items()
                                                if s.get("failed_retrieval")),
                     "failed_answers": sorted(r["key"] for r in self.first.values() if "answer_error" in r),
                     "differences": differences, "questions": self.questions, "facts": self.facts,
                     "drifted": drifted,
                     "integrity": {"fabricated": fabricated, "unsupported_verified": unsupported,
                                   "ungraded_verified": ungraded}}


def paired_report(rows: list[dict], data: dict, pairs: dict, fact_rows: list[dict] | None, k: int, cfg: dict,
                  repeats: list[dict] | None = None, checked: dict | None = None) -> dict:
    """The gates on the frozen cohort, the repetition candidates with their
    cost, and — when repetitions ran — each divergent arm's cause. `checked`
    is the fact pass's English by digest (`facts.json`'s `english`)."""

    p = Pairs(rows, data, pairs, fact_rows, k, checked)
    gates, detail = p.gates({**cfg, "dataset_labels": data["labels"]})
    return {**detail, "repetition": p.repetition(rows),
            "attribution": p.attribution(repeats) if repeats is not None else None, "gates": gates}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/eval/korean.py", description="Reliability PR 9's pairs")
    parser.add_argument("--pairs", type=Path, default=PAIRS)
    parser.add_argument("--check", action="store_true",
                        help="validate the pair file, its seven groups of twelve and its labels on the English originals")
    args = parser.parse_args(argv)
    pairs, data = load(args.pairs)
    wrong = baseline(pairs, data)
    print("\n".join(wrong) or f"{len(pairs['pairs'])} pairs, {len(pairs['twins'])} twins; calibration intents "
                              f"per group {calibration(pairs, data)}; reviewed by "
                              f"{pairs['labels']['reviewed_by'] or 'nobody yet'}")
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
