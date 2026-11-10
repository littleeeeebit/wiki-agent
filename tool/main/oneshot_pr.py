"""oneshot S8: the README turn, the final gate, and the first pull request, handed to Review Loop.

`docs/plans/oneshot/5-screen-pr.md` ("S8 — the first pull request") is the design. After R3's approval
one work turn writes the README (and, for a public repository, the CI workflow); the server checks it
and runs the adapter's whole gate; then the ordinary pull request path takes over: the worker's
`pr-summary` (`pr_text`), push and `gh pr create` as the person (`specs.written`, `specs.opened`).
From `PR #n` on the spec follows the ordinary cycle, and the run keeps its summary.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import yaml

from wiki import slots_for

from . import oneshot_build, oneshot_spec, pr_text, specs
from .query import ROOT

README_PROMPT = (ROOT / "tool/prompts/oneshot-readme.md").read_text(encoding="utf-8")
STAGE = "pr"
OS_NAMES = {"windows": "Windows", "macos": "macOS"}
WORKFLOWS = ".github/workflows/"
CI = f"{WORKFLOWS}ci.yml"
# The workflow's events. Written unquoted: `test_prerequisite_review` reads a quoted push as a git push.
TRIGGERS = set("push pull_request".split())


def readme_text(spec: dict) -> str:
    profile = spec["oneshot"]["profile"]
    now = profile["os_now"]
    both = [OS_NAMES[now]] + ([OS_NAMES["macos" if now == "windows" else "windows"]]
                              if profile.get("os_later") == "other" else [])
    public = profile.get("visibility") == "public" and profile.get("ci") == "github-actions"
    return (README_PROMPT.replace("{os}", " and ".join(both))
            .replace("{gate}", slots_for(None, Path(spec["worktree"])).get("gate_cmd", "").strip())
            .replace("{workflow}", "Also write `.github/workflows/ci.yml`: `on: [push, pull_request]` and a job "
                                   "whose step `run`s the gate command above, as written." if public else
                     "Write no CI workflow: this repository gets none, and the server refuses one."))


def begin(repo: str) -> None:
    """After R3's approval: the README turn."""

    oneshot_build.send(repo, STAGE, restart(oneshot_spec.spec_of(repo)))


def restart(spec: dict) -> str:
    """The README turn's text; a resumed S8 starts there again, so a pull request text asked before is dropped."""

    specs.update(spec["repo"], spec["id"], writing=None)
    return readme_text(spec)


def workflows(path: Path) -> dict[str, str]:
    """HEAD's files under `.github/workflows/`, each with its tree mode (`100644`, `120000` for a link)."""

    done = specs.sh(["git", "-c", "core.quotepath=off", "ls-tree", "-r", "-z", "HEAD", "--", WORKFLOWS], path)
    if done.returncode:
        raise ValueError(f"git ls-tree 실패 — {(done.stderr or '').strip()[-200:]}")
    return {name: meta.split()[0] for meta, _, name in (e.partition("\t") for e in done.stdout.split("\0") if e)}


def ci_file(path: Path, mode: str | None, gate: str) -> bool:
    """`ci.yml` committed as a plain file (a link points anywhere) whose workflow has a trigger and a
    job step that runs the gate: a comment or an empty job is no CI."""

    if mode not in ("100644", "100755"):
        return False
    shown = specs.sh(["git", "show", f"HEAD:{CI}"], path)
    try:
        flow = yaml.safe_load(shown.stdout) if not shown.returncode else None
    except yaml.YAMLError:
        return False
    if not isinstance(flow, dict) or not isinstance(flow.get("jobs"), dict):
        return False
    on = flow.get("on", flow.get(True))   # YAML 1.1 reads the bare key `on` as true
    events = {on} if isinstance(on, str) else set(on) if isinstance(on, (list, dict)) else set()
    if not TRIGGERS <= events:   # on every push and pull request, not by hand only
        return False
    steps = [s for job in flow["jobs"].values() if isinstance(job, dict) for s in job.get("steps") or []]
    # The gate's lines run as a block of `run`, each exactly: named in a comment or an `echo` it never runs,
    # and an argument added (`--collect-only`, a narrower selection) runs something else. Arguments live in
    # `gate_cmd`, which may span lines (the front build extends the backend's).
    want = [line.strip() for line in gate.splitlines() if line.strip()]
    for s in steps:
        have = [line.strip() for line in str(s.get("run") or "").splitlines() if line.strip()] if isinstance(s, dict) else []
        if want and any(have[i:i + len(want)] == want for i in range(len(have) - len(want) + 1)):
            return True
    return False


