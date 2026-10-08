"""What has to stay true about `translate.py`, and what broke to put it here.

Nothing in this file touches the network. The one thing a live call proved —
that a silent failure reads exactly like a success — is why almost every test
below asserts on the *unchanged* input rather than on a translation.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
from collections.abc import Iterator
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import translate as T  # noqa: E402

TOOL = HERE / "translate"  # the package runs as a directory: `__main__.py`

PAGE = (
    "---\n"
    "scope: operator\n"
    'triggers: ["한국어", "진행\\\\s*상황"]\n'
    "links: [hooks-fail-open]\n"
    "---\n"
    "\n"
    "# 사용자 화면에 뜨는 말은 한국어로\n"
    "\n"
    "규칙. `tool/korean_progress.py` 가 `Bash` 의 description 만 본다.\n"
    "자세한 것은 [[hooks-fail-open]] 과 [색인](index.md) 이 든다.\n"
    "나라장터 입찰공고 는 그대로 남는다. 슬롯 {review_dir} 도 마찬가지다.\n"
    "\n"
    '```python\nprint("이 안은 절대 안 바뀐다")\n```\n'
    "<!-- wiki:operator/korean-progress -->\n"
)

# Every substring that must come back byte-identical no matter what the model
# did to the prose around it.
FROZEN = (
    'triggers: ["한국어", "진행\\\\s*상황"]',
    "links: [hooks-fail-open]",
    "`tool/korean_progress.py`",
    "`Bash`",
    "[[hooks-fail-open]]",
    "(index.md)",
    "{review_dir}",
    'print("이 안은 절대 안 바뀐다")',
    "<!-- wiki:operator/korean-progress -->",
    "나라장터",
    "입찰공고",
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Never read or write the real cache, and never need a real key.

    `ENV` goes too: a limit written in the real `.env` would decide whether
    these tests ever reach their fake model.
    """
    monkeypatch.setattr(T, "CACHE", tmp_path / "cache.sqlite3")
    monkeypatch.setattr(T, "ENV", tmp_path / "absent.env")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    monkeypatch.delenv("TRANSLATE_MONTHLY_USD", raising=False)
    # Deadline tests start with a ready cache, not untimed schema setup.
    cache = T._store()
    assert cache is not None
    cache.close()
    before = set(threading.enumerate())
    yield
    # A group the caller stopped waiting for still finishes; it must do so
    # here, under this test's fakes, not refund into the next test's.
    for worker in set(threading.enumerate()) - before:
        worker.join(5)


def soon() -> float:
    return time.monotonic() + 5


def test_owned_normalization_cancels_between_batches_without_threads_or_shared_cache(monkeypatch):
    cancel = threading.Event()
    calls = []

    def response(system, batch, seconds):
        calls.append((threading.current_thread(), seconds))
        cancel.set()
        return _rewrites_all_prose(system, batch, seconds)

    monkeypatch.setattr(T, "_ask", response)
    before = set(threading.enumerate())
    out = T.english([f"취소되면 멈춘다 {i}" for i in range(T.BATCH + 1)], soon(), cancel=cancel)
    assert len(calls) == 1 and calls[0][0] == threading.current_thread()
    assert set(threading.enumerate()) == before and out[-1]["status"] == "unavailable"
    with sqlite3.connect(T.CACHE) as db:
        assert db.execute("SELECT count(*) FROM shots").fetchone()[0] == 0


def ko(text: str, deadline: float | None = None) -> str:
    return T.translate([text], T.KO_EN, soon() if deadline is None else deadline)[0]


def _rewrites_all_prose(_system: str, batch: list[str], _seconds: float) -> list[str]:
    """A model that translates aggressively but honours the placeholders."""
    return [re.sub(r"[가-힣]+", "ENGLISH", item) for item in batch]


