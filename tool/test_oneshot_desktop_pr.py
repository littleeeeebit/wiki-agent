"""oneshot step 9e6b: S8 of a PC app — the README turn writes the PC app's section and its `[release.desktop]`
table; after the final gate the server builds that table's installer once from a clean checkout, checks it as a
release does and freezes it; one draft prerelease per head carries it (with the APK when an Android app is among
the kinds), each file reused only for the same HEAD and digest; the run then waits before its pull request's text.

See `docs/plans/oneshot/9-kinds.md` ("R3 and S8", "Steps and their stops") and `docs/plans/release/0-overview.md`
(7b). Git and the gate run for real in a temporary worktree. The release build is a stand-in writing a synthetic NSIS
installer, which the real checks read (`test_release_desktop.nsis`). GitHub's Releases API is `FakeGitHub`, so
nothing goes out.
"""

import os
import shutil
import struct
import threading
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from main import github_release, oneshot_build, oneshot_needs, oneshot_pr, oneshot_spec, pr_text, release, \
    release_desktop as rd
from test_github_release import FakeGitHub
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_device import DEVICE
from test_oneshot_front_build import adapter, git, write
from test_oneshot_pr import answer, check, finished, o, readme, report, spec  # noqa: F401 — `finished` is a fixture
from test_oneshot_spec import world  # noqa: F401 — `world` is a fixture
from test_release_desktop import APP, nsis, resources  # noqa: F401 — `resources` is a fixture

RUNNER = "python -m pytest -p no:cacheprovider -q --junitxml={report}"   # the declared tool, by its name
GATE = RUNNER.replace("{report}", oneshot_build.REPORT)
DESKTOP = ('\n[release]\nversion_source = "version.json"\n\n[release.desktop]\nbuild = "python tauri.py"\n'
           'artifact = "nsis/r_{version}_x64-setup.exe"\nexe = "r.exe"\nproduct_name = "r"\npublisher = "o"\n')
ANDROID = (f'\n[release.android]\nbuild = "gradlew.bat assembleRelease"\nartifact = "app/release.apk"\n'
           f'application_id = "{DEVICE["package"]}"\nabi = "none"\n')
NOT_YET = "the pull request is the next step's"


@pytest.fixture
def pc(finished, resources, tmp_path, monkeypatch):  # noqa: F811 — the imported fixtures, requested by name
    """A PC app at S8 whose release build writes a synthetic NSIS installer holding the app EXE it made."""

    tree, sent = finished
    stack = [{"part": "build", "choice": "tauri", "why": "주인이 골랐다", "status": "confirmed",
              "tools": [{"name": "python"}]}]   # the gate's one program, found for this stage
    write(tree, ".gitignore", "__pycache__/\nartifacts/\n*.apk\n")
    write(tree, ".wiki/adapter.toml", adapter(gate=GATE))
    write(tree, "version.json", '{"version": "0.1.0"}')
    git(tree, "add", "-A")
    git(tree, "commit", "-m", "the approved build")
    head = git(tree, "rev-parse", "HEAD")
    oneshot_spec.saved("budget", oneshot={
        "intent": {"kind": "desktop"}, "tests_head": head, "front_built_head": head, "runner": RUNNER, "gate": GATE,
        "stack": stack, "toolpaths": {"stage": "pr", "paths": {"python": os.path.abspath(shutil.which("python"))}},
        "desktop_build": {"head": head, "installer": "src-tauri/target/release/bundle/nsis/r_0.1.0_x64-setup.exe"}})
    monkeypatch.setattr(oneshot_build, "toolchain", lambda spec: None)   # the gate runs as the test's own Python
    monkeypatch.setattr(oneshot_build, "built_env", lambda spec: None)
    monkeypatch.setattr(release, "WORK", tmp_path / "release-work")
    monkeypatch.setattr(release, "environment", lambda kit: {})
    built = []

    def build(src, kind, version, kit, halt, read=None, drop=frozenset()):
        assert release._stop.halt is halt   # under the stop's scope
        built.append((git(src, "rev-parse", "HEAD"), git(src, "status", "--porcelain"), version, kind["artifact"],
                      kind["build"], drop))
        if "exe" not in kind:   # Android's, as `test_oneshot_pr.releasing` stands it in
            return src / kind["artifact"]
        (folder := tmp_path / f"build-{len(built)}").mkdir()
        installer = release.output(src, kind, version)
        installer.parent.mkdir(parents=True, exist_ok=True)
        installer.write_bytes(nsis(folder).read_bytes())
        release.output(src, kind, version, "exe").write_bytes(APP)
        read(installer)
        return installer

    monkeypatch.setattr(release, "build", build)
    monkeypatch.setattr(release, "remote", lambda repo: "github.com/me/budget")
    return tree, sent, FakeGitHub("me/budget").install(monkeypatch), built


