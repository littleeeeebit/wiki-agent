"""Native target and approval schemas for every driver, kept apart from the collectors.

A desktop flow's signature hashes its collector module (`native_verification.py`). Schemas
live here so adding a driver never changes that digest and never revokes stored passes.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Discriminator, Field, Tag, model_validator

import native_verification
import native_webview
from native_verification import Closed, NativeSettings, NativeTarget


class Win32Target(NativeTarget):
    # Excluded from dumps, so a Win32 flow dumps exactly as before and keeps its signature.
    driver: Literal["win32"] = Field("win32", exclude=True)


class Checkpoint(Closed):
    """The collector reads `selector` itself; `no_requests` claims no backend request in this checkpoint."""
    assertion: str = Field(min_length=1)
    expected: str = Field(min_length=1)
    selector: str = Field(min_length=1)
    no_requests: bool = False


def origin(value: str) -> bool:
    """`scheme://host[:port]`: no credentials, path, query or fragment, and a numeric port if any."""
    url = urlsplit(value)
    try:
        url.port  # noqa: B018 — raises on a non-numeric or out-of-range port
    except ValueError:
        return False
    return bool(url.scheme and url.hostname) and not (url.username or url.password or url.path or url.query
                                                       or url.fragment)


class WebViewTarget(Closed):
    """A Tauri/WebView app built from the reviewed head; a tracked scenario drives it through the collector."""
    driver: Literal["webview"]
    platform: Literal["windows", "android"]
    application: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    build: str = Field(min_length=1)
    output: str = Field(min_length=1)
    origins: list[str] = Field(min_length=1)
    calls: list[str] = Field(default_factory=list)  # native-bridge origins, e.g. Tauri's http://ipc.localhost
    package: str = ""
    activity: str = ""
    build_inputs: list[str] = Field(min_length=1)
    actions: list[Checkpoint] = Field(min_length=1)

    @model_validator(mode="after")
    def platform_fields(self):
        android = self.platform == "android"
        if bool(self.package) is not android or bool(self.activity) is not android:
            raise ValueError("package and activity are required on Android and refused on Windows")
        if self.scenario not in self.build_inputs:
            raise ValueError("The WebView scenario must be a measured build input")
        output = PurePosixPath(self.output)  # one reading on every OS: no drive, no backslash
        if output.is_absolute() or ".." in output.parts or ":" in self.output or "\\" in self.output:
            raise ValueError("The WebView build output must be a path inside the checkout")
        if not all(map(origin, [*self.origins, *self.calls])):
            raise ValueError("WebView origins are scheme://host[:port] with no credentials or path")
        return self


def target_driver(value) -> str:
    if isinstance(value, native_webview.Target):
        return "approved-webview"
    if isinstance(value, dict):
        driver = value.get("driver", "win32")
        return "approved-webview" if driver == "webview" and "build" not in value else driver
    return value.driver


Target = Annotated[Annotated[Win32Target, Tag("win32")] | Annotated[WebViewTarget, Tag("webview")]
                   | Annotated[native_webview.Target, Tag("approved-webview")], Discriminator(target_driver)]


class WebViewSettings(Closed):
    """One approved WebView app: the Node that runs scenarios and the Android emulator; no binary, it is built at the head."""
    version: Literal[1]
    host: Literal["windows-webview2", "android-emulator"]
    application: str = Field(min_length=1)
    runner: str = Field(min_length=1)
    runner_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile: str = Field(min_length=1)
    # Android only: the host that installs, drives and observes the app, each bound by content.
    sdk: str = ""
    adb: str = ""
    adb_sha256: str = Field("", pattern=r"^([0-9a-f]{64})?$")
    emulator: str = ""
    emulator_sha256: str = Field("", pattern=r"^([0-9a-f]{64})?$")
    system_image: str = ""
    system_image_sha256: str = Field("", pattern=r"^([0-9a-f]{64})?$")  # over sorted relative paths and file hashes

    @model_validator(mode="after")
    def emulator_fields(self):
        android = self.host == "android-emulator"
        if any(bool(getattr(self, key)) is not android for key in ANDROID_HOST):
            raise ValueError("The Android host fields are required for the emulator and refused on Windows")
        return self


ANDROID_HOST = ("sdk", "adb", "adb_sha256", "emulator", "emulator_sha256", "system_image", "system_image_sha256")

Approval = Annotated[NativeSettings | WebViewSettings, Field(discriminator="host")]


def host(target: Win32Target | WebViewTarget | native_webview.Target) -> str:
    """The approval host a target needs."""
    if isinstance(target, native_webview.Target):
        return "webview"
    if target.driver == "win32":
        return "windows-win32"
    return "android-emulator" if target.platform == "android" else "windows-webview2"


def approval(settings: dict, target: Win32Target | WebViewTarget | native_webview.Target) -> dict:
    """The approval for `target`: settings version 2 holds one, version 3 one per application."""
    native = settings.get("native")
    if isinstance(target, native_webview.Target):
        approved = native_webview.Settings.model_validate(native)
        app = approved.applications.get(target.application)
        if not app or app.platform != target.platform:
            raise ValueError("등록된 플랫폼과 앱의 격리 실행 범위를 명시적으로 승인한다")
        return native
    for row in native if isinstance(native, list) else [native] if native else []:
        if row.get("application") == target.application and row.get("host") == host(target):
            return row
    raise ValueError(f"승인된 네이티브 테스트 범위가 없다 — {target.application} ({host(target)})")


def preflight(root, head: str, target: dict, settings: dict) -> dict:
    """Route a target to its collector's preflight; a driver without a collector fails closed."""
    if target.get("driver") == "webview" and "build" in target:
        # ponytail: refused until the WebView collectors land (docs/plans/native-webview, steps 2-3).
        raise ValueError("WebView 네이티브 수집기는 아직 없다 — 이 흐름은 지금 수집할 수 없다")
    return native_verification.preflight(root, head, target, settings)
