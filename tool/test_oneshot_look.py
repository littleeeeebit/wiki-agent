"""oneshot step 4b: the shooting harness on a real app, started by its `live_cmd` and driven by Playwright.

See `docs/plans/oneshot/4-frontend.md` ("Look: the shooting harness"). The fixture app
follows the conventions the build prompt fixes: one port from `PORT`, its data under
`APP_DATA_DIR`, hash routes, and `e2e/seed.json`.
"""

import http.server
import json
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from main import oneshot_look as look

MAIN = '''
import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()
DATA = Path(os.environ.get("APP_DATA_DIR", "."))


def items():
    f = DATA / "items.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


@app.get("/items")
def listed():
    return items()


@app.post("/items")
def added(body: dict):
    (DATA / "items.json").write_text(json.dumps([*items(), body["name"]]), encoding="utf-8")
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
'''
SERVE = "import os\n\nimport uvicorn\n\nuvicorn.run('app.main:app', host='127.0.0.1', port=int(os.environ['PORT']))\n"
FIRST = ('<button data-feature="add">적기</button><a href="#/months">달별</a>'
         '<ul>${items.map(i => `<li>${i}</li>`).join("")}</ul>')
INDEX = '''<!doctype html><html><head><meta charset="utf-8"></head><body><div id="root"></div><script>
async function render() {
  const route = location.hash.slice(1) || "/", root = document.getElementById("root");
  root.textContent = "불러오는 중";
  if (route === "/") {
    let items = [];
    try { const r = await fetch("/items"); if (!r.ok) throw new Error("x"); items = await r.json(); }
    catch (e) { root.textContent = "불러오지 못했다"; return; }
    root.innerHTML = `FIRST`;
  } else if (route === "/months") {
    root.innerHTML = '<div data-feature="months">달별 목록</div>';
  } else if (route === "/settings") {
    root.innerHTML = '<button data-feature="export">내보내기</button>';
  }
}
window.addEventListener("hashchange", render);
render();
</script></body></html>'''
FRONT = {"screens": [{"id": "S1", "route": "/", "primary": True, "features": ["add"]},
                     {"id": "S2", "route": "/months", "primary": False, "features": ["months"]},
                     {"id": "S3", "route": "/settings", "primary": False, "features": ["export"]}]}
FREQUENCY = {"add": "every", "months": "sometimes", "export": "rarely"}
OPS = ["GET /items", "POST /items"]
FOLD = '<div style="height:3000px"></div>'


@pytest.fixture
def app_tree(tmp_path):
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as exc:   # no package or no browser on this machine
        pytest.skip(f"Playwright 가 없다 — {exc}")

    def made(first=FIRST, frequency=FREQUENCY):
        tree = tmp_path / "tree"
        (tree / "app").mkdir(parents=True, exist_ok=True)
        (tree / "e2e").mkdir(exist_ok=True)
        (tree / "app" / "main.py").write_text(MAIN, encoding="utf-8")
        (tree / "app" / "__main__.py").write_text(SERVE, encoding="utf-8")
        (tree / "app" / "index.html").write_text(INDEX.replace("FIRST", first), encoding="utf-8")
        (tree / "e2e" / "seed.json").write_text(json.dumps([{"method": "POST", "path": "/items", "body": {"name": "커피"}}]),
                                                encoding="utf-8")
        found = look.look(tree, f'"{sys.executable}" -m app', FRONT, frequency, OPS, ["desktop"], tmp_path / "shots")
        return found, tmp_path / "shots"

    return made


def high(found):
    return [f["what"] for f in found if f["severity"] == "high"]


def test_a_clean_app_is_shot_in_every_state_with_no_high_finding(app_tree):
    found, shots = app_tree()
    assert high(found) == []
    assert len(list(shots.glob("*.png"))) == 12 and (shots / "S1-desktop-loading.png").is_file()


@pytest.mark.parametrize("first, said", [
    (FIRST.replace('<button data-feature="add">', '<button data-feature="add" style="display:none">'), "바로 보이지"),
    (FIRST + '<button data-feature="export">내보내기</button>', "첫 화면에 없어야"),
    (FIRST.replace('<a href="#/months">달별</a>', ""), "한 번 눌러서"),
    (FIRST + '<img src="x" onerror="console.error(\'boom\')">', "콘솔 오류"),
    # below the fold: shown, but not without scrolling, and a link down there is more than one click
    (FIRST.replace('<button data-feature="add">', f'{FOLD}<button data-feature="add">'), "바로 보이지"),
    (FIRST.replace('<a href="#/months">', f'{FOLD}<a href="#/months">'), "한 번 눌러서"),
])
def test_the_dom_breaks_of_the_owners_map_and_console_errors_are_high(app_tree, first, said):
    found, _ = app_tree(first)
    assert any(said in what for what in high(found)), found