def approved(tree, *files):
    """More of the app in the build R3 approved: committed, and every head the run keeps moved to it."""

    for name, text in files:
        write(tree, name, text)
    git(tree, "add", "-A")
    git(tree, "commit", "-m", "more of the approved build")
    head = git(tree, "rev-parse", "HEAD")
    oneshot_spec.saved("budget", oneshot={"tests_head": head, "front_built_head": head,
                                          "desktop_build": {**o()["desktop_build"], "head": head}})


def tabled(tree, *more):
    """The README turn's commit: the README and the release table."""

    readme(tree, (".wiki/adapter.toml", (tree / ".wiki/adapter.toml").read_text(encoding="utf-8") + "".join(more)))


def on_prerelease(fake, head):
    [rel] = [r for r in fake.rels if r["tag_name"] == f"oneshot-{head[:12]}"]
    return rel, {a["name"]: github_release.digest(a) for a in rel["assets"]}


def staged() -> list[Path]:
    return [p for key in oneshot_pr.ASSETS for p in oneshot_pr.staging(spec(), key).rglob("*") if p.is_file()]


def test_the_readme_turn_has_a_pc_section_and_its_release_table(pc):
    text = oneshot_pr.readme_text(spec())
    assert "This is a PC app (a Windows program)." in text and "[release.desktop]" in text and "adb's" not in text
    assert "version_source = " in text and "r_0.1.0_x64-setup.exe" in text and "not a Tauri app" in text
    oneshot_spec.saved("budget", oneshot={"intent": {"kind": "android", "kinds": ["android", "desktop"]}, "device": DEVICE})
    text = oneshot_pr.readme_text(spec())
    assert "In the PC app's section," in text and "beside `[release.android]`" in text
    assert text.count("version_source = ") == 1   # one `[release]`, the Android table's
    grant = oneshot_build.GRANTS[oneshot_pr.STAGE]   # the README turn alone may write the table
    assert grant(spec()) and not grant({**spec(), "writing": {"head": "h", "asked": 1}})


