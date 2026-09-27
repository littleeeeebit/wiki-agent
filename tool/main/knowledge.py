"""Jev retrieval, composed: `search`'s controller with `decision`'s transport
and `translate`'s English normalization — and, for stage 3, the sources that
feed it: `search.providers` fetches, `search.sources` keeps the records, and
adopted research reaches the wiki through a worktree (`workspace`).

The one place they meet, so the app's query path and the root CLIs
(`tool/jev_search.py`, `tool/ingest.py`, `tool/source.py`) run the same flow
on the same settings. The settings are read once per call — a snapshot for that run — and
never held, so a key changed in `.env` reaches a server that is already
running on its next turn.
"""

from __future__ import annotations

import functools
import json
import re
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path

import decision
import translate
from agent import oneshot
from common import settings
from common.budget import QUESTION, Budget
from common.language import language
from search import HUB, evidence_store, knowledge_graph, local_index, providers, records, resolve, retrieval, sources
from search import prepare as controlled
from search import retrieve as retrieve_from_daemon
from workspace import create, folder_for
from session_state import active_page, decisions, plans
from session_state import run as git

# The controller's ceiling on `current_state` (`search.controller.MAX_STATE`).
STATE_CHARS = 4000
# Texts per translation request while ingesting, and the time one may take.
BATCH = 16
BATCH_SECONDS = 60.0


def disabled(*_args) -> dict:
    raise decision.JevError("disabled")


def english(texts: list[str], seconds: float, owners: list[tuple[str, ...]] | None = None,
            project: str | Path | None = None) -> list[dict]:
    """English normalization, bounded by its seconds.

    A text with owners — the private sources it came from — never reaches the
    translator's cache. Its English is read from `project`'s evidence store
    and kept there, beside those sources, so it goes when they do; the
    translator checks what is read as it checks a cache hit (version,
    retirement, the protected spans).
    """

    deadline = time.monotonic() + seconds
    owners = owners or [()] * len(texts)
    out: list[dict] = [{}] * len(texts)
    for group, private in (([i for i, o in enumerate(owners) if not o], False),
                           ([i for i, o in enumerate(owners) if o], True)):
        if not group:
            continue
        sources = {s for i in group for s in owners[i]}
        store = evidence_store(project) if private and project else None
        try:
            held = {}
            for source in sources if store else ():
                held |= store.english(source, [texts[i] for i in group if source in owners[i]])
            made = translate.english([texts[i] for i in group], deadline, held=held if private else None)
            for i, outcome in zip(group, made):
                out[i] = outcome
            for source in sources if store else ():
                store.keep_english(source, [(texts[i], out[i]) for i in group
                                            if source in owners[i] and out[i]["status"] == "translated"])
        finally:
            if store:
                store.close()
    return out


