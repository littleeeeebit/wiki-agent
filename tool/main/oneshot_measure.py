"""oneshot step 12: the backend runs against the real services, between its build and its review.

`docs/plans/oneshot/12-review-converges-on-measurement.md` is the design. The build agent commits
`.wiki/measure.json` (`PLAN`):
- how to start the backend headless (`serve`), or, in a run that builds a PC app, the program itself (`app`, plan 13);
- the script that measures it against the real services (`measure`);
- what the owner gives it (`inputs`), each with the steps that get it, window by window;
- what the owner does while it runs (`during`);
- each metric with its threshold.

The server checks the plan and opens pull request #1 as a draft (`published`). It starts the backend in a job of
its own, runs the script with the owner's inputs, and keeps the report and the clips. It measures by itself first
(`auto`) until every metric that needs no person passes, each failure going back to the build agent; then the owner
measures once (`owner`), listens and decides. Approval sends the run to the review, which reads the measurement; a
failed measurement, or the owner's refusal, sends it back to the build agent.
"""

from __future__ import annotations

import json
import operator
import os
import re
import shutil
import stat
import tempfile
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from common import masked

from . import (oneshot, oneshot_build, oneshot_measure_app as product, oneshot_needs, oneshot_profile, oneshot_publish,
               oneshot_spec, release, runtime, specs, work)
from .oneshot_kept import (CLIPS, DISPUTED, LAUNCHER, MAX_CLIPS, clips_of, committed, fresh, handed,  # noqa: F401
                           RUNNING, input_report, kept_from, label_of, launched, plain, shown, spelled, strict, tail)
from .oneshot_publish import commented
from .query import _lock as work_lock, hold