def test_the_release_tables_installer_goes_alone_to_the_prerelease_and_the_run_waits_before_its_pull_request(pc):
    tree, sent, github, built = pc
    tabled(tree, DESKTOP)
    asked = len(sent)
    with patch.object(pr_text, "writing", lambda *a: pytest.fail(NOT_YET)):
        check(tree, answer(report()))()
    head, approved = o()["final_head"], o()["desktop_build"]["head"]
    # Built once, from a clean checkout of the commit R3 approved, with the version a press would publish next,
    # its program the confirmed tool's checked path.
    [(at, status, version, artifact, command, _)] = built
    assert (at, status, version, artifact) == (approved, "", "0.1.1", "nsis/r_{version}_x64-setup.exe")
    assert approved != head and o()["installer"]["built"] == approved
    assert command != "python tauri.py" and Path(shutil.which("python")).name.lower() in command.lower()
    assert list((release.WORK / tree.name).iterdir()) == []   # the checkout went with the check
    rel, digests = on_prerelease(github, head)
    name = f"budget-{head[:12]}-x64-setup.exe"
    assert rel["draft"] and rel["prerelease"] and list(digests) == [name]
    assert o()["installer"] == {"head": head, "name": name, "sha256": digests[name], "url": rel["html_url"],
                                "tag": f"oneshot-{head[:12]}", "version": "0.1.1", "built": approved} and "apk" not in o()
    assert not staged()   # the copy it uploaded is gone
    stop = o()["stopped"]
    assert stop["reason"] == "kind" and "풀 리퀘스트는 다음 버전에서" in stop["detail"]
    assert "설치 파일 — 초안 프리릴리스에 올렸다" in stop["detail"]
    assert len(sent) == asked and not spec().get("writing")
    body = "\n".join(pr_text.apk_section(spec()))
    assert f"- PC 앱 설치 파일: [{name}]({rel['html_url']})" in body and "버전 0.1.1" in body and "## APK" not in body
    assert f"빌드 커밋 `{approved[:12]}`" in body
    # Held there through a restart, though the README turn's commit left the frozen build's HEAD: `[재개]` is refused.
    oneshot_spec.recover()
    assert o()["stage"] == "pr" and o()["stopped"]["reason"] == "kind" and o()["desktop_build"]
    with pytest.raises(Exception, match="다음 버전"):
        oneshot_spec.resume("budget")
    # A restart between the saved records and the stop: the stop rebuilt from them says what went up too.
    oneshot_spec.saved("budget", oneshot={"stopped": None})
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "kind" and "설치 파일 — 초안 프리릴리스에 올렸다" in o()["stopped"]["detail"]


def test_a_pc_app_with_no_installer_table_still_finishes_its_gate_and_says_why(pc):
    tree, _, github, built = pc
    oneshot_spec.saved("budget", oneshot={"stack": [{**o()["stack"][0], "choice": "WinUI 3"}]})   # another stack
    readme(tree)   # so no `[release]`
    with patch.object(pr_text, "writing", lambda *a: pytest.fail(NOT_YET)):
        check(tree, answer(report()))()
    assert o()["final_head"] and not built and not github.calls
    assert o()["installer"]["error"] == oneshot_pr.NO_INSTALLER
    assert "설치 파일 — 올리지 못했다" in o()["stopped"]["detail"] and o()["stopped"]["reason"] == "kind"
    assert "PC 앱 설치 파일을 Release 에 올리지 못했다 — `[release.desktop]` 가 없다" in "\n".join(pr_text.apk_section(spec()))


@pytest.mark.parametrize("wrong, said", [
    ("build", "`[release.desktop]` 의 정식 빌드 — 빌드 실패"),
    ("version", "`[release.desktop]` 의 정식 빌드가 배포 검사에 걸린다 — 설치 파일 버전"),
    ("exe", "앱 EXE 와 다르다"),
    ("tauri", "`[release.desktop]` 가 없다 — 확정한 PC 앱 스택이 Tauri 다"),
    ("tool", "`cargo` 는 확정한 도구도 작업트리 안의 파일도 아니거나"),
    ("chain", "`build` 는 평범한 명령을 ` && ` 로만 잇는다"),
    ("far", "`build` 가 체크아웃 밖의 경로를 가리킨다"),
    ("rooted", "`build` 가 체크아웃 밖의 경로를 가리킨다"),
    ("inline", "`build` 는 평범한 명령을 ` && ` 로만 잇는다"),   # an inline config is no plain command
    ("source", "빌드가 체크아웃의 커밋된 파일을 바꿨다")])