def readied(path: Path, run, spec: dict, final: str) -> str:
    """The report met, a README written, a workflow file only where the profile asks for one, and the
    adapter's whole gate green on the clean, committed HEAD."""

    o = spec["oneshot"]
    if why := oneshot_build.reported(spec, final):
        return why
    names = oneshot_build.changed(path, o.get("front_built_head") or o["tests_head"])
    if "README.md" not in names or not (path / "README.md").is_file():
        return "`README.md` 를 쓰지 않았다 — 설치, 실행, 테스트 명령을 적는다"
    gate = slots_for(None, path).get("gate_cmd", "").strip()
    if not gate:
        return "어댑터의 `gate_cmd` 가 비었다"
    # What the published HEAD holds, not what changed: a deleted workflow or a stray note is no CI.
    held = workflows(path)
    flows = sorted({*held, *(n for n in names if n.startswith(WORKFLOWS) and os.path.lexists(path / n))})
    public = o["profile"].get("visibility") == "public" and o["profile"].get("ci") == "github-actions"
    if flows and not public:
        return "이 저장소는 CI 워크플로를 두지 않는다 — " + ", ".join(flows[:5]) + " 를 지운다"
    if public and not ci_file(path, held.get(CI), gate):
        return (f"공개 저장소의 CI 워크플로(`{CI}`)가 없다 — 커밋된 보통 파일로, `on:` 트리거와 "
                f"게이트(`{gate}`)를 `run` 하는 job step 을 둔다")
    if why := oneshot_build.pinned(path, o):
        return why
    verdict = specs.judge(path, [gate], run.halt, lambda text: specs.note(run, text))
    if not verdict["ok"]:
        return f"최종 게이트 실패 — {verdict['reason']}\n{verdict['tail']}"
    report = next(b["value"] for b in specs.blocks(final)[1] if b["name"] == "done-report")
    tail = (verdict.get("tail") or "").strip()
    # The pull request's body reads these as every spec's: the worker's report and the server's gate.
    specs.update(spec["repo"], spec["id"], report=report, gate={"cmd": gate, "head": verdict["head"], "tail": tail,
                                                                "selection": "full"})
    oneshot_spec.saved(spec["repo"], oneshot={"final_head": verdict["head"], "attempts": 0})
    specs.note(run, f"최종 게이트 통과 · {verdict['head'][:12]}")
    return ""


def decisions(o: dict) -> list[str]:
    """What the owner decided at the three reviews, for the pull request's `변경 이유`."""

    # Read off each submission's record: an applied edit's comment is gone from the sheet's decisions.
    out = [f"{gate.upper()} {item} (round {entry['round']}): {comment}"
           for gate in ("r1", "r2", "r3") for entry in (o.get("reading") or {}).get(gate) or []
           for item, comment in (entry.get("edits") or {}).items()]
    if palette := (o.get("r2") or {}).get("palette"):
        out.append(f"R2 palette: {palette}")
    return out


def asked(repo: str) -> None:
    """The gate passed: one turn asks the worker for the pull request's text, as every spec's does."""

    spec = oneshot_spec.spec_of(repo)
    path, head = Path(spec["worktree"]), oneshot_spec.spec_of(repo)["oneshot"]["final_head"]
    spec = specs.update(repo, spec["id"], writing={"head": head, "asked": 1})
    said = decisions(spec["oneshot"])
    text = pr_text.writing(spec, specs.channels.repo_for(repo), path, threading.Event())
    if said:
        text += ("\n\nThe owner's decisions at the three reviews, which belong in `why`:\n"
                 + "\n".join(f"- {d}" for d in said))
    oneshot_build.send(repo, STAGE, text)


def handed(path: Path, run, spec: dict, final: str):
    """`oneshot_build.checked`'s hand-over while a pull request's text is asked: `specs.written` checks it
    and opens the pull request; the run then keeps its summary and the spec its `PR #n`."""

    repo = spec["repo"]
    run.checked = True
    # The text asked again is the run's own turn: charged, and stopping the stage if it fails.
    ask = lambda text: oneshot_build.send(repo, STAGE, text) or True   # noqa: E731 — sent stops itself on a refusal
    try:
        then = specs.written(specs.channels.repo_for(repo), path, run, spec, final, ask)
    except Exception as exc:   # a push that timed out: stopped with its reason, resumed from the README turn
        oneshot_spec.store(repo).stop(STAGE, f"PR 을 만들지 못했다 — {type(exc).__name__}: {exc}")
        raise
    fresh = specs.load(repo, spec["id"])
    if fresh.get("pr"):
        oneshot_spec.saved(repo, oneshot={"stage": "done", "working": None, "pr": fresh["pr"]})
    elif not fresh.get("writing"):   # neither open nor asked again: the reason is the spec's fault
        oneshot_spec.store(repo).stop(STAGE, fresh.get("fault") or "PR 을 만들지 못했다")
    elif then:   # `working` stays until the turn goes: a restart in between is recovered as any running stage
        return oneshot_build.unless_stopped(run, repo, then)
    return then


oneshot_build.STAGES[STAGE] = (readied, STAGE, restart, asked)
oneshot_build.HANDOVERS[STAGE] = lambda spec: bool(spec.get("writing")), handed
oneshot_spec.TURNS.add(STAGE)


def summary(spec: dict) -> dict:
    """The finished run's one summary: the sentence, each gate's rounds and reading, the spend, the
    critique rounds and the pull request."""

    o = spec["oneshot"]
    reading = o.get("reading") or {}
    return {"sentence": o.get("sentence", ""), "pr": o.get("pr"),
            "gates": [{"gate": g, "rounds": (o.get(g) or {}).get("round", 0), "reading": reading.get(g) or [],
                       "edited": sorted({i for e in reading.get(g) or [] for i in e.get("edited") or []})}
                      for g in ("r1", "r2", "r3")],
            "spend": o.get("spend") or {}, "critiques": len(o.get("critiques") or []),
            "looks": len(o.get("looks") or [])}


def extra(spec: dict) -> dict:
    return {"summary": summary(spec)} if spec["oneshot"].get("stage") == "done" else {}


oneshot_spec.VIEWS.append(extra)
