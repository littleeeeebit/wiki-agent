"""oneshot step 12: the backend runs against the real services, between its build and its review.

`docs/plans/oneshot/12-review-converges-on-measurement.md` is the design. The build agent commits
`.wiki/measure.json` (`PLAN`):
- how to start the backend headless (`serve`);
- the script that measures it against the real services (`measure`);
- what the owner gives it (`inputs`), each with the steps that get it, window by window;
- what the owner does while it runs (`during`);
- each metric with its threshold.

The server checks the plan and opens pull request #1 as a draft (`published`). It starts the backend in a job of
its own, runs the script with the owner's inputs, and keeps the report and the clips. Then the owner listens and
decides. Approval sends the run to the review, which reads the measurement; a failed measurement, or the owner's
refusal, sends it back to the build agent.
"""

from __future__ import annotations

import json
import operator
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from common import masked, process, shell

from . import oneshot, oneshot_build, oneshot_needs, oneshot_publish, oneshot_spec, release, runtime, specs, work
from .oneshot_kept import CLIPS, MAX_CLIPS, clips_of, fresh, kept_from, label_of, plain, spelled, strict  # noqa: F401
from .query import _lock as work_lock, hold

router = APIRouter()
PLAN = ".wiki/measure.json"
IID = re.compile(r"M[1-9]\d*")
MID = re.compile(r"[a-z][a-z0-9_]{0,40}")
OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq}
MAX_MINUTES, MAX_SERVE_SECONDS = 60, 600
MAX_TEXT = 2_000   # an input's typed value, and the owner's note
HEALTH = re.compile(r"http://(127\.0\.0\.1|localhost)(:\d{1,5})?(/\S*)?")


class Halted:
    """A measurement's stop: the owner's, or the server going down. Made before its worker starts (`start`), so
    a stop pressed in between is never lost."""

    def __init__(self) -> None:
        self.own = threading.Event()

    def set(self) -> None:
        self.own.set()

    def is_set(self) -> bool:
        return self.own.is_set() or runtime.stopping.is_set()


HALTS: dict[str, Halted] = {}   # the measurement running now, by repository: `stop` sets it


# -- The plan the build agent commits ---------------------------------------------------------------------

def step_problem(s, where: str) -> str:
    """Why one guide step does not say which window, what to press and what the owner then sees, or empty."""

    if not isinstance(s, dict) or not all(isinstance(s.get(k), str) and s[k].strip() for k in ("window", "do", "see")):
        return f"{where}: 단계마다 `window`(어느 창), `do`(무엇을 누르거나 넣는지), `see`(그러면 보이는 것)를 적는다"
    if "url" in s and not (isinstance(s["url"], str) and s["url"].startswith("https://")):
        return f"{where}: `url` 은 https:// 주소다"
    if "copy" in s and not (isinstance(s["copy"], str) and s["copy"].strip()):
        return f"{where}: `copy` 는 그 화면에서 복사할 값의 이름이다"
    return ""