def test_a_release_table_whose_build_fails_its_checks_sends_the_readme_turn_back(pc, monkeypatch, wrong, said):
    tree, sent, github, _ = pc
    if wrong == "tauri":   # the confirmed stack is Tauri, and the README turn left its table out
        readme(tree)
    else:
        tabled(tree, DESKTOP.replace('"python tauri.py"', {"tool": '"cargo tauri build"', "chain": '"python a.py; cargo b"',
                                                                 "far": "'python tauri.py --dist ../tree/dist'",
                                                                 "rooted": "'python tauri.py --dist \\wt\\dist'",
                                                                 "inline": """'python tauri.py --config {"build":"""
                                                                           """{"frontendDist":"../tree/dist"}}'"""}
                                     .get(wrong, '"python tauri.py"')))
    real = release.build
    if wrong == "source":   # a build that rewrites a committed file
        monkeypatch.setattr(release, "build", lambda src, *a, **k: (src / "version.json").write_text("{}")
                            and real(src, *a, **k))
    elif wrong == "build":
        monkeypatch.setattr(release, "build", lambda *a, **k: (_ for _ in ()).throw(release.BuildFailed("빌드 실패")))
    elif wrong == "version":   # a build that kept the source's version instead of the one it was given
        monkeypatch.setattr(rd, "version_info", lambda file: {"version": "0.1.0", "ProductName": "r",
                                                               "FileVersion": "0.1.0", "CompanyName": "o"})
    else:   # the installer holds another app EXE than the one the build made
        real = rd.facts

        def other(installer, exe):
            exe.write_bytes(b"MZ another")
            return real(installer, exe)

        monkeypatch.setattr(rd, "facts", other)
    check(tree, answer(report()))()
    assert sent[-1][1] == "pr" and said in sent[-1][2] and "final_head" not in o()
    assert not staged() and not github.calls and not list((release.WORK / tree.name).glob("*"))


def mixed(tree, monkeypatch):
    """An Android app among the kinds: the PC app's tools then live in `desktop.build`, the approved build holds
    the Android project's wrapper, the gate builds the debug APK, and its release APK reads as the table says."""

    stack = [{**o()["stack"][0], "part": "desktop.build"}]
    oneshot_spec.saved("budget", oneshot={"intent": {"kind": "android", "kinds": ["android", "desktop"]},
                                          "device": DEVICE, "stack": stack})
    approved(tree, ("gradlew.bat", "@echo off\n"), ("app/build.gradle.kts", "android {}\n"))
    for apk in (DEVICE["apk"], DEVICE["tests"]["apk"]):
        write(tree, apk, "apk")   # as the gate builds them: ignored outputs
    real = oneshot_build.judged

    def building(path, *a, **k):   # the gate builds the debug APK again, as a real Android gate does
        verdict = real(path, *a, **k)
        (Path(path) / DEVICE["apk"]).write_text("apk", encoding="utf-8")
        return verdict

    monkeypatch.setattr(oneshot_build, "judged", building)
    monkeypatch.setattr(release, "tools", lambda cfg: {"aapt2": "aapt2"})
    monkeypatch.setattr(release, "run", lambda argv, env=None, timeout=300: "badging")
    monkeypatch.setattr(release, "badging", lambda text: {"package": DEVICE["package"], "code": 1001, "name": "0.1.1",
                                                          "abis": [], "debuggable": False})


