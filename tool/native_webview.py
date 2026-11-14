"""Fixed WebView driver for owned Windows applications and disposable Android emulators."""

import hashlib
import json
import os
import subprocess
import sys
import time
import shutil
import re
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, model_validator

from common import process
from common.settings import entries
from native_process import launched


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Assertion(Closed):
    assertion: str = Field(min_length=1)
    expected: str = Field(min_length=1)


class Target(Closed):
    driver: Literal["webview"]
    platform: Literal["windows", "android"]
    application: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    build_inputs: list[str] = Field(min_length=1)
    actions: list[Assertion] = Field(min_length=1)
    package: str = ""
    activity: str = ""

    @model_validator(mode="after")
    def android_identity(self):
        if self.platform == "android" and (not self.package or not self.activity):
            raise ValueError("Android requires an explicit package and activity")
        return self


class Asset(Closed):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Approval(Closed):
    platform: Literal["windows", "android"]
    binary: Asset
    profile: str = Field(min_length=1)
    adb: Asset | None = None
    emulator: Asset | None = None
    avd: str = ""
    renderer: Literal["auto", "host", "software"] = "auto"

    @model_validator(mode="after")
    def android_renderer(self):
        if self.platform != "android" and self.renderer != "auto":
            raise ValueError("Renderer selection applies only to Android applications")
        return self


class Settings(Closed):
    version: Literal[2]
    host: Literal["webview"]
    node: Asset
    applications: dict[str, Approval] = Field(min_length=1)


def assets(settings: dict) -> list[dict]:
    approved = Settings.model_validate(settings)
    return [approved.node.model_dump(), *[asset.model_dump() for app in approved.applications.values()
                                         for asset in (app.binary, app.adb, app.emulator) if asset]]


def preflight(root: Path, head: str, target: dict, settings: dict) -> dict:
    import native_verification as native

    target, approved = Target.model_validate(target), Settings.model_validate(settings)
    if sys.platform != "win32":
        raise ValueError("WebView native verification currently requires a Windows host")
    app = approved.applications.get(target.application)
    if not app or app.platform != target.platform:
        raise ValueError("The native platform/application is not approved")
    if app.platform == "android" and (not app.adb or not app.emulator or not app.avd):
        raise ValueError("Android requires approved adb, emulator and a disposable AVD template")
    for asset in assets(settings):
        file = Path(asset["path"])
        if not file.is_absolute() or file.is_symlink() or not file.is_file() or native.digest(file) != asset["sha256"]:
            raise ValueError("Approved native asset is missing or changed: " + file.name)
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True,
                             timeout=30, **process.background_options()).stdout.decode("utf-8").split("\0")
    names = set()
    for pattern in target.build_inputs:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ValueError("Build input patterns must stay inside the checkout")
        matches = {p for p in tracked if p and fnmatchcase(p, pattern)}
        if not matches:
            raise ValueError("Build input pattern matches no tracked files: " + pattern)
        names.update(matches)
    if target.scenario not in names or not target.scenario.endswith(".mjs"):
        raise ValueError("The scenario must be a measured tracked JavaScript module")
    return native.build_identity(root, head, sorted(names))


def collector_digest() -> str:
    sources = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in
               ("native_webview.py", "native_webview.mjs", "native_process.py", "native_window.py", "native_cleanup.mjs")}
    return hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()


