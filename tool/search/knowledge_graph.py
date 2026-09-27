"""knowledge_graph — sources, chunks, entities and decisions, and what
connects them, each connection with the original spans that establish it.
Stage 4 of `docs/plans/jev/`.

The graph lives in the evidence store's SQLite (`daemon.Store`), in the same
generations as the chunks it is made of: publishing a generation publishes
its graph, pruning one prunes it, and going back to a kept one reads that
one's graph. It is derived data, rebuilt from the chunks whenever they change
(`update`); authoritative Markdown is never rewritten.

Three origins.

- `deterministic`: document structure (`contains`, `next_chunk`), Markdown
  and wiki links (`links_to`), front-matter `reads` and `links`, and an
  explicit `supersedes`/`superseded_by` in a decision record. Always adopted,
  confidence 1.
- `extracted`: entities and relations a generative model proposed, kept only
  when code finds every quoted span in the passage and every entity name in
  its quote; a relation is adopted only when Jev judged the passage to
  support it (`ADOPT`), a candidate when uncertain or not judged, and dropped
  at or below `DROP`. The proposals and verdicts are cached in `extractions`
  per source and text under one `versions` — model, prompt, Jev model, policy
  — and the graph uses only the rows of the versions made active by the last
  extraction (`activate`), so a changed prompt or model reuses nothing.
  `retire` switches them all off; structure stays.
- `observed`: co-injection counts from the hub's `graph.json`. A hint for
  exploration, never support: always a candidate, with no span.

A candidate is not a default traversal edge (`Graph.edges` returns adopted
ones unless asked). Walking an edge backwards does not reverse it: a result
says `reverse` when it was reached against its direction.

Deletion. A source that leaves the store takes its nodes, the edges touching
them and every span it lent to another edge, in the transaction that removes
it (`drop`); an extracted edge left without a span goes, and one another
source still supports keeps only that source's provenance. A private source's
cached extractions go with it.
"""

from __future__ import annotations

import hashlib
import json
import math
import posixpath
import re
import sqlite3
from pathlib import Path

import yaml

from . import evidence

SCHEMA_VERSION = 1
# The deterministic extractor. Its edges carry it as their `extractor_version`.
STRUCTURE = "structure/1"
OBSERVED = "co-injection/1"
# Part of every extraction's `versions`: the questions and thresholds below.
POLICY = "graph-support/2"

NODE_KINDS = ("source", "chunk", "entity", "decision")
# Edge kind -> directed.
EDGE_KINDS = {"contains": True, "next_chunk": True, "links_to": True, "reads": True, "mentions": True,
              "depends_on": True, "supersedes": True, "contradicts": False, "co_injected": False}
ORIGINS = ("deterministic", "extracted", "observed")
STATUSES = ("adopted", "candidate")
ENTITY_TYPES = ("module", "setting", "feature", "paper_subject")
RELATIONS = ("depends_on",)
# ponytail: the retrieval controller's uncalibrated thresholds (`controller.NO/YES`); tune on the labeled subset.
ADOPT = 0.8
DROP = 0.2
MAX_NAME = 80

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (gen INTEGER, node_id TEXT, repo_id TEXT, kind TEXT, source_id TEXT, label TEXT,
    type TEXT, PRIMARY KEY (gen, node_id));
CREATE INDEX IF NOT EXISTS nodes_source ON nodes (gen, source_id);
CREATE TABLE IF NOT EXISTS edges (gen INTEGER, edge_id TEXT, repo_id TEXT, from_id TEXT, to_id TEXT, kind TEXT,
    directed INTEGER, origin TEXT, confidence REAL, extractor_version TEXT, status TEXT, PRIMARY KEY (gen, edge_id));
CREATE INDEX IF NOT EXISTS edges_out ON edges (gen, repo_id, from_id, kind);
CREATE INDEX IF NOT EXISTS edges_in ON edges (gen, repo_id, to_id, kind);
CREATE TABLE IF NOT EXISTS spans (gen INTEGER, edge_id TEXT, source_id TEXT, revision TEXT, chunk_id TEXT,
    locator TEXT, quote TEXT, confidence REAL);
CREATE INDEX IF NOT EXISTS spans_edge ON spans (gen, edge_id);
CREATE INDEX IF NOT EXISTS spans_source ON spans (gen, source_id);
CREATE TABLE IF NOT EXISTS extractions (source_id TEXT, text_sha TEXT, versions TEXT, result TEXT,
    PRIMARY KEY (source_id, text_sha, versions));
