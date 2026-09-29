"""sources — where evidence comes from: the local catalog, and the records of
what was fetched from outside. Stage 3 of `docs/plans/jev/`.

Local. `listing` is every file the index reads: the hub's rules, the
repository's Markdown as git sees it, and — named explicitly, because a
repository may git-ignore them — its decisions, module pages and saved
memories. Never a memory's raw transcript.

External. A `SourceRecord` per paper or web document, in `Records`: SQLite
beside the repository's evidence store, and the content actually read as
content-addressed snapshots next to it. A record says where it came from, the
edition and bytes that were read, how much was read (`coverage`), its status
and, once someone decided, why it was adopted or rejected. A re-fetch that
reads different content makes a new edition; the old snapshot stays, so a
citation of it still resolves. Only the enabled records whose content was read
are searchable, rejected ones included — a later search finds why an
alternative was turned down.

`authority` is where a source stands, not whether it is right.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from common.language import language
from common.process import background_options

from . import cache_dir, evidence

SCHEMA_VERSION = 1
KINDS = ("research", "paper")
AUTHORITIES = ("official", "paper", "repository", "memory", "community")
STATUSES = ("discovered", "fetched", "indexed", "adopted", "rejected", "unavailable")
COVERAGES = ("full_text", "abstract_only", "metadata_only", "partial")
# How a snapshot is cut and cited: `text` by character span of a URL
# snapshot, `pages` by PDF page and block, `file` by line, as a local file is.
FORMS = ("text", "pages", "file")
# Content was read and cut: these are searchable while enabled.
SEARCHABLE = ("indexed", "adopted", "rejected")
# The routing families external records add, and the kind each holds.
FAMILIES = {"papers": "paper", "research": "research"}
SOURCE_NAMES = ("hub", "documents", "memory", *FAMILIES)

# Who a hub document is written for (reliability PR 3). Relevance, not access:
# `repo_id` and `visibility` decide what a request may see. The first prefix
# that matches wins. The shared rules answer every audience. A hub path not
# named here, and every other repository's document, is unclassified: eligible
# under any audience, and counted as such (`retrieval.run`).
AUDIENCES = ("product", "hooks", "jev")
HUB_AUDIENCES = (
    (("operator/", "craft/"), AUDIENCES),
    (("docs/jev-maintenance.md", "docs/plans/jev/", "docs/research/jev-"), ("jev", "product")),
    (("docs/hooks-setup.md", "docs/chat-setup.md"), ("hooks",)),
    (("docs/", ".wiki/"), ("product",)),
)


def audiences(display: str) -> list[str] | None:
    """The audiences of the hub file at `display` (relative, `/`-separated); `None` for unclassified."""

    return next((list(names) for prefixes, names in HUB_AUDIENCES if display.startswith(prefixes)), None)


def listing(hub: Path, project: Path | None) -> list[Path]:
    """The hub's rules, every Markdown file the repository keeps as git sees
    it, so `node_modules` and the like never come in, and the knowledge
    folders git may ignore."""

    files = [p for scope in ("operator", "craft") for p in sorted((hub / scope).glob("*.md"))]
    if project is None:
        return files
    try:
        out = subprocess.run(
            ["git", "-C", str(project), "ls-files", "-co", "--exclude-standard", "-z", "--", "*.md"],
            capture_output=True, timeout=30, check=True, **background_options()).stdout.decode("utf-8", errors="replace")
        mine = [project / name for name in out.split("\0") if name]
    except (OSError, subprocess.SubprocessError):
        # Sorted as git's list is, so equal scores rank the same on every machine.
        mine = sorted(p for p in project.rglob("*.md")
                      if not any(part.startswith(".") or part == "node_modules"
                                 for part in p.relative_to(project).parts[:-1]))
    # Decisions (`harvest`), module pages (`main.survey`) and the memories a
    # cleared conversation left (`main.memory`): often git-ignored, so the
    # listing above can miss them. Not the memories' transcripts.
    for folder in ("decisions", "modules", "memory"):
        mine += [p for p in sorted((project / ".wiki" / folder).glob("*.md")) if not p.name.endswith(".raw.md")]
    seen = {p.resolve() for p in files}
    return files + [p for p in dict.fromkeys(mine) if p.resolve() not in seen]


def canonical_url(url: str) -> str:
    """One spelling per document: scheme and host in lower case, no default
    port, no fragment. Two URLs that differ only so are one source."""

    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    if port and (parts.scheme.lower(), port) not in (("https", 443), ("http", 80)):
        host = f"{host}:{port}"
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, ""))


def new(root: Path, kind: str, origin: str, **fields) -> dict:
    """A record of the repository at `root`, every field present; what is not known is `None`."""

    repo = evidence.repo_id(root)
    record = {"schema_version": SCHEMA_VERSION, "source_id": evidence.source_id(repo, origin), "repo_id": repo,
              "kind": kind, "origin": origin, "title": None, "authors": [], "published_at": None,
              "fetched_at": None, "revision": None, "content_hash": None,
              "authority": "paper" if kind == "paper" else "community", "status": "discovered",
              "coverage": "metadata_only", "adoption": None, "visibility": "repository", "license_note": None,
              "enabled": True, "form": None, "cite": None, "editions": [], "error": None, "relevance": None}
    record.update(fields)
    return record


def problems(record: dict) -> list[str]:
    """Everything that makes `record` not a SourceRecord. Empty means valid."""

    found = []
    if record.get("schema_version") != SCHEMA_VERSION:
        found.append("schema_version")
    for name, allowed in (("kind", KINDS), ("authority", AUTHORITIES), ("status", STATUSES),
                          ("coverage", COVERAGES), ("visibility", evidence.VISIBILITY)):
        if record.get(name) not in allowed:
            found.append(f"{name} {record.get(name)!r}")
    for name in ("source_id", "repo_id"):
        if not evidence.ID.match(str(record.get(name) or "")):
            found.append(f"{name} is not a sha256")
    if not isinstance(record.get("origin"), str) or not record["origin"]:
        found.append("origin")
    if type(record.get("enabled")) is not bool:
        found.append("enabled")
    read = record.get("status") in SEARCHABLE
    if read and not (evidence.ID.match(str(record.get("content_hash") or ""))
                     and record.get("form") in FORMS and record.get("cite")):
        found.append("read content without a snapshot, form and citation")
    if not read and record.get("status") != "fetched" and record.get("content_hash") is not None:
        found.append(f"{record.get('status')} with content")
    if record.get("status") == "unavailable" and not record.get("error"):
        found.append("unavailable without its error")
    adoption = record.get("adoption")
    decided = record.get("status") in ("adopted", "rejected")
    if decided != isinstance(adoption, dict) or (decided and adoption.get("decision") != record["status"]):
        found.append("adoption does not match status")
    return found


def records_folder(root: Path) -> Path:
    """`<cache>/knowledge/<repo_id>/`, beside the evidence store of the same repository."""

    return cache_dir() / "knowledge" / evidence.repo_id(root)


RECORDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS records (source_id TEXT PRIMARY KEY, record TEXT NOT NULL);
"""