router = APIRouter()
PLAN = ".wiki/measure.json"
IID = re.compile(r"M[1-9]\d*")
MID = re.compile(r"[a-z][a-z0-9_]{0,40}")
OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq}
MAX_MINUTES, MAX_SERVE_SECONDS = 60, 600
MAX_TEXT = 2_000   # an input's typed value, and the owner's note
CREDENTIAL = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION", re.I)
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
        raise ValueError(f"`{PLAN}` 는 serve(또는 app), measure, inputs, metrics 를 담은 객체다")
    desktop = "desktop" in oneshot_profile.kinds_of(o)   # the program the owner installs, never a headless server
    part = "app" if desktop else "serve"
    if ("serve" if desktop else "app") in value:
        raise ValueError("PC 앱을 만드는 실행의 실측은 `app` 으로 그 프로그램을 띄운다 — `serve` 는 적지 않는다" if desktop
                         else "PC 앱을 만들지 않는 실행의 실측은 `serve` 로 백엔드를 띄운다 — `app` 은 적지 않는다")
    serve, measure = value.get(part), value.get("measure")
    if desktop:
        run = product.app_of(serve)
    elif not isinstance(serve, dict) or not isinstance(serve.get("command"), str) \
            or not oneshot_build.chained(serve["command"]):
        raise ValueError(f"`serve.command` 는 백엔드를 화면 없이 띄우고 계속 도는 명령이고, {oneshot_build.CHAIN}")
    if not isinstance(serve.get("health"), str) or not HEALTH.fullmatch(serve["health"]):
        raise ValueError(f"`{part}.health` 는 떠 있는 {'프로그램' if desktop else '백엔드'}가 200 으로 답하는 "
                         "http://127.0.0.1:포트/경로 주소다")
    seconds = serve.get("seconds", 120)
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 5 <= seconds <= MAX_SERVE_SECONDS:
        raise ValueError(f"`{part}.seconds` 는 {'프로그램' if desktop else '백엔드'}가 뜨기를 기다릴 초, 5–{MAX_SERVE_SECONDS} 다")
    if not isinstance(measure, dict) or not isinstance(measure.get("command"), str) \
            or measure["command"].count("{out}") != 1 or not oneshot_build.chained(measure["command"].replace("{out}", "x")):
        raise ValueError(f"`measure.command` 는 결과 폴더 자리 `{{out}}` 를 한 번 담은 실측 명령이고, {oneshot_build.CHAIN}")
    minutes = measure.get("minutes")
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= MAX_MINUTES:
        raise ValueError(f"`measure.minutes` 는 실측이 걸리는 분, 1–{MAX_MINUTES} 다")
    started = measure["command"].replace("{out}", "x") if desktop else f"{serve['command']} && {measure['command'].replace('{out}', 'x')}"
    if (tools := oneshot_build.tools(o)) is not None and (bad := oneshot_build.undeclared(path, started, tools)):
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
        if launched(x["env"]):
            raise ValueError(f"{where} {x['id']}: `{x['env']}` 는 실측 스크립트를 띄우는 쪽이 정하는 이름이다 — 다른 이름을 쓴다")
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
    serving = [s.get("metrics") for s in during]
    if not all(isinstance(ids, list) and ids and all(isinstance(i, str) for i in ids) for ids in serving):
        raise ValueError("during 의 단계마다 `metrics` 에 그 단계가 있어야 잴 수 있는 측정값 id 를 적는다")
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
        if not isinstance(m.get("person", False), bool):
            raise ValueError(f"{where} {m['id']}: `person` 은 사람이 during 단계를 해야 잴 수 있으면 true 다")
        covered |= set(map(str, cl))
        metrics.append({"id": m["id"], "what": " ".join(m["what"].split()), "unit": str(m.get("unit") or ""),
                        "requirements": list(map(str, rq)), "calls": list(map(str, cl)), "pass": bound,
                        "person": m.get("person", False)})
    if not metrics or len({m["id"] for m in metrics}) != len(metrics):
        raise ValueError("`metrics` 는 id 가 겹치지 않는 하나 이상의 측정값이다")
    person = {m["id"] for m in metrics if m["person"]}
    if stray := sorted({i for ids in serving for i in ids} - person):
        raise ValueError(f"during 단계가 적은 측정값 {', '.join(stray)} 은 person: true 인 측정값이 아니다")
    if unserved := sorted(person - {i for ids in serving for i in ids}):
        raise ValueError(f"person: true 인 측정값 {', '.join(unserved)} 을 재게 하는 during 단계가 없다")
    if missing := sorted(calls - covered):
        raise ValueError(f"실측이 닿지 않는 바깥 호출이 있다: {', '.join(missing)} — 측정값의 `calls` 에 넣어 진짜로 부른다")
    if reserved := sorted({n["env"] for _, n in oneshot_needs.secret_needs(spec) if n["env"].upper() == product.MODE}):
        raise ValueError(f"디자인의 비밀 값 이름 `{reserved[0]}` 는 실측이 스크립트와 프로그램에 넘기는 이름이다 — 디자인에서 "
                         "그 비밀 값의 이름을 바꾼다")
    if desktop and (clash := sorted(({x["env"].upper() for x in inputs} | {n["env"].upper() for _, n in
                                     oneshot_needs.secret_needs(spec)}) & {k.upper() for k in run["env"]})):
        raise ValueError(f"`app.run.env` 의 `{clash[0]}` 는 입력값이나 비밀값의 이름이다 — 실행 설정(`{{data}}` 자리)을 "
                         "덮지 않게 다른 이름을 쓴다")
    launch = {"app": {"run": run, "health": serve["health"], "seconds": seconds}} if desktop else \
        {"serve": {"command": serve["command"].strip(), "health": serve["health"], "seconds": seconds}}
    return {**launch, "measure": {"command": measure["command"].strip(), "minutes": minutes},
            "inputs": inputs, "during": during, "metrics": metrics, "clips": bool(value.get("clips"))}


def planned(path: Path, spec: dict, rev: str | None = None) -> dict:
    """The worktree's plan, checked; at commit `rev` when given: the built HEAD's, whatever the tree holds now."""

    try:
        if rev and (shown := committed(path, rev, PLAN)) is None:   # its bytes, decoded strictly: never repaired
            raise FileNotFoundError
        value = strict(shown.decode("utf-8") if rev else (path / PLAN).read_text(encoding="utf-8"))
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


def env_of(spec: dict, plan: dict, base: dict | None = None) -> tuple[dict, dict[str, str]]:
    """The measurement's environment: the gate's (or `base`), with the design's secrets, and each input under its
    `env`; and what its output shows instead of each secret. A secret, the design's or the plan's, that cannot be
    read now raises `ValueError`; one the store does not hold is left out, so a variable of its name the server has
    never stands in for it. Each secret is read once: an input the design names too takes the design's read."""

    if base is None:   # `oneshot_build.built_env`, its design secrets read strictly
        android = "android" in oneshot_build.kinds_of(spec["oneshot"])
        base = oneshot_needs.gate_env(spec, oneshot_build.oneshot_android.build_env() if android else None, True)
    env = dict(os.environ if base is None else base)   # an empty filtered one stays empty: never the server's again
    design = {n["env"].upper() for _, n in oneshot_needs.secret_needs(spec)}
    secrets = {}
    for x in plan["inputs"]:
        if x["kind"] == "secret":
            try:
                value = ({k.upper(): v for k, v in env.items()}.get(x["env"].upper()) if x["env"].upper() in design
                         else release.secret_read(oneshot_needs.name(spec["repo"], x["env"])))
            except (OSError, ValueError):
                raise ValueError(f"`{x['env']}` 의 비밀값을 지금 읽지 못했다 — 다시 시도한다") from None
            env.pop(x["env"], None)
            if value:
                env[x["env"]] = secrets[x["env"]] = value
        elif value := (kept(spec).get(x["env"]) or {}).get("value"):
            env[x["env"]] = value
    # A value the server holds under a credential's name is masked as well, though no input names it: a script
    # that prints its environment shows none of them on the card.
    named = {k: v for k, v in env.items() if CREDENTIAL.search(k) and len(v) >= 8 and k not in secrets}
    return env, spelled(oneshot_needs.hidden(spec, env, {**named, **secrets}))   # every reader of the run's text masks the JSON-escaped spellings too


