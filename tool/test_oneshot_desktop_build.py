"""oneshot 9e3b1: a PC app's frontend build — its `oneshot-desktop` block names the program its look starts apart
from the installer S8 attaches, its gate builds that program with only the declared tools, the server checks it and
freezes its folder into staging the run owns, and the run waits there, before the look.

See `docs/plans/oneshot/9-kinds.md` ("The runnable program"). The work turn is a stand-in: each test writes what a
session would have committed, then hands the turn to the check. Git and the gate run for real.
"""

import json
import os
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

from main import oneshot, oneshot_build, oneshot_desktop as pc, oneshot_front_build as front_build, oneshot_profile, \
    oneshot_spec
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_front_build import Run, adapter, git, o, report, spec, staged, write  # noqa: F401 — `staged`
from test_oneshot_spec import fenced, world  # noqa: F401 — `world` is the temporary worktree's fixture

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="a PC app is built on Windows only")

RUNNER = "python -m pytest -p no:cacheprovider -q --junitxml={report}"   # the declared tool, by its name
GATE = RUNNER.replace("{report}", oneshot_build.REPORT)
MAKE = ("import pathlib, sys\n"
        "out = pathlib.Path('dist-pc')\n"
        "(out / 'res').mkdir(parents=True, exist_ok=True)\n"
        "(out / 'app.exe').write_bytes(b'MZ' + pathlib.Path('manifest.txt').read_bytes())\n"
        "(out / 'res' / 'strings.txt').write_bytes(pathlib.Path('strings.txt').read_bytes())\n")
BLOCK = {"run": {"folder": "dist-pc", "program": "app.exe", "args": ["--data", "{data}"], "cwd": ".",
                 "env": {"WEBVIEW2_USER_DATA_FOLDER": "{data}/webview"}},
         "installer": "dist-pc-release/app-setup.exe"}
AS_INVOKER = b'<requestedExecutionLevel level="asInvoker" uiAccess="false"/>'


@pytest.fixture
def pc_build(staged):  # noqa: F811 — the imported fixture, requested by name
    """A PC app whose screens R2 approved: its build declares `python`, found for this stage, and its committed
    adapter extends the core's gate with the program's build."""

    stack = [{"part": "build", "choice": "python", "why": "주인이 골랐다", "status": "confirmed",
              "tools": [{"name": "python"}]}]
    oneshot_spec.saved("budget", oneshot={
        "intent": {"kind": "desktop", "kinds": ["desktop"]}, "stack": stack, "runner": RUNNER, "gate": GATE,
        "profile": {k: v for k, v in o()["profile"].items() if k != "responsive"} | {"window": "1280x800"},
        "toolpaths": {"stage": "build-front", "paths": {"python": os.path.abspath(shutil.which("python"))}}})
    write(staged, ".gitignore", "__pycache__/\nartifacts/\ndist-pc/\n")
    write(staged, "make_pc.py", MAKE)
    write(staged, "manifest.txt", AS_INVOKER.decode())
    write(staged, "strings.txt", "번역 켜기")
    write(staged, ".wiki/adapter.toml", adapter(gate=f"{GATE} && python make_pc.py", live=""))
    git(staged, "add", "-A")
    git(staged, "commit", "-m", "the PC app")
    return staged


def checked(tree, block=BLOCK):
    """The build turn handed to the check, with the look a failure: this version never starts it."""

    sent = []
    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)), \
            patch.object(front_build, "looked", lambda *a: pytest.fail("the look is the next version's")):
        then = oneshot_build.checked(tree, Run(), spec(), report() + fenced(pc.BLOCK, block))
        if then:
            then()
    return sent


def staging() -> list:
    folder = oneshot_spec.store("budget").folder / "desktop"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def test_a_pc_apps_build_is_frozen_with_its_identity_and_the_run_waits_before_its_look(pc_build):
    assert not checked(pc_build)
    build = o()["desktop_build"]
    assert o()["stage"] == "build-front" and o()["working"] is None and o()["stopped"]["reason"] == "kind"
    assert oneshot_profile.held(o()) and build["head"] == git(pc_build, "rev-parse", "HEAD")
    [into] = staging()
    manifest = json.loads((into / "manifest.json").read_text("utf-8"))
    assert build["folder"] == str(into / "app") and (into / "app" / "app.exe").is_file() and build["files"] == 2
    assert manifest == {"identity": build["identity"], "files": {"app.exe": manifest["files"]["app.exe"],
                                                                "res/strings.txt": manifest["files"]["res/strings.txt"]},
                        "launch": {"program": "app.exe", "args": ["--data", "{data}"], "cwd": ".",
                                   "env": {"WEBVIEW2_USER_DATA_FOLDER": "{data}/webview"}}}
    assert build["installer"] == "dist-pc-release/app-setup.exe"
    with patch.object(oneshot, "start_worker", lambda *a, **k: pytest.fail("a held run starts nothing")):
        oneshot_build.send("budget", "build-front", "again")   # a stopped run that tries the next stage
    assert o()["stopped"]["reason"] == "kind" and o()["desktop_build"] == build


