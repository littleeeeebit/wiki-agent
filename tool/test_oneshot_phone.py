"""oneshot step 7f1: the round on the owner's phone before R3 (`docs/plans/oneshot/7-android.md`, "Before R3 —
one round on the owner's phone").

A fake handset stands in for `adb` behind the run's own USB server: it lists phones by serial, holds the
packages installed on it, and answers the commands the round sends. `android_phone.serve` is replaced by a
context that only writes the server's record, so `bind` and the serial each device command names are real.
"""

import json
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException

import android_owned
import android_phone
from main import oneshot_build, oneshot_critique as critique, oneshot_device as device, oneshot_phone as phone_round, \
    oneshot_spec
from test_oneshot_device import (DEVICE, PKG, Phone, android_build, clean_app, phone)  # noqa: F401 — fixtures
from test_oneshot_front_build import git, o, spec, staged  # noqa: F401 — `staged` is a fixture
from test_oneshot_spec import world  # noqa: F401 — the temporary worktree's fixture

SERIAL, OTHER = "R5CR10ABCDE", "R3CN20XYZ"
TESTS = DEVICE["tests"]["runner"].split("/")[0]


class Handset(Phone):
    """The phones the run's server lists, and what is installed on the one each command names."""

    def __init__(self, screens):
        super().__init__(screens)
        self.phones = {SERIAL: ("device", "SM_G991N"), OTHER: ("device", "Pixel_8")}
        # What is installed: each package with the SHA-256 of the APK it was installed from.
        self.present, self.addressed, self.locked, self.gone, self.on_call = {}, [], False, False, None
        self.ids, self.root = {DEVICE["apk"]: PKG, DEVICE["tests"]["apk"]: TESTS}, "/data/app"

    def call(self, folder, args, *, device=True, timeout=0, binary=False, stream=False):
        if device:
            serial = android_owned.load(folder)["serial"]
            assert serial, f"a device command before a phone was bound: {args}"
            self.addressed.append(serial)
            if self.gone:
                raise ValueError("adb: device 'R5CR10ABCDE' not found")
        if self.on_call:
            self.on_call(args)
        if args[:2] == ["devices", "-l"]:
            return "List of devices attached\n" + "".join(
                f"{s}\t{state} usb:1-1 product:x model:{model} device:y transport_id:1\n"
                for s, (state, model) in self.phones.items())
        if args[:4] == ["shell", "pm", "list", "packages"]:
            return "".join(f"package:{p}\n" for p in sorted(self.present) if p.startswith(args[4]))
        if args[0] == "uninstall":
            self.present.pop(args[1], None)
            return "Success\n"
        if args[0] == "install":
            package = next(p for apk, p in self.ids.items() if args[-1].replace("\\", "/").endswith(apk))
            # As Android's package manager: it replaces an existing installation unless given `-R`.
            if package in self.present and "-R" in args:
                raise ValueError("Failure [INSTALL_FAILED_ALREADY_EXISTS]")
            self.present[package] = android_owned.sha256(Path(args[-1]))
            return "Success\n"
        if args[:3] == ["shell", "pm", "path"]:
            return f"package:{self.root}/~~a1==/{args[3]}-b2==/base.apk\n" if args[3] in self.present else ""
        if args[:2] == ["shell", "sha256sum"]:
            package = re.search(r"/~~a1==/(.+)-b2==/base\.apk$", args[2]).group(1)
            return f"{self.present[package]}  {args[2]}\n"
        if args[:3] == ["shell", "wm", "size"]:
            return "Physical size: 1080x2400\n"
        if args[:3] == ["shell", "wm", "density"]:
            return "Physical density: 420\n"
        if args[:3] == ["shell", "dumpsys", "window"]:
            return f"  mDreamingLockscreen={str(self.locked).lower()}\n"
        if args[:3] == ["shell", "dumpsys", "gfxinfo"]:
            return ("Total frames rendered: 120\nJanky frames: 6 (5.00%)\n50th percentile: 7ms\n"
                    "90th percentile: 14ms\n99th percentile: 32ms\n")
        if args[:3] == ["shell", "dumpsys", "meminfo"]:
            return "        TOTAL PSS:    85432            TOTAL RSS:   190000\n"
        return super().call(folder, args, device=device, timeout=timeout, binary=binary, stream=stream)


