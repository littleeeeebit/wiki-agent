"""translate — Gemini translation that can never cost more than the original.

Every entry point fails open. A missing key, a timeout, a malformed response,
a protected span that came back changed, or a deadline already spent all return
the input unchanged. Callers are hooks assembling an injection: the injection
must still go out, so a translation failure is allowed to cost the translation
and nothing else.

One thing that is not failure: the cache answers before the key is looked at.
A hit needs no request and no key, so removing the key stops new translations
without retracting the ones already made. Point `TRANSLATE_CACHE` somewhere
disposable to get a run with neither.

The protected-span machinery is the load-bearing part, not the prompt. Commands,
paths, wiki links, front matter and glossary terms are lifted out of the text
before the request and put back after, and the response is rejected outright if
a single placeholder came back missing, duplicated or renumbered. Asking a model
to preserve something is a request; removing it from what the model can see is
a guarantee.

`__all__` is the whole contract. `lint.pipeline_surface` goes red when a module
at the `tool/` root uses anything else, so a helper here can change without a
caller elsewhere breaking over it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import tomllib
import urllib.request
from pathlib import Path

__all__ = ("translate", "usage", "glossary", "KO_EN", "EN_KO")

HERE = Path(__file__).resolve().parents[1]  # `tool/`
ROOT = HERE.parent

# The lite tier, because this runs on every utterance. Measured round trips for
# two short strings: 3.6-flash 6.3s, 3.8-flash 4.5s, the lite models 1.0-1.2s.
# The larger models spend that time thinking, which buys nothing on a
# translation whose protected spans are already lifted out of the text. The
# lite models reject `thinkingConfig` outright (HTTP 400) — it is already off.
#
# 3.1 rather than 3.5: medians over three rounds were 1.17s and 1.00s, and
# 0.17s does not pay for the price difference between the two generations.
#
# Pinned on purpose. A floating alias like `gemini-flash-latest` would change
# the model without changing the cache key, and the cache would then serve
# translations made by a model that is no longer the one being used.
MODEL = "gemini-3.1-flash-lite"
ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    f"/{MODEL}:generateContent"
)

# US dollars per million tokens for `MODEL`, from ai.google.dev's pricing page
# on 2026-09-24. They move with `MODEL`: a new model under the old prices would
# keep the monthly limit counting in the wrong currency.
PRICE_IN = 0.25
PRICE_OUT = 1.50

# The monthly limit when neither `.env` nor the environment names one.
MONTHLY_USD = 5.0

# Part of the cache key. Bump it whenever SYSTEM or the request shape changes.
# Without it the cache keeps serving text translated under a different contract,
# and that is worse than no cache: it looks current.
PROMPT_VERSION = "1"

# Overridable because the cache answers before the key is ever looked at — a
# hit needs no request, and no key. That is right in production and poison in a
# test, where a run seeded by an earlier one passes for reasons it did not
# create. Tests point this somewhere disposable.
CACHE = Path(
    os.environ.get("TRANSLATE_CACHE") or (ROOT / "raw" / "translate-cache.sqlite3")
)
GLOSSARY = HERE / "markers" / "glossary.toml"

# The key lives beside the repository rather than in the machine's environment.
# A user-level `GEMINI_API_KEY` is inherited by every process on the box, and
# here that meant one Gemini bill covering a voice agent, an image pipeline and
# this translator with no way to tell which of them spent what — the question
# this file could not answer about itself. `ROOT` comes from `__file__`, so a
# hook fired from inside someone else's repository still finds this file.
#
# Overridable for the same reason `CACHE` is. Once a real `.env` sits in the
# checkout, a suite run there reaches a live key, and the two tests that spawn
# `inject.py` say in their own comments that a live key makes them slow, flaky
# and expensive. Emptying the variable stopped suppressing anything the moment
# the file outranked it, so the file is what a test now has to point away.
ENV = Path(os.environ.get("TRANSLATE_ENV") or (ROOT / ".env"))

KO_EN = "ko->en"
EN_KO = "en->ko"

HANGUL = re.compile(r"[가-힣]")
HANGUL_WORD = re.compile(r"[가-힣]+")
LATIN_WORD = re.compile(r"[A-Za-z]+")
# Not prose: inline code, and a token with a dotted part, a backslash or an
# underscore — a file, a module, a Windows path, an identifier. A slash alone
# does not count: `Pass/Fail` is English.
NOT_PROSE = re.compile(r"`[^`\n]*`|\S*(?:\w\.[A-Za-z]|[\\_])\S*")

# Sentinels from the Unicode private use area. No source document and no
# model vocabulary produces these, so a placeholder that comes back altered
# is proof the whole response is untrustworthy.
OPEN, CLOSE = "", ""
TOKEN = re.compile(rf"{OPEN}(\d+){CLOSE}")

# Order is load-bearing. The outermost constructs have to be lifted first or a
# fenced block's inner backticks get masked one at a time and the fence itself
# never matches.
SPANS = (
    ("front_matter", re.compile(r"\A---\n.*?\n---\n", re.S)),  # triggers live here
    ("fence", re.compile(r"```.*?```", re.S)),
    ("comment", re.compile(r"<!--.*?-->", re.S)),   # the markers inject.py plants
    # Before the wikilink: `[[name]]` in backticks masked as a link first left
    # its placeholder inside the code span's, and `intact` refused every such page.
    ("code", re.compile(r"`[^`\n]+`")),             # commands, paths, identifiers
    ("wikilink", re.compile(r"\[\[[^\]\n]*\]\]")),  # the slug keys graph.json
    ("linkdest", re.compile(r"\]\([^)\n]*\)")),
    ("slot", re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")),  # apply.py fills these
)

# Category names a manifest entry may waive for a rewritten page. Waiving one
# exempts that category alone; everything else is still compared, because a
# page whose rule was inverted still must not lose its commands or its links.
KINDS = tuple(name for name, _ in SPANS) + ("keep_korean",)


def glossary() -> tuple[tuple[str, ...], dict[str, str], str]:
    """`(keep_korean, fixed, version)`. A missing or broken file means none.

    The version is a hash of the file, not a number someone has to remember to
    raise. It goes into the cache key, so editing the glossary retires the
    translations that were made under the old one.
    """

    try:
        raw = GLOSSARY.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
    except Exception:
        return (), {}, "none"
    keep = tuple(str(x) for x in (data.get("keep_korean") or ()))
    fixed = {str(k): str(v) for k, v in (data.get("fixed") or {}).items()}
    return keep, fixed, hashlib.sha256(raw).hexdigest()[:12]


def _mask(text: str, keep: tuple[str, ...]) -> tuple[str, list[str], list[str]]:
    """`(masked, spans, kinds)`. `kinds[i]` is the category of `spans[i]`."""

    spans: list[str] = []
    kinds: list[str] = []
    kind = ""

    def take(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        kinds.append(kind)
        return f"{OPEN}{len(spans) - 1}{CLOSE}"

    for kind, pattern in SPANS:
        text = pattern.sub(take, text)
    kind = "keep_korean"
    # Longest first, so a term that contains another does not get cut in half.
    for term in sorted(keep, key=len, reverse=True):
        if term:
            text = re.sub(re.escape(term), take, text)
    return text, spans, kinds


def protect(text: str, keep: tuple[str, ...] = ()) -> tuple[str, list[str]]:
    """Lift every span the model must not see out of `text`."""

    masked, spans, _ = _mask(text, keep)
    return masked, spans


def by_kind(text: str, keep: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """Every protected span, bucketed by category, for comparing two versions.

    Order inside a bucket is document order, which is what makes a moved link
    read as a difference. That is deliberate: a translation reorders sentences,
    not commands.
    """

    _masked, spans, kinds = _mask(text, keep)
    out: dict[str, list[str]] = {name: [] for name in KINDS}
    for span, kind in zip(spans, kinds):
        out[kind].append(span)
    return out


def intact(text: str, count: int) -> bool:
    """Did every placeholder survive exactly once, and nothing else appear?

    Three failures wear the same face here — a dropped span, a duplicated one,
    and a renumbered one — and all three produce a document that reads fine and
    is wrong. Checking before restore is what keeps them from reaching a file.
    """

    found = sorted(int(n) for n in TOKEN.findall(text))
    return (
        found == list(range(count))
        and text.count(OPEN) == count
        and text.count(CLOSE) == count
    )


def restore(text: str, spans: list[str]) -> str:
    return TOKEN.sub(lambda m: spans[int(m.group(1))], text)


def instruction(direction: str, fixed: dict[str, str]) -> str:
    """The system prompt. Its wording is covered by PROMPT_VERSION."""

    if direction == KO_EN:
        source, target = "Korean", "English"
        terms = [f'"{k}" -> "{v}"' for k, v in fixed.items()]
    else:
        source, target = "English", "Korean"
        terms = [f'"{v}" -> "{k}"' for k, v in fixed.items()]
    lines = [
        f"You translate {source} to {target} for a software engineering wiki.",
        "",
        "- Translate the meaning. Never summarize, expand, explain or add.",
        "- Keep Markdown structure exactly: headings, list markers, table",
        "  pipes, emphasis marks, blank lines.",
        "- Some text is replaced by placeholders that look like a private-use",
        "  character, digits, and another private-use character. Reproduce each",
        "  one verbatim and exactly once. Never translate, renumber or drop one.",
        "- The input is a JSON array of strings. Return a JSON array of the same",
        "  length, in the same order, with each element translated.",
    ]
    if terms:
        lines += ["", "Translate these terms exactly this way:", "  " + ", ".join(terms)]
    return "\n".join(lines)


def setting(name: str) -> str | None:
    """`name` from `ENV`, else from the environment, else `None`.

    The file beside the repository outranks the machine's environment, which
    is the reverse of what dotenv does by default. That default exists so a
    shell can override a development placeholder; this file is not a
    placeholder but a statement that the wiki's translations are billed to a
    key of their own. The variable is the general case and this is the
    specific one, so the specific one wins — and the variable is left as
    whatever every other project on this machine still needs it to be.

    A line that is present wins even when its value is empty. Falling through
    to the environment there would answer a half-filled `.env` with the shared
    key, and the split would read as done while the bill stayed merged.
    """

    try:
        for line in ENV.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name:
                return value.strip().strip("\"'")
    except Exception:
        pass
    value = os.environ.get(name)
    return None if value is None else value.strip()


def api_key() -> str:
    """The key to spend on one request, or `""` when there is none to spend."""

    return setting("GEMINI_API_KEY") or ""


def limit() -> float:
    """This month's ceiling in dollars. Unreadable means zero.

    A limit that cannot be read is not a reason to spend without one. Zero
    stops new requests and nothing else — the cache still answers, and the
    screens show English, which is visible where an overrun is not.
    """

    raw = setting("TRANSLATE_MONTHLY_USD")
    if raw is None:
        return MONTHLY_USD
    # A blank line is a statement, as it is for the key, and `inf` is not a
    # ceiling. Both read as unreadable rather than as the default or no limit.
    try:
        value = float(raw)
    except ValueError:
        return 0.0
    return value if math.isfinite(value) and value >= 0 else 0.0


def month() -> str:
    return time.strftime("%Y-%m", time.gmtime())


def spent(db: sqlite3.Connection) -> float:
    row = db.execute("SELECT usd FROM spend WHERE month = ?", (month(),)).fetchone()
    return float(row[0]) if row else 0.0


def charge(usd: float, at: str | None = None) -> bool:
    """Add to month `at` (this one by default). `False` when it was not written.

    Its own connection, because `_ask` has none.
    """

    db = _store()
    if db is None:
        return False
    try:
        db.execute(
            "INSERT INTO spend VALUES (?, ?) "
            "ON CONFLICT(month) DO UPDATE SET usd = usd + excluded.usd",
            (at or month(), usd),
        )
        db.commit()
        return True
    except Exception:
        return False
    finally:
        db.close()


def cost(metadata: dict) -> float:
    tokens_in = int(metadata.get("promptTokenCount") or 0)
    tokens_out = int(metadata.get("candidatesTokenCount") or 0) + int(
        metadata.get("thoughtsTokenCount") or 0
    )
    return (tokens_in * PRICE_IN + tokens_out * PRICE_OUT) / 1_000_000


def usage() -> dict:
    """`{"month", "usd", "limit"}` for this month. `usd` is `None` when unreadable."""

    db = _store()
    usd = None
    if db is not None:
        try:
            usd = spent(db)
        except Exception:
            pass
        finally:
            db.close()
    return {"month": month(), "usd": usd, "limit": limit()}


def _ask(system: str, batch: list[str], seconds: float) -> list[str] | None:
    """One request. `None` for every failure, so callers keep their originals."""

    started = time.monotonic()
    key = api_key()
    if not key or seconds <= 0 or not batch:
        return None
    text = json.dumps(batch, ensure_ascii=False)
    # A token is at least a character, so twice the input in characters is
    # more than any translation of it needs. Cut short, the array does not
    # parse and the originals are kept; the cap is what makes the hold below
    # a ceiling rather than a guess.
    most = 2 * len(text) + 64
    body = json.dumps(
        {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "user", "parts": [{"text": text}]}
            ],
            # A declared response schema is what makes batching safe: the answer
            # is an array or it is nothing, so a homegrown separator protocol
            # that the model could quietly break never has to exist.
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": most,
                "responseMimeType": "application/json",
                "responseSchema": {"type": "ARRAY", "items": {"type": "STRING"}},
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    # Written before the request, not after. A charge recorded after the
    # answer could fail on a lock — the hook and the screen share this file —
    # and the money would be spent with nothing on the books; every later
    # request would then be judged against a month that looked cheaper than
    # it was. Every byte sent counts as an input token and `maxOutputTokens`
    # as output — `MODEL` does not think, so nothing else is billed — which
    # makes the hold the most this request can cost. Not written means not
    # sent.
    #
    # One month for the hold and whatever follows it. A request held on the
    # 30th and settled on the 1st would otherwise leave the hold in the old
    # month and a negative difference in the new one — headroom nobody paid for.
    at = month()
    held = (len(body) * PRICE_IN + most * PRICE_OUT) / 1_000_000
    if not charge(held, at):
        return None
    # The hold may have waited on a lock, and that wait came out of the
    # caller's budget.
    seconds -= time.monotonic() - started
    if seconds <= 0:
        charge(-held, at)
        return None
    try:
        answer = urllib.request.urlopen(request, timeout=seconds)
    except TimeoutError:
        # The server may well have finished and billed it. With no usage to
        # read, the hold stands.
        return None
    except Exception:
        charge(-held, at)  # refused, or never connected: nothing was billed
        return None
    try:
        with answer:
            parsed = json.loads(answer.read().decode("utf-8"))
    except Exception:
        return None  # it answered, so it was billed; the hold stands
    try:
        # A settlement that fails leaves the hold in place.
        charge(cost(parsed.get("usageMetadata") or {}) - held, at)
        parts = parsed["candidates"][0]["content"]["parts"]
        out = json.loads("".join(str(p.get("text") or "") for p in parts))
    except Exception:
        return None
    if not isinstance(out, list) or len(out) != len(batch):
        return None
    return [str(x) for x in out]


def _store() -> sqlite3.Connection | None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(CACHE, timeout=2.0)
        # The UserPromptSubmit hook and the screen's overlay translate at the same time.
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS shots (k TEXT PRIMARY KEY, v TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS spend (month TEXT PRIMARY KEY, usd REAL)")
        return db
    except Exception:
        return None


def _key(direction: str, version: str, text: str) -> str:
    seed = json.dumps(
        [direction, MODEL, PROMPT_VERSION, version, text], ensure_ascii=False
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def worth_translating(text: str, direction: str) -> bool:
    """Is there anything of the source language in here at all?

    Skipping saves a request, but the reason it is a rule rather than an
    optimization is that translating English to English comes back subtly
    reworded, and reworded rules are rules nobody can diff.

    Toward Korean, one Latin letter is not enough: Korean prose carries paths
    and names, and one such answer sent to Korean came back in English. So the
    prose is weighed, words against words, with code and paths taken out first
    — they are neither language.
    """

    if not text.strip():
        return False
    if direction == KO_EN:
        return bool(HANGUL.search(text))
    prose = NOT_PROSE.sub(" ", text)
    return len(LATIN_WORD.findall(prose)) > len(HANGUL_WORD.findall(prose))


def translate(texts: list[str], direction: str, deadline: float) -> list[str]:
    """Translate many strings in one request. Always returns len(texts) items.

    `deadline` is a `time.monotonic()` value — the moment the caller's own
    budget runs out. Everything not translated by then comes back as the
    original, which is the whole point: the caller's output still gets built.

    There is no default. A default deadline is one budget shared by every
    caller that did not pass its own, and the chat overlay starved under the
    hooks' six seconds exactly that way (#19).
    """

    if not texts:
        return []
    try:
        return _translate(list(texts), direction, deadline)
    except Exception:
        # The callers are hooks part-way through assembling an injection. Their
        # own entry-point guard would catch this and pass the turn, which costs
        # the whole injection rather than one translation — the failure this
        # function exists to make impossible. So it is caught here instead.
        return list(texts)


def _translate(texts: list[str], direction: str, deadline: float) -> list[str]:
    keep, fixed, version = glossary()
    out = list(texts)

    wanted = [i for i, t in enumerate(texts) if worth_translating(t, direction)]
    if not wanted:
        return out

    db = _store()
    keys = {i: _key(direction, version, texts[i]) for i in wanted}
    if db is not None:
        try:
            rows = db.execute(
                f"SELECT k, v FROM shots WHERE k IN ({','.join('?' * len(keys))})",
                list(keys.values()),
            ).fetchall()
            hit = dict(rows)
            for i in list(wanted):
                if keys[i] in hit:
                    out[i] = hit[keys[i]]
                    wanted.remove(i)
        except Exception:
            pass

    # After the cache, never before it: what is cached was paid for already.
    # No store means no count, and a limit nobody can read back is not being
    # kept — so no request either.
    # A count that raised — a lock held past the timeout — is the same as no
    # count, and it must not take the cache hits above down with it.
    # ponytail: read-then-send, so processes racing at the limit each send one.
    if wanted:
        try:
            over = db is None or spent(db) >= limit()
        except Exception:
            over = True
        if over:
            wanted = []

    if wanted:
        masked: list[tuple[str, list[str]]] = [protect(texts[i], keep) for i in wanted]
        seconds = max(0.0, deadline - time.monotonic())
        answer = _ask(instruction(direction, fixed), [m for m, _ in masked], seconds)
        if answer is not None:
            fresh: list[tuple[str, str]] = []
            for i, reply, (_, spans) in zip(wanted, answer, masked):
                if not intact(reply, len(spans)):
                    continue  # keep the original; a mangled span is not a translation
                done = restore(reply, spans)
                fresh.append((keys[i], done))
                out[i] = done
            if db is not None and fresh:
                try:
                    db.executemany("INSERT OR REPLACE INTO shots VALUES (?, ?)", fresh)
                    db.commit()
                except Exception:
                    pass

    if db is not None:
        try:
            db.close()
        except Exception:
            pass

    # Checked here, after everything, rather than at the one moment the
    # response landed. The socket timeout bounds a single read, and restoring
    # spans and writing the cache take time of their own — measuring at any
    # earlier point leaves a stretch where the budget can quietly run out and
    # the caller still gets handed a translation it no longer has room for.
    # The work is kept: it is cached, so the next turn has it for nothing.
    if time.monotonic() > deadline:
        return list(texts)
    return out


# --------------------------------------------------------------------------
# `--check`: hold a translation against the original it was made from.
#
# The original lives in git, not in a snapshot directory. `raw/` is ignored, so
# a snapshot is absent from every other clone, and a comparison that only works
# on one machine is not a gate.
# --------------------------------------------------------------------------

BASELINE = ROOT / "docs" / "translation-baseline.json"
REVIEW = ROOT / "raw" / "translate-review.md"

# Written by hand in Korean and never translated, so they are not targets.
SKIP = ("docs/plans/",)

SCOPES = ("operator", "craft", ".wiki")
SHA = re.compile(r"\A[0-9a-f]{40}\Z")


def original(commit: str, path: str) -> str | None:
    """The pinned original, read out of history. `None` when it is not there.

    Bytes on purpose. A byte-exact comparison is the whole point of this check,
    and `text=True, errors="replace"` would quietly turn a mismatch into a match.
    """

    done = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{commit}:{path}"],
        capture_output=True,
        check=False,
    )
    if done.returncode != 0:
        return None
    try:
        return done.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def baseline(path: Path) -> tuple[dict[str, dict], list[str]]:
    """`({output: entry}, problems)`. A malformed manifest is a failure."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["entries"]
    except Exception as error:
        return {}, [f"{path}: 읽을 수 없다 ({type(error).__name__})"]

    entries: dict[str, dict] = {}
    problems: list[str] = []
    for row in rows:
        name = str(row.get("output") or "")
        kind = str(row.get("kind") or "")
        where = name or "<output 없음>"
        if not name or kind not in ("translation", "rewrite", "new", "kept"):
            problems.append(
                f"{where}: output 과 kind(translation|rewrite|new|kept) 가 있어야 한다"
            )
            continue
        if kind == "kept" and not str(row.get("why") or "").strip():
            # The only thing separating a deliberate Korean document from one
            # nobody got to is the sentence saying so.
            problems.append(f"{where}: kept 에는 why 에 한국어로 남긴 이유를 적는다")
        if kind not in ("new", "kept"):
            if not str(row.get("source") or ""):
                problems.append(f"{where}: {kind} 에는 source 가 있어야 한다")
            if not SHA.match(str(row.get("commit") or "")):
                # A branch name or a short sha moves. The point of pinning is
                # that the thing compared against cannot change under the check.
                problems.append(f"{where}: commit 은 40자리 전체 SHA 여야 한다")
        waived = row.get("allow") or []
        if waived and kind != "rewrite":
            problems.append(f"{where}: allow 는 rewrite 에서만 쓴다")
        if any(k not in KINDS for k in waived):
            problems.append(f"{where}: allow 는 {list(KINDS)} 중에서만 고른다")
        if waived and not str(row.get("why") or "").strip():
            problems.append(f"{where}: allow 를 쓰면 why 에 이유를 적는다")
        entries[name] = row
    return entries, problems