def test_a_never_feature_on_any_screen_is_high(app_tree):
    found, _ = app_tree(frequency={**FREQUENCY, "export": "never"})
    assert any("화면에 없기로" in what for what in high(found))


def test_an_app_the_command_cannot_start_is_refused_with_its_output(tmp_path):
    with pytest.raises(ValueError, match="띄우지 못했다"):
        with look.served(tmp_path, f'"{sys.executable}" -c "print(42)"', tmp_path):
            pass


def test_an_app_whose_process_tree_cannot_be_owned_is_never_started(tmp_path):
    started = []
    real = subprocess.Popen

    def popen(*a, **k):
        started.append(real(*a, **k))
        return started[-1]

    with patch.object(look.subprocess, "Popen", popen), patch.object(look.process, "contained", lambda proc: None), \
         pytest.raises(ValueError, match="묶지 못해"):
        with look.served(tmp_path, f'"{sys.executable}" -c "import time; time.sleep(60)"', tmp_path):
            pass
    assert started[0].poll() is not None


def unreadable(port):
    raise OSError("table")


@pytest.mark.parametrize("launcher, table", [
    ("import time; time.sleep(60)", None), ("raise SystemExit(1)", None),
    ("import time; time.sleep(60)", unreadable),            # not knowing the owner is not "nobody else"
    ("import time; time.sleep(60)", lambda port: {0}),      # a listener whose owner cannot be seen
])
def test_another_program_on_the_apps_port_gets_no_request(tmp_path, launcher, table):
    heard = []

    class Other(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            heard.append(self.path)
            self.send_response(200)
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *a):
            pass

    other = http.server.HTTPServer(("127.0.0.1", 0), Other)   # took the port before the app could bind it
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        with patch.object(look, "free_port", lambda: other.server_address[1]), patch.object(look, "READY_SECONDS", 3), \
             patch.object(look, "listeners", table or look.listeners), pytest.raises(ValueError):
            with look.served(tmp_path, f'"{sys.executable}" -c "{launcher}"', tmp_path) as url:
                look.seeded(tmp_path, url)
    finally:
        other.shutdown()
        other.server_close()
    assert heard == []


@pytest.mark.parametrize("launch", [
    "srv.py",
    "keeper.py",   # keeper → a launcher in between, which starts the server and exits → the server
])
def test_the_apps_own_listener_is_accepted_and_nothing_of_it_outlives_the_look(tmp_path, launch):
    (tmp_path / "srv.py").write_text(
        "import http.server, os\nhttp.server.HTTPServer(('127.0.0.1', int(os.environ['PORT'])),"
        " http.server.SimpleHTTPRequestHandler).serve_forever()\n", encoding="utf-8")
    for name, child, wait in (("keeper.py", "between.py", 600), ("between.py", "srv.py", 1)):
        (tmp_path / name).write_text(f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, {child!r}])\n"
                                     f"time.sleep({wait})\n", encoding="utf-8")
    with look.served(tmp_path, f'"{sys.executable}" {launch}', tmp_path) as url:
        time.sleep(1.5)   # the launcher in between has exited by now
        port = int(url.rsplit(":", 1)[1])
        assert look.listeners(port) not in (None, set())
    deadline = time.monotonic() + 10
    while look.listeners(port) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not look.listeners(port)   # the server under the launcher's shell is gone too


def test_a_stop_ends_the_wait_for_the_app(tmp_path):
    with pytest.raises(ValueError, match="멈췄다"):
        with look.served(tmp_path, f'"{sys.executable}" -c "import time; time.sleep(60)"', tmp_path, lambda: True):
            pass


def test_the_api_pattern_matches_a_path_parameter_and_nothing_else():
    api = look.api_paths(["GET /items/{item_id}", "POST /items"])
    assert api.fullmatch("/items/7") and api.fullmatch("/items") and not api.fullmatch("/items/7/x")
