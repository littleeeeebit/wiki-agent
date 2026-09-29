"""retrieval — chunk-level hybrid retrieval with a bounded graph lane.
Stage 5 of `docs/plans/jev/`.

`run` answers one RetrievalRequest from one index snapshot
(`daemon.Index.snapshot`), so a result never mixes two states of the store:

1. The request is checked: its repository is the snapshot's, its sources are
   registered names, and a generation it names is the one loaded (`Stale`
   otherwise — one answer reads one generation).
2. The original query and its English are ranked by BM25 and, once the
   vectors are complete, by e5 cosine, both queries encoded in one batch and
   the English skipped when it is the same text. Only what the request may
   see is ranked: sources, filters and visibility come before the cut.
3. The lanes are fused by reciprocal rank per chunk, not per page, so two
   sections of one document can both be kept.
4. Each allowed source gets a floor of the seed slots; a source with nothing
   gives its floor back to the rest.
5. The best seeds start a walk over adopted graph edges (`expand`) within
   hops, a fan-out per visited node and one candidate allowance.
6. What the walk reaches is a chunk of the snapshot, returned with its own
   text, its query scores and every path that reached it.
7. Chunks with the same text are one candidate carrying every id.

Lanes stay apart. `scores` keeps each chunk's BM25, cosine and RRF rank and
score, and its graph rank and hops. Nothing adds them into one number: a
discovery is not a probability that a chunk supports anything, and the graph
lane is appended after the seeds, never in their place.

Rounds. `repair` makes the next round's requests for what is missing — the
sources not yet searched, a bridge entity as a graph seed, scoped
subqueries, adjacent sections, the external families — never the same query
at the same count again. A round inherits the seen chunk ids, the deadline,
the generation and the candidate allowance; there are at most `MAX_ROUNDS`.

The graph-off switch is a request with no `graph_budget`: RRF alone, as
before, and the same result shape.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import defaultdict

from . import evidence, knowledge_graph
from .sources import AUDIENCES, FAMILIES, SOURCE_NAMES

REQUEST = "retrieval-request/1"
RESULT = "retrieval-result/1"
RRF_K = 60
# The plan's initial traversal budget: design defaults, not measurements.
# Every result records the one it ran under.
GRAPH = {"seeds": 8, "hops": 2, "fanout": 5}
CEILING = {"seeds": 40, "hops": 3, "fanout": 20}
# Unique candidate chunks one question may be given across all its rounds —
# seeds, adjacent sections and the graph lane alike, graph on or off.
MAX_CANDIDATES = 40
MAX_ALLOWANCE = 200
MAX_LIMIT = 40
MAX_ROUNDS = 3
MAX_SUBQUERIES = 3
MAX_QUERY = 4000
# Adjacent sections read per chunk whose context is missing.
CONTEXT = 2
# Which of a node's edges are walked first: explicit relations, then a shared
# entity, then the next section. `contains` is not walked: a source stands
# for its chunks, and a chunk reaches its neighbours by `next_chunk`.
# Candidates — `co_injected` among them — are never walked.
PRIORITY = {"links_to": 0, "reads": 0, "supersedes": 0, "contradicts": 0, "depends_on": 0, "mentions": 1,
            "next_chunk": 2}
FILTERS = ("kinds", "visibility", "fetched_after", "audiences")
NEEDS = ("sources", "bridge", "subqueries", "context", "external")

# A version or a year a question is scoped to, and what it excludes.
VERSION = re.compile(r"\bv?\d+(?:\.\d+)+\b|\bv\d+\b|\b(?:19|20)\d{2}\b", re.I)
EXCLUSION = re.compile(r"\b(?:not|except|excluding|without|other than)\s+(`[^`]+`|[\w./-]+)", re.I)


class Stale(Exception):
    """The request names a generation this snapshot does not read."""


class Exhausted(Exception):
    """No retrieval round is left."""

    category = "rounds"


def family(chunk: dict) -> str:
    """The retrieval source a chunk belongs to, as Jev routes them. The
    repository's own research notes are its documents; `research` is what was
    ingested from outside."""

    if chunk.get("record"):
        return next(name for name, kind in FAMILIES.items() if kind == chunk["kind"])
    return {"rule": "hub", "memory": "memory"}.get(chunk["kind"], "documents")


def normal(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def text_key(chunk: dict) -> str:
    """What makes two chunks one candidate: the same text, whitespace and case aside."""

    return evidence.digest(normal(chunk["text"]))


# ---- the contracts ----------------------------------------------------------------

def request(repo_id: str, query: str, *, query_en: str | None = None,
            sources: list[str] | tuple[str, ...] = ("hub", "documents", "memory"), filters: dict | None = None,
            generation: int | None = None, limit: int = 8, seconds: float = 15.0, graph: dict | None = GRAPH,
            max_candidates: int = MAX_CANDIDATES, seen: list[str] = (), spent: int | None = None,
            graph_seeds: list[str] = (), context_of: list[str] = ()) -> dict:
    """A RetrievalRequest for round 1. `graph=None` switches the graph lane off.
    `spent` is how many candidates `seen` already cost: one per id unless said."""

    return {"schema_version": REQUEST, "repo_id": repo_id, "query_original": query, "query_en": query_en,
            "source_allowlist": list(sources), "filters": dict(filters or {}), "generation": generation,
            "limit": limit, "deadline": time.time() + seconds, "graph_budget": dict(graph) if graph else None,
            "max_candidates": max_candidates, "seen_chunk_ids": list(seen),
            "spent": len(seen) if spent is None else spent, "round": 1,
            "graph_seeds": list(graph_seeds), "context_of": list(context_of)}


def problems(req: object) -> list[str]:
    """Everything that makes `req` not a RetrievalRequest. Empty means valid."""

    if not isinstance(req, dict):
        return ["not an object"]
    found = []
    if req.get("schema_version") != REQUEST:
        found.append("schema_version")
    if not evidence.ID.match(str(req.get("repo_id") or "")):
        found.append("repo_id is not a sha256")
    query = req.get("query_original")
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY:
        found.append("query_original")
    english = req.get("query_en")
    if english is not None and (not isinstance(english, str) or not english.strip() or len(english) > MAX_QUERY):
        found.append("query_en")
    allow = req.get("source_allowlist")
    if (not isinstance(allow, list) or not allow or len(set(map(str, allow))) != len(allow)
            or any(s not in SOURCE_NAMES for s in allow)):
        found.append("source_allowlist names unregistered or repeated sources")
    filters = req.get("filters")
    if not isinstance(filters, dict) or set(filters) - set(FILTERS):
        found.append("filters")
    else:
        for name, allowed in (("kinds", evidence.KINDS), ("visibility", evidence.VISIBILITY)):
            value = filters.get(name)
            if value is not None and (not isinstance(value, list) or any(v not in allowed for v in value)):
                found.append(f"filters.{name}")
        if filters.get("fetched_after") is not None and not isinstance(filters["fetched_after"], str):
            found.append("filters.fetched_after")
        wanted = filters.get("audiences")
        if wanted is not None and (not isinstance(wanted, list) or not wanted
                                   or len(set(map(str, wanted))) != len(wanted) or any(a not in AUDIENCES for a in wanted)):
            found.append("filters.audiences")
    if req.get("generation") is not None and type(req["generation"]) is not int:
        found.append("generation")
    limit = req.get("limit")
    if type(limit) is not int or not 0 <= limit <= MAX_LIMIT:
        found.append("limit")
    elif limit == 0 and not (req.get("graph_seeds") or req.get("context_of")):
        found.append("limit 0 without graph_seeds or context_of")
    deadline = req.get("deadline")
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        found.append("deadline")
    budget = req.get("graph_budget")
    if budget is not None and not (isinstance(budget, dict) and set(budget) == set(GRAPH) and all(
            type(budget[n]) is int and (0 if n == "hops" else 1) <= budget[n] <= CEILING[n] for n in GRAPH)):
        found.append("graph_budget")
    if req.get("graph_seeds") and budget is None:
        found.append("graph_seeds with the graph off")
    if type(req.get("max_candidates")) is not int or not 1 <= req["max_candidates"] <= MAX_ALLOWANCE:
        found.append("max_candidates")
    for name in ("seen_chunk_ids", "graph_seeds", "context_of"):
        ids = req.get(name)
        if not isinstance(ids, list) or not all(evidence.ID.match(str(i)) for i in ids):
            found.append(f"{name} are not sha256 ids")
    # Candidates earlier rounds returned; an identical twin's id is seen but costs nothing.
    spent, seen = req.get("spent"), req.get("seen_chunk_ids")
    if type(spent) is not int or not 0 <= spent <= (len(seen) if isinstance(seen, list) else 0):
        found.append("spent")
    if type(req.get("round")) is not int or not 1 <= req["round"] <= MAX_ROUNDS:
        found.append("round")
    return found


def blocked(chunk: dict, req: dict, repos: set[str]) -> str | None:
    """Why the request may not see `chunk`, or `None`. Asked of every seed and
    of every node a walk reaches: a link from one repository into another
    grants nothing."""

    if chunk["repo_id"] not in repos:
        return "out_of_scope"
    if family(chunk) not in req["source_allowlist"]:
        return "source_not_allowed"
    filters = req["filters"]
    if filters.get("kinds") and chunk["kind"] not in filters["kinds"]:
        return "filtered"
    if filters.get("visibility") and chunk["visibility"] not in filters["visibility"]:
        return "filtered"
    # An edition or time restriction reads what was fetched; a local file is its current revision.
    after = filters.get("fetched_after")
    if after and chunk.get("record") and (chunk["record"].get("fetched_at") or "") < after:
        return "filtered"
    # Relevance, not access: a chunk no audience claims is unclassified and stays.
    wanted = filters.get("audiences")
    if wanted and chunk.get("audiences") is not None and not set(wanted) & set(chunk["audiences"]):
        return "audience"
    return None


def hit(chunk: dict) -> dict:
    """A chunk as a result carries it: everything but what is only for indexing."""

    return {k: v for k, v in chunk.items() if k not in ("indexed", "key")}


# ---- one round ------------------------------------------------------------------------

def run(index, req: dict, cancel: threading.Event | None = None) -> dict:
    """The RetrievalResult for `req` from `index`, a snapshot. `ValueError`
    for a request that is not valid or not this index's; `Stale` when it
    names another generation."""

    started = time.monotonic()
    wrong = problems(req)
    if wrong:
        raise ValueError("; ".join(wrong))
    own = evidence.repo_id(index.project or index.hub)
    if req["repo_id"] != own:
        raise ValueError("repo_id is not this index's repository")
    generation = index.generation()
    if req["generation"] is not None and req["generation"] != generation:
        raise Stale("generation_changed")
    repos = {own} | ({evidence.repo_id(index.hub)} if "hub" in req["source_allowlist"] else set())
    chunks = index.chunks
    seen = set(req["seen_chunk_ids"])
    visible = {i for i, c in enumerate(chunks) if blocked(c, req, repos) is None}
    open_ = {i for i in visible if chunks[i]["chunk_id"] not in seen}

    queries = [req["query_original"]]
    if req["query_en"] and normal(req["query_en"]) != normal(req["query_original"]):
        queries.append(req["query_en"])
    lexical = [index.bm25(q) for q in queries]
    dense = index.cosines(queries)
    lanes = {f"bm25:{n}": s for n, s in enumerate(lexical)} | {f"cos:{n}": s for n, s in enumerate(dense or [])}
    ranks = {}
    for name, scores in lanes.items():
        order = sorted((i for i in scores if i in open_), key=lambda i: (-scores[i], i))
        ranks[name] = {i: r for r, i in enumerate(order)}
    fused: dict[int, float] = defaultdict(float)
    for ranked_ in ranks.values():
        for i, r in ranked_.items():
            fused[i] += 1 / (RRF_K + r + 1)

    def best(kind: str, i: int) -> dict | None:
        names = [n for n in lanes if n.startswith(kind) and i in lanes[n]]
        if not names:
            return None
        rank = min((ranks[n][i] for n in names if i in ranks[n]), default=None)
        return {"rank": None if rank is None else rank + 1, "score": round(max(lanes[n][i] for n in names), 5)}

    def relevance(i: int) -> float:
        return max([s.get(i, 0.0) for s in lexical], default=0.0)

    # Chunks with the same text are one candidate: whichever a lane returns
    # carries the ids of every other the request may see, so no later round
    # returns one of them as new — and a walk from it starts from each of
    # them, since the same words in another file may link elsewhere.
    # Each copy maps to its group's one list (itself included): linear in
    # the chunks, however many copies of a boilerplate paragraph there are.
    same: dict[str, list[str]] = defaultdict(list)
    for i in sorted(visible):
        same[text_key(chunks[i])].append(chunks[i]["chunk_id"])
    twins = {chunk_id: group for group in same.values() if len(group) > 1 for chunk_id in group}
    texts: set[str] = set()
    ranked = []
    # The source families each matching text stands for: a candidate kept
    # for one covers every family its copies belong to.
    covers: dict[str, set[str]] = defaultdict(set)
    for i in sorted(fused, key=lambda i: (-fused[i], i)):
        key = text_key(chunks[i])
        covers[key].add(family(chunks[i]))
        if key not in texts:
            texts.add(key)
            ranked.append(i)
    position = {i: n for n, i in enumerate(ranked)}

    budget = req["graph_budget"]
    # What earlier rounds left of the question's allowance; every lane of this one spends from it.
    allowance = req["max_candidates"] - req["spent"]
    slots = max(0, min(req["limit"], allowance))
    names = req["source_allowlist"]
    groups = {name: [i for i in ranked if name in covers[text_key(chunks[i])]] for name in names}
    floor = max(1, slots // (2 * len(names))) if slots >= len(names) else 0
    picked = {i for name in names for i in groups[name][:floor]}
    for i in ranked:
        if len(picked) >= slots:
            break
        picked.add(i)
    selected = sorted(picked, key=position.get)
    coverage = {name: {"candidates": len(groups[name]), "floor": min(floor, len(groups[name])),
                       "selected": sum(name in covers[text_key(chunks[i])] for i in selected)} for name in names}

    paths: list[dict] = []
    # Earlier rounds spent the allowance this one would have used.
    truncated: list[str] = ["candidates"] if req["limit"] and slots < req["limit"] else []
    taken = set(selected)
    walk = dict(index=index, req=req, repos=repos, seen=seen, relevance=relevance, paths=paths, cancel=cancel,
                twins=twins)
    # The snapshot's graph was built from other chunks than its own: nothing is walked.
    if index.graph is None and (req["context_of"] or budget):
        truncated.append("graph_stale")
    context = {}
    if req["context_of"] and index.graph is not None:
        context, cut = expand(**walk, starts=req["context_of"], kinds=("next_chunk",), hops=1, fanout=CONTEXT,
                              room=allowance - len(taken), taken=taken, lane="context")
        truncated += cut
        taken |= set(context)
    found = {}
    if budget and index.graph is not None:
        seeds = [chunks[i]["chunk_id"] for i in selected[:budget["seeds"]]] + req["graph_seeds"]
        found, cut = expand(**walk, starts=seeds, kinds=tuple(PRIORITY), hops=budget["hops"],
                            fanout=budget["fanout"], room=allowance - len(taken), taken=taken, lane="graph")
        truncated += cut

    def closeness(i: int) -> float:
        return max((s[i] for s in dense or [] if i in s), default=None) or relevance(i)

    # The graph lane's order: fewer hops, then a better seed — a graph seed
    # after every chunk seed — then cosine once there are vectors, else BM25.
    order = {chunks[i]["chunk_id"]: n for n, i in enumerate(selected)}

    def discovery(i: int) -> tuple:
        mine = [paths[p] for p in found[i]]
        return (min(p["hops"] for p in mine), min(order.get(p["seed"], len(order)) for p in mine), -closeness(i), i)

    graph_lane = [i for i in sorted(found, key=discovery) if i not in taken]
    # Adjacent sections in the order they stand in their source.
    lanes_of = [(i, "rrf") for i in selected] + [(i, "context") for i in sorted(context) if i not in picked]
    lanes_of += [(i, "graph") for i in graph_lane]
    rank_in_graph = {i: n for n, i in enumerate(graph_lane, 1)}
    out, scores = [], {}
    for i, lane in lanes_of:
        chunk_id = chunks[i]["chunk_id"]
        out.append({**hit(chunks[i]), "lane": lane,
                    "duplicates": [c for c in twins.get(chunk_id, ()) if c != chunk_id]})
        reached = found.get(i, []) + context.get(i, [])
        scores[chunk_id] = {
            "rrf": {"rank": position[i] + 1, "score": round(fused[i], 5)} if i in position else None,
            "bm25": best("bm25", i), "cos": best("cos", i),
            "graph": {"rank": rank_in_graph.get(i), "hops": min(paths[p]["hops"] for p in reached),
                      "paths": reached} if reached else None}
    returned = [c["chunk_id"] for c in out] + [d for c in out for d in c["duplicates"]]
    wanted = req["filters"].get("audiences")
    return {"schema_version": RESULT, "generation": generation, "round": req["round"], "queries": queries,
            "vectors": dense is not None, "graph_budget": budget, "max_candidates": req["max_candidates"],
            "chunks": out, "scores": scores, "paths": paths,
            "coverage": coverage, "missing_sources": [name for name in names if not groups[name]],
            "truncated": sorted(set(truncated)), "seen_chunk_ids": list(dict.fromkeys([*req["seen_chunk_ids"],
                                                                                       *returned])),
            "spent": req["spent"] + len(out),
            # What an audience filter let through only because no audience claims it.
            "audiences": {"requested": wanted, "unclassified": sum(c.get("audiences") is None for c in out)}
            if wanted else None,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 2)}


# ---- the graph lane -------------------------------------------------------------------

def expand(index, req: dict, repos: set[str], seen: set[str], relevance, paths: list[dict], cancel,
           starts: list[str], kinds: tuple[str, ...], hops: int, fanout: int, room: int, taken: set[int],
           lane: str, twins: dict[str, list[str]]) -> tuple[dict[int, list[int]], list[str]]:
    """Walk adopted edges of `kinds` from `starts` (chunk or entity ids).
    Returns `{chunk index: [indexes into paths]}` for every chunk reached,
    new or already `taken`, and why the walk stopped short, if it did.

    A node of the walk is a text, not a file: the copies of a chunk in
    `twins` are one candidate, so they are one node — walked from all of
    them at once, their edges pooled, reached when any of them is. A step
    names the copy whose edge it followed (`copy`) when that is not the
    path's own node. A copy reached is recorded as reached (the path names
    it) and credited to the chunk holding that text, taken or found.

    One hop is one edge. A source stands for its chunks, and a decision for
    its record's: reaching either offers their chunks, best BM25 first. From
    a chunk the walk also follows its source's and its decision's own edges
    — a front-matter `reads`, a `supersedes`. At each node at most `fanout`
    new neighbours are taken, by `PRIORITY` and then BM25, so a hub with a
    hundred links costs five; a node that had more says so (`fanout`). Every
    neighbour is checked as a seed is (`blocked`); a blocked one is recorded
    and not walked through. A node is walked once, so a cycle ends; a node
    reached again from another seed keeps that seed's path, one per seed, as
    corroboration. Only `room` new candidates may be added. Every path,
    reached or refused, goes into `paths`.
    """

    chunks, graph = index.chunks, index.graph
    by_id = {c["chunk_id"]: i for i, c in enumerate(chunks)}
    by_source: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(chunks):
        by_source[c["source_id"]].append(i)
    found: dict[int, list[int]] = {}
    truncated: list[str] = []

    # Each text a candidate already holds, and which candidate holds it.
    held = {text_key(chunks[i]): i for i in taken}

    def key(node: str) -> str:
        """The walk's node for an id: a chunk's text group, else the id."""

        return twins[node][0] if node in twins else node

    def members(node: str) -> list[str]:
        return twins.get(node, [node])

    def record(trail: list[dict], status: str) -> int:
        paths.append({"lane": lane, "seed": trail[0]["node"], "to": trail[-1]["node"], "hops": len(trail) - 1,
                      "status": status, "steps": trail})
        return len(paths) - 1

    def own_ids(i: int) -> list[str]:
        c = chunks[i]
        ids = [c["chunk_id"], c["source_id"]]
        if c["kind"] == "decision" and "path" in c["locator"]:
            ids.append(knowledge_graph.decision_id(c["repo_id"], c["locator"]["path"]))
        return ids

    nodes = graph.nodes([s for s in starts if s not in by_id])
    frontier: list[tuple[str, list[dict]]] = []
    visited: set[str] = set()
    for start in dict.fromkeys(starts):
        node = nodes.get(start)
        trail = [{"node": start, "node_kind": "chunk" if start in by_id else (node or {}).get("kind")}]
        # A start the caller named is checked as any node is: a walk never
        # begins in a source or a visibility the request may not see.
        why = blocked(chunks[by_id[start]], req, repos) if start in by_id else None
        if start not in by_id and (node is None or node["kind"] != "entity"):
            record(trail, "deleted")
        elif why or (node is not None and node["repo_id"] not in repos):
            record(trail, why or "out_of_scope")
        elif key(start) not in visited:
            visited.add(key(start))
            frontier.append((start, trail))

    for _hop in range(hops):
        if not frontier:
            break
        if cancel is not None and cancel.is_set():
            truncated.append("cancelled")
            break
        if time.time() >= req["deadline"]:
            truncated.append("deadline")
            break
        # Every copy's own edges: the same words in another file may link elsewhere.
        # `owner` is, per node, the copy each id asked for belongs to, which a
        # step names; the node's own ids first, since a source is shared by
        # every section of its file.
        owner: dict[tuple[str, str], str] = {}
        for node, _trail in frontier:
            if node in by_id:
                for m in (node, *members(node)):
                    for x in own_ids(by_id[m]):
                        owner.setdefault((node, x), m)
        asks = {node: list(dict.fromkeys(x for m in members(node) for x in own_ids(by_id[m])))
                if node in by_id else [node] for node, _trail in frontier}
        edges = graph.edges(sorted({x for ids in asks.values() for x in ids}), kinds=list(kinds))
        by_via: dict[str, list[dict]] = defaultdict(list)
        for edge in edges:
            by_via[edge["via"]].append(edge)
        others = {e["to_id"] if e["from_id"] == e["via"] else e["from_id"] for e in edges}
        far = graph.nodes(sorted(o for o in others if o not in by_id and o not in by_source))
        following: list[tuple[str, list[dict]]] = []
        for node, trail in frontier:
            options = []
            for x in asks[node]:
                for edge in by_via.get(x, []):
                    other = edge["to_id"] if edge["from_id"] == x else edge["from_id"]
                    target = far.get(other)
                    if other in by_id:
                        offered = [("chunk", other)]
                    elif other in by_source or (target and target["kind"] in ("source", "decision")):
                        source = other if other in by_source else target["source_id"]
                        mine = sorted(by_source.get(source, []), key=lambda i: (-relevance(i), i))
                        offered = [("chunk", chunks[i]["chunk_id"]) for i in mine] or [("gone", other)]
                    elif target and target["kind"] == "entity":
                        offered = [("entity", other)]
                    else:
                        offered = [("gone", other)]
                    for kind, tid in offered:
                        score = relevance(by_id[tid]) if kind == "chunk" else 0.0
                        options.append((PRIORITY[edge["kind"]], -score, tid, kind, edge, other,
                                        owner.get((node, x), node)))
            options.sort(key=lambda o: o[:3])
            taken_here = 0
            for n, (_p, _s, tid, kind, edge, other, copy) in enumerate(options):
                if taken_here >= fanout:
                    # More than a fan-out of new neighbours: the rest are not walked, and that is said.
                    if any(key(o[2]) not in visited for o in options[n:]):
                        truncated.append("fanout")
                    break
                step = {"node": tid, "node_kind": kind, "edge_id": edge["edge_id"], "kind": edge["kind"],
                        "reverse": edge["reverse"], "origin": edge["origin"], "confidence": edge["confidence"],
                        **({"through": other} if other != tid else {}),
                        # The copy of this node whose edge it is, when not the node itself.
                        **({"copy": copy} if copy != node else {})}
                path = trail + [step]
                if key(tid) in visited:
                    # Reached before, or a seed itself: the new seed's path is kept, credited to
                    # the chunk holding that text — never through a chunk the request may not see.
                    holder = (held.get(text_key(chunks[by_id[tid]]))
                              if kind == "chunk" and not blocked(chunks[by_id[tid]], req, repos) else None)
                    seed = trail[0]["node"]
                    if (holder is not None and key(seed) != key(tid)
                            and seed not in {paths[p]["seed"] for p in found.get(holder, [])}):
                        found.setdefault(holder, []).append(record(path, "corroborated"))
                    continue
                visited.add(key(tid))
                if kind == "gone":
                    record(path, "deleted")
                    continue
                if kind == "entity":
                    if far[tid]["repo_id"] not in repos:
                        record(path, "out_of_scope")
                        continue
                    taken_here += 1
                    following.append((tid, path))
                    continue
                i = by_id[tid]
                why = blocked(chunks[i], req, repos)
                if why:
                    record(path, why)
                    continue
                if tid in seen:
                    # Evidence of an earlier round: walked through, not returned again.
                    record(path, "seen")
                elif text_key(chunks[i]) in held:
                    # A taken chunk, or a copy of one: it corroborates the holder at no cost.
                    found.setdefault(held[text_key(chunks[i])], []).append(record(path, "corroborated"))
                elif room <= 0:
                    # The allowance is spent: one refusal says so, and the walk ends.
                    record(path, "budget")
                    truncated.append("candidates")
                    return found, truncated
                else:
                    room -= 1
                    found[i] = [record(path, "discovered")]
                    held[text_key(chunks[i])] = i
                taken_here += 1
                following.append((tid, path))
        frontier = following
    return found, truncated


# ---- the next round -------------------------------------------------------------------

def checked_subqueries(original: str, proposals: object,
                       every_exclusion: bool = False) -> tuple[list[str], list[dict]]:
    """`(kept, rejected)` of a generator's subqueries for `original`.

    Code keeps what the question is scoped to: every version or year it
    names stays in each subquery and none is added, and a subquery that
    names what the question excludes must exclude it too — with
    `every_exclusion`, every subquery must, named or not: a requirement
    that dropped an exclusion would be judged covered on broader evidence. Up to
    `MAX_SUBQUERIES`, none the question itself. Whether a subquery keeps
    the question's intent is the decision workflow's to judge.
    """

    versions = {v.casefold() for v in VERSION.findall(original)}
    exclusions = [(m.group(0).casefold(), m.group(1).strip("`").casefold()) for m in EXCLUSION.finditer(original)]
    kept, rejected = [], []
    for raw in proposals if isinstance(proposals, list) else []:
        query = " ".join(raw.split()) if isinstance(raw, str) else ""
        mine = {v.casefold() for v in VERSION.findall(query)}
        folded = query.casefold()
        why = ("empty" if not query else "too_long" if len(query) > MAX_QUERY
               else "same_as_question" if normal(query) == normal(original)
               else "duplicate" if normal(query) in map(normal, kept)
               else "version_added" if mine - versions else "version_dropped" if versions - mine
               else "exclusion_dropped" if any((every_exclusion or term in folded) and phrase not in folded
                                               for phrase, term in exclusions)
               else "over_limit" if len(kept) >= MAX_SUBQUERIES else None)
        if why:
            rejected.append({"subquery": raw, "reason": why})
        else:
            kept.append(query)
    return kept, rejected


def repair(req: dict, result: dict, need: str, *, sources: list[str] = (), subqueries: list[str] = (),
           chunk_ids: list[str] = (), entities: list[str] | None = None) -> tuple[list[dict], dict]:
    """The next round's requests for what `result` is missing, and a note of
    what was made and refused. `[]` when there is nothing different to ask;
    `Exhausted` past `MAX_ROUNDS`.

    - `sources`: the enabled `sources` this round did not search.
    - `bridge`: the entities this round's walk reached (or those of
      `entities` among them) as graph seeds, with no text search.
    - `subqueries`: each checked subquery (`checked_subqueries`), the seed
      slots and what is left of the allowance split between them.
    - `context`: the sections next to `chunk_ids`, which must be this
      round's.
    - `external`: the paper and research families, after the caller has
      fetched from an enabled provider.
    """

    if need not in NEEDS:
        raise ValueError(f"need is one of {NEEDS}")
    if req["round"] >= MAX_ROUNDS:
        raise Exhausted("rounds")
    base = {**req, "round": req["round"] + 1, "seen_chunk_ids": list(result["seen_chunk_ids"]),
            "spent": result["spent"], "generation": result["generation"], "graph_seeds": [], "context_of": []}
    note: dict = {"need": need, "round": base["round"]}
    if need == "sources":
        rest = [s for s in dict.fromkeys(sources) if s in SOURCE_NAMES and s not in req["source_allowlist"]]
        note["sources"] = rest
        return ([{**base, "source_allowlist": rest}] if rest else []), note
    if need == "external":
        return [{**base, "source_allowlist": list(FAMILIES)}], note
    if need == "bridge":
        reached = [s["node"] for p in result["paths"] if p["status"] in ("discovered", "corroborated")
                   for s in p["steps"][1:] if s["node_kind"] == "entity"]
        seeds = [e for e in dict.fromkeys(reached) if entities is None or e in entities]
        if req["graph_budget"] is None or not seeds:
            return [], {**note, "entities": []}
        seeds = seeds[:req["graph_budget"]["seeds"]]
        return [{**base, "limit": 0, "graph_seeds": seeds}], {**note, "entities": seeds}
    if need == "context":
        mine = {c["chunk_id"] for c in result["chunks"]}
        ids = [c for c in dict.fromkeys(chunk_ids) if c in mine]
        return ([{**base, "limit": 0, "context_of": ids}] if ids else []), {**note, "chunks": ids}
    kept, rejected = checked_subqueries(req["query_en"] or req["query_original"], list(subqueries))
    # Sibling rounds share what is left of the allowance, split between
    # them: each would otherwise spend all of it, since none sees the others.
    left = req["max_candidates"] - base["spent"]
    rejected += [{"subquery": q, "reason": "no_allowance"} for q in kept[max(0, left):]]
    kept = kept[:max(0, left)]
    note |= {"subqueries": kept, "rejected": rejected}
    share = max(1, req["limit"] // max(1, len(kept)))
    room = left // max(1, len(kept))
    return [{**base, "query_original": q, "query_en": None, "limit": min(share, room),
             "max_candidates": base["spent"] + room} for q in kept], note
