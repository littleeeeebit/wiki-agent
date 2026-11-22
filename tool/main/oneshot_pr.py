"""oneshot S8: the README turn, the final gate, and the first pull request, handed to Review Loop.

`docs/plans/oneshot/5-screen-pr.md` ("S8 — the first pull request") is the design. After R3's approval
one work turn writes the README (and, for a public repository, the CI workflow); the server checks it
and runs the adapter's whole gate; then the ordinary pull request path takes over: the worker's
`pr-summary` (`pr_text`), push and `gh pr create` as the person (`specs.written`, `specs.opened`). An
Android app's turn also writes the `[release]` table, and one unsigned release build checks it
(`release_checked`, `docs/plans/release/0-overview.md` step 7).
From `PR #n` on the spec follows the ordinary cycle, and the run keeps its summary.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import tomllib
from pathlib import Path

import yaml

import android_owned
from common import masked
from wiki import slots_for

from . import github_release, oneshot_build, oneshot_device, oneshot_needs, oneshot_spec, pr_text, release, specs
from .oneshot_profile import kinds_of
from .query import ROOT

README_PROMPT = (ROOT / "tool/prompts/oneshot-readme.md").read_text(encoding="utf-8")
ADAPTER = ".wiki/adapter.toml"
RELEASE_PROMPT = (ROOT / "tool/prompts/oneshot-readme-release.md").read_text(encoding="utf-8")
STAGE = "pr"
TRIES = 3   # the debug APK's create and upload, each, as `gh release` retried them
OS_NAMES = {"windows": "Windows", "macos": "macOS"}
WORKFLOWS = ".github/workflows/"
CI = f"{WORKFLOWS}ci.yml"
# The workflow's events. Written unquoted: `test_prerequisite_review` reads a quoted push as a git push.
TRIGGERS = set("push pull_request".split())
KIND_PROMPT_NAMES = {"webapp": "a web app", "android": "an Android app", "desktop": "a Windows app"}


def readme_text(spec: dict) -> str:
    profile = spec["oneshot"]["profile"]
    now = profile["os_now"]
    both = [OS_NAMES[now]] + ([OS_NAMES["macos" if now == "windows" else "windows"]]
                              if profile.get("os_later") == "other" else [])
    public = profile.get("visibility") == "public" and profile.get("ci") == "github-actions"
    kinds = kinds_of(spec["oneshot"])
    android = "android" in kinds   # alone, or among several kinds, whose gate builds it on the core
    job = ("a job on `ubuntu-latest` that sets up a JDK (`actions/setup-java`) and the Android SDK "
           "(`android-actions/setup-android`), then a step that `run`s the gate command above, as written"
           if android else "a job whose step `run`s the gate command above, as written")
    several = f"""