def test_a_companion_file_or_a_launch_flag_changed_under_the_same_head_is_a_new_build(pc_build, tmp_path):
    subprocess.run([sys.executable, "make_pc.py"], cwd=pc_build, check=True)   # what the gate builds
    first = pc.frozen(pc_build, pc.declared(pc_build, fenced(pc.BLOCK, BLOCK)), tmp_path / "a")
    subprocess.run([sys.executable, "make_pc.py"], cwd=pc_build, check=True)
    assert pc.frozen(pc_build, pc.declared(pc_build, fenced(pc.BLOCK, BLOCK)), tmp_path / "b")["identity"] \
        == first["identity"]   # the same files and settings: the same build
    (pc_build / "dist-pc" / "res" / "strings.txt").write_text("번역 끄기")   # a build output, untracked: HEAD stays
    companion = pc.frozen(pc_build, pc.declared(pc_build, fenced(pc.BLOCK, BLOCK)), tmp_path / "c")
    flagged = {**BLOCK, "run": {**BLOCK["run"], "args": ["--data", "{data}", "--verbose"]}}
    subprocess.run([sys.executable, "make_pc.py"], cwd=pc_build, check=True)
    flag = pc.frozen(pc_build, pc.declared(pc_build, fenced(pc.BLOCK, flagged)), tmp_path / "d")
    assert len({first["identity"], companion["identity"], flag["identity"]}) == 3


def test_a_second_build_replaces_the_frozen_one(pc_build):
    checked(pc_build)
    first = o()["desktop_build"]
    oneshot_spec.saved("budget", oneshot={"desktop_build": None, "stopped": None, "working": "build-front"})
    write(pc_build, "strings.txt", "번역 끄기")
    git(pc_build, "commit", "-am", "a new label")
    assert not (sent := checked(pc_build)), sent
    assert o()["desktop_build"]["identity"] != first["identity"]
    assert len(staging()) == 1 and not os.path.exists(first["folder"])


@pytest.mark.parametrize("change, said", [
    ({"program": "app.msix"}, "패키지하지 않은 디버그 빌드"),
    ({"cwd": "C:/elsewhere"}, "run.cwd"),
    ({"cwd": "../.."}, "run.cwd"),
    ({"args": [], "env": {"APP_DATA": "C:/ProgramData/app"}}, "{data}"),
    ({"env": {"APP_DATA": "{data}", "ASSETS": "<tree>/web"}}, "작업트리를 가리킨다"),
])
def test_a_run_that_is_packaged_or_reaches_outside_its_copy_goes_back_to_the_build(pc_build, change, said):
    run = {**BLOCK["run"], **json.loads(json.dumps(change).replace("<tree>", str(pc_build).replace("\\", "/")))}
    sent = checked(pc_build, {**BLOCK, "run": run})
    assert sent and said in sent[0][2] and not o().get("desktop_build") and not staging()


@pytest.mark.parametrize("trouble, said", [
    ("elevated", "관리자 권한"), ("msix", "MSIX"), ("link", "링크"), ("installer", "같은 파일")])
def test_a_built_program_the_look_may_not_start_goes_back_to_the_build(pc_build, trouble, said, tmp_path):
    block = BLOCK
    if trouble == "elevated":
        write(pc_build, "manifest.txt", '<requestedExecutionLevel level="requireAdministrator" uiAccess="false"/>')
        git(pc_build, "commit", "-am", "elevated")
    elif trouble == "msix":
        write(pc_build, "make_pc.py", MAKE + "(out / 'AppxManifest.xml').write_text('<Package/>')\n")
        git(pc_build, "commit", "-am", "packaged")
    elif trouble == "link":
        (tmp_path / "outside").mkdir()
        write(pc_build, "make_pc.py", MAKE + f"import subprocess\nsubprocess.run(['cmd', '/c', 'mklink', '/J', "
                                             f"r'dist-pc\\linked', r'{tmp_path / 'outside'}'], check=True)\n")
        git(pc_build, "commit", "-am", "linked")
    else:
        block = {**BLOCK, "installer": "dist-pc/app.exe"}
    sent = checked(pc_build, block)
    assert sent and said in sent[0][2] and not o().get("desktop_build") and not staging()


def test_the_build_text_names_the_window_and_the_declared_tools(pc_build):
    text = front_build.build_front_text(spec())
    assert "1280x800 physical pixels" in text and "- `python`" in text and "oneshot-desktop" in text
    assert "{" not in text.replace("{data}", "").split("```json")[0] and "Android" not in text
