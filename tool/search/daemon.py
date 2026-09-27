"""The search daemon. One per machine, started by `search.spawn`.

Chunks every page at its `##`/`###` headings, and a long section at its
blocks, into `evidence.EvidenceChunk`s kept in a SQLite `Store` beside the
vector cache. It ranks them two ways — BM25
over English words and Hangul bigrams, and cosine over a local
`multilingual-e5-small` — merged by reciprocal rank. It serves the wiki chat,
and keeps idle Claude cells' prompt caches warm (`Keeper`). No hook asks it
what to inject: the regex triggers stay the only authority there (in the
public copy a one-line hint for pages they missed was built and measured, and
no threshold reached 8% precision).

The four questions of `craft/client-lifecycle-in-one-scope`:

- Creation. `search.spawn`, when a call found no daemon. Binding the fixed
  port is the lock: a second one fails to bind and exits.
- Sharing. One process on the machine. A request names its hub and its
  repository, and each pair gets its own index; the model is loaded once.
- Closing. Three hours after the last request it ends itself, or at the last
  keep-alive session's expiry if that is later. `/quit` only with the token.
  The state file is removed on the way out.
- Ownership. The user's. `~/.cache/wiki-agent/searchd.json` holds `port`,
  `token`, `pid`, `version`. A daemon that died leaving the file is found by
  the next call failing to connect, and a new one is started.

Without `onnxruntime`, `tokenizers` and `numpy` it answers with BM25 alone,
and it does the same while the model downloads (about 120 MB, first start).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import queue
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from collections import Counter, defaultdict
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Run as a script by `spawn`: `tool/` goes on the path so the package imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.language import language  # noqa: E402
from search import PING, PORT, cache_dir, evidence, knowledge_graph, proof, state_path, version  # noqa: E402
from search.sources import FAMILIES, SOURCE_NAMES, Records, listing, records_folder  # noqa: E402

# The version this process runs, read once. Read per request it would follow
# the files on disk and a pulled daemon would never be told it is stale.
RUNNING = version()
IDLE = 3 * 3600
RRF_K = 60

MODEL = "intfloat/multilingual-e5-small"
FILES = {"model.onnx": "onnx/model_qint8_avx512_vnni.onnx", "tokenizer.json": "onnx/tokenizer.json"}
# In every vector's cache key, so another model or file never reads these.
MODEL_ID = f"{MODEL}/{FILES['model.onnx']}"

# An ATX heading of level 1 to 3, as CommonMark reads one: up to three spaces
# of indent, and a closing run of `#` only after a space — `## C#` keeps its `#`.
HEADING = re.compile(r"^ {0,3}(#{1,3})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
# A code fence's marker and what follows it, as CommonMark reads one.
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
WORD = re.compile(r"[a-z0-9_]+|[가-힣]+")


def terms(text: str) -> list[str]:
    """English words in lower case, Hangul as overlapping bigrams.

    Both, because the hub's pages are English while a repository's are often
    Korean, and so is the question. Bigrams catch a Korean stem through its
    endings without a morphological analyser.
    """

    out = []
    for word in WORD.findall(text.lower()):
        if "가" <= word[0] <= "힣":
            out += [word[i:i + 2] for i in range(len(word) - 1)] or [word]
        else:
            out.append(word)
    return out


def body_of(text: str) -> str:
    """The page without its front matter — the slice `wiki.front_matter` takes,
    without parsing the YAML this index never reads."""

    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    return text if end < 0 else text[end + 4:].lstrip("\n")


def fenced(lines: list[str]) -> list[bool]:
    """Per line, whether it belongs to a code fence, its markers included.

    As CommonMark reads one: a backtick opener whose info string holds a
    backtick is inline code, not a fence; a fence closes only on its opener's
    own character, at least as long, with nothing after it — a `~~~` inside a
    backtick fence is text. Found in review rounds 1 and 2.
    """

    out, fence = [], ""
    for line in lines:
        marker = FENCE.match(line)
        if marker and not fence:
            if not (marker.group(1)[0] == "`" and "`" in marker.group(2)):
                fence = marker.group(1)
            out.append(bool(fence))
        elif marker and fence and marker.group(1).startswith(fence) and not marker.group(2).strip():
            fence = ""
            out.append(True)
        else:
            out.append(bool(fence))
    return out


# A chunk's ceiling, Hangul counted twice: a Korean passage's English runs
# about twice as long, and both must fit e5's 512 tokens and Jev's passage.
MAX_CHUNK = 1500
LIST_ITEM = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])[ \t]")


def size(text: str) -> int:
    return len(text) + sum("가" <= c <= "힣" for c in text)


def blocks(lines: list[tuple[int, str, bool]]) -> list[list[tuple[int, str, bool]]]:
    """A section's lines in blocks: paragraphs, tables and lists split at blank
    lines, a code fence whole with its blank lines, and a loose list's items
    and indented continuations kept with the list."""

    found: list[list] = []
    current: list = []
    for item in lines:
        _number, line, hidden = item
        if not line.strip() and not hidden:
            if current:
                found.append(current)
                current = []
            continue
        opens = not current and found and not hidden and (
            line.startswith(("  ", "\t")) or LIST_ITEM.match(line)) and LIST_ITEM.match(found[-1][0][1])
        if opens:
            current = found.pop()
        # A fence is a block of its own, even with no blank line around it.
        elif current and current[-1][2] != hidden:
            found.append(current)
            current = []
        current.append(item)
    if current:
        found.append(current)
    return found


def pieces(section: list[tuple[int, str, bool]]) -> list[tuple[list, str]]:
    """A section in chunks of whole blocks up to `MAX_CHUNK`, as
    `(lines, completeness)`. A block over the ceiling is cut at its lines and
    each part says `partial`; a fence, which cannot be cut, stays whole as
    `oversized` — a target to read in full rather than to judge."""

    out: list[tuple[list, str]] = []
    buf: list = []

    def text(lines: list) -> str:
        return "\n".join(line for _n, line, _h in lines)

    for block in blocks(section):
        joined = buf + ([(0, "", False)] if buf else []) + block
        if size(text(joined)) <= MAX_CHUNK:
            buf = joined
            continue
        if buf:
            out.append((buf, "whole"))
            buf = []
        if size(text(block)) <= MAX_CHUNK:
            buf = block
        elif block[0][2] or len(block) == 1:
            out.append((block, "oversized"))
        else:
            part: list = []
            for line in block:
                if part and size(text(part + [line])) > MAX_CHUNK:
                    out.append((part, "partial"))
                    part = []
                part.append(line)
            out.append((part, "partial"))
    if buf:
        out.append((buf, "whole"))
    # Blank lines between blocks were dropped; a chunk spans its first to last line.
    return [([item for item in lines if item[0]], kind) for lines, kind in out]


def chunks(text: str, path: Path) -> list[dict]:
    """A page cut at `##` and `###`, and a long section at its blocks. Each
    chunk knows its first and last line.

    The page title and the heading path go in front of what is indexed — the
    cheap form of contextual retrieval, no model call — and are not part of
    the chunk's text, which is exactly its lines of the original. A heading
    inside a code fence is not a heading.
    """

    body = body_of(text)
    offset = text[: len(text) - len(body)].count("\n")
    lines = body.splitlines()
    inside = fenced(lines)
    # The first `#` outside a fence; a `# Fake` in a code sample is not the title.
    title = next((m.group(2) for line, hidden in zip(lines, inside)
                  if not hidden and (m := HEADING.match(line)) and len(m.group(1)) == 1), path.stem)
    found, trail, buf = [], [], []

    def flush() -> None:
        # A heading with nothing under it before the next one is not a chunk.
        if not "".join(line for _n, line, _h in (buf[1:] if trail else buf)).strip():
            return
        heading_path = [title, *trail]
        heading = " > ".join(heading_path)
        for part, completeness in pieces(buf):
            start, end = part[0][0], part[-1][0]
            original = "\n".join(lines[start - offset - 1:end - offset])
            found.append({"path": str(path), "line": start, "end_line": end, "heading": heading,
                          "heading_path": heading_path, "completeness": completeness,
                          "text": original, "indexed": heading + "\n" + original})

    for number, (line, hidden) in enumerate(zip(lines, inside), offset + 1):
        match = None if hidden else HEADING.match(line)
        if match and len(match.group(1)) >= 2:
            flush()
            trail = trail[: len(match.group(1)) - 2] + [match.group(2)]
            buf = [(number, line, hidden)]
            continue
        buf.append((number, line, hidden))
    flush()
    return found


class Embedder:
    """The model, loaded once, and a worker that fills the vector cache.

    Off for good when the libraries are missing or the download fails; the
    daemon then ranks with BM25 alone. Only the worker thread touches SQLite.

    A private memory's vectors stay in memory, never in `vectors.sqlite3`:
    they are made again after a restart, and one deleted while still queued
    is dropped when its batch is done (`drop`), so nothing on disk outlives
    the memory.
    """

    def __init__(self, root: Path | None):
        self.root = root
        self.state = "off" if root is None else "loading"
        self.vectors: dict[str, object] = {}
        self.pending: set[str] = set()
        # Chunks the model could not embed this run. Not retried until restart.
        self.failed: set[str] = set()
        # Keys of private chunks: embedded, never written to the cache.
        self.private: set[str] = set()
        # Guards `vectors` and `pending` between the worker and `drop`.
        self.lock = threading.Lock()
        self.jobs: queue.Queue = queue.Queue()

    def start(self) -> None:
        if self.root is not None:
            threading.Thread(target=self.run, daemon=True).start()

    def run(self) -> None:
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer

            folder = self.root / "models/e5"
            for name, remote in FILES.items():
                if not (folder / name).exists():
                    folder.mkdir(parents=True, exist_ok=True)
                    part = folder / (name + ".part")
                    urllib.request.urlretrieve(f"https://huggingface.co/{MODEL}/resolve/main/{remote}", part)
                    part.replace(folder / name)
            self.np = np
            self.tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
            self.tokenizer.enable_truncation(512)
            options = ort.SessionOptions()
            # Two threads: this runs beside the session it serves.
            options.intra_op_num_threads = 2
            # Off, the resident size stays near 440 MB instead of growing with
            # every batch shape the arena keeps.
            options.enable_cpu_mem_arena = False
            self.session = ort.InferenceSession(str(folder / "model.onnx"), options,
                                                providers=["CPUExecutionProvider"])
            db = sqlite3.connect(self.root / "vectors.sqlite3")
            db.execute("CREATE TABLE IF NOT EXISTS v (k TEXT PRIMARY KEY, v BLOB)")
            for key, blob in db.execute("SELECT k, v FROM v"):
                self.vectors[key] = np.frombuffer(blob, dtype=np.float32)
            self.state = "ready"
        except Exception as error:  # noqa: BLE001
            self.state = "off"
            print(f"vectors off: {type(error).__name__}", file=sys.stderr)
            return
        while True:
            batch = [self.jobs.get()]
            while len(batch) < 8 and not self.jobs.empty():
                batch.append(self.jobs.get())
            self.store(batch, db)

    def store(self, batch: list[tuple[str, str]], db) -> None:
        """Embed one batch and keep it. Never raises; every key leaves `pending`.

        A chunk that fails to embed has no vector. It goes to `failed`, stays
        out of the vector ranking and keeps its BM25 rank; the next start tries
        it again. Left in `pending` it was never queued again and the index
        never completed; given a zero vector it outranked every negative
        cosine (both found in the public copy's review).

        A cache that cannot be written — a full disk — keeps the vectors in
        memory. The worker must not die on it: with no worker every later
        chunk stays pending too.
        """

        keys = [key for key, _text in batch]
        try:
            done = self.encode([text for _key, text in batch], "passage: ")
            with self.lock:
                # One no longer pending was dropped while queued: a deleted memory's.
                keys = [key for key in keys if key in self.pending]
                for key, vector in zip([key for key, _text in batch], done):
                    if key in keys:
                        self.vectors[key] = vector
        except Exception as error:  # noqa: BLE001
            print(f"embedding skipped: {type(error).__name__}", file=sys.stderr)
            self.failed.update(keys)
            return
        finally:
            with self.lock:
                self.pending.difference_update(key for key, _text in batch)
        try:
            db.executemany("INSERT OR REPLACE INTO v VALUES (?, ?)",
                           [(key, self.vectors[key].tobytes()) for key in keys
                            if key not in self.private and key in self.vectors])
            db.commit()
        except Exception as error:  # noqa: BLE001
            print(f"vector cache not written: {type(error).__name__}", file=sys.stderr)

    def encode(self, texts: list[str], prefix: str):
        """e5's convention: `passage: ` and `query: `, mean pooling, unit length."""

        np = self.np
        encoded = self.tokenizer.encode_batch([prefix + t for t in texts])
        width = max(len(e.ids) for e in encoded)
        ids = np.array([e.ids + [1] * (width - len(e.ids)) for e in encoded], dtype=np.int64)
        mask = np.array([e.attention_mask + [0] * (width - len(e.ids)) for e in encoded], dtype=np.int64)
        hidden = self.session.run(None, {"input_ids": ids, "attention_mask": mask,
                                         "token_type_ids": np.zeros_like(ids)})[0]
        pooled = (hidden * mask[..., None]).sum(1) / mask.sum(1, keepdims=True)
        return (pooled / np.linalg.norm(pooled, axis=1, keepdims=True)).astype(np.float32)

    def want(self, items: list[tuple[str, str]], private: set[str] = frozenset()) -> None:
        """Queue these `(key, text)` for embedding; the keys in `private` are
        never written to the cache."""

        if self.state == "off":
            return
        self.private |= private
        for key, text in items:
            if key not in self.vectors and key not in self.pending and key not in self.failed:
                self.pending.add(key)
                self.jobs.put((key, text))

    def drop(self, keys: list[str]) -> None:
        """Forget these vectors, made or still queued."""

        with self.lock:
            for key in keys:
                self.vectors.pop(key, None)
                self.pending.discard(key)
                self.private.discard(key)


def key_of(text: str) -> str:
    return hashlib.sha256((MODEL_ID + "\0" + text).encode("utf-8")).hexdigest()


def drop_vectors(keys: list[str]) -> bool:
    """Remove cached vectors — a deleted private memory's. `False` when the
    cache could not be written; the journal then keeps the keys for next time.

    ponytail: a daemon that already holds one in memory keeps it, unreferenced,
    until it restarts; and a chunk still queued for embedding is written back.
    """

    path = cache_dir() / "vectors.sqlite3"
    if not keys or not path.exists():
        return True
    try:
        db = sqlite3.connect(path, timeout=5.0)
        try:
            db.execute("PRAGMA secure_delete=ON")
            db.executemany("DELETE FROM v WHERE k = ?", [(k,) for k in keys])
            db.commit()
        finally:
            db.close()
        return True
    except sqlite3.Error:
        return False


STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS generations (gen INTEGER PRIMARY KEY, chunker TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sources (gen INTEGER, source_id TEXT, repo_id TEXT, display TEXT, canonical TEXT,
    kind TEXT, visibility TEXT, revision TEXT, stamp INTEGER, size INTEGER, PRIMARY KEY (gen, source_id));
CREATE TABLE IF NOT EXISTS chunks (gen INTEGER, chunk_id TEXT, source_id TEXT, start_line INTEGER,
    end_line INTEGER, heading_path TEXT, text TEXT, completeness TEXT, PRIMARY KEY (gen, chunk_id));
CREATE INDEX IF NOT EXISTS chunks_source ON chunks (gen, source_id);
CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY AUTOINCREMENT, vector_key TEXT);
CREATE TABLE IF NOT EXISTS english (source_id TEXT, text_sha TEXT, outcome TEXT, PRIMARY KEY (source_id, text_sha));
"""


def store_path(hub: Path, project: Path | None) -> Path:
    """`<cache>/knowledge/<repo_id>/index-<hub>.sqlite3`: per repository, and
    per hub, since each hub brings its own rules."""

    repo = evidence.repo_id(project or hub)
    return cache_dir() / "knowledge" / repo / f"index-{evidence.repo_id(hub)[:16]}.sqlite3"


class Store:
    """One index's sources and chunks, in SQLite beside the vector cache.

    The four questions of `craft/client-lifecycle-in-one-scope`:

    - Creation. Its `Index`, in whichever thread builds that index.
    - Sharing. Not shared: one connection per store, taken in turns under the
      store's lock, because the daemon answers each request on its own thread.
    - Closing. `Index.close()`, by whoever made a short-lived index — the
      controller's cold build, the command lines. The daemon's indexes live as
      long as it does. One never closed is closed by the collector with its
      index; nothing else holds the connection.
    - Ownership. The user's cache. A file that cannot be opened becomes an
      in-memory store: retrieval goes on and nothing persists.

    Generations. The chunk rows of one `evidence.CHUNKER` are one generation.
    Code with another chunker builds its own beside the current one and
    publishes it, by moving `meta.current`, in the same transaction that
    completes it — no listed file left out. Until then it reads the published
    one (`reading`). Publishing prunes to the new generation and the one it
    replaced, which is kept, so going back to the older code selects it again
    (and brings it up to date) instead of rebuilding; nothing else prunes.
    Within a generation an update is one transaction: a failure rolls back to
    what was there. The knowledge graph (`knowledge_graph`) keeps its tables
    here too, per generation, so it is published, kept and pruned with its chunks.

    Private English. A private source's English is kept here, in `english`,
    not in the translator's cache: kept only for a text the source has at
    that moment, and gone with the source, or any edit of it, in the same
    transaction. Its vectors are never written to disk (`Embedder`).

    Deletion. A source that goes loses its rows in every generation; a
    private one edited here loses them in every generation holding its old
    content (`remove`). A private one's vector keys go to `journal` in the
    same transaction, for the
    daemon's memory and for what older code wrote to the vector cache;
    `Index.refresh` clears them, and a crash first leaves the journal to
    finish the job.
    """

    def __init__(self, path: Path | None):
        self.lock = threading.Lock()
        try:
            if path is None:
                raise OSError("no path")
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db = self.connect(str(path))
            self.persistent = True
        except (OSError, sqlite3.Error) as error:
            if path is not None:
                print(f"evidence store in memory: {type(error).__name__}", file=sys.stderr)
            self.db = self.connect(":memory:")
            self.persistent = False
        self.gen = self.generation()

    @staticmethod
    def connect(where: str) -> sqlite3.Connection:
        db = sqlite3.connect(where, timeout=5.0, check_same_thread=False, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        # A deleted memory's text is overwritten, not left in free pages.
        db.execute("PRAGMA secure_delete=ON")
        db.executescript(STORE_SCHEMA + knowledge_graph.SCHEMA)
        return db

    def close(self) -> None:
        with self.lock:
            self.db.close()

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

    def meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
        return row[0] if row else None

    def generation(self) -> int:
        """The generation this code reads: the current one when its chunker is
        this code's, else a kept one that is, else a new, empty one — which
        becomes current only when `sync` has filled it."""

        with self.transaction() as db:
            current = self.meta("current")
            gens = dict(db.execute("SELECT gen, chunker FROM generations"))
            if current is not None and gens.get(int(current)) == evidence.CHUNKER:
                return int(current)
            mine = [g for g, chunker in gens.items() if chunker == evidence.CHUNKER]
            if mine:
                return max(mine)
            gen = max(gens, default=0) + 1
            db.execute("INSERT INTO generations VALUES (?, ?, ?)", (gen, evidence.CHUNKER, time.time()))
            return gen

    def reading(self) -> int:
        """The generation read (`load`, `source_of`): this code's, unless it
        is still being built while another is published — then that one,
        whole, until this one is complete. Called under the lock."""

        current = self.meta("current")
        return self.gen if current is None or int(current) == self.gen else int(current)

    def version(self) -> str:
        """Changes whenever any process changes this store, or the generation read."""

        with self.lock:
            return f"{self.reading()}:{self.meta('version') or 0}"

    def sync(self, listed: list[tuple[Path, Path, Path, bool]], attempts: int = 3) -> bool:
        """Bring the generation in line with `listed` and publish it. Each file
        comes as `(path, root, resolved, shared)`. `True` when anything changed.

        A file is read again only when its size or modification time moved,
        and re-cut only when its bytes did. A file that cannot be read counts
        as gone. One that changed while it was read is read again, up to
        `attempts` passes; a generation not yet published is published only
        once no file was left out that way.
        """

        changed = False
        for _ in range(attempts):
            now, skipped = self.pass_(listed)
            changed |= now
            if not skipped:
                break
        return changed

    def pass_(self, listed: list[tuple[Path, Path, Path, bool]]) -> tuple[bool, bool]:
        """One pass of `sync`: whether anything changed, and whether a file
        was left out because it changed while it was read."""

        with self.lock:
            known = {row[0]: row[1:] for row in self.db.execute(
                "SELECT canonical, source_id, stamp, size, revision, visibility FROM sources WHERE gen = ?",
                (self.gen,))}
            # What other generations hold, which this one may never have
            # indexed: a file's deletion reaches every generation, the
            # published one this process may be reading included.
            elsewhere = self.db.execute(
                "SELECT gen, canonical, source_id, revision, visibility FROM sources WHERE gen != ?",
                (self.gen,)).fetchall()
        seen: set[str] = set()
        touched, fresh = [], []
        repos: dict[Path, str] = {}
        for path, root, resolved, shared in listed:
            canonical = os.path.normcase(str(resolved))
            if canonical in seen:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            old = known.get(canonical)
            seen.add(canonical)
            if old and (old[1], old[2]) == (stat.st_mtime_ns, stat.st_size):
                continue
            try:
                data = path.read_bytes()
                text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            except (OSError, UnicodeDecodeError):
                seen.discard(canonical)
                continue
            revision = hashlib.sha256(data).hexdigest()
            if old and old[3] == revision:
                touched.append((stat.st_mtime_ns, stat.st_size, self.gen, old[0]))
                continue
            repo = repos.get(root) or repos.setdefault(root, evidence.repo_id(root))
            display = resolved.relative_to(root.resolve()).as_posix()
            kind = evidence.kind_of(display, shared)
            source = evidence.source_id(repo, display)
            cut = [(evidence.chunk_id(source, revision, c["line"], c["end_line"]), c) for c in chunks(text, path)]
            fresh.append(((self.gen, source, repo, display, canonical, kind, evidence.visibility_of(kind),
                           revision, stat.st_mtime_ns, stat.st_size), cut, old[0] if old else None, path))
        gone = [(self.gen, row[0], row[4], row[3]) for canonical, row in known.items() if canonical not in seen]
        gone += [(gen, source, visibility, revision) for gen, canonical, source, revision, visibility in elsewhere
                 if canonical not in seen]
        private = changed = skipped = False
        # Everything above ran outside the write lock, so another process may
        # have synced since. Under it, a file is written only if it is still
        # what was read, and a row removed only if it is still the one seen:
        # a stale read must not bring back a memory another process deleted.
        with self.transaction() as db:
            db.executemany("UPDATE sources SET stamp = ?, size = ? WHERE gen = ? AND source_id = ?", touched)
            for record, cut, previous, path in fresh:
                try:
                    now = path.stat()
                except OSError:
                    skipped = True
                    continue
                if (now.st_mtime_ns, now.st_size) != (record[8], record[9]):
                    skipped = True
                    continue
                changed = True
                source, visibility = record[1], record[6]
                keep = {key_of(c["indexed"]) for _id, c in cut}
                private |= self.remove(db, {source, previous} - {None}, visibility == "private", keep,
                                       revision=record[7])
                db.execute("INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", record)
                db.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
                    (self.gen, cid, source, c["line"], c["end_line"], json.dumps(c["heading_path"], ensure_ascii=False),
                     c["text"], c["completeness"]) for cid, c in cut])
            for gen, source, visibility, revision in gone:
                row = db.execute("SELECT revision FROM sources WHERE gen = ? AND source_id = ?",
                                 (gen, source)).fetchone()
                if row is None or row[0] != revision:
                    continue
                changed = True
                private |= self.remove(db, {source}, visibility == "private", set(), gen)
            current = self.meta("current")
            # Another process pruning generations may have taken this one's row.
            db.execute("INSERT OR IGNORE INTO generations VALUES (?, ?, ?)", (self.gen, evidence.CHUNKER, time.time()))
            # A generation not yet published stays unpublished while a file is
            # missing from it: the one it would replace still has that file.
            # Only publishing prunes: to this generation and the one it
            # replaces, kept for rollback. A build that has not finished
            # deletes nothing.
            publish = current != str(self.gen) and not skipped
            if publish:
                kept = {self.gen} | ({int(current)} if current is not None else set())
                for table in ("generations", "sources", "chunks", *knowledge_graph.TABLES):
                    db.execute(f"DELETE FROM {table} WHERE gen NOT IN ({','.join('?' * len(kept))})", list(kept))
                db.execute("INSERT OR REPLACE INTO meta VALUES ('current', ?)", (str(self.gen),))
            if changed or publish:
                db.execute("INSERT OR REPLACE INTO meta VALUES ('version', ?)",
                           (str(int(self.meta("version") or 0) + 1),))
        if private:
            # Deleted pages can linger in the write-ahead log until a checkpoint.
            with self.lock:
                self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return changed, skipped

    def remove(self, db: sqlite3.Connection, sources: set[str], private: bool, keep: set[str],
               gen: int | None = None, revision: str | None = None) -> bool:
        """Delete these sources' rows in `gen` (this one by default), where
        `revision` is the content replacing them, `None` when the file is gone.

        A private source's rows go in every generation that holds other
        content than `revision` — a memory deleted or edited here must not
        come back with another generation — while a generation holding the
        same revision keeps them: that is the same memory, still readable
        there. With any of its rows goes its English, and each vector key of
        those rows that `keep` (the new version's) does not name is
        journalled: the key is the heading path with the text, so a title
        edit alone drops it too. `True` for a private source.

        ponytail: a key another generation still uses for the new content is
        dropped too and embedded again; subtract the kept rows' keys if that
        re-embedding ever costs.
        """

        gen = self.gen if gen is None else gen
        marks = ",".join("?" * len(sources))
        rows = db.execute(f"SELECT gen, source_id, revision FROM sources WHERE source_id IN ({marks})",
                          [*sources]).fetchall()
        doomed = [(g, s) for g, s, r in rows if g == gen or (private and r != revision)]
        if private and doomed:
            journal = {key_of(" > ".join(json.loads(heading)) + "\n" + text) for g, s in doomed
                       for heading, text in db.execute(
                           "SELECT heading_path, text FROM chunks WHERE gen = ? AND source_id = ?", (g, s))} - keep
            db.executemany("INSERT INTO journal (vector_key) VALUES (?)", [(key,) for key in journal])
            db.execute(f"DELETE FROM english WHERE source_id IN ({marks})", [*sources])
        for table in ("chunks", "sources"):
            db.executemany(f"DELETE FROM {table} WHERE gen = ? AND source_id = ?", doomed)
        # Its place in the graph goes in the same transaction: nothing derived outlives it.
        knowledge_graph.drop(db, doomed, private, revision is None)
        return private

    def load(self, roots: dict[str, Path]) -> list[dict]:
        """The chunks of the generation read as search hits, each path under the root
        its repository has in `roots` (repo id -> root), so every asker sees
        paths spelled as it spelled its own."""

        with self.lock:
            rows = self.db.execute(
                "SELECT s.repo_id, s.source_id, s.revision, s.display, s.kind, s.visibility, c.chunk_id,"
                " c.start_line, c.end_line, c.heading_path, c.text, c.completeness FROM chunks c JOIN sources s"
                " ON s.gen = c.gen AND s.source_id = c.source_id WHERE c.gen = ?", (self.reading(),)).fetchall()
        out = []
        for repo, source, revision, display, kind, visibility, cid, start, end, heading, text, completeness in rows:
            root = roots.get(repo)
            if root is None:
                continue
            heading_path = json.loads(heading)
            out.append({"path": str(root / display), "line": start, "end_line": end,
                        "heading": " > ".join(heading_path), "heading_path": heading_path, "text": text,
                        "indexed": " > ".join(heading_path) + "\n" + text, "completeness": completeness,
                        "chunk_id": cid, "source_id": source, "repo_id": repo, "revision": revision,
                        "kind": kind, "visibility": visibility, "language": language(text),
                        "locator": {"path": display, "start_line": start, "end_line": end},
                        "coverage": "full_text", "record": None})
        return out

    def english(self, source: str, texts: list[str]) -> dict[str, dict]:
        """The English kept for these texts of a private source: `{text: outcome}`."""

        shas = {evidence.digest(text): text for text in texts}
        with self.lock:
            rows = self.db.execute("SELECT text_sha, outcome FROM english WHERE source_id = ?", (source,)).fetchall()
        return {shas[sha]: json.loads(outcome) for sha, outcome in rows if sha in shas}

    def keep_english(self, source: str, outcomes: list[tuple[str, dict]]) -> int:
        """Keep a private source's English beside it, for each text — a chunk
        or a heading title — the source still has: a translation that lands
        after an edit or a deletion does not bring back what was removed.
        Returns how many were kept."""

        with self.transaction() as db:
            present = set()
            for heading, text in db.execute("SELECT heading_path, text FROM chunks WHERE source_id = ?", (source,)):
                present |= {text, " > ".join(json.loads(heading))}
            rows = [(source, evidence.digest(text), json.dumps(outcome, ensure_ascii=False))
                    for text, outcome in outcomes if text in present]
            db.executemany("INSERT OR REPLACE INTO english VALUES (?, ?, ?)", rows)
        return len(rows)

    def journal(self) -> list[tuple[int, str]]:
        """Pending clean-ups: the vector keys of deleted private chunks."""

        with self.lock:
            return self.db.execute("SELECT id, vector_key FROM journal WHERE vector_key IS NOT NULL").fetchall()

    def cleared(self, ids: list[int]) -> None:
        with self.transaction() as db:
            db.executemany("DELETE FROM journal WHERE id = ?", [(i,) for i in ids])
            # Rows older code journalled with a text and no key.
            db.execute("DELETE FROM journal WHERE vector_key IS NULL")

    def source_of(self, chunk: str) -> str | None:
        """The canonical path a chunk of this generation was cut from, for
        checking a citation against the file itself (`evidence.resolve`)."""

        with self.lock:
            row = self.db.execute("SELECT s.canonical FROM chunks c JOIN sources s ON s.gen = c.gen AND"
                                  " s.source_id = c.source_id WHERE c.gen = ? AND c.chunk_id = ?",
                                  (self.reading(), chunk)).fetchone()
        return row[0] if row else None


# What a hit carries besides its path, line, heading, text and scores:
# everything `evidence.contract` needs to make it an EvidenceChunk, how much
# of its source was read, and an external source's record (`sources.brief`).
EVIDENCE = ("end_line", "chunk_id", "source_id", "repo_id", "revision", "kind", "visibility",
            "locator", "heading_path", "completeness", "language", "coverage", "record")


class Index:
    """One repository's index, beside one hub, kept in its `Store`, with the
    repository's external sources from its `Records`. Re-cut only the files
    whose bytes changed; re-embed only the chunks whose text changed. One
    search reads one loaded set of chunks, so one answer never mixes two
    states of the store."""

    def __init__(self, hub: Path, project: Path | None, embedder: Embedder):
        self.hub, self.project, self.embedder = hub, project, embedder
        self.store = Store(store_path(hub, project))
        self.records = Records(records_folder(project or hub))
        self.loaded: str | None = None
        # Every file the loaded chunks came from, and its revision.
        self.files: dict[Path, str] = {}
        self.chunks: list[dict] = []
        self.matrix = None
        self.graph = knowledge_graph.Graph(self.store)
        self.co: tuple[str, list[tuple[str, str]]] = ("", [])

    def close(self) -> None:
        self.store.close()
        self.records.close()

    def refresh(self) -> None:
        listed = []
        hub_root = self.hub.resolve()
        for path in listing(self.hub, self.project):
            shared = path.parent.parent == self.hub and path.parent.name in ("operator", "craft")
            root = self.hub if shared else self.project
            try:
                resolved = path.resolve()
            except OSError:
                continue
            # A link that leads out of its repository is not that repository's evidence.
            if resolved.is_relative_to(hub_root if shared else root.resolve()):
                listed.append((path, root, resolved, shared))
        try:
            self.store.sync(listed)
        except sqlite3.Error as error:
            # What was loaded stays; the next refresh tries again.
            print(f"evidence store not updated: {type(error).__name__}", file=sys.stderr)
        self.forget()
        version = f"{self.store.version()}/{self.records.version()}"
        if self.chunks and version == self.loaded:
            self.link()
            return
        self.loaded = version
        roots = {evidence.repo_id(self.hub): self.hub}
        if self.project is not None:
            roots.setdefault(evidence.repo_id(self.project), self.project)
        order = {str(path): i for i, (path, _r, _resolved, _s) in enumerate(listed)}
        found = self.store.load(roots)
        # Listing order, as git lists, so equal scores rank the same on every machine.
        found.sort(key=lambda c: (order.get(c["path"], len(order)), c["path"], c["line"]))
        self.chunks = found + self.records.hits()
        self.files = {Path(c["path"]): c["revision"] for c in found}
        for chunk in self.chunks:
            chunk["key"] = key_of(chunk["indexed"])
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths = []
        for i, chunk in enumerate(self.chunks):
            counts = Counter(terms(chunk["indexed"]))
            self.lengths.append(sum(counts.values()))
            for term, n in counts.items():
                self.postings[term].append((i, n))
        self.average = sum(self.lengths) / max(1, len(self.lengths))
        self.matrix = None
        self.embedder.want([(c["key"], c["indexed"]) for c in self.chunks],
                           {c["key"] for c in self.chunks if c["visibility"] == "private"})
        self.link()

    def link(self) -> None:
        """Bring the knowledge graph in line with the loaded chunks. A no-op
        unless they, the extractions in use or the hub's `graph.json` changed.
        A failure leaves the graph as it was, and search goes on without it."""

        store, records = self.loaded.split("/")
        stamp, pairs = self.observed()
        try:
            knowledge_graph.update(self.store, self.chunks, store, pairs, f"{records}|{stamp}")
        except sqlite3.Error as error:
            print(f"knowledge graph not updated: {type(error).__name__}", file=sys.stderr)

    def observed(self) -> tuple[str, list[tuple[str, str]]]:
        """The hub rules `graph.json` saw injected together, read again only when it changed."""

        path = self.hub / "graph.json"
        try:
            stamp = str(path.stat().st_mtime_ns)
        except OSError:
            return "", []
        if self.co[0] != stamp:
            try:
                links = json.loads(path.read_text(encoding="utf-8")).get("links") or []
            except (OSError, ValueError, AttributeError):
                links = []
            self.co = (stamp, [(f"{x['a']}.md", f"{x['b']}.md") for x in links
                               if isinstance(x, dict) and x.get("kind") == "co" and (x.get("by") or {}).get("all")])
        return self.co

    def complete(self) -> bool:
        """Every chunk has been tried: it has a vector, or it failed."""

        done = self.embedder.vectors.keys() | self.embedder.failed
        return self.embedder.state == "ready" and all(c["key"] in done for c in self.chunks)

    def bm25(self, query: str) -> dict[int, float]:
        k1, b, total = 1.5, 0.75, len(self.chunks)
        scores: dict[int, float] = defaultdict(float)
        for term in set(terms(query)):
            posting = self.postings.get(term, ())
            if not posting:
                continue
            idf = math.log(1 + (total - len(posting) + 0.5) / (len(posting) + 0.5))
            for i, n in posting:
                norm = n + k1 * (1 - b + b * self.lengths[i] / self.average)
                scores[i] += idf * n * (k1 + 1) / norm
        return scores

    def search(self, query: str, k: int, sources: list[str] | None = None) -> list[dict]:
        """Pages, best first. A page scores as its best chunk.

        `rrf` merges the two rankings; `cos` is the best chunk's cosine, or
        `None` while the vectors are incomplete — then the ranking is BM25
        alone, since a vector ranking over part of the index would favour
        whatever happened to be embedded first. Once complete, the vector
        ranking covers the chunks that have a vector; one that failed to
        embed keeps only its BM25 rank, and a page with no vector at all has
        `cos` `None`.
        """

        if sources is not None and (not isinstance(sources, list) or
                                    any(s not in SOURCE_NAMES for s in sources)):
            raise ValueError("Unknown retrieval source")
        lexical = self.bm25(query)
        fused: dict[int, float] = defaultdict(float)
        for rank, i in enumerate(sorted(lexical, key=lexical.get, reverse=True)):
            fused[i] += 1 / (RRF_K + rank + 1)
        cosine: dict[int, float] = {}
        if self.chunks and self.complete():
            np = self.embedder.np
            if self.matrix is None:
                rows = [i for i, c in enumerate(self.chunks) if c["key"] in self.embedder.vectors]
                self.matrix = (rows, np.stack([self.embedder.vectors[self.chunks[i]["key"]] for i in rows])
                               if rows else None)
            rows, matrix = self.matrix
            if rows:
                scores = matrix @ self.embedder.encode([query], "query: ")[0]
                cosine = {i: float(s) for i, s in zip(rows, scores)}
                for rank, i in enumerate(sorted(cosine, key=cosine.get, reverse=True)):
                    fused[i] += 1 / (RRF_K + rank + 1)
        # A page is its source, not its path: two external sources can hold
        # the same bytes, and so the same snapshot file.
        best: dict[str, tuple[float, int]] = {}
        for i, score in fused.items():
            page = self.chunks[i]["source_id"]
            if sources is not None and source(self.chunks[i]) not in sources:
                continue
            if page not in best or score > best[page][0]:
                best[page] = (score, i)
        pages = []
        for page, (score, i) in sorted(best.items(), key=lambda x: -x[1][0])[:k]:
            chunk = self.chunks[i]
            mine = [s for j, s in cosine.items() if self.chunks[j]["source_id"] == page]
            top = max(mine) if mine else None
            pages.append({"path": chunk["path"], "line": chunk["line"], "heading": chunk["heading"],
                          "text": chunk["text"], "rrf": round(score, 5),
                          "cos": None if top is None else round(top, 4),
                          "bm25": round(lexical.get(i, 0.0), 3), **{f: chunk[f] for f in EVIDENCE}})
        return pages

    def forget(self) -> None:
        """Clear the vectors of what the journal says was deleted: in memory,
        in the queue, and on disk, where older code wrote private ones."""

        pending = self.store.journal()
        if not pending:
            return
        self.embedder.drop([key for _id, key in pending])
        if drop_vectors([key for _id, key in pending]):
            self.store.cleared([i for i, _key in pending])


def source(chunk: dict) -> str:
    """The retrieval source a chunk belongs to, as Jev routes them. The
    repository's own research notes are its documents; `research` is what was
    ingested from outside."""

    if chunk.get("record"):
        return next(family for family, kind in FAMILIES.items() if kind == chunk["kind"])
    return {"rule": "hub", "memory": "memory"}.get(chunk["kind"], "documents")


# ---- keep-alive -------------------------------------------------------------
#
# A Claude session left idle for an hour loses its prompt cache and writes its
# whole context again when the person comes back. One short turn inside the
# hour keeps it. The cell's hooks say what the cell is doing (`keepalive.py`,
# `inject.py`); this only writes it down, and never guesses from Orca's output
# times or a transcript — the public copy's review broke three such guesses.

PING_EVERY = 55 * 60
PING_REPLY = 10 * 60
TICK = 30


def orca(*args: str) -> dict | None:
    """One `orca` CLI call, its `result`, or `None` for anything else.

    The one place the daemon touches Orca, so a test swaps it for a fake.
    Decoded as UTF-8: the screen holds `❯` and box lines, and the console's
    own code page mangles them.
    """

    exe = shutil.which("orca")
    if exe is None:
        return None
    try:
        # Short: `send` runs under the keeper's lock, and notices wait on it.
        done = subprocess.run([exe, *args, "--json"], capture_output=True, timeout=5)
        answer = json.loads(done.stdout.decode("utf-8", errors="replace"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return answer.get("result") if isinstance(answer, dict) and answer.get("ok") else None


RULE = re.compile(r"─{20,}")


def input_empty(lines: list[str]) -> bool:
    """Is Claude Code's input box, at the bottom of the screen, empty?

    Its shape in an Orca cell, read on 2026-09-25 (Claude Code 2.1.282,
    Orca 1.4.167) — a lone `❯` between two rules, a status footer below:

        ────────────────────────────
        ❯
        ────────────────────────────
          ⏵⏵ bypass permissions on (shift+tab to cycle)

    A half-written message has text after `❯`; a cell that fell back to its
    shell has a prompt below the box. Anything that is not this shape is not
    empty, so an unknown screen sends nothing.
    """

    lines = [line.rstrip() for line in lines]
    while lines and not lines[-1].strip():
        lines.pop()
    rules = [i for i, line in enumerate(lines) if RULE.fullmatch(line.strip())]
    if len(rules) < 2 or rules[-2] != rules[-1] - 2:
        return False
    footer = lines[rules[-1] + 1:]
    return (lines[rules[-1] - 1].strip() == "❯" and len(footer) <= 3
            and all(line.startswith(" ") for line in footer))


def same_path(a: str, b: str) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except (OSError, ValueError):
        return False


class Keeper:
    """Per session `{handle, checkout, state, due, count, limit, expires, sent}`;
    per cell, its one owner. Times are `clock()` seconds.

    The owner is whoever sent the latest notice from that cell. A hook runs
    inside the session that holds the cell, so a notice is itself the proof;
    a session pushed out — `/clear` — can send nothing more. That also brings
    the owners back after a restart, from the next notice.

    The count is never brought back. A session first seen here, however, is
    at its cap (`None`), and only a person's utterance — `/busy` with
    `reset` — puts it to zero. `SessionStart` is no evidence of a new session:
    `resume` and `compact` arrive for one that was already pinged. Wrong, this
    pings less; it never pings past the cap.
    """

    def __init__(self, clock=time.time, call=None):
        self.clock = clock
        self.call = call or orca
        self.sessions: dict[str, dict] = {}
        self.owners: dict[str, str] = {}
        self.lock = threading.Lock()

    def drop(self, session: str) -> None:
        mine = self.sessions.pop(session, None)
        if mine and self.owners.get(mine["handle"]) == session:
            del self.owners[mine["handle"]]

    def claim(self, session: str, handle: str) -> dict:
        mine = self.sessions.get(session)
        if mine is None:
            mine = self.sessions[session] = {"handle": handle, "checkout": None, "state": None,
                                             "due": None, "count": None, "limit": 0,
                                             "expires": None, "sent": None}
        if mine["handle"] != handle and self.owners.get(mine["handle"]) == session:
            del self.owners[mine["handle"]]
        mine["handle"] = handle
        other = self.owners.get(handle)
        if other not in (None, session):
            self.drop(other)
        self.owners[handle] = session
        return mine

    def notice(self, kind: str, data: dict) -> bool:
        """`own`, `ping-turn`, `busy`, `idle` or `gone`, from the cell's hooks.

        `True` only for a `ping-turn` answering a ping this daemon sent. The
        hook knows the ping by its words, and a person can type the same
        words; one that arrives with no ping out is that person, and counts as
        their `busy` (review round 2). Except in a session this daemon holds
        no count for — first seen since a restart, perhaps by its `own`: that
        may be a ping sent before the restart, and a reset there could ping
        past the cap.
        """

        session, handle = str(data["session"]), str(data["handle"])
        with self.lock:
            if kind == "gone":
                self.drop(session)
                return False
            if kind not in ("own", "ping-turn", "busy", "idle"):
                raise ValueError(kind)
            known = self.sessions.get(session) or {}
            # `ping-turn` too: the hook retries a notice whose answer was
            # late, and the retry finds the first one's state. No other turn
            # can come between — a `Stop` lies between any two (round 3).
            if kind == "ping-turn" and known.get("state") not in ("sent", "ping-turn"):
                kind, data = "busy", data | {"reset": known.get("count") is not None}
            pinged = kind == "ping-turn"
            mine = self.claim(session, handle)
            # Every notice but `idle` means a turn is starting or running.
            # Its timer goes, and so does its expiry: a turn may run for hours,
            # and the count must still be here at its `Stop`.
            mine.update(state=kind, due=None, expires=None, sent=None)
            if kind == "busy" and data.get("reset"):
                mine["count"] = 0
            if kind == "idle":
                now = self.clock()
                mine["checkout"], mine["limit"] = str(data["checkout"]), int(data["limit"])
                mine["expires"] = now + PING_EVERY * mine["limit"] + PING_REPLY
                if mine["count"] is not None and mine["count"] < mine["limit"]:
                    mine["due"] = now + PING_EVERY
        return pinged

    def latest(self) -> float:
        """The last expiry among the sessions. The idle shutdown waits for it."""

        with self.lock:
            return max((s["expires"] for s in self.sessions.values() if s["expires"]), default=0.0)

    def tick(self) -> None:
        """Run every `TICK` seconds: drop what expired, ping what is due."""

        now = self.clock()
        due = []
        with self.lock:
            for session, mine in list(self.sessions.items()):
                if (mine["expires"] is not None and now >= mine["expires"]) or (
                        mine["state"] == "sent" and now >= mine["sent"] + PING_REPLY):
                    self.drop(session)
                elif mine["due"] is not None and now >= mine["due"]:
                    due.append((session, dict(mine)))
        for session, seen in due:
            self.ping(session, seen)

    def ready(self, handle: str, checkout: str) -> bool:
        """The cell is live, is still the repository `Stop` named, and its input box is empty."""

        shown = self.call("terminal", "show", "--terminal", handle)
        cell = (shown or {}).get("terminal") or {}
        if not (cell.get("connected") and cell.get("writable")
                and same_path(str(cell.get("worktreePath") or ""), checkout)):
            return False
        screen = ((self.call("terminal", "read", "--terminal", handle, "--limit", "20") or {})
                  .get("terminal") or {})
        return input_empty(list(screen.get("tail") or []))

    def armed(self, session: str, seen: dict) -> bool:
        """Still idle on the timer `tick` saw — no notice came in since."""

        mine = self.sessions.get(session)
        return (mine is not None and mine["state"] == "idle" and mine["due"] == seen["due"]
                and self.owners.get(mine["handle"]) == session)

    def ping(self, session: str, seen: dict) -> None:
        """Look again before typing. Anything off, and the session is dropped.

        The look — `show` and `read`, about 0.25 s each — runs outside the
        lock. The last check of the session and the send run inside it, back
        to back, so a notice that arrives during the send waits for it rather
        than slipping between the check and the keystrokes.

        What is left cannot be closed from here: Orca has no check-and-type.
        A person who presses Enter between the screen read and the keystrokes
        landing — about half a second — gets the ping in their turn. So does
        a turn whose `/busy` was lost even after `keepalive.RETRY`.
        """

        with self.lock:
            if not self.armed(session, seen):
                return
        ok = self.ready(seen["handle"], seen["checkout"])
        with self.lock:
            if not self.armed(session, seen):
                return
            if not ok or self.call("terminal", "send", "--terminal", seen["handle"],
                                   "--text", PING, "--enter") is None:
                self.drop(session)
                return
            mine = self.sessions[session]
            mine.update(state="sent", due=None, sent=self.clock(), count=mine["count"] + 1)


class Daemon:
    def __init__(self, token: str, embedder: Embedder):
        self.token, self.embedder = token, embedder
        self.keeper = Keeper()
        self.indexes: dict[tuple[Path, Path | None], Index] = {}
        # ponytail: one lock for every index; per-index locks if requests ever queue
        self.lock = threading.Lock()
        self.last = time.monotonic()

    def finished(self) -> bool:
        """Idle for `IDLE`. A pending keep-alive holds it open, but no longer
        than the last session's expiry — nothing can be due after that."""

        return time.monotonic() - self.last >= IDLE and self.keeper.latest() <= self.keeper.clock()

    def search(self, query: str, hub: str, project: str | None, k: int, wait: float,
               sources: list[str] | None = None) -> list[dict]:
        """The hub is the asker's, not this process's: two checkouts at the
        same version share one daemon, and each must search its own rules."""

        home = Path(hub).resolve()
        root = Path(project).resolve() if project else None
        with self.lock:
            index = self.indexes.setdefault((home, root), Index(home, root, self.embedder))
            index.refresh()
        until = time.monotonic() + wait
        while not index.complete() and self.embedder.state != "off" and time.monotonic() < until:
            time.sleep(0.2)
        with self.lock:
            return index.search(query, k, sources)


def handler(daemon: Daemon, server_ref: list) -> type:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:
            pass

        def reply(self, status: int, data: dict) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def allowed(self) -> bool:
            return hmac.compare_digest(self.headers.get("X-Wiki-Token", ""), daemon.token)

        def do_GET(self) -> None:
            daemon.last = time.monotonic()
            if self.path != "/health":
                return self.reply(404, {})
            nonce = self.headers.get("X-Wiki-Nonce", "")
            self.reply(200, {"version": RUNNING, "proof": proof(daemon.token, nonce),
                             "vectors": daemon.embedder.state})

        def do_POST(self) -> None:
            daemon.last = time.monotonic()
            data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not self.allowed():
                return self.reply(403, {})
            if self.path == "/quit":
                self.reply(200, {})
                threading.Thread(target=server_ref[0].shutdown, daemon=True).start()
                return None
            if self.path in ("/own", "/ping-turn", "/busy", "/idle", "/gone"):
                try:
                    pinged = daemon.keeper.notice(self.path[1:], json.loads(data))
                except Exception as error:  # noqa: BLE001
                    return self.reply(400, {"error": type(error).__name__})
                return self.reply(200, {"ping": pinged})
            if self.path != "/search":
                return self.reply(404, {})
            try:
                ask = json.loads(data)
                results = daemon.search(str(ask["query"]), str(ask["hub"]), ask.get("project"),
                                        int(ask.get("k") or 8), float(ask.get("wait") or 0), ask.get("sources"))
            except Exception as error:  # noqa: BLE001
                return self.reply(400, {"error": type(error).__name__})
            return self.reply(200, {"results": results})

    return Handler


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second socket bind a port already bound,
    # and binding is this daemon's only lock.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


def serve(port: int, daemon: Daemon, tries: int = 1) -> Server | None:
    """Bind, or `None` when another holds the port. A few tries, because a
    daemon replaced for its version is still letting go of the port."""

    ref: list = []
    for attempt in range(tries):
        try:
            server = Server(("127.0.0.1", port), handler(daemon, ref))
            ref.append(server)
            return server
        except OSError:
            if attempt + 1 < tries:
                time.sleep(0.2)
    return None


def main() -> int:
    # Detached, its streams are the null device — but pinned like every tool.
    sys.stdout.reconfigure(encoding="utf-8")
    token = secrets.token_hex(16)
    embedder = Embedder(cache_dir())
    daemon = Daemon(token, embedder)
    server = serve(PORT, daemon, tries=15)
    if server is None:
        return 0
    state = state_path()
    state.parent.mkdir(parents=True, exist_ok=True)
    temporary = state.with_suffix(".tmp")
    temporary.write_text(json.dumps({"port": PORT, "token": token, "pid": os.getpid(),
                                     "version": RUNNING}), encoding="utf-8")
    temporary.replace(state)
    embedder.start()

    def idle() -> None:
        while not daemon.finished():
            time.sleep(60)
        server.shutdown()

    def keep() -> None:
        while True:
            time.sleep(TICK)
            try:
                daemon.keeper.tick()
            except Exception as error:  # noqa: BLE001
                print(f"keep-alive tick skipped: {type(error).__name__}", file=sys.stderr)

    threading.Thread(target=idle, daemon=True).start()
    threading.Thread(target=keep, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        try:
            if json.loads(state.read_text(encoding="utf-8")).get("token") == token:
                state.unlink()
        except (OSError, ValueError):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
