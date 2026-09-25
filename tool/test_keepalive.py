"""Keep-alive: the cell's hooks, their notices, and the daemon's `Keeper`.

The daemon's half runs on a fake clock and a fake `orca`; nothing here types
into a real cell or binds the real port.
"""

import io
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import apply  # noqa: E402
import keepalive  # noqa: E402
import search  # noqa: E402
from search import daemon as searchd  # noqa: E402

HANDLE = "term_cell"
CHECKOUT = str(Path(tempfile.mkdtemp()).resolve())
MIN = 60

# What an idle Claude cell in Orca showed on 2026-09-25, cut to its bottom.
IDLE_SCREEN = [
    "✻ Crunched for 51s · done 5:18 PM",
    "                                                  ✔ Update installed · Restart to update",
    "─" * 120,
    "❯\xa0",
    "─" * 120,
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent",
    "",
]


def project(keep_alive: str | None) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / ".wiki").mkdir()
    if keep_alive is not None:
        (root / ".wiki" / "adapter.toml").write_text(
            f'{keep_alive}\nagents = ["claude"]\n\n[slots]\ngate_cmd = "x"\n', encoding="utf-8")
    return root


@pytest.fixture
def sent(monkeypatch):
    """`search.notify` replaced by a list of what would have gone out, in an
    Orca cell. The daemon answers as if every ping-turn followed its ping."""

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(search, "notify", lambda path, body, **_waits: calls.append((path, body))
                        or {"ping": path == "/ping-turn"})
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", HANDLE)
    return calls


def hook(event: str, repo: Path, host: str = "claude", session: str = "s1") -> int:
    payload = {"hook_event_name": event, "session_id": session, "source": "startup"}
    was = sys.stdin, sys.stdout, sys.argv
    sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8")))
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    sys.argv = ["keepalive.py", "--project", str(repo), "--checkout", CHECKOUT, "--host", host]
    try:
        return keepalive.main()
    finally:
        sys.stdin, sys.stdout, sys.argv = was


# ---- the hooks ----------------------------------------------------------------


def test_the_hooks_send_nothing_unless_claude_in_an_orca_cell_of_a_repository_that_opted_in(
        sent, monkeypatch):
    on = project("keep_alive = 2")
    cases = [
        ("Codex", on, "codex", HANDLE),
        ("no Orca cell", on, "claude", None),
        ("no keep_alive", project(""), "claude", HANDLE),
        ("keep_alive = 0", project("keep_alive = 0"), "claude", HANDLE),
        ("keep_alive = true", project("keep_alive = true"), "claude", HANDLE),
        ("no adapter", project(None), "claude", HANDLE),
    ]
    for name, repo, host, handle in cases:
        if handle:
            monkeypatch.setenv("ORCA_TERMINAL_HANDLE", handle)
        else:
            monkeypatch.delenv("ORCA_TERMINAL_HANDLE", raising=False)
        for event in ("SessionStart", "Stop", "SessionEnd"):
            assert hook(event, repo, host) == 0
        assert keepalive.on_prompt("사람 발화", host, str(repo), "s1") == (False, None)
        assert keepalive.on_prompt(keepalive.PING, host, str(repo), "s1") == (False, None)
        assert sent == [], (name, sent)

    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", HANDLE)
    for event in ("SessionStart", "Stop", "SessionEnd"):
        hook(event, on)
    assert [p for p, _b in sent] == ["/own", "/idle", "/gone"], sent
    assert sent[1][1] == {"session": "s1", "handle": HANDLE, "checkout": CHECKOUT, "limit": 2}


def test_the_hook_dispatcher_tells_keepalive_its_host_project_and_checkout():
    """Without `--host` keep-alive never runs; without `--checkout` a worktree
    cell is judged by the main clone's path and every ping is refused."""

    import hook as dispatcher
    from test_hook import attached_repo

    with tempfile.TemporaryDirectory() as raw:
        main, tree = attached_repo(Path(raw))
        ran = {}
        was = dispatcher.runpy.run_path, sys.stdin, sys.stdout, sys.argv
        dispatcher.runpy.run_path = lambda path, run_name=None: ran.setdefault(Path(path).name, sys.argv[1:])
        sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps({"cwd": str(tree)}).encode("utf-8")))
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        try:
            dispatcher.main(["claude", "keepalive.py"])
        finally:
            dispatcher.runpy.run_path, sys.stdin, sys.stdout, sys.argv = was
    args = ran["keepalive.py"]
    assert args[args.index("--host") + 1] == "claude", args
    assert Path(args[args.index("--project") + 1]).resolve() == main.resolve(), args
    assert Path(args[args.index("--checkout") + 1]).resolve() == tree.resolve(), args


