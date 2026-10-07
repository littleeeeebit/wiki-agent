"""Isolated browser save/reload acceptance with actual HTTP observations."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

HTML = b'''<input id="value"><button id="save">Save</button><p id="result"></p>
<script>
async function load() {
  const value = await (await fetch('/record')).json();
  document.querySelector('#result').textContent = value;
}
document.querySelector('#save').onclick = async () => {
  await fetch('/record', {method:'POST', body:document.querySelector('#value').value});
  await load();
};
load();
</script>'''
saved = ""


class API(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html" if self.path == "/" else "application/json")
        self.end_headers()
        self.wfile.write(HTML if self.path == "/" else json.dumps(saved).encode())

    def do_POST(self):
        global saved
        saved = self.rfile.read(int(self.headers["Content-Length"])).decode()
        self.send_response(201)
        self.end_headers()

    def log_message(self, *_args):
        pass


values = dict(line.split("=", 1) for line in Path(".env").read_text(encoding="utf-8").splitlines() if "=" in line)
server = ThreadingHTTPServer(("127.0.0.1", int(values["BROWSER_PORT"])), API)
thread = threading.Thread(target=server.serve_forever)
thread.start()
requests = []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.on("response", lambda response: requests.append({"method": response.request.method,
                    "url": response.url, "status": response.status}) if response.url.endswith("/record") else None)
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.fill("#value", "saved-fixture")
            page.click("#save")
            page.wait_for_function("document.querySelector('#result').textContent === 'saved-fixture'")
            page.reload()
            page.wait_for_function("document.querySelector('#result').textContent === 'saved-fixture'")
            actual = page.locator("#result").inner_text()
        finally:
            browser.close()
finally:
    server.shutdown()
    server.server_close()
    thread.join(5)
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, encoding="utf-8").strip()
receipt = {"head": head, "flow": "save-reload", "environment_id": os.environ["WIKI_VERIFICATION_ENVIRONMENT"],
           "test_scope": os.environ["WIKI_VERIFICATION_SCOPE"], "build_head": head,
           "browser_tool": os.environ["WIKI_VERIFICATION_BROWSER"], "requests": requests,
           "observations": [{"id": "persisted", "expected": "Saved value survives reload", "actual": actual,
                             "pass": actual == "saved-fixture"}],
           "actions": [{"action": "Fill, save and reload", "expected": "Saved value survives reload", "actual": actual}]}
print("```local-evidence\n" + json.dumps(receipt) + "\n```")
