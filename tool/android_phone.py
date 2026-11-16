"""The owner's phone, reached through an ADB server the run owns (`docs/plans/oneshot/7-android.md`, "Before
R3 — one round on the owner's phone"; step 7f1).

It is `android_owned`'s server with its USB backend on and nothing else: no emulator scan, no mDNS, its own
port, never the default 5037, cleaned on every exit by the same record. Every device command names the
serial the owner picked (`bind`), so a phone with another serial is never addressed, even when it is the
only one connected. Windows lets one ADB server hold a USB phone: when another server holds it, this one
lists nothing, and the screen says to close that server.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

import android_owned

USB = {"ADB_USB": "1"}
ENUMERATE_SECONDS = 3   # a fresh server's USB scan finds an attached phone within a second or two


def checked(adb: dict) -> dict:
    """The approved `adb` measured again, new enough to honour the variables that close its discovery."""

    file = Path(adb["path"])
    if not file.is_absolute() or file.is_symlink() or not file.is_file() or android_owned.sha256(file) != adb["sha256"]:
        raise ValueError("The approved adb is missing or changed")
    if android_owned.adb_version(file) < android_owned.MIN_ADB:
        raise ValueError("The approved adb is too old to close its discovery")
    return adb


@contextmanager
def serve(folder: Path, adb: dict):
    """An owned ADB server that sees USB phones only; cleaned on every exit."""

    with android_owned.owning(folder, {"adb": adb}, kind="phone") as record:
        checked(adb)
        android_owned.serve(folder, record, USB)
        time.sleep(ENUMERATE_SECONDS)
        yield


def listed(folder: Path) -> list[dict]:
    """`adb devices -l`: each phone's serial, state (`device`, `unauthorized`, `offline`) and model."""

    out = []
    for line in android_owned.call(folder, ["devices", "-l"], device=False).splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            info = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
            out.append({"serial": parts[0], "state": parts[1], "model": info.get("model", "").replace("_", " ")})
    return out


def bind(folder: Path, serial: str) -> None:
    """Every device command from here on names `serial`, the phone the owner picked."""

    record = android_owned.load(folder)
    record["serial"] = serial
    android_owned.save(folder, record)