def test_protected_spans_survive_a_model_that_rewrites_everything_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(T, "_ask", _rewrites_all_prose)
    out = T.translate([PAGE], T.KO_EN, soon())[0]

    assert out != PAGE, "the fake model was supposed to change the prose"
    for frozen in FROZEN:
        assert frozen in out, frozen


def test_a_dropped_placeholder_keeps_the_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response missing one span reads fine and is wrong. That is the danger.

    The document would come back with a command or a wiki link silently deleted
    and every other line intact, so nothing downstream would look suspicious.
    """

    def loses_one(_system: str, batch: list[str], _seconds: float) -> list[str]:
        return [T.TOKEN.sub("", item, count=1) for item in batch]

    monkeypatch.setattr(T, "_ask", loses_one)
    assert T.translate([PAGE], T.KO_EN, soon()) == [PAGE]


def test_a_duplicated_or_renumbered_placeholder_keeps_the_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def duplicates(_system: str, batch: list[str], _seconds: float) -> list[str]:
        return [item + f"{T.OPEN}0{T.CLOSE}" for item in batch]

    def renumbers(_system: str, batch: list[str], _seconds: float) -> list[str]:
        return [item.replace(f"{T.OPEN}0{T.CLOSE}", f"{T.OPEN}99{T.CLOSE}", 1)
                for item in batch]

    for faulty in (duplicates, renumbers):
        monkeypatch.setattr(T, "_ask", faulty)
        assert T.translate([PAGE], T.KO_EN, soon()) == [PAGE], faulty.__name__


def test_a_missing_key_returns_the_input_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ENV` is pointed away on purpose. This file makes no requests.

    Deleting the variable stopped being enough the moment the key could also
    live beside the repository: on a machine that has one, this test read it
    and made the live call the module docstring promises never happens.
    """

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(T, "ENV", tmp_path / "absent")
    assert ko("훅이 조용히 죽는다") == "훅이 조용히 죽는다"


def test_the_env_file_outranks_the_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The specific statement beats the general one, and a blank is a statement.

    A machine-wide variable is inherited by every project on the box, which is
    how one Gemini bill came to cover four of them. Letting it win here would
    mean the file could be written, look right, and never once be read. It
    stays as whatever those other projects need; this key lives in the file.

    An empty line in the file still wins. Falling through to the variable
    there answers a half-filled `.env` with the shared key, and the split
    reads as done while the bill stays merged — the one failure this whole
    change exists to end.
    """

    envfile = tmp_path / ".env"
    envfile.write_text('OTHER=x\nGEMINI_API_KEY="from-file"\n', encoding="utf-8")
    monkeypatch.setattr(T, "ENV", envfile)

    monkeypatch.setenv("GEMINI_API_KEY", "from-variable")
    assert T.api_key() == "from-file"

    envfile.write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    assert T.api_key() == ""

    monkeypatch.setattr(T, "ENV", tmp_path / "absent")
    assert T.api_key() == "from-variable"


def test_the_env_file_can_be_pointed_away_for_a_test_run(tmp_path: Path) -> None:
    """The knob the suites' money rides on, checked in a fresh process.

    `ENV` is resolved at import, so the two suites that spawn `inject.py` can
    only disarm the file through the environment. If this stopped working they
    would not go red. They would go quietly online and start spending, which
    is the one way this file's arrangement fails without saying so.
    """

    decoy = tmp_path / "decoy.env"
    decoy.write_text("GEMINI_API_KEY=pointed-away\n", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-c", "import translate; print(translate.api_key())"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(HERE),
        env={**os.environ, "TRANSLATE_ENV": str(decoy)},
    )
    assert done.stdout.strip() == "pointed-away", done.stderr


def test_pytest_configuration_disarms_an_inherited_machine_key() -> None:
    done = subprocess.run(
        [sys.executable, "-c", "import conftest, translate; assert not translate.api_key()"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(HERE),
        env={**os.environ, "GEMINI_API_KEY": "inherited-machine-key"}, timeout=15,
    )
    assert done.returncode == 0, done.stderr


def test_a_cached_entry_is_served_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache answers before the key is read, and that is the contract.

    The test above only holds because its cache is empty. Written as "no key
    means the original" and nothing more, it reads as a guarantee it cannot
    make — a run seeded by an earlier one translates with no key at all. This
    names the real boundary so the pair cannot drift back into that claim.
    """

    monkeypatch.setattr(T, "_ask", _rewrites_all_prose)
    assert ko("훅이 조용히 죽는다") == "ENGLISH ENGLISH ENGLISH"

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(T, "_ask", lambda *_a: None)
    assert ko("훅이 조용히 죽는다") == "ENGLISH ENGLISH ENGLISH"


