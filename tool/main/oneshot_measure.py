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
import posixpath
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from common import masked, process, shell

from . import oneshot, oneshot_build, oneshot_needs, oneshot_spec, prerequisite_review, release, specs

router = APIRouter()
PLAN = ".wiki/measure.json"
IID = re.compile(r"M[1-9]\d*")
MID = re.compile(r"[a-z][a-z0-9_]{0,40}")
OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq}
CLIPS = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/ogg",
         ".flac": "audio/flac", ".mp4": "video/mp4", ".webm": "video/webm"}
MAX_CLIPS, MAX_CLIP = 12, 64 * 1024 * 1024
MAX_MINUTES, MAX_SERVE_SECONDS = 60, 600
MAX_TEXT = 2_000   # an input's typed value, and the owner's note
HEALTH = re.compile(r"http://(127\.0\.0\.1|localhost)(:\d{1,5})?(/\S*)?")
HALTS: dict[str, threading.Event] = {}   # the measurement running now, by repository: `stop` sets it


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
    inputs, envs = [], set()
    for n, x in enumerate(value.get("inputs") or []):
        where = f"inputs[{n}]"
        if not isinstance(x, dict) or not isinstance(x.get("id"), str) or not IID.fullmatch(x["id"]):
            raise ValueError(f"{where}: `id` 는 M1, M2 … 다")
        if x.get("kind") not in ("secret", "text"):
            raise ValueError(f"{where} {x['id']}: `kind` 는 secret(비밀 값) 이나 text(주소 같은 보통 글) 다")
        if not isinstance(x.get("env"), str) or not oneshot_needs.ENV.fullmatch(x["env"]) or x["env"] in envs:
            raise ValueError(f"{where} {x['id']}: `env` 는 서로 다른 대문자 환경 변수 이름이다")
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
        if not isinstance(bound, dict) or bound.get("op") not in OPS or isinstance(bound.get("value"), str) \
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


def planned(path: Path, spec: dict) -> dict:
    """The worktree's committed plan, checked."""

    file = path / PLAN
    try:
        value = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"`{PLAN}` 가 없다 — 실측 계획(serve, measure, inputs, metrics)을 커밋한다") from None
    except (OSError, ValueError) as exc:
        raise ValueError(f"`{PLAN}` 를 읽지 못했다 — {exc}") from None
    return checked(value, spec, path)


# -- What a build turn sent back must end with ------------------------------------------------------------

def unchanged(path: Path, o: dict) -> str:
    """Why a build turn sent back by the review or the measurement ended without a new commit, or empty. A HEAD
    the review refused, or the measurement failed, is never read again: the turn commits its fix, or the tests
    that pin what it answered with evidence."""

    now = oneshot_build.head(path)
    if o.get("fix") == "review" and (last := (o.get("reviews") or [None])[-1]) and last["verdict"] == "refuse" \
            and last["head"] == now:
        return (f"리뷰가 거절한 HEAD `{now[:12]}` 에서 커밋이 하나도 늘지 않았다 — 고친 코드를 커밋하거나, 고치지 않고 "
                "증거로 답한 발견마다 그 동작을 고정하는 테스트를 커밋하고 `oneshot-answers` 에 적는다")
    if o.get("fix") == "measure" and (last := (o.get("measures") or [None])[-1]) and last["head"] == now:
        return f"실측이 실패한 HEAD `{now[:12]}` 에서 커밋이 하나도 늘지 않았다 — 원인을 고쳐 커밋한다"
    return ""


ANSWER = re.compile(r"^```oneshot-answers[ \t]*\r?\n(.*?)^```[ \t]*$", re.M | re.S)