def system_ui_wait_button(lines, hierarchy):
    current = [line.strip() for line in lines if line.strip().startswith("mCurrentFocus=")]
    if len(current) != 1 or not re.fullmatch(
        r"mCurrentFocus=Window\{[^{}]* Application Not Responding: com\.android\.systemui\}", current[0]
    ):
        return None
    buttons = [node for node in ElementTree.fromstring(hierarchy).iter("node") if
               node.get("resource-id") == "android:id/aerr_wait" and node.get("package") == "android"
               and node.get("enabled") == "true" and node.get("clickable") == "true"]
    if len(buttons) != 1:
        raise ValueError("System UI startup dialog has no unique enabled native Wait control")
    bounds = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", buttons[0].get("bounds", ""))
    if not bounds:
        raise ValueError("System UI Wait control has invalid bounds")
    left, top, right, bottom = map(int, bounds.groups())
    if not (0 <= left < right <= 16384 and 0 <= top < bottom <= 16384):
        raise ValueError("System UI Wait control has invalid bounds")
    return {"control": "android:id/aerr_wait", "x": (left + right) // 2, "y": (top + bottom) // 2}


def validate_receipt(data, root, artifact, target, settings, revisions, *, redact_text=None):
    import native_verification as native

    build = preflight(root, data["head"], target, settings)
    observed = data.get("native")
    approval = settings["applications"][target["application"]]
    fields = {"application", "host", "driver", "build_head", "build_inputs", "binary_sha256", "profile",
              "revisions", "desktop", "sessions", "actions", "artifacts"}
    if not isinstance(observed, dict) or set(observed) != fields:
        raise ValueError("Incomplete native WebView receipt")
    for key, value in {"application": target["application"], "host": target["platform"], "driver": "owned-webview-v1",
                       "build_head": data["head"], "build_inputs": build, "binary_sha256": approval["binary"]["sha256"],
                       "profile": approval["profile"], "revisions": revisions}.items():
        if observed[key] != value:
            raise ValueError("Native WebView identity changed: " + key)
    cleanup = json.loads((artifact / "cleanup.json").read_text(encoding="utf-8"))
    if cleanup != {"state": "cleaned", "desktop": observed["desktop"]} or not observed["desktop"]:
        raise ValueError("Native process tree cleanup is not proven")
    revision = json.loads((artifact / "build-observation.json").read_text(encoding="utf-8"))
    directory = str(artifact / "data") if target["platform"] == "windows" else "/data/user/0/" + target["package"]
    if revision != {"core": data["head"], "screen": data["head"], "profile": hashlib.sha256(directory.encode()).hexdigest()}:
        raise ValueError("Observed native revision or data profile differs from the reviewed scope")
    journal = json.loads((artifact / "sessions.json").read_text(encoding="utf-8"))
    if observed["sessions"] != journal or not journal:
        raise ValueError("Native ownership observations are missing")
    for session in journal:
        if session.get("build_head") != data["head"] or session.get("binary_sha256") != approval["binary"]["sha256"] \
                or any(type(session.get(key)) is not int or session[key] <= 0 for key in ("pid", "listener_pid")) \
                or not session.get("runtime"):
            raise ValueError("Native runtime/build identity is missing")
        if target["platform"] == "windows" and (not session.get("hwnd") or not session.get("geometry") or not session.get("dpi")):
            raise ValueError("Native Windows window observation is missing")
        if target["platform"] == "android" and (session.get("package") != target["package"]
                                                or not session.get("emulator_pid") or not session.get("activity")):
            raise ValueError("Installed Android application observation is missing")
    raw = json.loads((artifact / "observations.json").read_text(encoding="utf-8"))
    if len(raw) != len(target["actions"]) or len(observed["actions"]) != len(raw):
        raise ValueError("Registered native observations are incomplete")
    rows = {row["id"]: row for row in data["observations"]}
    for expected, action, measured in zip(target["actions"], observed["actions"], raw, strict=True):
        if set(action) != {"assertion", "expected", "actual"} or any(action.get(k) != v for k, v in expected.items()) \
                or measured != {**expected, "actual": measured.get("actual")}:
            raise ValueError("Unregistered native assertion")
        actual = redact_text(measured["actual"]) if redact_text else measured["actual"]
        row = rows[expected["assertion"]]
        if action["actual"] != actual or row["actual"] != actual or row["pass"] is not (measured["actual"] == expected["expected"]):
            raise ValueError("Native verdict differs from observed result")
    required = {"sessions.json", "observations.json", "build-observation.json"}
    if target["platform"] == "android":
        component = target["package"] + "/" + (target["package"] + target["activity"] if target["activity"].startswith(".") else target["activity"])
        focus = json.loads((artifact / "android-focus.json").read_text(encoding="utf-8"))
        if not any("mCurrentFocus" in line and component in line for line in focus.get("lines", [])):
            raise ValueError("Native Android activity focus is missing")
        required.add("android-focus.json")
    if not any(a.get("path", "").endswith(".png") for a in observed["artifacts"]):
        raise ValueError("Native screenshot evidence is missing")
    for row in observed["artifacts"]:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ValueError("Invalid native artifact")
        file = artifact / row["path"]
        if Path(row["path"]).is_absolute() or artifact.resolve() not in file.resolve().parents or file.is_symlink() \
                or native.digest(file) != row["sha256"]:
            raise ValueError("Native artifact changed or escaped its owned directory")
        required.discard(row["path"])
    if required:
        raise ValueError("Native raw observations are not hash bound")
    return observed


def remove_profile(artifact):
    profile = artifact / "profile"
    deadline = time.monotonic() + 5
    while profile.exists():
        if profile.is_symlink() or profile.resolve().parent != artifact.resolve():
            raise ValueError("Native profile cleanup escaped its attempt directory")
        try:
            shutil.rmtree(profile)
        except OSError as error:
            # WebView shutdown can release handles or write final state after enumeration.
            if getattr(error, "winerror", None) not in (5, 32, 33, 145) or time.monotonic() >= deadline:
                raise ValueError(f"Native profile cleanup failed: {error}") from error
            time.sleep(0.1)


def run(request):
    import native_verification as native

    root, artifact = Path(request["root"]), Path(request["artifact_dir"])
    preflight(root, request["head"], request["target"], request["settings"])
    with native.owned_desktop() as desktop, (artifact / "driver.log").open("wb") as log:
        request = {**request, "desktop": desktop, "python": sys.executable}
        file = artifact / "webview-request.json"
        file.write_text(json.dumps(request), encoding="utf-8")
        argv = [request["settings"]["node"]["path"], str(Path(__file__).with_suffix(".mjs")), str(file)]
        env = {key: value for key, value in {**os.environ, **entries(root / ".env")}.items()
               if not key.upper().startswith("NODE_")}
        failure = None
        try:
            with launched(argv, root, env, desktop, log) as child:
                deadline = time.monotonic() + 900
                while child.poll() is None:
                    if time.monotonic() >= deadline:
                        raise ValueError("Native WebView scenario exceeded its deadline")
                    time.sleep(0.1)
                code = child.wait()
            result = json.loads((artifact / "result.json").read_text(encoding="utf-8"))
            if code or "blocked" in result:
                raise ValueError(result.get("blocked", {}).get("reason", "Native scenario failed; see owned driver log"))
        except BaseException as error:
            failure = error
            raise
        finally:
            try:
                remove_profile(artifact)
            except Exception as cleanup:
                if failure:
                    raise ValueError(f"{failure}; {cleanup}") from cleanup
                raise
        (artifact / "cleanup.json").write_text(json.dumps({"state": "cleaned", "desktop": desktop}), encoding="utf-8")
        result["native"]["build_inputs"] = preflight(root, request["head"], request["target"], request["settings"])
        return result


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "android-wait":
        raise ValueError("Expected android-wait with observed focus and hierarchy files")
    focus = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    print(json.dumps(system_ui_wait_button(focus["lines"], Path(sys.argv[3]).read_text(encoding="utf-8"))))
