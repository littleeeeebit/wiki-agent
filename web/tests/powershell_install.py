"""Real Windows shells and the install control, with a private winget fixture.

Use --native to build and launch an isolated Tauri window over the same fixture.
No provider, user configuration or installed software is changed.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright
import uvicorn

from browser_fixture import served
from mobile_browser import ROOT, fixture
from common import process, shell
from main import mobile, powershell, runtime


def application(scratch: Path) -> FastAPI:
    app = FastAPI()

    @app.get("/api/connect")
    def listing():
        return {**fixture("connect", "GET"), "powershell": powershell.status()}

    @app.post("/api/fixture/reset")
    def reset():
        for name in ("pwsh.cmd", "winget.cmd"):
            (scratch / name).unlink(missing_ok=True)
        shell.detect(refresh=True)
        return {"ok": True}

    app.include_router(powershell.router)
    app.include_router(mobile.router)

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        value = fixture(path, request.method)
        if path == "worktrees":
            value["rows"][0]["path"] = str(scratch)
        return value

    app.mount("/", StaticFiles(directory=ROOT / "web/dist", html=True))
    return app


def server():
    """The isolated native sidecar's entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--exit-with-stdin", action="store_true")
    args = parser.parse_args()
    server = uvicorn.Server(uvicorn.Config(application(Path(os.environ["WIKI_SHELL_FIXTURE"])),
                                          host="127.0.0.1", port=args.port, log_level="error"))

    def watch():
        sys.stdin.buffer.read()
        runtime.stopping.set()
        powershell.close_install()
        server.should_exit = True

    threading.Thread(target=watch, daemon=True).start()
    server.run()


def check(page, scratch: Path, native: bool):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    for fonts in ("https://fonts.googleapis.com/**", "https://fonts.gstatic.com/**"):
        page.route(fonts, lambda route: route.abort())
    page.reload(wait_until="domcontentloaded")
    if native:
        page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(has_text="fixture-task").click()
        page.get_by_role("tab", name="터미널", exact=True).click()
        terminal = page.get_by_role("region", name="터미널", exact=True)
        entry = terminal.locator("textarea.xterm-helper-textarea")
        entry.wait_for()
        expect(terminal).to_contain_text("PS ", timeout=30000)
        entry.focus()
        page.keyboard.insert_text("Write-Output ('FIXTURE_PS_' + $PSVersionTable.PSVersion.Major)")
        page.keyboard.press("Enter")
        expect(terminal).to_contain_text("FIXTURE_PS_5", timeout=15000)
    page.get_by_role("button", name="프로젝트 · 연결", exact=True).click()
    region = page.get_by_role("region", name="PowerShell", exact=True)
    expect(region).to_contain_text("PowerShell 5.1.", timeout=30000)
    button = region.get_by_role("button", name="PowerShell 7 설치", exact=True)
    button.click()
    expect(region.get_by_role("log")).to_contain_text("FIXTURE winget started", timeout=15000)
    expect(region.get_by_role("button")).to_be_disabled()
    expect(region).to_contain_text("PowerShell 7.", timeout=30000)
    expect(region.get_by_role("log")).to_contain_text("FIXTURE installation complete")
    expect(region.get_by_role("button")).to_have_count(0)
    assert (scratch / "args.txt").read_text(encoding="utf-8").strip() == \
        "install --id Microsoft.PowerShell --source winget"
    artifacts = ROOT / "artifacts/powershell"
    artifacts.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(artifacts / ("native-installed.png" if native else "browser-installed.png")))
    page.request.post(page.url.rstrip("/") + "/api/fixture/reset")
    page.reload(wait_until="domcontentloaded")
    page.get_by_role("button", name="프로젝트 · 연결", exact=True).click()
    region = page.get_by_role("region", name="PowerShell", exact=True)
    expect(region).to_contain_text("winget이 없어 공식 다운로드 페이지를 연다.", timeout=15000)
    opened = []
    if not native:
        page.evaluate("() => { window.fixtureOpened = []; window.open = (url) => window.fixtureOpened.push(url); }")
    region.get_by_role("button", name="PowerShell 7 설치", exact=True).click()
    if native:
        for _ in range(60):
            if (scratch / "opened.txt").exists():
                break
            time.sleep(0.05)
        opened = [(scratch / "opened.txt").read_text(encoding="utf-8")]
    else:
        opened = page.evaluate("window.fixtureOpened")
    assert opened == [shell.DOWNLOAD], opened
    assert errors == [], errors
    page.screenshot(path=str(artifacts / ("native-no-winget.png" if native else "browser-no-winget.png")))
    print(f"PASS: {'native window and 5.1 PTY' if native else 'browser'}, absent pwsh PATH, owned winget output, 7 re-probe, missing-winget download")


