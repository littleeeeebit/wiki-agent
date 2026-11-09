"""oneshot S4: the contract tests first, then the build, each checked by the server rather than by the agent's word.

`docs/plans/oneshot/3-spec-backend.md` ("S4 — Backend build") is the design.
The turns are the ordinary work session's (`work.session`), sent as a `Turn`.
`specs._check` hands a oneshot spec's turn here in the stages `tests` and `build`.
Nothing here pushes or opens a pull request.
"""

from __future__ import annotations

import json
import os
import threading
import time
import tomllib
import uuid
from fnmatch import fnmatchcase
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from . import oneshot, oneshot_spec, refactor_run, runtime, specs, work
from .oneshot_profile import allowed, charged, gate_cmd
from .query import ROOT, _lock as work_lock, hold

TESTS_PROMPT = (ROOT / "tool/prompts/oneshot-tests.md").read_text(encoding="utf-8")
BUILD_PROMPT = (ROOT / "tool/prompts/oneshot-build.md").read_text(encoding="utf-8")
MAX_ATTEMPTS = 3
GRANTS: set[str] = set()   # a stage whose turn may write the worktree's adapter, only while it runs
# A stage's turn another module checks while its condition on the spec holds: `(applies, check)`.
HANDOVERS: dict[str, tuple] = {}
CONFIG = ("pyproject.toml", "uv.lock", "pytest.ini")
RUN_SECONDS = 900
# The contract runs on its own files: no plugin loads itself from the environment, and no option comes in.
PYTEST_ENV = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": "", "PYTHONDONTWRITEBYTECODE": "1"}
# Each can change how a test runs: pytest's configuration files in its own search order, and a conftest.
PYTEST_FILES = ("pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg", "conftest.py")
PROBE = Path(__file__).with_name("oneshot_probe.py")   # node ids, a body that ran, the reason it failed


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
    path, codex = Path(spec["worktree"]), oneshot.codex_role()
    with oneshot._lock:
        why = allowed(spec["oneshot"].get("spend"), spec["oneshot"].get("profile"), stage, codex)
        if why:
            store.stop("budget", why)
    if why:
        idle(path)
        return
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
        cfg = oneshot.role_config()
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
        .replace("{gate}", gate_cmd(spec["oneshot"]["profile"]))


def build_text(spec: dict) -> str:
    o = spec["oneshot"]
    return BUILD_PROMPT.replace("{tests_head}", o["tests_head"]).replace("{gate}", gate_cmd(o["profile"])) \
        .replace("{contract}", "\n".join(f"- `{f}`" for f in o["contract"]))


def begin(repo: str) -> None:
    """After R1's approval: the tests-first turn."""

    send(repo, "tests", tests_text(oneshot_spec.spec_of(repo)))


def resume(repo: str) -> None:
    spec = oneshot_spec.saved(repo, oneshot={"attempts": 0})   # the owner's resume starts the attempts afresh
    stage = spec["oneshot"]["stage"]
    send(repo, stage, STAGES[stage][2](spec))


# -- What the server checks ------------------------------------------------------------

