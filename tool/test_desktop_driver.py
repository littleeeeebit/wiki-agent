"""The desktop look's driver: the main window chosen once and sized, each state shot and read, steps through
patterns only, a deadline per phase, and the mechanical findings (docs/research/oneshot-desktop-session.md)."""

import json
import subprocess
import sys
import time
import types

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows desktops")

if sys.platform == "win32":
    import desktop_driver
    import desktop_session
    import owned_job

WINDOW = ("import tkinter; root = tkinter.Tk(); root.title('fixture'); tkinter.Label(root, text='ready').pack(); "
          "root.after(60000, root.destroy); root.mainloop()")
TWO = ("import tkinter; root = tkinter.Tk(); root.title('one'); other = tkinter.Toplevel(root); other.title('two'); "
       "root.after(60000, root.destroy); root.mainloop()")
NONE = "import time; time.sleep(60)"


def python(code):
    return [sys.executable, "-c", code]


def test_the_main_window_is_sized_to_the_card_shot_and_read(tmp_path):
    with desktop_session.look(tmp_path, "tk") as look:
        look.start("app", python(WINDOW), tmp_path)
        report = desktop_driver.drive(look, "app", (640, 480), [], settle_ms=500)

    saved = json.loads((tmp_path / "tk" / "evidence" / "driver" / "result.json").read_text(encoding="utf-8"))
    assert report["phase"] == "done" and report["main"]["title"] == "fixture", report
    assert report["size"]["frame"][2:] == [640, 480]
    assert desktop_driver.findings(report) == desktop_driver.findings(saved) == []   # the saved evidence agrees
    start = report["states"][0]
    assert start["name"] == "start" and start["shots"][0]["drawn"] and start["reads"][0]["read"]
    assert start["shots"][0]["file"] == f"s00-{start['shots'][0]['hwnd']}.png"   # the id names it, not the label
    assert (tmp_path / "tk" / "evidence" / "driver" / start["shots"][0]["file"]).stat().st_size > 0
    assert start["nodes"][0]["type"] == "Window" and start["nodes"][0]["name"] == "fixture"


def test_without_shots_each_state_is_read_and_no_window_is_shot(tmp_path):
    # The owner's desktop round's mode (plan 9e5b): PrintWindow is never called there, so nothing is shot.
    with desktop_session.look(tmp_path, "tk") as look:
        look.start("app", python(WINDOW), tmp_path)
        report = desktop_driver.drive(look, "app", (640, 480), [], settle_ms=500, shoot=False)
    start = report["states"][0]
    assert report["phase"] == "done" and start["shots"] == [{"hwnd": start["windows"][0]["hwnd"], "drawn": False,
                                                              "unshot": True}], report
    assert start["reads"][0]["read"] and start["nodes"][0]["name"] == "fixture"
    assert not list((tmp_path / "tk" / "evidence" / "driver").glob("*.png"))


LATER = ("import pathlib, subprocess, sys, time; marker = pathlib.Path(sys.argv[1]) / 'evidence' / 'driver' / "
         "'progress.json'\nwhile not marker.exists(): time.sleep(0.05)\n"   # the driver runs before the child starts
         f"subprocess.run([sys.executable, '-c', {WINDOW!r}])")


def test_a_window_a_later_child_of_the_app_opens_is_the_apps(tmp_path):
    with desktop_session.look(tmp_path, "later") as look:
        root = look.start("app", [sys.executable, "-c", LATER, str(look.folder)], tmp_path)
        report = desktop_driver.drive(look, "app", (640, 480), [], settle_ms=500)

    assert report["phase"] == "done" and report["main"]["title"] == "fixture", report
    assert report["main"]["pid"] != root["pid"] and desktop_driver.findings(report) == []


SWAPPED = """
import json, sys, time, tkinter
from pathlib import Path
report = Path(sys.argv[1]) / "evidence" / "driver" / "driver.json"
first = tkinter.Tk(); first.title("one")
def chosen():
    try:
        if json.loads(report.read_text(encoding="utf-8-sig")).get("main"):
            first.destroy(); return
    except (OSError, ValueError):
        pass
    first.after(20, chosen)
first.after(20, chosen); first.mainloop()
second = tkinter.Tk(); second.title("two"); second.after(60000, second.destroy); second.mainloop()
"""


def test_a_main_window_replaced_after_its_choice_is_a_refusal_before_any_action(tmp_path):
    with desktop_session.look(tmp_path, "swapped") as look:
        look.start("app", [sys.executable, "-c", SWAPPED, str(look.folder)], tmp_path)
        report = desktop_driver.drive(look, "app", (640, 480), [{"name": "go", "action": "invoke", "id": "go"}],
                                      settle_ms=3000)

    assert report["main"]["title"] == "one", report
    assert report["refusal"] == "the main window is gone or no longer the app's", report
    assert not any(step["acted"] for step in report["steps"])


def test_two_candidate_windows_at_startup_are_a_refusal(tmp_path):
    with desktop_session.look(tmp_path, "two") as look:
        look.start("app", python(TWO), tmp_path)
        report = desktop_driver.drive(look, "app", (640, 480), [], settle_ms=500)

    assert report["refusal"] == "2 candidate main windows at startup"
    assert desktop_driver.findings(report) == ["refused: 2 candidate main windows at startup"]


OWNED = ("import tkinter; root = tkinter.Tk(); root.title('main'); dialog = tkinter.Toplevel(root); "
         "dialog.title('dialog'); dialog.transient(root); root.after(60000, root.destroy); root.mainloop()")


def test_a_window_the_main_window_owns_at_startup_is_no_candidate_and_is_shot_with_the_state(tmp_path):
    with desktop_session.look(tmp_path, "owned") as look:
        look.start("app", python(OWNED), tmp_path)
        report = desktop_driver.drive(look, "app", (640, 480), [], settle_ms=500)

    assert report["phase"] == "done" and report["main"]["title"] == "main", report
    start = report["states"][0]
    assert sorted(w["title"] for w in start["windows"]) == ["dialog", "main"] and all(s["drawn"] for s in start["shots"])
    owners = {w["title"]: w["owner"] for w in start["windows"]}
    assert owners == {"main": 0, "dialog": report["main"]["hwnd"]}   # the look tells a dialog screen's window by it
    assert desktop_driver.findings(report) == []