def checked(value, spec: dict, path: Path) -> dict:
    """`.wiki/measure.json`, as the server runs it; `ValueError` says what is wrong, in the owner's words."""

    o = spec["oneshot"]
    if not isinstance(value, dict):
        raise ValueError(f"`{PLAN}` 는 serve, measure, inputs, metrics 를 담은 객체다")
    serve, measure = value.get("serve"), value.get("measure")
    if not isinstance(serve, dict) or not isinstance(serve.get("command"), str) \
            or not oneshot_build.chained(serve["command"]):
        raise ValueError(f"`serve.command` 는 백엔드를 화면 없이 띄우고 계속 도는 명령이고, {oneshot_build.CHAIN}")
    if not isinstance(serve.get("health"), str) or not HEALTH.fullmatch(serve["health"]):
        raise ValueError("`serve.health` 는 떠 있는 백엔드가 200 으로 답하는 http://127.0.0.1:포트/경로 주소다")
    seconds = serve.get("seconds", 120)
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 5 <= seconds <= MAX_SERVE_SECONDS:
        raise ValueError(f"`serve.seconds` 는 백엔드가 뜨기를 기다릴 초, 5–{MAX_SERVE_SECONDS} 다")
    if not isinstance(measure, dict) or not isinstance(measure.get("command"), str) \
            or measure["command"].count("{out}") != 1 or not oneshot_build.chained(measure["command"].replace("{out}", "x")):
        raise ValueError(f"`measure.command` 는 결과 폴더 자리 `{{out}}` 를 한 번 담은 실측 명령이고, {oneshot_build.CHAIN}")
    minutes = measure.get("minutes")
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= MAX_MINUTES:
        raise ValueError(f"`measure.minutes` 는 실측이 걸리는 분, 1–{MAX_MINUTES} 다")
    if (tools := oneshot_build.tools(o)) is not None and (
            bad := oneshot_build.undeclared(path, f"{serve['command']} && {measure['command'].replace('{out}', 'x')}", tools)):
        raise ValueError(f"확정한 도구도 작업트리 안의 파일도 아닌 프로그램을 부른다: {', '.join(bad)}")
    for part in ("inputs", "during", "metrics"):
        if not isinstance(value.get(part) or [], list):
            raise ValueError(f"`{part}` 는 목록이다")
    inputs, envs = [], set()
    secret = {n["env"].upper() for _, n in oneshot_needs.secret_needs(spec)}   # the design's secrets stay secret
    for n, x in enumerate(value.get("inputs") or []):
        where = f"inputs[{n}]"
        if not isinstance(x, dict) or not isinstance(x.get("id"), str) or not IID.fullmatch(x["id"]):
            raise ValueError(f"{where}: `id` 는 M1, M2 … 다")
        if x.get("kind") not in ("secret", "text"):
            raise ValueError(f"{where} {x['id']}: `kind` 는 secret(비밀 값) 이나 text(주소 같은 보통 글) 다")
        if not isinstance(x.get("env"), str) or not oneshot_needs.ENV.fullmatch(x["env"]) or x["env"] in envs:
            raise ValueError(f"{where} {x['id']}: `env` 는 서로 다른 대문자 환경 변수 이름이다")
        if x["kind"] == "text" and x["env"].upper() in secret:
            raise ValueError(f"{where} {x['id']}: `{x['env']}` 는 설계의 비밀 값이다 — kind 를 secret 으로 적는다")
        if not isinstance(x.get("what"), str) or not x["what"].strip():
            raise ValueError(f"{where} {x['id']}: `what` 이 비었다")
        guide = x.get("guide")
        if not isinstance(guide, list) or len(guide) < 3:
            raise ValueError(f"{where} {x['id']}: `guide` 는 그 값을 얻는 단계를 처음부터 셋 이상 적은 목록이다")
        for m, s in enumerate(guide, 1):
            if why := step_problem(s, f"{where} {x['id']} {m}단계"):
                raise ValueError(why)
        if "copy" not in guide[-1]:
            raise ValueError(f"{where} {x['id']}: 마지막 단계의 `copy` 에 그 화면에서 복사해 넣을 값을 적는다")
        envs.add(x["env"])
        inputs.append({k: x[k] for k in ("id", "kind", "env")} | {"what": " ".join(x["what"].split()), "guide": guide})
    if len({x["id"] for x in inputs}) != len(inputs):
        raise ValueError("`inputs` 의 id 가 겹친다")
    during = value.get("during") or []
    if not isinstance(during, list) or any(why := step_problem(s, f"during {m}단계") for m, s in enumerate(during, 1)):
        raise ValueError(why if isinstance(during, list) else "`during` 은 실측하는 동안 주인이 할 단계의 목록이다")
    reqs = {str(r["id"]) for r in spec.get("requirements") or []}
    calls = {x["id"] for x in (o.get("design") or {}).get("outside") or []}
    metrics, covered = [], set()
    for n, m in enumerate(value.get("metrics") or []):
        where = f"metrics[{n}]"
        if not isinstance(m, dict) or not isinstance(m.get("id"), str) or not MID.fullmatch(m["id"]):
            raise ValueError(f"{where}: `id` 는 소문자로 시작하는 짧은 이름이다 (live_delay_s)")
        if not isinstance(m.get("what"), str) or not m["what"].strip():
            raise ValueError(f"{where} {m['id']}: `what` 이 비었다")
        rq, cl = m.get("requirements"), m.get("calls", [])
        if not isinstance(rq, list) or not rq or not set(map(str, rq)) <= reqs:
            raise ValueError(f"{where} {m['id']}: `requirements` 는 이 값이 재는 요구 번호의 목록이다")
        if not isinstance(cl, list) or not set(map(str, cl)) <= calls:
            raise ValueError(f"{where} {m['id']}: `calls` 는 설계의 바깥 호출 번호(X1 …)의 목록이다")
        bound = m.get("pass")
        if not isinstance(bound, dict) or not isinstance(bound.get("op"), str) or bound["op"] not in OPS \
                or isinstance(bound.get("value"), str) \
                or not isinstance(bound.get("value"), (int, float)) \
                or isinstance(bound["value"], bool) and bound["op"] != "==":
            raise ValueError(f"{where} {m['id']}: `pass` 는 op({', '.join(OPS)})와 숫자 value, 또는 == 와 true/false 다")
        covered |= set(map(str, cl))
        metrics.append({"id": m["id"], "what": " ".join(m["what"].split()), "unit": str(m.get("unit") or ""),
                        "requirements": list(map(str, rq)), "calls": list(map(str, cl)), "pass": bound})
    if not metrics or len({m["id"] for m in metrics}) != len(metrics):
        raise ValueError("`metrics` 는 id 가 겹치지 않는 하나 이상의 측정값이다")
    if missing := sorted(calls - covered):
        raise ValueError(f"실측이 닿지 않는 바깥 호출이 있다: {', '.join(missing)} — 측정값의 `calls` 에 넣어 진짜로 부른다")
    return {"serve": {"command": serve["command"].strip(), "health": serve["health"], "seconds": seconds},
            "measure": {"command": measure["command"].strip(), "minutes": minutes},
            "inputs": inputs, "during": during, "metrics": metrics, "clips": bool(value.get("clips"))}


