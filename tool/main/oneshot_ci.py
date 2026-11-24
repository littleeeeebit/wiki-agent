"""A PC app's CI steps before the gate (plan 9e, "S8"): what the README turn is told to write into the job, and
how S8 matches a workflow's steps against them (`oneshot_pr.ci_file`)."""

from __future__ import annotations

from . import oneshot_build
from .oneshot_profile import kinds_of


def ci_steps(o: dict) -> list[dict]:
    """Each declared tool's confirmed `setup` as written (none for a `preinstalled` one), then a check that every
    declared tool is on `PATH`; none without a PC app."""

    if (declared := oneshot_build.tools(o)) is None:
        return []
    names = ", ".join(f"'{t['name']}'" for t in declared)
    check = (f"foreach ($tool in @({names})) {{ if (-not (Get-Command $tool -CommandType Application,ExternalScript "
             '-ErrorAction SilentlyContinue)) { throw "$tool is not on PATH" } }')   # a file, as the gate finds it
    return [dict(t["setup"]) for t in declared if isinstance(t.get("setup"), dict)] + [
        {"name": "Declared tools on PATH", "shell": "pwsh", "run": check}]


# An Android app's JDK and SDK setup the prompt names (`readme_text`), any version, required beside a PC app's steps.
ANDROID_STEPS = [{"uses": "actions/setup-java"}, {"uses": "android-actions/setup-android"}]


def required(o: dict) -> list[dict]:
    """The steps S8 requires before the gate's in a PC app's CI job, the `PATH` check last; none without one."""

    return (ANDROID_STEPS if "android" in kinds_of(o) else []) + steps if (steps := ci_steps(o)) else []


def matches(step: dict, want: dict) -> bool:
    """A step that is `want`, name aside: the same action and inputs or `run` lines and shell, no `if` to skip it nor
    failure to go past, nor an `env` of its own (it would see another `PATH` than the gate's). A `uses` without `@`
    is that action at any version, with any inputs and environment."""

    if step.get("if") is not None or any(step.get(k) not in (None, False) for k in ("continue-on-error", "background")):
        return False   # skipped, gone past, or still running when the gate starts
    if not isinstance(step.get("with", {}), dict):   # inputs Actions cannot read
        return False
    if "uses" in want and "@" not in want["uses"]:
        return str(step.get("uses") or "").startswith(want["uses"] + "@")
    if step.get("env") is not None:
        return False
    if "run" in want:
        lines = [[x.strip() for x in str(s.get("run") or "").splitlines() if x.strip()] for s in (step, want)]
        return lines[0] == lines[1] and step.get("shell") == want.get("shell")
    return step.get("uses") == want["uses"] and {   # inputs read as text
        k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in (step.get("with") or {}).items()
    } == dict(want.get("with") or {})