def test_a_response_that_lands_after_the_deadline_is_not_adopted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The socket timeout bounds one read, not the whole exchange.

    Passing the remaining seconds into the request and never looking at the
    clock again let a slow success be adopted past the caller's budget — which
    costs the caller its whole injection, not just the translation.
    """

    gave_up = threading.Event()

    def slow(_system: str, batch: list[str], _seconds: float) -> list[str]:
        gave_up.wait(10)   # it lands only once the caller has stopped waiting
        return ["EN"] * len(batch)

    monkeypatch.setattr(T, "_ask", slow)
    assert T.translate(["훅이 조용히 죽는다"], T.KO_EN, time.monotonic() + 0.5) == [
        "훅이 조용히 죽는다"
    ]
    gave_up.set()

    # It was still cached: the work was done and the next turn should have it.
    monkeypatch.setattr(T, "_ask", lambda *_a: None)
    for _ in range(100):
        if ko("훅이 조용히 죽는다") == "EN":
            break
        time.sleep(0.05)
    assert ko("훅이 조용히 죽는다") == "EN"


def test_a_passage_the_model_splits_in_two_costs_only_its_own_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Twenty-one passages came back as twenty-three strings, the length check
    refused the answer, and every passage of the turn went unread."""

    texts = [f"{'가나다라마바사아자차카타파하'[n]} 문단은 한도를 넘으면 멈춘다" for n in range(T.BATCH * 3)]
    split = texts[T.BATCH]
    sizes: list[int] = []

    def answers(_system: str, batch: list[str], _seconds: float) -> list[str] | None:
        sizes.append(len(batch))
        return None if split in batch else ["EN"] * len(batch)   # `_ask` refuses a length that does not match

    monkeypatch.setattr(T, "_ask", answers)
    out = T.translate(texts, T.KO_EN, soon())
    assert max(sizes) <= T.BATCH
    lost = [t for t, o in zip(texts, out) if o != "EN"]
    assert split in lost and len(lost) == T.BATCH


def test_a_list_is_asked_a_line_an_item_and_comes_back_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Found in the window: a three-item list sent as one string came back as
    three strings, and the answer's last two paragraphs stayed English."""

    asked: list[list[str]] = []

    def answers(_system: str, batch: list[str], _seconds: float) -> list[str]:
        asked.append(batch)
        return [f"KO {line}" for line in batch]

    monkeypatch.setattr(T, "_ask", answers)
    out = T.translate(["줄바꿈한\n문단", "- 첫째 줄\n- 둘째 줄\n  이어지는 줄\n| 가 | 나 |"], T.KO_EN, soon())
    assert asked == [["줄바꿈한\n문단", "- 첫째 줄", "- 둘째 줄\n  이어지는 줄", "| 가 | 나 |"]], "wrapped prose stays whole"
    assert out == ["KO 줄바꿈한\n문단", "KO - 첫째 줄\nKO - 둘째 줄\n  이어지는 줄\nKO | 가 | 나 |"]