def answered(path: Path, spec: dict, final: str) -> list[dict]:
    """The turn's `oneshot-answers`: each finding of the standing refusal it answered with evidence instead of a
    change, the requirement a change would break, and the test committed since that refusal which pins the
    behaviour it defends. `ValueError` says what is wrong; a turn sent back by nothing answers nothing."""

    from . import oneshot_review   # it imports this module

    o = spec["oneshot"]
    found = ANSWER.findall(final)
    if not found:
        return []
    last = (o.get("reviews") or [None])[-1]
    if len(found) > 1 or o.get("fix") != "review" or not last or last["verdict"] != "refuse":
        raise ValueError("`oneshot-answers` 는 리뷰가 거절해 돌아온 빌드 턴에 하나만 적는다")
    try:
        value = json.loads(found[0])
    except ValueError as exc:
        raise ValueError(f"`oneshot-answers` 가 JSON 이 아니다 — {exc}") from None
    areas = {oneshot_review.area_of(f) for f in oneshot_review.blocking(last)}
    anchors, since = oneshot_review.anchors(spec), set(oneshot_build.changed(path, last["head"]))
    out = []
    for n, a in enumerate(value if isinstance(value, list) else [None]):
        where = f"`oneshot-answers`[{n}]"
        if not isinstance(a, dict) or not all(isinstance(a.get(k), str) and a[k].strip()
                                               for k in ("area", "location", "evidence", "test")):
            raise ValueError(f"{where}: area, location, evidence, breaks, test 를 적는다")
        if oneshot_review.area_of(a) not in areas:
            raise ValueError(f"{where}: `{a['area']}` 는 이번 거절의 영역이 아니다 — 발견의 `area` 를 그대로 적는다")
        if not isinstance(a.get("breaks"), list) or not a["breaks"] or not all(isinstance(b, str) and b in anchors
                                                                              for b in a["breaks"]):
            raise ValueError(f"{where}: `breaks` 는 고치면 깨지는 설계 항목·요구 번호의 목록이다")
        file, _, name = a["test"].partition("::")
        target = (path / file).resolve()
        if not name or not target.is_relative_to(path.resolve()) or not target.is_file() \
                or posixpath.normpath(file.replace("\\", "/")) not in since \
                or name.split("[")[0] not in target.read_text(encoding="utf-8", errors="replace"):
            raise ValueError(f"{where}: `test` 는 이번 턴에 커밋한 테스트 파일과 그 안의 테스트 이름(`경로::이름`)이다 — {a['test']}")
        out.append({"round": len(o["reviews"]), "head": last["head"], "area": " ".join(a["area"].split()),
                    **{k: a[k].strip() for k in ("location", "evidence", "test")}, "breaks": a["breaks"]})
    return out


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
    hide = {**oneshot_needs.hidden(spec, env), **{v: f"[{k}]" for k, v in secrets.items()}}
    return env, hide


# -- Starting the backend ---------------------------------------------------------------------------------

@contextmanager
def served(command: str, path: Path, env: dict, health: str, seconds: int, halt: threading.Event, log: Path):
    """The backend started in a job of its own and answering `health` with 200, for the block; `ValueError` with
    the end of its log when it exits, or does not answer in `seconds`. Killed with everything it started after."""

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
                    raise ValueError(f"`serve.command` 가 떠 있지 않고 끝났다 (종료 코드 {proc.returncode}) — {tail(log)}")
                try:
                    with urllib.request.urlopen(health, timeout=3) as answer:
                        if answer.status == 200:
                            break
                except (urllib.error.URLError, OSError, ValueError):
                    pass
                if time.monotonic() > deadline:
                    raise ValueError(f"백엔드가 {seconds}초 안에 `{health}` 에 200 으로 답하지 않았다 — {tail(log)}")
                time.sleep(1)
            yield
        finally:
            process.terminated(job) if job else proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def tail(log: Path, size: int = 1500) -> str:
    try:
        return log.read_text(encoding="utf-8", errors="replace")[-size:].strip() or "(출력 없음)"
    except OSError:
        return "(출력 없음)"