def is_test(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return name.startswith("tests/") or base == "conftest.py" or fnmatchcase(base, "test_*.py") \
        or fnmatchcase(base, "*_test.py")


def changed(path: Path, since: str) -> list[str]:
    """Paths differing from `since` in the worktree, committed or not, untracked included."""

    names = set()
    for args in (["diff", "--name-only", "--no-renames", "-z", since], ["ls-files", "--others", "--exclude-standard", "-z"]):
        done = specs.sh(["git", "-c", "core.quotepath=off", *args], path)
        if done.returncode:
            raise ValueError(f"git {args[0]} 실패 — {(done.stderr or '').strip()[-200:]}")
        names.update(n for n in done.stdout.split("\0") if n)
    return sorted(names)


def probe(path: Path, run, spec: dict, files, nodes=None) -> tuple[int | None, str, list[dict]]:
    """One pytest run of the contract `files` with `oneshot_probe` loaded: its exit code, output and rows.
    With `nodes`, only those node ids run; without, the run only collects."""

    folder = oneshot_spec.store(spec["repo"]).folder / "probe"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / PROBE.name).write_bytes(PROBE.read_bytes())   # the only module on the path this adds
    stamp = time.time_ns()
    out, wanted = folder / f"run-{stamp}.jsonl", folder / f"nodes-{stamp}.json"
    env = {**os.environ, **PYTEST_ENV, "ONESHOT_PROBE": str(out),
           "PYTHONPATH": os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH", "")) if p)}
    if nodes is not None:
        wanted.write_text(json.dumps(nodes), encoding="utf-8")
        env["ONESHOT_PROBE_NODES"] = str(wanted)
    # What pytest collects: the test modules only, never a data file the tests read; a run of chosen nodes,
    # the modules those nodes came from.
    modules = {n.split("::", 1)[0] for n in nodes} if nodes is not None else {
        f for f in files if fnmatchcase(f.rsplit("/", 1)[-1], "test_*.py") or fnmatchcase(f.rsplit("/", 1)[-1], "*_test.py")}
    quoted = " ".join(f'"{f}"' for f in sorted(modules))
    code, text, cut = specs.gate(f"{gate_cmd(spec['oneshot']['profile'])} -p no:cacheprovider -p oneshot_probe -q "
                                 f"{'' if nodes is not None else '--collect-only '}{quoted}", path, run.halt, env=env,
                                 timeout=RUN_SECONDS)
    if cut:
        raise ValueError(f"계약 테스트 실행이 끊겼다 ({cut}) — {text.strip()[-800:]}")
    try:
        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        rows = []
    return code, text, rows


def collected(path: Path, run, spec: dict, files: set[str]) -> dict[str, list[str]]:
    """Each planned test's node ids, parameter cases included, as pytest itself names them, from `files` alone."""

    code, out, rows = probe(path, run, spec, files)
    if code != 0 or not rows:
        raise ValueError(f"테스트 수집이 실패했다 ({code}) — {out.strip()[-800:]}")
    names, found = {t["test_name"] for t in planned(spec)}, {}
    for row in rows:
        if row["kind"] == "item" and row["node"].split("::", 1)[0] in files and row["name"] in names:
            found.setdefault(row["name"], []).append(row["node"])
    return found


def ran(path: Path, run, spec: dict, files, nodes: list[str]) -> dict[str, dict]:
    """Exactly these node ids in one run: `{node: {kind, entered, intact, missing_app, text}}`, where `kind` is
    `passed`, `failed`, `skipped` or `error` (setup or teardown). A node that did not run is absent."""

    _, out, rows = probe(path, run, spec, files, nodes)
    phases: dict[str, dict[str, dict]] = {}
    for row in rows:
        if row["kind"] == "report":
            phases.setdefault(row["node"], {})[row["when"]] = row
    found = {}
    for node, by in phases.items():
        broken = next((by[w] for w in ("setup", "teardown") if by.get(w, {}).get("outcome") == "failed"), None)
        call = by.get("call") or {}
        skipped = any(r.get("outcome") == "skipped" for r in by.values())   # teardown's too: nothing skipped counts
        if not (broken or skipped or call):
            continue
        found[node] = {"kind": "error" if broken else "skipped" if skipped else call["outcome"],
                       "entered": bool(call.get("entered")), "intact": all(r.get("intact") for r in by.values()),
                       "missing_app": bool(call.get("missing_app")), "text": (broken or call).get("text", "")}
    if not found and nodes:
        raise ValueError(f"계획된 테스트를 돌리지 못했다 — {out.strip()[-800:]}")
    return found


def red(nodes: dict[str, list[str]], spec: dict, found: dict[str, dict]) -> str:
    """Why the planned tests do not all fail, in their own bodies, for want of the application, or empty."""

    if missing := sorted(t["test_name"] for t in planned(spec) if not nodes.get(t["test_name"])):
        return "수집되지 않은 계획 테스트: " + ", ".join(missing)
    flat = [n for ids in nodes.values() for n in ids]
    kind = lambda n: found.get(n, {}).get("kind", "absent")   # noqa: E731
    for want, label in (("absent", "돌지 않은"), ("passed", "아직 구현이 없는데 통과하는 테스트는 아무것도 확인하지 않는다:"),
                        ("skipped", "건너뛴"), ("error", "준비(setup·teardown)에서 오류가 난")):
        if bad := [n for n in flat if kind(n) == want]:
            return f"{label} 계획 테스트: " + ", ".join(bad) + (
                f" — {found[bad[0]]['text'].strip()[-300:]}" if want == "error" else "")
    if bad := [n for n in flat if not (found[n]["entered"] and found[n]["intact"])]:
        return "본문이 돌지 않았거나 pytest 의 실행 방식이 바뀐 계획 테스트: " + ", ".join(bad)
    if other := [n for n in flat if not found[n]["missing_app"]]:
        return ("앱이 없어서가 아닌 이유로 실패한 계획 테스트: " + ", ".join(other)
                + f" — {found[other[0]]['text'].strip()[-300:]}. 앱은 테스트 본문에서 `from app.main import app` 으로 가져온다")
    return ""


def green(nodes: dict[str, list[str]], found: dict[str, dict]) -> str:
    """Why a node id pinned at the tests commit did not run its own body and pass, or empty."""

    def why(n):
        f = found.get(n)
        return ("돌지 않음" if f is None else f["kind"] if f["kind"] != "passed" else
                "본문이 돌지 않음" if not f["entered"] else "pytest 실행 방식이 바뀜" if not f["intact"] else "")

    bad = [f"{n}({w})" for ids in nodes.values() for n in ids if (w := why(n))]
    return "통과하지 못한 계획 테스트: " + ", ".join(bad) if bad else ""


def pinned(path: Path, o: dict) -> str:
    """Why the contract is no longer what was committed at `tests_head`, or empty: a contract file changed,
    or a pytest configuration that can change how the tests run was added or changed."""

    if specs.sh(["git", "merge-base", "--is-ancestor", o["tests_head"], "HEAD"], path).returncode:
        return f"HEAD 가 계약 테스트 커밋 {o['tests_head'][:12]} 위에 있지 않다"
    for name in o["contract"]:
        old = specs.sh(["git", "show", f"{o['tests_head']}:{name}"], path)
        try:
            new = (path / name).read_bytes()
        except OSError:
            return f"계약 테스트 `{name}` 가 없다 — 새 테스트는 새 파일에 쓴다"
        if old.returncode or not refactor_run.equivalent(name, old.stdout.encode("utf-8"), new):
            return f"계약 테스트 `{name}` 가 바뀌었다 — 계약 테스트는 고치지 않는다. 새 테스트는 새 파일에 쓴다"
    names = changed(path, o["tests_head"])
    # A package's `__init__.py` above a contract file runs before its tests do, as a conftest would.
    folder = lambda n: n.rsplit("/", 1)[0] if "/" in n else ""   # noqa: E731
    above = {folder(c) for c in o["contract"]}
    package = lambda n: n.rsplit("/", 1)[-1] == "__init__.py" and any(   # noqa: E731
        folder(n) in ("", d) or d.startswith(folder(n) + "/") for d in above)
    if touched := [n for n in names if (n.rsplit("/", 1)[-1] in PYTEST_FILES or package(n)) and n not in o["contract"]]:
        return "계약 테스트 커밋 뒤에 pytest 설정을 더하거나 바꿨다 — " + ", ".join(touched[:10])
    if "pyproject.toml" in names and pytest_table(specs.sh(["git", "show", f"{o['tests_head']}:pyproject.toml"], path)
                                                  .stdout) != pytest_table(read(path / "pyproject.toml")):
        return "계약 테스트 커밋 뒤에 `pyproject.toml` 의 `[tool.pytest]` 를 바꿨다"
    return ""


def read(file: Path) -> str:
    try:
        return file.read_text(encoding="utf-8")
    except OSError:
        return ""


def pytest_table(text: str):
    try:
        return tomllib.loads(text).get("tool", {}).get("pytest")
    except ValueError:
        return "unreadable"


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
    store.put(attempts=attempts, working=None)
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
    """Only tests and their configuration changed, they collect, and every planned test fails because the
    application is not there yet: then the server commits those files alone and pins them, with each planned
    test's node ids, at that commit."""

    o = spec["oneshot"]
    if specs.sh(["git", "merge-base", "--is-ancestor", o["design_head"], "HEAD"], path).returncode:
        return f"HEAD 가 승인된 설계 커밋 {o['design_head'][:12]} 위에 있지 않다"
    names = changed(path, o["design_head"])
    if other := [n for n in names if not is_test(n) and n not in CONFIG]:
        return "테스트 단계에서는 테스트와 테스트 설정만 쓴다 — " + ", ".join(other[:10])
    contract = sorted(n for n in names if is_test(n) and (path / n).is_file())
    before = (snapshot(path, names), head(path), names)
    nodes = collected(path, run, spec, set(contract))
    flat = [n for ids in nodes.values() for n in ids]
    if why := red(nodes, spec, ran(path, run, spec, contract, flat) if flat else {}):
        return why
    if (snapshot(path, names), head(path), changed(path, o["design_head"])) != before:
        return "계획 테스트를 돌리는 동안 작업트리가 바뀌었다"
    for args in (["add", "-A", "--", *names], ["commit", "-m", "Write the contract tests before the backend", "--", *names]):
        done = specs.sh(["git", *args], path)
        if done.returncode and "nothing to commit" not in done.stdout + done.stderr:
            raise ValueError(f"계약 테스트를 커밋하지 못했다 — {(done.stderr or done.stdout).strip()[-300:]}")
    if left := specs.sh(["git", "status", "--porcelain", "--", *names], path).stdout.strip():
        raise ValueError(f"계약 테스트 커밋 뒤에도 바뀐 파일이 남았다 — {left[:300]}")
    tests_head = head(path)
    scenarios = [f"{r['id']}: {r['text']}" for r in spec.get("requirements") or [] if r.get("postconditions")]
    oneshot_spec.saved(spec["repo"], done=[gate_cmd(o["profile"]), *scenarios],
                       oneshot={"stage": "build", "tests_head": tests_head, "contract": contract, "nodes": nodes,
                                "attempts": 0, "working": None})
    specs.note(run, f"계약 테스트 커밋 {tests_head[:12]} · 파일 {len(contract)}개 · 테스트 {len(flat)}개 고정")
    return ""


def head(path: Path) -> str:
    return specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip()


def built(path: Path, run, spec: dict, final: str) -> str:
    """A done-report meeting every item, the contract tests unchanged, the gate green on a clean HEAD, and
    every node id pinned at the tests commit passing: then the run moves to the backend review."""

    o = spec["oneshot"]
    if why := reported(spec, final) or pinned(path, o):
        return why
    verdict = specs.judge(path, [gate_cmd(o["profile"])], run.halt, lambda text: specs.note(run, text))
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
    """Empty when every node id pinned at the tests commit passes and the runs left HEAD and the worktree as
    the green gate saw them, or why not."""

    o = spec["oneshot"]
    nodes = o["nodes"]
    if why := green(nodes, ran(path, run, spec, o["contract"], [n for ids in nodes.values() for n in ids])):
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
