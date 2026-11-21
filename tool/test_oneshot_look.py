"""oneshot step 4b: the shooting harness on a real app, started by its `live_cmd` and driven by Playwright.

See `docs/plans/oneshot/4-frontend.md` ("Look: the shooting harness"). The fixture app
follows the conventions the build prompt fixes: one port from `PORT`, its data under
`APP_DATA_DIR`, each screen served at its own path, and `e2e/seed.json`.
"""

import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
from urllib.parse import quote
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
@app.get("/months", response_class=HTMLResponse)
@app.get("/settings", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
'''
SERVE = "import os\n\nimport uvicorn\n\nuvicorn.run('app.main:app', host='127.0.0.1', port=int(os.environ['PORT']))\n"
FIRST = ('<button data-feature="add">적기</button><a href="/months">달별</a>'
         '<ul>${items.map(i => `<li>${i}</li>`).join("")}</ul>')
INDEX = '''<!doctype html><html><head><meta charset="utf-8"></head><body><div id="root"></div><script>
async function render() {
  const route = location.pathname, root = document.getElementById("root");
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

    def made(first=FIRST, frequency=FREQUENCY, hide=None):
        tree = tmp_path / "tree"
        (tree / "app").mkdir(parents=True, exist_ok=True)
        (tree / "e2e").mkdir(exist_ok=True)
        (tree / "app" / "main.py").write_text(MAIN, encoding="utf-8")
        (tree / "app" / "__main__.py").write_text(SERVE, encoding="utf-8")
        (tree / "app" / "index.html").write_text(INDEX.replace("FIRST", first), encoding="utf-8")
        (tree / "e2e" / "seed.json").write_text(json.dumps([{"method": "POST", "path": "/items", "body": {"name": "커피"}}]),
                                                encoding="utf-8")
        found = look.look(tree, f'"{sys.executable}" -m app', FRONT, frequency, OPS, ["desktop"], tmp_path / "shots",
                          hide=hide)
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
    (FIRST.replace('<a href="/months">달별</a>', ""), "한 번 눌러서"),
    (FIRST + '<img src="x" onerror="console.error(\'boom\')">', "콘솔 오류"),
    # below the fold: shown, but not without scrolling, and a link down there is more than one click
    (FIRST.replace('<button data-feature="add">', f'{FOLD}<button data-feature="add">'), "바로 보이지"),
    (FIRST.replace('<a href="/months">', f'{FOLD}<a href="/months">'), "한 번 눌러서"),
])
def test_the_dom_breaks_of_the_owners_map_and_console_errors_are_high(app_tree, first, said):
    found, _ = app_tree(first)
    assert any(said in what for what in high(found)), found


def test_a_never_feature_on_any_screen_is_high(app_tree):
    found, _ = app_tree(frequency={**FREQUENCY, "export": "never"})
    assert any("화면에 없기로" in what for what in high(found))


def test_a_page_that_holds_a_secret_is_never_shot_and_its_findings_name_only_the_need(app_tree):
    value = f"key-{os.getpid()}-{time.time_ns()}"
    found, shots = app_tree(FIRST + f"<p>{value}</p>", hide={value: "[N1]"})
    assert high(found) and all("[N1]" in w for w in high(found) if "비밀" in w)
    # S1 renders it once its items load; the other screens carry it only in their script, which no picture shows
    assert {(f["screen"], f["state"]) for f in found if "[N1]" in f["what"]} == {("S1", "empty"), ("S1", "full")}
    assert value not in json.dumps(found, ensure_ascii=False)
    assert not list(shots.glob("S1-*-empty.png")) + list(shots.glob("S1-*-full.png"))
    assert (shots / "S2-desktop-full.png").is_file() and (shots / "S1-desktop-loading.png").is_file()


def test_a_one_character_secret_masks_only_the_findings_text():
    found = [look.finding("high", "S1", "desktop", "full", "콘솔 오류: [x] boom")]
    with patch.object(look, "_looked", lambda *a: found):
        assert look.look(*[None] * 7, hide={"]": "[N1]", "high": "[N2]"}) == [{**found[0], "what": "콘솔 오류: [x[N1] boom"}]


