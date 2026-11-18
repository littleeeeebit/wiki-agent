param([Parameter(Mandatory)][string]$Request)
# The desktop look's driver (docs/research/oneshot-desktop-session.md, "Reading and driving it"). It runs as its own
# Windows PowerShell 5.1 process on the look's owned desktop, in a job of its own, so the server can end it when a
# deadline passes. It chooses the app's main window once, sizes it without activation, and then for each state
# shoots every visible top-level window of the app with PrintWindow and reads the UI Automation control view. It
# acts only through patterns and sends no synthetic input, no window message and no clipboard. Its report is
# rewritten after every phase, so a driver ended at a deadline still leaves what it saw.
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text; using System.Collections.Generic;
public static class Desk {
  public delegate bool Visit(IntPtr h, IntPtr l);
  [DllImport("user32")] public static extern bool SetProcessDpiAwarenessContext(IntPtr c);
  [DllImport("user32")] public static extern bool EnumWindows(Visit v, IntPtr l);
  [DllImport("user32")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32")] public static extern IntPtr GetWindow(IntPtr h, uint cmd);
  [DllImport("user32")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
  [DllImport("user32")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
  [DllImport("user32", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("dwmapi")] public static extern int DwmGetWindowAttribute(IntPtr h, int a, out RECT r, int size);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  public static List<IntPtr> Visible() {
    var found = new List<IntPtr>();
    EnumWindows((h, l) => { if (IsWindowVisible(h)) found.Add(h); return true; }, IntPtr.Zero);
    return found;
  }
  public static uint Owner(IntPtr h) { uint p; GetWindowThreadProcessId(h, out p); return p; }
  public static string Cls(IntPtr h) { var s = new StringBuilder(256); GetClassName(h, s, 256); return s.ToString(); }
  public static string Title(IntPtr h) { var s = new StringBuilder(512); GetWindowText(h, s, 512); return s.ToString(); }
  public static int[] Frame(IntPtr h) {   // DWMWA_EXTENDED_FRAME_BOUNDS: physical pixels, whatever the caller's DPI
    RECT r; if (DwmGetWindowAttribute(h, 9, out r, Marshal.SizeOf(typeof(RECT))) != 0) return null;
    return new int[] { r.L, r.T, r.R - r.L, r.B - r.T };
  }
}
"@
[void][Desk]::SetProcessDpiAwarenessContext([IntPtr]-4)   # per-monitor v2: UI Automation answers in physical pixels

$ask = Get-Content -Raw -Encoding UTF8 $Request | ConvertFrom-Json
$out = $ask.out
$report = [ordered]@{ phase = "starting"; main = $null; size = $null; states = @(); steps = @(); refusal = $null }
$UTILITY = @("UAC_InputIndicatorOverlayWnd", "UAC Input Indicator", "IME", "MSCTFIME UI")

function Save {
  $part = Join-Path $out "driver.part"
  $report | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 $part
  for ($try = 1; ; $try++) {   # the server may be reading the last report: a replace waits for it, briefly
    try { Move-Item -Force $part (Join-Path $out "driver.json"); return }
    catch { if ($try -ge 100) { throw }; Start-Sleep -Milliseconds 20 }
  }
}

function Refuse([string]$why) { $report.refusal = $why; $report.phase = "refused"; Save; exit 3 }

function Windows {   # every visible top-level window an app process owns, input indicators set apart
  $pids = @($ask.pids | ForEach-Object { [uint32]$_ })
  @([Desk]::Visible() | Where-Object { $pids -contains [Desk]::Owner($_) } | ForEach-Object {
    [ordered]@{ hwnd = [int64]$_; pid = [Desk]::Owner($_); class = [Desk]::Cls($_); title = [Desk]::Title($_);
                frame = [Desk]::Frame($_); utility = $UTILITY -contains [Desk]::Cls($_) } })
}

function Shoot($window, [string]$state) {   # the whole window, then cut to its visible frame
  $h, $frame = [IntPtr]$window.hwnd, [Desk]::Frame([IntPtr]$window.hwnd)
  $outer = New-Object Desk+RECT; [void][Desk]::GetWindowRect($h, [ref]$outer)
  $bmp = New-Object System.Drawing.Bitmap ([Math]::Max(1, $outer.R - $outer.L)), ([Math]::Max(1, $outer.B - $outer.T))
  $g = [System.Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc()
  try { $drawn = [Desk]::PrintWindow($h, $dc, 2) } finally { $g.ReleaseHdc($dc); $g.Dispose() }   # PW_RENDERFULLCONTENT
  $cut = New-Object System.Drawing.Rectangle ($frame[0] - $outer.L), ($frame[1] - $outer.T), $frame[2], $frame[3]
  $visible = $bmp.Clone($cut, $bmp.PixelFormat); $bmp.Dispose()
  $file = "$state-$($window.hwnd).png"
  $visible.Save((Join-Path $out $file)); $visible.Dispose()
  [ordered]@{ hwnd = $window.hwnd; drawn = $drawn; file = $file; size = @($frame[2], $frame[3]) }
}

function Nodes([System.Windows.Automation.AutomationElement]$element, [int64]$hwnd, [int]$depth = 0) {
  $c = $element.Current; $b = $c.BoundingRectangle
  $script:nodes += [ordered]@{ window = $hwnd; depth = $depth; type = $c.ControlType.ProgrammaticName -replace "ControlType\.", "";
    name = $c.Name; id = $c.AutomationId; enabled = $c.IsEnabled; offscreen = $c.IsOffscreen;
    rect = if ($b.IsEmpty) { $null } else { @([int]$b.X, [int]$b.Y, [int]$b.Width, [int]$b.Height) };
    patterns = @($element.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName -replace "PatternIdentifiers\.Pattern", "" }) }
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $child = $walker.GetFirstChild($element)
  while ($child) { Nodes $child $hwnd ($depth + 1); $child = $walker.GetNextSibling($child) }
}

function State([string]$name) {   # every window of the app in this state, shot and read
  $windows = @(Windows | Where-Object { -not $_.utility })
  $state = [ordered]@{ name = $name; windows = $windows; shots = @(); nodes = @() }
  foreach ($w in $windows) {
    $state.shots += Shoot $w $name
    $script:nodes = @()
    Nodes ([System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$w.hwnd)) $w.hwnd
    $state.nodes += $script:nodes
  }
  $report.states += $state; $report.phase = "read $name"; Save
  $state
}

function Target($step) {   # by AutomationId within the main window, or by a name only one element carries
  $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$report.main.hwnd)
  $A = [System.Windows.Automation.AutomationElement]
  $property, $value = if ($step.id) { $A::AutomationIdProperty, $step.id } else { $A::NameProperty, $step.control }
  $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                       (New-Object System.Windows.Automation.PropertyCondition $property, $value))
  if ($all.Count -ne 1) { return [ordered]@{ error = "$($all.Count) elements match $value" } }
  [ordered]@{ element = $all[0] }
}

function Act($element, $step) {
  switch ($step.action) {
    "invoke" { $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() }
    "set_value" { $element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue([string]$step.value) }
    "toggle" { $element.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle() }
    "select" { $element.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select() }
    "expand" { $element.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Expand() }
    "collapse" { $element.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Collapse() }
    "scroll_into_view" { $element.GetCurrentPattern([System.Windows.Automation.ScrollItemPattern]::Pattern).ScrollIntoView() }
    default { throw "the action $($step.action) is not one the driver performs" }
  }
}

# The main window, chosen once: the one visible top-level window of an app process that is not an input indicator.
$deadline = (Get-Date).AddSeconds([double]$ask.wait_seconds)
do { $candidates = @(Windows | Where-Object { -not $_.utility }); if ($candidates.Count) { break }
     Start-Sleep -Milliseconds 200 } while ((Get-Date) -lt $deadline)
if ($candidates.Count -eq 0) { Refuse "the app showed no window on the owned desktop" }
Start-Sleep -Milliseconds ([int]$ask.settle_ms)
$candidates = @(Windows | Where-Object { -not $_.utility })
if ($candidates.Count -ne 1) { $report.candidates = $candidates; Refuse "$($candidates.Count) candidate main windows at startup" }
$report.main = $candidates[0]

# Sized to S1's window without activation or a z-order change, and read back.
$width, $height = [int]$ask.window[0], [int]$ask.window[1]
# SetWindowPos sizes the window with its invisible resize borders; the frame read back is without them.
$outer = New-Object Desk+RECT; [void][Desk]::GetWindowRect([IntPtr]$report.main.hwnd, [ref]$outer)
$inner = [Desk]::Frame([IntPtr]$report.main.hwnd)
$extraW, $extraH = (($outer.R - $outer.L) - $inner[2]), (($outer.B - $outer.T) - $inner[3])
$moved = [Desk]::SetWindowPos([IntPtr]$report.main.hwnd, [IntPtr]::Zero, 0, 0, ($width + $extraW), ($height + $extraH), 0x0014)
Start-Sleep -Milliseconds ([int]$ask.settle_ms)
$frame = [Desk]::Frame([IntPtr]$report.main.hwnd)
$report.size = [ordered]@{ asked = @($width, $height); moved = $moved; frame = $frame }
$report.phase = "sized"; Save

[void](State "start")
foreach ($step in $ask.steps) {
  $done = [ordered]@{ name = $step.name; action = $step.action; error = $null; postcondition = $null }
  $report.steps += $done; $report.phase = "step $($step.name)"; Save
  $found = Target $step
  if ($found.error) { $done.error = $found.error; Save; continue }
  try { Act $found.element $step } catch { $done.error = "$($_.Exception.Message)"; Save; continue }
  Start-Sleep -Milliseconds ([int]$ask.settle_ms)
  $state = State "after-$($step.name)"
  if ($step.expect) {   # the step's postcondition, read from the tree: an element with this id or name exists
    $key = if ($step.expect.id) { "id" } else { "name" }
    $done.postcondition = [bool]@($state.nodes | Where-Object { $_[$key] -eq $step.expect.$key }).Count
  }
  Save
}
$report.phase = "done"; Save