"""
TABLES = ("nodes", "edges", "spans")

# Documents are pointed at two ways: a Markdown link, and a backticked path,
# `docs/development/TDD.md` or `README.md:24`. Moved here from `repo_graph`,
# which reads them from here, so the map and retrieval resolve links alike.
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s#]+\.md)[^)]*\)")
BARE_PATH = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.md)(?::\d+(?:-\d+)?)?`")
WIKI_LINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
FRONT_KEY = re.compile(r"^([A-Za-z_][\w-]*):")


def targets(text: str) -> set[str]:
    return set(MD_LINK.findall(text)) | set(BARE_PATH.findall(text))


def resolve(raw: str, source: str, known: set[str]) -> str | None:
    """A pointed-at path resolved against the repository, `None` when not in
    `known`. Relative to the pointing document first, then from the root. The
    filesystem is never touched: `known` already says what exists."""

    here = posixpath.dirname(source)
    for candidate in (posixpath.join(here, raw), raw):
        # Only a root slash goes: `lstrip("./")` also ate the dot of `.wiki/`.
        name = posixpath.normpath(candidate).lstrip("/")
        if name in known:
            return name
    return None


def named(raw: str, source: str, known: set[str]) -> str | None:
    """A wiki-style name — `[[agent-delegation]]`, `craft/x`, a decision id —
    as a path in `known`: resolved as a path first, then the one document of
    that name, if there is exactly one."""

    raw = str(raw).strip().removesuffix(".md")
    if not raw or raw.startswith(("http://", "https://")):
        return None
    hit = resolve(raw + ".md", source, known)
    if hit:
        return hit
    found = [k for k in known if k.endswith("/" + raw + ".md")]
    return found[0] if len(found) == 1 else None


def digest(*parts: str) -> str:
    return evidence.digest(*parts)


def entity_id(repo: str, kind: str, name: str) -> str:
    """An entity is its repository, its type and its normalized name: the same
    name in two repositories, or as a module and a setting, is two entities."""

    return digest("entity", repo, kind, normal(name))


def decision_id(repo: str, display: str) -> str:
    return digest("decision", repo, display.removesuffix(".md"))


def edge_id(kind: str, a: str, b: str) -> str:
    if not EDGE_KINDS[kind]:
        a, b = sorted((a, b))
    return digest("edge", kind, a, b)


def normal(name: str) -> str:
    return " ".join(str(name).split()).casefold()


# ---- the contract -------------------------------------------------------------

def problems(edge: dict) -> list[str]:
    """Everything that makes `edge` not a KnowledgeEdge. Empty means valid."""

    found = []
    if edge.get("schema_version") != SCHEMA_VERSION:
        found.append("schema_version")
    for name in ("edge_id", "repo_id", "from_id", "to_id"):
        if not evidence.ID.match(str(edge.get(name) or "")):
            found.append(f"{name} is not a sha256")
    kind = edge.get("kind")
    if kind not in EDGE_KINDS:
        found.append(f"kind {kind!r}")
    elif edge.get("directed") is not EDGE_KINDS[kind]:
        found.append("directed does not match the kind")
    origin, status = edge.get("origin"), edge.get("status")
    if origin not in ORIGINS:
        found.append(f"origin {origin!r}")
    if status not in STATUSES:
        found.append(f"status {status!r}")
    confidence = edge.get("confidence")
    if confidence is not None and not (type(confidence) in (int, float) and math.isfinite(confidence)
                                       and 0 <= confidence <= 1):
        found.append("confidence is not a probability")
    if origin == "deterministic" and (confidence != 1 or status != "adopted"):
        found.append("a deterministic edge is adopted with confidence 1")
    if (origin == "observed") != (kind == "co_injected") or (origin == "observed" and status != "candidate"):
        found.append("only co-injection is observed, and only as a candidate")
    spans = edge.get("source_spans")
    if not isinstance(spans, list) or (status == "adopted" and not spans):
        found.append("an adopted edge without source spans")
    for span in spans if isinstance(spans, list) else []:
        if not (isinstance(span, dict) and evidence.ID.match(str(span.get("source_id") or ""))
                and evidence.ID.match(str(span.get("revision") or ""))):
            found.append("a span without its source and revision")
        elif (problem := evidence.locator_problem(span.get("locator"))) is not None:
            found.append(problem)
    if isinstance(spans, list) and edge.get("source_revisions") != sorted({s.get("revision") for s in spans
                                                                             if isinstance(s, dict)}):
        found.append("source_revisions do not match the spans")
    if not str(edge.get("extractor_version") or ""):
        found.append("extractor_version")
    return found


# ---- derivation ---------------------------------------------------------------

def place(chunk: dict) -> tuple:
    """A chunk's position in its source, for `next_chunk`."""

    locator = chunk["locator"]
    return (locator.get("start_line", locator.get("start", locator.get("page", 0))), locator.get("block", 0))


def own(chunk: dict) -> dict:
    """A span that is the whole chunk."""

    return {"source_id": chunk["source_id"], "revision": chunk["revision"], "chunk_id": chunk["chunk_id"],
            "locator": chunk["locator"], "quote": None, "confidence": 1.0}


