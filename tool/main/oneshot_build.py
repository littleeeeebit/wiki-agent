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
import shlex
import shutil
import subprocess
import threading
import time
import tomllib
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

from common import masked
from common.powershell_syntax import syntax
from fastapi import HTTPException

from . import connect, oneshot, oneshot_android, oneshot_calls, oneshot_needs, oneshot_spec, refactor_run, runtime, specs, work
from .oneshot_profile import charged, environment, held as kind_held, kinds_of, shape, tool_key, tooled
from .query import _lock as work_lock, hold

MAX_ATTEMPTS = 3
# A stage whose turn may write the worktree's adapter, only while it runs, by whether its spec's turn may.
GRANTS: dict[str, Callable[[dict], bool]] = {}
# A stage's turn another module checks while its condition on the spec holds: `(applies, check)`.
HANDOVERS: dict[str, tuple] = {}
RUN_SECONDS = 900
RUNNER = "oneshot-runner"   # the tests turn's block: the project's own test command and its files
REPORT = "artifacts/junit.xml"   # where the adapter's gate writes the report; the server's runs write elsewhere
# The block a stage's turn closes with besides a `done-report`: the tests turn's runner, the PR body's
# summary. Only that turn reads it as closing, so a background wait it left does not hold it for good.
CLOSES = {"tests": RUNNER, "pr": "pr-summary"}


class Turn(work.Run):
    """A work turn the oneshot sent: charged to the run's ledger when it ends,
    and a turn that ended without reaching its check stops the stage with the reason."""

    def __init__(self, chat, repo: str, stage: str, codex: bool) -> None:
        super().__init__(chat, task=True)
        self.repo, self.stage, self.codex, self.checked = repo, stage, codex, False
        self.token = uuid.uuid4().hex   # names this turn's `inflight`, so a late end clears only its own
        self.sender = threading.get_ident()   # `work.begin` ends a turn it could not start on this thread
        self.ended = threading.Event()
        # The session is the worktree's, the person's turns included: this turn's closing block is its own.
        block = CLOSES.get(stage)
        self.closes = chat.settled = (re.compile(rf"^```(?:done-report|disposition|{block})[ \t]*\r?$", re.M).search
                                      if block else work.SETTLING.search)
        threading.Thread(target=oneshot.heartbeat, args=(oneshot_spec.store(repo), self.ended), daemon=True,
                         name=f"oneshot-beat-{repo}").start()

    def settle(self) -> None:
        """The session's ordinary closing answers again, unless a later turn has set its own meanwhile."""

        if getattr(self.chat, "settled", None) is self.closes:
            self.chat.settled = work.SETTLING.search

    def finish(self) -> None:
        super().finish()
        self.settle()
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

        self.settle()
        self.ended.set()
        store = oneshot_spec.store(self.repo)
        with oneshot._lock:
            if self.stage in GRANTS:
                oneshot_spec.saved(self.repo, adapter=False)
            if (store.get().get("inflight") or {}).get("turn") == self.token:
                store.put(inflight=None)


def stopped_for_kind(repo: str) -> bool:
    """A run at or past its kind's stop, however it got here (an older version's `working` included): its stop
    is put back and nothing is sent. Checked before any stage text is built: a held kind may have no prompt."""

    o = oneshot_spec.spec_of(repo)["oneshot"]
    if kind_held(o):
        oneshot_spec.waiting(repo, oneshot_spec.saved(repo, oneshot={"working": None})["oneshot"])
    return kind_held(o)


def tools(o: dict) -> list[dict] | None:
    """A PC app's confirmed build tools, the only host programs its commands may start (plan 9e, "The
    toolchain, declared once"), or `None` for a run without one."""

    kinds = kinds_of(o)
    if "desktop" not in kinds:
        return None
    return next((s.get("tools") or [] for s in o.get("stack") or [] if tooled(s["part"], kinds)), [])


# A word ends at whitespace or the end: a quote right after it (`'a.cmd''/x'`, an escaped apostrophe to
# PowerShell) would join the emitted path to an unchecked suffix, so it reads as no word and is refused.
WORD = re.compile(r"\s*(\"[^\"]*\"|'[^']*'|[^\s\"']+)(?=\s|$)")


