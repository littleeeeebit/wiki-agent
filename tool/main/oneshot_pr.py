"""oneshot S8: the README turn, the final gate, and the first pull request, handed to Review Loop.

`docs/plans/oneshot/5-screen-pr.md` ("S8 — the first pull request") is the design. After R3's approval
one work turn writes the README (and, for a public repository, the CI workflow); the server checks it
and runs the adapter's whole gate; then the ordinary pull request path takes over: the worker's
`pr-summary` (`pr_text`), push and `gh pr create` as the person (`specs.written`, `specs.opened`).
From `PR #n` on the spec follows the ordinary cycle, and the run keeps its summary.
"""

from __future__ import annotations

import threading
from pathlib import Path

from wiki import slots_for

from . import oneshot_build, oneshot_spec, pr_text, specs
from .query import ROOT

README_PROMPT = (ROOT / "tool/prompts/oneshot-readme.md").read_text(encoding="utf-8")
STAGE = "pr"
OS_NAMES = {"windows": "Windows", "macos": "macOS"}
WORKFLOWS = ".github/workflows/"


def readme_text(spec: dict) -> str:
    profile = spec["oneshot"]["profile"]
    now = profile["os_now"]
    both = [OS_NAMES[now]] + ([OS_NAMES["macos" if now == "windows" else "windows"]]
                              if profile.get("os_later") == "other" else [])
    public = profile.get("visibility") == "public" and profile.get("ci") == "github-actions"
    return (README_PROMPT.replace("{os}", " and ".join(both))
            .replace("{gate}", slots_for(None, Path(spec["worktree"])).get("gate_cmd", "").strip())
            .replace("{workflow}", "Also write `.github/workflows/ci.yml` running the same gate on every push and "
                                   "pull request." if public else
                     "Write no CI workflow: this repository gets none, and the server refuses one."))


def begin(repo: str) -> None:
    """After R3's approval: the README turn."""

    oneshot_build.send(repo, STAGE, restart(oneshot_spec.spec_of(repo)))


def restart(spec: dict) -> str:
    """The README turn's text; a resumed S8 starts there again, so a pull request text asked before is dropped."""

    specs.update(spec["repo"], spec["id"], writing=None)
    return readme_text(spec)


def readied(path: Path, run, spec: dict, final: str) -> str:
    """The report met, a README written, a workflow file only where the profile asks for one, and the
    adapter's whole gate green on the clean, committed HEAD."""

    o = spec["oneshot"]
    if why := oneshot_build.reported(spec, final):
        return why
    names = oneshot_build.changed(path, o.get("front_built_head") or o["tests_head"])
    if "README.md" not in names or not (path / "README.md").is_file():
        return "`README.md` 를 쓰지 않았다 — 설치, 실행, 테스트 명령을 적는다"
    flows = [n for n in names if n.startswith(WORKFLOWS)] + [
        f"{WORKFLOWS}{p.name}" for p in (path / WORKFLOWS).glob("*") if p.is_file()]
    public = o["profile"].get("visibility") == "public" and o["profile"].get("ci") == "github-actions"
    if flows and not public:
        return "이 저장소는 CI 워크플로를 두지 않는다 — " + ", ".join(sorted(set(flows))[:5]) + " 를 지운다"
    if public and not flows:
        return "공개 저장소의 CI 워크플로(`.github/workflows/ci.yml`)가 없다"
    if why := oneshot_build.pinned(path, o):
        return why
    gate = slots_for(None, path).get("gate_cmd", "").strip()
    if not gate:
        return "어댑터의 `gate_cmd` 가 비었다"
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

    out = []
    for gate in ("r1", "r2", "r3"):
        for item, d in sorted(((o.get(gate) or {}).get("decisions") or {}).items()):
            if d.get("edit"):
                out.append(f"{gate.upper()} {item}: {d['edit']}")
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
    then = specs.written(specs.channels.repo_for(repo), path, run, spec, final)
    fresh = specs.load(repo, spec["id"])
    if fresh.get("pr"):
        oneshot_spec.saved(repo, oneshot={"stage": "done", "working": None, "pr": fresh["pr"]})
    elif not fresh.get("writing"):   # neither open nor asked again: the reason is the spec's fault
        oneshot_spec.store(repo).stop(STAGE, fresh.get("fault") or "PR 을 만들지 못했다")
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
                       "edited": sorted(i for i, d in ((o.get(g) or {}).get("decisions") or {}).items() if d.get("edit"))}
                      for g in ("r1", "r2", "r3")],
            "spend": o.get("spend") or {}, "critiques": len(o.get("critiques") or []),
            "looks": len(o.get("looks") or [])}


def extra(spec: dict) -> dict:
    return {"summary": summary(spec)} if spec["oneshot"].get("stage") == "done" else {}


oneshot_spec.VIEWS.append(extra)