@pytest.fixture
def handset(android_build, phone, monkeypatch):  # noqa: F811 — the imported fixtures, requested by name
    h = Handset(clean_app())
    h.tree = android_build

    @contextmanager
    def serve(folder, adb):
        android_owned.save(folder, {"state": "owning", "kind": "phone", "serial": None})
        yield

    monkeypatch.setattr(android_owned, "call", h.call)
    monkeypatch.setattr(android_phone, "serve", serve)
    monkeypatch.setattr(phone_round, "adb_tool", lambda: {"path": "adb", "sha256": ""})
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=android_build, capture_output=True, text=True).stdout.strip()
    oneshot_spec.saved("budget", oneshot={"stage": "phone", "working": None, "stopped": None, "device": DEVICE,
                                          "apks": device.digests(android_build, DEVICE), "front_built_head": head,
                                          "critiques": [{"look": 1, "clean": True}, {"look": 1, "clean": True}],
                                          "phone": {"status": "waiting"}})
    return h


def played(h, serial=SERIAL, keep=False):
    """A round on `serial`, run in this thread as the worker would."""

    oneshot_spec.saved("budget", oneshot={"working": "phone", "phone": {**o()["phone"], "serial": serial, "keep": keep}})
    phone_round.phone_round("budget")
    return o()


def test_a_clean_round_on_the_picked_phone_waits_before_r3_and_leaves_nothing_on_it(handset):
    after = played(handset)
    assert after["stage"] == "r3" and after["stopped"]["reason"] == "kind" and "SM G991N" in after["stopped"]["detail"]
    result = after["phone"]
    assert result["status"] == "passed" and result["head"] == after["front_built_head"] and result["apks"] == after["apks"]
    assert result["timing"]["janky_percent"] == 5.0 and result["timing"]["p90_ms"] == 14 and result["memory_kb"] == 85432
    assert (phone_round.base("budget") / "shots" / "p1" / "S1-phone-full.png").is_file()
    # Only the picked serial was ever addressed, and what the run installed is gone again.
    assert set(handset.addressed) == {SERIAL} and handset.present == {}
    assert after["phone_owned"][-1] | {"ts": 0} == {"serial": SERIAL, "model": "SM G991N", "packages": [PKG, TESTS],
                                                    "digests": {PKG: after["apks"][DEVICE["apk"]],
                                                                TESTS: after["apks"][DEVICE["tests"]["apk"]]},
                                                    "installed": [PKG, TESTS], "state": "cleaned", "left": [],
                                                    "foreign": [], "unsure": [], "ts": 0}
    assert "phone_owned" not in phone_round.extra(spec())


def test_an_app_on_adopted_storage_is_checked_and_cleaned(handset):
    handset.root = "/mnt/expand/01234567-89ab-cdef-0123-456789abcdef/app"
    after = played(handset)
    assert after["phone"]["status"] == "passed" and handset.present == {}
    assert after["phone_owned"][-1]["state"] == "cleaned"


@pytest.mark.parametrize("root", ["/data/local/tmp", "/data/app/../local/tmp", "/data/app/x;reboot"])
def test_an_apk_path_outside_where_apps_are_kept_is_never_sent_to_the_phones_shell(handset, root):
    handset.root = root
    after = played(handset)
    assert after["phone"]["status"] == "waiting" and "다르다" in after["phone"]["detail"]
    assert not any(a[:2] == ["shell", "sha256sum"] for a in handset.sent)
    # The digest it would be removed by cannot be read: the cleanup stays pending, shown on the run.
    assert after["phone_owned"][-1]["state"] == "pending" and PKG in handset.present