def word_of(part: str) -> tuple[str, str]:
    """A chain part's program word, unquoted, and the rest after it exactly as written, starting at whitespace.
    The check and the launch both read the program through this, so they cannot read two different words."""

    if not (m := WORD.match(part)):
        return "", part
    word = m[1][1:-1] if m[1][0] in "'\"" else m[1]   # only the outer pair: a quote inside is the name's own
    return word, part[m.end():]


def links(command: str) -> list[str]:
    """`command`'s chain parts, cut at each `&&` outside quoted text (a quoted label or the server's own report
    path may hold one)."""

    bare, cuts, start = syntax(command)[0], [], 0
    for m in re.finditer("&&", bare):
        cuts.append(command[start:m.start()])
        start = m.end()
    return [*cuts, command[start:]]


def target(path: Path, word: str, declared_tools: list[dict]) -> tuple[str, str] | None:
    """What `word` starts, or `None` for anything else.
    A declared tool named bare gives `("tool", its declared name)`.
    An existing file inside the worktree gives `("file", its absolute path)`."""

    names = {tool_key(t["name"]): t["name"] for t in declared_tools}
    if word and not re.search(r"[\\/]", word) and tool_key(word) in names:
        return "tool", names[tool_key(word)]
    file = (path / word).resolve()
    if word and file.is_relative_to(path.resolve()) and file.is_file():
        return "file", str(file)
    return None


def undeclared(path: Path, command: str, declared_tools: list[dict]) -> list[str]:
    """The programs in `command`'s chain that are neither a declared tool nor a file inside the worktree."""

    words = [word_of(part)[0] or part.strip() for part in links(command)]
    return [w for w in words if target(path, w, declared_tools) is None]


def resolve_tools(repo: str, stage: str) -> bool:
    """A PC app's declared tools, each found on `PATH` once for `stage` before its first command runs: saved,
    so the gates run the program checked. A tool not found stops the run with `toolchain`; nothing installs it."""

    o = oneshot_spec.spec_of(repo)["oneshot"]
    if (declared_tools := tools(o)) is None:
        return True
    # The name as S2 declared it, found from the server's folder and saved absolute: a relative `PATH` entry
    # would otherwise point at another file once the gate runs in the worktree.
    found = {t["name"]: (at := shutil.which(t["name"])) and os.path.abspath(at) for t in declared_tools}
    if missing := [name for name, at in found.items() if not at]:
        oneshot_spec.store(repo).stop("toolchain", f"PC 앱을 만드는 데 쓰기로 한 도구를 이 PC 에서 찾지 못했다: "
                                                   f"{', '.join(missing)} — 설치하고 [재개]를 누르면 다시 찾는다")
        return False
    oneshot_spec.saved(repo, oneshot={"toolpaths": {"stage": stage, "paths": found}})
    return True


def run_as(command: str, o: dict, path: Path) -> str:
    """`command` with every program it starts given as an explicit path: a declared tool's, found for this
    stage (`resolve_tools`), or a worktree file's, read again now, so the shell looks nothing up and the program
    checked is the program run. A program with neither is refused before anything runs. A run without a PC app
    runs the command as it is."""

    if (declared_tools := tools(o)) is None:
        return command
    paths, parts = (o.get("toolpaths") or {}).get("paths") or {}, []
    for part in links(command):
        word, rest = word_of(part)
        kind, name = target(path, word, declared_tools) or ("", "")
        if not (program := paths.get(name) if kind == "tool" else name):
            raise ValueError(f"`{word or part.strip()}` 는 확정한 도구도 작업트리 안의 파일도 아니거나, 이 단계에서 "
                             "찾은 경로가 없다 — 아무것도 실행하지 않았다")
        lead = part[:len(part) - len(part.lstrip())]   # PowerShell runs a quoted path only through `&`
        parts.append(lead + ("& " if os.name == "nt" else "") + literal(program) + rest)
    return "&&".join(parts)