def test_with_an_android_app_both_files_go_to_one_prerelease_and_a_resumed_upload_sends_neither_again(pc, monkeypatch):
    tree, sent, github, built = pc
    mixed(tree, monkeypatch)
    write(tree, "app/build.gradle.kts", 'android {}\nval versionName = System.getenv("RELEASE_VERSION")\n')   # its wiring
    tabled(tree, DESKTOP, ANDROID)
    with patch.object(pr_text, "writing", lambda *a: pytest.fail(NOT_YET)):
        check(tree, answer(report()))()
    head = o()["final_head"]
    # Each kind from its own checkout: the APK of the gate's HEAD, the installer of the commit R3 approved.
    assert [(b[0], b[3]) for b in built] == [(head, "app/release.apk"),
                                             (o()["desktop_build"]["head"], "nsis/r_{version}_x64-setup.exe")]
    assert "gradlew.bat" in built[0][4] and built[0][4] != "gradlew.bat assembleRelease"   # the checkout's own file
    assert not list((release.WORK / tree.name).glob("*"))   # both checkouts gone
    rel, digests = on_prerelease(github, head)
    assert sorted(digests) == [f"budget-{head[:12]}-debug.apk", f"budget-{head[:12]}-x64-setup.exe"]
    assert o()["apk"]["url"] == o()["installer"]["url"] == rel["html_url"]
    assert len([c for c in github.calls if c[0] == "POST" and c[1] == "releases"]) == 1
    first, calls = (o()["apk"], o()["installer"]), len(github.calls)
    with patch.object(pr_text, "writing", lambda *a: pytest.fail(NOT_YET)):
        oneshot_pr.asked("budget")   # a resumed S8: the same head and digests, so nothing goes up again
    assert (o()["apk"], o()["installer"]) == first and not [c for c in github.calls[calls:] if c[0] != "GET"]
    body = "\n".join(pr_text.apk_section(spec()))
    assert "## APK" in body and "## 설치 파일" in body


@pytest.mark.parametrize("name, text", [
    ("app.py", "a change R3 never saw\n"),
    ("app-version.json", '{"version": "0.1.0", "server": "https://example.invalid"}')])   # more than the version
def test_a_pc_apps_file_changed_after_the_approved_build_is_refused(pc, name, text):
    # A PC app alone: the pull request's app is then the installer's.
    tree, sent, _, built = pc
    write(tree, name, text)
    tabled(tree, DESKTOP.replace('"version.json"', '"app-version.json"'))
    check(tree, answer(report()))()
    assert "R3 가 승인한 PC 앱 빌드 뒤에 앱 파일이 바뀌었다" in sent[-1][2] and f": {name}" in sent[-1][2]
    assert "final_head" not in o() and not built
    assert "a version file it adds holding the version alone." in oneshot_pr.readme_text(spec())


def test_with_a_web_app_the_pc_apps_files_are_held_as_alone(pc):
    tree, _, _, _ = pc
    write(tree, "app.py", "a change R3 never saw\n")
    tabled(tree, DESKTOP)
    both = {**o(), "intent": {"kind": "webapp", "kinds": ["webapp", "desktop"]}}
    assert oneshot_pr.unapproved(tree, both, git(tree, "rev-parse", "HEAD"), {"release": {}}) == ["app.py"]
    assert "Any other change is refused." in oneshot_pr.scope(both)


def test_a_build_path_that_climbs_but_stays_inside_the_repository_passes():
    for kept in ("npm --prefix src-tauri/../frontend run build", "msbuild /p:Configuration=Release",
                 "cargo tauri build --config src-tauri/release.json", "x --config $env:RELEASE_TAURI_CONFIG",
                 "curl https://example.invalid/a/b", 'x "Desktop App/build outputs/../../frontend"',
                 "dotnet publish -p:Version=1 -c Release", "x --dist='a b/x/../../c'", "cmd /c x",
                 'x "out/build,debug/../../frontend"', "msbuild /out:bin/x", "npm run build:release",
                 'x --dist="Bob\'s app/x/../dist"', 'x "--dist=src/../dist"', 'msbuild "/p:Dist=src/dist"',
                 "x --url=https://example.invalid/a/b"):
        assert oneshot_pr.outside(kept) == "", kept
    for far in ("x ../wt", "x a/../../wt", "x --dist=..\\wt", "x C:/wt", "x \\wt\\dist", "x \\\\host\\s", "x ~/wt",
                "x /home/wt", """x --config '{"build":{"frontendDist":"C:/wt/dist"}}'""", "x C:..\\shared\\dist",
                "x --dist=D:dist", 'x "Desktop App/../../wt"', 'x "/Desktop App/wt"', 'x "\\Desktop App\\wt"',
                "x /out:C:\\wt", "x -o:C:\\wt", "x /out:\\wt", "x /out:../wt", "x a,../wt", "x --dist=/wt",
                'x --dist="/wt"', "x /out:/wt", 'x --dist="Bob\'s/../../wt"', "x --dist='a \"b'=/wt",
                'x "--dist=/wt"', "x '--dist=../wt'", 'x "/out:\\wt"', 'x "--dist=a b/../../wt"',
                'msbuild "/p:Dist=\\wt"', 'msbuild "/p:Dist=../wt"', "x '--a=--b=/wt'"):
        assert oneshot_pr.outside(far), far


