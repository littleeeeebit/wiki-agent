"""oneshot step 9e6b: S8 of a PC app — the README turn's `PC 앱` section, the installer the final gate builds frozen
with its HEAD and digest, and the draft prerelease carrying it (with the APK when an Android app is among the kinds),
each file reused only for the same HEAD and digest; the run then waits before its pull request's text.

See `docs/plans/oneshot/9-kinds.md` ("R3 and S8", "Steps and their stops"). Git and the gate run for real in a
temporary worktree; GitHub's Releases API is `FakeGitHub`, so nothing goes out.
"""

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

import android_owned
from main import github_release, oneshot_build, oneshot_pr, oneshot_spec, pr_text, release
from test_github_release import FakeGitHub
from test_main import no_machine_settings  # noqa: F401 — the fixture is autouse
from test_oneshot_device import DEVICE
from test_oneshot_front_build import adapter, git, write
from test_oneshot_pr import answer, check, finished, o, readme, report, spec  # noqa: F401 — `finished` is a fixture
from test_oneshot_spec import world  # noqa: F401 — `world` is a fixture

INSTALLER = "dist/budget-setup.exe"
RUNNER = "python -m pytest -p no:cacheprovider -q --junitxml={report}"   # the declared tool, by its name
GATE = RUNNER.replace("{report}", oneshot_build.REPORT)


@pytest.fixture
def pc(finished, monkeypatch):  # noqa: F811 — the imported fixture, requested by name
    """A PC app at S8: its frozen build names the installer, which the gate builds from `built.exe`."""

    tree, sent = finished
    stack = [{"part": "build", "choice": "python", "why": "주인이 골랐다", "status": "confirmed",
              "tools": [{"name": "python"}]}]   # the gate's one program, found for this stage
    oneshot_spec.saved("budget", oneshot={"intent": {"kind": "desktop"}, "desktop_build": {"installer": INSTALLER},
                                          "runner": RUNNER, "gate": GATE, "stack": stack, "toolpaths": {"stage": "pr", "paths": {
                                              "python": os.path.abspath(shutil.which("python"))}}})
    monkeypatch.setattr(oneshot_build, "toolchain", lambda spec: None)   # the gate runs as the test's own Python
    monkeypatch.setattr(oneshot_build, "built_env", lambda spec: None)
    monkeypatch.setattr(oneshot_pr, "release_checked", lambda *a: "")   # Android's own tests build it
    real = oneshot_build.judged

    def building(path, *a, **k):   # the gate builds what each kind attaches, as a real gate does
        verdict = real(path, *a, **k)
        for name, out in ((INSTALLER, "built.exe"), (DEVICE["apk"], "built.apk")):
            if not (Path(path) / name).exists() and (Path(path).parent / out).exists():
                (Path(path) / name).parent.mkdir(parents=True, exist_ok=True)
                (Path(path) / name).write_bytes((Path(path).parent / out).read_bytes())
        return verdict

    monkeypatch.setattr(oneshot_build, "judged", building)
    write(tree, INSTALLER, "an earlier build's installer")   # removed before the gate, as an earlier build's APK is
    (tree.parent / "built.exe").write_text("installer", encoding="utf-8")
    write(tree, ".gitignore", "__pycache__/\nartifacts/\ndist/\n*.apk\n")
    write(tree, ".wiki/adapter.toml", adapter(gate=GATE))
    git(tree, "add", ".gitignore", ".wiki/adapter.toml")
    git(tree, "commit", "-m", "ignore build outputs")
    head = git(tree, "rev-parse", "HEAD")
    oneshot_spec.saved("budget", oneshot={"tests_head": head, "front_built_head": head})
    monkeypatch.setattr(release, "remote", lambda repo: "github.com/me/budget")
    return tree, sent, FakeGitHub("me/budget").install(monkeypatch)


def on_prerelease(fake, head):
    [rel] = [r for r in fake.rels if r["tag_name"] == f"oneshot-{head[:12]}"]
    return rel, {a["name"]: github_release.digest(a) for a in rel["assets"]}


def test_the_readme_turn_has_a_pc_section_naming_the_installer(pc):
    text = oneshot_pr.readme_text(spec())
    assert "This is a PC app (a Windows program)." in text and f"`{INSTALLER}`" in text and "adb's" not in text
    oneshot_spec.saved("budget", oneshot={"intent": {"kind": "webapp", "kinds": ["webapp", "desktop"]}})
    text = oneshot_pr.readme_text(spec())
    assert "In the PC app's section," in text and "a Windows app" in text and "Give each its own section" in text