def planned(path: Path, spec: dict, rev: str | None = None) -> dict:
    """The worktree's plan, checked; at commit `rev` when given, so what the owner is shown and gives inputs to
    is the plan of the HEAD the build passed, whatever the tree holds now."""

    try:
        if rev:
            shown = specs.sh(["git", "show", f"{rev}:{PLAN}"], path)
            if shown.returncode:
                raise FileNotFoundError
        value = strict(shown.stdout if rev else (path / PLAN).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"`{PLAN}` 가 없다 — 실측 계획(serve, measure, inputs, metrics)을 커밋한다") from None
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError(f"`{PLAN}` 를 읽지 못했다 — {exc}") from None
    try:
        return checked(value, spec, path)
    except (TypeError, KeyError, AttributeError) as exc:   # a shape `checked` did not foresee is still a bad plan
        raise ValueError(f"`{PLAN}` 의 형식이 틀렸다 — {type(exc).__name__}: {exc}") from None


# -- What the owner gives the measurement ----------------------------------------------------------------

def kept(spec: dict) -> dict:
    return spec["oneshot"].get("measure_given") or {}


def has(spec: dict, x: dict) -> bool:
    if x["kind"] == "secret":
        try:
            return release.secret_read(oneshot_needs.name(spec["repo"], x["env"])) is not None
        except (OSError, ValueError):
            return False
    held = kept(spec).get(x["env"]) or {}
    return bool(held.get("value")) and held.get("what") == x["what"]


def env_of(spec: dict, plan: dict) -> tuple[dict, dict[str, str]]:
    """The measurement's environment: the gate's, with the design's secrets, and each input under its `env`; and
    what its output shows instead of each secret."""

    env = dict(oneshot_build.built_env(spec) or os.environ)
    secrets = {}
    for x in plan["inputs"]:
        if x["kind"] == "secret":
            try:
                value = release.secret_read(oneshot_needs.name(spec["repo"], x["env"]))
            except (OSError, ValueError):
                value = None
            if value:
                env[x["env"]] = secrets[x["env"]] = value
        elif value := (kept(spec).get(x["env"]) or {}).get("value"):
            env[x["env"]] = value
    hide = {**oneshot_needs.hidden(spec, env),
            **{form: f"[{k}]" for k, v in secrets.items() for form in {v, quote(v, safe="")}}}
    return env, spelled(hide)   # every reader of the run's text masks the JSON-escaped spellings too


# -- Starting the backend ---------------------------------------------------------------------------------

@contextmanager
def served(command: str, path: Path, env: dict, health: str, seconds: int, halt, log: Path, hide: dict):
    """The backend started in a job of its own and answering `health` with 200, for the block; `ValueError` with
    the end of its log when it exits, or does not answer in `seconds`. Killed with everything it started after."""

    if answers(health):   # whatever answers now is not the backend about to start
        raise ValueError(f"`{health}` 에 이미 다른 프로그램이 답한다 — 그 프로그램을 끄거나 `serve` 의 포트를 바꾼다")
    argv, use_shell = shell.launch("gate", command=command)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w+", encoding="utf-8", errors="replace") as output:
        proc = subprocess.Popen(argv, shell=use_shell, cwd=path, env={**env, "PYTHONUNBUFFERED": "1"},
                                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                start_new_session=os.name != "nt",
                                **{**process.background_options(), "creationflags":
                                   process.background_options().get("creationflags", 0) | process.SUSPENDED})
        job = process.contained(proc)
        try:
            if os.name == "nt" and not job:
                proc.kill()
                raise ValueError("백엔드 프로세스를 묶어 둘 수 없었다")
            process.resumed(proc)
            deadline = time.monotonic() + seconds
            while True:
                if halt.is_set():
                    raise ValueError("사람이 멈췄다")
                if proc.poll() is not None:
                    raise ValueError(f"`serve.command` 가 떠 있지 않고 끝났다 (종료 코드 {proc.returncode}) — {tail(log, hide)}")
                if answers(health) == 200:
                    if not owned(proc, health):
                        raise ValueError(f"`{health}` 에 답한 것이 띄운 백엔드가 아니다 — 같은 포트를 쓰는 다른 프로그램을 끈다")
                    break
                if time.monotonic() > deadline:
                    raise ValueError(f"백엔드가 {seconds}초 안에 `{health}` 에 200 으로 답하지 않았다 — {tail(log, hide)}")
                time.sleep(1)
            yield
        finally:
            process.terminated(job) if job else proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def answers(health: str) -> int | None:
    """The status `health` itself answers with, or `None` when nothing does. A redirect is its status, never
    followed: whatever answers at the other end is not the backend being checked."""

    try:
        with NO_REDIRECT.open(health, timeout=3) as answer:
            return answer.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError, ValueError):
        return None


class Unfollowed(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None   # urllib then raises `HTTPError` with the redirect's own status


NO_REDIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}), Unfollowed)   # loopback: never a proxy
def owned(proc: subprocess.Popen, health: str) -> bool:
    """Whether every process that accepts a connection to `health` (one connection to each address its host
    resolves to) is `proc` or a process it started: read off the accepted socket itself, whose remote port is
    the connection's own, so whichever listener the system picks is the one judged."""

    try:
        import psutil   # `requirements-chat.txt`; here, so a server without it still starts and refuses only this
    except ImportError:
        raise ValueError("psutil 이 설치되지 않았다 — `pip install -r requirements-chat.txt` 로 다시 설치한다") from None
    url = urlsplit(health)
    port, pids = url.port or 80, []
    try:
        tree = {proc.pid, *(c.pid for c in psutil.Process(proc.pid).children(recursive=True))}
        for family, kind, _, _, address in socket.getaddrinfo(url.hostname, port, type=socket.SOCK_STREAM):
            with socket.socket(family, kind) as s:
                s.settimeout(3)
                try:
                    s.connect(address)
                except OSError:
                    continue   # nothing answers on this address: it cannot answer for the backend either
                pids.append(accepted_by(psutil, s.getpeername()[:2], s.getsockname()[:2]))
        return bool(pids) and all(p in tree for p in pids)
    except (psutil.Error, OSError):
        return False