def test_a_request_for_the_shared_cache_reads_past_the_callers_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Korean passage Gemini needs more than a turn's seconds for timed out
    on every turn: the socket was cut at the caller's deadline, so nothing was
    cached and the next turn asked again. The read now runs on past it, off
    the caller's thread; a private text, with no shared cache, does not."""

    timeouts: list[float] = []

    def urlopen(_request, timeout):
        timeouts.append(timeout)
        raise OSError("not sent")

    monkeypatch.setattr(T.urllib.request, "urlopen", urlopen)
    T.translate(["훅이 조용히 죽는다"], T.KO_EN, time.monotonic() + 1)
    T.english(["훅이 조용히 죽는다"], time.monotonic() + 1, held={})
    shared, private = timeouts
    assert shared > T.LATE_SECONDS and private <= 1


def test_the_deadline_is_checked_after_the_work_not_at_the_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restoring spans and writing the cache take time of their own.

    Judging lateness the moment the response landed left a stretch where the
    budget could run out afterwards and the caller still be handed a
    translation it no longer had room for.
    """

    monkeypatch.setattr(T, "_ask", lambda _s, batch, _t: ["EN"] * len(batch))
    monkeypatch.setattr(T, "restore", lambda text, spans: (time.sleep(0.2) or text))

    assert T.translate(["훅이 조용히 죽는다"], T.KO_EN, time.monotonic() + 0.1) == [
        "훅이 조용히 죽는다"
    ]


def test_a_deadline_already_past_returns_the_input_unchanged() -> None:
    """The caller's budget wins. Its output still has to be assembled."""
    assert ko("훅이 조용히 죽는다", 0.0) == "훅이 조용히 죽는다"


def test_text_without_the_source_language_is_never_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-translating English to English comes back reworded, and a reworded
    rule is a rule nobody can diff."""

    asked: list[list[str]] = []
    monkeypatch.setattr(T, "_ask", lambda _s, batch, _t: asked.append(batch))

    assert T.translate(["pure ascii", "", "   "], T.KO_EN, soon()) == ["pure ascii", "", "   "]
    assert asked == [], "a request went out for text with no Korean in it"


def test_korean_with_a_few_identifiers_is_not_sent_to_korean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An answer that came back in Korean still carries paths and names. Sent
    English-to-Korean, Gemini once returned it in English — the overlay turned
    the one readable answer on the screen into the unreadable one."""

    asked: list[list[str]] = []
    monkeypatch.setattr(T, "_ask", lambda _s, batch, _t: asked.append(batch))

    korean = "없음. 이 저장소에 `worktree-safety`라는 문서가 없다. SCHEMA.md와 ENFORCEMENT.md에도 없다."
    assert T.translate([korean], T.EN_KO, soon()) == [korean]
    assert asked == [], "a request went out for text that is already Korean"
    paths = "없음. src/App.tsx, tool/main/app.py, README.md"
    assert T.translate([paths], T.EN_KO, soon()) == [paths]
    assert asked == [], "paths counted as English"
    english = ["The hook passes. 훅.", "wiki-agent", "See " + "하" * 12 + " README.md for details.", "Pass/Fail"]
    T.translate(english, T.EN_KO, soon())
    assert asked == [english], "English stopped being sent"


def test_anything_that_raises_still_returns_the_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller is a hook mid-injection. Its own entry guard would catch this
    and pass the turn, costing the whole injection instead of one translation."""

    def broken() -> tuple[tuple[str, ...], dict[str, str], str]:
        raise RuntimeError("glossary is on fire")

    monkeypatch.setattr(T, "glossary", broken)
    assert T.translate(["훅이 조용히 죽는다"], T.KO_EN, soon()) == ["훅이 조용히 죽는다"]


def test_a_failed_translation_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caching a failure would freeze the original in place permanently."""
    monkeypatch.setattr(T, "_ask", lambda *_a: None)
    assert T.translate(["훅이 조용히 죽는다"], T.KO_EN, soon()) == ["훅이 조용히 죽는다"]

    monkeypatch.setattr(T, "_ask", _rewrites_all_prose)
    assert T.translate(["훅이 조용히 죽는다"], T.KO_EN, soon()) == ["ENGLISH ENGLISH ENGLISH"]