def links(text: str) -> list[str]:
    return [s[2:-2].strip() for s in by_kind(text)["wikilink"]]


def inspect(entry: dict, keep: tuple[str, ...], root: Path | None) -> list[str]:
    """Everything wrong with one translated file. Empty means it is sound."""

    name = str(entry["output"])
    produced = ROOT / name
    if not produced.exists():
        return [f"{name}: 산출물이 없다"]
    made = produced.read_text(encoding="utf-8")
    if not made.strip():
        return [f"{name}: 산출물이 비었다"]

    found = []
    for slug in links(made):
        if not any((ROOT / scope / f"{slug}.md").exists() for scope in SCOPES):
            found.append(f"{name}: 깨진 링크 [[{slug}]]")

    if entry["kind"] in ("new", "kept"):
        # `new` was written in English from the start and `kept` stays Korean
        # on purpose. Neither has a Korean original standing behind an English
        # output, which is the only thing the comparison below can read.
        return found

    source = str(entry["source"])
    was = original(str(entry["commit"]), source)
    if was is None and root is not None:
        spare = root / source
        was = spare.read_text(encoding="utf-8") if spare.exists() else None
    if was is None:
        return found + [f"{name}: 원문을 못 읽는다 ({entry['commit'][:12]}:{source})"]

    waived = set(entry.get("allow") or ())
    before, after = by_kind(was, keep), by_kind(made, keep)
    for kind in KINDS:
        if kind in waived:
            continue
        if kind == "keep_korean":
            lost = [t for t in set(before[kind]) if t not in after[kind]]
            if lost:
                found.append(f"{name}: 보존 용어가 사라졌다 — {', '.join(sorted(lost))}")
        elif before[kind] != after[kind]:
            found.append(
                f"{name}: {kind} 가 원문과 다르다 "
                f"(원문 {len(before[kind])}개, 산출물 {len(after[kind])}개)"
            )
    return found


