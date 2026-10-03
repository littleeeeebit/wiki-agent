"""Phone layout and optional real HTTPS tunnel checks against synthetic data.

python web/tests/mobile_browser.py [--live-tunnel]
Requires the local Playwright package and Chromium. Never opens a user's app.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
APK_FIXTURE = b"synthetic-apk-download" + bytes(range(256)) * 8192
sys.path.insert(0, str(ROOT / "tool"))
os.environ["WIKI_SEARCH"] = "off"

from main import app, mobile, query  # noqa: E402


def fixture(path, method):
    if path == "channels":
        return [{"id": cid, "label": label, "blurb": "Synthetic conversation", "live": False,
                 "repo": "fixture", "remote": "", "model": "", "model_name": "Default", "effort": ""}
                for cid, label in (("next", "다음 작업"), ("wiki", "위키"), ("retro", "회고"))]
    if path == "options":
        return {"projects": [{"id": "fixture", "state": "연결 완료", "missing": [], "notes": [], "wired": True}],
                "models": [{"id": "", "label": "기본", "note": ""}], "efforts": [], "codex_error": ""}
    if path == "graph":
        return {"repo": "fixture", "hub": False, "wiki": "fixture", "layers": {
            "repo": {"nodes": [], "edges": []}, "hub": {"nodes": [], "edges": [], "ladder": []}},
                "metrics": {"pages": 0, "orphans": 0, "lint": 0}}
    if path == "switch":
        return {"translate": False, "usage": {"month": "2026-10", "usd": 0, "limit": 10}}
    if path == "worktrees":
        return {"project": "fixture", "repo": "fixture", "rows": [
            {"path": "fixture-task", "name": "fixture-task", "branch": "fixture", "dirty": False,
             "merged": False, "live": False, "busy": False}]}
    if path == "specs":
        return {"project": "fixture", "specs": []}
    if path == "prs":
        return {"project": "fixture", "rows": []}
    if path == "loops":
        return {"loops": [], "turns": []}
    if path == "loop/settings":
        return {"rounds": 3, "concurrent": 1}
    if path.startswith("log/"):
        return [{"role": "assistant", "text": "Synthetic conversation visible on the phone.", "ts": 1}]
    if path == "work/log":
        return {"rows": [{"role": "assistant", "text": "Synthetic task reply."}], "session_id": "fixture",
                "busy": False, "running": None, "rules": [], "queued": None}
    if path == "work/diff":
        return {"diff": "+synthetic change", "base": "fixture", "truncated": False, "omitted": [],
                "totals": {"files": 1, "added": 1, "deleted": 0, "binary": 0, "unknown": 0}}
    if path == "providers/usage":
        return {"providers": []}
    if path.startswith("providers/"):
        return {"provider": "claude", "live": False, "connection_ms": None, "error": "", "quota": [], "usage": {}}
    if path == "connect":
        return {"rows": [], "settings": {"survey": False}, "hub": {"name": "fixture", "needed": False, "refused": ""}}
    if path == "work/settings":
        return {"bypass": False}
    if path.endswith("events"):
        async def idle():
            yield b"data: null\n\n"
            await asyncio.sleep(60)
        return StreamingResponse(idle(), media_type="text/event-stream")
    if method == "POST" and path.startswith("say/"):
        return StreamingResponse(iter([b'data: {"kind":"done","text":"Synthetic sent reply.","seq":0}\n\n']),
                                 media_type="text/event-stream")
    return {}


def inspect_desktop(page, capture):
    measurements = []
    for width, height in ((1440, 900), (1440, 390), (1200, 390), (1100, 800)):
        page.set_viewport_size({"width": width, "height": height})
        expected = 'portrait' if width <= 1100 else 'desktop'
        page.wait_for_function("mode => document.documentElement.dataset.mobileLayout === mode", arg=expected)
        # An APK layout hint must never change a local PC window.
        page.evaluate("window.dispatchEvent(new CustomEvent('mobile-screen-mode', { detail: 'landscape' }))")
        measurements.append(page.evaluate("""() => ({
          viewport: [innerWidth, innerHeight], mode: document.documentElement.dataset.mobileLayout,
          columns: getComputedStyle(document.querySelector('.app-shell')).gridTemplateColumns,
          rows: getComputedStyle(document.querySelector('.app-shell')).gridTemplateRows,
          panes: [...document.querySelectorAll('.app-shell > aside, .conversation-pane, .task-pane')].map(e => {
            const r = e.getBoundingClientRect(), s = getComputedStyle(e);
            return [r.x, r.y, r.width, r.height, s.display, s.padding, s.fontSize];
          })
        })"""))
        assert measurements[-1]['mode'] == expected, measurements[-1]
    baseline = ROOT / 'artifacts' / 'desktop-before-landscape.json'
    if capture:
        baseline.write_text(json.dumps(measurements, indent=2), encoding='utf-8')
    elif baseline.exists():
        assert measurements == json.loads(baseline.read_text(encoding='utf-8')), measurements
    print(json.dumps({'desktop_geometry': measurements}))
    page.set_viewport_size({"width": 1440, "height": 900})


def inspect_phone(page, width, height):
    page.set_viewport_size({"width": width, "height": height})
    nav = page.get_by_role("navigation", name="화면", exact=True)
    nav.get_by_role("button", name="작업 목록", exact=True).click()
    settings_width = page.get_by_role("button", name="설정", exact=True).bounding_box()["width"]
    row = page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(has_text="fixture-task")
    row.wait_for()
    assert row.inner_text().find("fixture-task") >= 0
    row.click()
    page.get_by_text("Synthetic task reply.", exact=True).wait_for()
    diff = page.get_by_label("실시간 코드 diff", exact=True)
    assert not diff.is_visible()
    page.get_by_text('코드 변경 현황', exact=False).click()
    diff.wait_for()
    page.get_by_text('코드 변경 현황', exact=False).click()
    assert not diff.is_visible()
    assert not page.get_by_role("tab", name="터미널", exact=True).count()
    page.get_by_role("tab", name="리뷰", exact=True).click()
    page.get_by_role("tab", name="에이전트", exact=True).click()
    nav.get_by_role("button", name="대화", exact=True).click()
    page.get_by_text("Synthetic conversation visible on the phone.", exact=True).first.wait_for()
    page.get_by_role("button", name="옵션", exact=True).click()
    measured = page.evaluate("""() => ({
      viewport: innerWidth, document: document.documentElement.scrollWidth,
      oversized: [...document.querySelectorAll('header, main, nav, textarea, button, [role="combobox"]')]
        .filter(e => e.getBoundingClientRect().width && e.getBoundingClientRect().right > innerWidth + 1)
        .map(e => [e.tagName, e.className, e.getBoundingClientRect().right]),
      input: getComputedStyle(document.querySelector('textarea')).fontSize,
      targets: [...document.querySelectorAll('.mobile-navigation button')].map(e => e.getBoundingClientRect().height)
    })""")
    assert measured["document"] <= width and not measured["oversized"], measured
    assert measured["input"] == "16px" and min(measured["targets"]) >= 44, measured
    assert settings_width >= 44, settings_width
    page.get_by_role("button", name="옵션", exact=True).click()
    page.locator('.mobile-header').get_by_role("button", name="지도", exact=True).click()
    page.get_by_role("button", name="되돌리기", exact=True).wait_for()
    assert page.locator('.map-toolbar').evaluate("e => e.scrollWidth <= e.clientWidth"), "map controls overflow"
    page.locator('.mobile-header').get_by_role("button", name="대화", exact=True).click()
    print(json.dumps({"width": width, "height": height, **measured}))


def select_app_mode(page, mode):
    # Emulate the one-way hint from the APK menu, not Android rotation itself.
    page.evaluate("""mode => {
      document.documentElement.dataset.nativeShell = 'android';
      document.documentElement.dataset.nativeMode = mode;
      window.dispatchEvent(new CustomEvent('mobile-screen-mode', { detail: mode }));
    }""", mode)
    page.wait_for_function("mode => document.documentElement.dataset.mobileLayout === mode", arg=mode)


def inspect_modes(page):
    # A preference from the former three-pane UI must not restore it.
    page.evaluate("localStorage.setItem('mobile-layout', 'landscape')")
    page.reload()
    page.get_by_text("fixture-task", exact=True).wait_for()
    nav = page.get_by_role("navigation", name="화면", exact=True)
    nav.get_by_role("button", name="작업 목록").click()
    page.get_by_role("button", name="프로젝트 · 리뷰", exact=True).click()
    page.get_by_role("button", name="프로젝트 · 연결", exact=True).wait_for()
    page.get_by_role("button", name="프로젝트 · 리뷰", exact=True).click()
    assert not page.get_by_role("button", name="프로젝트 · 연결", exact=True).is_visible()
    page.get_by_role("navigation", name="작업", exact=True).get_by_role("button").filter(has_text="fixture-task").click()
    page.get_by_text("Synthetic task reply.", exact=True).wait_for()
    assert not page.get_by_role("combobox").count()
    toggle = page.get_by_role("button", name="작업 옵션 열기", exact=True)
    toggle.click()
    page.get_by_role("combobox").first.wait_for()
    page.get_by_label("사용 토큰", exact=True).wait_for(state="attached")
    page.get_by_role("button", name="작업 옵션 닫기", exact=True).click()
    assert not page.get_by_role("combobox").count()
    assert not page.get_by_label("연결 및 사용량", exact=True).count()
    # Measure content, not merely the absence of a horizontal scrollbar.
    for width, height, mode in ((320, 740, 'portrait'), (400, 800, 'portrait')):
        select_app_mode(page, mode)
        page.set_viewport_size({"width": width, "height": height})
        measured = page.evaluate("""() => {
          const session = document.querySelector('[aria-label="에이전트 세션"]');
          const reading = session.querySelector('.overflow-y-auto').getBoundingClientRect();
          const header = document.querySelector('.mobile-header').getBoundingClientRect();
          const composer = session.querySelector('.composer').getBoundingClientRect();
          const changes = document.querySelector('.task-changes').getBoundingClientRect();
          return { mode: document.documentElement.dataset.mobileLayout,
            document: document.documentElement.scrollWidth,
            columns: getComputedStyle(document.querySelector('.app-shell')).gridTemplateColumns,
            header: header.height, reading: reading.height, composerBottom: composer.bottom,
            contentShare: reading.height / innerHeight, changes: changes.height,
            navigationTop: document.querySelector('.mobile-navigation').getBoundingClientRect().top,
            navigationWidth: document.querySelector('.mobile-navigation').getBoundingClientRect().width,
            navigationDirection: getComputedStyle(document.querySelector('.mobile-navigation')).flexDirection,
            pane: document.querySelector('.task-pane').getBoundingClientRect().toJSON() };
        }""")
        assert measured["mode"] == mode and measured["document"] <= width, measured
        assert measured["header"] == 48, measured
        assert len(measured["columns"].split()) == 1, measured
        # The requested fixed diff disclosure now occupies one row outside the transcript.
        assert measured["reading"] >= height * 0.70 - measured["changes"], measured
        assert measured["changes"] <= 48, measured
        assert measured["composerBottom"] <= measured["navigationTop"] + 1, measured
        assert not page.get_by_text("Synthetic conversation visible on the phone.", exact=True).first.is_visible()
        print(json.dumps({"width": width, "height": height, **measured}))
        if width in (400, 844):
            page.screenshot(path=str(ROOT / "artifacts" / f"mobile-focused-{width}-fixture.png"))
    # Public native-shell hint reserves the menu target without another toolbar.
    assert page.locator('.mobile-header').evaluate("e => parseInt(getComputedStyle(e).paddingInlineStart)") == 56
    colors = []
    for theme in ('dark', 'light'):
        page.evaluate("theme => { document.documentElement.classList.remove('dark', 'light'); document.documentElement.classList.add(theme) }", theme)
        measured = page.evaluate("""() => {
          const rgb = color => color.match(/[\\d.]+/g).slice(0, 3).map(Number);
          const luminance = color => rgb(color).map(v => v / 255)
            .map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
            .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
          const selected = document.querySelector('.mobile-navigation button[aria-pressed="true"]');
          const candidates = ['filled', 'outlined'].map(style => {
            selected.style.backgroundColor = style === 'filled' ? 'var(--primary)' : 'var(--card)';
            selected.style.color = style === 'filled' ? 'var(--primary-foreground)' : 'var(--primary)';
            const s = getComputedStyle(selected), a = luminance(s.color), b = luminance(s.backgroundColor);
            return { style, contrast: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) };
          });
          selected.style.removeProperty('background-color');
          selected.style.removeProperty('color');
          const pairs = [...document.querySelectorAll('.mobile-header h1, .mobile-navigation button')].map(e => {
            let surface = e;
            while (getComputedStyle(surface).backgroundColor === 'rgba(0, 0, 0, 0)') surface = surface.parentElement;
            const foreground = getComputedStyle(e).color, background = getComputedStyle(surface).backgroundColor;
            const a = luminance(foreground), b = luminance(background);
            return { label: e.textContent.trim(), contrast: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) };
          });
          const body = [...document.querySelectorAll('.task-pane p')].find(e => e.textContent === 'Synthetic task reply.');
          return { pairs, candidates, title: getComputedStyle(document.querySelector('.mobile-header h1')).fontSize,
            body: getComputedStyle(body).fontSize, lineHeight: getComputedStyle(body).lineHeight };
        }""")
        assert all(pair['contrast'] >= 4.5 for pair in measured['pairs']), measured
        assert all(candidate['contrast'] >= 4.5 for candidate in measured['candidates']), measured
        assert measured['title'] == measured['body'] == '16px', measured
        colors.append({'theme': theme, **measured})
    page.evaluate("document.documentElement.classList.remove('light'); document.documentElement.classList.add('dark')")
    print(json.dumps({'type_and_contrast': colors}))
    inspect_landscape_workspace(page)
    # Check a leading-side rail mirrors, rather than overlaying RTL content.
    page.evaluate("document.documentElement.dir = 'rtl'")
    assert page.locator('.app-shell > aside').bounding_box()['x'] == 740 - 180
    assert page.locator('.task-pane').bounding_box()['x'] == 0
    page.evaluate("document.documentElement.dir = 'ltr'")
    # Switching layout does not unmount the conversation or overwrite its draft.
    select_app_mode(page, 'portrait')
    nav.get_by_role("button", name="대화", exact=True).click()
    draft = page.locator('.conversation-pane').get_by_role("textbox", name="질문 또는 지시", exact=True)
    draft.fill("Synthetic unsent draft")
    # Both choices work from the other mode. Viewport and IME resizing never
    # choose a mode, even while dimensions briefly disagree during rotation.
    for mode in ('portrait', 'landscape', 'portrait', 'landscape'):
        select_app_mode(page, mode)
        for width, height in ((400, 800), (844, 390), (844, 210)):
            page.set_viewport_size({'width': width, 'height': height})
            assert page.locator('html').get_attribute('data-mobile-layout') == mode
            assert draft.input_value() == "Synthetic unsent draft"
    page.set_viewport_size({'width': 844, 'height': 210})
    draft.focus()
    assert draft.bounding_box()['y'] + draft.bounding_box()['height'] <= 210
    assert page.locator('.conversation-pane').get_by_role('button', name='보내', exact=True).is_visible()
    page.evaluate("window.dispatchEvent(new CustomEvent('mobile-screen-mode', { detail: 'desktop' }))")
    assert page.locator('html').get_attribute('data-mobile-layout') == 'landscape'
    select_app_mode(page, 'portrait')
    page.set_viewport_size({'width': 400, 'height': 800})
    nav.get_by_role("button", name="선택한 작업", exact=True).click()
    nav.get_by_role("button", name="대화", exact=True).click()
    assert draft.input_value() == "Synthetic unsent draft"
    draft.fill("")
    page.set_viewport_size({"width": 400, "height": 400})
    draft.focus()
    assert draft.bounding_box()["y"] + draft.bounding_box()["height"] <= 352
    inspect_phone(page, 400, 800)
    page.set_viewport_size({"width": 1440, "height": 900})
    assert page.get_by_role("navigation", name="화면", exact=True).is_visible()
    assert page.locator("html").get_attribute("data-mobile-layout") == "portrait"
    select_app_mode(page, 'landscape')
    assert page.locator("html").get_attribute("data-mobile-layout") == "landscape"
    assert page.locator('.task-pane').is_visible() and page.locator('.conversation-pane').is_visible()
    # The APK may deliver its saved choice before the remote-status response.
    page.add_init_script("document.addEventListener('DOMContentLoaded', () => { document.documentElement.dataset.nativeMode = 'landscape' })")
    page.reload()
    page.get_by_text('fixture-task', exact=True).wait_for()
    assert page.locator('html').get_attribute('data-mobile-layout') == 'landscape'
    select_app_mode(page, 'portrait')
    print("PASS: explicit APK modes, distinct PC-style landscape workspace, no orientation inference, disclosures, drafts and reduced viewport")


def inspect_landscape_workspace(page):
    select_app_mode(page, 'landscape')
    task = page.locator('.task-pane')
    chat = page.locator('.conversation-pane')
    header = page.locator('.mobile-header')
    group = page.get_by_role('group', name='가로 작업 공간', exact=True)
    for width, height in ((740, 320), (800, 360), (844, 390), (932, 430), (1100, 800)):
        page.set_viewport_size({'width': width, 'height': height})
        assert task.is_visible() and chat.is_visible() and page.locator('.app-shell > aside').is_visible()
        assert page.get_by_text('Synthetic task reply.', exact=True).is_visible()
        assert page.get_by_text('Synthetic conversation visible on the phone.', exact=True).first.is_visible()
        measured = page.evaluate("""() => ({
          viewport: [innerWidth, innerHeight], document: document.documentElement.scrollWidth,
          columns: getComputedStyle(document.querySelector('.app-shell')).gridTemplateColumns,
          panes: [...document.querySelectorAll('.app-shell > aside, .conversation-pane, .task-pane')]
            .map(e => e.getBoundingClientRect().toJSON()),
          targets: [...document.querySelectorAll('.mobile-header button')].filter(e => e.getBoundingClientRect().width)
            .map(e => [e.getBoundingClientRect().width, e.getBoundingClientRect().height]),
          reading: [...document.querySelectorAll('.task-pane .prose-answer p, .conversation-pane .prose-answer p')]
            .map(e => [getComputedStyle(e).fontSize, getComputedStyle(e).lineHeight]),
          composers: [...document.querySelectorAll('.composer')].map(e => e.getBoundingClientRect().bottom),
          inputs: [...document.querySelectorAll('textarea')].map(e => getComputedStyle(e).fontSize)
        })""")
        assert measured['document'] <= width and len(measured['columns'].split()) == 3, measured
        assert measured['panes'][0]['width'] == 180, measured
        assert min(pane['width'] for pane in measured['panes'][1:]) >= 260, measured
        assert max(measured['composers']) <= height and measured['inputs'] == ['16px', '16px'], measured
        assert min(min(target) for target in measured['targets']) >= 44, measured
        assert all(size == '16px' and float(height[:-2]) >= 24 for size, height in measured['reading']), measured
        print(json.dumps({'landscape_workspace': measured}))
        task.get_by_role('button', name='작업 옵션 열기', exact=True).click()
        task.get_by_role('combobox').first.wait_for()
        task.get_by_role('button', name='작업 옵션 닫기', exact=True).click()
        task.get_by_role('tab', name='리뷰', exact=True).click()
        task.get_by_role('tab', name='에이전트', exact=True).click()
        if width == 844:
            page.screenshot(path=str(ROOT / 'artifacts' / 'mobile-landscape-workspace-844-fixture.png'))
    page.set_viewport_size({'width': 844, 'height': 390})
    for theme in ('dark', 'light'):
        page.evaluate("theme => { document.documentElement.classList.remove('dark', 'light'); document.documentElement.classList.add(theme) }", theme)
        contrast = group.locator('.landscape-selected').evaluate("""e => {
          const luminance = color => color.match(/[\\d.]+/g).slice(0, 3).map(Number).map(v => v / 255)
            .map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
            .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
          const s = getComputedStyle(e), a = luminance(s.color), b = luminance(s.backgroundColor);
          return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        }""")
        assert contrast >= 4.5, (theme, contrast)
        print(json.dumps({'landscape_selected_contrast': {'theme': theme, 'ratio': contrast}}))
    page.evaluate("document.documentElement.classList.remove('light'); document.documentElement.classList.add('dark')")
    query_draft = chat.get_by_role('textbox', name='질문 또는 지시', exact=True)
    work_draft = task.get_by_role('textbox', name='질문 또는 지시', exact=True)
    query_draft.fill('Synthetic conversation draft')
    work_draft.fill('Synthetic task draft')
    header.get_by_role('button', name='작업 목록 접기', exact=True).click()
    assert not page.locator('.app-shell > aside').is_visible()
    for pane, visible, hidden in (('대화', chat, task), ('작업', task, chat)):
        group.get_by_role('button', name=pane, exact=True).click()
        assert visible.is_visible() and not hidden.is_visible()
        assert visible.bounding_box()['width'] == 844
    group.get_by_role('button', name='함께', exact=True).click()
    assert query_draft.input_value() == 'Synthetic conversation draft'
    assert work_draft.input_value() == 'Synthetic task draft'
    header.get_by_role('button', name='지도', exact=True).click()
    page.get_by_role('button', name='되돌리기', exact=True).wait_for()
    assert page.locator('.map-toolbar').evaluate('e => e.scrollWidth <= e.clientWidth')
    header.get_by_role('button', name='대화로', exact=True).click()
    assert query_draft.input_value() == 'Synthetic conversation draft'
    header.get_by_role('button', name='작업 목록 펼치기', exact=True).click()
    query_draft.fill('')
    work_draft.fill('')
    page.set_viewport_size({'width': 740, 'height': 320})


def inspect_install(page):
    page.evaluate("""() => {
      const prompt = new Event('beforeinstallprompt', { cancelable: true })
      prompt.prompt = async () => { window.__installPrompted = true }
      prompt.userChoice = Promise.resolve({ outcome: 'accepted' })
      window.dispatchEvent(prompt)
    }""")
    page.get_by_role("navigation", name="화면", exact=True).get_by_role(
        "button", name="작업 목록", exact=True).click()
    page.get_by_role("button", name="설정", exact=True).click()
    page.screenshot(path=str(ROOT / "artifacts" / "mobile-install-fixture.png"))
    page.get_by_role("button", name="앱 설치", exact=True).click()
    assert page.evaluate("window.__installPrompted === true")
    page.get_by_text("홈 화면 앱으로 설치되어 있습니다.", exact=True).wait_for()
    page.get_by_role("button", name="닫기", exact=True).click()
    print("PASS: app install prompt and manual platform fallback")


def main():
    # Regression guard for native rotation/keyboard setup, not a hardware test.
    activity = ET.parse(ROOT / 'android/app/src/main/AndroidManifest.xml').find('application/activity')
    android_attr = '{http://schemas.android.com/apk/res/android}'
    assert {'orientation', 'screenSize', 'screenLayout', 'keyboardHidden'} <= set(activity.get(android_attr + 'configChanges', '').split('|'))
    assert activity.get(android_attr + 'screenOrientation') == 'portrait'
    assert activity.get(android_attr + 'windowSoftInputMode') == 'adjustResize'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-tunnel", action="store_true")
    parser.add_argument("--capture-desktop", action="store_true", help="Record local UI before mobile-only changes")
    opts = parser.parse_args()
    fake = FastAPI()
    fake.middleware("http")(app.only_this_screen)
    fake.include_router(mobile.router)
    fake.add_api_route("/mobile-install", app.mobile_install, methods=["GET"])
    fake.add_api_route("/mobile-install.apk", app.mobile_apk, methods=["GET"])

    @fake.api_route("/api/{path:path}", methods=["GET", "POST", "PUT"])
    async def api(path: str, request: Request):
        return fixture(path, request.method)

    fake.mount("/", StaticFiles(directory=ROOT / "web" / "dist", html=True))
    with tempfile.TemporaryDirectory(prefix="wiki-mobile-fixture-") as scratch, \
         patch.object(mobile, "APK", Path(scratch) / "wiki-agent.apk"):
        mobile.APK.write_bytes(APK_FIXTURE)
        query.LOGS = Path(scratch)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        mobile.companion.port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(fake, log_level="error", proxy_headers=False, ws="wsproto",
                                              timeout_graceful_shutdown=1))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
                page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
                (ROOT / "artifacts").mkdir(exist_ok=True)
                base = f"http://127.0.0.1:{mobile.companion.port}"
                page.goto(base)
                page.get_by_text("fixture-task", exact=True).wait_for()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                inspect_desktop(page, opts.capture_desktop)
                for width, height in ((400, 800), (320, 740), (844, 390), (932, 430), (1100, 800)):
                    inspect_phone(page, width, height)
                # Generate a real one-use fixture link without starting a tunnel.
                mobile.companion.origin = "https://fixture.trycloudflare.com"
                page.set_viewport_size({"width": 1440, "height": 900})
                page.wait_for_function("document.documentElement.dataset.mobileLayout === 'desktop'")
                page.get_by_role("button", name="설정", exact=True).click()
                install_qr = page.get_by_role("img", name="Android 앱 설치 QR 코드", exact=True)
                install_qr.wait_for()
                installation = install_qr.inner_html()
                page.context.grant_permissions(["clipboard-read", "clipboard-write"])
                page.get_by_role("button", name="설치 링크 복사", exact=True).click()
                assert page.evaluate("navigator.clipboard.readText()") == mobile.companion.origin + "/mobile-install"
                for width in (1440, 400):
                    page.set_viewport_size({"width": width, "height": 900})
                    assert install_qr.bounding_box()["width"] <= 232
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(ROOT / "artifacts" / "mobile-apk-install-qr-fixture.png"))
                page.get_by_role("button", name="연결 링크 만들기", exact=True).click()
                qr = page.get_by_role("img", name="휴대폰 연결 QR 코드", exact=True)
                qr.wait_for()
                first = qr.inner_html()
                page.get_by_role("button", name="연결 링크 만들기", exact=True).click()
                page.wait_for_function("old => document.querySelector('svg[aria-label=\"휴대폰 연결 QR 코드\"]').innerHTML !== old", arg=first)
                qr.screenshot(path=str(ROOT / "artifacts" / "mobile-pairing-qr-fixture.png"))
                (ROOT / "artifacts" / "mobile-pairing-qr-fixture.txt").write_text(
                    page.get_by_role("textbox", name="휴대폰 연결 링크", exact=True).input_value(), encoding="utf-8")
                page.context.grant_permissions(["clipboard-read", "clipboard-write"])
                page.get_by_role("button", name="링크 복사", exact=True).click()
                assert page.evaluate("navigator.clipboard.readText()") == page.get_by_role(
                    "textbox", name="휴대폰 연결 링크", exact=True).input_value()
                page.clock.install()
                page.clock.fast_forward(301_000)
                qr.wait_for(state="detached")
                assert install_qr.inner_html() == installation, "installation QR outlives pairing expiry"
                page.get_by_text("연결 링크가 만료됐습니다. 새로 만드세요.", exact=True).wait_for()
                print("PASS: QR regeneration, copy fallback and expiry")
                page.get_by_role("button", name="닫기", exact=True).click()
                installer = browser.new_page(viewport={"width": 400, "height": 800})
                installer.goto(base + "/mobile-install")
                for width in (320, 400, 844):
                    installer.set_viewport_size({"width": width, "height": 800})
                    assert installer.evaluate("document.documentElement.scrollWidth <= innerWidth")
                installer.set_viewport_size({"width": 400, "height": 800})
                installer.screenshot(path=str(ROOT / "artifacts" / "mobile-apk-install-page-fixture.png"))
                with installer.expect_download() as download:
                    installer.get_by_role("link", name="APK 다운로드", exact=True).click()
                assert download.value.suggested_filename == "wiki-agent.apk"
                assert Path(download.value.path()).read_bytes() == APK_FIXTURE
                installer.get_by_text('Galaxy에서 ‘기기 보호를 위해 앱 차단됨’이 나오나요?', exact=True).click()
                installer.get_by_text('본인 PC에서 받은 APK를 신뢰하는 경우에만', exact=False).wait_for()
                installer.get_by_text('Play 프로텍트를 끄지 마세요.', exact=False).wait_for()
                installer.close()
                print("PASS: installation QR, clipboard link, responsive install page and APK download")
                mobile.companion.origin = ""

                # Exercise remote UI locally, without publishing data or needing DNS.
                mock_phone = browser.new_page(viewport={"width": 400, "height": 800})
                mock_phone.on("pageerror", lambda error: errors.append(str(error)))
                mock_phone.route("https://fonts.googleapis.com/**", lambda route: route.abort())
                mock_phone.route("https://fonts.gstatic.com/**", lambda route: route.abort())
                mock_phone.route("**/api/mobile/status", lambda route: route.fulfill(
                    json={"local": False, "paired": True}))

                def bridge(ws):
                    def answer(message):
                        request = json.loads(message)
                        path = request["url"].removeprefix("/api/").split("?")[0]
                        stream = path.endswith("events") or path.startswith("say/")
                        ws.send(json.dumps({"status": 200, "headers": [["content-type",
                            "text/event-stream" if stream else "application/json"]]}))
                        ws.send(b"data: null\n\n" if stream else json.dumps(fixture(path, request["method"])).encode())
                        if not path.endswith("events"):
                            ws.close(code=1000)
                    ws.on_message(answer)

                mock_phone.route_web_socket("**/api/mobile/request", bridge)
                mock_phone.goto(base)
                manifest = mock_phone.evaluate("fetch('/manifest.webmanifest').then(r => r.json())")
                assert manifest["display"] == "standalone" and {
                    icon["sizes"] for icon in manifest["icons"]
                } >= {"192x192", "512x512"}, manifest
                assert any(icon["purpose"] == "maskable" for icon in manifest["icons"]), manifest
                mock_phone.evaluate("navigator.serviceWorker.ready")
                cached = mock_phone.evaluate("""async () => {
                  const keys = await caches.keys()
                  return (await Promise.all(keys.map(async key => (await caches.open(key)).keys())))
                    .flat().map(request => new URL(request.url).pathname)
                }""")
                assert cached == ["/offline.html"], cached
                inspect_phone(mock_phone, 400, 800)
                inspect_install(mock_phone)
                mock_phone.context.set_offline(True)
                mock_phone.reload()
                mock_phone.get_by_text("PC에 연결할 수 없습니다", exact=True).wait_for()
                mock_phone.context.set_offline(False)
                mock_phone.reload()
                mock_phone.get_by_text("fixture-task", exact=True).wait_for()
                print("PASS: offline shell without cached conversations")
                inspect_modes(mock_phone)
                mock_phone.close()
                if opts.live_tunnel:
                    page.set_viewport_size({"width": 1440, "height": 900})
                    page.get_by_role("button", name="설정", exact=True).click()
                    page.get_by_role("button", name="외부 연결 켜기", exact=True).click()
                    page.get_by_role("button", name="연결 링크 만들기", exact=True).wait_for(timeout=65000)
                    page.get_by_role("button", name="연결 링크 만들기", exact=True).click()
                    link = page.get_by_role("textbox", name="휴대폰 연결 링크", exact=True).input_value()
                    phone = browser.new_page(viewport={"width": 400, "height": 800})
                    phone.on("pageerror", lambda error: errors.append(str(error)))
                    phone.route("https://fonts.googleapis.com/**", lambda route: route.abort())
                    phone.route("https://fonts.gstatic.com/**", lambda route: route.abort())
                    # Only this synthetic fixture is published. No user repository or record is served.
                    phone.goto(mobile.companion.origin + "/mobile-install", timeout=60000)
                    with phone.expect_download() as download:
                        phone.get_by_role("link", name="APK 다운로드", exact=True).click()
                    assert Path(download.value.path()).read_bytes() == APK_FIXTURE
                    phone.goto(link, timeout=60000)
                    inspect_phone(phone, 400, 800)
                    assert "pair=" not in phone.url
                    cookie = next(c for c in phone.context.cookies() if c["name"] == mobile.COOKIE)
                    assert cookie["httpOnly"] and cookie["secure"]
                    phone.reload()
                    inspect_phone(phone, 400, 800)
                    phone.context.set_offline(True)
                    phone.get_by_role("button", name="다시 연결", exact=True).wait_for()
                    phone.context.set_offline(False)
                    phone.get_by_role("button", name="다시 연결", exact=True).click()
                    inspect_phone(phone, 400, 800)
                    inspect_modes(phone)
                    print("PASS: real HTTPS tunnel, pairing cookie, streamed API transport, reload and network recovery")
                assert errors == [], errors
                preview = phone if opts.live_tunnel else page
                preview.set_viewport_size({"width": 400, "height": 800})
                preview.screenshot(path=str(ROOT / "artifacts" / "mobile-companion-fixture.png"))
                browser.close()
                print("PASS: desktop, portrait, small phone, landscape and touch target checks")
        finally:
            mobile.companion.stop()
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()


if __name__ == "__main__":
    main()