def test_a_halt_while_the_driver_runs_ends_it_at_once(tmp_path):
    reads = []   # the driver's start reads it twice, as it is recorded and just before it runs; its watch then finds it set
    halt = types.SimpleNamespace(is_set=lambda: reads.append(1) or len(reads) > 2)
    with desktop_session.look(tmp_path, "halt") as look:
        look.start("app", python(NONE), tmp_path)   # no window: the driver would wait out its first-window wait
        started = time.monotonic()
        report = desktop_driver.drive(look, "app", (640, 480), [], wait=30, settle_ms=200, halt=halt)
    assert report["ended"] == "halted" and report["end"]["clean"] and time.monotonic() - started < 15, report


def test_an_app_that_shows_no_window_is_a_refusal(tmp_path):
    with desktop_session.look(tmp_path, "none") as look:
        look.start("app", python(NONE), tmp_path)
        report = desktop_driver.drive(look, "app", (640, 480), [], wait=2, settle_ms=200)

    assert report["refusal"] == "the app showed no window on the owned desktop"


STAND_IN = """
import json, sys, time
from pathlib import Path
out = Path(json.loads(Path(sys.argv[-1]).read_text(encoding="utf-8"))["out"])
def put(name, value):
    (out / (name + ".part")).write_text(json.dumps(value), encoding="utf-8")
    (out / (name + ".part")).replace(out / name)
for seq, (kind, value) in enumerate(json.loads(sys.argv[-2]), 1):
    if kind == "op":
        put("progress.json", {"seq": seq, "op": value[0]}); time.sleep(value[1])
    else:
        put("driver.json", value)
"""


def stand_in(tmp_path, monkeypatch, script):
    """The driver replaced by a Python process that enters operations and saves reports as `script` says."""

    path = tmp_path / "stand_in.py"
    path.write_text(STAND_IN, encoding="utf-8")
    monkeypatch.setattr(desktop_driver, "POWERSHELL", sys.executable)
    monkeypatch.setattr(desktop_driver, "SCRIPT", path)
    monkeypatch.setattr(desktop_driver, "PHASE_SECONDS", 1)
    monkeypatch.setattr(desktop_driver, "arguments", lambda script_, request: [str(script_), json.dumps(script), str(request)])
    with desktop_session.look(tmp_path, "stand_in") as look:
        look.start("app", python(NONE), tmp_path)
        began = time.monotonic()
        report = desktop_driver.drive(look, "app", (640, 480), [], wait=30, settle_ms=0)
        took = time.monotonic() - began
        saved = json.loads((look.folder / "evidence" / "driver" / "result.json").read_text(encoding="utf-8"))
    assert saved == report   # what a reader opens later is what the server concluded
    return report, took


def test_a_blocked_resize_is_held_to_its_own_deadline_not_the_startup_wait(tmp_path, monkeypatch):
    report, took = stand_in(tmp_path, monkeypatch, [["op", ["wait for a window", 0.2]], ["op", ["resize", 120]]])

    assert report["ended"] == "resize" and report["end"]["clean"], report
    assert took < 15   # the startup wait is 30 s; the resize's own deadline is 1 s
    assert desktop_driver.findings(report) == ["the driver passed its deadline at resize", "the driver left no report"]


START = {"id": "s00", "name": "start", "windows": [{"hwnd": 1, "frame": [0, 0, 640, 480]}],
         "shots": [{"hwnd": 1, "drawn": True}], "reads": [{"hwnd": 1, "read": True}],
         "nodes": [{"window": 1, "depth": 0, "foreign": False, "type": "Window", "name": "fixture", "id": "",
                    "enabled": True, "offscreen": False, "rect": [0, 0, 640, 480], "patterns": []}]}
DONE = {"phase": "done", "size": {"asked": [640, 480], "moved": True, "frame": [0, 0, 640, 480]}, "states": [START],
        "steps": []}


def test_operations_each_within_their_own_deadline_are_never_summed(tmp_path, monkeypatch):
    report, _ = stand_in(tmp_path, monkeypatch, [["op", ["choose", 0.7]], ["op", ["resize", 0.7]],
                                                 ["op", ["read size", 0.7]], ["op", ["done", 0]], ["report", DONE]])

    assert report["ended"] is None and report["last"] == "done", report
    assert desktop_driver.findings(report) == []


WPF_LISTS = """param([string]$kind, [string]$virtual)
Add-Type -AssemblyName PresentationFramework
$V = [System.Windows.Controls.VirtualizingPanel]; $on = $virtual -eq "on"
$w = New-Object System.Windows.Window
$w.Title = "list"; $w.Width = 400; $w.Height = 300
$items = 1..40 | ForEach-Object { [pscustomobject]@{ Name = "item $_"; Group = "group $($_ % 2)" } }
switch ($kind) {
  "listbox" { $c = New-Object System.Windows.Controls.ListBox; $c.DisplayMemberPath = "Name"; $c.ItemsSource = $items }
  "grouped" {
    $c = New-Object System.Windows.Controls.ListView; $c.DisplayMemberPath = "Name"
    $view = New-Object System.Windows.Data.ListCollectionView (, [System.Collections.ArrayList]@($items))
    $view.GroupDescriptions.Add((New-Object System.Windows.Data.PropertyGroupDescription "Group"))
    $c.ItemsSource = $view
    $c.GroupStyle.Add((New-Object System.Windows.Controls.GroupStyle))
    $V::SetIsVirtualizingWhenGrouping($c, $on)
  }
  "grid" { $c = New-Object System.Windows.Controls.DataGrid; $c.ItemsSource = $items; $c.EnableRowVirtualization = $on }
  "tree" {
    $c = New-Object System.Windows.Controls.TreeView
    $top = New-Object System.Windows.Controls.TreeViewItem; $top.Header = "all"; $top.IsExpanded = $true
    $items | ForEach-Object { [void]$top.Items.Add($_.Name) }
    [void]$c.Items.Add($top)
  }
}
$V::SetIsVirtualizing($c, $on)
$w.Content = $c
[void]$w.ShowDialog()
"""