def targets(given: list[str]) -> list[str]:
    """Expand directories and globs to repo-relative Markdown paths."""

    out: list[str] = []
    for raw in given:
        base = (ROOT / raw) if not Path(raw).is_absolute() else Path(raw)
        found = sorted(base.rglob("*.md")) if base.is_dir() else sorted(
            ROOT.glob(raw)
        ) or ([base] if base.exists() else [])
        for path in found:
            name = path.resolve().relative_to(ROOT).as_posix()
            if not name.startswith(SKIP) and path.suffix == ".md":
                out.append(name)
    return sorted(dict.fromkeys(out))


def check(given: list[str], manifest: Path, root: Path | None, samples: int) -> int:
    keep, _fixed, _version = glossary()
    entries, problems = baseline(manifest)

    chosen = targets(given)
    if not chosen:
        # Silence is the failure this guards against: a gate that checked
        # nothing exits 0 and reads exactly like a gate that checked everything.
        problems.append("검사 대상이 없다. 경로가 비었거나 전부 제외됐다")

    for name in chosen:
        if name not in entries:
            problems.append(f"{name}: manifest 에 없다. 빠뜨린 것과 새 문서를 구별할 수 없다")
        else:
            problems += inspect(entries[name], keep, root)

    print(f"# translate --check — 대상 {len(chosen)}개, manifest {len(entries)}건")
    if root is not None:
        print("\n**`--source-root` 로 돌렸다. 배포 게이트 통과로 세지 않는다.**")
    print()
    for line in problems:
        print(f"- {line}")
    if not problems:
        print("결함 없음.")

    if samples > 0 and chosen:
        review(chosen[:samples])
    return 1 if problems else 0


