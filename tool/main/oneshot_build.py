"""oneshot S4: the contract tests first, then the build, each checked by the server rather than by the agent's word.

`docs/plans/oneshot/3-spec-backend.md` ("S4 — Backend build") is the design.
The turns are the ordinary work session's (`work.session`), sent as a `Turn`.
`specs._check` hands a oneshot spec's turn here in the stages `tests` and `build`.
Nothing here pushes or opens a pull request.
"""

from __future__ import annotations

import json
import threading
import time
import xml.etree.ElementTree as ET
from fnmatch import fnmatchcase
from pathlib import Path

from fastapi import HTTPException

from . import oneshot, oneshot_spec, refactor_run, runtime, specs, work
from .oneshot_profile import allowed, charged, gate_cmd
from .query import ROOT, _lock as work_lock, hold

TESTS_PROMPT = (ROOT / "tool/prompts/oneshot-tests.md").read_text(encoding="utf-8")
BUILD_PROMPT = (ROOT / "tool/prompts/oneshot-build.md").read_text(encoding="utf-8")
STAGES = ("tests", "build")
MAX_ATTEMPTS = 3
STOPS = {"tests": "tests", "build": "gate"}
CONFIG = ("pyproject.toml", "uv.lock", "pytest.ini")
RUN_SECONDS = 900


class Turn(work.Run):
    """A work turn the oneshot sent: charged to the run's ledger when it ends,
    and a turn that ended without reaching its check stops the stage with the reason."""

    def __init__(self, chat, repo: str, stage: str, codex: bool) -> None:
        super().__init__(chat, task=True)
        self.repo, self.stage, self.codex, self.checked = repo, stage, codex, False
        self.ended = threading.Event()
        threading.Thread(target=oneshot.heartbeat, args=(oneshot_spec.store(repo), self.ended), daemon=True,
                         name=f"oneshot-beat-{repo}").start()

    def finish(self) -> None:
        super().finish()
        self.ended.set()
        done = next((e for e in self.events if e["kind"] == "done"), None) or {}
        error = next((e["text"] for e in reversed(self.events) if e["kind"] == "error"), "") or "완료된 답이 없다"
        store = oneshot_spec.store(self.repo)
        with oneshot._lock:
            current = store.get()
            store.put(inflight=None, spend=charged(current.get("spend"), self.stage, self.codex,
                                                   (done.get("meta") or {}).get("cost_usd"),
                                                   time.time() - self.started_at))
            if not self.checked and current.get("working") == self.stage:
                store.stop("restart" if runtime.stopping.is_set() else "block", f"작업 턴이 멈췄다 — {error}")


def send(repo: str, stage: str, text: str) -> None:
    """One work turn for `stage` in the spec's worktree; a turn that cannot be sent stops the stage."""

    store = oneshot_spec.store(repo)
    spec = oneshot_spec.spec_of(repo)
    path, codex = Path(spec["worktree"]), oneshot.codex_role()
    with oneshot._lock:
        if why := allowed(spec["oneshot"].get("spend"), spec["oneshot"].get("profile"), stage, codex):
            store.stop("budget", why)
            return
    try:
        release = hold(work._busy, work_lock, str(path), "작업트리를 다른 턴이 쓰고 있다", kind="turn")
    except HTTPException as exc:
        store.stop("block", str(exc.detail))
        return
    try:
        cfg = oneshot.role_config()
        chat = work.session(path, cfg["model"], cfg["effort"])
        turn = Turn(chat, repo, stage, codex)
        store.put(working=stage, stopped=None, inflight={"role": stage, "codex": codex, "sent": time.time()})
        work.begin(path, chat, text, release, turn)
    except BaseException as exc:
        release()
        store.stop("restart" if runtime.stopping.is_set() else "block",
                   f"작업 턴을 보내지 못했다 — {getattr(exc, 'detail', exc)}")


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
    send(repo, stage, tests_text(spec) if stage == "tests" else build_text(spec))


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


def outcomes(path: Path, run, spec: dict, raw: Path) -> dict[str, list[str]]:
    """Each planned test's outcomes (`passed`, `failure`, `error`, `skipped`) from a JUnit run by node id;
    a planned test the collection did not find has none."""

    cmd = gate_cmd(spec["oneshot"]["profile"])
    code, out, cut = specs.gate(f"{cmd} --collect-only -q -p no:cacheprovider", path, run.halt, timeout=RUN_SECONDS)
    if code != 0:
        raise ValueError(f"테스트 수집이 실패했다 ({cut or code}) — {out.strip()[-800:]}")
    names = {t["test_name"] for t in planned(spec)}
    ids = [line.strip() for line in out.splitlines()
           if "::" in line and line.strip().rsplit("::", 1)[-1].split("[")[0] in names]
    if not ids:
        return {}
    raw.mkdir(parents=True, exist_ok=True)
    xml = raw / f"junit-{int(time.time() * 1000)}.xml"
    quoted = " ".join(f'"{i}"' for i in ids)
    code, out, cut = specs.gate(f'{cmd} -q -p no:cacheprovider --junitxml="{xml.as_posix()}" {quoted}', path,
                                run.halt, timeout=RUN_SECONDS)
    if cut or not xml.exists():
        raise ValueError(f"계획된 테스트를 돌리지 못했다 ({cut or code}) — {out.strip()[-800:]}")
    found: dict[str, list[str]] = {}
    for case in ET.parse(xml).getroot().iter("testcase"):
        kind = next((c.tag for c in case if c.tag in ("failure", "error", "skipped")), "passed")
        found.setdefault(case.get("name", "").split("[")[0], []).append(kind)
    return {n: found[n] for n in names if n in found}