def test_a_passing_app_the_owner_keeps_stays_installed_and_its_record_is_released(handset):
    after = played(handset, keep=True)
    assert after["phone"]["status"] == "passed" and set(handset.present) == {PKG, TESTS}
    assert after["phone_owned"][-1]["state"] == "released"


def test_a_high_finding_on_the_phone_goes_back_through_the_emulator_loop(handset, monkeypatch):
    sent = []
    monkeypatch.setattr(oneshot_build, "send", lambda *a: sent.append(a))
    monkeypatch.setattr(critique, "front_turn", lambda s: json.dumps(s["oneshot"]["fix"], ensure_ascii=False))
    handset.events = f"11-16 12:00:00.000 I am_crash: [123,0,{PKG},1,java.lang.IllegalStateException]\n"
    after = played(handset)
    assert after["stage"] == "build-front" and after["working"] == "build-front" and after["phone"]["status"] == "failed"
    # The critic's stop rule counts afresh, so the fix reaches the phone only after two clean emulator rounds.
    assert after["critique_base"] == 2 and "owner's phone (SM G991N)" in after["fix"]["who"]
    assert "앱이 죽거나 응답하지 않았다" in sent[-1][2] and handset.present == {}


def test_an_app_already_on_the_phone_is_never_installed_over_nor_removed(handset):
    handset.present = {PKG: "f" * 64}
    after = played(handset)
    assert after["stage"] == "phone" and after["phone"]["status"] == "waiting" and "이미 있다" in after["phone"]["detail"]
    assert handset.present == {PKG: "f" * 64} and not after.get("phone_owned")
    assert not any(a[0] == "install" for a in handset.sent)


@pytest.mark.parametrize("serial, state", [("NOPE123", None), (SERIAL, "unauthorized")])
def test_a_phone_not_listed_or_not_allowed_is_never_addressed(handset, serial, state):
    if state:
        handset.phones[SERIAL] = (state, "")
    after = played(handset, serial=serial)
    assert after["phone"]["status"] == "waiting" and "연결돼 있지 않거나" in after["phone"]["detail"]
    assert handset.addressed == []   # not even the only other phone connected


def test_another_connected_phone_is_never_addressed(handset):
    played(handset, serial=OTHER)
    assert set(handset.addressed) == {OTHER}


def test_a_locked_phone_is_asked_to_be_unlocked_and_nothing_is_installed(handset):
    handset.locked = True
    after = played(handset)
    assert "잠겨 있다" in after["phone"]["detail"] and handset.present == {}


def test_a_phone_pulled_out_mid_round_leaves_a_pending_cleanup_finished_when_it_returns(handset):
    def unplug(args):
        if args[0] == "install" and "androidTest" in args[-1]:
            handset.gone = True

    handset.on_call = unplug
    after = played(handset)
    assert after["stage"] == "phone" and after["phone"]["status"] == "waiting" and "끝나지 못했다" in after["phone"]["detail"]
    record = after["phone_owned"][-1]
    assert record["state"] == "pending" and set(record["left"]) == {PKG, TESTS}
    assert phone_round.extra(spec())["phone_owned"] == [record]   # shown on the run until done
    handset.gone, handset.on_call = False, None
    listed = phone_round.devices("budget")   # the same serial reconnects: the cleanup finishes on the way
    assert {d["serial"] for d in listed["devices"]} == {SERIAL, OTHER} and listed["owned"] == []
    assert handset.present == {} and o()["phone_owned"][-1]["state"] == "cleaned"