def lines_of(chunk: dict, first: int, last: int) -> dict:
    """A span of lines `first` to `last` (counted from 0) of a file chunk."""

    start = chunk["locator"]["start_line"]
    return {"source_id": chunk["source_id"], "revision": chunk["revision"], "chunk_id": chunk["chunk_id"],
            "locator": {"path": chunk["locator"]["path"], "start_line": start + first, "end_line": start + last},
            "quote": None, "confidence": 1.0}


def quoted(chunk: dict, quote: str, confidence: float | None) -> dict | None:
    """The narrowest citable span of `quote` in `chunk`: its lines in a file,
    its characters in a URL snapshot, its block in a PDF. `None` when the
    chunk does not hold it verbatim."""

    at = chunk["text"].find(quote) if quote else -1
    if at < 0:
        return None
    locator = dict(chunk["locator"])
    if "start_line" in locator:
        text = chunk["text"]
        locator["start_line"] += text.count("\n", 0, at)
        locator["end_line"] = chunk["locator"]["start_line"] + text.count("\n", 0, at + len(quote) - 1)
    elif "url" in locator:
        locator["start"], locator["end"] = locator["start"] + at, locator["start"] + at + len(quote)
    return {"source_id": chunk["source_id"], "revision": chunk["revision"], "chunk_id": chunk["chunk_id"],
            "locator": locator, "quote": quote, "confidence": confidence}


def front(path: str, revision: str) -> list[tuple[str, object, int, int]]:
    """`(key, value, first line, last line)` of each top-level front-matter
    key of the file — `[]` when it has none or is no longer `revision`."""

    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    if hashlib.sha256(data).hexdigest() != revision:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return []
    try:
        meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(meta, dict):
        return []
    starts = [(i, m.group(1)) for i in range(1, end) if (m := FRONT_KEY.match(lines[i]))]
    out = []
    for n, (i, key) in enumerate(starts):
        last = (starts[n + 1][0] if n + 1 < len(starts) else end) - 1
        out.append((key, meta.get(key), i + 1, last + 1))
    return out


def listed(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)] if value not in (None, "") and not isinstance(value, dict) else []


class Build:
    """The nodes and edges being derived, with each edge's spans gathered."""

    def __init__(self):
        self.nodes: dict[str, tuple] = {}
        self.edges: dict[str, list] = {}
        self.spans: dict[str, dict[tuple, dict]] = {}

    def node(self, node: str, repo: str, kind: str, source: str | None, label: str, type_: str | None) -> None:
        self.nodes.setdefault(node, (repo, kind, source, label, type_))

    def edge(self, kind: str, repo: str, a: str, b: str, origin: str, version: str, spans: list[dict]) -> str:
        eid = edge_id(kind, a, b)
        if EDGE_KINDS[kind] is False:
            a, b = sorted((a, b))
        self.edges.setdefault(eid, [repo, a, b, kind, int(EDGE_KINDS[kind]), origin, version])
        held = self.spans.setdefault(eid, {})
        for span in spans:
            held.setdefault((span["source_id"], json.dumps(span["locator"], sort_keys=True), span["quote"]), span)
        return eid

    def rows(self) -> tuple[dict[str, tuple], dict[str, tuple], dict[str, list[tuple]]]:
        """`(nodes, edges, spans)` as table rows, with each edge's confidence and
        status from its spans. An extracted edge whose spans are all at or
        below `DROP` is not an edge."""

        edges, spans = {}, {}
        for eid, (repo, a, b, kind, directed, origin, version) in self.edges.items():
            if a not in self.nodes or b not in self.nodes:
                continue
            held = list(self.spans[eid].values())
            scores = [s["confidence"] for s in held if s["confidence"] is not None]
            if origin == "deterministic":
                confidence, status = 1.0, "adopted"
            elif origin == "observed":
                confidence, status = None, "candidate"
            else:
                held = [s for s in held if s["confidence"] is None or s["confidence"] > DROP]
                if not held:
                    continue
                scores = [s["confidence"] for s in held if s["confidence"] is not None]
                confidence = max(scores) if scores else None
                status = "adopted" if confidence is not None and confidence >= ADOPT else "candidate"
            edges[eid] = (repo, a, b, kind, directed, origin, confidence, version, status)
            spans[eid] = sorted((s["source_id"], s["revision"], s["chunk_id"],
                                 json.dumps(s["locator"], sort_keys=True), s["quote"], s["confidence"])
                                for s in held)
        return self.nodes, edges, spans