def review(names: list[str]) -> None:
    """Back-translate a few files so a person can read what the meaning became.

    Never part of the exit code. Whether meaning survived is a judgement, and
    a machine that claimed to have made it would only hide that nobody did.
    """

    made = [(ROOT / n).read_text(encoding="utf-8") for n in names]
    back = translate(made, EN_KO, time.monotonic() + 120)
    body = ["# 표본 역번역 — 사람이 읽는 자리다", ""]
    for name, english, korean in zip(names, made, back):
        body += [f"## {name}", "", "### 영어 산출물", "", english,
                 "", "### 되돌린 한국어", "", korean, ""]
    try:
        REVIEW.parent.mkdir(parents=True, exist_ok=True)
        REVIEW.write_text("\n".join(body), encoding="utf-8")
        print(f"\n표본 역번역 {len(names)}건 → `{REVIEW.relative_to(ROOT).as_posix()}`")
    except Exception as error:
        print(f"\n표본 역번역을 못 썼다: {type(error).__name__}")


# --------------------------------------------------------------------------
# `--in-place`: a page rewritten in English where it stands.
#
# The wiki is read by agents, and a question reaches them already rendered in
# English, so a Korean page is one search does not find. Front matter stays
# protected — `triggers` match what a person types — except `title:`.
# --------------------------------------------------------------------------

