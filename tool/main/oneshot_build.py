"""oneshot S4: the contract tests first, then the build, each checked by the server rather than by the agent's word.

`docs/plans/oneshot/3-spec-backend.md` ("S4 — Backend build") is the design.
The turns are the ordinary work session's (`work.session`), sent as a `Turn`.
`specs._check` hands a oneshot spec's turn here in the stages `tests` and `build`.
Nothing here pushes or opens a pull request.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import subprocess
import threading
import time
import tomllib
import uuid
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

from fastapi import HTTPException

from . import connect, oneshot, oneshot_spec, refactor_run, runtime, specs, work
from .oneshot_profile import charged
from .query import ROOT, _lock as work_lock, hold

TESTS_PROMPT = (ROOT / "tool/prompts/oneshot-tests.md").read_text(encoding="utf-8")
BUILD_PROMPT = (ROOT / "tool/prompts/oneshot-build.md").read_text(encoding="utf-8")
MAX_ATTEMPTS = 3
GRANTS: set[str] = set()   # a stage whose turn may write the worktree's adapter, only while it runs
# A stage's turn another module checks while its condition on the spec holds: `(applies, check)`.
HANDOVERS: dict[str, tuple] = {}
RUN_SECONDS = 900
RUNNER = "oneshot-runner"   # the tests turn's block: the project's own test command and its files
REPORT = "artifacts/junit.xml"   # where the adapter's gate writes the report; the server's runs write elsewhere


class Turn(work.Run):
    """A work turn the oneshot sent: charged to the run's ledger when it ends,
    and a turn that ended without reaching its check stops the stage with the reason."""

    def __init__(self, chat, repo: str, stage: str, codex: bool) -> None:
        super().__init__(chat, task=True)
        self.repo, self.stage, self.codex, self.checked = repo, stage, codex, False
        self.token = uuid.uuid4().hex   # names this turn's `inflight`, so a late end clears only its own
        self.sender = threading.get_ident()   # `work.begin` ends a turn it could not start on this thread
        self.ended = threading.Event()
        threading.Thread(target=oneshot.heartbeat, args=(oneshot_spec.store(repo), self.ended), daemon=True,
                         name=f"oneshot-beat-{repo}").start()

    def finish(self) -> None:
        super().finish()
        if threading.get_ident() == self.sender:   # never started: no model ran, so nothing is charged
            self.unsent()
            return
        self.ended.set()
        done = next((e for e in self.events if e["kind"] == "done"), None) or {}
        error = next((e["text"] for e in reversed(self.events) if e["kind"] == "error"), "") or "완료된 답이 없다"
        store = oneshot_spec.store(self.repo)
        with oneshot._lock:
            current = store.get()
            mine = (current.get("inflight") or {}).get("turn") == self.token
            store.put(**({"inflight": None} if mine else {}),
                      spend=charged(current.get("spend"), self.stage, self.codex,
                                    (done.get("meta") or {}).get("cost_usd"), time.time() - self.started_at))
            if not self.checked and mine and current.get("working") == self.stage:
                store.stop("restart" if runtime.stopping.is_set() else "block", f"작업 턴이 멈췄다 — {error}")

    def unsent(self) -> None:
        """`work.begin` refused the turn before it took it: no model ran, so nothing is charged."""

        self.ended.set()
        store = oneshot_spec.store(self.repo)
        with oneshot._lock:
            if self.stage in GRANTS:
                oneshot_spec.saved(self.repo, adapter=False)
            if (store.get().get("inflight") or {}).get("turn") == self.token:
                store.put(inflight=None)


def send(repo: str, stage: str, text: str) -> None:
    """One work turn for `stage` in the spec's worktree; a turn that cannot be sent stops the stage, and
    the instruction the person queued for the worktree goes then, as after a turn."""

    store = oneshot_spec.store(repo)
    spec = oneshot_spec.spec_of(repo)
    path, codex = Path(spec["worktree"]), oneshot.codex_role("build", store)
    try:
        held = hold(work._busy, work_lock, str(path), "작업트리를 다른 턴이 쓰고 있다", kind="turn")
    except HTTPException as exc:   # whoever holds it sends the queued instruction when it lets go
        store.stop("block", str(exc.detail))
        return

    def release() -> None:   # the grant goes before the tree does: the next turn there never finds it set
        if stage in GRANTS:
            oneshot_spec.saved(repo, adapter=False)
        held()

    release.held = held.held
    turn = None
    try:
        cfg = oneshot.role_config("build", store)
        chat = work.session(path, cfg["model"], cfg["effort"])
        turn = Turn(chat, repo, stage, codex)
        store.put(working=stage, stopped=None,
                  inflight={"role": stage, "codex": codex, "sent": time.time(), "turn": turn.token})
        if stage in GRANTS:   # set as the turn starts, a resumed one included; the turn's end clears it
            oneshot_spec.saved(repo, adapter=True)
        work.begin(path, chat, text, release, turn)
    except BaseException as exc:
        if turn is not None:
            turn.unsent()
        release()
        store.stop("restart" if runtime.stopping.is_set() else "block",
                   f"작업 턴을 보내지 못했다 — {getattr(exc, 'detail', exc)}")
        idle(path)


def idle(path: Path) -> None:
    """No turn was sent: the instruction queued for the worktree goes now, unless the server is stopping."""

    work.dispatch(path, SimpleNamespace(halt=runtime.stopping, turn=""))


def planned(spec: dict) -> list[dict]:
    return [{k: t[k] for k in ("id", "test_name", "given", "when", "then", "depends_on") if k in t}
            for t in spec["oneshot"]["design"]["tests"]]


def tests_text(spec: dict) -> str:
    return TESTS_PROMPT.replace("{tests}", json.dumps(planned(spec), ensure_ascii=False, indent=2)) \
        .replace("{profile}", json.dumps(spec["oneshot"]["profile"], ensure_ascii=False))


def build_text(spec: dict) -> str:
    o = spec["oneshot"]
    return BUILD_PROMPT.replace("{tests_head}", o["tests_head"]).replace("{gate}", o["gate"]) \
        .replace("{contract}", "\n".join(f"- `{f}`" for f in o["contract"]))


def begin(repo: str) -> None:
    """After R1's approval: the tests-first turn."""

    send(repo, "tests", tests_text(oneshot_spec.spec_of(repo)))