def serve_check(path: Path, run, spec: dict) -> str:
    """At the build's gate: the plan holds and the backend starts and answers its health address, or why not."""

    try:
        plan = planned(path, spec)
        env, hide = env_of(spec, plan)
        log = Path(tempfile.mkdtemp(prefix="oneshot-serve-")) / "serve.log"
        try:
            with served(oneshot_build.run_as(plan["serve"]["command"], spec["oneshot"], path), path, env,
                        plan["serve"]["health"], plan["serve"]["seconds"], run.halt, log):
                pass
        finally:
            shutil.rmtree(log.parent, ignore_errors=True)
    except ValueError as exc:
        return masked(str(exc), hide) if "hide" in locals() else str(exc)
    if specs.dirty(path):   # the HEAD the build passed is the one measured, so the tree stays as committed
        return "백엔드를 띄우자 작업트리가 바뀌었다 — 백엔드가 쓰는 파일(데이터베이스, 로그)은 .gitignore 에 둔다"
    specs.note(run, "백엔드가 화면 없이 떠서 상태 주소에 답했다")
    return ""


# -- Pull request #1 --------------------------------------------------------------------------------------

def body(spec: dict, head: str) -> str:
    o = spec["oneshot"]
    lines = ["원샷이 만든 백엔드의 첫 PR 이다. 아직 초안이다.", "",
             f"- 빌드 커밋: `{head}`",
             "- 이 PR 에서 진짜 환경 실측과 백엔드 리뷰를 한다. 주인이 승인한 실측 기록은 이 PR 의 댓글로 붙는다.",
             "- 화면을 만든 뒤 마지막 단계에서 설명을 다시 쓰고 초안을 푼다.", "", "## 요구", ""]
    lines += [f"- {r['id']}: {r['text']}" for r in spec.get("requirements") or []]
    if o.get("measures"):
        lines += ["", f"실측 {len(o['measures'])}회 · 리뷰 {len(o.get('reviews') or [])}회"]
    return "\n".join(lines)


def published(repo: str) -> dict:
    """The built HEAD pushed, and the run's pull request opened as a draft, or found and its body brought up to
    date; `ValueError` says why not. The final stage reuses it (`specs.pull_request` finds it) and makes it ready."""

    spec = oneshot_spec.spec_of(repo)
    path, head, branch = Path(spec["worktree"]), spec["oneshot"]["built_head"], specs.branch_of(spec)
    pushed = prerequisite_review.push(path, spec, ["git", "push", "-u", "origin", f"{head}:refs/heads/{branch}"])
    if pushed.returncode:
        raise ValueError(f"push 실패 — {specs.said(pushed)}")
    base = specs.base_of(spec, path)
    if base.returncode or not base.stdout.strip():
        raise ValueError(f"기본 브랜치를 모른다 — {specs.said(base)}")
    base = base.stdout.strip()
    title = f"[초안] 백엔드 — {' '.join(spec['goal'].split())}"
    title = title if len(title) <= 200 else title[:199] + "…"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
        fh.write(body(spec, head))
    try:
        found = specs.existing_pr(path, branch, base)   # `RuntimeError` when GitHub cannot be read
        if found:
            done = specs.sh(["gh", "pr", "edit", str(found[0]), "--body-file", fh.name], path, 120)
        else:
            done = specs.sh(["gh", "pr", "create", "--draft", "--base", base, "--head", branch, "--title", title,
                             "--body-file", fh.name], path, 120)
            if m := re.search(r"/pull/(\d+)", done.stdout or ""):
                found = (int(m[1]), done.stdout.strip().splitlines()[-1])
            else:
                found = specs.existing_pr(path, branch, base)   # a create whose reply was lost
        if not found:
            raise ValueError(f"PR 을 만들지 못했다 — {specs.said(done)}")
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"PR 을 만들거나 고치지 못했다 — {exc}") from None
    finally:
        os.unlink(fh.name)
    pr = {"number": found[0], "url": found[1], "branch": branch, "base": base, "head": head}
    oneshot_spec.saved(repo, oneshot={"pr1": pr})
    return pr


def begin(repo: str) -> None:
    """After the build passed: the HEAD goes up to pull request #1, then the run waits for the owner's start."""

    try:
        oneshot.start_worker(oneshot_spec.store(repo), "publish", publish, (repo,), {})
    except HTTPException:
        oneshot_spec.store(repo).stop("restart", "서버가 꺼지는 중이라 PR #1 을 올리지 못했다")