# A decision record's labels, set before the model sees them: left to it, `왜.`
# came back `Reason.` as often as `Why.`, and the readers look for one word.
LABELS = (("무엇. ", "What. "), ("왜. ", "Why. "), ("출처. ", "Source. "))
TITLE = re.compile(r"^title: (.+)$", re.M)
IN_PLACE_SECONDS = 300
IN_PLACE_THREADS = 8


def page(text: str, deadline: float) -> str | None:
    """`text`, a Markdown page, in English. `None` when any of it could not be
    translated: half a page is not written."""

    for ko, en in LABELS:
        text = re.sub(rf"^{re.escape(ko)}", en, text, flags=re.M)
    front = SPANS[0][1].match(text)
    found = TITLE.search(front.group(0)) if front else None
    raw = found.group(1).strip() if found else ""
    quoted = raw.startswith('"')
    title = json.loads(raw) if quoted else raw
    body, title_en = translate([text, title], KO_EN, deadline)
    if (worth_translating(text, KO_EN) and body == text) or (worth_translating(title, KO_EN) and title_en == title):
        return None
    if found:
        value = json.dumps(title_en, ensure_ascii=False) if quoted else title_en
        head = SPANS[0][1].match(body).group(0)
        body = TITLE.sub(lambda _: f"title: {value}", head, count=1) + body[len(head):]
    return body