def test_a_checkout_whose_removal_fails_once_is_removed_on_the_second_try(pc, monkeypatch):
    tree, _, _, built = pc
    tabled(tree, DESKTOP)
    real, tries = release.discard, []

    def held(repo, attempt):   # Windows holds a file for a moment the first time
        tries.append(attempt)
        if len(tries) == 1:
            raise PermissionError("in use")
        return real(repo, attempt)

    monkeypatch.setattr(release, "discard", held)
    assert oneshot_pr.release_checked(tree, spec(), git(tree, "rev-parse", "HEAD"), threading.Event(), {}, {}) == ""
    assert len(tries) == 2 and tries[0] == tries[1] and not list((release.WORK / tree.name).glob("*"))


def test_with_an_android_app_a_change_after_the_approved_build_never_reaches_the_installer(pc, monkeypatch):
    # The Android app wires its version as it does alone; the installer is the approved commit's all the same.
    tree, _, github, built = pc
    mixed(tree, monkeypatch)
    write(tree, "app/build.gradle.kts", 'android {}\ndependencies { implementation("x:y:1") }\n')
    write(tree, "shared.py", "a change R3 never saw\n")
    tabled(tree, DESKTOP, ANDROID)
    with patch.object(pr_text, "writing", lambda *a: pytest.fail(NOT_YET)):
        check(tree, answer(report()))()
    approved = o()["desktop_build"]["head"]
    assert built[1][0] == approved and o()["installer"]["url"] and o()["installer"]["built"] == approved
    assert "built from the commit R3 approved" in oneshot_pr.readme_text(spec())


def test_tauri_is_read_off_the_pc_apps_own_stack_parts():
    one = {"intent": {"kind": "desktop"}, "stack": [{"part": "ui", "choice": "Tauri 2 (web view)"},
                                                    {"part": "build", "choice": "npm run build"}]}
    assert oneshot_pr.tauri(one)
    several = {"intent": {"kind": "android", "kinds": ["android", "desktop"]},
               "stack": [{"part": "android.ui", "choice": "Tauri 2 mobile"}, {"part": "desktop.ui", "choice": "WinUI 3"}]}
    assert not oneshot_pr.tauri(several)
    several["stack"][1]["choice"] = "TAURI 2"
    assert oneshot_pr.tauri(several)


def test_a_version_file_the_readme_turn_adds_is_s8s_own(pc):
    tree, _, _, built = pc
    write(tree, "app-version.json", '{"version": "0.1.0"}')
    tabled(tree, DESKTOP.replace('"version.json"', '"app-version.json"'))
    with patch.object(pr_text, "writing", lambda *a: pytest.fail(NOT_YET)):
        check(tree, answer(report()))()
    assert o()["final_head"] and len(built) == 1 and o()["installer"]["url"]