def accepted_by(psutil, server: tuple, client: tuple, seconds: float = 3.0) -> int | None:
    """The process holding the server side of the connection `client` made to `server`, matched on both ends'
    address and port (an IPv4-mapped address read as IPv4), or `None`. A connection still queued for `accept`
    has no owner on Linux yet, so the table is read again until one shows or `seconds` pass."""

    ip = lambda a: a.lower().removeprefix("::ffff:")   # noqa: E731
    deadline = time.monotonic() + seconds
    while True:
        pid = next((c.pid for c in psutil.net_connections("tcp") if c.laddr and c.raddr
                    and (ip(c.laddr.ip), c.laddr.port) == (ip(server[0]), server[1])
                    and (ip(c.raddr.ip), c.raddr.port) == (ip(client[0]), client[1])), None)
        if pid is not None or time.monotonic() > deadline:
            return pid
        time.sleep(0.1)


def tail(log: Path, hide: dict, size: int = 1500) -> str:   # masked whole first: the cut never splits a secret
    try:
        return masked(log.read_text(encoding="utf-8", errors="replace"), hide)[-size:].strip() or "(출력 없음)"
    except OSError:
        return "(출력 없음)"


def serve_check(path: Path, run, spec: dict) -> str:
    """At the build's gate: the plan holds and the backend starts and answers its health address, or why not."""

    try:
        plan = planned(path, spec, "HEAD")   # the plan the commit holds, as the measurement reads it
        env, hide = env_of(spec, plan)
        log = Path(tempfile.mkdtemp(prefix="oneshot-serve-")) / "serve.log"
        try:
            with served(oneshot_build.run_as(plan["serve"]["command"], spec["oneshot"], path), path, env,
                        plan["serve"]["health"], plan["serve"]["seconds"], run.halt, log, hide):
                pass
        finally:
            shutil.rmtree(log.parent, ignore_errors=True)
    except ValueError as exc:
        return masked(str(exc), hide) if "hide" in locals() else str(exc)
    if specs.dirty(path):   # the HEAD the build passed is the one measured, so the tree stays as committed
        return "백엔드를 띄우자 작업트리가 바뀌었다 — 백엔드가 쓰는 파일(데이터베이스, 로그)은 .gitignore 에 둔다"
    specs.note(run, "백엔드가 화면 없이 떠서 상태 주소에 답했다")
    return ""


# -- One measurement ---------------------------------------------------------------------------------------