def publish(repo: str) -> None:
    try:
        pr = published(repo)
    except ValueError as exc:
        oneshot_spec.store(repo).stop("measure", f"PR #1 을 올리지 못했다 — {exc} — [실측 시작]을 누르면 다시 올린다")
        return
    oneshot_spec.saved(repo, oneshot={"working": None})
    from . import work   # the owner is told where the run waits, as a pull request's readiness is
    work.notice(f"PR #{pr['number']} 초안 · 실측을 기다린다", f"{repo} · 입력을 넣고 [실측 시작]을 누른다")


# -- One measurement ---------------------------------------------------------------------------------------

def report_of(out: Path, plan: dict, hide: dict[str, str]) -> dict:
    """`report.json` the script wrote, read against the plan: each metric's value and whether it passed, the input
    the script says was wrong, and its notes, none of them showing a secret."""

    try:
        value = json.loads((out / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"error": f"실측 명령이 `{{out}}/report.json` 을 쓰지 않았거나 JSON 이 아니다 — {exc}"}
    if not isinstance(value, dict):
        return {"error": "`report.json` 은 metrics 를 담은 객체다"}
    if isinstance(bad := value.get("input_error"), dict) and isinstance(bad.get("why"), str):
        return {"input_error": {"input": str(bad.get("input") or ""), "why": masked(bad["why"], hide)[:MAX_TEXT]}}
    got = value.get("metrics") if isinstance(value.get("metrics"), dict) else {}
    rows = []
    for m in plan["metrics"]:
        v = got.get(m["id"])
        number = isinstance(v, (int, float)) and (not isinstance(v, bool) or m["pass"]["op"] == "==")
        ok = bool(number and OPS[m["pass"]["op"]](v, m["pass"]["value"]))
        rows.append({**m, "value": v if number else None, "ok": ok})
    return {"metrics": rows, "notes": masked(str(value.get("notes") or ""), hide)[:MAX_TEXT]}


def clips_of(out: Path, hide: dict[str, str]) -> list[str]:
    folder = out / "clips"
    if not folder.is_dir():
        return []
    found = sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in CLIPS
                   and p.stat().st_size <= MAX_CLIP and masked(p.name, hide) == p.name)
    return found[:MAX_CLIPS]


def measure(repo: str) -> None:
    from . import oneshot_review   # it imports this module

    halt = HALTS.setdefault(repo, threading.Event())
    halt.clear()

    def body(store, spec, tree):
        o = spec["oneshot"]
        try:
            plan = planned(tree, spec)
            if missing := [x["id"] for x in plan["inputs"] if not has(spec, x)]:
                raise ValueError(f"아직 넣지 않은 입력이 있다: {', '.join(missing)}")
            if (o.get("pr1") or {}).get("head") != o["built_head"]:
                published(repo)   # the HEAD measured is the one pull request #1 shows
        except ValueError as exc:
            store.stop("measure", str(exc))
            return None
        n = len(o.get("measures") or []) + 1
        out = store.folder / "measure" / f"m{n}-{o['built_head'][:12]}"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        env, hide = env_of(spec, plan)
        record = {"n": n, "head": o["built_head"], "ts": time.time(), "folder": out.name}
        try:
            with served(oneshot_build.run_as(plan["serve"]["command"], o, tree), tree, env, plan["serve"]["health"],
                        plan["serve"]["seconds"], halt, out / "serve.log"):
                limit = plan["measure"]["minutes"] * 60 + 300
                code, text, cut = specs.gate(oneshot_build.run_as(plan["measure"]["command"].replace(
                    "{out}", oneshot_build.literal(out.as_posix())), o, tree), tree, halt, env=env, timeout=limit,
                    idle=limit, hide=hide)
        except ValueError as exc:
            code, text, cut = None, "", masked(str(exc), hide)
        record["tail"] = (text or "").strip()[-3000:]
        if halt.is_set():
            store.stop("measure", "사람이 실측을 멈췄다 — 다시 하려면 [실측 시작]을 누른다")
            return None
        found = report_of(out, plan, hide) if not cut else {"error": cut}
        record |= found | {"clips": clips_of(out, hide), "code": code}
        if "input_error" in found:
            record["status"] = "input"
        elif found.get("error") or code or not all(m["ok"] for m in found["metrics"]) or plan["clips"] and not record["clips"]:
            record["status"] = "failed"
            if not found.get("error") and plan["clips"] and not record["clips"]:
                record["error"] = "변환한 소리를 들어 볼 클립이 `{out}/clips` 에 없다"
        else:
            record["status"] = "owner"
        return lambda: decided(repo, store, record)

    oneshot_review.holding(repo, "실측", body)


