"""oneshot step 12: the measurement plan the build agent commits, `.wiki/measure.json` (`PLAN`), checked as the
server runs it (`checked`) and read at the built HEAD (`planned`). `oneshot_measure` runs it.
"""

from __future__ import annotations

import operator
import re
from pathlib import Path

from . import oneshot_build, oneshot_measure_app as product, oneshot_needs, oneshot_profile
from .oneshot_kept import committed, launched, strict

PLAN = ".wiki/measure.json"
IID = re.compile(r"M[1-9]\d*")
MID = re.compile(r"[a-z][a-z0-9_]{0,40}")
OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq}
MAX_MINUTES, MAX_SERVE_SECONDS = 60, 600
HEALTH = re.compile(r"http://(127\.0\.0\.1|localhost)(:\d{1,5})?(/\S*)?")


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