def report_of(out: Path, plan: dict, hide: dict[str, str]) -> dict:
    """`report.json` the script wrote, read against the plan: each metric's value and whether it passed, the input
    the script says was wrong, and its notes, none of them showing a secret."""

    try:
        if not plain(out, stat.S_IFDIR) or not plain(out / "report.json"):   # never read through a link
            raise OSError("링크이거나 일반 파일이 아니다")
        value = strict((out / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        return {"error": f"실측 명령이 `{{out}}/report.json` 을 쓰지 않았거나 JSON 이 아니다 — {masked(str(exc), hide)}"}
    if not isinstance(value, dict):
        return {"error": "`report.json` 은 metrics 를 담은 객체다"}
    if isinstance(bad := value.get("input_error"), dict) and isinstance(bad.get("why"), str):
        return {"input_error": {"input": masked(str(bad.get("input") or ""), hide)[:40],
                                "why": masked(bad["why"], hide)[:MAX_TEXT]}}
    got = value.get("metrics") if isinstance(value.get("metrics"), dict) else {}
    rows = []
    for m in plan["metrics"]:
        v = got.get(m["id"])
        number = isinstance(v, (int, float)) and (not isinstance(v, bool) or m["pass"]["op"] == "==") \
            and label_of(v, hide) is None   # a value that is a secret is never shown as a measurement
        ok = bool(number and OPS[m["pass"]["op"]](v, m["pass"]["value"]))
        rows.append({**m, "value": v if number else None, "ok": ok})
    return {"metrics": rows, "notes": masked(str(value.get("notes") or ""), hide)[:MAX_TEXT]}


def measure(repo: str) -> None:
    from . import oneshot_review   # it imports this module

    halt = HALTS.setdefault(repo, Halted())   # `start` made a fresh one: a stop pressed since still holds

    def body(store, spec, tree):
        o = spec["oneshot"]
        try:
            plan = planned(tree, spec)
            if missing := [x["id"] for x in plan["inputs"] if not has(spec, x)]:
                raise ValueError(f"아직 넣지 않은 입력이 있다: {', '.join(missing)}")
            if (o.get("pr1") or {}).get("head") != o["built_head"]:
                oneshot_publish.published(repo)   # the HEAD measured is the one pull request #1 shows
        except ValueError as exc:
            store.stop("measure", str(exc))
            return None
        n = len(o.get("measures") or []) + 1
        out = store.folder / "measure" / f"m{n}-{o['built_head'][:12]}"
        raw = out.with_name(out.name + ".raw")   # what the script writes; only `kept_from` copies leave it
        with oneshot._lock:   # every stored-value change counts under this lock: the values and the count agree
            now = oneshot_spec.spec_of(repo)   # what the owner gave by now, a value taken back since included
            if missing := [x["id"] for x in plan["inputs"] if not has(now, x)]:
                store.stop("measure", f"아직 넣지 않은 입력이 있다: {', '.join(missing)}")   # no failure counted
                return None
            env, hide = env_of(now, plan)
            since = CHANGES.get(repo, 0)
        if why := fresh(out, raw):   # made only once the run goes ahead: an early stop leaves no folder
            store.stop("measure", why)
            return None
        record, failed = {"n": n, "head": o["built_head"], "ts": time.time(), "folder": out.name}, None
        try:
            with served(oneshot_build.run_as(plan["serve"]["command"], o, tree), tree, env, plan["serve"]["health"],
                        plan["serve"]["seconds"], halt, raw / "serve.log", hide):
                limit = plan["measure"]["minutes"] * 60 + 300
                code, text, cut = specs.gate(oneshot_build.run_as(plan["measure"]["command"].replace(
                    "{out}", oneshot_build.literal(raw.as_posix())), o, tree), tree, halt, env=env, timeout=limit,
                    idle=limit, hide=hide)
        except ValueError as exc:
            code, text, cut = None, "", masked(str(exc), hide)
        except Exception as exc:   # unforeseen: raised again once the folder is dealt with
            failed = exc
        finally:
            try:
                found = report_of(raw, plan, hide)
            finally:
                left = kept_from(raw, out, hide)   # every exit, a stop or a failed read included, keeps no secret
        if left:   # the copy failed, or the script's own folder is still there: the owner's reason, before any other
            store.stop("measure", left)
            return None
        if failed:
            raise failed
        record["tail"] = (text or "").strip()[-3000:]
        if halt.is_set():
            store.stop("measure", "사람이 실측을 멈췄다 — 다시 하려면 [실측 시작]을 누른다" if not runtime.stopping.is_set()
                       else "서버가 꺼져 실측이 멈췄다 — 다시 켠 뒤 [실측 시작]을 누른다")
            return None
        found = found if not cut else {"error": cut}
        record |= found | {"clips": clips_of(out, hide), "code": code}
        if "input_error" in found:
            record["status"] = "input"
        elif found.get("error") or code or not all(m["ok"] for m in found["metrics"]) or plan["clips"] and not record["clips"]:
            record["status"] = "failed"
            if not found.get("error") and plan["clips"] and not record["clips"]:
                record["error"] = "변환한 소리를 들어 볼 클립이 `{out}/clips` 에 없다"
        else:
            record["status"] = "owner"
        return lambda: decided(repo, store, record, since)

    oneshot_review.holding(repo, "실측", body)


def decided(repo: str, store, record: dict, since: int):
    """`result` of a measurement whose run started at change count `since`: compared and saved under the lock every
    stored-value change takes, so a change made at any moment of the run sets it aside."""

    with oneshot._lock:
        if CHANGES.get(repo, 0) != since:
            record = {**record, "status": "stale", "error": "실측하는 동안 비밀 값이 바뀌었다 — [실측 시작]으로 다시 잰다"}
        return result(repo, store, record)


def result(repo: str, store, record: dict):
    """A measurement saved. One the owner must judge waits; a wrong input stops for the owner; a failure goes to
    the build agent, up to the review's round cap."""

    from . import oneshot_review   # it imports this module

    o = store.get()
    runs = [*(o.get("measures") or []), record]
    if record["status"] == "owner":
        oneshot_spec.saved(repo, oneshot={"measures": runs, "working": None})
        return None
    if record["status"] == "stale":   # an input changed while it ran: no fault of the build, measured again
        oneshot_spec.saved(repo, oneshot={"measures": runs})
        store.stop("measure", record["error"])
        return None
    if record["status"] == "input":
        bad = record["input_error"]
        oneshot_spec.saved(repo, oneshot={"measures": runs})
        store.stop("measure-input", f"{bad['input'] or '입력'}: {bad['why']} — 그 입력을 고쳐 넣고 [실측 시작]을 누른다")
        return None
    fails = int(o.get("measure_fails") or 0) + 1
    if fails >= oneshot_review.cap():
        oneshot_spec.saved(repo, oneshot={"measures": runs, "measure_fails": fails})
        store.stop("measure", f"실측이 {fails}번 실패했다 — {failure(record)}")
        return None
    spec = oneshot_spec.saved(repo, oneshot={"measures": runs, "measure_fails": fails, "stage": "build", "fix": "measure",
                                             "attempts": 0, "working": "build"})
    text = fixing(spec)
    return lambda: oneshot_build.send(repo, "build", text)


def failure(record: dict) -> str:
    if record.get("error"):
        return record["error"]
    bad = [f"{m['id']}={m['value']} (기준 {m['pass']['op']} {m['pass']['value']})" for m in record.get("metrics") or []
           if not m["ok"]]
    return ", ".join(bad) or f"실측 명령이 {record.get('code')} 로 끝났다"


def fixing(spec: dict) -> str:
    """The build turn after the measurement of the built HEAD failed, or the owner refused it; empty when no
    such measurement stands."""

    o = spec["oneshot"]
    last = (o.get("measures") or [None])[-1]
    if not last or last["head"] != o.get("built_head"):
        return ""
    if last["status"] == "failed":
        why = (f"The real measurement of HEAD `{last['head']}` failed: {failure(last)}.\n\n"
               f"The metrics as measured:\n\n```json\n{json.dumps(last.get('metrics') or [], ensure_ascii=False, indent=2)}\n```\n\n"
               f"The end of the measuring script's output:\n\n```\n{last.get('tail', '')[-1500:]}\n```\n")
    elif (last.get("owner") or {}).get("approve") is False:
        why = (f"The owner listened to the real measurement of HEAD `{last['head']}` and refused it. They wrote, in "
               f"their own words:\n\n> {last['owner']['note']}\n\nThe metrics as measured:\n\n```json\n"
               f"{json.dumps(last.get('metrics') or [], ensure_ascii=False, indent=2)}\n```\n")
    else:
        return ""
    return (why + "\nFind the cause in the code against the real services, fix it, and end this turn on a new "
            "commit; the measurement runs again before any review.\n\n" + oneshot_build.build_text(spec))


# -- What the owner does ----------------------------------------------------------------------------------

def measuring(repo: str, plan_needed: bool = True) -> tuple[dict, dict | None, str]:
    """`(spec, plan, why the plan does not hold)` while the run is at the measurement with no worker running."""

    spec = oneshot_spec.spec_of(repo)
    o = spec["oneshot"]
    oneshot_spec.supported(o)
    if o.get("stage") != "measure" or o.get("working"):
        raise HTTPException(409, f"지금 할 수 없다 — {o.get('stage')}{' 진행 중' if o.get('working') else ''}")
    try:
        return spec, planned(Path(spec["worktree"]), spec, o.get("built_head")), ""
    except ValueError as exc:
        if plan_needed:
            raise HTTPException(409, str(exc)) from None
        return spec, None, str(exc)


class Value(BaseModel):
    value: str = ""
    head: str = ""   # the input as the owner's screen showed it, with the built HEAD whose plan it read:
    env: str = ""    # a plan rebuilt since is refused
    kind: str = ""


def shown_input(spec: dict, plan: dict, iid: str, body: Value) -> dict:
    """The input the owner's screen showed, or 409 when the plan they followed is not this one. A result waiting
    for the owner's verdict is retired: its inputs are no longer what it ran with."""

    x = next((x for x in plan["inputs"] if x["id"] == iid), None)
    if x is None:
        raise HTTPException(404, f"실측 계획에 없는 입력이다: {iid}")
    if (body.head, body.env, body.kind) != (spec["oneshot"].get("built_head"), x["env"], x["kind"]):
        raise HTTPException(409, "실측 계획이 그 사이 바뀌었다 — 다시 불러와 새 칸에 넣는다")
    retire(spec, "입력이 바뀌었다 — 이 결과는 판정하지 않고 바뀐 입력으로 다시 잰다")
    return x


def retire(spec: dict, why: str, **fields) -> dict:
    """The result waiting for the owner's verdict set aside (`stale`) with `why`, saved with `fields`."""

    runs = list(spec["oneshot"].get("measures") or [])
    if runs and runs[-1]["status"] == "owner" and not runs[-1].get("owner"):
        runs[-1] = {**runs[-1], "status": "stale", "error": why}
        fields["measures"] = runs
    return oneshot_spec.saved(spec["repo"], oneshot=fields) if fields else spec


@router.post("/api/oneshot/spec/{repo}/measure/inputs/{iid}")
def give(repo: str, iid: str, body: Value) -> dict:
    """One input the owner got by following its steps: a secret to Credential Manager, other text to the spec."""

    with oneshot._lock:
        spec, plan, _ = measuring(repo)
        value = body.value.strip()
        if not value or len(value) > MAX_TEXT:
            raise HTTPException(400, f"값이 비었거나 {MAX_TEXT}자를 넘는다")
        x = shown_input(spec, plan, iid, body)
        if x["kind"] == "secret":
            oneshot_needs.stored(repo, x["env"], value)
        else:
            oneshot_spec.saved(repo, oneshot={"measure_given": {**kept(spec), x["env"]: {"value": value, "what": x["what"]}}})
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


@router.delete("/api/oneshot/spec/{repo}/measure/inputs/{iid}")
def take_back(repo: str, iid: str, head: str = "", env: str = "", kind: str = "") -> dict:
    with oneshot._lock:
        spec, plan, _ = measuring(repo)
        x = shown_input(spec, plan, iid, Value(head=head, env=env, kind=kind))
        if x["kind"] == "secret":
            oneshot_needs.stored(repo, x["env"], None)
        else:
            oneshot_spec.saved(repo, oneshot={"measure_given": {k: v for k, v in kept(spec).items() if k != x["env"]}})
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


class Start(BaseModel):
    head: str = ""   # the built HEAD the owner saw; any other is refused with 409


@router.post("/api/oneshot/spec/{repo}/measure/start")
def start(repo: str, body: Start) -> dict:
    with oneshot._lock:
        spec, plan, broken = measuring(repo, plan_needed=False)
        o = spec["oneshot"]   # a measurement stopped (a wrong input, a restart) starts again from here
        if body.head != o.get("built_head"):
            raise HTTPException(409, "빌드가 그 사이 바뀌었다 — 다시 불러와 새 화면에서 시작하라")
        if broken:   # the committed plan no longer holds: only the build agent can fix it
            spec = oneshot_spec.saved(repo, oneshot={"stage": "build", "fix": None, "attempts": 0, "working": "build",
                                                     "stopped": None})
            text = (f"The committed `{PLAN}` no longer holds: {broken}\n\nFix it and end this turn on a new commit; "
                    "the measurement runs before any review.\n\n" + oneshot_build.build_text(spec))
            then = lambda: oneshot_build.send(repo, "build", text)   # noqa: E731
        elif (last := (o.get("measures") or [None])[-1]) and last["head"] == o["built_head"] \
                and last["status"] == "failed":   # the capped failure goes to the build agent, never measured again
            spec = oneshot_spec.saved(repo, oneshot={"stage": "build", "fix": "measure", "attempts": 0, "measure_fails": 0,
                                                     "working": "build", "stopped": None})
            text = fixing(spec)
            then = lambda: oneshot_build.send(repo, "build", text)   # noqa: E731
        else:   # the same lock scope decides and launches: no other request's result slips in between
            if missing := [x["id"] for x in plan["inputs"] if not has(spec, x)]:
                raise HTTPException(400, f"아직 넣지 않은 입력이 있다: {', '.join(missing)}")
            if last and last["head"] == o["built_head"] and last["status"] == "owner" and not last.get("owner"):
                raise HTTPException(409, "이 빌드의 실측이 판정을 기다린다 — 듣고 승인하거나 거절한다")
            HALTS[repo] = Halted()   # before the worker: a stop pressed as it starts is kept
            oneshot.start_worker(oneshot_spec.store(repo), "measure", measure, (repo,), {})
            then = None
    if then:   # the build agent's turn goes after the lock is let go
        then()
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


@router.post("/api/oneshot/spec/{repo}/measure/stop")
def stop(repo: str) -> dict:
    if halt := HALTS.get(repo):
        halt.set()
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


class Decision(BaseModel):
    head: str = ""
    n: int = 0   # the measurement the owner listened to: a later one, even of the same HEAD, is refused
    approve: bool = False
    note: str = ""


@router.post("/api/oneshot/spec/{repo}/measure/decide")
def decide(repo: str, body: Decision) -> dict:
    """The owner's verdict on the measurement they listened to: approval goes to the review, which reads it; a
    refusal, with what they heard, goes to the build agent."""

    from . import oneshot_review   # it imports this module

    note = body.note.strip()
    with oneshot._lock:
        spec, plan, _ = measuring(repo, plan_needed=False)
        o, tree = spec["oneshot"], Path(spec["worktree"])
        runs = list(o.get("measures") or [])
        last = runs[-1] if runs else None
        if not last or last["head"] != o.get("built_head") or body.head != last["head"] or body.n != last["n"] \
                or last["status"] != "owner" or last.get("owner"):
            raise HTTPException(409, "판정할 실측이 없다 — 다시 불러온다")
        if not body.approve and not note:
            raise HTTPException(400, "거절하려면 들은 것과 무엇이 틀렸는지 적는다")
        if len(note) > MAX_TEXT:
            raise HTTPException(400, f"글은 {MAX_TEXT}자까지다")
        # The tree is the one measured, read while no turn writes it. A moved one retires the result and goes back
        # through the build check, then a new measurement: never a verdict on code that is no longer there.
        let_go = hold(work._busy, work_lock, str(tree), "작업트리를 다른 턴이 쓰고 있다", kind="turn")
        try:   # held until the verdict is saved: no turn moves HEAD between the check and the record of it
            why = oneshot_review.moved(spec, tree, "built_head")
            hide = env_of(spec, plan)[1] if plan else spelled(oneshot_needs.hidden(spec, oneshot_build.built_env(spec)))
            runs[-1] = {**last, "owner": {"approve": body.approve, "note": masked(note, hide), "ts": time.time()}}
            if why:
                spec = retire(spec, f"실측한 뒤 작업트리가 바뀌었다 — {why}", stage="build", fix=None, attempts=0,
                              working="build")
                text = (f"The worktree moved after HEAD `{last['head']}` was measured: {why}. Commit what belongs to "
                        "the build, or undo it, and finish the turn; the build check and a new measurement follow.\n\n"
                        + oneshot_build.build_text(spec))
                then = lambda: oneshot_build.send(repo, "build", text)   # noqa: E731
            elif body.approve:
                spec = oneshot_spec.saved(repo, oneshot={"measures": runs, "stage": "review", "attempts": 0,
                                                         "measure_fails": 0, "fix": None, "working": "review"})
                then = lambda: (noted_comment(repo, masked(commented(spec, runs[-1]), hide)),   # noqa: E731
                                oneshot_review.begin(repo))
            else:
                spec = oneshot_spec.saved(repo, oneshot={"measures": runs, "stage": "build", "fix": "measure",
                                                         "attempts": 0, "working": "build"})
                text = fixing(spec)
                then = lambda: oneshot_build.send(repo, "build", text)   # noqa: E731
        finally:
            let_go()
    then()
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


def noted_comment(repo: str, why: str) -> None:
    """A comment that could not be posted: kept on its measurement, and told to the owner, whose screen has moved
    on to the review by then."""

    if why:
        runs = list(oneshot_spec.spec_of(repo)["oneshot"].get("measures") or [])
        runs[-1] = {**runs[-1], "comment_error": why}
        oneshot_spec.saved(repo, oneshot={"measures": runs})
        work.notice("실측 기록을 PR 에 달지 못했다", f"{repo} · {why} — 기록은 원샷 화면에 남아 있다")


def commented(spec: dict, record: dict) -> str:
    """The approved measurement as a comment on pull request #1, so the review's record is the PR's; why it could
    not be posted, or empty. The run goes on either way: the record is the spec's, the comment its copy."""

    pr = spec["oneshot"].get("pr1")
    if not pr:
        return "PR #1 이 없다"
    if pr.get("head") != record["head"] or spec["oneshot"].get("built_head") != record["head"]:
        return f"PR #1 이 승인한 HEAD `{record['head'][:12]}` 를 보이지 않는다 — 실측 기록은 원샷 화면에만 있다"
    rows = [f"| {m['what']} | {m['value']}{m['unit']} | {m['pass']['op']} {m['pass']['value']} | {'통과' if m['ok'] else '실패'} |"
            for m in record.get("metrics") or []]
    text = "\n".join([f"## 실측 {record['n']} · `{record['head'][:12]}` · 주인 승인", "",
                      "| 측정 | 값 | 기준 | 결과 |", "| --- | --- | --- | --- |", *rows, "",
                      f"주인 메모: {record['owner']['note'] or '(없음)'}", "",
                      f"스크립트 메모: {record.get('notes') or '(없음)'}"])
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
        fh.write(text)
    try:   # GitHub's own head, read just before: a push from elsewhere since PR #1 was published is not approved
        now = specs.sh(["gh", "pr", "view", str(pr["number"]), "--json", "headRefOid", "-q", ".headRefOid"],
                       Path(spec["worktree"]), 60)
        if now.returncode or now.stdout.strip() != record["head"]:
            return f"PR #{pr['number']} 이 지금 승인한 HEAD `{record['head'][:12]}` 를 보이지 않는다 — {specs.said(now)}"
        done = specs.sh(["gh", "pr", "comment", str(pr["number"]), "--body-file", fh.name], Path(spec["worktree"]), 60)
        return f"PR #{pr['number']} 에 실측 기록을 달지 못했다 — {specs.said(done)}" if done.returncode else ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"PR #{pr['number']} 에 실측 기록을 달지 못했다 — {exc}"
    finally:
        os.unlink(fh.name)


@router.get("/api/oneshot/spec/{repo}/measure/{n}/clips/{name}")
def clip(repo: str, n: int, name: str) -> FileResponse:
    spec = oneshot_spec.spec_of(repo)
    record = next((m for m in spec["oneshot"].get("measures") or [] if m["n"] == n), None)
    if not record or name not in (record.get("clips") or []):
        raise HTTPException(404, "그런 클립이 없다")
    file = oneshot_spec.store(repo).folder / "measure" / record["folder"] / "clips" / name
    if not plain(file) or not all(plain(d, stat.S_IFDIR) for d in (file.parent, file.parent.parent)):   # no link
        raise HTTPException(404, "클립 파일이 없다")
    return FileResponse(file, media_type=CLIPS[file.suffix.lower()])


# -- The screen -------------------------------------------------------------------------------------------

def shown(spec: dict) -> dict:
    """The measurement's plan and its inputs, given or not (never a secret's value), the runs, and PR #1."""

    o = spec["oneshot"]
    if not o.get("built_head") or not (o.get("measures") or o.get("stage") == "measure"):
        return {}
    plan, error = None, ""
    if o.get("stage") == "measure":
        try:
            plan = planned(Path(spec["worktree"]), spec, o["built_head"])
        except ValueError as exc:
            error = str(exc)
    inputs = [{**x, "given": has(spec, x)} for x in (plan or {}).get("inputs") or []]
    runs = [{k: v for k, v in m.items() if k != "folder"} for m in o.get("measures") or []]
    return {"measure": {"plan": plan and {**plan, "inputs": inputs}, "error": error, "runs": runs,
                        "pr": o.get("pr1"), "head": o["built_head"]}}


oneshot_spec.VIEWS.append(shown)
CHANGES: dict[str, int] = {}   # stored-value changes per repository, so a measurement sees one made while it ran


def secret_changed(repo: str, env: str) -> None:
    """A secret changed by any route (these boxes, the R1 sheet, `forget`): a waiting result no longer ran with
    it, and a running one is set aside when it ends."""

    CHANGES[repo] = CHANGES.get(repo, 0) + 1
    retire(oneshot_spec.spec_of(repo), "비밀 값이 바뀌었다 — 이 결과는 판정하지 않고 다시 잰다")


oneshot_needs.CHANGED.append(secret_changed)
oneshot_spec.SHEETS.add("measure")
oneshot_spec.WAITS.add("measure")