def summarized(state: str, repo: Path | None) -> tuple[str, dict | None]:
    """`state` as it is when it fits; past `STATE_CHARS`, a structured summary
    of where the work stands, and what the summary left out.

    Jev then judges against the summary, told what is missing, instead of the
    whole feature falling back because the conversation grew long.
    """

    if len(state) <= STATE_CHARS:
        return state, None
    active = active_page(repo)[0] if repo else ""
    open_plans = plans(repo) if repo else []
    revision = git(repo, "rev-parse", "HEAD") if repo else ""
    summary = {
        "summary_of": f"current_state, which exceeded {STATE_CHARS} characters",
        # In backticks: a code span is lifted out before translation, untouched.
        "revision": f"`{revision}`" if revision else None,
        "active_specification": active[:600] or (open_plans[0][0].relative_to(repo).as_posix() if open_plans else None),
        "unresolved_requirements": [f"{path.relative_to(repo).as_posix()}: {row}"
                                    for path, rows in open_plans for row in rows],
        "recent_decisions": [f"{title} — {why}".rstrip(" —") for title, why in (decisions(repo) if repo else [])],
        "recent_state": state[-1500:],
    }
    while len(json.dumps(summary, ensure_ascii=False)) > STATE_CHARS:
        longest = max(("unresolved_requirements", "recent_decisions"), key=lambda k: len(summary[k]))
        if summary[longest]:
            summary[longest].pop()
        else:
            summary["recent_state"] = summary["recent_state"][len(summary["recent_state"]) // 4 + 1:]
    omitted = {"characters": len(state) - len(summary["recent_state"]),
               "reference": "current_state before recent_state: the earlier conversation turns"}
    return json.dumps(summary, ensure_ascii=False), omitted


def prepare(query: str, project: str | Path | None, state: str = "", k: int = 8,
            cfg: decision.Config | None = None, cancel: threading.Event | None = None) -> dict:
    """The dossier for one question, with the settings it ran under (never the key).

    Mode off sends nothing — to Jev or to the translator: the dossier is
    baseline retrieval with the reason `disabled`, whoever asked.
    """

    cfg = cfg or decision.config()
    live = cfg.mode != "off"
    evaluate = functools.partial(decision.evaluate, cfg) if live else disabled
    root = Path(project).resolve() if project else None
    brief, omitted = summarized(state, root)
    dossier = controlled(query, project, brief, k, evaluate=evaluate, budget=Budget(**QUESTION, cancel=cancel),
                         normalize=functools.partial(english, project=project) if live else None, omitted=omitted)
    return {**dossier, "jev": cfg.status(),
            "state": {"characters": len(state), "summarized": omitted is not None, "omitted": omitted}}


def ingest(project: str | Path | None, seconds: float = 600.0, estimate: bool = False) -> dict:
    """Index `project` and normalize its evidence into English ahead of the
    questions, so a turn finds its passages' English cached — a private
    memory's in the evidence store beside it, the rest in the translator's cache.

    `estimate` counts what would be sent and sends nothing. English needs no
    request; only Korean text, chunk and heading, is translated, `BATCH` per
    request, until `seconds` run out.
    """

    index = local_index(project)
    try:
        chunks = list(index.chunks)
    finally:
        index.close()
    names = translate.glossary()[0]
    # The private sources each text came from; `""` for a shared file, whose
    # text is then the shared file's, cached as any.
    owners: dict[str, set[str]] = {}
    for c in chunks:
        for text in (c["text"], c["heading"]):
            owners.setdefault(text, set()).add(c["source_id"] if c["visibility"] == "private" else "")
    texts = list(owners)
    shared = [t for t in texts if "" in owners[t] and language(t, names) == "ko"]
    private = [t for t in texts if "" not in owners[t] and language(t, names) == "ko"]
    # Every chunk's citation, read back from its file: what it quotes must be what is there.
    unresolved = [f"{where(c)}:{c['line']}" for c in chunks if resolve(c, c["path"]) != c["text"]]
    counts = {"chunks": len(chunks), "texts": len(texts), "korean": len(shared) + len(private),
              "requests_at_most": -(-len(shared) // BATCH) - (-len(private) // BATCH),
              "unresolved_citations": unresolved}
    if estimate:
        return counts
    end = time.monotonic() + seconds
    statuses: Counter = Counter()
    for group in (shared, private):
        for start in range(0, len(group), BATCH):
            batch = group[start:start + BATCH]
            seconds = max(0.0, min(end - time.monotonic(), BATCH_SECONDS))
            mine = [() if "" in owners[t] else tuple(owners[t]) for t in batch]
            statuses.update(o["status"] for o in english(batch, seconds, mine, project))
    return {**counts, "statuses": dict(statuses)}


def where(chunk: dict) -> str:
    """A chunk's source as a person would name it: its path, URL or document."""

    locator = chunk["locator"]
    return locator.get("path") or locator.get("url") or locator["document"]


# ---- sources (stage 3 of `docs/plans/jev/`) ------------------------------------
#
# Every fetch here is one somebody asked for: a URL, a paper search, a file.
# A link inside a document is never followed on its own. What is fetched lands
# in the user's cache (`search.records`); nothing is written into the
# checkout, and adopted research reaches the wiki only as a worktree commit.

LICENSE_ARXIV = "arXiv abstract and metadata; the paper's license is on its abstract page."
# At or below this a graded paper is kept as discovered, not indexed (`search.controller.NO`).
NOT_RELEVANT = 0.2
QUERY_SECONDS = 4.0


def stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def root_of(project: str | Path | None) -> Path:
    return Path(project).resolve() if project else HUB


def failed(record: dict, error: providers.FetchError) -> dict:
    """A fetch that failed. Content read before stays, and stays searchable;
    a source never read becomes `unavailable`, with the reason in view."""

    record["error"] = {"reason": error.reason, "detail": error.detail, "at": stamp()}
    if record["content_hash"] is None:
        record.update(status="unavailable", coverage="metadata_only")
    return record


def read_into(store, record: dict, data: bytes, *, coverage: str, form: str, cite: str,
              revision: str | None = None) -> dict:
    """`record` after reading `data`, its edition `revision` (the content's
    hash by default). Nothing in it to cut is a `FetchError`. Other content
    than before, or another edition of the same bytes — an arXiv version
    whose abstract did not change — is a new edition: the old snapshot stays,
    citable, with the decision that was made about it."""

    sha = store.keep(data)
    probe = {**record, "content_hash": sha, "form": form, "cite": cite, "coverage": coverage}
    if not sources.cut(probe, data.decode("utf-8", errors="replace"), store.snapshot(sha)):
        raise providers.FetchError("no_text")
    if record["content_hash"] and (record["content_hash"] != sha or record["revision"] != (revision or sha)):
        record["editions"].append({name: record[name] for name in
                                   ("revision", "content_hash", "fetched_at", "coverage", "form", "cite", "adoption")})
        # A decision was about what it read; new content is undecided.
        record["adoption"] = None
    record.update(content_hash=sha, revision=revision or sha, coverage=coverage, form=form, cite=cite,
                  fetched_at=stamp(), error=None)
    if record["adoption"] is None:
        record["status"] = "indexed"
    return record


def unwanted(record: dict | None) -> bool:
    """A source switched off: not fetched again, and not searched."""

    return record is not None and not record["enabled"]


def pdf_text(data: bytes) -> tuple[str, str]:
    """`(text, coverage)` of a PDF, pages apart by form feeds. Some pages
    without text is `partial`; none is a failure, never an empty full text."""

    got = [sources.text_of(page) for page in providers.pages(data)]
    if not any(page.strip() for page in got):
        raise providers.FetchError("no_text", "no page has extractable text")
    return "\f".join(got), "full_text" if all(page.strip() for page in got) else "partial"


def add_url(project: str | Path | None, url: str, seconds: float = providers.SECONDS) -> dict:
    """Fetch one explicit URL into `project`'s research. The same document
    under another spelling is the same record. A URL the fetcher would refuse
    as written — credentials in it, another scheme — is refused here, before
    canonical spelling could drop what made it refused, and nothing is kept."""

    root = root_of(project)
    providers.checked(url.strip())
    origin = sources.canonical_url(url)
    with records(project) as store:
        record = store.get(sources.new(root, "research", origin)["source_id"])
        if unwanted(record):
            raise ValueError(f"disabled source: {origin}")
        record = record or sources.new(root, "research", origin)
        try:
            got = providers.fetch(origin, providers.PDF_BYTES, seconds)
            if got["content_type"] == "application/pdf":
                text, coverage = pdf_text(got["body"])
                read_into(store, record, text.encode("utf-8"), coverage=coverage, form="pages", cite=origin)
            else:
                body = providers.decoded(got)
                title, text = (providers.html_text(body) if "html" in got["content_type"] else
                               (next((m.group(1) for m in re.finditer(r"^# (.+)$", body, re.M)), ""), body))
                record["title"] = title or record["title"]
                read_into(store, record, sources.text_of(text).encode("utf-8"), coverage="full_text", form="text",
                          cite=got["url"])
        except providers.FetchError as error:
            failed(record, error)
        return store.put(record)


def add_papers(project: str | Path | None, query: str | None = None, ids: list[str] | None = None, n: int = 5,
               full: bool = False, cfg: decision.Config | None = None) -> dict:
    """arXiv papers into `project`: a search, or identifiers.

    Each paper's abstract is read and indexed as `abstract_only`; with `full`,
    its PDF too, and only a successful extraction makes it `full_text`. With
    Jev on, a search's papers are graded against the query and the grade is
    recorded. Only in active mode does it act: a paper graded as not relevant
    keeps its metadata as `discovered` and nothing of it is indexed — shadow
    records, as it does for questions, and changes nothing. Jev failing grades
    nothing and costs no paper.
    """

    root = root_of(project)
    cfg = cfg or decision.config()
    if query:
        asked = english([query], QUERY_SECONDS)[0]
        query = asked["text"] if asked["status"] in ("original_english", "translated") else query
    entries = providers.arxiv(query, ids, n)
    grades, trace = grade_papers(query, entries, cfg) if query else ({}, [])
    acting = cfg.mode == "active"
    out = []
    with records(project) as store:
        for i, entry in enumerate(entries):
            origin = f"arxiv:{entry['arxiv_id']}"
            record = store.get(sources.new(root, "paper", origin)["source_id"])
            if unwanted(record):
                out.append(record)
                continue
            record = record or sources.new(root, "paper", origin)
            record.update(title=entry["title"], authors=entry["authors"], published_at=entry["published"],
                          license_note=LICENSE_ARXIV, relevance=grades.get(i, record["relevance"]))
            edition = f"{entry['arxiv_id']}{entry['version']}"
            if acting and grades.get(i) is not None and grades[i] <= NOT_RELEVANT and record["content_hash"] is None:
                record.update(status="discovered", revision=edition)
                out.append(store.put(record))
                continue
            try:
                # A full text already read of this edition is not traded for its abstract.
                if not (record["revision"] == edition and record["coverage"] in ("full_text", "partial")):
                    read_into(store, record, sources.text_of(entry["summary"]).encode("utf-8"), revision=edition,
                              coverage="abstract_only", form="text", cite=entry["abs_url"])
                if full and record["coverage"] == "abstract_only":
                    got = providers.fetch(entry["pdf_url"], providers.PDF_BYTES, types=("application/pdf",))
                    text, coverage = pdf_text(got["body"])
                    read_into(store, record, text.encode("utf-8"), revision=edition, coverage=coverage,
                              form="pages", cite=f"arxiv:{edition}")
            except providers.FetchError as error:
                failed(record, error)
            out.append(store.put(record))
    return {"query": query, "trace": trace,
            "papers": [sources.brief(r) | {"relevance": r["relevance"], "error": r["error"]} for r in out]}


def grade_papers(query: str, entries: list[dict], cfg: decision.Config) -> tuple[dict[int, float], list]:
    """Jev's relevance of each abstract to the query, to decide what to read.
    `{}` when Jev is off or fails: every paper is then read."""

    trace: list[dict] = []
    if cfg.mode == "off" or not entries:
        return {}, trace
    state = {"query": query, "papers": [{"id": str(i), "title": e["title"], "abstract": e["summary"][:2000]}
                                        for i, e in enumerate(entries)]}
    questions = {str(i): decision.noul(f"Could paper {i}'s abstract contain evidence useful for the query, "
                                       "including a partial answer or a contradiction? Topic overlap alone is "
                                       "insufficient.") for i in range(len(entries))}
    try:
        got = decision.evaluate(cfg, state, questions, trace, Budget(**QUESTION), "papers")
    except Exception as error:  # noqa: BLE001 — no grade is no ranking, never a rejection
        trace.append({"fallback": type(error).__name__, "reason": getattr(error, "category", "")})
        return {}, trace
    return {int(k): v for k, v in got.items()}, trace


def add_file(project: str | Path | None, path: str | Path) -> dict:
    """A local paper — PDF, Markdown or text — registered into `project`.
    Its content is kept as read, so a later edit of the file is a new edition."""

    file = Path(path).expanduser().resolve()
    root = root_of(project)
    origin = file.as_posix()
    with records(project) as store:
        record = store.get(sources.new(root, "paper", origin)["source_id"])
        if unwanted(record):
            raise ValueError(f"disabled source: {origin}")
        record = record or sources.new(root, "paper", origin, title=file.stem)
        try:
            try:
                data = file.read_bytes()
            except OSError as error:
                raise providers.FetchError("unreadable", type(error).__name__) from None
            if file.suffix.lower() == ".pdf":
                text, coverage = pdf_text(data)
                read_into(store, record, text.encode("utf-8"), coverage=coverage, form="pages", cite=origin)
            elif file.suffix.lower() in (".md", ".markdown", ".txt"):
                try:
                    data.decode("utf-8")
                except UnicodeDecodeError:
                    raise providers.FetchError("not_utf8") from None
                read_into(store, record, data, coverage="full_text", form="file", cite=origin)
            else:
                raise providers.FetchError("unsupported_type", file.suffix)
        except providers.FetchError as error:
            failed(record, error)
        return store.put(record)


def find(store, source: str) -> dict:
    """The record whose id is `source` or starts with it (eight characters at least)."""

    found = [r for r in store.all()
             if r["source_id"] == source or (len(source) >= 8 and r["source_id"].startswith(source))]
    if len(found) != 1:
        raise KeyError(f"{'no' if not found else 'more than one'} source record matches {source!r}")
    return found[0]


def decide(project: str | Path | None, source: str, verdict: str, *, rationale: str, claims: list[str] = (),
           scope: str = "", counterevidence: list[str] = (), conditions: list[str] = ()) -> dict:
    """Adopt or reject a source, with why. Only content that was read can be
    decided on; adopting needs the claims taken and where they apply. The
    decision names the edition and the coverage it was made on."""

    if verdict not in ("adopted", "rejected"):
        raise ValueError("adopted or rejected")
    if not rationale.strip():
        raise ValueError("a decision needs its rationale")
    if verdict == "adopted" and not (list(claims) and scope.strip()):
        raise ValueError("adopting needs the claims taken and their scope")
    with records(project) as store:
        record = find(store, source)
        if record["status"] not in sources.SEARCHABLE:
            raise ValueError(f"{record['status']}: nothing of this source was read")
        record["adoption"] = {"decision": verdict, "claims": list(claims), "scope": scope.strip(),
                              "rationale": rationale.strip(), "counterevidence": list(counterevidence),
                              "conditions": list(conditions), "revision": record["revision"],
                              "content_hash": record["content_hash"], "coverage": record["coverage"],
                              "decided_at": stamp()}
        record["status"] = verdict
        return store.put(record)


def switch(project: str | Path | None, source: str, enabled: bool) -> dict:
    """Enable or disable a source. Disabled, it is kept but neither searched nor fetched."""

    with records(project) as store:
        record = find(store, source)
        record["enabled"] = enabled
        return store.put(record)


def forget(project: str | Path | None, source: str) -> bool:
    """Remove a source record, the snapshots only it held, and its place in
    the graph with the cached extractions — all of which quote it.

    Two databases cannot commit as one, so the record goes first — it is
    the decision — and the graph after it. The graph step is idempotent and
    needs no record: when it fails, forgetting the full source id again
    finishes it. `keep` asks whether the record exists inside its own
    write transaction, so a model answer landing before the graph step is
    removed by it and one landing after finds the record gone. A store that
    could not be opened (in memory) forgets nothing.
    ponytail: a rebuild that loaded the record just before its deletion can
    write its structure — no extraction, so no quote — until the refresh the
    deletion triggers."""

    evidence, late = evidence_store(project), None
    try:
        if not evidence.persistent:
            raise OSError("the evidence store could not be opened; nothing was forgotten")
        with records(project) as store:
            try:
                source_id = find(store, source)["source_id"]
            except KeyError:
                # A record already gone whose graph step failed: its full id retries that step.
                if not re.fullmatch(r"[0-9a-f]{64}", source):
                    raise
                source_id, removed = source, False
            else:
                try:
                    removed = store.delete(source_id)
                except Exception as error:
                    # Failing after its commit (a snapshot) the record is gone all the same:
                    # the graph step still runs, and this is raised after it.
                    if store.get(source_id) is not None:
                        raise
                    removed, late = True, error
        try:
            with evidence.transaction() as db:
                cleaned = knowledge_graph.forget(db, source_id)
        except Exception as error:
            error.add_note(f"the record is gone; forget {source_id} again to finish")
            raise
        if late:
            raise late
        return removed or cleaned
    finally:
        evidence.close()


def still(project: str | Path | None, external: set[tuple[str, str]]):
    """`external`, the records' chunks as `(source, text sha)`, narrowed to
    the records that still exist when it is called — for `keep`."""

    def now() -> set[tuple[str, str]]:
        with records(project) as store:
            held = {r["source_id"] for r in store.all()}
        return {key for key in external if key[0] in held}

    return now


def catalog(project: str | Path | None) -> dict:
    """What `project` can be answered from: its local sources by kind, and
    its external records by family and status. For the status views of stage 9."""

    index = local_index(project)
    try:
        local = Counter(kind for _source, kind in {(c["source_id"], c["kind"]) for c in index.chunks
                                                   if not c.get("record")})
    finally:
        index.close()
    with records(project) as store:
        held = store.all()
    external: dict[str, Counter] = {}
    for record in held:
        family = next(f for f, kind in sources.FAMILIES.items() if kind == record["kind"])
        external.setdefault(family, Counter())[record["status"] if record["enabled"] else "disabled"] += 1
    return {"local": dict(local), "external": {f: dict(c) for f, c in external.items()},
            "records": [sources.brief(r) | {"enabled": r["enabled"], "error": r["error"]} for r in held]}


def page(record: dict) -> str:
    """The wiki page of an adopted source: what was taken from it, why, and
    how far it was read — a summary with its links, never its text."""

    adoption = record["adoption"]
    link = record["cite"] if record["cite"].startswith("https://") else record["origin"]
    read = {"abstract_only": "abstract only — the full text was not read",
            "partial": "part of the text — some pages had none to extract",
            "full_text": "full text", "metadata_only": "metadata only"}[adoption["coverage"]]
    rows = [("Origin", f"<{link}>" if link.startswith("https://") else f"`{link}`"),
            ("Authors", ", ".join(record["authors"]) or "unknown"),
            ("Edition read", f"`{adoption['revision']}`, content `{adoption['content_hash'][:12]}`, "
                             f"fetched {record['fetched_at']}"),
            ("Coverage", read), ("Authority", record["authority"]),
            ("License", record["license_note"] or "not recorded"), ("Decided", adoption["decided_at"])]
    parts = [f"# {record['title'] or record['origin']}\n",
             "Adopted research. The source is summarized here, not reproduced; read it at its origin.\n",
             "| Field | Value |\n| --- | --- |\n" + "".join(f"| {k} | {v} |\n" for k, v in rows),
             "## Adopted claims\n\n" + "".join(f"- {c}\n" for c in adoption["claims"]),
             f"## Scope\n\n{adoption['scope']}\n", f"## Rationale\n\n{adoption['rationale']}\n"]
    for title, items in (("Counterevidence", adoption["counterevidence"]),
                         ("Validation conditions", adoption["conditions"])):
        if items:
            parts.append(f"## {title}\n\n" + "".join(f"- {x}\n" for x in items))
    return "\n".join(parts)


def promote(project: str | Path, source: str, task: str | None = None) -> dict:
    """An adopted source's page, committed on a new branch in a new worktree
    beside `project` — a diff to review and open as a pull request. The
    original checkout is not touched."""

    repo = Path(project).resolve()
    with records(repo) as store:
        record = find(store, source)
    if record["status"] != "adopted":
        raise ValueError(f"{record['status']}: only an adopted source is promoted")
    slug = folder_for(record["title"] or "")[:48].rstrip("-") or record["source_id"][:12]
    task = task or f"research-{slug}"
    name = f"docs/research/{slug}.md"
    tree = create(repo, task)
    target = tree / name
    if target.exists():
        raise FileExistsError(f"{name} already exists in {tree}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page(record), encoding="utf-8", newline="\n")
    title = record["title"] or record["origin"]
    for args in (["add", "--", name], ["commit", "-q", "-m", f"docs: adopt research — {title}"]):
        done = subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
        if done.returncode:
            raise RuntimeError(done.stderr.strip() or f"git {args[0]} failed in {tree}")
    return {"worktree": str(tree), "branch": task, "file": name,
            "commit": git(tree, "rev-parse", "HEAD"), "stat": git(tree, "show", "--stat", "--format=", "HEAD")}


# ---- the knowledge graph (stage 4 of `docs/plans/jev/`) ----------------------------
#
# Structure the index builds on its own (`search.knowledge_graph.update`). What
# needs a model is here, and runs only when somebody asks: a generative model
# proposes each passage's entities and dependencies, code keeps what it finds
# verbatim in the passage, and Jev judges whether each dependency is
# supported — and, for bounded pairs of passages naming the same entity,
# whether they contradict each other. Results are cached per passage under
# the versions they were made with; the graph uses only those of the versions
# last run, and `retire_graph` stops using any.

GRAPH_PROMPT = "graph-extract.md"
# Passages per proposal request.
GRAPH_BATCH = 8
# Pairs per Jev request, and the most pairs one run compares.
PAIR_BATCH = 6
MAX_PAIRS = 24
# An entity more passages than this mention is too common to make two of them a pair.
FANOUT = 8
# The longest English passage Jev judges, as for retrieval (`search.controller.MAX_PASSAGE`).
MAX_PASSAGE = 3000
# One extraction run's allowance, shared by all its Jev requests. Design defaults, not measurements.
EXTRACTION = {"seconds": 900.0, "calls": 60, "candidates": 0}


def graph_versions(model: str, cfg: decision.Config) -> str:
    """What an extraction is reused under: the proposing model, the prompt,
    Jev's model and the support policy. English normalization is checked per
    judgment (`supported`)."""

    prompt = (Path(__file__).resolve().parents[1] / "prompts" / GRAPH_PROMPT).read_text(encoding="utf-8")
    return knowledge_graph.digest("graph", model or "default", prompt, cfg.model, knowledge_graph.POLICY)


def propose(passages: list[dict], model: str = "") -> tuple[dict[str, dict], str]:
    """The model's proposal per passage id, and the model that answered.
    `RuntimeError` or `ValueError` when there is none."""

    answer, used = "", model or "default"
    for ev in oneshot(GRAPH_PROMPT, {"passages": passages}, model):
        if ev.kind == "error":
            raise RuntimeError(ev.text)
        if ev.kind == "done":
            answer, used = ev.text, ev.meta.get("model") or used
    data = parsed(answer)
    items = data.get("passages") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("the proposal has no passages")
    return {str(p.get("id")): p for p in items if isinstance(p, dict)}, used


def english_of(chunks: list[dict], budget: Budget, project: str | Path | None) -> dict[str, dict]:
    """Each chunk's English outcome by chunk id; a private one's kept beside its source."""

    owners = [(c["source_id"],) if c.get("visibility") == "private" else () for c in chunks]
    outcomes = english([c["text"] for c in chunks], min(BATCH_SECONDS, budget.left()), owners, project)
    return {c["chunk_id"]: o for c, o in zip(chunks, outcomes)}


def readable(outcome: dict) -> bool:
    return outcome.get("status") in ("original_english", "translated") and len(outcome["text"]) <= MAX_PASSAGE


def supported(items: list[tuple[dict, dict]], cfg: decision.Config, budget: Budget, trace: list,
              project: str | Path | None) -> int:
    """Jev's support for each proposed dependency of `(chunk, result)`,
    written into the result: one not judged yet, or judged on other English
    than the passage and its quote have now.

    What is judged is the quote — the span the edge will cite — in English,
    normalized as any passage is; the passage is only its context. So another
    sentence of the passage can never stand behind a cited span that does not
    say it. Mode off, a failed request, or a passage or quote with no English
    leaves it `None` — a candidate, never a rejection. A verdict holds only
    for the English it was given on: once that has changed it is withdrawn
    first, so one that cannot be given again is not kept. Returns how many
    verdicts were written or withdrawn."""

    if cfg.mode == "off":
        return 0
    chunks = list({c["chunk_id"]: c for c, r in items if r["relations"]}.values())
    if not chunks:
        return 0
    # Each quote as a text of its passage's owner, so a private one stays private.
    quotes = {(c["chunk_id"], rel["quote"]): {**c, "chunk_id": knowledge_graph.digest("quote", c["chunk_id"], rel["quote"]),
                                              "text": rel["quote"]}
              for c, r in items for rel in r["relations"]}
    english_ = english_of(chunks + list(quotes.values()), budget, project)
    ids = {c["chunk_id"]: str(i) for i, c in enumerate(c for c in chunks if readable(english_[c["chunk_id"]]))}
    questions, where, claims = {}, {}, []
    withdrawn = 0
    for chunk, result in items:
        outcome = english_.get(chunk["chunk_id"], {})
        for relation in result["relations"]:
            quoted = english_.get(quotes[(chunk["chunk_id"], relation["quote"])]["chunk_id"], {})
            seen = "|".join(str(o.get("version") or o.get("status")) for o in (outcome, quoted))
            if relation["support"] is not None:
                if relation.get("english") == seen:
                    continue
                relation.update(support=None, english=None)
                withdrawn += 1
            if chunk["chunk_id"] not in ids or not readable(quoted):
                continue
            name = f"r{len(questions)}"
            claims.append({"id": name, "passage": ids[chunk["chunk_id"]], "cited_words": quoted["text"]})
            questions[name] = decision.noul(
                f"Do the cited_words of claim {name}, read in their passage, themselves state that "
                f"`{relation['from']}` depends on `{relation['to']}` — uses, requires, calls, imports or reads it, "
                "in that direction? Only the cited words count: another sentence of the passage saying so does "
                "not. Both being mentioned, or the reverse direction, is insufficient.")
            where[name] = (relation, seen)
    if not questions:
        return withdrawn
    state = {"passages": [{"id": ids[c["chunk_id"]], "heading": c["heading"], "text": english_[c["chunk_id"]]["text"]}
                          for c in chunks if c["chunk_id"] in ids], "claims": claims}
    try:
        got = decision.evaluate(cfg, state, questions, trace, budget, "graph_support")
    except Exception as error:  # noqa: BLE001 — no verdict is no verdict, never a rejection
        trace.append({"fallback": type(error).__name__, "reason": getattr(error, "category", "")})
        return withdrawn
    for name, value in got.items():
        relation, seen = where[name]
        relation.update(support=value, english=seen)
    return withdrawn + len(got)


def contradictions(index, repo: str, versions: str, cfg: decision.Config, budget: Budget, trace: list,
                   project: str | Path | None, external) -> dict:
    """Jev's comparison of bounded pairs: passages that name the same entity,
    one of them a decision or a memory, not compared under these versions
    yet — at most `MAX_PAIRS`. A pair Jev did not judge is not an edge."""

    if cfg.mode == "off":
        return {"pairs": 0, "judged": 0}
    have = knowledge_graph.cached(index.store, versions)
    chunks = {c["chunk_id"]: c for c in index.chunks}
    todo = []
    for a, b, subject in index.graph.pairs(repo, FANOUT):
        if a not in chunks or b not in chunks:
            continue
        ends = sorted([[c["source_id"], knowledge_graph.digest(c["text"])] for c in (chunks[a], chunks[b])])
        sha = knowledge_graph.digest("pair", *ends[0], *ends[1])
        if (ends[0][0], sha) not in have:
            todo.append((chunks[a], chunks[b], subject, ends, sha))
    todo = todo[:MAX_PAIRS]
    judged = 0
    for start in range(0, len(todo), PAIR_BATCH):
        group = todo[start:start + PAIR_BATCH]
        try:
            budget.check(budget.call_seconds)
        except Exception as error:  # noqa: BLE001 — out of time or cancelled: the rest waits for the next run
            trace.append({"stopped": type(error).__name__, "reason": str(error)})
            break
        english_ = english_of(list({c["chunk_id"]: c for g in group for c in g[:2]}.values()), budget, project)
        asked = [g for g in group if readable(english_[g[0]["chunk_id"]]) and readable(english_[g[1]["chunk_id"]])]
        if not asked:
            continue
        state = {"pairs": [{"id": str(i), "subject": subject, "a": english_[a["chunk_id"]]["text"],
                            "b": english_[b["chunk_id"]]["text"]} for i, (a, b, subject, _e, _s) in enumerate(asked)]}
        questions = {f"p{i}": decision.noul(
            f"In pair {i}, do passages a and b make claims about `{subject}` that cannot both be true at the same "
            "time? A difference of scope or version, or one passage only adding detail, is not a contradiction.")
            for i, (_a, _b, subject, _e, _s) in enumerate(asked)}
        try:
            got = decision.evaluate(cfg, state, questions, trace, budget, "graph_contradiction")
        except Exception as error:  # noqa: BLE001
            trace.append({"fallback": type(error).__name__, "reason": getattr(error, "category", "")})
            continue
        rows = []
        for i, (_a, _b, subject, ends, sha) in enumerate(asked):
            result = {"pair": ends, "subject": subject, "support": got[f"p{i}"]}
            rows += [(ends[0][0], sha, versions, result), (ends[1][0], sha, versions, result)]
        judged += len(asked)
        knowledge_graph.keep(index.store, rows, external)
    return {"pairs": len(todo), "judged": judged}


def extract_graph(project: str | Path | None, limit: int = 40, seconds: float = EXTRACTION["seconds"],
                  estimate: bool = False, cfg: decision.Config | None = None, model: str = "",
                  proposer=None) -> dict:
    """Propose, check and judge the entities and relations of up to `limit`
    of `project`'s passages not yet extracted under the current versions,
    then compare bounded pairs for contradictions. The hub's rules are the
    hub's to extract. `estimate` counts and sends nothing.

    The versions run become the ones the graph uses: until a passage is
    extracted under them, it has no semantic edges. Returns what was done and
    the graph's check (`knowledge_graph.verify`).
    """

    if limit < 1:
        raise ValueError("limit must be at least 1")
    cfg = cfg or decision.config()
    proposer = proposer or propose
    repo = knowledge_graph.evidence.repo_id(root_of(project))
    versions = graph_versions(model, cfg)
    index = local_index(project)
    try:
        store = index.store
        mine = {(c["source_id"], knowledge_graph.digest(c["text"])): c for c in index.chunks if c["repo_id"] == repo}
        have = knowledge_graph.cached(store, versions)
        pending = [c for key, c in mine.items() if key not in have]
        todo = pending[:limit]
        # Judged before, perhaps on English that has changed since, or not at all.
        again = [(mine[key], result) for key, result in have.items() if key in mine and result.get("relations")]
        report = {"versions": versions, "passages": len(mine), "extracted_before": len(mine) - len(pending),
                  "to_extract": len(todo), "jev": cfg.status()}
        if estimate:
            return {**report, "model_requests_at_most": -(-len(todo) // GRAPH_BATCH)}
        knowledge_graph.activate(store, versions)
        external = still(project, {key for key, c in mine.items() if c.get("record")})
        budget = Budget(seconds=seconds, calls=EXTRACTION["calls"], candidates=EXTRACTION["candidates"])
        trace: list[dict] = []
        rejected: Counter = Counter()
        counts: Counter = Counter()
        for start in range(0, len(todo), GRAPH_BATCH):
            batch = todo[start:start + GRAPH_BATCH]
            try:
                budget.check()
            except Exception as error:  # noqa: BLE001 — the rest waits for the next run
                trace.append({"stopped": type(error).__name__, "reason": str(error)})
                break
            try:
                proposals, used = proposer([{"id": str(i), "heading": c["heading"], "text": c["text"]}
                                            for i, c in enumerate(batch)], model)
            except (RuntimeError, ValueError, OSError) as error:
                # Nothing cached: the batch is proposed again next run.
                trace.append({"proposal_failed": type(error).__name__, "detail": str(error)[:200]})
                continue
            items = []
            for i, chunk in enumerate(batch):
                entities, relations, bad = knowledge_graph.validate(chunk["text"], proposals.get(str(i)) or {})
                rejected.update(b["reason"] for b in bad)
                counts.update(entities=len(entities), relations=len(relations))
                items.append((chunk, {"model": used, "entities": entities, "relations": relations, "rejected": bad}))
            counts["judged"] += supported(items, cfg, budget, trace, project)
            knowledge_graph.keep(store, [(c["source_id"], knowledge_graph.digest(c["text"]), versions, r)
                                         for c, r in items], external)
        # Every cached result is looked at, not only the first `limit`: a
        # verdict past them would otherwise never be withdrawn or given.
        # ponytail: each run starts from the first; if the budget ends first
        # every time, later ones wait — rotate a cursor if that shows up.
        for start in range(0, len(again), limit):
            batch = again[start:start + limit]
            try:
                budget.check()
            except Exception as error:  # noqa: BLE001 — the rest waits for the next run
                trace.append({"stopped": type(error).__name__, "reason": str(error)})
                break
            changed = supported(batch, cfg, budget, trace, project)
            counts["judged"] += changed
            if changed:
                knowledge_graph.keep(store, [(c["source_id"], knowledge_graph.digest(c["text"]), versions, r)
                                             for c, r in batch], external)
        index.refresh()
        pairs = contradictions(index, repo, versions, cfg, budget, trace, project, external)
        index.refresh()
        return {**report, "proposed": dict(counts), "rejected": dict(rejected), "contradictions": pairs,
                "trace": trace, "budget": budget.record(), "graph": knowledge_graph.verify(index.graph, index.chunks)}
    finally:
        index.close()


def retire_graph(project: str | Path | None) -> dict:
    """Stop using every extracted relationship. Structure and baseline
    retrieval stay; the cached extractions stay too, so running the same
    versions again brings them back without asking a model."""

    index = local_index(project)
    try:
        knowledge_graph.activate(index.store, None)
        index.refresh()
        return knowledge_graph.verify(index.graph, index.chunks)
    finally:
        index.close()


def check_graph(project: str | Path | None) -> dict:
    """The graph as the index has it now, checked (`knowledge_graph.verify`)."""

    index = local_index(project)
    try:
        return knowledge_graph.verify(index.graph, index.chunks)
    finally:
        index.close()


# ---- retrieval rounds (stage 5 of `docs/plans/jev/`) ------------------------------
#
# `search.retrieval` ranks, walks and repairs within one index; here the
# question gets its English, the settings decide the graph lane, and a repair
# that needs another pipeline — a model's subqueries, an arXiv search — is
# done before its round is sent. Every round of one question spends one
# `Budget`, and every request carries its deadline.

SUBQUERY_PROMPT = "retrieval-subqueries.md"
# How long one round waits for the daemon, as the controller does (`search.controller.SEARCH_TIMEOUT`).
ROUND_SECONDS = 3.0
# Papers one external repair asks arXiv for.
REPAIR_PAPERS = 3


def graph_enabled() -> bool:
    """The graph lane's switch: `WIKI_GRAPH_RETRIEVAL=off` in the hub's
    `.env` (or the environment) restores RRF alone. On by default."""

    try:
        found = settings.entries(decision.env_file())
    except (OSError, ValueError):
        found = {}
    return (settings.pick(found, "WIKI_GRAPH_RETRIEVAL")[0] or "on").strip().lower() != "off"


def available(root: Path | None) -> list[str]:
    """The sources a question in `root` may search: the local three, and each
    external family holding something enabled and read."""

    if root is None:
        return ["hub"]
    with records(root) as store:
        held = {r["kind"] for r in store.all() if r["enabled"] and r["status"] in sources.SEARCHABLE}
    return ["hub", "documents", "memory", *(f for f, kind in sources.FAMILIES.items() if kind in held)]


def retrieve(query: str, project: str | Path | None, k: int = 8, *, sources_: list[str] | None = None,
             graph: bool | None = None, budget: Budget | None = None, cfg: decision.Config | None = None) -> dict:
    """Round 1 for `query`: its RetrievalRequest and RetrievalResult, as
    `{"request", "result"}`, `result` `None` when nothing could be searched.

    The question's English goes in beside it when Jev is on — normalization
    sends it to the translator, which mode off never does. `graph` overrides
    the switch (`graph_enabled`).
    """

    cfg = cfg or decision.config()
    budget = budget or Budget(**QUESTION)
    root = Path(project).resolve() if project else None
    query_en = None
    if cfg.mode != "off":
        asked = english([query], min(ROUND_SECONDS, budget.left()), project=project)[0]
        query_en = asked["text"] if asked["status"] in ("original_english", "translated") else None
    on = graph_enabled() if graph is None else graph
    req = retrieval.request(knowledge_graph.evidence.repo_id(root or HUB), query, query_en=query_en,
                            sources=sources_ or available(root), limit=k, seconds=budget.left(),
                            graph=retrieval.GRAPH if on else None)
    return {"request": req, "result": run_round(req, project, budget)}


def run_round(req: dict, project: str | Path | None, budget: Budget) -> dict | None:
    """One request answered by the daemon, else by a cold index built here;
    `None` when there is no time left or the generation it names is gone."""

    if budget.cancel.is_set() or budget.left() <= 0:
        return None
    found = retrieve_from_daemon(req, project, min(ROUND_SECONDS, budget.left()))
    if found is not None:
        return found
    found = bounded(lambda: cold(req, project, budget.cancel), budget)
    return found if (found or {}).get("schema_version") == retrieval.RESULT else None


# Held by the one cold index build allowed at a time, as the controller's (`search.controller.COLD`).
COLD = threading.Lock()


def cold(req: dict, project: str | Path | None, cancel: threading.Event) -> dict | None:
    """A round answered by an index built in this process. A build already
    running is not joined by a second: this round then has no answer."""

    if not COLD.acquire(blocking=False):
        return None
    try:
        index = local_index(project)
        try:
            return retrieval.run(index.snapshot(), req, cancel)
        except retrieval.Stale:
            return None
        finally:
            index.close()
    finally:
        COLD.release()


def repair(req: dict, result: dict, need: str, project: str | Path | None, *, budget: Budget,
           cfg: decision.Config | None = None, chunk_ids: list[str] = (), entities: list[str] | None = None,
           external: bool = False, model: str = "") -> dict:
    """The next round for what `result` is missing (`retrieval.repair`),
    run: `{"note", "requests", "results"}`. Nothing is asked twice the same
    way; past three rounds `retrieval.Exhausted`, before anything is done.

    `subqueries` asks a model first, taking one request of the budget
    (`common.budget.Exhausted` when none is left); `external` searches
    arXiv first, only when the caller says the provider may be used, since
    that sends the question outside.
    """

    root = Path(project).resolve() if project else None
    # Before any work is spent on a round that cannot be run.
    if req["round"] >= retrieval.MAX_ROUNDS:
        raise retrieval.Exhausted("rounds")
    note: dict = {}
    proposals: list = []
    if need == "subqueries":
        # A model request, out of the question's one allowance: `Exhausted` when none is left.
        budget.call()
        proposals = subqueries(req["query_en"] or req["query_original"], budget, model)
        note["proposed"] = len(proposals)
    if need == "external":
        if not external:
            return {"note": {"need": need, "skipped": "external_not_allowed"}, "requests": [], "results": []}
        note["fetched"] = bounded(lambda: add_papers(project, req["query_en"] or req["query_original"],
                                                     n=REPAIR_PAPERS, cfg=cfg), budget)
    requests, made = retrieval.repair(req, result, need, sources=available(root), subqueries=proposals,
                                      chunk_ids=list(chunk_ids), entities=entities)
    return {"note": {**made, **note}, "requests": requests,
            "results": [run_round(r, project, budget) for r in requests]}


def bounded(work, budget: Budget):
    """`work()`'s value, or `None` once the budget is spent or cancelled.
    What is still running then is abandoned in its thread, as `search.prepare`
    abandons a slow run.

    ponytail: an abandoned model session or fetch runs to its own end; bound
    in-flight work per process if that ever piles up (stage 6).
    """

    if budget.cancel.is_set() or budget.left() <= 0:
        return None
    got: list = []

    def call() -> None:
        try:
            got.append(work())
        except Exception as error:  # noqa: BLE001 — a failed repair is no repair, and says why
            got.append({"error": type(error).__name__})

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    while worker.is_alive() and budget.left() > 0 and not budget.cancel.is_set():
        worker.join(min(0.05, budget.left()))
    return got[0] if got else None


def subqueries(question: str, budget: Budget, model: str = "") -> list:
    """Up to three scoped subqueries a model proposes for a question with
    several requirements, unchecked — `retrieval.checked_subqueries` decides
    which stand. `[]` when none came within the budget."""

    def ask() -> list:
        answer = ""
        for ev in oneshot(SUBQUERY_PROMPT, {"question": question}, model):
            if ev.kind == "error":
                raise RuntimeError(ev.text)
            if ev.kind == "done":
                answer = ev.text
        data = parsed(answer)
        return data.get("subqueries") if isinstance(data, dict) else []

    got = bounded(ask, budget)
    return got if isinstance(got, list) else []


def parsed(answer: str):
    """A model's JSON answer, a code fence around it allowed."""

    body = answer.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, re.S)
    return json.loads(fenced.group(1) if fenced else body)