def derive(chunks: list[dict], extracted: list[tuple[str, str, dict]] = (),
           co: list[tuple[str, str]] = ()) -> tuple[dict, dict, dict]:
    """The graph of `chunks` (search hits, local and external): nodes, edges
    and spans as table rows.

    `extracted` is `(source_id, text sha, result)` from the active
    extractions; `co` pairs of hub rule paths that were injected together.
    Every endpoint is a chunk, its source, an entity one of them mentions, or
    a decision record among them: nothing points at what is not evidence.
    """

    build = Build()
    by_source: dict[str, list[dict]] = {}
    for chunk in chunks:
        by_source.setdefault(chunk["source_id"], []).append(chunk)
    # Each repository's documents by path, for links; decision records apart.
    known: dict[str, dict[str, str]] = {}
    decisions: dict[str, dict[str, str]] = {}
    for source, mine in by_source.items():
        first = mine[0]
        repo = first["repo_id"]
        record = first.get("record")
        display = first["locator"].get("path") if not record else None
        label = (record or {}).get("title") or (record or {}).get("origin") or display or source
        build.node(source, repo, "source", source, label, first["kind"])
        if display:
            known.setdefault(repo, {})[display] = source
        if display and first["kind"] == "decision":
            decisions.setdefault(repo, {})[display] = source
            build.node(decision_id(repo, display), repo, "decision", source, first["heading_path"][0], None)
            build.edge("contains", repo, source, decision_id(repo, display), "deterministic", STRUCTURE,
                       [own(min(mine, key=place))])
        mine.sort(key=place)
        for i, chunk in enumerate(mine):
            build.node(chunk["chunk_id"], repo, "chunk", source, chunk["heading"], chunk["kind"])
            build.edge("contains", repo, source, chunk["chunk_id"], "deterministic", STRUCTURE, [own(chunk)])
            if i:
                build.edge("next_chunk", repo, mine[i - 1]["chunk_id"], chunk["chunk_id"], "deterministic",
                           STRUCTURE, [own(mine[i - 1]), own(chunk)])

    for source, mine in by_source.items():
        first = mine[0]
        repo, display = first["repo_id"], first["locator"].get("path")
        if first.get("record") or not display:
            continue
        paths = set(known[repo])
        for chunk in mine:
            for n, line in enumerate(chunk["text"].split("\n")):
                hits = {resolve(raw, display, paths) for raw in targets(line)}
                hits |= {named(raw, display, paths) for raw in WIKI_LINK.findall(line)}
                for hit in hits - {None, display}:
                    build.edge("links_to", repo, chunk["chunk_id"], known[repo][hit], "deterministic", STRUCTURE,
                               [lines_of(chunk, n, n)])
        for key, value, start, end in front(first["path"], first["revision"]):
            span = {"source_id": source, "revision": first["revision"], "chunk_id": None,
                    "locator": {"path": display, "start_line": start, "end_line": end}, "quote": None,
                    "confidence": 1.0}
            if key in ("links", "reads"):
                for raw in listed(value):
                    hit = (resolve(raw, display, paths) if key == "reads" else None) or named(raw, display, paths)
                    if hit and hit != display:
                        build.edge("reads" if key == "reads" else "links_to", repo, source, known[repo][hit],
                                   "deterministic", STRUCTURE, [span])
            elif key in ("supersedes", "superseded_by") and first["kind"] == "decision":
                mine_ = decisions.get(repo, {})
                for raw in listed(value):
                    hit = named(raw, display, set(mine_))
                    if hit and hit != display:
                        new, old = (display, hit) if key == "supersedes" else (hit, display)
                        build.edge("supersedes", repo, decision_id(repo, new), decision_id(repo, old),
                                   "deterministic", STRUCTURE, [span])

    for a, b in co:
        for repo, paths in known.items():
            if a in paths and b in paths:
                build.edge("co_injected", repo, paths[a], paths[b], "observed", OBSERVED, [])

    # A result is per text: every section of a source holding that text gets it.
    texts: dict[tuple[str, str], list[dict]] = {}
    for c in chunks:
        texts.setdefault((c["source_id"], digest(c["text"])), []).append(c)
    for source, sha, result in extracted:
        if "entities" in result:
            for chunk in texts.get((source, sha), []):
                materialize(build, chunk, result)
    for source, sha, result in extracted:
        pair = result.get("pair")
        if pair and source == pair[0][0]:
            for a in texts.get(tuple(pair[0]), []):
                for b in texts.get(tuple(pair[1]), []):
                    if a["repo_id"] == b["repo_id"]:
                        spans = [{**own(a), "confidence": result["support"]},
                                 {**own(b), "confidence": result["support"]}]
                        build.edge("contradicts", a["repo_id"], a["chunk_id"], b["chunk_id"], "extracted",
                                   result["versions"], spans)
    return build.rows()


def materialize(build: Build, chunk: dict, result: dict) -> None:
    """One chunk's cached extraction as `mentions` and `depends_on` edges,
    checked again against the chunk's text as it is now."""

    entities, _relations, _rejected = validate(chunk["text"], result)
    repo, version = chunk["repo_id"], result["versions"]
    ids = {}
    for entity in entities:
        span = quoted(chunk, entity["quote"], 1.0)
        node = entity_id(repo, entity["type"], entity["name"])
        build.node(node, repo, "entity", None, entity["name"], entity["type"])
        build.edge("mentions", repo, chunk["chunk_id"], node, "extracted", version, [span])
        ids.setdefault(normal(entity["name"]), node)
    for relation in _relations:
        span = quoted(chunk, relation["quote"], relation.get("support"))
        build.edge(relation["kind"], repo, ids[normal(relation["from"])], ids[normal(relation["to"])], "extracted",
                   version, [span])