This repository holds {len(kinds)} kinds of the application, sharing one core:
{', '.join(KIND_PROMPT_NAMES[k] for k in kinds)}. Give each its own section, in that
order, with its own Setup and Run; Test is one, since the gate runs the core's
tests and every kind's build. For the web app, Run is the command that starts
it and where it is then served.
""" if len(kinds) > 1 else ""
    return (README_PROMPT.replace("{os}", " and ".join(both))
            .replace("{gate}", slots_for(None, Path(spec["worktree"])).get("gate_cmd", "").strip())
            .replace("{android}", several + (f"""
{"In the Android app's section," if several else 'This is an Android app.'} Setup names the JDK and the Android SDK the build
needs. Run is instead the command that builds the debug APK,
`{spec['oneshot']['device']['apk']}`, and the one that installs it on a phone
connected with USB debugging on: adb's install command with that APK's path.
""" + RELEASE_PROMPT.replace("{package}", spec["oneshot"]["device"]["package"]) if android else ""))
            .replace("{workflow}", f"Also write `.github/workflows/ci.yml`: `on: [push, pull_request]` and {job}."
                     if public else "Write no CI workflow: this repository gets none, and the server refuses one."))


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
    # Before its gate is read, let alone run: the turn may have added `[release]` and nothing else. The gate is
    # read from that very commit's adapter, and the gate runs only on that commit (`judge`'s `expect`).
    try:
        with release.stoppable(run.halt):
            checked = release.git(path, "rev-parse", "HEAD").strip()
            adapter = kept_adapter(path, o, checked, tree=True)
    except (ValueError, release.Stopped) as exc:
        return str(exc)
    gate, backend = str((adapter.get("slots") or {}).get("gate_cmd", "")).strip(), o["gate"]
    if not gate:
        return "어댑터의 `gate_cmd` 가 비었다"
    if why := oneshot_build.published(gate, backend):
        return why
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
    # As at S7: the contract's command as the server stored it, then the adapter's gate, then each pinned case.
    ran = [backend, gate] if gate != backend else [gate]
    env = oneshot_build.built_env(spec)   # read once: the freeze scan below looks for exactly these values
    verdict = oneshot_build.judged(path, run, spec, ran, env, expect=checked)
    if not verdict["ok"]:
        return f"최종 게이트 실패 — {verdict['reason']}\n{verdict['tail']}"
    if why := oneshot_build.held(path, run, spec, verdict):
        return why
    frozen = None
    if "android" in kinds_of(o):   # the APK S8 attaches, frozen while the check still holds the tree
        if why := oneshot_device.built(path, o["device"]) or release_checked(path, spec, verdict["head"], run.halt):
            return why
        try:
            frozen = froze(spec, verdict["head"], oneshot_needs.hidden(spec, env))
        except OSError as exc:   # the attachment is optional: the pull request opens and its body says why
            frozen = {"head": verdict["head"], "error": f"최종 게이트가 만든 APK 를 복사하지 못했다 — {exc}"}
    report = next(b["value"] for b in specs.blocks(final)[1] if b["name"] == "done-report")
    tail = (verdict.get("tail") or "").strip()
    # The pull request's body reads these as every spec's: the worker's report and the server's gate.
    specs.update(spec["repo"], spec["id"], report=report, gate={"cmd": "\n".join(ran), "head": verdict["head"],
                                                                "tail": tail, "selection": "full"})
    oneshot_spec.saved(spec["repo"], oneshot={"final_head": verdict["head"], "attempts": 0,
                                              **({"apk_frozen": frozen} if frozen else {})})
    specs.note(run, f"최종 게이트 통과 · {verdict['head'][:12]}")
    return ""


def release_checked(path: Path, spec: dict, head: str, halt: threading.Event) -> str:
    """The `[release]` table the README turn committed, read as a press reads it at `head`, and its Android
    build made once from a clean checkout of `head` with the version a press would publish after the seed, so
    the build is shown to take the version it is given, then checked as a release checks what a build made
    (`release.built_wrong`). Unsigned: no key exists before the first press, and none is made. Only Android's
    table, until step 7b. Every command ends within a second of a stop, the checkout's own included."""

    attempt = f"oneshot-{time.time_ns()}"
    try:
        with release.stoppable(halt):
            try:
                android, later, kit = _declared(path, spec, head)
            except ValueError as exc:
                return f"`[release]` 표 — {exc}"
            apk = release.build(release.checkout(path, head, attempt), android, later, kit, halt)
            seen = release.badging(release.run([kit["aapt2"], "dump", "badging", apk], release.environment(kit)))
    except (ValueError, release.Stopped) as exc:   # `BuildFailed` among them
        return f"`[release.android]` 의 정식 빌드 — {exc}"
    finally:
        with release.stoppable(None):   # the cleanup runs even after the stop
            if release.place(path, attempt).exists():
                release.discard(path, attempt)
    wrong = release.built_wrong(seen, later, android)
    return f"`[release.android]` 의 정식 빌드가 배포 검사에 걸린다 — {'; '.join(wrong)}" if wrong else ""


def kept_adapter(path: Path, o: dict, head: str, tree: bool = False) -> dict:
    """The adapter at `head`, parsed, refused (`ValueError`) unless it, and the worktree's with `tree`, is the
    approved frontend's with at most `[release]` added: the README turn may write that table alone."""

    def rest(text: str) -> dict:
        return {k: v for k, v in tomllib.loads(text).items() if k != "release"}

    try:
        was = rest(release.git(path, "show", f"{o.get('front_built_head') or o['tests_head']}:{ADAPTER}"))
        at = tomllib.loads(release.git(path, "show", f"{head}:{ADAPTER}"))
        now = [{k: v for k, v in at.items() if k != "release"},
               *([rest((path / ADAPTER).read_bytes().decode("utf-8"))] if tree else [])]
    except (ValueError, OSError) as exc:   # a TOML error is a `ValueError`
        raise ValueError(f"`{ADAPTER}` 를 읽지 못했다 — {exc}") from exc
    if any(n != was for n in now):
        raise ValueError(f"`{ADAPTER}` 의 `[release]` 밖이 바뀌었다 — 그 표만 더하고 나머지는 그대로 둔다")
    return at