# -- Starting what is measured: `oneshot_measure_app` ------------------------------------------------

def serve_check(path: Path, run, spec: dict) -> str:
    """At the build's gate: the plan holds and the backend starts and answers its health address, or why not."""

    try:
        plan = planned(path, spec, "HEAD")   # the plan the commit holds, as the measurement reads it
        env, hide = env_of(spec, plan)
        log = Path(tempfile.mkdtemp(prefix="oneshot-serve-")) / "serve.log"
        try:
            with product.started(spec, plan, path, {**env, product.MODE: "auto"}, run.halt, log, hide, "auto",
                                 seen=False):   # only its health address is read: the owner sees the measurement
                pass
        finally:
            error = None
            for _ in range(40):   # a file the ended backend held a moment ago: Windows lets it go shortly
                try:
                    shutil.rmtree(log.parent)
                except FileNotFoundError:
                    break
                except OSError as exc:
                    error = exc
                    time.sleep(0.25)
                if not log.parent.exists():
                    break
            if log.parent.exists():   # never left quietly: the owner's, not the build's (`RuntimeError`)
                raise RuntimeError(f"빌드 검사의 임시 로그 폴더 `{log.parent}` 를 지우지 못했다"
                                   f"{f' ({type(error).__name__}: {error})' if error else ''} — 손으로 지운 뒤 [재개]를 누른다")
    except ValueError as exc:
        return masked(str(exc), hide) if "hide" in locals() else str(exc)
    if specs.dirty(path):   # the HEAD the build passed is the one measured, so the tree stays as committed
        return "백엔드를 띄우자 작업트리가 바뀌었다 — 백엔드가 쓰는 파일(데이터베이스, 로그)은 .gitignore 에 둔다"
    specs.note(run, "PC 프로그램이 떠서 상태 주소에 답했다" if "app" in plan else "백엔드가 화면 없이 떠서 상태 주소에 답했다")
    return ""


def waiting(record: dict | None) -> bool:
    """Whether `record` is the owner's own measurement, waiting for their verdict. A result from before plan 13,
    which has no `mode`, is never one: it never had the server's own pass, so the server measures again. An
    approval the owner gave before plan 13 stands, as the plan says."""

    return bool(record) and record["status"] == "owner" and record.get("mode") == "owner" and not record.get("owner")


def next_mode(o: dict) -> str:
    """`owner` once the server's own measurement of the built HEAD passed every metric that needs no person, and
    nothing since; else `auto`: the owner is called only when a machine has nothing left to find."""

    last = (o.get("measures") or [None])[-1]
    return "owner" if last and last.get("mode") == "auto" and last["status"] == "passed" \
        and last["head"] == o.get("built_head") else "auto"


# -- One measurement ---------------------------------------------------------------------------------------

