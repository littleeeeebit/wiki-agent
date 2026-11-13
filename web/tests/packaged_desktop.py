"""Run a relocated real desktop image with disposable data, no agent login or quota."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tool"))
from common import process  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    image, out = args.image.resolve(), args.out.resolve()
    if not (image / "wiki-agent.exe").is_file() or not (image / "payload/app/release.json").is_file():
        parser.error("Select a completed build directory containing wiki-agent.exe and payload/")
    scratch = Path(tempfile.mkdtemp(prefix="wiki desktop 한글 "))
    installed = scratch / "프로그램 폴더"
    installed.mkdir()
    shutil.copyfile(image / "wiki-agent.exe", installed / "wiki-agent.exe")
    shutil.copytree(image / "payload", installed / "payload")
    home = scratch / "user"
    data = home / "AppData/Local/wiki-agent-data"
    projects = scratch / "팀 프로젝트"
    (projects / "fixture/.git").mkdir(parents=True)
    for folder in (data, home, home / "AppData/Local", home / "AppData/Roaming"):
        folder.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        debug = sock.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if k != "WIKI_DATA_HOME" and not any(
        marker in k.upper() for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "LANGFUSE"))}
    env.update(WIKI_USER_HOME=str(home), USERPROFILE=str(home), HOME=str(home),
               LOCALAPPDATA=str(home / "AppData/Local"), APPDATA=str(home / "AppData/Roaming"),
               CODEX_HOME=str(home / ".codex"), CLAUDE_CONFIG_DIR=str(home / ".claude"), WIKI_SEARCH="off",
               JEV_ENV=str(home / "missing.env"), TRANSLATE_ENV=str(home / "missing.env"), WIKI_JEV_FALLBACK="off",
               WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={debug}",
               WEBVIEW2_USER_DATA_FOLDER=str(scratch / "webview"),
               PATH=os.pathsep.join([str(Path(os.environ["SystemRoot"]) / "System32"),
                                    str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0")]))
    child = subprocess.Popen([str(installed / "wiki-agent.exe")], cwd=scratch, env=env,
                             creationflags=process.background_options().get("creationflags", 0) | process.SUSPENDED)
    job = process.contained(child)
    observed = {"image": str(installed), "data": str(data), "build_image": str(image),
                "release": json.loads((image / "payload/app/release.json").read_text(encoding="utf-8")),
                "default_data_path": True,
                "executable_sha256": hashlib.sha256((installed / "wiki-agent.exe").read_bytes()).hexdigest()}
    try:
        if os.name == "nt" and not job:
            raise RuntimeError("Could not own the disposable app process tree")
        process.resumed(child)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError(f"Desktop exited: {child.returncode}; {list(data.rglob('*.log'))}")
            try:
                with urlopen(f"http://127.0.0.1:{debug}/json/version", timeout=1):
                    break
            except OSError:
                time.sleep(0.2)
        with sync_playwright() as playwright:
            with urlopen(f"http://127.0.0.1:{debug}/json/list", timeout=5) as response:
                print(json.dumps({"debug_targets": json.load(response)}, ensure_ascii=True), flush=True)
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{debug}")
            deadline = time.monotonic() + 50
            page = None
            while time.monotonic() < deadline:
                pages = [p for c in browser.contexts for p in c.pages]
                if pages:
                    page = pages[0]
                    break
                time.sleep(0.2)
            if page is None:
                print(json.dumps({"pages": [p.url for c in browser.contexts for p in c.pages],
                                  "backend_log": (data / "raw/main.err.log").read_text(encoding="utf-8")}, ensure_ascii=True))
                raise AssertionError("No backend page; inspect the disposable raw/main.err.log")
            page.wait_for_function("location.href.startsWith('http://127.0.0.1:')", timeout=40000)
            observed["initial_url"] = page.evaluate("location.href")
            assert observed["initial_url"].startswith("http://127.0.0.1:"), observed["initial_url"]
            page.get_by_role("heading", name="이 PC에서 시작하기").wait_for()
            status = page.evaluate("fetch('/api/onboarding').then(r => r.json())")
            assert Path(status["data"]) == data and status["packaged"] is True
            assert not data.is_relative_to(home / "AppData/Local/wiki-agent")
            if "python" in status:
                assert Path(status["python"]) == installed / "payload/python/python.exe"
                assert Path(status["resources"]) == installed / "payload/app"
            observed["backend"] = status
            for _ in range(2):
                failed = page.evaluate("""async () => {
                  try { await window.__TAURI_INTERNALS__.invoke('package_install', {version:'0.0.0'}); return '' }
                  catch (error) { return String(error) }
                }""")
                assert failed and '다른 창에서' not in failed, failed
                assert page.evaluate("fetch('/api/onboarding').then(r => r.ok)")
            observed["update_failure_retry"] = True
            page.get_by_label("Git 프로젝트들이 들어 있는 폴더").fill(str(projects))
            page.get_by_role("button", name="폴더 저장", exact=True).click()
            page.wait_for_function("[...document.querySelectorAll('button')].some(b => b.textContent === '폴더 저장' && !b.disabled)")
            saved = json.loads((data / ".chat-local.json").read_text(encoding="utf-8"))
            assert Path(saved["workspace"]) == projects
            await_listen = """async () => {
              window.__fixtureTerminal = '';
              const callback = window.__TAURI_INTERNALS__.transformCallback(event => {
                const chunk = new TextDecoder().decode(new Uint8Array(event.payload[1]));
                window.__fixtureTerminal += chunk;
                if (chunk.includes('\u001b[6n')) void window.__TAURI_INTERNALS__.invoke('pty_write', {
                  id:event.payload[0], data:'\u001b[1;1R'
                });
              });
              return window.__TAURI_INTERNALS__.invoke('plugin:event|listen', {
                event:'pty-out', target:{kind:'Any'}, handler:callback
              });
            }"""
            page.evaluate(await_listen)
            terminal = page.evaluate("cwd => window.__TAURI_INTERNALS__.invoke('pty_open', {cwd, cols:80, rows:24})", str(projects))
            try:
                page.wait_for_function("window.__fixtureTerminal.includes('>')", timeout=20000)
            except Exception:
                raise AssertionError(repr(page.evaluate("window.__fixtureTerminal"))) from None
            marker = projects / "터미널 확인.txt"
            script = f"[System.IO.File]::WriteAllText('{str(marker).replace(chr(39), chr(39) * 2)}', 'fixture terminal');\r"
            page.evaluate("args => window.__TAURI_INTERNALS__.invoke('pty_write', args)", {"id": terminal, "data": script})
            deadline = time.monotonic() + 20
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            assert marker.is_file(), page.evaluate("window.__fixtureTerminal")
            assert marker.read_text(encoding="utf-8") == "fixture terminal"
            page.evaluate("window.__TAURI_INTERNALS__.invoke('new_window')")
            deadline = time.monotonic() + 20
            while sum(len(c.pages) for c in browser.contexts) < 2 and time.monotonic() < deadline:
                page.wait_for_timeout(100)
            second = next(p for c in browser.contexts for p in c.pages if p != page)
            second.get_by_role("heading", name="이 PC에서 시작하기").wait_for()
            assert second.evaluate("location.origin") == page.evaluate("location.origin")
            out.mkdir(parents=True, exist_ok=True)
            second.screenshot(path=str(out / "onboarding-desktop.png"))
            try:
                page.evaluate("window.__TAURI_INTERNALS__.invoke('plugin:window|destroy')")
            except Exception:
                pass  # Destroying the calling webview can close the transport before its reply.
            assert second.evaluate("fetch('/api/onboarding').then(r => r.ok)")
            try:
                second.evaluate("window.__TAURI_INTERNALS__.invoke('plugin:window|destroy')")
            except Exception:
                pass
            child.wait(timeout=20)
            try:
                with urlopen(observed["initial_url"] + "api/onboarding", timeout=2):
                    raise AssertionError("Backend survived the last window")
            except OSError:
                pass
        observed.update(passed=True, checks=["relocated resources", "bundled backend requests", "workspace save",
                                             "native terminal", "two windows share one server", "last-window shutdown"])
        (out / "receipt.json").write_text(json.dumps(observed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"PASS: relocated desktop, backend, terminal and window lifecycle; receipt: {out / 'receipt.json'}")
    finally:
        process.terminated(job)
        if child.poll() is None:
            child.kill()
        child.wait()


if __name__ == "__main__":
    main()