def send(repo: str, stage: str, text: str) -> None:
    """One work turn for `stage` in the spec's worktree; a turn that cannot be sent stops the stage, and
    the instruction the person queued for the worktree goes then, as after a turn."""

    if stopped_for_kind(repo) or not resolve_tools(repo, stage):
        return
    store = oneshot_spec.store(repo)
    spec = oneshot_spec.spec_of(repo)
    path, codex = Path(spec["worktree"]), oneshot.codex_role("build", store)
    try:
        held = hold(work._busy, work_lock, str(path), "작업트리를 다른 턴이 쓰고 있다", kind="turn")
    except HTTPException as exc:   # whoever holds it sends the queued instruction when it lets go
        store.stop("block", str(exc.detail))
        return

    def release() -> None:   # the grant and the closing block go before the tree does: the next turn there,
        if turn is not None:   # a person's included, never finds them set (`work.run_turn` lets go first)
            turn.settle()
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
        if stage in GRANTS and GRANTS[stage](spec):   # set as the turn starts, a resumed one included; its end clears it
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


TOOLCHAIN = ("JAVA_HOME", "ANDROID_HOME", "ANDROID_USER_HOME", "GRADLE_USER_HOME")


def toolchain(spec: dict) -> dict | None:
    """The environment an Android run's tests and gate run in, or `None` for the server's own; either with the
    owner's secrets for the outside calls."""

    return oneshot_needs.gate_env(spec, oneshot_android.build_env() if shape(spec["oneshot"]) == "android" else None)


def built_env(spec: dict) -> dict | None:
    """The environment a gate that builds the app runs in (S7, S8): an Android app's, alone or among several
    kinds, since several kinds' gate builds the Android app on the core; else the server's own. Either has the
    owner's secrets for the outside calls."""

    return oneshot_needs.gate_env(spec, oneshot_android.build_env() if "android" in kinds_of(spec["oneshot"]) else None)


def toolchain_text(spec: dict) -> str:
    """The variables a turn sets for its own Android builds, as the server's runs set them."""

    try:
        env = oneshot_android.build_env()
    except (ValueError, OSError) as exc:
        return f"(the toolchain is not ready: {exc})"
    return "\n".join(f"- `{k}={env[k]}`" for k in TOOLCHAIN)


def tools_text(o: dict) -> str:
    """The rule a PC app's commands follow, with its declared tools, or empty for a run without a PC app."""

    if (declared_tools := tools(o)) is None:
        return ""
    names = "\n".join(f"- `{t['name']}`" for t in declared_tools)
    return ("The PC app's build declared these host programs, and they are the only ones its commands may start. "
            "Every command you give the server (`command`, and any after it) names its program first: one of "
            "these, or a file inside this worktree that the project holds or builds, such as `gradlew.bat` or "
            "`scripts/test.ps1`. A command starting any other program is refused and nothing runs. Install "
            f"nothing; if a tool is missing, say so in your answer.\n\n{names}\n")


def tests_text(spec: dict) -> str:
    kind = shape(spec["oneshot"])
    text = oneshot.prompt("tests", kind).replace("{tests}", json.dumps(planned(spec), ensure_ascii=False, indent=2)) \
        .replace("{profile}", environment(spec["oneshot"])).replace("{tools}", tools_text(spec["oneshot"])) \
        .replace("{needs}", oneshot_needs.told(spec))
    return text.replace("{toolchain}", toolchain_text(spec)) if kind == "android" else text


def build_text(spec: dict) -> str:
    o = spec["oneshot"]
    kind = shape(o)
    text = oneshot.prompt("build", kind).replace("{tests_head}", o["tests_head"]).replace("{gate}", o["gate"]) \
        .replace("{contract}", "\n".join(f"- `{f}`" for f in o["contract"])).replace("{tools}", tools_text(o)) \
        .replace("{needs}", oneshot_needs.told(spec))
    return text.replace("{toolchain}", toolchain_text(spec)) if kind == "android" else text


def begin(repo: str) -> None:
    """After R1's approval: the tests-first turn."""

    if not stopped_for_kind(repo):
        send(repo, "tests", tests_text(oneshot_spec.spec_of(repo)))


def resume(repo: str) -> None:
    if stopped_for_kind(repo):
        return
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