def test_a_draft_published_between_two_uploads_gets_no_more_of_them(pc, monkeypatch):
    tree, _, github, _ = pc
    mixed(tree, monkeypatch)
    tabled(tree, DESKTOP, ANDROID)
    real = github_release.upload

    def published(repo, rid, file, name):   # the owner publishes the draft right after the APK lands
        made = real(repo, rid, file, name)
        github.rels[0]["draft"] = False
        return made

    monkeypatch.setattr(github_release, "upload", published)
    with patch.object(pr_text, "writing", lambda *a: pytest.fail(NOT_YET)):
        check(tree, answer(report()))()
    assert o()["apk"]["url"] and "더는 초안 프리릴리스가 아니다" in o()["installer"]["error"]
    assert [a["name"] for a in github.rels[0]["assets"]] == [o()["apk"]["name"]] and not staged()
    assert "설치 파일 — 올리지 못했다: 태그" in o()["stopped"]["detail"]


def test_a_published_release_of_the_tag_is_left_untouched(pc):
    tree, _, github, _ = pc
    tabled(tree, DESKTOP)
    github.add(f"oneshot-{git(tree, 'rev-parse', 'HEAD')[:12]}", assets={"kept.txt": b"published"})   # not a draft
    with patch.object(pr_text, "writing", lambda *a: pytest.fail(NOT_YET)):
        check(tree, answer(report()))()
    assert "초안 프리릴리스가 아니다" in o()["installer"]["error"] and "url" not in o()["installer"]
    assert not [c for c in github.calls if c[0] != "GET"] and [a["name"] for a in github.rels[0]["assets"]] == ["kept.txt"]
    assert not staged()


def test_an_asset_already_uploaded_with_these_bytes_is_not_sent_again(pc):
    # A restart after GitHub took the upload and before its record was saved: the next S8 finds it there.
    tree, _, github, _ = pc
    tabled(tree, DESKTOP)
    with patch.object(pr_text, "writing", lambda *a: pytest.fail(NOT_YET)):
        check(tree, answer(report()))()
    [asset] = github.rels[0]["assets"]
    (again := tree.parent / "again.exe").write_bytes(github.blobs[asset["id"]])   # a same-head build's same bytes
    copy = oneshot_pr.froze(spec(), o()["final_head"], {}, "installer", again)
    oneshot_spec.saved("budget", oneshot={"installer": None, "installer_frozen": {**copy, "version": "0.1.1"}})
    calls = len(github.calls)
    oneshot_pr.asked("budget")
    assert o()["installer"]["url"] and o()["installer"]["sha256"] == copy["sha256"]
    assert not [c for c in github.calls[calls:] if c[0] != "GET"] and github.rels[0]["assets"] == [asset]


@pytest.mark.parametrize("spelled, value", [
    ('{"key": "s3cr3t-value-0123456789"}'.encode(), "s3cr3t-value-0123456789"),
    # The widest spelling the scan matches: each `i` an `I` and a dot above, both JSON escapes, in UTF-16.
    (('{"key": "' + "\\u0049\\u0307" * 20 + '"}').encode("utf-16-le"), "i" * 20)])
def test_a_secret_inside_the_packed_installer_is_found_across_chunks(tmp_path, monkeypatch, spelled, value):
    installer = nsis(tmp_path, files=(("helper.exe", b"MZ helper " + rd.MARKER + rd.NSIS), ("cfg.json", spelled),
                                      ("r.exe", b"MZ app")))
    assert spelled not in installer.read_bytes()   # packed: the raw file shows none of it
    monkeypatch.setattr(rd, "CHUNK", 7)   # every value split across chunks
    said = "비밀 값 [N1] 이(가) 올릴 파일에 들어 있다 — 올리지 않는다"
    assert oneshot_needs.asset(installer, {}, {value: "N1"}, packed=True) == said
    assert oneshot_needs.asset(installer, {}, {"absent-value-987": "N2"}, packed=True) == ""
    with zipfile.ZipFile(installer, "a") as z:   # a zip after the installer: its stream is read all the same
        z.writestr("note.txt", "nothing")
    assert zipfile.is_zipfile(installer) and oneshot_needs.asset(installer, {}, {value: "N1"}, packed=True) == said
    (tmp_path / "cut").mkdir()
    cut = nsis(tmp_path / "cut", tail=struct.pack("<I", 100) + b"short")
    assert "올릴 파일을 읽지 못해" in oneshot_needs.asset(cut, {}, {value: "N1"}, packed=True)   # a stream cut short
    assert "올릴 파일을 읽지 못해" in oneshot_needs.asset(cut, {}, {}, packed=True)   # with no secret to look for too