def test_a_per_project_install_names_the_host_and_keepalive_finds_the_worktree(sent):
    """Review round 1: the per-project commands carried no `--host`, so
    neither `/idle` nor `/busy` ever went out, and no `--checkout`, so a
    worktree cell would have been judged by the main clone's path."""

    import shlex

    from test_hook import attached_repo

    with tempfile.TemporaryDirectory() as raw:
        main, tree = attached_repo(Path(raw))
        (main / ".wiki/adapter.toml").write_text('keep_alive = 2\nagents=["claude"]\n', encoding="utf-8")
        settings = {}
        apply.configure(settings, main, None, "C:/py.exe", "claude")

        def args(event, script):
            command = next(h["command"] for group in settings["hooks"][event]
                           for h in group["hooks"] if script in h["command"])
            return shlex.split(command)[2:]

        prompt = args("UserPromptSubmit", "inject.py")
        assert prompt[prompt.index("--host") + 1] == "claude", prompt
        payload = {"hook_event_name": "Stop", "session_id": "s1", "cwd": str(tree)}
        was = sys.stdin, sys.stdout, sys.argv
        sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8")))
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        sys.argv = ["keepalive.py", *args("Stop", "keepalive.py")]
        try:
            keepalive.main()
        finally:
            sys.stdin, sys.stdout, sys.argv = was
    assert sent == [("/idle", {"session": "s1", "handle": HANDLE,
                               "checkout": str(tree.resolve()), "limit": 2})], sent


def test_the_busy_notice_outlives_a_hook_that_fails_open(tmp_path):
    """Review round 1: a malformed adapter raised after `/busy` had started,
    `__main__` passed with 0 before the join, and a daemon thread died
    unsent — the timer stayed armed through the person's turn."""

    import subprocess

    repo = tmp_path / "repo"
    (repo / ".wiki").mkdir(parents=True)
    (repo / ".wiki/adapter.toml").write_text('keep_alive = 2\nslots = "bad"\n', encoding="utf-8")
    delivered = tmp_path / "delivered"
    runner = tmp_path / "run.py"
    runner.write_text(f"""import runpy, sys, time
sys.path.insert(0, {str(HERE)!r})
import search
def slow(path, body, **_waits):
    time.sleep(0.5)
    open({str(delivered)!r}, "a", encoding="utf-8").write(path)
search.notify = slow
sys.argv = ["inject.py", "--host", "claude", "--project", {str(repo)!r}]
runpy.run_path({str(HERE / "inject.py")!r}, run_name="__main__")
""", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(runner)], input=json.dumps({"prompt": "사람 발화", "session_id": "s1"}),
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        env={**__import__("os").environ, "ORCA_TERMINAL_HANDLE": HANDLE})
    assert done.returncode == 0 and "hook skipped" in done.stderr, (done.returncode, done.stderr)
    assert delivered.read_text(encoding="utf-8") == "/busy"


def test_claude_is_wired_on_three_events_and_codex_not_at_all():
    for project_path in (None, Path(tempfile.mkdtemp())):
        claude, codex = {}, {}
        apply.configure(claude, project_path, None, "C:/py.exe", "claude")
        apply.configure(codex, project_path, None, "C:/py.exe", "codex")
        wired = {event for event, groups in claude["hooks"].items()
                 for group in groups for h in group["hooks"] if "keepalive.py" in h["command"]}
        assert wired == {"SessionStart", "Stop", "SessionEnd"}, claude["hooks"].keys()
        assert "keepalive.py" not in json.dumps(codex)


@pytest.fixture
def home(monkeypatch):
    """No daemon, no state file, the search switch on."""

    monkeypatch.delenv("WIKI_SEARCH", raising=False)
    search.state_path().unlink(missing_ok=True)
    yield
    search.state_path().unlink(missing_ok=True)