def test_the_cache_answers_without_a_second_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(T, "_ask", _rewrites_all_prose)
    first = T.translate(["훅이 조용히 죽는다"], T.KO_EN, soon())

    asked: list[list[str]] = []
    monkeypatch.setattr(T, "_ask", lambda _s, batch, _t: asked.append(batch))
    assert T.translate(["훅이 조용히 죽는다"], T.KO_EN, soon()) == first
    assert asked == [], "a second request went out for text already cached"


def test_the_glossary_version_retires_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Editing the glossary has to retire what was translated under the old one.

    Without this the cache keeps serving a term the glossary no longer spells
    that way, and the wiki ends up naming one rule two ways.
    """

    monkeypatch.setattr(T, "_ask", _rewrites_all_prose)
    T.translate(["훅이 조용히 죽는다"], T.KO_EN, soon())

    monkeypatch.setattr(T, "glossary", lambda: ((), {}, "a-different-version"))

    # Recorded rather than raised. `translate` swallows exceptions by contract,
    # so an assert thrown in here would be caught and the test would pass
    # whatever happened — a check that cannot fail is not a check.
    asked: list[list[str]] = []
    monkeypatch.setattr(T, "_ask", lambda _s, batch, _t: asked.append(batch))

    assert T.translate(["훅이 조용히 죽는다"], T.KO_EN, soon()) == ["훅이 조용히 죽는다"]
    assert asked == [["훅이 조용히 죽는다"]], "the retired entry was served anyway"


def test_keep_korean_terms_are_masked_not_merely_requested() -> None:
    """The prompt asks; the placeholder guarantees. Only the second one holds."""
    masked, spans = T.protect("나라장터 입찰공고 파서", ("나라장터", "입찰공고"))

    assert "나라장터" not in masked and "입찰공고" not in masked
    assert T.restore(masked, spans) == "나라장터 입찰공고 파서"


def test_it_runs_as_a_process_and_korean_survives_the_pipe(tmp_path: Path) -> None:
    """`craft/hooks-fail-open`, third face. The child writes Korean, the parent
    reads it, and a cp949 default in between kills the reader thread quietly."""

    done = subprocess.run(
        [sys.executable, str(TOOL), "훅이 조용히 죽는다"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        # No key, a cache of its own, and no `.env` either. Dropping the key
        # from the environment is not enough twice over: the cache answers
        # before the key is read, and `.env` is read from disk, where an
        # emptied environment cannot reach it. On a machine with a real key
        # beside the repository this translated for real and the assert below
        # flipped — the check passed only where nobody had set the tool up.
        env={
            "PATH": "",
            "SYSTEMROOT": "C:\\Windows",
            "TRANSLATE_CACHE": str(tmp_path / "cache.sqlite3"),
            "TRANSLATE_ENV": str(tmp_path / "absent.env"),
        },
        check=False,
    )

    assert done.returncode == 0, done.stderr
    # No key in that environment, so it fails open and echoes the input back.
    assert done.stdout.strip() == "훅이 조용히 죽는다"


# --------------------------------------------------------------------------
# The monthly limit. Counted by `translate` itself, so no caller can spend
# another caller's month — the same shape as #18 and #19, in dollars.
# --------------------------------------------------------------------------


def test_a_spent_month_sends_nothing_but_the_cache_still_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(T, "_ask", _rewrites_all_prose)
    assert ko("훅이 조용히 죽는다") == "ENGLISH ENGLISH ENGLISH"

    T.charge(T.MONTHLY_USD)
    asked: list[list[str]] = []
    monkeypatch.setattr(T, "_ask", lambda _s, batch, _t: asked.append(batch))

    assert ko("다른 문장이다") == "다른 문장이다"
    assert asked == [], "a request went out past the monthly limit"
    # Already paid for, so the limit does not take it back.
    assert ko("훅이 조용히 죽는다") == "ENGLISH ENGLISH ENGLISH"


def test_the_limit_is_read_like_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Absent means the default; unreadable means zero, never unlimited."""

    envfile = tmp_path / ".env"
    monkeypatch.setattr(T, "ENV", envfile)
    assert T.limit() == T.MONTHLY_USD

    monkeypatch.setenv("TRANSLATE_MONTHLY_USD", "12.5")
    assert T.limit() == 12.5

    envfile.write_text("TRANSLATE_MONTHLY_USD=0\n", encoding="utf-8")
    assert T.limit() == 0.0, "the file outranks the variable"

    # A blank line is a statement, as it is for the key. `nan` and `inf` pass
    # `float()`, and neither is a ceiling.
    for unreadable in ("five dollars", "", "nan", "inf", "-1"):
        envfile.write_text(f"TRANSLATE_MONTHLY_USD={unreadable}\n", encoding="utf-8")
        assert T.limit() == 0.0, unreadable