@pytest.fixture
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


def shot_of(browser, html, folder, hide, ready=None):
    page = browser.new_page(offline=True)
    page.set_content(html)
    if ready:
        page.wait_for_function(ready)
    with patch.object(look, "opened", lambda *a: page):
        found = look.shot(None, "", {"id": "S1", "route": "/"}, "desktop", "full", re.compile("$^"), folder, {}, hide)
    return found, (folder / "S1-desktop-full.png").is_file()


def test_a_frame_that_shows_a_secret_is_never_shot_and_a_short_secret_only_in_markup_is_not_shown(browser, tmp_path):
    value = f"key-{os.getpid()}"
    found, picture = shot_of(browser, f"<main><iframe srcdoc='<p>error: {value}</p>'></iframe></main>", tmp_path,
                             {value: "[N1]"}, "() => document.querySelector('iframe').contentDocument?.body?.innerText")
    assert [f["severity"] for f in found] == ["high"] and "[N1]" in found[0]["what"] and not picture
    html = "<main id='m'>Ready</main><script>window.ready = () => 1 > 0</script>"
    assert shot_of(browser, html, tmp_path, {">": "[N1]"}) == ([], True)   # tag syntax and code show nothing


def test_a_secret_split_across_elements_is_seen_and_a_stylesheet_never_counts(browser, tmp_path):
    found, picture = shot_of(browser, "<p><span>key-</span><b>split</b></p>", tmp_path, {"key-split": "[N1]"})
    assert "[N1]" in found[0]["what"] and not picture
    html = "<main>Ready</main><svg><style>.unused > path { fill: black; }</style></svg>"
    assert shot_of(browser, html, tmp_path, {">": "[N1]"}) == ([], True)


def test_a_document_that_replaces_the_one_read_before_the_picture_is_read_again(browser, tmp_path):
    value, page = f"key-{os.getpid()}", browser.new_page(offline=True)
    page.route("https://look.test/next", lambda route: route.fulfill(content_type="text/html", body=f"<p>{value}</p>"))
    page.set_content("<meta http-equiv='refresh' content='1; url=https://look.test/next'><main>Ready</main>")
    shoot = page.screenshot

    def late(**kw):   # the refresh lands while the picture is taken, scripts off
        page.wait_for_url("https://look.test/next", timeout=10000)
        return shoot(**kw)

    page.screenshot = late
    with patch.object(look, "opened", lambda *a: page):
        found = look.shot(None, "", {"id": "S1", "route": "/"}, "desktop", "full", re.compile("$^"), tmp_path, {},
                          {value: "[N1]"})
    assert "[N1]" in found[0]["what"] and not (tmp_path / "S1-desktop-full.png").exists()


SHADOW = "(host, html) => { host.attachShadow({mode: 'open'}).innerHTML = html; }"