@pytest.mark.parametrize("kind", ["listbox", "grouped", "grid", "tree"])
@pytest.mark.parametrize("virtual", ["on", "off"])
def test_a_wpf_list_counts_the_items_its_read_did_not_reach(tmp_path, kind, virtual):
    # 40 items, more than the window shows, few enough that a hosted runner walks them fully read within the read's
    # deadline: in a list, a grouped list, a grid with its header, and a tree's one expanded branch
    from main import oneshot_desktop_look

    script = tmp_path / "lists.ps1"
    script.write_text(WPF_LISTS, encoding="utf-8")
    with desktop_session.look(tmp_path, f"{kind}-{virtual}") as look:
        look.start("app", [str(desktop_driver.POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(script), kind, virtual], tmp_path)
        report = desktop_driver.drive(look, "app", (400, 300), [], wait=30, settle_ms=1000)
    assert report["states"], {k: report.get(k) for k in ("phase", "ended", "refusal", "error")}
    start = report["states"][0]
    [box] = [n for n in start["nodes"] if n.get("items") == 40]
    assert (box["unread"] > 0) == (virtual == "on"), report
    assert oneshot_desktop_look.partial(start, {w["hwnd"] for w in start["windows"]}) == (virtual == "on")
    assert not [f for f in desktop_driver.findings(report) if "outside its window" in f]   # scrolled rows reachable


WPF_SCROLLER = """param([int]$rows)
Add-Type -AssemblyName PresentationFramework
$w = New-Object System.Windows.Window
$w.Title = "scroller"; $w.Width = 400; $w.Height = 300
$v = New-Object System.Windows.Controls.ScrollViewer
$v.VerticalScrollBarVisibility = "Auto"; $v.HorizontalScrollBarVisibility = "Disabled"
$p = New-Object System.Windows.Controls.StackPanel
foreach ($i in 1..$rows) { $t = New-Object System.Windows.Controls.Button; $t.Content = "row $i"; $t.Height = 30; [void]$p.Children.Add($t) }
$v.Content = $p; $w.Content = $v
[void]$w.ShowDialog()
"""


@pytest.mark.parametrize("rows", [2, 40])
def test_a_container_that_only_scrolls_leaves_its_read_not_whole_while_it_scrolls(tmp_path, rows):
    # A scroll viewer offers no enumeration of what lies past its view, and the driver does not scroll: 40 rows
    # scroll, so its end is unverified; 2 rows fit, so its view holds them all
    from main import oneshot_desktop_look

    script = tmp_path / "scroller.ps1"
    script.write_text(WPF_SCROLLER, encoding="utf-8")
    with desktop_session.look(tmp_path, f"scroller-{rows}") as look:
        look.start("app", [str(desktop_driver.POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(script), str(rows)], tmp_path)
        report = desktop_driver.drive(look, "app", (400, 300), [], wait=30, settle_ms=1000)
    assert report["states"], {k: report.get(k) for k in ("phase", "ended", "refusal", "error")}
    start = report["states"][0]
    [viewer] = [n for n in start["nodes"] if "Scroll" in (n.get("patterns") or []) and n.get("depth", 0) > 0]
    assert ("unread" in viewer) == (rows == 40), viewer
    assert oneshot_desktop_look.partial(start, {w["hwnd"] for w in start["windows"]}) == (rows == 40)


def test_a_view_counts_as_whole_only_not_scrollable_both_ways_at_a_full_size():
    script = desktop_driver.SCRIPT.read_text(encoding="utf-8")
    rule = "if ($patterns -contains \"Scroll\" -and" + \
        script.split("if ($patterns -contains \"Scroll\" -and", 1)[1].split("  $script:nodes += $node", 1)[0]
    # The rule's own text over stand-ins answering the four scroll properties: (vertical, its size, horizontal,
    # its size), `none` unsupplied. Only the first holds its view whole.
    cases = ["$false 100 $false 100", "$false 25 $false 100", "$true 25 $false 100", "$false 100 $false 99.5",
             "none 100 $false 100", "$false none $false 100", "'no' 100 $false 100"]
    probe = subprocess.run([str(desktop_driver.POWERSHELL), "-NoProfile", "-Command",
                            "Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes\n"
                            "$P = [System.Windows.Automation.ScrollPattern]\n"
                            "$patterns = @('Scroll')\n"
                            "function Case($vc, $vs, $hc, $hs) {\n"
                            "  $element = [pscustomobject]@{ k = @{ $P::VerticallyScrollableProperty.Id = $vc; "
                            "$P::VerticalViewSizeProperty.Id = $vs; $P::HorizontallyScrollableProperty.Id = $hc; "
                            "$P::HorizontalViewSizeProperty.Id = $hs } }\n"
                            "  $element | Add-Member ScriptMethod GetCurrentPropertyValue { param($p, $d) "
                            "$x = $this.k[$p.Id]; if ($x -eq 'none') { [System.Windows.Automation.AutomationElement]::NotSupported } "
                            "elseif ($x -is [int]) { [double]$x } else { $x } }\n"
                            "  $node = [ordered]@{}\n  " + rule + "\n  if ($node.Contains('unread')) { 'partial' } else { 'whole' }\n}\n"
                            + "\n".join(f"Case {c}" for c in cases)], capture_output=True, text=True, timeout=60)
    assert probe.stdout.split() == ["whole"] + ["partial"] * (len(cases) - 1), probe


WPF_OWNED = """Add-Type -AssemblyName PresentationFramework
$A = [System.Windows.Automation.AutomationProperties]
$w = New-Object System.Windows.Window
$w.Title = "main"; $w.Width = 400; $w.Height = 300
$b = New-Object System.Windows.Controls.Button
$b.Content = "Search"; $b.Width = 120; $b.Height = 40; $A::SetAutomationId($b, "open-search")
$w.Content = $b
$d = New-Object System.Windows.Window
$d.Title = "dialog"; $d.Width = 240; $d.Height = 160; $A::SetAutomationId($d, "open-search")
$w.Add_ContentRendered({ $d.Owner = $w; $d.Show() })   # owned once the main window has its handle
[void]$w.ShowDialog()
"""


def test_an_owned_window_that_carries_the_id_is_no_second_target_in_the_main_window(tmp_path):
    script = tmp_path / "owned.ps1"
    script.write_text(WPF_OWNED, encoding="utf-8")
    step = {"name": "try", "action": "invoke", "id": "open-search", "expect": {"id": "open-search"}}
    with desktop_session.look(tmp_path, "owned-id") as look:
        look.start("app", [str(desktop_driver.POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(script)], tmp_path)
        report = desktop_driver.drive(look, "app", (400, 300), [step], wait=20, settle_ms=800)
    assert report["steps"][0].get("acted") and not report["steps"][0].get("error"), report


WPF_FORM = """Add-Type -AssemblyName PresentationFramework
$A = [System.Windows.Automation.AutomationProperties]
$w = New-Object System.Windows.Window
$w.Title = "form"; $w.Width = 400; $w.Height = 300
$panel = New-Object System.Windows.Controls.StackPanel
$box = New-Object System.Windows.Controls.TextBox; $box.Height = 32; $A::SetAutomationId($box, "source"); $A::SetName($box, "원문")
$check = New-Object System.Windows.Controls.CheckBox; $check.Content = "번역"; $check.Height = 32; $A::SetAutomationId($check, "translate-on")
[void]$panel.Children.Add($box); [void]$panel.Children.Add($check)
$w.Content = $panel
[void]$w.ShowDialog()
"""


@pytest.mark.parametrize("want, holds", [("On", True), ("Off", False)])
def test_a_check_reads_a_controls_value_and_toggle_state_after_its_steps(tmp_path, want, holds):
    script = tmp_path / "form.ps1"
    script.write_text(WPF_FORM, encoding="utf-8")
    steps = [{"name": "type", "action": "set_value", "id": "source", "value": "안녕", "expect": {"id": "source", "value": "안녕"}},
             {"name": "on", "action": "toggle", "id": "translate-on", "expect": {"id": "translate-on", "toggle": want}}]
    with desktop_session.look(tmp_path, f"form-{want}") as look:
        look.start("app", [str(desktop_driver.POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(script)], tmp_path)
        report = desktop_driver.drive(look, "app", (400, 300), steps, wait=30, settle_ms=800)
    states = {s["id"]: s for s in report["states"]}
    assert [desktop_driver.holds(states[s["state"]], s["expect"]) for s in report["steps"]] == [True, holds], report
    assert [n.get("toggle") for n in states["s00"]["nodes"] if n.get("id") == "translate-on"] == ["Off"]
    assert ("step on: its postcondition was not read" in desktop_driver.findings(report)) is not holds


def test_a_steps_baseline_and_before_state_are_read_with_its_target_found_just_before_it_acts(tmp_path):
    script, there = tmp_path / "form.ps1", tmp_path / "there.txt"
    script.write_text(WPF_FORM, encoding="utf-8")
    there.write_text("old 보고", encoding="utf-8")
    steps = [{"name": "one", "action": "set_value", "id": "source", "value": "a", "baseline": str(there)},
             {"name": "two", "action": "set_value", "id": "source", "value": "b", "before": True,
              "baseline": str(tmp_path / "none.txt")}]
    with desktop_session.look(tmp_path, "baseline") as look:
        look.start("app", [str(desktop_driver.POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(script)], tmp_path)
        report = desktop_driver.drive(look, "app", (400, 300), steps, wait=30, settle_ms=300)
    assert [s.get("baseline") for s in report["steps"]] == [{"text": "old 보고"}, {"absent": True}], report
    states = {s["id"]: s for s in report["states"]}
    before = states[report["steps"][1]["before"]]   # read after step one acted, before step two did
    assert before["name"] == "before-two" and [n.get("value") for n in before["nodes"] if n.get("id") == "source"] == ["a"]
    after = states[report["steps"][1]["state"]]   # the target found again after that read is the one acted on
    assert report["steps"][1]["acted"] and [n.get("value") for n in after["nodes"] if n.get("id") == "source"] == ["b"]


def test_a_step_with_either_read_finds_its_target_again_after_both_and_before_it_acts():
    loop = desktop_driver.SCRIPT.read_text(encoding="utf-8").split("foreach ($step in $ask.steps) {", 1)[1]
    loop = loop.split('Enter "done"', 1)[0]
    at = [loop.index(s) for s in ('$done.before = (State', '$done.baseline = Baseline',
                                  'if ($step.before -or $step.baseline) {', '$found = Target $step', 'Act $found.element')]
    again = loop.index("$found = Target $step", loop.index("if ($step.before -or $step.baseline) {"))
    assert at[0] < at[1] < at[2] < again < at[4], at


def test_a_report_file_that_is_not_utf8_has_no_baseline_text(tmp_path):
    script, bad = tmp_path / "form.ps1", tmp_path / "bad.txt"
    script.write_text(WPF_FORM, encoding="utf-8")
    bad.write_bytes(b"\xffapple river garden")
    with desktop_session.look(tmp_path, "badtext") as look:
        look.start("app", [str(desktop_driver.POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(script)], tmp_path)
        report = desktop_driver.drive(look, "app", (400, 300), [
            {"name": "one", "action": "set_value", "id": "source", "value": "a", "baseline": str(bad)}],
            wait=30, settle_ms=300)
    assert set(report["steps"][0]["baseline"]) == {"error"}, report


def test_an_element_whose_id_changes_while_it_is_read_leaves_its_window_read_not_whole():
    script = desktop_driver.SCRIPT.read_text(encoding="utf-8")
    nodes = script.split("function Nodes(", 1)[1].split("\nfunction ", 1)[0]
    at = [nodes.index(s) for s in ("id = $c.AutomationId", "ValuePattern]::ValueProperty, $true)",
                                   "AutomationElement]::AutomationIdProperty)",
                                   "[System.StringComparison]::Ordinal", "$script:rebound = ")]
    assert at == sorted(at), at
    assert nodes.index("$script:rebound = ") < nodes.index("$script:walked.Add")   # every element, not only valued
    state = script.split("function State(", 1)[1].split("\nfunction ", 1)[0]
    assert "$script:rebound, $script:under = @(), @(), $null, @()" in state and "elseif ($script:rebound) { $read.error = $script:rebound }" in state
    # Ordinal, never PowerShell's culture-aware -eq/-ceq, which takes a soft hyphen for nothing.
    probe = subprocess.run([str(desktop_driver.POWERSHELL), "-NoProfile", "-Command",
                            "[string]::Equals('se' + [char]0xAD + 'en', 'seen', [System.StringComparison]::Ordinal)"],
                           capture_output=True, text=True, timeout=60)
    assert probe.stdout.strip() == "False", probe


def test_an_id_that_is_not_whole_utf16_leaves_its_window_read_not_whole(tmp_path):
    script = tmp_path / "form.ps1"   # the app names its control `seen` + U+D800, which UTF-8 would carry as U+FFFD
    script.write_text(WPF_FORM.replace('$A::SetAutomationId($box, "source")',
                                       '$A::SetAutomationId($box, "seen" + [char]0xD800)'), encoding="utf-8")
    with desktop_session.look(tmp_path, "badid") as look:
        look.start("app", [str(desktop_driver.POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(script)], tmp_path)
        report = desktop_driver.drive(look, "app", (400, 300), [
            {"name": "one", "action": "toggle", "id": "translate-on"}], wait=30, settle_ms=300)
    last = report["states"][-1]
    assert not any("�" in (n.get("id") or "") for n in last["nodes"]), last["nodes"]
    assert [r["read"] for r in last["reads"]] == [False] and "not whole UTF-16" in last["reads"][0]["error"]


def test_a_toggle_state_is_read_without_defaults_so_an_unsupplied_one_is_unknown():
    nodes = desktop_driver.SCRIPT.read_text(encoding="utf-8").split("function Nodes(", 1)[1].split("\nfunction ", 1)[0]
    assert "TogglePattern]::ToggleStateProperty, $true)" in nodes and ".Current.ToggleState" not in nodes
    assert "if ($t -is [System.Windows.Automation.ToggleState]) { [string]$t } else { $null }" in nodes


def test_an_unsupplied_expansion_state_has_its_items_audited_never_taken_for_a_leaf():
    nodes = desktop_driver.SCRIPT.read_text(encoding="utf-8").split("function Nodes(", 1)[1].split("\nfunction ", 1)[0]
    assert "ExpandCollapsePattern]::ExpandCollapseStateProperty, $true)" in nodes and \
        ".Current.ExpandCollapseState" not in nodes
    assert "$open = -not ($e -is [System.Windows.Automation.ExpandCollapseState] -and $e -in" in nodes
    # The rule itself, run: only Collapsed and LeafNode close a container; unsupplied and out of range are audited.
    rule = nodes.split("$open = ", 2)[2].split("\n    } catch", 1)[0]
    probe = subprocess.run([str(desktop_driver.POWERSHELL), "-NoProfile", "-Command",
                            "Add-Type -AssemblyName UIAutomationTypes, UIAutomationClient; "
                            "$S = [System.Windows.Automation.ExpandCollapseState]; "
                            "foreach ($e in @($S::Collapsed, $S::LeafNode, $S::Expanded, $S::PartiallyExpanded, "
                            "[Enum]::ToObject($S, 42), [System.Windows.Automation.AutomationElement]::NotSupported, $null)) "
                            "{ $open = " + rule + "; [string]$open }"], capture_output=True, text=True, timeout=60)
    assert probe.stdout.split() == ["False", "False", "True", "True", "True", "True", "True"], probe


def test_an_element_without_a_runtime_id_is_never_taken_for_one_already_read():
    script = desktop_driver.SCRIPT.read_text(encoding="utf-8")
    rid = "function Rid(" + script.split("function Rid(", 1)[1].split("\nfunction ", 1)[0]
    probe = subprocess.run([str(desktop_driver.POWERSHELL), "-NoProfile", "-Command", rid + "\n"
                            "$none = [pscustomobject]@{}; $none | Add-Member ScriptMethod GetRuntimeId { $null }\n"
                            "$empty = [pscustomobject]@{}; $empty | Add-Member ScriptMethod GetRuntimeId { ,@() }\n"
                            "$some = [pscustomobject]@{}; $some | Add-Member ScriptMethod GetRuntimeId { ,@(42, 7) }\n"
                            "foreach ($e in @($none, $empty, $some)) { try { Rid $e } catch { 'refused' } }"],
                           capture_output=True, text=True, timeout=60)
    assert probe.stdout.split() == ["refused", "refused", "42.7"], probe
    items = script.split("function Items(", 1)[1].split("\nfunction ", 1)[0]
    assert "try { $r = Rid $item; if (-not $mine.Add($r) -or -not ($set -and $set.Contains($r))) { $unread++ } } " \
        "catch { $unread++ }" in items


def test_two_items_of_one_container_sharing_a_runtime_id_leave_one_of_them_unread():
    script = desktop_driver.SCRIPT.read_text(encoding="utf-8")
    rid = "function Rid(" + script.split("function Rid(", 1)[1].split("\nfunction ", 1)[0]
    audit = script.split("function Items(", 1)[1].split("$mine = ", 1)[1].split("} while", 1)[0]
    # The audit's own text over a stand-in container of two items, both `42.7`, with one element by that id walked.
    probe = subprocess.run([str(desktop_driver.POWERSHELL), "-NoProfile", "-Command", rid + "\n"
                            "function Peer($n) { $p = [pscustomobject]@{ n = $n }; "
                            "$p | Add-Member ScriptMethod GetRuntimeId { ,@(42, 7) }; $p }\n"
                            "$peers = @((Peer 1), (Peer 2))\n"
                            "$container = [pscustomobject]@{}; $container | Add-Member ScriptMethod FindItemByProperty "
                            "{ param($after, $p, $v) if ($null -eq $after) { $peers[0] } elseif ($after.n -eq 1) "
                            "{ $peers[1] } else { $null } }\n"
                            "$set = New-Object 'System.Collections.Generic.HashSet[string]'; [void]$set.Add('42.7')\n"
                            "$count, $unread, $item = 0, 0, $null\n$mine = " + audit + "} while ($item)\n"
                            "\"$count $unread\""], capture_output=True, text=True, timeout=60)
    assert probe.stdout.split() == ["2", "1"], probe


def test_an_item_is_read_only_as_an_element_walked_inside_its_own_container():
    script = desktop_driver.SCRIPT.read_text(encoding="utf-8")
    rid = "function Rid(" + script.split("function Rid(", 1)[1].split("\nfunction ", 1)[0]
    # How `Items` sorts the walk: two sibling lists (0 and 1), each walking one element; the second list's element
    # has the id the first's has too, as list-local counters give. Then a tree, 2, whose item 3 holds an element.
    identify = script.split("function Items(", 1)[1].split("$seen = @{}", 1)[1].split("  for ($n = 0;", 1)[0]
    probe = subprocess.run([str(desktop_driver.POWERSHELL), "-NoProfile", "-Command", rid + "\n"
                            "function El($id) { $p = [pscustomobject]@{}; $p | Add-Member NoteProperty k $id; "
                            "$p | Add-Member ScriptMethod GetRuntimeId { ,@($this.k) }; $p }\n"
                            "$script:walked = New-Object 'System.Collections.Generic.List[object]'\n"
                            "$script:walked.Add(@((El 5), @(0))); $script:walked.Add(@((El 9), @()))\n"
                            "$script:walked.Add(@((El 6), @(2, 3)))\n"
                            "$seen = @{}" + identify +
                            "foreach ($i in 0, 1, 2, 3) { if ($seen[$i]) { \"$i=\" + (@($seen[$i]) -join ',') } "
                            "else { \"$i=none\" } }"], capture_output=True, text=True, timeout=60)
    assert probe.stdout.split() == ["0=5", "1=none", "2=6", "3=6"], probe   # list 1's item 5 is not list 0's
    # The whole audit, `Items` itself, over two sibling lists: the left walked and holds 5; the right walked 7
    # and 8 and holds them and an unwalked 5, its own counter's, the left row's id.
    items = "function Items(" + script.split("function Items(", 1)[1].split("\nfunction ", 1)[0]
    audit = subprocess.run([str(desktop_driver.POWERSHELL), "-NoProfile", "-Command",
                            "Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes\n" + rid + "\n" + items + "\n"
                            "function Enter($what) { }\n$MAX_ITEMS = 100\n"
                            "function El($id) { $p = [pscustomobject]@{}; $p | Add-Member NoteProperty k $id; "
                            "$p | Add-Member ScriptMethod GetRuntimeId { ,@($this.k) }; $p }\n"
                            "function List([object[]]$held) { $c = [pscustomobject]@{ held = $held }; "
                            "$c | Add-Member ScriptMethod FindItemByProperty { param($after, $p, $v) "
                            "$i = if ($null -eq $after) { 0 } else { [array]::IndexOf($this.held, $after) + 1 }; "
                            "if ($i -lt $this.held.Count) { $this.held[$i] } else { $null } }; "
                            "$e = [pscustomobject]@{ c = $c }; $e | Add-Member ScriptMethod GetCurrentPattern "
                            "{ param($x) $this.c }; $e }\n"
                            "$left, $right = @{}, @{}\n"
                            "$script:lists = @(, @((List @((El 5))), $left)) + , @((List @((El 7), (El 5), (El 8))), $right)\n"
                            "$script:walked = New-Object 'System.Collections.Generic.List[object]'\n"
                            "$script:walked.Add(@((El 5), @(0))); $script:walked.Add(@((El 7), @(1)))\n"
                            "$script:walked.Add(@((El 8), @(1)))\n"
                            "Items 's00'\n\"left=$($left.items)/$($left.unread) right=$($right.items)/$($right.unread)\""],
                           capture_output=True, text=True, timeout=60)
    assert audit.stdout.split() == ["left=1/0", "right=3/1"], audit
    nodes = script.split("function Nodes(", 1)[1].split("\nfunction ", 1)[0]
    assert nodes.index("$script:walked.Add(@($element, $script:under))") < nodes.index("$script:under = @($outer) +")
    assert nodes.rstrip().endswith("$script:under = $outer\n}") or nodes.rstrip().endswith("$script:under = $outer")


def test_a_value_that_is_not_whole_utf16_is_read_as_unknown_never_with_a_replacement(tmp_path):
    script = tmp_path / "form.ps1"
    # The app holds a lone surrogate itself: a request's JSON would carry one only as U+FFFD.
    script.write_text(WPF_FORM.replace('$A::SetName($box, "원문")',
                                       '$A::SetName($box, "원문"); $box.Text = "apple " + [char]0xD800 + "river"'),
                      encoding="utf-8")
    with desktop_session.look(tmp_path, "surrogate") as look:
        look.start("app", [str(desktop_driver.POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(script)], tmp_path)
        report = desktop_driver.drive(look, "app", (400, 300), [
            {"name": "one", "action": "toggle", "id": "translate-on"}], wait=30, settle_ms=300)
    box = [n for n in report["states"][-1]["nodes"] if n.get("id") == "source"]
    assert [(n.get("value"), n.get("unvalued")) for n in box] == [(None, True)], report


def test_a_postcondition_holds_on_one_element_with_every_property_it_names():
    state = {"nodes": [{"id": "a", "name": "번역", "value": "", "toggle": "On"}, {"id": "b", "value": "x"},
                       {"id": "c", "toggle": "On", "foreign": True}]}
    assert desktop_driver.holds(state, {"id": "a", "toggle": "On"}) and desktop_driver.holds(state, {"id": "a", "value": ""})
    assert not desktop_driver.holds(state, {"id": "a", "value": "x"})   # `x` is another element's
    assert not desktop_driver.holds(state, {"id": "c", "toggle": "On"})   # another process's element
    assert desktop_driver.holds(state, {"name": "번역"}) and not desktop_driver.holds(state, {"value": "x"})   # no id, no name


def test_a_postcondition_in_a_window_reads_the_one_element_there_its_id_names():
    on = {"id": "t", "toggle": "On"}
    state = {"windows": [{"hwnd": 1, "owner": 0}, {"hwnd": 2, "owner": 1}],
             "nodes": [{"window": 1, "id": "t", "toggle": "Off"}, {"window": 2, "id": "t", "toggle": "On"}]}
    report = {"main": {"hwnd": 1}}
    assert desktop_driver.holds(state, on) and not desktop_driver.holds(state, on, desktop_driver.scope(report, state, "main"))
    assert desktop_driver.holds(state, on, desktop_driver.scope(report, state, "owned"))
    twice = {**state, "nodes": [*state["nodes"], {"window": 2, "id": "t", "toggle": "On"}]}
    assert not desktop_driver.holds(twice, on, 2)   # two elements carry the id: which one is unproven
    assert desktop_driver.scope(report, {**state, "windows": state["windows"][:1]}, "owned") is None


def test_a_result_that_cannot_be_saved_still_returns_the_unproven_end(tmp_path, monkeypatch):
    look = types.SimpleNamespace(folder=tmp_path, jobs={"app": types.SimpleNamespace(name="job"), "driver": None},
                                 check_key=lambda key: None, start=lambda *a, **k: None)
    (tmp_path / "evidence").mkdir()
    monkeypatch.setattr(desktop_driver, "_watch", lambda *a: (None, {"clean": False, "reasons": ["pid 3 runs"]}))

    def full(path, record):
        raise OSError(28, "the disk is full")

    monkeypatch.setattr(desktop_driver, "write_record", full)
    report = desktop_driver.drive(look, "app", (640, 480), [])
    assert report["end"] == {"clean": False, "reasons": ["pid 3 runs"]} and "disk is full" in report["unsaved"]
    assert desktop_driver.findings(report)[:2] == ["the driver's end was not proven: pid 3 runs",
                                                   "the driver's result could not be saved: [Errno 28] the disk is full"]


def test_a_driver_that_exits_before_any_report_leaves_a_finding(tmp_path, monkeypatch):
    report, _ = stand_in(tmp_path, monkeypatch, [])

    assert desktop_driver.findings(report) == ["the driver left no report"]


def test_a_driver_that_saved_done_but_lingers_past_its_deadline_is_a_finding_in_the_saved_result(tmp_path, monkeypatch):
    report, _ = stand_in(tmp_path, monkeypatch, [["report", DONE], ["op", ["done", 120]]])

    assert report["phase"] == "done" and report["ended"] == "done" and report["end"]["clean"]
    assert desktop_driver.findings(report) == ["the driver passed its deadline at done"]


def test_an_unproven_end_of_the_driver_is_a_finding_in_the_saved_result(tmp_path, monkeypatch):
    real = owned_job.Job.end

    def unproven(job, wait=30.0):   # the driver's end only: the look's own cleanup asks with another wait
        proof = real(job, wait)
        return {**proof, "clean": False, "reasons": ["no exit proven for pid 1"]} if wait == 4 else proof

    monkeypatch.setattr(desktop_driver, "ENDED_SECONDS", 4)
    monkeypatch.setattr(owned_job.Job, "end", unproven)
    report, _ = stand_in(tmp_path, monkeypatch, [["report", DONE], ["op", ["done", 120]]])

    assert desktop_driver.findings(report) == ["the driver passed its deadline at done",
                                               "the driver's end was not proven: no exit proven for pid 1"]


def test_an_expectation_naming_neither_an_id_nor_a_name_is_a_finding_of_its_step():
    report = {**DONE, "states": [START, {**START, "id": "s01", "name": "after-go"}],
              "steps": [{"name": "go", "acted": True, "state": "s01", "expect": {"id": ""}},
                        {"name": "set", "acted": True, "state": "s01", "expect": {"id": "", "name": "fixture"}}]}

    assert desktop_driver.findings(report) == ["step go: it names no postcondition"]


def test_a_driver_that_exits_after_an_intermediate_report_leaves_a_finding(tmp_path, monkeypatch):
    report, _ = stand_in(tmp_path, monkeypatch, [["op", ["read size", 0]], ["report", {**DONE, "phase": "read size"}]])

    assert report["ended"] is None
    assert desktop_driver.findings(report) == ["the driver stopped before finishing, at read size"]


def test_a_key_that_is_not_one_plain_component_is_refused_before_any_path(tmp_path):
    with desktop_session.look(tmp_path, "keys") as look:
        look.start("app", python(NONE), tmp_path)
        evidence = sorted(path.name for path in (look.folder / "evidence").iterdir())
        folders = list(look.record["folders"])
        for key in ("..", r"..\outside", str(tmp_path / "rooted"), "app", "APP"):
            with pytest.raises(ValueError):
                desktop_driver.drive(look, "app", (640, 480), [], key=key)
        for key in ("..", r"..\outside", str(tmp_path / "rooted")):
            with pytest.raises(ValueError):
                look.reserve(key)

        assert sorted(path.name for path in (look.folder / "evidence").iterdir()) == evidence
        assert look.record["folders"] == folders
    assert not (tmp_path / "rooted").exists() and not (tmp_path / "outside").exists()


def node(**fields):
    return {"window": 1, "depth": 1, "type": "Button", "name": "Go", "id": "go", "enabled": True,
            "offscreen": False, "rect": [10, 10, 40, 30], "patterns": ["Invoke"], **fields}


def test_the_mechanical_findings_read_off_a_report():
    report = {"phase": "done", "size": {"asked": [800, 600], "moved": True, "frame": [0, 0, 786, 593]},
              "states": [{"name": "start", "windows": [{"hwnd": 1, "frame": [0, 0, 800, 600]}],
                          "shots": [{"hwnd": 1, "drawn": True}], "reads": [{"hwnd": 1, "read": True}], "nodes": [
                  node(depth=0, type="Window", rect=[-7, 0, 814, 607], patterns=[]),   # the window itself
                  {"window": 1, "depth": 2, "foreign": True, "pid": 9},   # another process's fragment: not checked
                  node(),
                  node(name="", id="blank"),
                  node(id="small", rect=[10, 10, 11, 11]),
                  node(id="outside", rect=[790, 10, 40, 30]),
                  node(id="hidden", rect=[900, 900, 1, 1], offscreen=True),
                  node(type="Text", name="", id="label", patterns=[], rect=[0, 0, 5, 5]),
                  node(type="Edit", name="", id="hidden-edit", rect=None, patterns=["Value"])]}],
              "steps": [{"name": "go", "error": None, "acted": True, "state": "s01", "expect": {"name": "x"}},
                        {"name": "set", "error": "2 elements match name"},
                        {"name": "late", "error": None, "acted": False},
                        {"name": "lost", "error": None, "acted": True, "state": None},
                        {"name": "dangling", "error": None, "acted": True, "state": "s09", "expect": {"id": "go"},
                         "postcondition": True},   # a flag the report claims, with no state behind it
                        {"name": "blind", "error": None, "acted": True, "state": "s02", "expect": None},
                        {"name": "fine", "error": None, "acted": True, "state": "s03", "expect": {"id": "go"}}]}
    report["states"] += [step_state(state) for state in ("s01", "s02", "s03")]

    assert desktop_driver.findings(report) == [
        "the main window is 786x593, not 800x600",
        "start: Button 'blank' has no name",
        "start: Button 'small' is 11x11, under 24x24",
        "start: Button 'outside' lies outside its window",
        "step go: its postcondition was not read",
        "step set: 2 elements match name",
        "step late: it was not performed",
        "step lost: its state was not read",
        "step dangling: its state was not read",
        "step blind: it names no postcondition",
    ]


def step_state(state):
    """A state a step left, fully observed: one window, drawn and read, holding the `go` button."""

    return {"id": state, "name": f"after-{state}", "windows": [{"hwnd": 1, "frame": [0, 0, 800, 600]}],
            "shots": [{"hwnd": 1, "drawn": True}], "reads": [{"hwnd": 1, "read": True}], "nodes": [node()]}


def test_a_postcondition_is_read_only_off_the_apps_own_nodes():
    owned = {"window": 1, "depth": 1, "foreign": False, "name": "ready", "id": "line"}
    foreign = {"window": 1, "depth": 1, "foreign": True, "pid": 9, "name": "clicked", "id": "line"}
    state = {"id": "s01", "nodes": [owned, foreign]}

    assert not desktop_driver.holds(state, {"name": "clicked"}) and desktop_driver.holds(state, {"name": "ready"})


def test_a_missed_or_malformed_progress_marker_keeps_the_operation_and_its_clock(monkeypatch):
    now = [0.0]
    malformed = [None, {"seq": "torn"}, {"seq": 2}, {"seq": 3, "op": None}, {"seq": 4, "op": " "}, [1], 5,
                 {"seq": True, "op": "x"}, {"seq": -1, "op": "x"}, "resize"]
    reads = iter([{"seq": 1, "op": "resize"}, *malformed] + [{"seq": 1, "op": "resize"}] * 20)
    monkeypatch.setattr(desktop_driver, "_read", lambda file: next(reads))
    monkeypatch.setattr(desktop_driver, "time", types.SimpleNamespace(
        monotonic=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + 1)))

    class Job:
        def finished(self):
            return False

        def end(self, wait):
            return {"clean": True, "reasons": []}

    ended, end = desktop_driver._watch(Job(), None, 30, 0)

    assert ended == "resize" and end["clean"] and now[0] == 11   # past 10 s from the marker seen at 0


def test_what_a_report_could_not_observe_is_a_finding():
    report = {"phase": "done", "size": {"asked": [800, 600], "moved": True, "frame": None},
              "states": [{"name": "start", "windows": [{"hwnd": 1, "frame": [0, 0, 800, 600]}],
                          "shots": [{"hwnd": 1, "drawn": False, "error": "PrintWindow drew nothing"}], "nodes": []},
                         {"name": "after-go", "windows": [], "shots": [], "nodes": []},
                         {"name": "after-set", "windows": [{"hwnd": 2, "frame": [0, 0, 9, 9]}, {"hwnd": 3, "frame": [0, 0, 9, 9]}],
                          "shots": [{"hwnd": 2, "drawn": True}, {"hwnd": 3, "drawn": True}],
                          "reads": [{"hwnd": 2, "read": False, "error": "the window is gone or no longer the app's"}],
                          "nodes": []}],
              "steps": [], "end": {"clean": False, "reasons": ["no exit proven for pid 7"]}}

    assert desktop_driver.findings(report) == [
        "the driver's end was not proven: no exit proven for pid 7",
        "the main window's size was not read back",
        "start: window 1 was not drawn: PrintWindow drew nothing",
        "after-go: no app window to shoot",
        "after-set: window 2's tree was not read: the window is gone or no longer the app's",
        "after-set: window 3's tree was not read: no read was recorded",
    ]
    assert "no start state was read" in desktop_driver.findings({**report, "states": []})
    assert desktop_driver.findings({**report, "phase": "act go", "last": "act go", "error": "boom", "end": None,
                                    "size": {"asked": [800, 600], "moved": False, "frame": [0, 0, 800, 600]},
                                    "states": []}) == [
        "the driver failed at act go: boom", "the main window could not be sized"]


def test_the_request_names_the_apps_job_not_its_pids(tmp_path, monkeypatch):
    seen = {}

    def starting(look, key, argv, cwd, extra=None, halt=None):
        seen.update(json.loads((look.folder / "evidence" / key / "request.json").read_text(encoding="utf-8")))
        raise desktop_session.Refused("stop here")

    with desktop_session.look(tmp_path, "request") as look:
        look.start("app", python(NONE), tmp_path)
        name = look.jobs["app"].name
        monkeypatch.setattr(desktop_session.Look, "start", starting)
        with pytest.raises(desktop_session.Refused, match="stop here"):
            desktop_driver.drive(look, "app", (640, 480), [{"name": "go", "action": "invoke", "id": "go"}])

    assert seen["job"] == name and "pids" not in seen
    assert seen["window"] == [640, 480] and seen["steps"][0]["id"] == "go"


EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
FIXTURE = desktop_driver.Path(__file__).parent.parent / "docs" / "research" / "oneshot-desktop-edge-run" / "fixture.html" \
    if sys.platform == "win32" else None


@pytest.mark.skipif(sys.platform != "win32" or not __import__("os").environ.get("WIKI_DESKTOP_EDGE"),
                    reason="a real Edge run, on request: WIKI_DESKTOP_EDGE=1")
def test_the_edge_fixture_is_driven_through_a_pattern_and_its_cleanup_is_proven(tmp_path):
    with desktop_session.look(tmp_path, "edge") as look:
        profile = look.reserve("edge")
        look.start("app", [EDGE, f"--user-data-dir={profile}", "--no-first-run", "--lang=en-US", "--force-renderer-accessibility",
                           "--window-size=800,600", f"--app={FIXTURE.as_uri()}"], tmp_path)
        report = desktop_driver.drive(look, "app", (800, 600),   # one label twice: each state keeps its own shot
                                      [{"name": "go", "action": "invoke", "id": "go", "expect": {"name": "clicked 1"}},
                                       {"name": "go", "action": "invoke", "id": "go", "expect": {"name": "clicked 2"}}],
                                      settle_ms=1500)
        shots = [look.folder / "evidence" / "driver" / state["shots"][0]["file"] for state in report["states"][1:]]
        first, second = (shot.read_bytes() for shot in shots)

    record = desktop_session.load(tmp_path / "edge")
    saved = json.loads((tmp_path / "edge" / "evidence" / "driver" / "result.json").read_text(encoding="utf-8"))
    found = desktop_driver.findings(report)
    assert report["phase"] == "done" and not [line for line in found if line.startswith("step")], found
    assert desktop_driver.findings(saved) == found
    assert shots[0] != shots[1] and first != second
    assert record["state"] == "proven" and record["proof"]["clean"], record["proof"]
    found = desktop_driver.findings(report)
    assert "start: Button 'tiny' is 11x11, under 24x24" in found and "start: Button 'tiny' has no name" in found
    assert record["jobs"]["app"]["proof"]["total"] == record["jobs"]["app"]["proof"]["joined"] > 1