def test_a_round_cut_by_a_restart_is_cleaned_on_the_next_listing(handset):
    mine = o()["apks"][DEVICE["apk"]]
    phone_round.recorded("budget", serial=SERIAL, model="", packages=[PKG, TESTS], digests={PKG: mine, TESTS: "a" * 64},
                         installed=[PKG, TESTS], state="owning", left=[])
    handset.present = {PKG: mine, TESTS: "b" * 64}   # the tests' id now holds another installer's APK
    phone_round.devices("budget")
    assert handset.present == {TESTS: "b" * 64}
    assert {"state": "cleaned", "left": [], "foreign": [TESTS]}.items() <= o()["phone_owned"][-1].items()


def test_the_same_apk_installed_by_the_owner_after_a_crash_is_never_removed(handset):
    """The server died after the record was saved and before an install was confirmed: the same bytes on the
    phone prove nothing about who installed them, so they stay, shown on the run until the owner removes them."""

    mine = o()["apks"][DEVICE["apk"]]
    phone_round.recorded("budget", serial=SERIAL, model="", packages=[PKG], digests={PKG: mine}, installed=[],
                         state="owning", left=[])
    handset.present = {PKG: mine}
    assert [r["state"] for r in phone_round.devices("budget")["owned"]] == ["unsure"]
    assert handset.present == {PKG: mine} and phone_round.extra(spec())["phone_owned"][0]["unsure"] == [PKG]
    handset.present = {}   # the owner removed it
    assert phone_round.devices("budget")["owned"] == [] and o()["phone_owned"][-1]["state"] == "cleaned"


def test_an_installation_another_installer_made_after_the_check_is_never_replaced_nor_removed(handset):
    """The id taken between the check and the install: `-R` fails the install, as Android replaces by default,
    and the cleanup removes only a package this run is known to have installed."""

    def raced(args):
        if args[0] == "install" and PKG not in handset.present:
            handset.present[PKG] = "c" * 64
    handset.on_call = raced
    after = played(handset)
    assert after["stage"] == "phone" and "끝나지 못했다" in after["phone"]["detail"]
    assert handset.present == {PKG: "c" * 64}
    assert {"state": "cleaned", "foreign": [PKG], "installed": []}.items() <= after["phone_owned"][-1].items()
    assert all(a[:3] == ["install", "-R", "-t"] for a in handset.sent if a[0] == "install")


def test_a_cleanup_left_pending_by_a_passing_round_is_finished_at_r3(handset):
    def unplug_at_memory(args):
        if args[:3] == ["shell", "dumpsys", "meminfo"]:
            handset.gone = True
    handset.on_call = unplug_at_memory
    after = played(handset)
    assert after["stage"] == "r3" and after["phone_owned"][-1]["state"] == "pending"
    assert phone_round.extra(spec())["phone_owned"]   # the notice stands at R3
    handset.gone, handset.on_call = False, None
    assert phone_round.devices("budget")["owned"] == []
    assert handset.present == {} and o()["phone_owned"][-1]["state"] == "cleaned"


def untracked(h):
    """The APKs as a real build leaves them: outputs Git ignores, which move neither HEAD nor the tree's state."""

    git(h.tree, "rm", "--cached", "-q", "--", *[apk for apk, _ in device.apks(DEVICE)])
    ignored = h.tree / ".gitignore"
    ignored.write_text((ignored.read_text(encoding="utf-8") if ignored.exists() else "") + "\napp/\n", encoding="utf-8")
    git(h.tree, "add", ".gitignore")
    git(h.tree, "commit", "-q", "-m", "Keep the APK outputs untracked")
    oneshot_spec.saved("budget", oneshot={"front_built_head": git(h.tree, "rev-parse", "HEAD")})


def test_a_missing_apk_goes_back_to_the_build(handset):
    untracked(handset)
    (handset.tree / DEVICE["apk"]).unlink()
    after = played(handset)
    assert after["stage"] == "build-front" and after["stopped"]["reason"] == "gate" and handset.present == {}