def validate(text: str, proposal: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """`(entities, relations, rejected)` of a model's proposal for one passage.

    Code, not the model, decides what stands: an entity needs a known type, a
    quote found verbatim in the passage and its name inside that quote; a
    relation needs a known kind, two different entities that stand, and a
    quote of its own. A relation keeps the `support` Jev gave it, if any.
    """

    entities, relations, rejected, names = [], [], [], set()
    for entity in proposal.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        name, kind, quote = (str(entity.get(k) or "") for k in ("name", "type", "quote"))
        why = ("type" if kind not in ENTITY_TYPES else "name" if not name.strip() or len(name) > MAX_NAME
               else "quote not in passage" if not quote or quote not in text
               else "name not in quote" if normal(name) not in normal(quote) else None)
        if why:
            rejected.append({"entity": name, "reason": why})
        elif normal(name) not in names:
            names.add(normal(name))
            entities.append({"name": " ".join(name.split()), "type": kind, "quote": quote})
    for relation in proposal.get("relations") or []:
        if not isinstance(relation, dict):
            continue
        kind, a, b, quote = (str(relation.get(k) or "") for k in ("kind", "from", "to", "quote"))
        why = ("kind" if kind not in RELATIONS else
               "endpoint is not an entity of this passage" if normal(a) not in names or normal(b) not in names
               else "same entity" if normal(a) == normal(b)
               else "quote not in passage" if not quote or quote not in text
               # The span cited for the claim, not merely somewhere in its passage.
               else "quote does not name both ends" if normal(a) not in normal(quote) or normal(b) not in normal(quote)
               else None)
        if why:
            rejected.append({"relation": f"{a} {kind} {b}", "reason": why})
        else:
            support = relation.get("support")
            relations.append({"kind": kind, "from": a, "to": b, "quote": quote,
                              "support": support if type(support) in (int, float) else None})
    return entities, relations, rejected


# ---- storage --------------------------------------------------------------------

def meta(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
    return row[0] if row else None


def write(db: sqlite3.Connection, gen: int, nodes: dict, edges: dict, spans: dict) -> int:
    """Make generation `gen`'s graph exactly these rows, touching only what
    differs. Returns how many nodes and edges changed."""

    have_nodes = {r[0]: tuple(r[1:]) for r in db.execute(
        "SELECT node_id, repo_id, kind, source_id, label, type FROM nodes WHERE gen = ?", (gen,))}
    have_edges = {r[0]: tuple(r[1:]) for r in db.execute(
        "SELECT edge_id, repo_id, from_id, to_id, kind, directed, origin, confidence, extractor_version, status"
        " FROM edges WHERE gen = ?", (gen,))}
    have_spans: dict[str, list] = {}
    for row in db.execute("SELECT edge_id, source_id, revision, chunk_id, locator, quote, confidence FROM spans"
                          " WHERE gen = ?", (gen,)):
        have_spans.setdefault(row[0], []).append(tuple(row[1:]))
    stale_nodes = [n for n in have_nodes if have_nodes[n] != nodes.get(n)]
    stale_edges = [e for e in have_edges if have_edges[e] != edges.get(e) or sorted(have_spans.get(e, [])) != spans[e]]
    db.executemany("DELETE FROM nodes WHERE gen = ? AND node_id = ?", [(gen, n) for n in stale_nodes])
    db.executemany("DELETE FROM edges WHERE gen = ? AND edge_id = ?", [(gen, e) for e in stale_edges])
    db.executemany("DELETE FROM spans WHERE gen = ? AND edge_id = ?", [(gen, e) for e in stale_edges])
    new_nodes = [n for n in nodes if have_nodes.get(n) != nodes[n]]
    new_edges = [e for e in edges if e in stale_edges or e not in have_edges]
    db.executemany("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)", [(gen, n, *nodes[n]) for n in new_nodes])
    db.executemany("INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   [(gen, e, *edges[e]) for e in new_edges])
    db.executemany("INSERT INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   [(gen, e, *span) for e in new_edges for span in spans[e]])
    return len(stale_nodes) + len(new_nodes) + len(stale_edges) + len(new_edges)


def drop(db: sqlite3.Connection, doomed: list[tuple[int, str]], private: bool, gone: bool) -> None:
    """Remove these `(generation, source)`s from the graph, in the caller's
    transaction: their nodes, the edges touching them, and the spans they lent
    to other edges. An extracted edge left with no span goes, one still
    supported by another source keeps that provenance and its confidence, and
    an entity nothing mentions any more goes. A private source's cached
    extractions go too, as do anyone's once it is `gone`."""

    for gen, source in doomed:
        mine = [n for (n,) in db.execute("SELECT node_id FROM nodes WHERE gen = ? AND source_id = ?", (gen, source))]
        db.execute("DELETE FROM spans WHERE gen = ? AND source_id = ?", (gen, source))
        for start in range(0, len(mine), 400):
            part = mine[start:start + 400]
            marks = ",".join("?" * len(part))
            db.execute(f"DELETE FROM edges WHERE gen = ? AND (from_id IN ({marks}) OR to_id IN ({marks}))",
                       [gen, *part, *part])
        db.execute("DELETE FROM nodes WHERE gen = ? AND source_id = ?", (gen, source))
    for gen in {g for g, _s in doomed}:
        db.execute("DELETE FROM spans WHERE gen = ? AND edge_id NOT IN (SELECT edge_id FROM edges WHERE gen = ?)",
                   (gen, gen))
        db.execute("DELETE FROM edges WHERE gen = ? AND origin = 'extracted' AND edge_id NOT IN"
                   " (SELECT edge_id FROM spans WHERE gen = ?)", (gen, gen))
        db.execute("UPDATE edges SET confidence = (SELECT MAX(s.confidence) FROM spans s WHERE s.gen = edges.gen"
                   " AND s.edge_id = edges.edge_id) WHERE gen = ? AND origin = 'extracted'", (gen,))
        db.execute("UPDATE edges SET status = CASE WHEN confidence >= ? THEN 'adopted' ELSE 'candidate' END"
                   " WHERE gen = ? AND origin = 'extracted'", (ADOPT, gen))
        db.execute("DELETE FROM nodes WHERE gen = ? AND kind = 'entity' AND node_id NOT IN"
                   " (SELECT from_id FROM edges WHERE gen = ?) AND node_id NOT IN (SELECT to_id FROM edges WHERE gen = ?)",
                   (gen, gen, gen))
    if private or gone:
        db.executemany("DELETE FROM extractions WHERE source_id = ?", [(s,) for s in {s for _g, s in doomed}])


def update(store, chunks: list[dict], seen: str, co: list[tuple[str, str]] = (), stamp: str = "") -> bool:
    """Bring the graph of the generation `store` reads in line with `chunks`,
    the chunks loaded at store version `seen`. `stamp` names what else went
    in (the records' version, `graph.json`'s). Nothing happens when the graph
    was already built from the same; nothing is written when the store or
    the extractions moved on while it derived — the next refresh builds from
    what is there now. One
    transaction: a failure leaves the graph as it was. `True` when written."""

    def extraction(db) -> tuple:
        return meta(db, "graph_active"), meta(db, "graph_serial")

    with store.lock:
        gen = store.reading()
        active, serial = read = extraction(store.db)
        key = json.dumps([gen, seen, stamp, active, serial, STRUCTURE])
        if meta(store.db, "graph") == key:
            return False
        rows = store.db.execute("SELECT source_id, text_sha, result FROM extractions WHERE versions = ?",
                                (active,)).fetchall() if active else []
    extracted = [(source, sha, {**json.loads(result), "versions": active}) for source, sha, result in rows]
    nodes, edges, spans = derive(chunks, extracted, co)
    with store.transaction() as db:
        # Neither the chunks nor the extractions it was derived from may have moved on.
        if f"{store.reading()}:{store.meta('version') or 0}" != seen or extraction(db) != read:
            return False
        write(db, gen, nodes, edges, spans)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('graph', ?)", (key,))
    return True


def activate(store, versions: str | None) -> None:
    """Use the extractions of `versions` from now on; `None` retires them all."""

    with store.transaction() as db:
        if versions is None:
            db.execute("DELETE FROM meta WHERE k = 'graph_active'")
        else:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('graph_active', ?)", (versions,))


def keep(store, rows: list[tuple[str, str, str, dict]], external=frozenset) -> int:
    """Cache extraction results, `(source_id, text sha, versions, result)`,
    only for text the store still holds — or that `external()` names, the
    records' chunks as `(source, text sha)`, which live beside it. It is
    asked inside this transaction, the one `forget` also takes, so a memory
    deleted or a record forgotten while the model was answering is not
    brought back. A pair's result needs both of its passages. Returns how
    many were kept."""

    with store.transaction() as db:
        present = {(s, digest(t)) for s, t in db.execute("SELECT source_id, text FROM chunks")} | set(external())
        kept = [(s, sha, v, json.dumps(r, ensure_ascii=False)) for s, sha, v, r in rows
                if (all(tuple(end) in present for end in r["pair"]) if r.get("pair") else (s, sha) in present)]
        db.executemany("INSERT OR REPLACE INTO extractions VALUES (?, ?, ?, ?)", kept)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('graph_serial', ?)",
                   (str(int(meta(db, "graph_serial") or 0) + 1),))
    return len(kept)


def forget(db: sqlite3.Connection, source: str) -> None:
    """Remove an external source that is being forgotten from the graph of
    every generation, and its cached extractions — all of them quote its
    text — in the caller's transaction. The serial moves, so a rebuild that
    read the graph before this does not write it back."""

    gens = {g for (g,) in db.execute("SELECT gen FROM nodes WHERE source_id = ? UNION"
                                     " SELECT gen FROM spans WHERE source_id = ?", (source, source))}
    drop(db, [(g, source) for g in sorted(gens)], False, True)
    db.execute("DELETE FROM extractions WHERE source_id = ?", (source,))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('graph_serial', ?)",
               (str(int(meta(db, "graph_serial") or 0) + 1),))


def cached(store, versions: str) -> dict[tuple[str, str], dict]:
    with store.lock:
        rows = store.db.execute("SELECT source_id, text_sha, result FROM extractions WHERE versions = ?",
                                (versions,)).fetchall()
    return {(s, sha): json.loads(result) for s, sha, result in rows}


# ---- reading ----------------------------------------------------------------------

EDGE_COLUMNS = "edge_id, repo_id, from_id, to_id, kind, directed, origin, confidence, extractor_version, status"


class Graph:
    """Read access to the graph of the generation a `Store` reads — for
    retrieval (stage 5) and for checking it. Chunk-level: the map's
    document-level view is `projection`."""

    def __init__(self, store):
        self.store = store

    def rows(self, sql: str, args: list) -> list[tuple]:
        with self.store.lock:
            return self.store.db.execute(sql, [self.store.reading(), *args]).fetchall()

    def nodes(self, ids: list[str] | None = None) -> dict[str, dict]:
        """Every node, or those of `ids`, by id."""

        out = {}
        for part in parts(ids):
            where = f" AND node_id IN ({','.join('?' * len(part))})" if part is not None else ""
            for node, repo, kind, source, label, type_ in self.rows(
                    "SELECT node_id, repo_id, kind, source_id, label, type FROM nodes WHERE gen = ?" + where,
                    list(part or [])):
                out[node] = {"node_id": node, "repo_id": repo, "kind": kind, "source_id": source, "label": label,
                             "type": type_}
        return out

    def edges(self, ids: list[str] | None = None, direction: str = "both", kinds: list[str] | None = None,
              statuses: tuple[str, ...] = ("adopted",), repo: str | None = None) -> list[dict]:
        """KnowledgeEdges touching `ids` (all of them without), adopted only
        unless `statuses` says otherwise. Each says which queried node it was
        reached from (`via`) and whether that was against its direction
        (`reverse`) — which reverses the walk, never the relationship."""

        if direction not in ("out", "in", "both"):
            raise ValueError("direction is out, in or both")
        found: dict[tuple, dict] = {}
        ends = {"out": ("from_id",), "in": ("to_id",), "both": ("from_id", "to_id")}[direction]
        for part in parts(ids):
            for end in ends:
                sql = f"SELECT {EDGE_COLUMNS} FROM edges WHERE gen = ?"
                args: list = []
                if part is not None:
                    sql += f" AND {end} IN ({','.join('?' * len(part))})"
                    args += part
                if kinds:
                    sql += f" AND kind IN ({','.join('?' * len(kinds))})"
                    args += list(kinds)
                sql += f" AND status IN ({','.join('?' * len(statuses))})"
                args += list(statuses)
                if repo:
                    sql += " AND repo_id = ?"
                    args.append(repo)
                for row in self.rows(sql, args):
                    via = None if part is None else row[2] if end == "from_id" else row[3]
                    # An undirected edge has no against; a self-query from both ends is one result.
                    reverse = end == "to_id" and bool(row[5])
                    found.setdefault((row[0], via), {"row": row, "via": via, "reverse": reverse})
        spans = self.spans([key[0] for key in found])
        return [{**contract(item["row"], spans.get(item["row"][0], [])), "via": item["via"],
                 "reverse": item["reverse"]} for item in found.values()]

    def spans(self, ids: list[str]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for part in parts(sorted(set(ids))):
            if not part:
                continue
            for edge, source, revision, chunk, locator, quote, confidence in self.rows(
                    "SELECT edge_id, source_id, revision, chunk_id, locator, quote, confidence FROM spans"
                    f" WHERE gen = ? AND edge_id IN ({','.join('?' * len(part))})", part):
                out.setdefault(edge, []).append({"source_id": source, "revision": revision, "chunk_id": chunk,
                                                 "locator": json.loads(locator), "quote": quote,
                                                 "confidence": confidence})
        return out

    def pairs(self, repo: str, fanout: int, kinds: tuple[str, ...] = ("decision", "memory")) -> list[tuple]:
        """`(chunk a, chunk b, entity name)`: chunks that mention the same
        entity, at least one of them of `kinds` — the bounded comparisons for
        contradictions. An entity mentioned by more than `fanout` chunks is too
        common to say two passages share a topic, and adds none: never all
        pairs of a corpus."""

        mentions: dict[str, list[str]] = {}
        names = {}
        for edge in self.edges(kinds=["mentions"], repo=repo):
            mentions.setdefault(edge["to_id"], []).append(edge["from_id"])
        nodes = self.nodes(sorted({c for chunks in mentions.values() for c in chunks} | set(mentions)))
        for entity, node in nodes.items():
            names[entity] = node["label"]
        out = set()
        for entity, chunks in sorted(mentions.items()):
            chunks = sorted(set(chunks))
            if len(chunks) > fanout:
                continue
            for i, a in enumerate(chunks):
                for b in chunks[i + 1:]:
                    if nodes[a]["source_id"] != nodes[b]["source_id"] and (
                            nodes[a]["type"] in kinds or nodes[b]["type"] in kinds):
                        out.add((a, b, names[entity]))
        return sorted(out)


def parts(ids: list[str] | None, size: int = 400):
    if ids is None:
        yield None
        return
    ids = list(ids)
    for start in range(0, len(ids), size):
        yield ids[start:start + size]


def contract(row: tuple, spans: list[dict]) -> dict:
    """A table row and its spans as a KnowledgeEdge."""

    edge, repo, a, b, kind, directed, origin, confidence, version, status = row
    return {"schema_version": SCHEMA_VERSION, "edge_id": edge, "repo_id": repo, "from_id": a, "to_id": b,
            "kind": kind, "directed": bool(directed), "source_spans": spans,
            "source_revisions": sorted({s["revision"] for s in spans}), "origin": origin, "confidence": confidence,
            "extractor_version": version, "status": status}


def projection(path: Path, repo: str) -> list[dict]:
    """The document-level view of repository `repo`'s adopted graph, for the
    map: `{a, b, kind}` between two of its files, a chunk standing for the
    file it is in. Read only — the database is opened read-only, and a store
    that is missing or holds no graph gives `[]`."""

    try:
        db = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return []
    try:
        current = meta(db, "current")
        rows = db.execute(
            "SELECT e.kind, sa.display, sb.display FROM edges e"
            " JOIN nodes na ON na.gen = e.gen AND na.node_id = e.from_id"
            " JOIN nodes nb ON nb.gen = e.gen AND nb.node_id = e.to_id"
            " JOIN sources sa ON sa.gen = e.gen AND sa.source_id = na.source_id"
            " JOIN sources sb ON sb.gen = e.gen AND sb.source_id = nb.source_id"
            " WHERE e.gen = ? AND e.repo_id = ? AND e.status = 'adopted' AND sa.source_id != sb.source_id"
            " AND e.kind NOT IN ('contains', 'next_chunk', 'mentions')", (int(current or 0), repo)).fetchall()
    except (sqlite3.Error, ValueError):
        return []
    finally:
        db.close()
    return [{"a": a, "b": b, "kind": kind} for kind, a, b in sorted(set(rows), key=lambda r: (r[1], r[2], r[0]))]


# ---- checking -------------------------------------------------------------------

def verify(graph: Graph, chunks: list[dict]) -> dict:
    """The completion gate over the whole graph: every edge a valid
    KnowledgeEdge, every endpoint a node of its own repository, every span
    resolving to its original — and an extracted span to text holding its
    quote. Counts, and each failure by edge id."""

    from .evidence import resolve as from_file
    from .sources import resolve as from_snapshot

    where = {c["source_id"]: c["path"] for c in chunks}
    nodes = graph.nodes()
    edges = graph.edges(statuses=STATUSES)
    report = {"nodes": len(nodes), "edges": len(edges), "adopted": 0, "by_kind": {}, "invalid": [],
              "dangling": [], "out_of_scope": [], "unresolved_spans": []}
    for edge in edges:
        report["adopted"] += edge["status"] == "adopted"
        report["by_kind"][edge["kind"]] = report["by_kind"].get(edge["kind"], 0) + 1
        if problems(edge):
            report["invalid"].append({"edge_id": edge["edge_id"], "problems": problems(edge)})
        ends = [nodes.get(edge["from_id"]), nodes.get(edge["to_id"])]
        if None in ends:
            report["dangling"].append(edge["edge_id"])
        elif any(n["repo_id"] != edge["repo_id"] for n in ends):
            report["out_of_scope"].append(edge["edge_id"])
        for span in edge["source_spans"]:
            fake = {"revision": span["revision"], "locator": span["locator"]}
            path = where.get(span["source_id"])
            # Lines of a file or of a file-form snapshot; characters or a block of the rest.
            read = from_file if "start_line" in span["locator"] else from_snapshot
            text = None if path is None else read(fake, Path(path))
            if not text or (span["quote"] and span["quote"] not in text):
                report["unresolved_spans"].append({"edge_id": edge["edge_id"], "locator": span["locator"]})
    return report
