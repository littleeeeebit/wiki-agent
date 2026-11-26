"""oneshot step 12: the backend runs against the real services, between its build and its review.

`docs/plans/oneshot/12-review-converges-on-measurement.md` is the design. The build agent commits
`.wiki/measure.json` (`PLAN`):
- how to start the backend headless (`serve`), or, in a run that builds a PC app, the program itself (`app`, plan 13);
- the script that measures it against the real services (`measure`);
- what the owner gives it (`inputs`), each with the steps that get it, window by window;
- what the owner does while it runs (`during`);
- each metric with its threshold.

The server checks the plan (`oneshot_measure_plan`) and opens pull request #1 as a draft (`published`). It starts
the backend in a job of its own, runs the script with the owner's inputs, and keeps the report and the clips. It
measures by itself first (`auto`) until every metric that needs no person passes; then the owner measures once
(`owner`), listens and decides. Approval sends the run to the review, which reads the measurement. A failed
measurement, the owner's refusal, a plan that no longer holds and a moved tree are repaired by the build agent
while the run stays at the measurement (plan 13); an input the script calls wrong is the owner's to answer.
"""

from __future__ import annotations

import json
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

from . import (oneshot, oneshot_build, oneshot_measure_app as product, oneshot_needs, oneshot_publish,
               oneshot_spec, release, runtime, specs, work)
from .oneshot_kept import (CLIPS, LAUNCHER, MAX_CLIPS, clips_of, committed, fresh,  # noqa: F401
                           RUNNING, input_report, kept_from, label_of, launched, plain, shown, spelled, strict, tail)
from .oneshot_measure_plan import (HEALTH, IID, MAX_MINUTES, MAX_SERVE_SECONDS, MID, OPS, PLAN,  # noqa: F401
                                   checked, planned, step_problem)
from .oneshot_publish import commented
from .query import _lock as work_lock, hold

router = APIRouter()
MAX_TEXT = 2_000   # an input's typed value, and the owner's note
CREDENTIAL = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION", re.I)


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
            with product.started(spec, plan, path, {**env, product.MODE: "auto"}, run.halt, log, hide, "auto"):
                pass   # only its health address is read, with the program on the owner's screen as always
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
            since = (secrets := reads(now, plan)), changes(repo, secrets)
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

    oneshot_review.holding(repo, "실측", body, again=moved_repair)


def moved_repair(repo: str, store, why: str, _build: str = "build"):   # `holding`'s `again`: never back a stage
    with oneshot._lock:   # a stop pressed while the tree was checked holds: no build turn starts after it
        if halted(repo, store):
            return None
        # A pass of a tree that moved stands for nothing: the repair, even on the same HEAD, is measured by the
        # machine first, and the owner's run waits for the owner's press.
        retire(oneshot_spec.spec_of(repo), f"실측하는 동안 작업트리가 바뀌었다 — {why}")
        return repair(repo, {"moved": why}, "moved")


def halted(repo: str, store, hide: dict[str, str] | None = None) -> bool:
    """A stop the owner pressed, or the server going down, saved as the measurement's; read under `oneshot._lock`,
    which `stop` sets it under too."""

    if (halt := HALTS.get(repo)) is None or not halt.is_set():
        return False
    store.stop("measure", masked("사람이 실측을 멈췄다 — 다시 하려면 [실측 시작]을 누른다" if not runtime.stopping.is_set()
                                 else "서버가 꺼져 실측이 멈췄다 — 다시 켠 뒤 [실측 시작]을 누른다", hide))
    return True


def reads(spec: dict, plan: dict) -> set[str]:
    """The secrets a measurement reads: the design's and the plan's secret inputs, by name."""

    return {n["env"].upper() for _, n in oneshot_needs.secret_needs(spec)} | {
        x["env"].upper() for x in plan["inputs"] if x["kind"] == "secret"}


def changes(repo: str, envs) -> int:   # how many changes the secrets named `envs` have seen (`oneshot_needs.CHANGES`)
    return sum(oneshot_needs.CHANGES.get(repo, {}).get(env, 0) for env in envs)


def decided(repo: str, store, record: dict, since: tuple, hide: dict[str, str] | None = None):
    """`result` of a measurement whose run started when the secrets it reads had `since` changes: compared and saved
    under the lock every stored-value change takes, so a change to one of them at any moment sets it aside."""

    with oneshot._lock:
        if (halt := HALTS.get(repo)) is not None and halt.is_set() and getattr(halt, "told", False) \
                and not runtime.stopping.is_set():   # the owner spoke: their words are taken in, then built
            from . import oneshot_tell   # it imports this module's neighbours
            return lambda: oneshot_tell.handoff(store)   # run once `holding` lets the tree go
        if halted(repo, store, hide):
            return None
        if changes(repo, since[0]) != since[1]:   # every sentence the owner reads masked whole (13e)
            record = {**record, "status": "stale", "error": masked("실측하는 동안 비밀 값이 바뀌었다 — [실측 시작]으로 다시 잰다", hide)}
        return result(repo, store, record, hide)