def test_a_pc_apps_installer_goes_alone_to_the_prerelease_and_the_run_waits_before_its_pull_request(pc):
    tree, sent, github = pc
    readme(tree)
    asked = len(sent)
    with patch.object(pr_text, "writing", lambda *a: pytest.fail("the pull request is the next step's")):
        check(tree, answer(report()))()
    head = o()["final_head"]
    rel, digests = on_prerelease(github, head)
    name = f"budget-{head[:12]}-setup.exe"
    assert rel["draft"] and rel["prerelease"] and digests == {name: android_owned.sha256(tree / INSTALLER)}
    assert o()["installer"] == {"head": head, "name": name, "sha256": digests[name], "url": rel["html_url"],
                                "tag": f"oneshot-{head[:12]}"} and "apk" not in o()
    assert (tree / INSTALLER).read_text(encoding="utf-8") == "installer"   # the gate's own, not the earlier build's
    assert not Path(o()["installer_frozen"]["path"]).exists()   # the copy it uploaded is gone
    assert o()["stopped"]["reason"] == "kind" and "풀 리퀘스트는 다음 버전에서" in o()["stopped"]["detail"]
    assert len(sent) == asked and not spec().get("writing")
    body = "\n".join(pr_text.apk_section(spec()))
    assert f"- PC 앱 설치 파일: [{name}]({rel['html_url']})" in body and "## APK" not in body
    # Held there through a restart, though the README turn's commit left the frozen build's HEAD: `[재개]` is refused.
    oneshot_spec.recover()
    assert o()["stage"] == "pr" and o()["stopped"]["reason"] == "kind" and o()["desktop_build"]
    with pytest.raises(Exception, match="다음 버전"):
        oneshot_spec.resume("budget")


def with_android(tree):
    """An Android app among the kinds: the PC app's tools then live in `desktop.build`, and the gate builds the APK."""

    stack = [{**o()["stack"][0], "part": "desktop.build"}]
    oneshot_spec.saved("budget", oneshot={"intent": {"kind": "android", "kinds": ["android", "desktop"]},
                                          "device": DEVICE, "stack": stack})
    for apk in (DEVICE["apk"], DEVICE["tests"]["apk"]):
        write(tree, apk, "apk")
    (tree.parent / "built.apk").write_text("apk", encoding="utf-8")


def test_with_an_android_app_both_files_go_to_one_prerelease_and_a_resumed_upload_sends_neither_again(pc):
    tree, sent, github = pc
    with_android(tree)
    readme(tree)
    with patch.object(pr_text, "writing", lambda *a: pytest.fail("the pull request is the next step's")):
        check(tree, answer(report()))()
    head = o()["final_head"]
    rel, digests = on_prerelease(github, head)
    assert sorted(digests) == [f"budget-{head[:12]}-debug.apk", f"budget-{head[:12]}-setup.exe"]
    assert o()["apk"]["url"] == o()["installer"]["url"] == rel["html_url"]
    assert len([c for c in github.calls if c[0] == "POST" and c[1] == "releases"]) == 1
    first, calls = (o()["apk"], o()["installer"]), len(github.calls)
    with patch.object(pr_text, "writing", lambda *a: pytest.fail("the pull request is the next step's")):
        oneshot_pr.asked("budget")   # a resumed S8: the same head and digests, so nothing goes up again
    assert (o()["apk"], o()["installer"]) == first and not [c for c in github.calls[calls:] if c[0] != "GET"]
    body = "\n".join(pr_text.apk_section(spec()))
    assert "## APK" in body and "## 설치 파일" in body


def test_an_installer_the_gate_does_not_build_or_cannot_copy_is_said_and_the_apk_still_goes_up(pc):
    tree, _, github = pc
    with_android(tree)
    (tree.parent / "built.exe").unlink()   # this gate builds no installer; the earlier build's is removed before it
    readme(tree)
    with patch.object(pr_text, "writing", lambda *a: pytest.fail("the pull request is the next step's")):
        check(tree, answer(report()))()
    assert not (tree / INSTALLER).exists() and f"`{INSTALLER}` 를 만들지 않았다" in o()["installer"]["error"]
    assert o()["apk"]["url"] and "PC 앱 설치 파일을 Release 에 올리지 못했다" in "\n".join(pr_text.apk_section(spec()))
    # A frozen copy changed before it goes up: said, nothing of it uploaded, and the copy removed.
    write(tree, INSTALLER, "installer")
    copy = oneshot_pr.froze(spec(), o()["final_head"], {}, "installer")
    Path(copy["path"]).write_bytes(b"changed after the freeze")
    oneshot_spec.saved("budget", oneshot={"installer_frozen": copy, "installer": None})
    calls = len(github.calls)
    assert "설치 파일 사본이 없거나 바뀌었다" in oneshot_pr.released(spec(), ["installer"])["installer"]["error"]
    assert not [c for c in github.calls[calls:] if c[0] != "GET"] and not Path(copy["path"]).exists()