class Records:
    """One repository's external source records and their snapshots.

    The four questions of `craft/client-lifecycle-in-one-scope`:

    - Creation. Whoever reads or writes them: the repository's `Index`, and
      `main.knowledge` for an ingestion or a decision.
    - Sharing. Not shared: one connection each, taken in turns under a lock.
      Processes share the file; SQLite serializes their writes.
    - Closing. `close()`, or the `with` block, by whoever made it — `Index.close`
      for the index's.
    - Ownership. The user's cache, per repository. A folder that cannot be
      opened is an in-memory store: nothing persists and nothing is found.
    """

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.lock = threading.Lock()
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self.db = self.connect(str(self.folder / "sources.sqlite3"))
        except (OSError, sqlite3.Error) as error:
            sys.stderr.write(f"source records in memory: {type(error).__name__}\n")
            self.db = self.connect(":memory:")
        self.cut: dict[str, list[dict]] = {}

    @staticmethod
    def connect(where: str) -> sqlite3.Connection:
        db = sqlite3.connect(where, timeout=5.0, check_same_thread=False, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(RECORDS_SCHEMA)
        return db

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def __enter__(self) -> Records:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")

    def version(self) -> str:
        """Changes whenever any process changes a record."""

        with self.lock:
            row = self.db.execute("SELECT v FROM meta WHERE k = 'version'").fetchone()
        return row[0] if row else "0"

    def get(self, source: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT record FROM records WHERE source_id = ?", (source,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT record FROM records ORDER BY source_id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def put(self, record: dict) -> dict:
        """Write `record`, replacing its source's. `ValueError` for one that is not valid."""

        wrong = problems(record)
        if wrong:
            raise ValueError(f"not a SourceRecord: {'; '.join(wrong)}")
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO records VALUES (?, ?)",
                       (record["source_id"], json.dumps(record, ensure_ascii=False)))
            self.bump(db)
        return record

    def delete(self, source: str) -> bool:
        """Remove a record and every snapshot only it held."""

        record = self.get(source)
        if record is None:
            return False
        with self.transaction() as db:
            db.execute("DELETE FROM records WHERE source_id = ?", (source,))
            self.bump(db)
            held = {h for (text,) in db.execute("SELECT record FROM records")
                    for h in hashes(json.loads(text))}
        for sha in set(hashes(record)) - held:
            self.snapshot(sha).unlink(missing_ok=True)
        return True

    @staticmethod
    def bump(db: sqlite3.Connection) -> None:
        row = db.execute("SELECT v FROM meta WHERE k = 'version'").fetchone()
        db.execute("INSERT OR REPLACE INTO meta VALUES ('version', ?)", (str(int(row[0] if row else 0) + 1),))

    def snapshot(self, sha: str) -> Path:
        return self.folder / "snapshots" / f"{sha}.txt"

    def keep(self, data: bytes) -> str:
        """Keep content as read, under its own hash; never overwritten, so a
        citation of it resolves for as long as its record holds it."""

        sha = hashlib.sha256(data).hexdigest()
        path = self.snapshot(sha)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_suffix(f".{os.getpid()}.part")
            part.write_bytes(data)
            part.replace(path)
        return sha

    def hits(self) -> list[dict]:
        """The enabled, read records cut into search hits. A snapshot that is
        missing or no longer its hash is skipped, not cited."""

        out = []
        for record in self.all():
            if not record["enabled"] or record["status"] not in SEARCHABLE:
                continue
            # Everything a hit carries of its record: two records holding the
            # same bytes are two sources, each cut with its own identity.
            key = json.dumps({**brief(record), "form": record["form"], "cite": record["cite"],
                              "content_hash": record["content_hash"], "adoption": record["adoption"]},
                             sort_keys=True)
            if key not in self.cut:
                path = self.snapshot(record["content_hash"])
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                if hashlib.sha256(data).hexdigest() != record["content_hash"]:
                    continue
                text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
                self.cut[key] = cut(record, text, path)
            out += self.cut[key]
        return out


def hashes(record: dict) -> list[str]:
    """Every snapshot a record holds: its current content and its earlier editions'."""

    return [h for h in [record.get("content_hash"), *(e.get("content_hash") for e in record.get("editions") or [])]
            if h]


def brief(record: dict) -> dict:
    """What a hit carries of its record: enough to know how far to trust it."""

    adoption = record.get("adoption") or {}
    return {"source_id": record["source_id"], "origin": record["origin"], "title": record["title"],
            "authority": record["authority"], "status": record["status"], "coverage": record["coverage"],
            "revision": record["revision"], "fetched_at": record["fetched_at"],
            "decision": adoption.get("decision"), "rationale": adoption.get("rationale")}


def text_of(text: str) -> str:
    """Text as a snapshot of form `text` keeps it: every line break a `\\n`,
    so a character span and a line agree."""

    return "\n".join(text.splitlines())


def pieces(record: dict, text: str, path: Path) -> list[tuple[dict, dict, tuple[int, int]]]:
    """`(daemon chunk, locator, (start, end) for the chunk id)` for a snapshot."""

    from .daemon import chunks

    out = []
    if record["form"] == "pages":
        for page, body in enumerate(text.split("\f"), 1):
            for block, c in enumerate(chunks(body, path)):
                out.append((c, {"document": record["cite"], "page": page, "block": block}, (page, block)))
        return out
    lines = text.splitlines()
    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    for c in chunks(text, path):
        if record["form"] == "file":
            out.append((c, {"path": record["cite"], "start_line": c["line"], "end_line": c["end_line"]},
                        (c["line"], c["end_line"])))
        else:
            start, end = starts[c["line"] - 1], starts[c["end_line"] - 1] + len(lines[c["end_line"] - 1])
            out.append((c, {"url": record["cite"], "snapshot": record["content_hash"], "start": start, "end": end},
                        (start, end)))
    return out


def cut(record: dict, text: str, path: Path) -> list[dict]:
    """A record's snapshot as search hits, in the shape `daemon.Store.load` gives."""

    title = record["title"] or record["origin"]
    adoption = record.get("adoption") or {}
    # A decision's reason is found by what it says, not only by the source's text.
    decided = f"\n{adoption['decision']}: {adoption['rationale']}" if adoption else ""
    out = []
    for c, locator, (a, b) in pieces(record, text, path):
        heading_path = [title if c["heading_path"][0] == path.stem else c["heading_path"][0], *c["heading_path"][1:]]
        if "page" in locator:
            heading_path.append(f"page {locator['page']}")
        heading = " > ".join(heading_path)
        line = locator.get("page", c["line"])
        out.append({"path": str(path), "line": line, "end_line": locator.get("page", c["end_line"]),
                    "heading": heading, "heading_path": heading_path, "text": c["text"],
                    "indexed": heading + "\n" + c["text"] + decided, "completeness": c["completeness"],
                    "chunk_id": evidence.chunk_id(record["source_id"], record["content_hash"], a, b),
                    "source_id": record["source_id"], "repo_id": record["repo_id"],
                    "revision": record["content_hash"], "kind": record["kind"], "visibility": record["visibility"],
                    "language": language(c["text"]), "locator": locator, "coverage": record["coverage"],
                    "record": brief(record)})
    return out


def resolve(chunk: dict, path: Path) -> str | None:
    """The original span a URL-snapshot or PDF chunk cites, read again from
    its snapshot — `None` when the snapshot is gone or not that revision."""

    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if hashlib.sha256(data).hexdigest() != chunk["revision"]:
        return None
    text = data.decode("utf-8", errors="replace")
    locator = chunk["locator"]
    if "url" in locator:
        return text[locator["start"]:locator["end"]]
    found = [c for c, where, _span in pieces({"form": "pages", "cite": locator["document"]}, text, Path(path))
             if (where["page"], where["block"]) == (locator["page"], locator["block"])]
    return found[0]["text"] if found else None