def test_an_apk_rebuilt_during_the_round_is_not_what_the_phone_installs(handset):
    untracked(handset)
    passed = o()["apks"]

    def rebuilt(args):
        if args[:3] == ["shell", "wm", "density"]:
            (handset.tree / DEVICE["apk"]).write_bytes(b"another build")
    installed = []
    handset.on_call = lambda args: rebuilt(args) or (args[0] == "install" and installed.append(
        android_owned.sha256(Path(args[-1]))))
    after = played(handset)
    assert after["phone"]["status"] == "passed" and installed[0] == passed[DEVICE["apk"]]
    assert not (phone_round.base("budget") / "apks").exists() or not any((phone_round.base("budget") / "apks").iterdir())


def test_a_stopped_round_removes_what_it_installed(handset):
    def stop_at_first_screen(args):
        if args[:3] == ["shell", "am", "start"]:
            phone_round._halts["budget"].set()

    handset.on_call = stop_at_first_screen
    after = played(handset)
    assert after["phone"]["status"] == "waiting" and after["phone"]["detail"] == "휴대폰 회차를 멈췄다"
    assert handset.present == {} and after["phone_owned"][-1]["state"] == "cleaned"


def test_an_apk_that_changed_since_the_emulators_look_goes_back_to_the_build(handset):
    (handset.tree / DEVICE["apk"]).write_text("another build", encoding="utf-8")
    after = played(handset)
    assert after["stage"] == "build-front" and after["stopped"]["reason"] == "gate" and handset.present == {}


def test_the_pick_is_checked_and_a_restart_returns_to_the_same_prompt(handset, monkeypatch):
    monkeypatch.setattr(oneshot_spec, "view", lambda s: s)   # the test spec carries no sheet to render
    with pytest.raises(HTTPException) as bad:
        phone_round.pick("budget", phone_round.Pick(serial="R5 ; rm"))
    assert bad.value.status_code == 400
    started = []
    monkeypatch.setattr(oneshot_spec, "start", lambda repo, work, **f: started.append((work, f)))
    phone_round.pick("budget", phone_round.Pick(serial=SERIAL, keep=True))
    work, fields = started[0]
    assert work == "phone" and fields["phone"]["serial"] == SERIAL and fields["phone"]["keep"] is True
    oneshot_spec.saved("budget", oneshot={"working": "phone"})
    oneshot_spec.recover()
    assert o()["stopped"]["reason"] == "restart"
    oneshot_spec.resume("budget")
    assert o()["stage"] == "phone" and not o().get("stopped") and not o().get("working")
    with pytest.raises(HTTPException):   # nothing to stop once it is not running
        phone_round.stop("budget")


def test_the_phones_are_listed_with_their_state_and_model(monkeypatch, tmp_path):
    monkeypatch.setattr(android_owned, "call", lambda folder, args, device=True, **_: (
        "List of devices attached\nR5CR10ABCDE\tdevice usb:1-1 product:o1s model:SM_G991N device:o1s transport_id:2\n"
        "ZY22\tunauthorized usb:1-2 transport_id:3\n\n"))
    assert android_phone.listed(tmp_path) == [{"serial": "R5CR10ABCDE", "state": "device", "model": "SM G991N"},
                                              {"serial": "ZY22", "state": "unauthorized", "model": ""}]
    android_owned.save(tmp_path, {"kind": "phone", "serial": None})
    android_phone.bind(tmp_path, "R5CR10ABCDE")
    assert json.loads((tmp_path / android_owned.RECORD).read_text())["serial"] == "R5CR10ABCDE"


def test_the_phone_server_opens_usb_alone_and_never_the_default_port(monkeypatch):
    monkeypatch.setenv("ANDROID_ADB_SERVER_PORT", "5037")
    env = android_owned.environment(android_phone.USB)
    assert env["ADB_USB"] == "1" and env["ADB_EMU"] == "0" and env["ADB_MDNS"] == "0"
    assert "ANDROID_ADB_SERVER_PORT" not in env   # the server's port is the record's, passed with `-P`