def native_window(scratch: Path):
    root = scratch / "app"
    source = root / "web/src-tauri"
    shutil.copytree(ROOT / "web/src-tauri", source, ignore=shutil.ignore_patterns("target"))
    # Stub only the external browser launch in this private build; retain
    # the real UI, native IPC, URL validation, PTY and server resolver.
    rust = source / "src/main.rs"
    rust.write_text(rust.read_text(encoding="utf-8").replace(
        '    let launcher = if cfg!(windows)',
        '    if let Some(path) = std::env::var_os("WIKI_SHELL_CAPTURE") {\n'
        '        std::fs::write(path, parsed.as_str()).map_err(|e| e.to_string())?;\n'
        '        return Ok(());\n    }\n    let launcher = if cfg!(windows)'), encoding="utf-8")
    launcher = root / "tool/main/__main__.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(
        f"import sys\nsys.path.insert(0, {str(ROOT / 'web/tests')!r})\n"
        "from powershell_install import server\nserver()\n", encoding="utf-8")
    built = subprocess.run(["cargo", "build", "--offline", "--manifest-path", str(source / "Cargo.toml"),
                            "--target-dir", str(ROOT / "web/src-tauri/target")], capture_output=True, timeout=180)
    assert built.returncode == 0, built.stderr.decode("utf-8", errors="replace")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={port}",
           "WEBVIEW2_USER_DATA_FOLDER": str(scratch / "webview-profile"),
           "WIKI_SHELL_CAPTURE": str(scratch / "opened.txt")}
    child = subprocess.Popen([str(ROOT / "web/src-tauri/target/debug/wiki-agent.exe")], env=env,
                             creationflags=process.SUSPENDED)
    job = process.contained(child)
    assert job, "The isolated window must own its sidecar"
    process.resumed(child)
    try:
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1) as response:
                    if any(p.get("url", "").startswith("http://127.0.0.1:") for p in json.load(response)):
                        break
            except OSError:
                pass
            assert child.poll() is None, "Isolated native window exited"
            time.sleep(1)
        else:
            logs = {str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="replace")[-5000:]
                    for p in root.rglob("*.log")}
            raise AssertionError(f"Isolated native window did not expose CDP: {logs}")
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            page = None
            for _ in range(100):
                page = next((page for context in browser.contexts for page in context.pages
                             if page.url.startswith("http://127.0.0.1:")), None)
                if page:
                    break
                time.sleep(0.1)
            assert page is not None, "The native app page did not attach"
            try:
                check(page, scratch, True)
            except Exception:
                print("Native fixture:", page.url, page.locator("body").inner_text()[:3000])
                raise
    finally:
        process.terminated(job)
        child.wait(timeout=15)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        print("SKIP: Windows install acceptance requires a Windows host")
        return
    pwsh = shutil.which("pwsh")
    assert pwsh and shutil.which("powershell"), "Both real versions are required to prepare this isolated test"
    original = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix="wiki-shell-") as directory:
        scratch = Path(directory)
        try:
            path = [str(scratch), *(part for part in os.environ["PATH"].split(os.pathsep)
                                   if not any((Path(part) / f"{name}.exe").is_file() for name in ("pwsh", "winget")))]
            os.environ.update(PATH=os.pathsep.join(path), WIKI_SHELL_FIXTURE=directory,
                              ProgramFiles=str(scratch / "programs"), WIKI_SEARCH="off", WIKI_JEV_FALLBACK="off")
            assert shutil.which("pwsh") is None
            (scratch / "pwsh-next.cmd").write_text(f'@echo off\n"{pwsh}" %*\n', encoding="utf-8")
            (scratch / "winget.cmd").write_text(
                f'@echo off\necho %* > "{scratch / "args.txt"}"\necho FIXTURE winget started\n'
                f'"{sys.executable}" -c "import time; time.sleep(3)"\n'
                f'copy /Y "{scratch / "pwsh-next.cmd"}" "{scratch / "pwsh.cmd"}" > nul\n'
                'echo FIXTURE installation complete\n', encoding="utf-8")
            shell._detected = None
            if args.native:
                native_window(scratch)
            else:
                with served(application(scratch)) as port, sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    try:
                        page = browser.new_page(viewport={"width": 1440, "height": 1000})
                        page.goto(f"http://127.0.0.1:{port}")
                        check(page, scratch, False)
                    finally:
                        browser.close()
        finally:
            runtime.stopping.set()
            powershell.close_install()
            os.environ.clear()
            os.environ.update(original)
            shell._detected = None


if __name__ == "__main__":
    main()