def _declared(path: Path, spec: dict, head: str) -> tuple[dict, str, dict]:
    """The Android table at `head`, the version its build is made with, and the tools; a `ValueError` says why
    not. The adapter around it is the approved frontend's (`kept_adapter`)."""

    o = spec["oneshot"]
    kept_adapter(path, o, head)
    cfg = release.settings(path, head)
    if "android" not in cfg:
        raise ValueError(f"`{ADAPTER}` 에 `[release.android]` 가 없다 — 정식 빌드 명령, APK 경로, 앱 ID, ABI 를 적는다")
    if "desktop" in cfg:   # an installer's table comes with the oneshot's own installer (step 7b), checked then
        raise ValueError("`[release.desktop]` 는 아직 쓰지 않는다 — 원샷은 Android 앱의 표만 쓴다")
    android, first = cfg["android"], release.seed(path, head, cfg["version_source"])
    if android["application_id"] != (want := o["device"]["package"]):
        raise ValueError(f"`[release.android]` 의 `application_id` {android['application_id']!r} 가 앱의 {want!r} 와 다르다")
    release.version([], [], None, first, cfg)   # a seed a declared kind cannot carry, refused as the card would
    # A version a press could publish next: a build that keeps the source's version instead fails on it.
    for bump in release.BUMPS:   # a patch past what Android carries moves on to the minor, as a person would
        try:
            return android, release.version([{"tagName": f"v{first}"}], [], bump, first, cfg), release.tools(cfg)
        except ValueError:
            if bump == release.BUMPS[-1]:
                raise


def decisions(o: dict) -> list[str]:
    """What the owner decided at the three reviews, for the pull request's `변경 이유`."""

    # Read off each submission's record: an applied edit's comment is gone from the sheet's decisions.
    out = [f"{gate.upper()} {item} (round {entry['round']}): {comment}"
           for gate in ("r1", "r2", "r3") for entry in (o.get("reading") or {}).get(gate) or []
           for item, comment in (entry.get("edits") or {}).items()]
    if palette := (o.get("r2") or {}).get("palette"):
        out.append(f"R2 palette: {palette}")
    return out


def froze(spec: dict, head: str, hide: dict) -> dict:
    """The APK the final gate just built, copied out of the worktree while the check holds it, with its
    digest: the APK is a build output Git does not track, so nothing read from the tree later is the one the
    gate passed. An earlier gate's copy goes first."""

    base = oneshot_spec.store(spec["repo"]).folder / "release"
    shutil.rmtree(base, ignore_errors=True)
    name, into = f"{spec['repo']}-{head[:12]}-debug.apk", base / f"r{time.time_ns()}"
    try:
        into.mkdir(parents=True)
        shutil.copyfile(Path(spec["worktree"]) / spec["oneshot"]["device"]["apk"], into / name)
        # Read under the values its build ran with (`hide`, from the gate's own environment): an upload later,
        # after a value changed or was taken back, still needs this scan, bound to these bytes by their digest.
        scan = oneshot_needs.asset(into / name, spec, hide)
        return {"head": head, "name": name, "path": str(into / name), "sha256": android_owned.sha256(into / name),
                "scan": scan}
    except OSError:
        shutil.rmtree(into, ignore_errors=True)   # never a partial copy left behind
        raise