def result(repo: str, store, record: dict, hide: dict[str, str] | None = None):   # `hide`: the report's mask
    """A measurement saved. One the owner must judge waits; a failure goes to the build agent up to the round cap,
    the run kept at the measurement; a wrong input stops for the owner, whose input it is (`give`, `start`)."""

    from . import oneshot_review   # it imports this module

    o = store.get()
    runs = [*(o.get("measures") or []), record]
    if record["status"] == "owner":
        oneshot_spec.saved(repo, oneshot={"measures": runs, "working": None})
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
    if record["status"] == "input":   # never counted: the program is not shown wrong until the owner keeps the value
        oneshot_spec.saved(repo, oneshot={"measures": runs})
        store.stop("measure-input", masked(record["input_error"]["asked"], hide))
        return None
    fails = int(o.get("measure_fails") or 0) + 1
    if fails >= oneshot_review.cap():   # sent by `start`, the owner's retry
        oneshot_spec.saved(repo, oneshot={"measures": runs, "measure_fails": fails})
        store.stop("measure", masked(f"실측이 {fails}번 실패했다 — {failure(record)}", hide))   # masked whole, as shown
        return None
    return repair(repo, {"measures": runs, "measure_fails": fails})


def repair(repo: str, fields: dict, fix: str = "measure"):
    """The build turn repairing the measured program, `fields` saved with it, at the measurement (`turn_stage`)."""

    text = fixing(oneshot_spec.saved(repo, oneshot={**fields, "fix": fix, "attempts": 0, "working": "build", "stopped": None}))
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
    if o.get("fix") in ("plan", "moved"):
        return ((f"The committed `{PLAN}` no longer holds: {o.get('broken')}\n\nFix it and end this turn on a new "
                 "commit; the measurement runs before any review.\n\n" if o["fix"] == "plan" else
                 f"The worktree moved after HEAD `{o.get('built_head')}` passed the build check: {o.get('moved')}. Commit "
                 "what belongs to the build, or undo it, and finish the turn; the build check and a new measurement "
                 "follow.\n\n") + oneshot_build.build_text(spec))
    last = (o.get("measures") or [None])[-1]
    if not last or last["head"] != o.get("built_head"):
        return ""
    if last["status"] in ("failed", "input"):
        auto, bad = last.get("mode") == "auto", last.get("input_error")
        why = (f"The {'server' + chr(39) + 's own ' if auto else ''}real measurement of HEAD `{last['head']}` failed: "
               + (f"the measuring script reported the owner's input `{bad['input']}` as wrong: {bad['why']}\n\nThe owner "
                  "was shown this and kept the input as it is: it stands. Check it against the real service outside "
                  "the code that refused it and fix the program, or name that check in `checked` (contract below).\n\n"
                  if bad else f"{failure(last)}.\n\n")
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
    return spec if given == kept(spec) else retire(   # a changed value is measured again, never sent to the build
        spec, "입력이 바뀌었다 — 이 결과는 판정하지 않고 바뀐 입력으로 다시 잰다", measure_given=given,
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
    remeasure(repo)
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


def remeasure(repo: str) -> None:
    """A `measure-input` stop the owner answered with a new value: measured again at once, no build between. The
    measurement that asked may still be letting the tree go; it is waited for outside the lock."""

    if ((oneshot_spec.spec_of(repo)["oneshot"].get("stopped") or {}).get("reason") != "measure-input"):
        return
    if (thread := oneshot._workers.get(oneshot_spec.store(repo).key)) and thread.is_alive():
        thread.join(60)
    with oneshot._lock:
        o = oneshot_spec.spec_of(repo)["oneshot"]
        if o.get("working") or (o.get("stopped") or {}).get("reason") != "measure-input":
            return   # another answer meanwhile started it, or the stop was answered otherwise
        spec, plan, _ = measuring(repo)
        o = spec["oneshot"]
        if not kept_input(o, o["measures"][-1]) and all(has(spec, x) for x in plan["inputs"]):
            launch(repo, spec, plan)


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
    n: int = 0   # the measurement whose result the owner saw: a repair it sends is of that one, never a later one


@router.post("/api/oneshot/spec/{repo}/measure/start")
def start(repo: str, body: Start) -> dict:
    with oneshot._lock:   # the request checked, then the owner's waiting words, then the old plan (`oneshot_tell`)
        if body.head != (now := at_measure(repo)["oneshot"]).get("built_head"):
            raise HTTPException(409, "빌드가 그 사이 바뀌었다 — 다시 불러와 새 화면에서 시작하라")
        if now.get("fix"):   # a repair still owed: only `[재개]` sends it on, the owner's waiting words included
            raise HTTPException(409, "빌드 에이전트의 수리가 끝나지 않았다 — 다시 불러와 [재개]로 이어 간다")
        if oneshot.diverted(oneshot_spec.store(repo), "measure"):
            return oneshot_spec.view(oneshot_spec.spec_of(repo))
        spec, plan, broken = measuring(repo, plan_needed=False)
        o = spec["oneshot"]   # a measurement stopped (a wrong input, a restart) starts again from here
        last = (o.get("measures") or [None])[-1]
        if broken:   # the committed plan no longer holds: only the build agent can fix it
            then = repair(repo, {"broken": broken}, "plan")
        elif last and last["head"] == o["built_head"] and (last["status"] == "failed" and not last.get("input_error")
                                                           or kept_input(o, last)):
            # The capped failure, or an input the owner kept as it was, goes to the build agent, never measured again;
            # an input an earlier version disputed at its cap is measured again, so the owner is asked about it.
            if body.n != last["n"]:   # a screen that never showed this result: the owner answers it first
                raise HTTPException(409, "그 사이 새 실측 결과가 나왔다 — 다시 불러와 그 결과를 보고 고른다")
            then = repair(repo, {"measure_fails": 0})
        else:   # the same lock scope decides and launches: no other request's result slips in between
            if last and last["status"] == "failed" and last.get("input_error"):   # its count was the disputes'
                spec = oneshot_spec.saved(repo, oneshot={"measure_fails": 0})
            then = launch(repo, spec, plan)
    if then:   # the build agent's turn goes after the lock is let go
        then()
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


def kept_input(o: dict, last: dict) -> bool:   # `last` called an input wrong, and none changed since it
    return last["status"] == "input" and int(o.get("input_since") or 0) < last["n"]


def launch(repo: str, spec: dict, plan: dict) -> None:
    """The next measurement's worker, under `oneshot._lock`: refused while an input is missing or a result waits."""

    o = spec["oneshot"]
    if missing := [x["id"] for x in plan["inputs"] if not has(spec, x)]:
        raise HTTPException(400, f"아직 넣지 않은 입력이 있다: {', '.join(missing)}")
    if (last := (o.get("measures") or [None])[-1]) and last["head"] == o["built_head"] and waiting(last):
        raise HTTPException(409, "이 빌드의 실측이 판정을 기다린다 — 듣고 승인하거나 거절한다")
    HALTS[repo] = Halted()   # before the worker: a stop pressed as it starts is kept
    oneshot.start_worker(oneshot_spec.store(repo), "measure", measure, (repo,), {})


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
                retire(spec, f"실측한 뒤 작업트리가 바뀌었다 — {why}")
                then = repair(repo, {"moved": why}, "moved")
            elif body.approve:
                spec = oneshot_spec.saved(repo, oneshot={"measures": runs, "stage": "review", "attempts": 0,
                                                         "measure_fails": 0, "fix": None, "working": "review"})
                then = lambda: (noted_comment(repo, masked(commented(spec, runs[-1]), hide)),   # noqa: E731
                                oneshot_review.begin(repo))
            else:
                then = repair(repo, {"measures": runs})
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
    with it. A running one is set aside when it ends, by `changes`, counted once the store changed."""
    o = (spec := oneshot_spec.spec_of(repo))["oneshot"]   # a changed value is measured again, never sent to the build
    try:   # one no measurement reads (`reads`) leaves the results and a dispute as they are
        if env.upper() not in reads(spec, planned(Path(spec["worktree"]), spec, o["built_head"])):
            return {}
    except (KeyError, OSError, ValueError):   # no plan to read: taken as one it reads
        pass
    retire(spec, "비밀 값이 바뀌었다 — 이 결과는 판정하지 않고 다시 잰다", input_since=len(o.get("measures") or []))
    return {"measures": o.get("measures") or [], "input_since": o.get("input_since")}


oneshot_needs.CHANGED.append(secret_changed)
oneshot_spec.SHEETS.add("measure")
oneshot_spec.WAITS.add("measure")