def report_of(out: Path, plan: dict, hide: dict[str, str]) -> dict:
    """`report.json` the script wrote, read against the plan: each metric's value and whether it passed, the input
    the script says was wrong, and its notes, none of them showing a secret."""

    try:
        if not plain(out, stat.S_IFDIR) or not plain(out / "report.json"):   # never read through a link
            raise FileNotFoundError("없거나, 링크이거나, 일반 파일이 아니다")
        value = strict((out / "report.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, RecursionError) as exc:
        return {"error": f"실측 명령이 `{{out}}/report.json` 을 쓰지 않았거나 JSON 이 아니다 — {masked(str(exc), hide)}"}
    except OSError as exc:   # the disk, not the build: the owner's stop, never a counted failure
        return {"io": f"실측 보고서를 읽지 못했다 ({type(exc).__name__}) — 디스크를 확인하고 [실측 시작]을 누른다"}
    if not isinstance(value, dict):
        return {"error": "`report.json` 은 metrics 를 담은 객체다"}
    if "input_error" in value:   # in any shape: one that is not an input's is a failed report
        return input_report(value["input_error"], plan, hide, MAX_TEXT)
    got = value.get("metrics") if isinstance(value.get("metrics"), dict) else {}
    rows = []
    for m in plan["metrics"]:
        v = got.get(m["id"])
        # A truth value only against a truth value and a number only against a number: Python's `False == 0`
        # would pass a metric the script could not observe and wrote as `false`.
        number = isinstance(v, (int, float)) and isinstance(v, bool) == isinstance(m["pass"]["value"], bool) \
            and (not isinstance(v, bool) or m["pass"]["op"] == "==") \
            and label_of(v, hide) is None   # a value that is a secret is never shown as a measurement
        ok = bool(number and OPS[m["pass"]["op"]](v, m["pass"]["value"]))
        rows.append({**m, "value": v if number else None, "ok": ok})
    return {"metrics": rows, "notes": masked(str(value.get("notes") or ""), hide)[:MAX_TEXT]}


def measure(repo: str, mode: str | None = None) -> None:
    """One measurement of the built HEAD: `auto` by the server, `owner` on the owner's screen, the one the record
    calls for when the worker reads it (`next_mode`). `mode` forces one, for a test; the server never passes it."""

    from . import oneshot_review   # it imports this module

    halt = HALTS.setdefault(repo, Halted())   # `start` made a fresh one: a stop pressed since still holds

    def body(store, spec, tree):
        o = spec["oneshot"]
        try:
            plan = planned(tree, spec, o["built_head"])   # the built HEAD's, as Start checked it: never the tree's
            if missing := [x["id"] for x in plan["inputs"] if not has(spec, x)]:
                raise ValueError(f"아직 넣지 않은 입력이 있다: {', '.join(missing)}")
            if (o.get("pr1") or {}).get("head") != o["built_head"]:
                oneshot_publish.published(repo)   # the HEAD measured is the one pull request #1 shows
        except ValueError as exc:
            store.stop("measure", str(exc))
            return None
        with oneshot._lock:   # every stored-value change counts under this lock: the values and the count agree
            now = oneshot_spec.spec_of(repo)   # what the owner gave by now, a value taken back since included
            runs = now["oneshot"].get("measures") or []   # whose measurement, read with the values it measures
            n, how = len(runs) + 1, mode or next_mode(now["oneshot"])
            passed = runs[-1] if how == "owner" and runs else None
            if passed and "app" in plan and (why := product.changed(passed.get("build"))):
                retire(now, why)   # the pass no longer stands for what would open: the server measures again
                store.stop("measure", f"{why} — [자동 실측 시작]으로 다시 잰다")
                return None
            try:   # one read gives the presence, the child's environment and the mask
                env, hide = env_of(now, plan)
            except ValueError as exc:
                store.stop("measure", str(exc))
                return None
            if missing := [x["id"] for x in plan["inputs"]   # a secret by the read just made, never read again
                           if x["env"] not in env or x["kind"] != "secret" and not has(now, x)]:
                store.stop("measure", f"아직 넣지 않은 입력이 있다: {', '.join(missing)}")   # no failure counted
                return None
            since = oneshot_needs.CHANGES.get(repo, 0)
            env[product.MODE] = how   # the script and the program both know whose measurement it is
        out = store.folder / "measure" / f"m{n}-{o['built_head'][:12]}"
        raw = out.with_name(out.name + ".raw")   # what the script writes; only `kept_from` copies leave it
        if why := fresh(out, raw):   # made only once the run goes ahead: an early stop leaves no folder
            store.stop("measure", why)
            return None
        record, failed = {"n": n, "head": o["built_head"], "ts": time.time(), "folder": out.name, "mode": how}, None
        # The card's while it runs, this worker's alone (`halt`, fresh per start): its cut-off once the script starts.
        RUNNING[repo] = {"n": n, "head": o["built_head"], "mode": how, "ts": record["ts"], "cut": None, "ended": False,
                         "halt": halt}
        try:
            with product.started(now, plan, tree, env, halt, raw / "serve.log", hide, how,
                                 (passed or {}).get("build")) as build:
                if build:   # the frozen copy measured: the owner's measurement starts this one, never a new one
                    record["build"] = build
                limit = plan["measure"]["minutes"] * 60 + 300
                RUNNING[repo]["cut"] = time.time() + limit   # the script's own clock starts now, after the launch
                try:
                    code, text, cut = specs.gate(oneshot_build.run_as(plan["measure"]["command"].replace(
                        "{out}", oneshot_build.literal(raw.as_posix())), o, tree), tree, halt, env=env, timeout=limit,
                        idle=limit, hide=hide)
                finally:   # returned or raised, before the teardown: what is left is closing and the report
                    RUNNING[repo].update(cut=None, ended=True)
        except ValueError as exc:
            code, text, cut = None, "", masked(str(exc), hide)
        except Exception as exc:   # unforeseen: a masked owner stop once the folder is dealt with
            failed = exc
        finally:
            try:
                found = report_of(raw, plan, hide)
            finally:
                left = kept_from(raw, out, hide)   # every exit, a stop or a failed read included, keeps no secret
        if left:   # the copy failed, or the script's own folder is still there: the owner's reason, before any other
            store.stop("measure", left)
            return None
        if failed:   # unforeseen, a launch error naming an input included: its text masked like every other reason
            store.stop("measure", f"실측이 예상하지 못한 오류로 멈췄다 — {masked(f'{type(failed).__name__}: {failed}', hide)}")
            return None
        if found.get("io"):   # the report could not be read off the disk: the owner's, the build is not charged
            store.stop("measure", found["io"])
            return None
        record["tail"] = (text or "").strip()[-3000:]
        if halt.is_set() and getattr(halt, "told", False) and not runtime.stopping.is_set():
            return lambda: decided(repo, store, record, since, hide)   # the owner spoke: the words are taken in
        if halt.is_set():
            store.stop("measure", masked("사람이 실측을 멈췄다 — 다시 하려면 [실측 시작]을 누른다" if not runtime.stopping.is_set()
                                         else "서버가 꺼져 실측이 멈췄다 — 다시 켠 뒤 [실측 시작]을 누른다", hide))
            return None
        found = found if not cut else {"error": cut}
        record |= found | {"clips": clips_of(out, hide), "code": code}
        # The server's own measurement judges only what needs no person; the owner's judges every metric.
        judged = [m for m in found.get("metrics") or [] if how == "owner" or not m["person"]]
        if "input_error" in found:
            record["status"] = "input"
        elif found.get("error") or code or not all(m["ok"] for m in judged) or plan["clips"] and not record["clips"]:
            record["status"] = "failed"
            if not found.get("error") and plan["clips"] and not record["clips"]:
                record["error"] = "변환한 소리를 들어 볼 클립이 `{out}/clips` 에 없다"
        else:
            record["status"] = "owner" if how == "owner" else "passed"
        return lambda: decided(repo, store, record, since, hide)

    oneshot_review.holding(repo, "실측", body)


def decided(repo: str, store, record: dict, since: int, hide: dict[str, str] | None = None):
    """`result` of a measurement whose run started at change count `since`: compared and saved under the lock every
    stored-value change takes, so a change made at any moment of the run sets it aside."""

    with oneshot._lock:
        if (halt := HALTS.get(repo)) is not None and halt.is_set() and getattr(halt, "told", False) \
                and not runtime.stopping.is_set():   # the owner spoke: their words are taken in, then built
            from . import oneshot_tell   # it imports this module's neighbours
            return lambda: oneshot_tell.handoff(store)   # run once `holding` lets the tree go
        if (halt := HALTS.get(repo)) is not None and halt.is_set():   # `stop` sets it under this lock too
            store.stop("measure", masked("사람이 실측을 멈췄다 — 다시 하려면 [실측 시작]을 누른다" if not runtime.stopping.is_set()
                                         else "서버가 꺼져 실측이 멈췄다 — 다시 켠 뒤 [실측 시작]을 누른다", hide))
            return None
        if oneshot_needs.CHANGES.get(repo, 0) != since:   # every sentence the owner reads masked whole (13e)
            record = {**record, "status": "stale", "error": masked("실측하는 동안 비밀 값이 바뀌었다 — [실측 시작]으로 다시 잰다", hide)}
        return result(repo, store, record, hide)


def result(repo: str, store, record: dict, hide: dict[str, str] | None = None):   # `hide`: the report's mask
    """A measurement saved. One the owner must judge waits; a failure goes to the build agent, up to the review's
    round cap; a wrong input is one too, the owner's only once the build was sent it and names a check (13e)."""

    from . import oneshot_review   # it imports this module

    o = store.get()
    runs = [*(o.get("measures") or []), record]
    if record["status"] == "owner":   # it ran through every input: no dispute stands
        oneshot_spec.saved(repo, oneshot={"measures": runs, "working": None, "input_sent": None, "input_fails": 0,
                                          "input_owed": None})
        return None
    if record["status"] == "passed":   # every machine metric passed: the owner measures once, on their own screen
        oneshot_spec.saved(repo, oneshot={"measures": runs, "working": None, "measure_fails": 0})
        work.notice("자동 실측 통과 · 주인 실측을 기다린다", f"{repo} · [주인 실측 시작]을 누르면 " + (
            "통과한 PC 프로그램이 이 화면에 뜨고 소리가 난다" if record.get("build") else "사람이 필요한 항목까지 잰다"))
        return None
    if record["status"] == "stale":   # an input changed while it ran: no fault of the build, measured again
        oneshot_spec.saved(repo, oneshot={"measures": runs})
        store.stop("measure", masked(record["error"], hide))
        return None
    fails, disputes = int(o.get("measure_fails") or 0) + 1, int(o.get("input_fails") or 0)
    if record["status"] == "input":   # the owner gave it: the build proves it wrong before the owner is asked
        bad = record["input_error"]
        # The build was sent it: the owner's. Their press sends the next report to the build, all counts from zero.
        if bad["asked"] and {k: bad[k] for k in DISPUTED} in (o.get("input_sent") or []):
            oneshot_spec.saved(repo, oneshot={"measures": runs, "input_sent": None, "input_fails": 0, "measure_fails": 0,
                                              "input_owed": True})   # whatever the cap, the next report is the build's
            store.stop("measure-input", masked(bad["asked"], hide))
            return None
        disputes += 1   # every input's, kept through a machine pass: one only the owner's measurement uses reaches the cap
        fails = max(fails, disputes)
        runs[-1] = record = {**record, "status": "failed", "error": masked(bad["said"], hide)}
    if fails >= oneshot_review.cap() and not o.get("input_owed"):   # else sent by `start`, the owner's retry
        oneshot_spec.saved(repo, oneshot={"measures": runs, "measure_fails": fails, "input_fails": disputes})
        store.stop("measure", masked(f"실측이 {fails}번 실패했다 — {failure(record)}", hide))   # masked whole, as shown
        return None
    spec = oneshot_spec.saved(repo, oneshot={"measures": runs, "measure_fails": fails, "stage": "build", "fix": "measure",
                                             "attempts": 0, "working": "build", "input_fails": disputes, "input_sent": handed(o, record),
                                             "input_owed": None})
    text = fixing(spec)
    return lambda: oneshot_build.send(repo, "build", text)


def failure(record: dict) -> str:
    if record.get("error"):
        return record["error"]
    bad = [f"{m['id']}={m['value']} (기준 {m['pass']['op']} {m['pass']['value']})" for m in failed_metrics(record)]
    return ", ".join(bad) or f"실측 명령이 {record.get('code')} 로 끝났다"


def failed_metrics(record: dict) -> list[dict]:
    """The metrics that failed the measurement: in the server's own, only those that need no person."""

    return [m for m in record.get("metrics") or [] if not m["ok"]
            and (record.get("mode") != "auto" or not m.get("person"))]


def fixing(spec: dict) -> str:
    """The build turn after the committed plan no longer held, the measurement of the built HEAD failed, or the
    owner refused it; empty when none of them stands. `[재개]` sends it again (`oneshot_review.build_turn`)."""

    o = spec["oneshot"]
    if o.get("fix") == "plan":
        return (f"The committed `{PLAN}` no longer holds: {o.get('broken')}\n\nFix it and end this turn on a new "
                "commit; the measurement runs before any review.\n\n" + oneshot_build.build_text(spec))
    last = (o.get("measures") or [None])[-1]
    if not last or last["head"] != o.get("built_head"):
        return ""
    if last["status"] == "failed":
        auto, bad = last.get("mode") == "auto", last.get("input_error")
        why = (f"The {'server' + chr(39) + 's own ' if auto else ''}real measurement of HEAD `{last['head']}` failed: "
               + (f"the measuring script reported the owner's input `{bad['input']}` as wrong: {bad['why']}\n\nThe owner's "
                  "input stands: check it against the real service outside the code that refused it and fix the program, "
                  "or name that check in `checked` as the measurement contract below says.\n\n" if bad else f"{failure(last)}.\n\n")
               + ("Only the metrics that failed are below; those that need a person were not judged in this "
                  "measurement.\n\n" if auto else "")
               + f"The metrics {'that failed' if auto else 'as measured'}:\n\n```json\n"
               f"{json.dumps(failed_metrics(last) if auto else last.get('metrics') or [], ensure_ascii=False, indent=2)}\n```\n\n"
               + (f"The report's notes:\n\n```\n{last['notes']}\n```\n\n" if last.get("notes") else "")
               + f"The end of the measuring script's output:\n\n```\n{last.get('tail', '')[-1500:]}\n```\n\n"
               f"The end of the log of what it measured (the program, or the backend):\n\n```\n"
               f"{tail(oneshot_spec.store(spec['repo']).folder / 'measure' / last.get('folder', '') / 'serve.log', {})}\n```\n")
    elif (last.get("owner") or {}).get("approve") is False:
        why = (f"The owner listened to the real measurement of HEAD `{last['head']}` and refused it. They wrote, in "
               f"their own words:\n\n> {last['owner']['note']}\n\nThe metrics as measured:\n\n```json\n"
               f"{json.dumps(last.get('metrics') or [], ensure_ascii=False, indent=2)}\n```\n")
    else:
        return ""
    return (why + "\nFind the cause in the code against the real services, fix it, and end this turn on a new "
            "commit; the measurement runs again before any review.\n\n" + oneshot_build.build_text(spec))


# -- What the owner does ----------------------------------------------------------------------------------

def at_measure(repo: str) -> dict:
    """The spec while the run is at the measurement with no worker running; otherwise 409."""

    o = (spec := oneshot_spec.spec_of(repo))["oneshot"]
    oneshot_spec.supported(o)
    if o.get("stage") != "measure" or o.get("working"):
        raise HTTPException(409, f"지금 할 수 없다 — {o.get('stage')}{' 진행 중' if o.get('working') else ''}")
    return spec


def measuring(repo: str, plan_needed: bool = True) -> tuple[dict, dict | None, str]:
    """`(spec, plan, why the plan does not hold)` while the run is at the measurement with no worker running."""

    o = (spec := at_measure(repo))["oneshot"]
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
    """The input the owner's screen showed, or 409 when the plan they followed is not this one. Its change retires
    a result waiting for the owner's verdict: a secret's through `oneshot_needs.stored`, before the store changes;
    a plain value's in the same save as the value (`plain_input`)."""

    x = next((x for x in plan["inputs"] if x["id"] == iid), None)
    if x is None:
        raise HTTPException(404, f"실측 계획에 없는 입력이다: {iid}")
    if (body.head, body.env, body.kind) != (spec["oneshot"].get("built_head"), x["env"], x["kind"]):
        raise HTTPException(409, "실측 계획이 그 사이 바뀌었다 — 다시 불러와 새 칸에 넣는다")
    return x


def plain_input(spec: dict, given: dict) -> dict:   # the same values again change nothing, a waiting result included
    return spec if given == kept(spec) else retire(   # a changed value is the build's to see first again (13e)
        spec, "입력이 바뀌었다 — 이 결과는 판정하지 않고 바뀐 입력으로 다시 잰다", measure_given=given, input_sent=None, input_fails=0,
        input_since=len(spec["oneshot"].get("measures") or []))


def retire(spec: dict, why: str, top: dict | None = None, **fields) -> dict:
    """The result waiting for the owner, their verdict or their measurement after the server's own pass, set
    aside (`stale`) with `why`, saved with `fields` (and the spec's own `top` fields) in one save: what it
    measured is no longer what would be judged."""

    runs = list(spec["oneshot"].get("measures") or [])
    if runs and runs[-1]["status"] in ("owner", "passed") and not runs[-1].get("owner"):
        runs[-1] = {**runs[-1], "status": "stale", "error": why}
        fields["measures"] = runs
    return oneshot_spec.saved(spec["repo"], oneshot=fields, **(top or {})) if fields or top else spec


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
            plain_input(spec, {**kept(spec), x["env"]: {"value": value, "what": x["what"]}})
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


@router.delete("/api/oneshot/spec/{repo}/measure/inputs/{iid}")
def take_back(repo: str, iid: str, head: str = "", env: str = "", kind: str = "") -> dict:
    with oneshot._lock:
        spec, plan, _ = measuring(repo)
        x = shown_input(spec, plan, iid, Value(head=head, env=env, kind=kind))
        if x["kind"] == "secret":
            oneshot_needs.stored(repo, x["env"], None)
        else:
            plain_input(spec, {k: v for k, v in kept(spec).items() if k != x["env"]})
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


class Start(BaseModel):
    head: str = ""   # the built HEAD the owner saw; any other is refused with 409


@router.post("/api/oneshot/spec/{repo}/measure/start")
def start(repo: str, body: Start) -> dict:
    with oneshot._lock:   # the request checked, then the owner's waiting words, then the old plan (`oneshot_tell`)
        if body.head != at_measure(repo)["oneshot"].get("built_head"):
            raise HTTPException(409, "빌드가 그 사이 바뀌었다 — 다시 불러와 새 화면에서 시작하라")
        if oneshot.diverted(oneshot_spec.store(repo), "measure"):
            return oneshot_spec.view(oneshot_spec.spec_of(repo))
        spec, plan, broken = measuring(repo, plan_needed=False)
        o = spec["oneshot"]   # a measurement stopped (a wrong input, a restart) starts again from here
        if broken:   # the committed plan no longer holds: only the build agent can fix it
            spec = oneshot_spec.saved(repo, oneshot={"stage": "build", "fix": "plan", "broken": broken, "attempts": 0,
                                                     "working": "build", "stopped": None})
            text = fixing(spec)
            then = lambda: oneshot_build.send(repo, "build", text)   # noqa: E731
        elif (last := (o.get("measures") or [None])[-1]) and last["head"] == o["built_head"] \
                and last["status"] == "failed":   # the capped failure goes to the build agent, never measured again
            spec = oneshot_spec.saved(repo, oneshot={"stage": "build", "fix": "measure", "attempts": 0, "measure_fails": 0, "working": "build",
                                                     "stopped": None, "input_fails": 0, "input_sent": handed(o, last)})
            text = fixing(spec)
            then = lambda: oneshot_build.send(repo, "build", text)   # noqa: E731
        else:   # the same lock scope decides and launches: no other request's result slips in between
            if missing := [x["id"] for x in plan["inputs"] if not has(spec, x)]:
                raise HTTPException(400, f"아직 넣지 않은 입력이 있다: {', '.join(missing)}")
            if last and last["head"] == o["built_head"] and waiting(last):
                raise HTTPException(409, "이 빌드의 실측이 판정을 기다린다 — 듣고 승인하거나 거절한다")
            HALTS[repo] = Halted()   # before the worker: a stop pressed as it starts is kept
            oneshot.start_worker(oneshot_spec.store(repo), "measure", measure, (repo,), {})
            then = None
    if then:   # the build agent's turn goes after the lock is let go
        then()
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


@router.post("/api/oneshot/spec/{repo}/measure/stop")
def stop(repo: str) -> dict:
    with oneshot._lock:   # a stop accepted here is seen by `decided`, which saves the outcome under it
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
                or not waiting(last):
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
            try:   # the mask, read apart from the launch toolchain: every secret of the plan, or none
                env, hide = env_of(spec, plan, oneshot_needs.gate_env(spec, dict(os.environ), True)) if plan \
                    else ({}, None)
                if hide is not None and not all(x["env"] in env for x in plan["inputs"] if x["kind"] == "secret"):
                    hide = None   # a secret the store no longer holds
            except (OSError, ValueError):   # a secret not read now
                hide = None
            if note and hide is None:   # a note is kept only behind a mask holding every secret
                raise HTTPException(409, "메모를 가릴 비밀값을 지금 읽지 못했다 — 메모 없이 승인하거나 다시 시도한다")
            hide = hide or {}   # no note: the record was masked when it was made
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
    on to the review by then. A failed save of it is told too, and never keeps the review from starting."""

    if why:
        try:
            runs = list(oneshot_spec.spec_of(repo)["oneshot"].get("measures") or [])
            runs[-1] = {**runs[-1], "comment_error": why}
            oneshot_spec.saved(repo, oneshot={"measures": runs})
        except Exception as exc:
            why = f"{why} (그 사유를 원샷 기록에 저장하지도 못했다: {type(exc).__name__})"
        work.notice("실측 기록을 PR 에 달지 못했다", f"{repo} · {why} — 기록은 원샷 화면에 남아 있다")


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


oneshot_spec.VIEWS.append(shown)   # the screen's fields, in `oneshot_kept`


def secret_changed(repo: str, env: str):   # the fields to put back if the store refuses
    """A secret about to change by any route (these boxes, the R1 sheet, `forget`): a waiting result no longer ran
    with it. A running one is set aside when it ends, by `oneshot_needs.CHANGES`, counted once the store changed."""
    o = (spec := oneshot_spec.spec_of(repo))["oneshot"]   # a changed value is the build's to see first again (13e)
    retire(spec, "비밀 값이 바뀌었다 — 이 결과는 판정하지 않고 다시 잰다", input_sent=None, input_fails=0, input_since=len(o.get("measures") or []))
    return {"measures": o.get("measures") or [], **{k: o.get(k) for k in ("input_sent", "input_fails", "input_since")}}


oneshot_needs.CHANGED.append(secret_changed)
oneshot_spec.SHEETS.add("measure")
oneshot_spec.WAITS.add("measure")