def test_shadow_text_drawn_or_not_is_seen_and_no_stylesheet_svg_frame_or_pair_of_streams_makes_a_finding(browser):
    page = browser.new_page(offline=True)
    for html, hide, seen in (
            ("key-<b>shadow</b>", {"key-shadow": "[N1]"}, True),   # a shadow root's own text node
            ("<p style='opacity: 0'>key-clear</p>", {"key-clear": "[N1]"}, True),   # carried, drawn or not
            ("<table><tr style='visibility: collapse'><td>key-row</td></tr></table>", {"key-row": "[N1]"}, True),
            ("<section><style>.a > .b { color: red }</style></section>", {">": "[N1]"}, False)):
        for hidden in ("", " hidden"):
            page.set_content(f"<main>Ready</main><div{hidden}></div>")
            page.locator("div").evaluate(SHADOW, html)
            assert bool(look.secrets_shown(page, hide)) == seen   # a hidden host carries its text all the same
    gap = "<span>key-</span><span hidden>note</span><span style='visibility: hidden'>more</span><b>gap</b>"
    for host in ("", " hidden"):   # shown joined past hidden text between; under a hidden host, neither carried nor shown
        page.set_content(f"<main>Ready</main><div{host}></div>")
        page.locator("div").evaluate(SHADOW, gap)
        assert bool(look.secrets_shown(page, {"key-gap": "[N1]"})) == (not host)
    page.set_content(f"<main>{gap}</main>")
    assert look.secrets_shown(page, {"key-gap": "[N1]"})   # the document's own rendered text
    for cased, carried, shown in (("uppercase", "key-up", "KEY-UP"), ("lowercase", "KEY-LOW", "key-low"),
                                  ("capitalize", "key cap", "Key Cap")):
        page.set_content(f"<main>Ready <span style='text-transform: {cased}'>{carried}</span></main>")
        assert look.secrets_shown(page, {shown: "[N1]"})   # cased as drawn
        page.locator("main").evaluate(SHADOW, f"<p style='text-transform: {cased}'>{carried}</p>")
        assert look.secrets_shown(page, {shown: "[N1]"})
    word = "<p style='text-transform: capitalize'><span>read</span><b>er</b></p>"   # one word across elements
    page.set_content(f"<main>{word}</main>")
    assert look.secrets_shown(page, {"Reader": "[N1]"})
    page.locator("main").evaluate(SHADOW, word)
    assert look.secrets_shown(page, {"Reader": "[N1]"})
    page.set_content("<main>KEY-<b>Mixed</b></main>")   # any casing of a value is that value
    assert look.secrets_shown(page, {"key-mixed": "[N1]"})
    for root in ("", " hidden"):   # a hidden root's rendered text is empty, never its stylesheets
        page.set_content(f"<html{root}><body><main>Ready</main><style>.a > .b {{ color: red }}</style></body></html>")
        assert look.secrets_shown(page, {">": "[N1]"}) == ""
    svg = "data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%2F%3E"
    page.set_content(f"<main>Ready</main><iframe src=\"{svg}\"></iframe>")
    page.frames[1].wait_for_load_state()
    assert look.secrets_shown(page, {"key-x": "[N1]"}) == ""   # an SVG frame with no text is read, not a crash
    label = "data:image/svg+xml," + quote("<svg xmlns='http://www.w3.org/2000/svg'><text y='20'><![CDATA[key-x]]></text>"
                                          "<style><![CDATA[.a > .b {}]]></style></svg>")
    page.set_content(f"<main>Ready</main><iframe src=\"{label}\"></iframe>")
    page.frames[1].wait_for_load_state()
    assert "[N1]" in look.secrets_shown(page, {"key-x": "[N1]"}) and look.secrets_shown(page, {">": "[N2]"}) == ""
    page.set_content("<p>ab</p><iframe srcdoc='<p>cd</p>'></iframe><input value='ef'>")
    page.wait_for_function("() => document.querySelector('iframe').contentDocument?.body?.innerText")
    assert look.secrets_shown(page, {"abcd": "[N1]", "cdef": "[N2]"}) == ""   # each stream is matched alone


def test_an_animation_not_started_yet_starts_before_it_is_held(browser):
    for _ in range(10):   # held while pending, it started later in its before phase in about a third of runs
        page = browser.new_page(offline=True)
        page.set_content("<p id='t'>x</p>")
        page.add_style_tag(content="@keyframes m { from { opacity: 0 } } #t { animation: m 5s linear infinite }")
        with look.still(page):
            at = page.evaluate("document.getAnimations()[0].currentTime")
            page.screenshot()
            time.sleep(0.05)
            assert page.evaluate("document.getAnimations()[0].currentTime") == at
        page.close()


def test_rendered_text_an_animation_would_change_holds_while_the_page_is_still(browser):
    page = browser.new_page(offline=True)
    page.set_content("<main>key-<span>note</span>gap</main><style>@keyframes off { to { visibility: hidden } }"
                     " span { animation: off 50ms 600ms both }</style>")
    with look.still(page):
        assert look.secrets_shown(page, {"key-gap": "[N1]"}) == ""
        time.sleep(1)
        assert look.secrets_shown(page, {"key-gap": "[N1]"}) == ""   # the span never hides while held
    page.close()