def unmet(spec: dict, found: dict[str, list[str]], want: str) -> str:
    """Why the planned tests' outcomes are not what the stage needs, or empty.
    `failing`: each ran and failed; `passed`: each ran and passed."""

    missing = sorted(t["test_name"] for t in planned(spec) if t["test_name"] not in found)
    if missing:
        return "수집되지 않았거나 돌지 않은 계획 테스트: " + ", ".join(missing)
    if want == "failing":
        passing = sorted(n for n, k in found.items() if "passed" in k)
        skipped = sorted(n for n, k in found.items() if "skipped" in k)
        return ("아직 구현이 없는데 통과하는 테스트는 아무것도 확인하지 않는다: " + ", ".join(passing) if passing else
                "건너뛴 계획 테스트: " + ", ".join(skipped) if skipped else "")
    bad = sorted(f"{n}({', '.join(sorted(set(k) - {'passed'}))})" for n, k in found.items() if set(k) != {"passed"})
    return "통과하지 못한 계획 테스트: " + ", ".join(bad) if bad else ""


def pinned(path: Path, o: dict) -> str:
    """Why a contract test file is no longer the one committed at `tests_head`, or empty."""

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
    return ""


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
    run.checked = True
    try:
        why = tested(path, run, spec) if stage == "tests" else built(path, run, spec, final)
    except (ValueError, OSError, ET.ParseError) as exc:
        why = str(exc)
    except Exception as exc:   # an unforeseen failure still stops with a reason, never leaves the stage running
        oneshot_spec.store(repo).stop("block", f"{type(exc).__name__}: {exc}")
        raise
    if not why:
        if stage == "tests":
            return lambda: send(repo, "build", build_text(oneshot_spec.spec_of(repo)))
        return None
    specs.note(run, why)
    attempts = int(spec["oneshot"].get("attempts") or 0) + 1
    store = oneshot_spec.store(repo)
    if attempts >= MAX_ATTEMPTS or run.halt.is_set():
        store.put(attempts=attempts)
        store.stop(STOPS[stage], why)
        return None
    store.put(attempts=attempts, working=None)
    return lambda: send(repo, stage, f"The server refused this {stage} turn: {why}\n\nFix it and finish the "
                                     f"turn again (attempt {attempts + 1} of {MAX_ATTEMPTS}).")


def tested(path: Path, run, spec: dict) -> str:
    """Only tests and their configuration changed, they collect, and every planned test fails: then the
    server commits them and pins the test files at that commit."""

    o = spec["oneshot"]
    if specs.sh(["git", "merge-base", "--is-ancestor", o["design_head"], "HEAD"], path).returncode:
        return f"HEAD 가 승인된 설계 커밋 {o['design_head'][:12]} 위에 있지 않다"
    names = changed(path, o["design_head"])
    if other := [n for n in names if not is_test(n) and n not in CONFIG]:
        return "테스트 단계에서는 테스트와 테스트 설정만 쓴다 — " + ", ".join(other[:10])
    if why := unmet(spec, outcomes(path, run, spec, oneshot_spec.store(spec["repo"]).folder), "failing"):
        return why
    for args in (["add", "-A", "--", *names], ["commit", "-m", "Write the contract tests before the backend"]):
        done = specs.sh(["git", *args], path)
        if done.returncode and "nothing to commit" not in done.stdout + done.stderr:
            raise ValueError(f"계약 테스트를 커밋하지 못했다 — {(done.stderr or done.stdout).strip()[-300:]}")
    head = specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip()
    contract = sorted(n for n in names if is_test(n) and (path / n).is_file())
    scenarios = [f"{r['id']}: {r['text']}" for r in spec.get("requirements") or [] if r.get("postconditions")]
    oneshot_spec.saved(spec["repo"], done=[gate_cmd(o["profile"]), *scenarios],
                       oneshot={"stage": "build", "tests_head": head, "contract": contract, "attempts": 0,
                                "working": None})
    specs.note(run, f"계약 테스트 커밋 {head[:12]} · 파일 {len(contract)}개 고정")
    return ""


def built(path: Path, run, spec: dict, final: str) -> str:
    """A done-report meeting every item, the contract tests unchanged, the gate green on a clean HEAD, and
    every planned test passing by node id: then the run moves to the backend review."""

    report = next((b for b in specs.blocks(final)[1] if b["name"] == "done-report"), None)
    if report is None or "error" in report or not specs.valid(report.get("value")):
        return "완료 보고(`done-report`)가 없거나 형식이 틀렸다 — 항목마다 item 과 pass 를 적는다"
    if short := specs.shortfall(report["value"], spec):
        return short
    o = spec["oneshot"]
    if why := pinned(path, o):
        return why
    verdict = specs.judge(path, [gate_cmd(o["profile"])], run.halt, lambda text: specs.note(run, text))
    if not verdict["ok"]:
        return f"게이트 실패 — {verdict['reason']}\n{verdict['tail']}"
    if why := unmet(spec, outcomes(path, run, spec, oneshot_spec.store(spec["repo"]).folder), "passed"):
        return why
    if specs.sh(["git", "rev-parse", "HEAD"], path).stdout.strip() != verdict["head"] or specs.dirty(path):
        return "계획 테스트를 돌리는 동안 작업트리가 바뀌었다"
    oneshot_spec.saved(spec["repo"], oneshot={"stage": "review", "built_head": verdict["head"], "attempts": 0,
                                              "working": None})
    specs.note(run, f"백엔드 빌드 통과 · {verdict['head'][:12]}")
    return ""