def test_a_count_that_raises_keeps_the_cache_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock held past the timeout while the hook and the mirror both write.

    The raise used to escape to `translate`'s outer guard, which hands back
    every original — the cached translations it had already found included.
    """

    monkeypatch.setattr(T, "_ask", _rewrites_all_prose)
    assert ko("훅이 조용히 죽는다") == "ENGLISH ENGLISH ENGLISH"

    def locked(_db: object) -> float:
        raise sqlite3.OperationalError("database is locked")

    asked: list[list[str]] = []
    monkeypatch.setattr(T, "_ask", lambda _s, batch, _t: asked.append(batch))
    monkeypatch.setattr(T, "spent", locked)

    assert T.translate(["훅이 조용히 죽는다", "다른 문장이다"], T.KO_EN, soon()) == [
        "ENGLISH ENGLISH ENGLISH", "다른 문장이다"
    ]
    assert asked == [], "a request went out with the count unreadable"


def test_a_zero_limit_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRANSLATE_MONTHLY_USD", "0")
    asked: list[list[str]] = []
    monkeypatch.setattr(T, "_ask", lambda _s, batch, _t: asked.append(batch))

    assert ko("훅이 조용히 죽는다") == "훅이 조용히 죽는다"
    assert asked == []


def test_without_a_store_nothing_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A limit that cannot be counted is not being kept."""

    asked: list[list[str]] = []
    monkeypatch.setattr(T, "_ask", lambda _s, batch, _t: asked.append(batch))
    monkeypatch.setattr(T, "_store", lambda: None)

    assert ko("훅이 조용히 죽는다") == "훅이 조용히 죽는다"
    assert asked == []