def resume(repo: str) -> None:
    spec = oneshot_spec.saved(repo, oneshot={"attempts": 0})   # the owner's resume starts the attempts afresh
    stage = spec["oneshot"]["stage"]
    send(repo, stage, STAGES[stage][2](spec))


# -- What the server checks ------------------------------------------------------------

def changed(path: Path, since: str) -> list[str]:
    """Paths differing from `since` in the worktree, committed or not, untracked included."""

    names = set()
    for args in (["diff", "--name-only", "--no-renames", "-z", since], ["ls-files", "--others", "--exclude-standard", "-z"]):
        done = specs.sh(["git", "-c", "core.quotepath=off", *args], path)
        if done.returncode:
            raise ValueError(f"git {args[0]} 실패 — {(done.stderr or '').strip()[-200:]}")
        names.update(n for n in done.stdout.split("\0") if n)
    return sorted(names)


def declared(path: Path, final: str) -> dict:
    """The tests turn's `oneshot-runner` block, checked: one command that runs every test and writes a JUnit
    report to `{report}`, the files holding the contract tests, and the files configuring the tool."""

    value = oneshot.block(final, RUNNER)
    if not isinstance(value, dict):
        raise ValueError(f"`{RUNNER}` 블록은 command, contract, config 를 담은 객체다")
    command, contract, config = value.get("command"), value.get("contract"), value.get("config", [])
    folder = value.get("root", "")
    if not isinstance(command, str) or command.count("{report}") != 1:
        raise ValueError("`command` 는 JUnit 보고서 경로 자리 `{report}` 를 한 번 담은 명령이다")
    # The published gate appends ` && …` to this command: anything that ends the shell here would skip the rest.
    if not chained(command.replace("{report}", REPORT)):
        raise ValueError(f"`command` 는 {CHAIN}")
    if not (isinstance(contract, list) and contract and isinstance(config, list)
            and all(isinstance(f, str) and f for f in contract + config)):
        raise ValueError("`contract` 는 비지 않은 파일 목록, `config` 는 파일 목록이다")
    root = path.resolve()
    if bad := [f for f in contract + config if not (path / f).resolve().is_relative_to(root) or not (path / f).is_file()]:
        raise ValueError("`" + RUNNER + "` 가 적은 파일이 작업트리에 없다 — " + ", ".join(bad[:10]))
    if not isinstance(folder, str) or not (path / folder).resolve().is_relative_to(root) or not (path / folder).is_dir():
        raise ValueError(f"`root` 는 보고서의 `file` 경로가 기준으로 삼는 작업트리 안의 폴더다 — {folder}")
    folder = (path / folder).resolve().relative_to(root).as_posix()
    return {"command": command.strip(), "contract": sorted(set(contract)), "config": sorted(set(config) - set(contract)),
            "root": "" if folder == "." else folder}