def decided(repo: str, store, record: dict):
    """A measurement saved. One the owner must judge waits; a wrong input stops for the owner; a failure goes to
    the build agent, up to the review's round cap."""

    from . import oneshot_review   # it imports this module

    o = store.get()
    runs = [*(o.get("measures") or []), record]
    if record["status"] == "owner":
        oneshot_spec.saved(repo, oneshot={"measures": runs, "working": None})
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

def measuring(repo: str) -> tuple[dict, dict]:
    """`(spec, plan)` while the run is at the measurement with no worker running."""

    spec = oneshot_spec.spec_of(repo)
    o = spec["oneshot"]
    oneshot_spec.supported(o)
    if o.get("stage") != "measure" or o.get("working"):
        raise HTTPException(409, f"지금 할 수 없다 — {o.get('stage')}{' 진행 중' if o.get('working') else ''}")
    try:
        return spec, planned(Path(spec["worktree"]), spec)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


class Value(BaseModel):
    value: str = ""


@router.post("/api/oneshot/spec/{repo}/measure/inputs/{iid}")
def give(repo: str, iid: str, body: Value) -> dict:
    """One input the owner got by following its steps: a secret to Credential Manager, other text to the spec."""

    with oneshot._lock:
        spec, plan = measuring(repo)
        x = next((x for x in plan["inputs"] if x["id"] == iid), None)
        value = body.value.strip()
        if x is None:
            raise HTTPException(404, f"실측 계획에 없는 입력이다: {iid}")
        if not value or len(value) > MAX_TEXT:
            raise HTTPException(400, f"값이 비었거나 {MAX_TEXT}자를 넘는다")
        if x["kind"] == "secret":
            oneshot_needs.stored(repo, x["env"], value)
        else:
            oneshot_spec.saved(repo, oneshot={"measure_given": {**kept(spec), x["env"]: {"value": value, "what": x["what"]}}})
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


@router.delete("/api/oneshot/spec/{repo}/measure/inputs/{iid}")
def take_back(repo: str, iid: str) -> dict:
    with oneshot._lock:
        spec, plan = measuring(repo)
        if (x := next((x for x in plan["inputs"] if x["id"] == iid), None)) is None:
            raise HTTPException(404, f"실측 계획에 없는 입력이다: {iid}")
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
        spec, plan = measuring(repo)
        o = spec["oneshot"]   # a measurement stopped (a wrong input, a restart) starts again from here
        if body.head != o.get("built_head"):
            raise HTTPException(409, "빌드가 그 사이 바뀌었다 — 다시 불러와 새 화면에서 시작하라")
        if missing := [x["id"] for x in plan["inputs"] if not has(spec, x)]:
            raise HTTPException(400, f"아직 넣지 않은 입력이 있다: {', '.join(missing)}")
        last = (o.get("measures") or [None])[-1]
        if last and last["head"] == o["built_head"] and last["status"] == "owner" and not last.get("owner"):
            raise HTTPException(409, "이 빌드의 실측이 판정을 기다린다 — 듣고 승인하거나 거절한다")
        oneshot.start_worker(oneshot_spec.store(repo), "measure", measure, (repo,), {})
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


@router.post("/api/oneshot/spec/{repo}/measure/stop")
def stop(repo: str) -> dict:
    if halt := HALTS.get(repo):
        halt.set()
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