def declared(path: Path, final: str, kind: str = "webapp", declared_tools: list[dict] | None = None) -> dict:
    """The tests turn's `oneshot-runner` block, checked: one command that runs every test and writes a JUnit
    report to `{report}`, the files holding the contract tests, and the files configuring the tool. An Android
    app's block also names its lint and its debug APK build, which the gate runs after the tests. With a PC app
    among the kinds, every program the command starts is a declared tool or a file in the worktree."""

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
    after = []
    for key in ("lint", "apk") if kind == "android" else ():
        if not isinstance(value.get(key), str) or "{report}" in value[key] or not chained(value[key]):
            raise ValueError(f"`{key}` 는 스택의 {'린트' if key == 'lint' else '디버그 APK 빌드'} 명령이고, {CHAIN}")
        after.append(value[key].strip())
    if declared_tools is not None and (bad := undeclared(path, " && ".join([command, *after]), declared_tools)):
        raise ValueError(f"S2 에서 확정한 도구도 작업트리 안의 파일도 아닌 프로그램을 부른다: {', '.join(bad)} — 확정한 도구는 "
                         f"{', '.join(t['name'] for t in declared_tools) or '없음'} 이다. 다른 도구가 필요하면 그 도구로 바꿔 쓴다")
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
            "root": "" if folder == "." else folder, "after": after}


def junit(path: Path, run, spec: dict, command: str) -> list[tuple[str, str, str, str]]:
    """One run of `command`: every testcase of its JUnit report as `(classname, name, file, outcome)`, where
    `file` is empty when the report carries none and the outcome is `passed`, `failure`, `error` or `skipped`.
    The report goes to the run's folder, outside the tree."""

    folder = oneshot_spec.store(spec["repo"]).folder / "junit"
    folder.mkdir(parents=True, exist_ok=True)
    report, env = folder / f"run-{time.time_ns()}.xml", toolchain(spec)
    hide = oneshot_needs.hidden(spec, env)
    try:   # the report may quote a secret in a failure: read once and never kept
        _, text, cut = specs.gate(run_as(command.replace("{report}", literal(report.as_posix())), spec["oneshot"], path),
                                  path, run.halt, env=env, timeout=RUN_SECONDS, hide=hide)
        if cut:
            raise ValueError(f"계약 테스트 실행이 끊겼다 ({cut}) — {text.strip()[-800:]}")
        try:
            root = ElementTree.parse(report).getroot()
        except (OSError, ElementTree.ParseError):
            raise ValueError(f"테스트 명령이 `{{report}}` 에 JUnit 보고서를 쓰지 않았다 — {text.strip()[-800:]}") from None
    finally:
        report.unlink(missing_ok=True)
    found = oneshot_calls.listed(root)
    if leaked := sorted({label for c in found for v, label in hide.items() if any(v in f for f in c[:3])}):
        raise ValueError(f"테스트 사례 이름에 비밀 값이 들어 있다 — {', '.join(leaked)}: 값을 사례 이름에 넣지 말라")
    oneshot_calls.reached(spec, found)
    return found


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
SMART_QUOTES = re.compile("[‘’‚‛“”„]")   # quotes to PowerShell, not to `links`
# PowerShell's two quote families: a typographic quote closes a string its ASCII sibling opened.
FAMILY = {"'": re.compile("[‘’‚‛]"), '"': re.compile("[“”„]")}


def quote_safe(command: str) -> bool:
    """PowerShell and the masker read the same strings: no stop-parsing `--%`, after which PowerShell reads quotes
    as text; no typographic quote outside ASCII-quoted text; and none inside a string of its own family, which it
    could close (`'a’ && b` is two commands to PowerShell)."""

    if "--%" in command:   # after it PowerShell reads quotes as text, which the masker would take as strings
        return False
    bare, spans = syntax(command)
    if SMART_QUOTES.search(bare):
        return False
    for start, end in spans:
        opener = command[start + 1] if command[start] == "@" else command[start]   # a here-string's `@'` or `@"`
        if (family := FAMILY.get(opener)) and family.search(command, start, end):
            return False
    return True
CHAIN = ("평범한 명령을 ` && ` 로만 잇는다 — `;`, `|`, `#`, `` ` ``, `$(`, 괄호·중괄호, 홑 `&`, 둥근 따옴표, 줄바꿈 없이, "
         "어느 명령도 `exit`·`exec`·`eval`·`iex` 같은 셸 제어어로 시작하지 않는다")