def released(spec: dict) -> dict:
    """The APK the final gate froze, attached to a draft prerelease of the repository, which the pull request's
    body links: the URL, the asset's name and digest and the head, or why it could not. The owner decided on
    2026-11-16 that a Release holds it: it works for a private repository and keeps."""

    o, path = spec["oneshot"], Path(spec["worktree"])
    head, frozen, old = o["final_head"], o.get("apk_frozen") or {}, o.get("apk") or {}
    if frozen.get("head") != head or frozen.get("error"):
        return {"head": head, "error": frozen.get("error") or "최종 게이트가 고정한 APK 가 없다"}
    # A resumed S8 asks for the text again: the same APK is not uploaded twice, a rebuilt one replaces it.
    if old.get("url") and (old.get("head"), old.get("sha256")) == (head, frozen["sha256"]):
        shutil.rmtree(Path(frozen["path"]).parent, ignore_errors=True)
        return old
    file, tag = Path(frozen["path"]), f"oneshot-apk-{head[:12]}"
    try:
        if not file.is_file() or android_owned.sha256(file) != frozen["sha256"]:
            raise ValueError("최종 게이트가 고정한 APK 사본이 없거나 바뀌었다")
        # The exact bytes that go up: clean under the values the build ran with (`froze`) and under today's.
        if why := ("scan" not in frozen and "최종 게이트가 고정한 APK 를 검사한 기록이 없다") or frozen["scan"] \
                or oneshot_needs.asset(file, spec):
            raise ValueError(why)
        repo = github_release.slug(release.remote(path))

        def found() -> dict | None:
            return next((r for r in github_release.releases(repo) if r.get("tag_name") == tag), None)

        # This head's release already there (an earlier upload, or a create whose reply was lost) gets this
        # APK as its asset: a second draft of the same tag would leave the first behind.
        # A transient failure is tried again, a bounded number of times, as `gh release` itself did.
        for n in range(TRIES):
            if held := found():
                break
            try:
                held = github_release.create(repo, tag, None, f"{spec['repo']} 디버그 APK · {head[:12]}",
                                             f"원샷 실행의 첫 PR 이 링크하는 디버그 APK 다. 빌드 커밋 `{head}`.",
                                             draft=True, prerelease=True)
                break
            except github_release.Failed as exc:   # a transient one is looked for again at the loop's top
                if not exc.transient or n == TRIES - 1:
                    if held := found():   # it was made; only the answer was lost
                        break
                    raise
        failed = None
        for n in range(TRIES + 1):   # the read after the last try decides: a last upload that landed counts
            mine = next((a for a in github_release.release(repo, held["id"]).get("assets") or []
                         if a["name"] == frozen["name"]), None)
            if n and mine and mine.get("state") == "uploaded" and github_release.digest(mine) == frozen["sha256"]:
                break   # the try before landed and only its answer was lost
            if n == TRIES:
                raise failed
            if mine:   # S8's own policy: the asset of that name is replaced
                github_release.delete_asset(repo, mine["id"])
            try:
                github_release.upload(repo, held["id"], file, frozen["name"])
                break
            except github_release.Failed as exc:
                if not exc.transient:
                    raise
                failed = exc
        return {"head": head, "name": frozen["name"], "sha256": frozen["sha256"], "url": held["html_url"], "tag": tag}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"head": head, "error": str(exc) or type(exc).__name__}
    finally:
        shutil.rmtree(file.parent, ignore_errors=True)


def asked(repo: str) -> None:
    """The gate passed: an Android app's APK goes up for the pull request to link, then one turn asks the
    worker for the pull request's text, as every spec's does."""

    spec = oneshot_spec.spec_of(repo)
    if why := oneshot_needs.publishable(Path(spec["worktree"]), spec):   # before anything goes out
        return oneshot_spec.store(repo).stop(STAGE, why) and None   # stopped: no next turn
    if "android" in kinds_of(spec["oneshot"]):   # alone or among several kinds
        spec = oneshot_spec.saved(repo, oneshot={"apk": released(spec)})
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
        oneshot_spec.store(repo).stop(STAGE, masked(f"PR 을 만들지 못했다 — {type(exc).__name__}: {exc}",
                                                    getattr(run.chat, "hide", None)))
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
# An Android app's README turn writes `[release]`; the pull request's text turn (`writing`) writes nothing there.
oneshot_build.GRANTS[STAGE] = lambda spec: "android" in kinds_of(spec["oneshot"]) and not spec.get("writing")
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
            "looks": len(o.get("looks") or []), "apk": o.get("apk")}


def extra(spec: dict) -> dict:
    return {"summary": summary(spec)} if spec["oneshot"].get("stage") == "done" else {}


oneshot_spec.VIEWS.append(extra)
