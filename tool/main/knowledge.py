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
from common.budget import QUESTION, Budget
from common.language import language
from search import HUB, evidence_store, local_index, providers, records, resolve, sources
from search import prepare as controlled
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
    than before is a new edition: the old snapshot stays, citable, with the
    decision that was made about it."""

    sha = store.keep(data)
    probe = {**record, "content_hash": sha, "form": form, "cite": cite, "coverage": coverage}
    if not sources.cut(probe, data.decode("utf-8", errors="replace"), store.snapshot(sha)):
        raise providers.FetchError("no_text")
    if record["content_hash"] and record["content_hash"] != sha:
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
    under another spelling is the same record."""

    root = root_of(project)
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
    """Remove a source record and the snapshots only it held."""

    with records(project) as store:
        return store.delete(find(store, source)["source_id"])


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