def leaves(word: str) -> bool:
    """`word`, as the shell would find it, is one that leaves or replaces the shell: its quotes, folder or
    module and `.exe` set aside. An environment assignment in front (`CI=1 exec …`) counts as one too."""

    if "=" in word:
        return True
    name = re.split(r"[\\/]", word.strip("'\""))[-1].lower().removesuffix(".exe")
    return name in LEAVE


def commands(command: str) -> list[str]:
    """`command`'s chain parts as the shell that runs the gate cuts them, at an unquoted `&&`: on Windows `links`
    (the masker reads PowerShell); elsewhere as the POSIX shell quotes, a backslash escaping the next character
    outside single quotes. A quote left open raises ValueError."""

    if os.name == "nt":
        return links(command)
    parts, start, quote, i = [], 0, "", 0
    while i < len(command):
        c = command[i]
        if c == "\\" and quote != "'":
            i += 1
        elif quote:
            quote = "" if c == quote else quote
        elif c in "'\"":
            quote = c
        elif command.startswith("&&", i):
            parts.append(command[start:i])
            start, i = i + 2, i + 1
        i += 1
    if quote:
        raise ValueError("a quote left open")
    return [*parts, command[start:]]


def chained(command: str) -> bool:
    """`command` is plain commands joined by `&&` and nothing else: each runs only after the one before it
    passed, and none starts with a word that ends the shell early, so the chain's status is its first failure.
    On Windows, where the gate runs in PowerShell, the parts are those `links` cuts, at an unquoted `&&`, so a
    quoted argument holding `&&` stays one command, and a typographic quote that PowerShell could read as a
    delimiter and `links` does not (`quote_safe`) is refused, so the two readings cannot differ. Elsewhere the POSIX
    shell runs it, and the parts are cut at the `&&` it reads unquoted; a quote left open is refused.
    ponytail: by character and first word, not shell syntax: it stops a command that ends the chain by
    mistake, not one written to get past it; a quoted `;` or `(` is refused too, and a project script takes it."""

    if CONTROL.search(command):   # the cheap refusal first: the masker below never reads what it refuses
        return False
    windows = os.name == "nt"
    try:
        parts = commands(command)
        safe = not windows or quote_safe(command)
        words = [p.split() if windows else shlex.split(p) for p in parts]   # the word the POSIX shell runs
    except (RecursionError, ValueError):   # nesting deeper than the masker reads, a quote left open: unreadable
        return False
    return safe and all(w and not leaves(w[0]) for w in words)


def published(gate: str, backend: str) -> str:
    """Why the adapter's gate, which CI and the next session run, would not run the contract's command and
    fail with it, or empty: it is that command, or that command and then more after `&&`, all one chain."""

    gate = gate.strip()
    rest = gate[len(backend):] if gate.startswith(backend) else None
    if rest is None or rest and not rest.lstrip().startswith("&&") or not chained(gate) or not chained(backend):
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
    """Every turn in a oneshot spec's worktree until the oneshot publishes: none of them may reach the
    general path, which publishes a pull request, before that. After it the task is an ordinary one under
    review, and a repair turn's `spec-update` must reach `specs.revise`: dropped here, the person's scope
    decision never reached the reviewer, which refused the same finding against revision 1 every round."""

    return bool(spec.get("oneshot")) and spec["oneshot"].get("stage") != "done"