class _Answer:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_Answer":
        return self

    def __exit__(self, *_a: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_a_response_is_charged_from_its_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Thinking tokens bill as output, so they count as output."""

    payload = {
        "candidates": [{"content": {"parts": [{"text": '["EN"]'}]}}],
        "usageMetadata": {
            "promptTokenCount": 1_000_000,
            "candidatesTokenCount": 500_000,
            "thoughtsTokenCount": 500_000,
        },
    }
    monkeypatch.setattr(T.urllib.request, "urlopen", lambda *_a, **_k: _Answer(payload))

    assert ko("훅이 조용히 죽는다") == "EN"
    assert T.usage()["usd"] == pytest.approx(T.PRICE_IN + T.PRICE_OUT)


def test_a_timeout_is_charged_and_a_refusal_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server may have finished a request the client stopped waiting for."""

    def refused(*_a: object, **_k: object) -> None:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(T.urllib.request, "urlopen", refused)
    assert ko("훅이 조용히 죽는다") == "훅이 조용히 죽는다"
    assert T.usage()["usd"] == 0.0

    def slow(*_a: object, **_k: object) -> None:
        raise TimeoutError("read timed out")

    monkeypatch.setattr(T.urllib.request, "urlopen", slow)
    assert ko("훅이 조용히 죽는다") == "훅이 조용히 죽는다"
    assert T.usage()["usd"] > 0.0


def test_a_hold_that_cannot_be_written_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review round 1: charged after the answer, a lock lost the cost for good.

    The hold goes on the books before the request, so a write that fails
    stops the request instead of the record.
    """

    sent: list[object] = []
    monkeypatch.setattr(T.urllib.request, "urlopen", lambda *a, **_k: sent.append(a))
    monkeypatch.setattr(T, "charge", lambda _usd, _at=None: False)

    assert ko("훅이 조용히 죽는다") == "훅이 조용히 죽는다"
    assert sent == [], "a request went out with nothing on the books"


def test_the_hold_and_its_settlement_land_in_one_month(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review round 2: held on the 30th, settled on the 1st.

    The old month kept the hold and the new one got a negative difference —
    a month that started below zero and let that much through unpaid.
    """

    crossed: list[bool] = []
    monkeypatch.setattr(T, "month", lambda: "2026-10" if crossed else "2026-09")
    payload = {
        "candidates": [{"content": {"parts": [{"text": '["EN"]'}]}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 1},
    }

    def midnight(*_a: object, **_k: object) -> _Answer:
        crossed.append(True)
        return _Answer(payload)

    monkeypatch.setattr(T.urllib.request, "urlopen", midnight)
    assert ko("훅이 조용히 죽는다") == "EN"

    db = sqlite3.connect(T.CACHE)
    rows = dict(db.execute("SELECT month, usd FROM spend").fetchall())
    db.close()
    assert "2026-10" not in rows, rows
    assert rows["2026-09"] == pytest.approx(T.cost(payload["usageMetadata"]))


def test_a_body_lost_after_the_answer_keeps_the_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review round 2: once the server answered, the request was billed.

    Refunding every failure but a timeout wrote a cut connection mid-body down
    as free.
    """

    class Cut(_Answer):
        def read(self) -> bytes:
            raise ConnectionResetError("reset mid-body")

    monkeypatch.setattr(T.urllib.request, "urlopen", lambda *_a, **_k: Cut({}))
    assert ko("훅이 조용히 죽는다") == "훅이 조용히 죽는다"
    assert T.usage()["usd"] > 0.0


def test_a_hold_that_waited_out_the_budget_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review round 2: the wait on a lock came out of the caller's seconds."""

    sent: list[object] = []
    monkeypatch.setattr(T.urllib.request, "urlopen", lambda *a, **_k: sent.append(a))
    monkeypatch.setattr(T, "charge", lambda _usd, _at=None: time.sleep(0.2) or True)

    assert T.translate(["훅이 조용히 죽는다"], T.KO_EN, time.monotonic() + 0.1) == [
        "훅이 조용히 죽는다"
    ]
    assert sent == [], "the request went out after the budget was gone"


def test_the_hold_is_the_most_the_request_can_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 3, PR #4: nothing capped the output, so the hold was not a ceiling.

    The request carries `maxOutputTokens`, and the hold is every byte sent as
    an input token plus that cap as output — the worst the bill can be.
    """

    sent: list[bytes] = []
    holds: list[float] = []
    real = T.charge

    def recorded(usd: float, at: str | None = None) -> bool:
        holds.append(usd)
        return real(usd, at)

    def answer(request: object, **_k: object) -> _Answer:
        sent.append(request.data)  # type: ignore[attr-defined]
        return _Answer({"candidates": [{"content": {"parts": [{"text": '["EN"]'}]}}]})

    monkeypatch.setattr(T, "charge", recorded)
    monkeypatch.setattr(T.urllib.request, "urlopen", answer)
    assert ko("훅이 조용히 죽는다") == "EN"

    most = json.loads(sent[0])["generationConfig"]["maxOutputTokens"]
    assert 0 < most < 1_000
    assert holds[0] == pytest.approx(
        T.cost({"promptTokenCount": len(sent[0]), "candidatesTokenCount": most})
    )


def test_a_wikilink_inside_code_is_one_span_not_two() -> None:
    """Nested, the inner placeholder vanished into the outer span's text, and
    no answer could ever pass `intact`."""

    masked, spans = T.protect("본문의 `[[이름]]` 만 센다, 밖의 [[다른]] 도")
    assert spans == ["`[[이름]]`", "[[다른]]"]
    assert T.intact(masked, len(spans))


def test_a_page_goes_english_whole_or_not_at_all(monkeypatch) -> None:
    """`--in-place`: the record labels are set, not left to the model; the
    front matter keeps its triggers and takes an English title; a page the
    model gave back unchanged is not written as if it were done."""

    page = ('---\ntriggers: ["화면"]\ntitle: "화면이 멈춘다"\n---\n\n# 화면이 멈춘다\n\n'
            "무엇. 고쳤다\n\n왜. 멈췄다\n\n출처. PR #3 · `fix/x`\n")
    seen = []

    def english(texts, direction, deadline):
        seen.extend(texts)
        return [t.replace("화면이 멈춘다", "The screen freezes").replace("고쳤다", "fixed it")
                .replace("멈췄다", "it froze") for t in texts]

    monkeypatch.setattr(T, "translate", english)
    done = T.page(page, time.monotonic() + 1)
    assert 'triggers: ["화면"]' in done and 'title: "The screen freezes"' in done
    assert "What. fixed it" in done and "Why. it froze" in done and "Source. PR #3 · `fix/x`" in done
    assert "무엇." not in seen[0], "라벨은 모델에 맡기지 않는다"
    monkeypatch.setattr(T, "translate", lambda texts, direction, deadline: list(texts))
    assert T.page(page, time.monotonic() + 1) is None


def test_the_line_by_line_retry_keeps_a_span_that_runs_over_lines(monkeypatch) -> None:
    """Split on newlines alone, a multi-line comment's halves went to the model
    as prose and came back translated."""

    page = "# 화면\n\n<!-- 화면\n주석 -->\n\n```\n화면\n```\n\n멈췄다\n"

    def refuses_the_page(texts, direction, deadline):
        if len(texts) == 1 and texts[0] == page:
            return list(texts)
        # As the real one does: the model sees only what `protect` leaves.
        return [T.restore(m.replace("화면", "Screen").replace("멈췄다", "it froze").replace("주석", "note"), s)
                for m, s in map(T.protect, texts)]

    monkeypatch.setattr(T, "translate", refuses_the_page)
    done = T.page(page, time.monotonic() + 1)
    assert done == "# Screen\n\n<!-- 화면\n주석 -->\n\n```\n화면\n```\n\nit froze\n"


def test_in_place_skips_an_english_page_and_leaves_code_as_it_was(monkeypatch, tmp_path) -> None:
    """Korean `triggers` alone do not make a page Korean — counted, a rerun
    failed every English page. A label inside a fence is the code's."""

    english = tmp_path / "english.md"
    english.write_text('---\ntriggers: ["화면"]\ntitle: Screen\n---\n\n# Screen\n\nIt froze.\n', encoding="utf-8")
    korean = tmp_path / "korean.md"
    korean.write_text("# 화면\n\n왜. 멈췄다\n\n```\n왜. keep\n```\n", encoding="utf-8")
    monkeypatch.setattr(T, "translate", lambda texts, direction, deadline: [
        t.replace("화면", "Screen").replace("멈췄다", "it froze") for t in texts])

    assert T.in_place([english, korean]) == 0
    assert english.read_text(encoding="utf-8").startswith('---\ntriggers: ["화면"]')
    done = korean.read_text(encoding="utf-8")
    assert "Why. it froze" in done and "```\n왜. keep\n```" in done
    assert not list(tmp_path.glob("*.in-place")), "옆에 쓴 파일은 원본 자리로 옮겨졌다"
