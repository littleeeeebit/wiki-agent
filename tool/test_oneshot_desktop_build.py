"""oneshot 9e3b and 9e4a: a PC app's frontend build — its `oneshot-desktop` block names the program its look starts
apart from the installer S8 attaches, its gate builds that program with only the declared tools, the server checks
it and freezes its folder into staging the run owns, its look launches that frozen copy, and a run that passes waits
at the critique.

See `docs/plans/oneshot/9-kinds.md` ("The runnable program"). The work turn is a stand-in: each test writes what a
session would have committed, then hands the turn to the check. Git and the gate run for real.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
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
PROGRAM = "sys.executable"   # a real Windows program whose manifest asks for no more than its caller's rights
MAKE = ("import pathlib, shutil, sys\n"
        "out = pathlib.Path('dist-pc')\n"
        "(out / 'res').mkdir(parents=True, exist_ok=True)\n"
        "shutil.copyfile({program}, out / 'app.exe')\n"
        "(out / 'res' / 'strings.txt').write_bytes(pathlib.Path('strings.txt').read_bytes())\n")
BLOCK = {"run": {"folder": "dist-pc", "program": "app.exe", "args": ["--data", "{data}"], "cwd": ".",
                 "env": {"WEBVIEW2_USER_DATA_FOLDER": "{data}/webview"}},
         "installer": "dist-pc-release/app-setup.exe"}
ELEVATED = r"C:\Windows\regedit.exe"   # its manifest asks for `highestAvailable`
SIDECAR = '<?xml version="1.0" encoding="UTF-16"?><assembly xmlns="urn:schemas-microsoft-com:asm.v1" ' \
          'manifestVersion="1.0"><trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges>' \
          '<requestedExecutionLevel level="requireAdministrator" uiAccess="false"/></requestedPrivileges></security>' \
          '</trustInfo></assembly>'


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
    write(staged, "make_pc.py", MAKE.format(program=PROGRAM))
    write(staged, "strings.txt", "번역 켜기")
    write(staged, ".wiki/adapter.toml", adapter(gate=f"{GATE} && python make_pc.py", live=""))
    git(staged, "add", "-A")
    git(staged, "commit", "-m", "the PC app")
    return staged


def checked(tree, block=BLOCK, found=(), looked=None):
    """The build turn handed to the check, each kind's look a stand-in: the PC app's records the build it was
    given and finds `found`, a web app's records that it ran and finds nothing. A critique a pass would start
    never runs."""

    sent, looked = [], [] if looked is None else looked

    def look(spec, build, shots, stopped):
        looked.append(build)
        return list(found)

    with patch.object(oneshot_build, "send", lambda *a: sent.append(a)), \
            patch.object(oneshot, "start_worker", lambda *a, **k: None), \
            patch.object(front_build.oneshot_desktop_look, "look", look), \
            patch.object(front_build.oneshot_look, "look", lambda *a: looked.append("webapp") or []):
        then = oneshot_build.checked(tree, Run(), spec(), report() + fenced(pc.BLOCK, block))
        if then:
            then()
    return sent


def staging() -> list:
    folder = oneshot_spec.store("budget").folder / "desktop"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def test_a_pc_apps_build_is_frozen_looked_at_and_its_critique_follows(pc_build):
    looked = []
    assert not checked(pc_build, looked=looked)
    build = o()["desktop_build"]
    assert looked == [build] and o()["looks"][-1]["build"] == build["identity"]   # the look launched the frozen copy
    assert o()["stage"] == "critique" and o()["working"] == "critique" and not o().get("stopped")
    assert not oneshot_profile.held(o()) and build["head"] == git(pc_build, "rev-parse", "HEAD")
    [into] = staging()
    manifest = json.loads((into / "manifest.json").read_text("utf-8"))
    assert build["folder"] == str(into / "app") and (into / "app" / "app.exe").is_file() and build["files"] == 2
    assert manifest == {"identity": build["identity"], "files": {"app.exe": manifest["files"]["app.exe"],
                                                                "res/strings.txt": manifest["files"]["res/strings.txt"]},
                        "launch": {"program": "app.exe", "args": ["--data", "{data}"], "cwd": ".",
                                   "env": {"WEBVIEW2_USER_DATA_FOLDER": "{data}/webview"}}}
    assert build["installer"] == "dist-pc-release/app-setup.exe" and build["checks"] == {}


@pytest.mark.parametrize("kinds", [["webapp", "desktop"], ["desktop", "webapp"]])
def test_among_several_kinds_the_pc_app_is_frozen_and_looked_at_in_the_owners_order(pc_build, kinds):
    stack = [{**o()["stack"][0], "part": "desktop.build"}]   # several kinds declare the PC app's tools on its part
    oneshot_spec.saved("budget", oneshot={"intent": {"kind": kinds[0], "kinds": kinds}, "stack": stack, "features": [],
                                          "first_screen": dict.fromkeys(kinds, "첫 화면")})
    write(pc_build, ".wiki/adapter.toml", adapter(gate=f"{GATE} && python make_pc.py && python --version"))
    git(pc_build, "commit", "-am", "the web app's build")
    looked = []
    assert not (sent := checked(pc_build, looked=looked)), sent
    assert [k if k == "webapp" else "desktop" for k in looked] == kinds and looked[kinds.index("desktop")] == \
        o()["desktop_build"]
    assert not o().get("stopped") and not oneshot_profile.held(o()) and o()["stage"] == "critique"


def test_a_look_check_owed_to_the_pc_app_needs_its_scenario_and_the_frozen_build_keeps_it(pc_build):
    oneshot_spec.saved("budget", oneshot={"owed": [{"id": "O1", "kind": "desktop", "source": "look",
                                                     "requirement": "R1", "check": "켜짐", "location": "core/a.rs:1",
                                                     "reason": "UI 에서만 보인다",
                                                     "act": {"action": "toggle", "id": "translate-on"},
                                                     "expect": {"id": "translate-on", "toggle": "On"}}]})
    assert 'act `{"action": "toggle", "id": "translate-on"}` · expect `{"id": "translate-on", "toggle": "On"}`' \
        in front_build.build_front_text(oneshot_spec.spec_of("budget"))
    sent = checked(pc_build)
    assert sent and "O1 의 시나리오가 없다" in sent[0][2] and not o().get("desktop_build")
    scenario = {"screen": "S1", "state": "full", "steps": [
        {"name": "on", "action": "toggle", "id": "translate-on", "expect": {"id": "translate-on", "toggle": "On"}}]}
    assert not checked(pc_build, {**BLOCK, "checks": {"O1": scenario}})
    assert o()["desktop_build"]["checks"] == {"O1": scenario} and o()["stage"] == "critique"
    assert o()["looks"][-1]["proven"] == ["O1"]   # the look that passed ran it: the critic reads that
    high = {"severity": "high", "screen": "S1", "viewport": "window", "state": "full", "what": "O1 이 통과하지 못했다"}
    oneshot_spec.saved("budget", oneshot={"stage": "build-front"})
    assert checked(pc_build, {**BLOCK, "checks": {"O1": scenario}}, found=[high])
    assert o()["looks"][-1]["proven"] == [] and o()["desktop_build"]["frozen"] > 0   # a failed look proves nothing


def test_a_pc_look_finding_sends_the_build_back_and_keeps_the_frozen_build(pc_build):
    high = {"severity": "high", "screen": "S1", "viewport": "window", "state": "full", "what": "단추가 이름이 없다"}
    sent = checked(pc_build, found=[high])
    assert sent and "S1 window full: 단추가 이름이 없다" in sent[0][2] and o()["stage"] == "build-front"
    assert o()["looks"][-1]["findings"] == [high] and o()["desktop_build"] and not oneshot_profile.held(o())


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
    oneshot_spec.saved("budget", oneshot={"stage": "build-front", "stopped": None, "working": "build-front"})
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
    *[({"args": ["--data", "{data}", setting]}, "사본 밖을 가리킬 수 있다") for setting in [
        "--assets=<tree>/web", "--config=<tree>/./app/../config.json", "--paths=C:/Windows;<TREE>\\bin",
        "--page=file:///<tree>/web/index%20page.html", "--page=file://<DRIVE>:/<REST>/web/index%20page.html",
        "--flags --root <tree>/web", "--root=\\\\localhost\\<DRIVE>$\\<REST>", "--root=\"<tree>\" --verbose",
        "--root <tree> --verbose", "--page=file:<tree>", "--sources=https://cdn.example/a.js;<tree>",
        "--home=\\\\fileserver\\share", "--home=//fileserver/share", "--root=/Users/someone/tree",
        "--root=web/../../..", "{data}/../escape", "--root=%USERPROFILE%\\tree", "--root=C:relative",
        "--root=%ProgramFiles(x86)%", "--page=res/%2e%2e/%2e%2e/escape", "--first\u0000second", "NUL",
        "--out=nul.txt", "--assets=res\\strings.txt", "$HOME/x", "~/x", "--title=\"quoted\"", "{other}/x",
        "--page=file:x", "--sources=https://cdn.example/a.js;C:/elsewhere", "--base=../#top", "--base=web/..",
        "@../args.txt", "@args.txt", "--port=COM¹", "--title=what?", "https:///", "https://", "shell:Profile",
        "--start=shell:Downloads", "https://cdn.example:99999/a.js", "https://999.999.999.999/v1",
        "https://0x7f.1/", "https://example.123/", "https://[zz]/", "https://cdn.example:0/",
        "https://1.2.3%2e4/", "https://[fe80::1%25eth0]/", "https://999.999.999.999\\@cdn.example/",
        "https://cdn^example/", "https://cdn|example/", "https://a<b>/", "https://xn--/", "https://xn--a/",
        "https://xn--a-wbb/",   # a label that begins with a combining mark
        "--log=myapp::ipc=trace"]],
    ({"env": {"oneshot_screen": "S9"}, "args": ["{data}"]}, "열 화면과 상태"),
    ({"env": {"APP_DATA": "{data}", "SHELL_PATH": "shell::Profile"}}, "사본 밖을 가리킬 수 있다"),
    ({"env": {"APPDATA": "{data}/named"}}, "look 이"),
    ({"args": ["--remote=https://cdn.example/{data}"], "env": {}}, "{data}"),
    ({"env": {"webview2_user_data_folder": "{data}/webview", "WEBVIEW2_USER_DATA_FOLDER": "{data}/w"}}, "대소문자만"),
    ({"env": {"APP_DATA": "{data}", "ASSETS": "<tree>/web"}}, "사본 밖을 가리킬 수 있다"),
    ({"env": {"APP_DATA": "{data}", "PATHS": "<tree>;C:/Windows"}}, "사본 밖을 가리킬 수 있다"),
    ({"program": "NUL.exe"}, "장치 이름"),
    ({"program": "app.exe."}, "끝의 `.`"),
])
def test_a_run_that_is_packaged_or_reaches_outside_its_copy_goes_back_to_the_build(pc_build, change, said):
    tree = str(pc_build).replace("\\", "/")
    run = {**BLOCK["run"], **json.loads(json.dumps(change).replace("<tree>", tree).replace(
        "<TREE>", tree.upper().replace("/", "\\\\")).replace("<DRIVE>", tree[0]).replace(
        "<REST>", tree[3:].replace("/", "\\\\")))}
    sent = checked(pc_build, {**BLOCK, "run": run})
    assert sent and said in sent[0][2] and not o().get("desktop_build") and not staging()


@pytest.mark.parametrize("installer", ["dist-pc/app.exe", "DIST-PC/APP.EXE", "release/CON.msi"])
def test_an_installer_that_is_the_program_or_a_device_goes_back_to_the_build(pc_build, installer):
    sent = checked(pc_build, {**BLOCK, "installer": installer})
    assert sent and ("같은 파일" in sent[0][2] or "장치 이름" in sent[0][2]) and not staging()


@pytest.mark.parametrize("trouble, said", [
    ("elevated", "관리자 권한"), ("sidecar", "관리자 권한"), ("zip", "프로그램이 아니다"), ("msix", "MSIX"),
    ("link", "링크"), ("big", "실행이 읽는 것보다 크다"), ("dll", "프로세스로 띄우는 32·64비트 프로그램이 아니다"),
    ("nested", "MSIX"), ("encoding", "XML 로 읽지 못했다")])
def test_a_built_program_the_look_may_not_start_goes_back_to_the_build(pc_build, trouble, said, tmp_path,
                                                                       monkeypatch):
    make, block = MAKE.format(program=PROGRAM), BLOCK
    if trouble == "dll":   # Windows maps a library for its resources, but never starts it as a process
        make = MAKE.format(program=repr(r"C:\Windows\System32\winmm.dll"))
    elif trouble == "nested":   # a packaged layout one folder down, the program inside it
        make += ("(out / 'package').mkdir()\nshutil.copyfile(out / 'app.exe', out / 'package' / 'app.exe')\n"
                 "(out / 'package' / 'AppxManifest.xml').write_text('<Package/>')\n")
        block = {**BLOCK, "run": {**BLOCK["run"], "program": "package/app.exe"}}
    elif trouble == "encoding":
        make += "(out / 'app.exe.manifest').write_bytes(b'<?xml version=\"1.0\" encoding=\"x-unknown\"?><a/>')\n"
    elif trouble == "elevated":
        if not os.path.exists(ELEVATED):
            pytest.skip("this Windows has no regedit.exe")
        make = MAKE.format(program=repr(ELEVATED))
    elif trouble == "sidecar":   # UTF-16, as Windows reads one beside a program with none embedded
        make += f"(out / 'app.exe.manifest').write_text({SIDECAR!r}, encoding='utf-16')\n"
    elif trouble == "zip":   # a package's layout under a program's name
        make += ("import zipfile\nwith zipfile.ZipFile(out / 'app.exe', 'w') as z:\n"
                 "    z.writestr('AppxManifest.xml', '<Package/>')\n")
    elif trouble == "msix":
        make += "(out / 'AppxManifest.xml').write_text('<Package/>')\n"
    elif trouble == "link":
        (tmp_path / "outside").mkdir()
        make += (f"import subprocess\nsubprocess.run(['cmd', '/c', 'mklink', '/J', r'dist-pc\\linked', "
                 f"r'{tmp_path / 'outside'}'], check=True)\n")
    else:
        monkeypatch.setattr(pc, "MAX_FILES", 1)   # two files: refused while copying
    write(pc_build, "make_pc.py", make)
    git(pc_build, "commit", "--allow-empty", "-am", trouble)
    sent = checked(pc_build, block)
    assert sent and said in sent[0][2] and not o().get("desktop_build") and not staging()


def test_a_folder_turned_into_a_junction_after_its_check_is_never_read(pc_build, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not the build's")
    subprocess.run([sys.executable, "make_pc.py"], cwd=pc_build, check=True)   # what the gate builds
    real = pc.is_junction

    def swapped(path):   # the check sees the folder the gate built; the walk then meets a junction outside
        seen = real(path)
        if path.name == "res" and not seen:
            shutil.rmtree(path)
            subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(outside)], check=True, capture_output=True)
        return seen

    monkeypatch.setattr(pc, "is_junction", swapped)
    into = tmp_path / "into"
    with pytest.raises(ValueError, match="밖 파일로 열렸다"):
        pc.frozen(pc_build, pc.declared(pc_build, fenced(pc.BLOCK, BLOCK)), into)
    assert not (into / "app" / "res" / "secret.txt").exists()


@pytest.mark.parametrize("setting", [
    "--cdn=https://cdn.example/web/app.js", "--socket=ws://127.0.0.1:5173/live", "--assets=res/strings.txt",
    "--cache={data}/cache", "--size=1280x800", "--langs=ko,en;fallback=en", "--name=file-viewer",
    "--title=Translator: on", "--title=번역 켜기", "https://x.example/?return=/home",
    "https://x.example/search?q=%ED%95%9C", "--api=https://[::1]:8443/v1", "--api=https://127.0.0.1:8080/v1",
    "--api=https://127.0.0.1./v1", "--cdn=https://xn--bcher-kva.example/a.js",
    "--cdn=https://xn--mgba3gch31f060k.example/a.js"])   # a joiner in its context
def test_a_setting_naming_the_copy_data_or_a_remote_url_is_kept(pc_build, setting):
    run = {**BLOCK["run"], "args": ["--data", "{data}", setting]}
    assert pc.declared(pc_build, fenced(pc.BLOCK, {**BLOCK, "run": run}))["run"]["args"][-1] == setting


@pytest.mark.parametrize("name, value", [("RUST_LOG", "tauri=debug,myapp::ipc=trace"),
                                         ("rust_log", "tauri=debug,myapp::ipc=trace"), ("RUST_LOG", "번역기::화면=trace"),
                                         ("RUST_LOG", "ऐप::हिंदी=trace")])   # combining marks continue an identifier
def test_rusts_log_filter_may_name_modules(pc_build, name, value):
    run = {**BLOCK["run"], "env": {**BLOCK["run"]["env"], name: value}}
    assert pc.declared(pc_build, fenced(pc.BLOCK, {**BLOCK, "run": run}))["run"]["env"][name] == run["env"][name]


def test_a_file_resolved_under_another_case_of_its_folder_is_never_read(pc_build, tmp_path, monkeypatch):
    # A case-sensitive folder may hold `dist-pc` and `DIST-PC` apart: only the folder the check opened counts.
    subprocess.run([sys.executable, "make_pc.py"], cwd=pc_build, check=True)
    root, real = pc._folder(pc_build / "dist-pc"), pc._final
    monkeypatch.setattr(pc, "_folder", lambda _path: root)
    monkeypatch.setattr(pc, "_final", lambda handle: real(handle).replace("\\dist-pc\\", "\\DIST-PC\\"))
    with pytest.raises(ValueError, match="밖 파일로 열렸다"):
        pc.frozen(pc_build, pc.declared(pc_build, fenced(pc.BLOCK, BLOCK)), tmp_path / "into")


@pytest.mark.parametrize("record, kept", [
    ("unreadable", True), ("no-head", False), ("nul", False), ("unreadable-and-missing", False),
    ("unreadable-and-moved", False)])
def test_an_unreadable_snapshot_is_kept_and_one_shown_gone_or_malformed_is_built_again(pc_build, record, kept,
                                                                                        monkeypatch):
    checked(pc_build)
    build = o()["desktop_build"]
    if record == "no-head":
        oneshot_spec.saved("budget", oneshot={"desktop_build": {k: v for k, v in build.items() if k != "head"}})
    elif record == "nul":
        oneshot_spec.saved("budget", oneshot={"desktop_build": {**build, "folder": build["folder"] + "\0x"}})
    else:   # the program unreadable; with the manifest also gone, or the worktree at another HEAD, that wins
        if record == "unreadable-and-missing":
            (Path(build["folder"]).parent / "manifest.json").unlink()
        elif record == "unreadable-and-moved":
            write(pc_build, "strings.txt", "번역 끄기")
            git(pc_build, "commit", "-am", "moved on")
        real = Path.stat

        def denied(path, *a, **kw):
            if path.name == "app.exe":
                raise PermissionError(13, "denied", str(path))
            return real(path, *a, **kw)

        monkeypatch.setattr(Path, "stat", denied)
    oneshot_spec.recover()
    monkeypatch.undo()
    assert bool(o()["desktop_build"]) is kept and o()["stage"] == ("critique" if kept else "build-front")   # rebuilt


def test_a_worktree_named_by_its_extended_length_path_is_copied(pc_build, tmp_path):
    subprocess.run([sys.executable, "make_pc.py"], cwd=pc_build, check=True)
    tree = Path("\\\\?\\" + str(pc_build))
    assert pc.frozen(tree, pc.declared(tree, fenced(pc.BLOCK, BLOCK)), tmp_path / "into")["files"] == 2


def test_a_build_folder_stored_under_another_case_is_never_copied(pc_build, tmp_path, monkeypatch):
    # The folder opened must be the one declared, spelled as stored: a case-sensitive sibling never stands in.
    subprocess.run([sys.executable, "make_pc.py"], cwd=pc_build, check=True)
    real = pc._folder
    monkeypatch.setattr(pc, "_folder", lambda path: real(path).replace("\\dist-pc", "\\DIST-PC"))
    with pytest.raises(ValueError, match="그 이름 그대로의 폴더"):
        pc.frozen(pc_build, pc.declared(pc_build, fenced(pc.BLOCK, BLOCK)), tmp_path / "into")



def test_a_frozen_build_that_cannot_be_saved_leaves_no_copy(pc_build):
    real = oneshot_spec.saved

    def failing(repo, **fields):
        if "desktop_build" in fields.get("oneshot", {}):
            raise OSError("the disk is full")
        return real(repo, **fields)

    subprocess.run([sys.executable, "make_pc.py"], cwd=pc_build, check=True)   # what the gate builds
    with patch.object(oneshot_spec, "saved", failing), pytest.raises(OSError, match="disk is full"):
        front_build.froze(pc_build, spec(), pc.declared(pc_build, fenced(pc.BLOCK, BLOCK)), git(pc_build, "rev-parse",
                                                                                                 "HEAD"), Run())
    assert not staging() or not any(staging()[0].iterdir())


@pytest.mark.parametrize("gone", ["copy", "program", "manifest", "companion", "identity", "shape", "head"])
def test_a_frozen_build_whose_copy_is_gone_or_whose_head_moved_is_built_again(pc_build, gone):
    checked(pc_build)
    app = Path(o()["desktop_build"]["folder"])
    if gone == "copy":
        shutil.rmtree(app)
    elif gone == "program":
        (app / "app.exe").unlink()
    elif gone == "manifest":
        (app.parent / "manifest.json").unlink()
    elif gone == "companion":   # a file the manifest records, beside the program
        next(p for p in app.rglob("*") if p.is_file() and p.name != "app.exe").unlink()
    elif gone == "identity":   # a manifest rewritten to other files than the saved identity names
        manifest = json.loads((app.parent / "manifest.json").read_text(encoding="utf-8"))
        manifest["files"] = {name: "0" * 64 for name in manifest["files"]}
        (app.parent / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    elif gone == "shape":   # JSON, but no manifest: recovery must not raise on it
        (app.parent / "manifest.json").write_text("[]", encoding="utf-8")
    else:
        write(pc_build, "strings.txt", "번역 끄기")
        git(pc_build, "commit", "-am", "moved on")
    oneshot_spec.saved("budget", oneshot={"stopped": {"reason": "restart", "state": "critique", "detail": "x"}})
    oneshot_spec.recover()   # held at the critique with nothing to hold: back to the build, resumable
    assert not o()["desktop_build"] and o()["stage"] == "build-front" and not oneshot_profile.held(o())
    assert o()["stopped"]["reason"] == "restart" and "다시 빌드한다" in o()["stopped"]["detail"]


@pytest.mark.parametrize("git_fails", [subprocess.TimeoutExpired("git", 60), "nonzero"])
def test_a_head_that_cannot_be_read_keeps_the_frozen_build_and_the_startup(pc_build, git_fails):
    checked(pc_build)
    before = o()["desktop_build"]

    def reading(args, cwd, **_kw):
        if git_fails == "nonzero":
            return subprocess.CompletedProcess(args, 128, "", "fatal: not a git repository")
        raise git_fails

    with patch.object(oneshot_spec.specs, "sh", reading):
        oneshot_spec.recover()
    assert o()["desktop_build"] == before and o()["stage"] == "critique"


TOGGLE = {"name": "on", "action": "toggle", "id": "translate-on", "expect": {"id": "translate-on", "toggle": "On"}}
CHECK = {"screen": "S1", "state": "full", "steps": [
    {"name": "type", "action": "set_value", "id": "source", "value": "hello", "expect": {"id": "source", "value": "hello"}},
    {**TOGGLE, "in": "owned"}]}


@pytest.mark.parametrize("step, why", [
    ({**TOGGLE, "action": "click"}, "action"),
    ({**TOGGLE, "id": " "}, "AutomationId"),
    ({**TOGGLE, "value": "x"}, "set_value 에만"),
    ({**TOGGLE, "in": "desktop"}, "in 은"),
    ({**TOGGLE, "name": "a b"}, "name"),
    ({**TOGGLE, "expect": {"toggle": "On"}}, "expect"),
    ({**TOGGLE, "expect": {"id": "translate-on", "toggle": "Yes"}}, "toggle 은"),
    ({**TOGGLE, "expect": {"id": "translate-on", "name": ""}}, "expect"),
    ({**TOGGLE, "expect": {"id": "translate-on"}}, "마지막 step"),   # the postcondition reads a property
    ({**TOGGLE, "expect": {**TOGGLE["expect"], "in": "desktop"}}, "in 은 main 이나 owned"),
    ({**TOGGLE, "action": "set_value", "value": "𝄞" * 600}, "UTF-16 1000자"),   # 1200 code units: the driver drops it
    ({**TOGGLE, "expect": {"id": "translate-on", "value": "𝄞" * 600}}, "UTF-16 1000자"),
])
def test_a_look_checks_scenario_outside_the_drivers_steps_is_refused(pc_build, step, why):
    assert pc.declared(pc_build, fenced(pc.BLOCK, {**BLOCK, "checks": {"O1": CHECK}}))["checks"] == {"O1": CHECK}
    with pytest.raises(ValueError, match=why):
        pc.declared(pc_build, fenced(pc.BLOCK, {**BLOCK, "checks": {"O1": {**CHECK, "steps": [step]}}}))
    with pytest.raises(ValueError, match="screen 과 state 는 문자열"):   # a build correction, never a TypeError
        pc.declared(pc_build, fenced(pc.BLOCK, {**BLOCK, "checks": {"O1": {**CHECK, "screen": []}}}))


@pytest.mark.parametrize("checks, why", [
    ({"O1": CHECK}, ""), ({}, "O1 의 시나리오가 없다"), ({"O1": CHECK, "O9": CHECK}, "O9"),
    ({"O1": CHECK, "O4": CHECK}, "O4"),   # an earlier version's `look` check without `expect` is the owner's
    ({"O1": {**CHECK, "screen": "S9"}}, "PC 앱의 화면"), ({"O1": {**CHECK, "state": "busy"}}, "PC 앱의 화면"),
    ({"O1": {**CHECK, "steps": [*CHECK["steps"][:1], {**TOGGLE, "in": "owned",
                                                      "expect": {"id": "translate-on", "name": "번역"}}]}},
     "리뷰가 정한 동작"),   # the builder's own postcondition never stands in for the review's
    ({"O1": {**CHECK, "steps": [*CHECK["steps"][:1], {**TOGGLE, "action": "invoke", "id": "refresh"}]}},
     "리뷰가 정한 동작"),   # nor its own step
    ({"O1": {**CHECK, "steps": [*CHECK["steps"][:1], {**TOGGLE, "in": "main"}]}}, "리뷰가 정한 동작")])
def test_the_checks_script_exactly_the_look_checks_owed_to_the_pc_app(pc_build, checks, why):
    owed = [{"id": "O1", "kind": "desktop", "source": "look", "expect": TOGGLE["expect"],
             "act": {"action": "toggle", "id": "translate-on", "in": "owned"}},
            {"id": "O2", "kind": "desktop", "source": "owner"}, {"id": "O3", "kind": "android"},
            {"id": "O4", "kind": "desktop", "source": "look"}]
    run = {"oneshot": {"owed": owed, "front": {"screens": [{"id": "S1"}, {"id": "S2", "kind": "webapp"}]}}}
    got = pc.scripted(checks, run)
    assert (why in got if why else got == "") and (not why or got)


def test_the_build_text_names_the_window_and_the_declared_tools(pc_build):
    text = front_build.build_front_text(spec())
    assert "1280x800 physical pixels" in text and "- `python`" in text and "oneshot-desktop" in text
    assert "{" not in text.replace("{data}", "").split("```json")[0] and "Android" not in text and "<!--" not in text