class Decision(BaseModel):
    head: str = ""
    approve: bool = False
    note: str = ""


@router.post("/api/oneshot/spec/{repo}/measure/decide")
def decide(repo: str, body: Decision) -> dict:
    """The owner's verdict on the measurement they listened to: approval goes to the review, which reads it; a
    refusal, with what they heard, goes to the build agent."""

    from . import oneshot_review   # it imports this module

    note = body.note.strip()
    with oneshot._lock:
        spec, _ = measuring(repo)
        o = spec["oneshot"]
        runs = list(o.get("measures") or [])
        last = runs[-1] if runs else None
        if not last or last["head"] != o.get("built_head") or body.head != last["head"] or last["status"] != "owner" \
                or last.get("owner"):
            raise HTTPException(409, "판정할 실측이 없다 — 다시 불러온다")
        if not body.approve and not note:
            raise HTTPException(400, "거절하려면 들은 것과 무엇이 틀렸는지 적는다")
        if len(note) > MAX_TEXT:
            raise HTTPException(400, f"글은 {MAX_TEXT}자까지다")
        runs[-1] = {**last, "owner": {"approve": body.approve, "note": note, "ts": time.time()}}
        if body.approve:
            spec = oneshot_spec.saved(repo, oneshot={"measures": runs, "stage": "review", "attempts": 0,
                                                     "measure_fails": 0, "fix": None, "working": "review"})
            then = lambda: (commented(spec, runs[-1]), oneshot_review.begin(repo))   # noqa: E731
        else:
            spec = oneshot_spec.saved(repo, oneshot={"measures": runs, "stage": "build", "fix": "measure",
                                                     "attempts": 0, "working": "build"})
            text = fixing(spec)
            then = lambda: oneshot_build.send(repo, "build", text)   # noqa: E731
    then()
    return oneshot_spec.view(oneshot_spec.spec_of(repo))


def commented(spec: dict, record: dict) -> None:
    """The approved measurement as a comment on pull request #1, so the review's record is the PR's; best effort."""

    pr = spec["oneshot"].get("pr1")
    if not pr:
        return
    rows = [f"| {m['what']} | {m['value']}{m['unit']} | {m['pass']['op']} {m['pass']['value']} | {'통과' if m['ok'] else '실패'} |"
            for m in record.get("metrics") or []]
    text = "\n".join([f"## 실측 {record['n']} · `{record['head'][:12]}` · 주인 승인", "",
                      "| 측정 | 값 | 기준 | 결과 |", "| --- | --- | --- | --- |", *rows, "",
                      f"주인 메모: {record['owner']['note'] or '(없음)'}", "",
                      f"스크립트 메모: {record.get('notes') or '(없음)'}"])
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
        fh.write(text)
    try:
        specs.sh(["gh", "pr", "comment", str(pr["number"]), "--body-file", fh.name], Path(spec["worktree"]), 60)
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        os.unlink(fh.name)


@router.get("/api/oneshot/spec/{repo}/measure/{n}/clips/{name}")
def clip(repo: str, n: int, name: str) -> FileResponse:
    spec = oneshot_spec.spec_of(repo)
    record = next((m for m in spec["oneshot"].get("measures") or [] if m["n"] == n), None)
    if not record or name not in (record.get("clips") or []):
        raise HTTPException(404, "그런 클립이 없다")
    file = oneshot_spec.store(repo).folder / "measure" / record["folder"] / "clips" / name
    if not file.is_file():
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
            plan = planned(Path(spec["worktree"]), spec)
        except ValueError as exc:
            error = str(exc)
    inputs = [{**x, "given": has(spec, x)} for x in (plan or {}).get("inputs") or []]
    runs = [{k: v for k, v in m.items() if k != "folder"} for m in o.get("measures") or []]
    return {"measure": {"plan": plan and {**plan, "inputs": inputs}, "error": error, "runs": runs,
                        "pr": o.get("pr1"), "head": o["built_head"]}}


oneshot_spec.VIEWS.append(shown)
oneshot_spec.SHEETS.add("measure")
oneshot_spec.WAITS.add("measure")