def junit(path: Path, run, spec: dict, command: str) -> list[tuple[str, str, str, str]]:
    """One run of `command`: every testcase of its JUnit report as `(classname, name, file, outcome)`, where
    `file` is empty when the report carries none and the outcome is `passed`, `failure`, `error` or `skipped`.
    The report goes to the run's folder, outside the tree."""

    folder = oneshot_spec.store(spec["repo"]).folder / "junit"
    folder.mkdir(parents=True, exist_ok=True)
    report = folder / f"run-{time.time_ns()}.xml"
    _, text, cut = specs.gate(command.replace("{report}", literal(report.as_posix())), path, run.halt, timeout=RUN_SECONDS)
    if cut:
        raise ValueError(f"계약 테스트 실행이 끊겼다 ({cut}) — {text.strip()[-800:]}")
    try:
        root = ElementTree.parse(report).getroot()
    except (OSError, ElementTree.ParseError):
        raise ValueError(f"테스트 명령이 `{{report}}` 에 JUnit 보고서를 쓰지 않았다 — {text.strip()[-800:]}") from None
    return [(case.get("classname") or "", case.get("name") or "", case.get("file") or "",
             # A skip anywhere, a cleanup's included, outranks the rest: nothing skipped counts either way.
             next((k for k in ("skipped", "error", "failure") if case.find(k) is not None), "passed"))
            for case in root.iter("testcase")]


QUOTES = "'‘’‚‛"   # every character PowerShell reads as a single quote


def literal(text: str) -> str:
    """`text` as one word the gate's shell expands nothing in: single quotes, in PowerShell on Windows and in
    bash elsewhere (`shell.launch`), each escaping a quote its own way. PowerShell doubles any quote it knows."""

    if os.name == "nt":
        return "'" + "".join(c * 2 if c in QUOTES else c for c in text) + "'"
    return "'" + text.replace("'", "'\\''") + "'"


# What lets a command end, hide or outlive the chain it is in, in PowerShell or bash: separators, pipes,
# comments, escapes, substitutions, groups and blocks, a lone `&`, and the words that leave or replace the shell.
CONTROL = re.compile(r"[\r\n;|#`(){}]|\$\(|&&&|(?<!&)&(?!&)")
LEAVE = {"exit", "exec", "return", "break", "continue", "eval", "source", ".", "iex", "invoke-expression",
         "trap", "logout", "kill", "stop-process", "set", "command", "builtin", "time", "enable", "shopt",
         "sal", "set-alias", "nal", "new-alias", "exsn", "exit-pssession"}
CHAIN = ("평범한 명령을 ` && ` 로만 잇는다 — `;`, `|`, `#`, `` ` ``, `$(`, 괄호·중괄호, 홑 `&`, 줄바꿈 없이, "
         "어느 명령도 `exit`·`exec`·`eval`·`iex` 같은 셸 제어어로 시작하지 않는다")


def leaves(word: str) -> bool:
    """`word`, as the shell would find it, is one that leaves or replaces the shell: its quotes, folder or
    module and `.exe` set aside. An environment assignment in front (`CI=1 exec …`) counts as one too."""

    if "=" in word:
        return True
    name = re.split(r"[\\/]", word.strip("'\""))[-1].lower().removesuffix(".exe")
    return name in LEAVE


def chained(command: str) -> bool:
    """`command` is plain commands joined by `&&` and nothing else: each runs only after the one before it
    passed, and none starts with a word that ends the shell early, so the chain's status is its first failure.
    ponytail: by character and first word, not shell syntax: it stops a command that ends the chain by
    mistake, not one written to get past it; a quoted `;` or `(` is refused too, and a project script takes it."""

    parts = command.split("&&")
    return not CONTROL.search(command) and all(p.split() and not leaves(p.split()[0]) for p in parts)


def published(gate: str, backend: str) -> str:
    """Why the adapter's gate, which CI and the next session run, would not run the contract's command and
    fail with it, or empty: it is that command, or that command and then more after `&&`, all one chain."""

    gate = gate.strip()
    rest = gate[len(backend):] if gate.startswith(backend) else None
    if rest is None or rest and not rest.lstrip().startswith("&&") or not chained(gate):
        return f"어댑터의 `gate_cmd` 는 계약 테스트 `{backend}` 로 시작하고, 그 뒤로 {CHAIN} — `{gate}`"
    return ""