def test_a_page_is_still_while_it_is_read_and_shot_and_runs_again_after(browser):
    page = browser.new_page(offline=True)
    page.set_content("<p id='t'>0</p><iframe srcdoc=\"<p id='f'>0</p><script>let i = 0; "
                     "setInterval(() => document.getElementById('f').textContent = ++i, 2)</script>\"></iframe>"
                     "<script>let n = 0; setInterval(() => document.getElementById('t').textContent = ++n, 2)</script>")
    page.wait_for_function("() => +document.getElementById('t').textContent > 5")
    def read():
        return [f.evaluate("() => document.body.innerText") for f in page.frames]

    def moved():
        return page.evaluate("document.getAnimations()[0].currentTime")

    page.add_style_tag(content="@keyframes m { to { opacity: 0 } } #t { animation: m 5s linear infinite }")
    with look.still(page):
        before, at = read(), moved()
        page.screenshot()
        time.sleep(0.2)
        assert read() == before and moved() == at   # a CSS animation, which can show or hide rendered text, holds too
    time.sleep(0.2)
    assert read() != before and moved() != at


def test_an_app_the_command_cannot_start_is_refused_with_its_output(tmp_path):
    with pytest.raises(ValueError, match="띄우지 못했다"):
        with look.served(tmp_path, f'"{sys.executable}" -c "print(42)"', tmp_path):
            pass


def test_the_app_starts_with_the_secrets_it_was_given_and_its_refusal_never_shows_one(tmp_path):
    value = f"key-{os.getpid()}-{time.time_ns()}"
    (tmp_path / "boom.py").write_text("import os\nprint('using', os.environ['SERVICE_KEY'])\n", encoding="utf-8")
    with pytest.raises(ValueError, match="띄우지 못했다") as e:
        look.look(tmp_path, f'"{sys.executable}" boom.py', FRONT, FREQUENCY, OPS, ["desktop"], tmp_path / "shots",
                  env={**os.environ, "SERVICE_KEY": value}, hide={value: "[N1]"})
    assert "using [N1]" in str(e.value) and value not in str(e.value)   # it read the key, and the refusal hid it


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


@pytest.mark.skipif(os.name != "nt", reason="the watcher runs on Windows only")
def test_a_child_the_watcher_finds_while_the_look_ends_is_still_killed():
    entered, release, killed, closed, watcher = threading.Event(), threading.Event(), [], [], []

    def parents():   # the watcher's scan sees a new child, then is held before it registers it
        if threading.current_thread().name == "oneshot-look-tree":
            watcher.append(threading.current_thread())
            entered.set()
            release.wait(5)
            return {100: 50, 200: 100}
        return {100: 50}

    k32 = type("K32", (), {"OpenProcess": staticmethod(lambda access, inherit, pid: 10000 + pid),
                           "TerminateProcess": staticmethod(lambda h, code: killed.append(h)),
                           "CloseHandle": staticmethod(closed.append)})
    proc = type("Proc", (), {"pid": 100, "poll": lambda self: 0})()
    with patch.object(look, "parents", parents), patch.object(look, "_k32", k32), \
         patch.object(look.process, "terminated", lambda job: None):
        tree = look.Tree(proc, None)
        assert entered.wait(5)
        reaping = threading.Thread(target=tree.reap)
        reaping.start()
        time.sleep(0.3)   # the end of the look gets as far as it can while the scan is held
        release.set()
        reaping.join(5)
        watcher[0].join(5)
    assert not reaping.is_alive() and not watcher[0].is_alive()
    assert tree.held == {} and killed == [10200] and closed == [10200]


def test_a_stop_ends_the_wait_for_the_app(tmp_path):
    with pytest.raises(ValueError, match="멈췄다"):
        with look.served(tmp_path, f'"{sys.executable}" -c "import time; time.sleep(60)"', tmp_path, lambda: True):
            pass


def test_the_api_pattern_matches_a_path_parameter_and_nothing_else():
    api = look.api_paths(["GET /items/{item_id}", "POST /items"])
    assert api.fullmatch("/items/7") and api.fullmatch("/items") and not api.fullmatch("/items/7/x")
