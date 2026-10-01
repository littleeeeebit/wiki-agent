"""Native PTY privacy regression using Playwright/CDP, never desktop control.

Run against an isolated wiki-agent build and fixture task:
python web/tests/terminal_privacy.py --app <test exe> --task live-check
Requires the locally installed Playwright package and a free CDP port 8793.
Translation requests are intercepted locally; no output goes to a provider.
"""

import argparse
import json
import os
import socket
import subprocess
import time
import urllib.request

from playwright.sync_api import sync_playwright


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--app", required=True)
    args.add_argument("--task", required=True)
    args.add_argument("--project", help="Select an isolated fixture project before testing")
    args.add_argument("--expect-leak", action="store_true", help="Reproduce the pre-fix behavior")
    opts = args.parse_args()
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8793)) == 0:
            raise RuntimeError("CDP port 8793 is already in use; leave the existing app alone")
    env = {**os.environ, "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--remote-debugging-port=8793"}
    app = subprocess.Popen([opts.app], env=env)
    try:
        for _ in range(45):
            try:
                with urllib.request.urlopen("http://127.0.0.1:8793/json/list", timeout=1) as response:
                    if any(p.get("url", "").startswith("http://127.0.0.1:") for p in json.load(response)):
                        break
            except OSError:
                pass
            if app.poll() is not None:
                raise RuntimeError("The isolated native app exited")
            time.sleep(1)
        else:
            raise RuntimeError("The isolated native app did not expose CDP")
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:8793")
            page = browser.contexts[0].pages[0]
            requests, errors = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def translate(route):
                body = route.request.post_data_json
                requests.extend(body["texts"])
                route.fulfill(json={"texts": ["검증을 통과했다." for _ in body["texts"]]})

            page.route("**/api/translate", translate)
            page.reload(wait_until="domcontentloaded")
            if opts.project:
                page.get_by_role("combobox", name="프로젝트", exact=True).click()
                page.get_by_role("option").filter(has_text=opts.project).click()
            page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(has_text=opts.task).click()
            page.get_by_role("tab", name="터미널", exact=True).click()
            terminal = page.get_by_role("region", name="터미널", exact=True)
            entry = terminal.locator("textarea.xterm-helper-textarea")
            entry.wait_for()
            page.wait_for_timeout(1500)
            requests.clear()
            entry.focus()
            page.keyboard.insert_text("Write-Output 'SECRET=synthetic-private-token'; Write-Output 'The fixture check passed.'")
            page.keyboard.press("Enter")
            page.wait_for_timeout(3500)
            if opts.expect_leak:
                assert any("synthetic-private-token" in text for text in requests), requests
                print("PRE-FIX REPRODUCED: synthetic secret was offered to the translator")
                return
            assert requests == [], requests
            screen = terminal.locator(".xterm-screen").bounding_box()
            page.mouse.move(screen["x"] + 2, screen["y"] + 2)
            page.mouse.down()
            page.mouse.move(screen["x"] + screen["width"] - 2, screen["y"] + screen["height"] - 2, steps=10)
            page.mouse.up()
            terminal.get_by_role("button", name="선택한 출력 가져오기", exact=True).click()
            preview = terminal.get_by_role("textbox", name="외부 번역 서비스로 보낼 내용")
            assert "synthetic-private-token" in preview.input_value()
            page.wait_for_timeout(1000)
            assert requests == [], requests
            preview.fill("The fixture check passed.")
            terminal.get_by_role("button", name="확인한 내용 번역", exact=True).click()
            terminal.get_by_text("검증을 통과했다.", exact=True).wait_for()
            assert requests == ["The fixture check passed."], requests
            assert errors == [], errors
            print("PASS: no automatic upload, no preview upload, only confirmed safe text translated")
            page.screenshot(path="artifacts/terminal-privacy.png")
    finally:
        if app.poll() is None:
            app.terminate()
        app.wait(timeout=15)


if __name__ == "__main__":
    main()