def named(name: str, case: str) -> bool:
    """A testcase's name is the planned `name`, in any case, or a parameter case of it (`name[…]`, `name (…)`)."""

    case, name = case.lower(), name.lower()
    return case == name or (case.startswith(name + "[") and case.endswith("]")
                            or case.startswith(name + " (") and case.endswith(")"))


def source(path: Path, file: str, root: str) -> str:
    """A report's `file`, as a worktree path. An absolute one is made relative; a relative one is read from
    `root`, the folder the runner block says its tool names files from (`web` for `cd web && …`)."""

    p = Path(file.replace("\\", "/"))
    if not p.is_absolute():
        return posixpath.normpath(posixpath.join(root, p.as_posix()))
    try:
        return p.resolve().relative_to(path.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def red(spec: dict, found: list[tuple[str, str, str, str]]) -> str:
    """Why the planned tests do not all fail now, before any application exists, or empty."""

    names = [t["test_name"] for t in planned(spec)]
    if missing := [n for n in names if not any(named(n, c[1]) for c in found)]:
        return "JUnit 보고서에 없는 계획 테스트: " + ", ".join(missing) + " — 테스트 이름이 `test_name` 그대로여야 한다"
    for want, label in (("passed", "아직 구현이 없는데 통과하는 테스트는 아무것도 확인하지 않는다:"), ("skipped", "건너뛴")):
        if bad := sorted({c[1] for c in found if c[3] == want and any(named(n, c[1]) for n in names)}):
            return f"{label} 계획 테스트: " + ", ".join(bad)
    keys = [c[:3] for c in found if any(named(n, c[1]) for n in names)]
    if twice := sorted({"::".join(filter(None, k)) for k in keys if keys.count(k) > 1}):
        return "보고서가 구별하지 못하는 계획 테스트 사례(같은 classname·name·file): " + ", ".join(twice)
    return ""


def cases(spec: dict, found: list[tuple[str, str, str, str]]) -> list[list[str]]:
    """Each planned test's cases, `[classname, name, file]`, as the report names them: what the build must pass."""

    names = [t["test_name"] for t in planned(spec)]
    return sorted({c[:3] for c in found if any(named(n, c[1]) for n in names)})


def green(pins: list[list[str]], found: list[tuple[str, str, str, str]]) -> str:
    """Why a case pinned at the tests commit did not run and pass exactly once, or empty. A same-named test
    in another class, or in another file the report names, never stands in for it."""

    by: dict[tuple[str, ...], list[str]] = {}
    for *key, kind in found:
        by.setdefault(tuple(key), []).append(kind)
    bad = [f"{'::'.join(filter(None, pin))}({'돌지 않음' if not k else '두 번 이상 보고됨' if len(k) > 1 else k[0]})"
           for pin in pins if (k := by.get(tuple(pin), [])) != ["passed"]]
    return "통과하지 못한 계획 테스트: " + ", ".join(bad) if bad else ""


def pinned(path: Path, o: dict) -> str:
    """Why the contract is no longer what was committed at `tests_head`, or empty: a contract test file or a
    file the tests turn named as the test tool's configuration changed."""

    if specs.sh(["git", "merge-base", "--is-ancestor", o["tests_head"], "HEAD"], path).returncode:
        return f"HEAD 가 계약 테스트 커밋 {o['tests_head'][:12]} 위에 있지 않다"
    for name in o["contract"]:
        try:
            new = (path / name).read_bytes()
        except OSError:
            return f"계약 테스트 `{name}` 가 없다 — 새 테스트는 새 파일에 쓴다"
        # The blob as `git add` would write it now, filters and line endings included, then the same file in
        # another form; the committed bytes, never text decoded on the way.
        blob = specs.sh(["git", "rev-parse", f"{o['tests_head']}:{name}"], path).stdout.strip()
        now = specs.sh(["git", "hash-object", "--", name], path).stdout.strip()
        old = subprocess.run(["git", "cat-file", "blob", blob], cwd=path, capture_output=True).stdout if blob else None
        if not blob or now != blob and not refactor_run.equivalent(name, old, new):
            return f"계약 테스트 `{name}` 가 바뀌었다 — 계약 테스트와 그 설정은 고치지 않는다. 새 테스트는 새 파일에 쓴다"
    return ""


def snapshot(path: Path, names: list[str]) -> dict[str, bytes | None]:
    return {n: (path / n).read_bytes() if (path / n).is_file() else None for n in names}


# -- The two checks --------------------------------------------------------------------

def owns(spec: dict) -> bool:
    """Every turn in a oneshot spec's worktree: none of them may reach the general path, which
    publishes a pull request, before the oneshot itself publishes."""

    return bool(spec.get("oneshot"))


def checked(path: Path, run, spec: dict, final: str):
    """`specs._check`'s hand-over: the stage's check, then the next turn to send, as `specs` returns it.
    A person's turn, or a turn in a stage S4 does not own, changes nothing."""

    repo, stage = spec["repo"], spec["oneshot"]["stage"]
    if getattr(run, "person", False) or stage not in STAGES:
        return None
    if (hand := HANDOVERS.get(stage)) and hand[0](spec):
        return hand[1](path, run, spec, final)
    run.checked = True
    check, stop, _, after = STAGES[stage]
    try:
        why = check(path, run, spec, final)
    except (ValueError, OSError) as exc:
        why = str(exc)
    except Exception as exc:   # an unforeseen failure still stops with a reason, never leaves the stage running
        oneshot_spec.store(repo).stop("block", f"{type(exc).__name__}: {exc}")
        raise
    if not why:
        return unless_stopped(run, repo, lambda: after(repo))
    specs.note(run, why)
    attempts = int(spec["oneshot"].get("attempts") or 0) + 1
    store = oneshot_spec.store(repo)
    if attempts >= MAX_ATTEMPTS or run.halt.is_set():
        store.put(attempts=attempts)
        store.stop(stop, why)
        return None
    store.put(attempts=attempts)   # `working` stays until the turn goes: a restart in between is recovered
    return unless_stopped(run, repo, lambda: send(
        repo, stage, f"The server refused this {stage} turn: {why}\n\nFix it and finish the turn again "
                     f"(attempt {attempts + 1} of {MAX_ATTEMPTS})."))


def unless_stopped(run, repo: str, then):
    """The next turn, sent only if no stop arrived by the time `work.run_turn` sends it: a stop pressed
    after the check, while the turn was being recorded, leaves the stage resumable instead."""

    def next_turn():
        if run.halt.is_set() or runtime.stopping.is_set():
            oneshot_spec.store(repo).stop("restart" if runtime.stopping.is_set() else "block",
                                          "다음 턴을 보내기 전에 멈췄다 — [재개] 로 이어 간다")
            return
        then()

    return next_turn


def tested(path: Path, run, spec: dict, final: str = "") -> str:
    """The turn names its test command and files, and every planned test fails in that command's JUnit report
    because the application is not there yet: then the server writes the command into the adapter as the gate,
    commits the turn's files, and pins the contract files, the tool's configuration and each planned case."""

    o = spec["oneshot"]
    if specs.sh(["git", "merge-base", "--is-ancestor", o["design_head"], "HEAD"], path).returncode:
        return f"HEAD 가 승인된 설계 커밋 {o['design_head'][:12]} 위에 있지 않다"
    runner = declared(path, final)
    names = changed(path, o["design_head"])
    # A case is pinned by its class and name, its file only when named: a planned test outside the named files
    # could be weakened later with nothing to show for it.
    tests = [t["test_name"].lower() for t in planned(spec)]
    mentions = lambda f: (path / f).is_file() and any(   # noqa: E731
        t in (path / f).read_bytes().decode("utf-8", "ignore").lower() for t in tests)
    if loose := [f for f in names if f not in runner["contract"] + runner["config"] and mentions(f)]:
        return "계획 테스트 이름이 든 파일은 `contract` 에 적는다 — " + ", ".join(loose[:10])
    before = (snapshot(path, names), head(path), names)
    found = junit(path, run, spec, runner["command"])
    if why := red(spec, found):
        return why
    # A file the report names for a planned case is the case's file: one left out of `contract` is not pinned.
    if outside := sorted({source(path, c[2], runner["root"]) for c in found
                          if c[2] and any(named(t, c[1]) for t in tests)}
                         - set(runner["contract"])):
        return "계획 테스트 사례가 `contract` 에 없는 파일에서 나왔다 — " + ", ".join(outside[:10])
    if (snapshot(path, names), head(path), changed(path, o["design_head"])) != before:
        return "계획 테스트를 돌리는 동안 작업트리가 바뀌었다"
    gate = runner["command"].replace("{report}", REPORT)
    adapter = path / ".wiki" / "adapter.toml"
    try:
        slots = tomllib.loads(adapter.read_text(encoding="utf-8")).get("slots", {})
    except (OSError, ValueError):
        slots = {}
    adapter.parent.mkdir(parents=True, exist_ok=True)
    adapter.write_text(connect.adapter_text({**slots, "gate_cmd": gate}), encoding="utf-8", newline="\n")
    names = sorted({*names, ".wiki/adapter.toml"})
    for args in (["add", "-A", "--", *names], ["commit", "-m", "Write the contract tests before the backend", "--", *names]):
        done = specs.sh(["git", *args], path)
        if done.returncode and "nothing to commit" not in done.stdout + done.stderr:
            raise ValueError(f"계약 테스트를 커밋하지 못했다 — {(done.stderr or done.stdout).strip()[-300:]}")
    if left := specs.sh(["git", "status", "--porcelain", "--", *names], path).stdout.strip():
        raise ValueError(f"계약 테스트 커밋 뒤에도 바뀐 파일이 남았다 — {left[:300]}")
    tests_head, pins = head(path), cases(spec, found)
    scenarios = [f"{r['id']}: {r['text']}" for r in spec.get("requirements") or [] if r.get("postconditions")]
    oneshot_spec.saved(spec["repo"], done=[gate, *scenarios],
                       # the record is version 2's now, whatever version the run started at
                       oneshot={"version": max(o.get("version", 1), 2), "stage": "build", "tests_head": tests_head, "contract": runner["contract"] + runner["config"],
                                "runner": runner["command"], "gate": gate, "cases": pins, "attempts": 0, "working": None})
    specs.note(run, f"계약 테스트 커밋 {tests_head[:12]} · 파일 {len(runner['contract'])}개 · 테스트 {len(pins)}개 고정")
    return ""


def head(path: Path) -> str:
    return specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip()


def built(path: Path, run, spec: dict, final: str) -> str:
    """A done-report meeting every item, the contract tests unchanged, the gate green on a clean HEAD, and
    every node id pinned at the tests commit passing: then the run moves to the backend review."""

    o = spec["oneshot"]
    if why := reported(spec, final) or pinned(path, o):
        return why
    verdict = specs.judge(path, [o["gate"]], run.halt, lambda text: specs.note(run, text))
    if not verdict["ok"]:
        return f"게이트 실패 — {verdict['reason']}\n{verdict['tail']}"
    if why := held(path, run, spec, verdict):
        return why
    # `working` names the review before its worker starts: a server that dies in between leaves it for `recover`.
    oneshot_spec.saved(spec["repo"], oneshot={"stage": "review", "built_head": verdict["head"], "attempts": 0,
                                              "working": "review"})
    specs.note(run, f"백엔드 빌드 통과 · {verdict['head'][:12]}")
    return ""


def held(path: Path, run, spec: dict, verdict: dict) -> str:
    """Empty when every case pinned at the tests commit passes in a run of the pinned command, and the run left
    HEAD and the worktree as the green gate saw them, or why not."""

    o = spec["oneshot"]
    if why := green(o["cases"], junit(path, run, spec, o["runner"])):
        return why
    if head(path) != verdict["head"] or specs.dirty(path):
        return "계획 테스트를 돌리는 동안 작업트리가 바뀌었다"
    return ""


def reported(spec: dict, final: str) -> str:
    """Empty when the answer's `done-report` meets every item of the spec's `done`, or why not."""

    report = next((b for b in specs.blocks(final)[1] if b["name"] == "done-report"), None)
    if report is None or "error" in report or not specs.valid(report.get("value")):
        return "완료 보고(`done-report`)가 없거나 형식이 틀렸다 — 항목마다 item 과 pass 를 적는다"
    return specs.shortfall(report["value"], spec) or ""


def review_module():
    from . import oneshot_review   # it imports this module

    return oneshot_review


def reviewed(repo: str) -> None:
    review_module().begin(repo)


# A stage's turn: `(check, stop reason after the last refusal, the turn's text, what follows a pass)`.
# A later stage's module adds its own.
STAGES = {"tests": (tested, "tests", tests_text, lambda repo: send(repo, "build", build_text(oneshot_spec.spec_of(repo)))),
          "build": (built, "gate", lambda spec: review_module().build_turn(spec), reviewed)}