def test_a_copy_the_freezes_scan_flags_is_not_kept(pc, tmp_path):
    secret = "s3cr3t-value-0123456789"
    installer = nsis(tmp_path, files=(("helper.exe", b"MZ helper " + rd.MARKER + rd.NSIS),
                                      ("cfg.json", secret.encode()), ("r.exe", b"MZ app")))
    copy = oneshot_pr.froze(spec(), "h" * 40, {secret: "N1"}, "installer", installer)
    assert copy["scan"].startswith("비밀 값 [N1]") and "path" not in copy
    assert not list(oneshot_pr.staging(spec(), "installer").rglob("*.exe"))
    oneshot_spec.saved("budget", oneshot={"final_head": "h" * 40, "installer_frozen": copy})
    assert oneshot_pr._released(spec(), "installer", lambda: pytest.fail("nothing goes up"), "t")["error"] == copy["scan"]
    # The same bytes went up before, under values that did not flag them: refused now all the same.
    oneshot_spec.saved("budget", oneshot={"installer": {"head": "h" * 40, "sha256": copy["sha256"], "url": "u"}})
    assert oneshot_pr._released(spec(), "installer", lambda: pytest.fail("nothing goes up"), "t")["error"] == copy["scan"]


def test_the_release_build_runs_without_the_owners_secrets(pc, monkeypatch):
    tree, _, _, built = pc
    tabled(tree, DESKTOP)
    monkeypatch.setattr(oneshot_needs, "secret_needs", lambda spec: [("N1", {"env": "budget_key"})])
    monkeypatch.setenv("RENAMED_COPY", "x-s3cr3t-value-0123456789-x")   # the value under another name
    assert oneshot_pr.release_checked(tree, spec(), git(tree, "rev-parse", "HEAD"), threading.Event(), {},
                                      {"s3cr3t-value-0123456789": "[N1]"}) == ""
    assert {"BUDGET_KEY", "RENAMED_COPY"} <= built[-1][5]


def test_release_build_leaves_out_the_variables_it_is_told_to(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(release, "environment", lambda kit: {"BUDGET_KEY": "k", "PATH": "p"})
    monkeypatch.setattr(release.specs, "gate", lambda cmd, path, halt, env, **k: seen.update(env) or (0, "", None))
    (tmp_path / "src").mkdir()
    with pytest.raises(release.BuildFailed, match="만들지 않았다"):   # this stand-in builds nothing
        release.build(tmp_path / "src", {"build": "b", "artifact": "a.apk"}, "0.1.1", {}, threading.Event(),
                      drop=frozenset({"BUDGET_KEY"}))
    assert "BUDGET_KEY" not in seen and seen["PATH"] == "p" and seen["RELEASE_VERSION"] == "0.1.1"


def test_a_refusal_before_the_upload_keeps_no_frozen_copy(pc, monkeypatch):
    tree, _, github, _ = pc
    tabled(tree, DESKTOP)
    then = check(tree, answer(report()))
    assert staged()   # frozen by the gate
    monkeypatch.setattr(oneshot_needs, "publishable", lambda *a: "비밀 값 [N1] 이(가) 커밋 에 들어 있다")
    then()
    assert not staged() and not github.calls and o()["stopped"]["detail"].startswith("비밀 값 [N1]")
    # A stop that kept the hand-over from running at all: the resumed README turn removes what the gate froze.
    check(tree, answer(report()))
    assert staged()
    oneshot_pr.restart(spec())
    assert not staged()