def test_a_notice_starts_the_missing_daemon_waits_for_it_and_is_delivered(home, monkeypatch):
    daemon = searchd.Daemon("secret", searchd.Embedder(None))
    servers = []

    def start():
        def later():
            time.sleep(0.3)
            server = searchd.serve(0, daemon)
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            search.state_path().parent.mkdir(parents=True, exist_ok=True)
            search.state_path().write_text(json.dumps(
                {"port": server.server_address[1], "token": "secret"}), encoding="utf-8")
        threading.Thread(target=later, daemon=True).start()

    monkeypatch.setattr(search, "spawn", start)
    try:
        assert search.notify("/own", {"session": "s1", "handle": HANDLE})
        assert daemon.keeper.owners == {HANDLE: "s1"}
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def test_a_notice_is_dropped_when_no_daemon_comes_up_and_the_hook_still_passes(home, monkeypatch):
    started = []
    monkeypatch.setattr(search, "spawn", lambda: started.append(1))
    monkeypatch.setattr(search, "SPAWN_WAIT", 0.4)
    began = time.perf_counter()
    assert not search.notify("/own", {"session": "s1", "handle": HANDLE})
    assert started == [1]
    assert time.perf_counter() - began < 1.5
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", HANDLE)
    assert hook("Stop", project("keep_alive = 2")) == 0


def test_a_notice_is_retried_against_a_daemon_that_was_there_but_slow(home, monkeypatch):
    """A `/busy` lost to one 150 ms timeout left the timer armed while the
    turn ran, and the ping landed in it (the public copy's review)."""

    took = ({"ping": False}, False)
    answers = [(None, False), (None, False), took]
    monkeypatch.setattr(search, "call", lambda *a, **k: answers.pop(0) if answers else took)
    assert search.notify("/busy", {}, spawn_wait=0, retry=2.0)
    assert answers == [], "not retried"
    answers[:] = [(None, False)] * 100
    assert not search.notify("/busy", {}, spawn_wait=0, retry=0.3), "the retry is bounded"
    answers[:] = [(None, True)] + [took] * 5
    assert not search.notify("/busy", {}, spawn_wait=0, retry=2.0), (
        "a daemon this call had to start holds no timer — no wait on every utterance")
    monkeypatch.setenv("WIKI_SEARCH", "off")
    answers[:] = [(None, False)] * 100
    began = time.perf_counter()
    assert not search.notify("/busy", {}, retry=2.0)
    assert time.perf_counter() - began < 0.1


def test_a_stale_state_file_and_no_daemon_stays_inside_the_hooks_budget(home, monkeypatch):
    """A refused connect was feared to cost 2 s on Windows. The socket timeout
    cuts it; the whole notice is the spawn wait plus two calls."""

    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    search.state_path().parent.mkdir(parents=True, exist_ok=True)
    search.state_path().write_text(json.dumps({"port": port, "token": "t"}), encoding="utf-8")
    monkeypatch.setattr(search, "spawn", lambda: None)
    began = time.perf_counter()
    assert not search.notify("/idle", {"session": "s1", "handle": HANDLE}, retry=keepalive.RETRY)
    assert time.perf_counter() - began < search.SPAWN_WAIT + 2 * 2 * search.NOTIFY_TIMEOUT + 0.5 < 5


# ---- inject.py ----------------------------------------------------------------


def utter(prompt: str, repo: Path) -> str:
    import inject
    import translate

    was = (translate.translate, sys.stdin, sys.stdout, sys.argv)
    translate.translate = lambda texts, direction=None, deadline=None: list(texts)
    sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(
        {"prompt": prompt, "session_id": "s1"}, ensure_ascii=False).encode("utf-8")))
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    sys.argv = ["inject.py", "--host", "claude", "--project", str(repo)]
    try:
        inject.main()
        sys.stdout.seek(0)
        return sys.stdout.read()
    finally:
        translate.translate, sys.stdin, sys.stdout, sys.argv = was