def checked(path: Path, run, spec: dict, final: str):
    """`specs._check`'s hand-over: the stage's check, then the next turn to send, as `specs` returns it.
    A person's turn, or a turn in a stage S4 does not own, changes nothing."""

    repo, stage = spec["repo"], spec["oneshot"]["stage"]
    if getattr(run, "person", False) or stage not in STAGES:   # scanned at the turn's end (`oneshot_needs.settle`)
        return None
    hide = getattr(run.chat, "hide", None)
    why = oneshot_needs.leaked(path, spec, hide, final)   # before any check reads it; again at the turn's end
    if not why and (hand := HANDOVERS.get(stage)) and hand[0](spec):
        return hand[1](path, run, spec, final)
    run.checked = True
    check, stop, _, after = STAGES[stage]
    try:
        why = why or check(path, run, spec, final)
    except (ValueError, OSError) as exc:
        why = str(exc)
    except Exception as exc:   # an unforeseen failure still stops with a reason, never leaves the stage running
        oneshot_spec.store(repo).stop("block", why := masked(f"{type(exc).__name__}: {exc}", hide))
        if hide:   # the caller logs and notes what it catches: it gets the masked reason, never the values
            raise RuntimeError(why) from None
        raise
    why = masked(why, hide)   # a check's reason can quote what the turn wrote: shown, kept and sent as each need
    if not why:
        run.passed = True   # what follows moves the run on: the turn's end scan (`oneshot_needs.settle`) gates it
        if kind_held(oneshot_spec.spec_of(repo)["oneshot"]):   # the pass left the run at its kind's stop: no next
            return None
        return unless_stopped(run, repo, lambda: after(repo))
    specs.note(run, why)
    attempts = int(spec["oneshot"].get("attempts") or 0) + 1
    store = oneshot_spec.store(repo)
    if attempts >= MAX_ATTEMPTS or run.halt.is_set():
        store.put(attempts=attempts)
        store.stop(stop, why)
        return None
    store.put(attempts=attempts)   # `working` stays until the turn goes: a restart in between is recovered
    # Masked as composed, as the model gets it: a value can run across the reason and the words around it.
    return unless_stopped(run, repo, lambda: send(repo, stage, masked(
        f"The server refused this {stage} turn: {why}\n\nFix it and finish the turn again "
        f"(attempt {attempts + 1} of {MAX_ATTEMPTS}).", hide)))


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
    runner = declared(path, final, shape(o), tools(o))   # several kinds: the core's tests alone, each kind's build is 9c's
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
    gate = " && ".join([runner["command"].replace("{report}", REPORT), *runner["after"]])
    adapter = path / ".wiki" / "adapter.toml"
    try:   # every table but `[slots]` is kept as written; one that is there but unreadable is never replaced
        old = adapter.read_bytes().decode("utf-8") if adapter.exists() else ""   # its line endings as they are
        slots = tomllib.loads(old).get("slots", {})
    except (OSError, ValueError) as exc:
        return f"`.wiki/adapter.toml` 를 읽지 못했다 — 고쳐 두면 게이트를 그 `[slots]` 에 적는다 — {exc}"
    adapter.parent.mkdir(parents=True, exist_ok=True)
    adapter.write_text(connect.adapter_text({**slots, "gate_cmd": gate}, old), encoding="utf-8", newline="\n")
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
    try:
        gate = run_as(o["gate"], o, path)   # the worktree as it is now: a file the build moved out is refused
    except ValueError as e:
        return str(e)
    verdict = judged(path, run, spec, [gate], toolchain(spec))
    if not verdict["ok"]:
        return f"게이트 실패 — {verdict['reason']}\n{verdict['tail']}"
    if why := held(path, run, spec, verdict):
        return why
    # `working` names the review before its worker starts: a server that dies in between leaves it for `recover`.
    oneshot_spec.saved(spec["repo"], oneshot={"stage": "review", "built_head": verdict["head"], "attempts": 0,
                                              "working": "review"})
    specs.note(run, f"백엔드 빌드 통과 · {verdict['head'][:12]}")
    return ""


def judged(path: Path, run, spec: dict, commands: list[str], env: dict | None, **kw) -> dict:
    """`specs.judge` of `commands` in `env` as the server runs a gate: the output shows no secret `env` carries, and
    the adapter's JUnit report, which may quote one, is written outside the tree and deleted after."""

    report = oneshot_spec.store(spec["repo"]).folder / "junit" / f"gate-{time.time_ns()}.xml"
    report.parent.mkdir(parents=True, exist_ok=True)
    try:
        verdict = specs.judge(path, [c.replace(REPORT, literal(report.as_posix())) for c in commands], run.halt,
                              lambda text: specs.note(run, text), env=env, hide=oneshot_needs.hidden(spec, env), **kw)
        oneshot_calls.ran(spec, report)
        return verdict
    finally:
        report.unlink(missing_ok=True)


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