def in_place(paths: list[Path]) -> int:
    """Each Korean page, rewritten in English. Line endings are kept."""

    from concurrent.futures import ThreadPoolExecutor

    def one(path: Path) -> str:
        raw = path.read_bytes().decode("utf-8")
        text = raw.replace("\r\n", "\n")
        if not worth_translating(text, KO_EN):
            return "skip"
        done = page(text, time.monotonic() + IN_PLACE_SECONDS)
        if done is None:
            return "failed"
        path.write_bytes((done.replace("\n", "\r\n") if "\r\n" in raw else done).encode("utf-8"))
        return "done"

    with ThreadPoolExecutor(IN_PLACE_THREADS) as pool:
        results = list(pool.map(one, paths))
    failed = [p for p, r in zip(paths, results) if r == "failed"]
    print(f"옮김 {results.count('done')} · 이미 영어 {results.count('skip')} · 못 옮김 {len(failed)}")
    for path in failed:
        print(f"- {path}")
    return 1 if failed else 0


def main() -> int:
    # Output is a pipe more often than not, and the default there is cp949.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="번역과 그 검사")
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--check", action="store_true", help="산출물을 원문과 대조한다")
    parser.add_argument("--manifest", type=Path, default=BASELINE)
    parser.add_argument("--source-root", type=Path,
                        help="커밋 전 작업 검사용 대체 원문. 게이트 통과로 안 센다")
    parser.add_argument("--review", type=int, default=0, metavar="N",
                        help="표본 N건을 역번역해 사람이 읽을 파일에 적는다")
    parser.add_argument("--en-to-ko", action="store_true")
    parser.add_argument("--usage", action="store_true", help="이번 달 번역 사용액과 한도")
    parser.add_argument("--in-place", action="store_true", help="한국어 페이지를 영어로 옮겨 그 자리에 쓴다")
    args = parser.parse_args()

    if args.in_place:
        return in_place([Path(p) for p in args.paths])
    if args.usage:
        now = usage()
        usd = "읽을 수 없다" if now["usd"] is None else f"${now['usd']:.4f}"
        print(f"{now['month']} 사용 {usd} / 한도 ${now['limit']:.2f}")
        return 0
    if args.check:
        return check(args.paths, args.manifest, args.source_root, args.review)

    direction = EN_KO if args.en_to_ko else KO_EN
    text = " ".join(args.paths) if args.paths else sys.stdin.read()
    print(translate([text], direction, time.monotonic() + 30)[0])
    return 0


if __name__ == "__main__":
    # No entry-point guard here on purpose. This is a CLI, and `hooks-fail-open`
    # only swallows exceptions where a failure would stop a session; when a
    # person is reading the output a traceback is the answer, not noise.
    raise SystemExit(main())