def test_the_ping_turn_carries_nothing_records_nothing_and_says_so(sent):
    repo = project("keep_alive = 2")
    assert utter(keepalive.PING, repo) == ""
    assert not (repo / ".wiki" / "trajectory.jsonl").exists(), "the ping went into the trajectory"
    utter("<task-notification>\n<task-id>x</task-id>", repo)
    utter("이어서 해라", repo)
    assert sent == [
        ("/ping-turn", {"session": "s1", "handle": HANDLE}),
        ("/busy", {"session": "s1", "handle": HANDLE, "reset": False}),
        ("/busy", {"session": "s1", "handle": HANDLE, "reset": True}),
    ], sent
    rows = (repo / ".wiki" / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2, "the two utterances that are not the ping are recorded as before"


def test_the_pings_words_from_a_person_are_the_persons_turn(sent, monkeypatch):
    """Review round 2: the hook knew the ping by its words alone. A person
    who typed them lost the turn's injection and record; one who quoted them
    never reset the count."""

    repo = project("keep_alive = 2")
    monkeypatch.setattr(search, "notify", lambda path, body, **_waits: sent.append((path, body))
                        or {"ping": False})
    utter(keepalive.PING, repo)
    utter(f"Why did I see {keepalive.PING}", repo)
    assert sent == [
        ("/ping-turn", {"session": "s1", "handle": HANDLE}),
        ("/busy", {"session": "s1", "handle": HANDLE, "reset": True}),
    ], sent
    rows = (repo / ".wiki" / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2, "both turns are recorded as the person's"


def test_the_ping_is_not_a_person_speaking():
    """Counted as one, every ping would be a cheap utterance in `usage` and
    pull the per-utterance cost down by the very turns keep-alive adds."""

    from transcript import human_text

    assert human_text({"type": "user", "message": {"content": search.PING}}) is None
    assert human_text({"type": "user", "message": {"content": "keep-alive 를 켜라"}}) == "keep-alive 를 켜라"


def test_the_ping_turn_answered_ok_is_not_a_promise_to_continue():
    from declared_continuation import verdict

    transcript = Path(tempfile.mkdtemp()) / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(row) for row in [
        {"type": "user", "message": {"content": keepalive.PING}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
    ]), encoding="utf-8")
    assert verdict({"transcript_path": str(transcript)}) is None


# ---- the daemon ---------------------------------------------------------------


class Orca:
    """What the daemon asks Orca, answered from fields a test can change."""

    def __init__(self):
        self.cell = {"connected": True, "writable": True, "worktreePath": CHECKOUT}
        self.screen = list(IDLE_SCREEN)
        self.sent: list[str] = []

    def __call__(self, *args):
        if args[:2] == ("terminal", "show"):
            return {"terminal": dict(self.cell)} if self.cell else None
        if args[:2] == ("terminal", "read"):
            return {"terminal": {"tail": list(self.screen)}}
        if args[:2] == ("terminal", "send"):
            self.sent.append(args[args.index("--text") + 1])
            return {}
        raise AssertionError(args)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def at(self, minutes: float) -> None:
        self.now = 1_000_000.0 + minutes * MIN


def keeper(orca=None, clock=None):
    clock = clock or Clock()
    return searchd.Keeper(clock, orca or Orca()), clock


def person(k, session="s1", handle=HANDLE):
    k.notice("busy", {"session": session, "handle": handle, "reset": True})


def stop(k, session="s1", handle=HANDLE, limit=2):
    k.notice("idle", {"session": session, "handle": handle, "checkout": CHECKOUT, "limit": limit})


def ping_turn(k, session="s1", handle=HANDLE):
    k.notice("ping-turn", {"session": session, "handle": handle})


def test_one_ping_at_55_minutes_and_none_past_the_cap():
    k, clock = keeper()
    person(k)
    stop(k)
    clock.at(54)
    k.tick()
    assert k.call.sent == []
    clock.at(55)
    k.tick()
    assert k.call.sent == [keepalive.PING]
    ping_turn(k)
    clock.at(56)
    stop(k)
    clock.at(111)
    k.tick()
    assert len(k.call.sent) == 2
    ping_turn(k)
    clock.at(112)
    stop(k)
    for minute in range(113, 240, 5):
        clock.at(minute)
        k.tick()
    assert len(k.call.sent) == 2, "past the cap"


def test_a_ping_turn_with_no_ping_out_is_a_person_but_resets_only_a_counted_session():
    """The daemon, not the words, says whether a turn is its ping (round 2).
    A session it holds no count for may be answering a ping sent before a
    restart, so there it resets nothing — it pings less, never past the cap
    (`test_restarts_after_each_ping_never_take_a_session_past_two`)."""

    k, clock = keeper()
    person(k)
    stop(k)
    clock.at(55)
    k.tick()
    assert k.notice("ping-turn", {"session": "s1", "handle": HANDLE}) is True
    assert k.sessions["s1"]["count"] == 1
    stop(k)
    assert k.notice("ping-turn", {"session": "s1", "handle": HANDLE}) is False
    assert k.sessions["s1"]["state"] == "busy" and k.sessions["s1"]["count"] == 0
    assert k.notice("ping-turn", {"session": "s9", "handle": "term_other"}) is False
    assert k.sessions["s9"]["count"] is None


def test_a_harness_utterance_after_a_ping_does_not_reset_the_count():
    k, clock = keeper()
    person(k)
    stop(k)
    clock.at(55)
    k.tick()
    ping_turn(k)
    stop(k)
    k.notice("busy", {"session": "s1", "handle": HANDLE, "reset": False})
    stop(k)
    for minute in range(56, 400, 5):
        clock.at(minute)
        k.tick()
        if k.sessions.get("s1", {}).get("state") == "sent":
            ping_turn(k)
            stop(k)
    assert len(k.call.sent) == 2, k.call.sent


def test_another_session_in_the_same_cell_takes_it_over():
    """The shape of `/clear`: the old session can say nothing more."""

    k, clock = keeper()
    person(k)
    stop(k)
    k.notice("own", {"session": "s2", "handle": HANDLE})
    assert "s1" not in k.sessions and k.owners == {HANDLE: "s2"}
    clock.at(60)
    k.tick()
    assert k.call.sent == []


def test_after_a_restart_the_owner_comes_back_but_the_count_does_not():
    k, clock = keeper()
    stop(k)
    assert k.owners == {HANDLE: "s1"}, "the owner comes back from any notice"
    clock.at(60)
    k.tick()
    assert k.call.sent == [], "first seen at Stop: the count is at the cap"

    k, clock = keeper()
    person(k)
    stop(k)
    clock.at(55)
    k.tick()
    assert k.call.sent == [keepalive.PING], "a person spoke, so the timer is set"


def test_restarts_after_each_ping_never_take_a_session_past_two():
    orca, clock, sent = Orca(), Clock(), 0
    for restart_after in (1, 2):
        k, _ = keeper(orca, clock)
        clock.at(0)
        person(k)
        stop(k)
        for n in range(restart_after):
            clock.at(55 * (n + 1) + n)
            k.tick()
            ping_turn(k)
            stop(k)
        k, _ = keeper(orca, clock)  # the daemon started again
        for source in ("compact", "resume"):
            k.notice("own", {"session": "s1", "handle": HANDLE, "source": source})
            ping_turn(k)
            stop(k)
        for _minute in range(0, 600, 5):
            clock.now += 5 * MIN
            k.tick()
            if k.sessions.get("s1", {}).get("state") == "sent":
                ping_turn(k)
                stop(k)
        assert len(orca.sent) - sent <= 2, (restart_after, orca.sent)
        sent = len(orca.sent)


def test_nothing_is_sent_and_the_session_goes_when_the_look_before_sending_fails():
    def run(change, last_is_stop=True):
        orca = Orca()
        change(orca)
        k, clock = keeper(orca)
        person(k)
        stop(k)
        if not last_is_stop:
            k.notice("busy", {"session": "s1", "handle": HANDLE, "reset": False})
        clock.at(55)
        k.tick()
        return orca.sent, k

    sent, k = run(lambda o: None, last_is_stop=False)
    assert sent == [] and k.sessions["s1"]["due"] is None, "the last notice was not Stop"
    cases = {
        "cell gone": lambda o: setattr(o, "cell", None),
        "not writable": lambda o: o.cell.update(writable=False),
        "another repository": lambda o: o.cell.update(worktreePath=str(Path(CHECKOUT).parent)),
        "half-written message": lambda o: o.screen.__setitem__(3, "❯ 이어서 하"),
        "fell back to the shell": lambda o: o.screen.extend(["PS C:\\work> "]),
        "unknown screen": lambda o: setattr(o, "screen", ["$ "]),
    }
    for name, change in cases.items():
        sent, k = run(change)
        assert sent == [], name
        assert "s1" not in k.sessions, name


def test_a_notice_cannot_come_between_the_last_check_and_the_send():
    """Marked `sent` and then sent outside the lock, a `/busy` arriving in
    between cleared the timer while the ping still went out (the public
    copy's review)."""

    class Racing(Orca):
        def __init__(self, keeper_ref, during):
            super().__init__()
            self.keeper_ref, self.during, self.late = keeper_ref, during, None

        def __call__(self, *args):
            if args[:2] == ("terminal", self.during):
                if self.during == "read":
                    person(self.keeper_ref[0])
                else:
                    self.late = threading.Thread(target=person, args=(self.keeper_ref[0],))
                    self.late.start()
                    self.late.join(0.2)
                    assert self.late.is_alive(), "a notice got in while the ping was being sent"
            return super().__call__(*args)

    ref = []
    orca = Racing(ref, "read")
    k, clock = keeper(orca)
    ref.append(k)
    person(k)
    stop(k)
    clock.at(55)
    k.tick()
    assert orca.sent == [], "a person spoke while the screen was being read"
    assert k.sessions["s1"]["state"] == "busy"

    ref.clear()
    orca = Racing(ref, "send")
    k, clock = keeper(orca)
    ref.append(k)
    person(k)
    stop(k)
    clock.at(55)
    k.tick()
    orca.late.join()
    assert orca.sent == [keepalive.PING]
    assert k.sessions["s1"]["state"] == "busy", "the notice that waited was applied after the send"


def test_no_reply_within_ten_minutes_and_expiry_both_drop_the_session():
    k, clock = keeper()
    person(k)
    stop(k)
    clock.at(55)
    k.tick()
    clock.at(64)
    k.tick()
    assert "s1" in k.sessions
    clock.at(65)
    k.tick()
    assert "s1" not in k.sessions, "no ping turn ten minutes after the ping"

    k, clock = keeper()
    person(k)
    stop(k, limit=1)
    clock.at(55)
    k.tick()
    ping_turn(k)
    clock.at(56)
    stop(k, limit=1)
    assert k.sessions["s1"]["expires"] == clock() + 65 * MIN
    clock.at(56 + 65)
    k.tick()
    assert "s1" not in k.sessions, "past its expiry with no notice"


def test_the_idle_shutdown_waits_for_the_last_expiry_and_no_longer():
    daemon = searchd.Daemon("t", searchd.Embedder(None))
    clock = Clock()
    daemon.keeper = searchd.Keeper(clock, Orca())
    daemon.last = time.monotonic() - searchd.IDLE - 1
    assert daemon.finished()
    person(daemon.keeper)
    stop(daemon.keeper)
    assert not daemon.finished()
    clock.at(55 * 2 + 10)
    assert daemon.finished()


def test_the_input_box_reads_empty_only_in_its_own_shape():
    assert searchd.input_empty(IDLE_SCREEN)
    assert searchd.input_empty([*IDLE_SCREEN[:5]]), "no footer"
    assert not searchd.input_empty(IDLE_SCREEN[:3] + ["❯ half"] + IDLE_SCREEN[4:])
    assert not searchd.input_empty(IDLE_SCREEN[:3] + ["❯ first line", "  second"] + IDLE_SCREEN[4:])
    assert not searchd.input_empty(IDLE_SCREEN + ["PS C:\\work> "])
    assert not searchd.input_empty([])


def test_the_daemon_takes_notices_only_with_the_token():
    import http.client

    daemon = searchd.Daemon("secret", searchd.Embedder(None))
    server = searchd.serve(0, daemon)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for token, status in (("wrong", 403), ("secret", 200)):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("POST", "/own", body=json.dumps({"session": "s1", "handle": HANDLE}),
                         headers={"X-Wiki-Token": token})
            assert conn.getresponse().status == status
            conn.close()
        assert daemon.keeper.owners == {HANDLE: "s1"}
    finally:
        server.shutdown()
        server.server_close()
