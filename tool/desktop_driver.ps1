param([Parameter(Mandatory)][string]$Request)
# The desktop look's driver (docs/research/oneshot-desktop-session.md, "Reading and driving it"). It runs as its own
# Windows PowerShell 5.1 process on the look's owned desktop, in a job of its own, so the server can end it when a
# deadline passes. It chooses the app's main window once, sizes it without activation, and then for each state
# shoots every visible top-level window of the app with PrintWindow and reads the UI Automation control view. It
# acts only through patterns and sends no synthetic input, no window message and no clipboard.
#
# The app is its job, not a list of pids: a window belongs to the app when its process is in the app's job now, so
# a child started later counts and a recycled pid does not. Every use of a window first proves it is still that
# window: alive, on this desktop, owned by the same process of the app's job, and of the same class.
#
# Before each operation the driver names it in `progress.json` with a fresh sequence number, so the server holds
# every operation to a deadline of its own. The report, `driver.json`, is saved at each boundary, and an error
# that stops the driver is saved in it with the operation it stopped.
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
  [DllImport("kernel32", CharSet=CharSet.Unicode, SetLastError=true)] public static extern IntPtr OpenJobObjectW(uint a, bool i, string name);
  [DllImport("kernel32")] public static extern IntPtr OpenProcess(uint a, bool i, uint pid);
  [DllImport("kernel32")] public static extern bool IsProcessInJob(IntPtr p, IntPtr job, out bool inside);
  [DllImport("kernel32")] public static extern bool CloseHandle(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  public static IntPtr App = IntPtr.Zero;   // the app's job, opened for query only
  public static List<IntPtr> Visible() {   // EnumWindows sees this thread's desktop only: the owned one
    var found = new List<IntPtr>();
    EnumWindows((h, l) => { if (IsWindowVisible(h)) found.Add(h); return true; }, IntPtr.Zero);
    return found;
  }
  public static uint Owner(IntPtr h) { uint p; GetWindowThreadProcessId(h, out p); return p; }
  public static string Cls(IntPtr h) { var s = new StringBuilder(256); GetClassName(h, s, 256); return s.ToString(); }
  public static string Title(IntPtr h) { var s = new StringBuilder(512); GetWindowText(h, s, 512); return s.ToString(); }
  public static bool InApp(uint pid) {   // this pid is, right now, a process of the app's job
    IntPtr p = OpenProcess(0x1000, false, pid);   // PROCESS_QUERY_LIMITED_INFORMATION
    if (p == IntPtr.Zero) return false;
    try { bool inside; return IsProcessInJob(p, App, out inside) && inside; } finally { CloseHandle(p); }
  }
  public static bool Same(IntPtr h, uint pid, string cls) {   // still that window, on this desktop, of the app
    return Visible().Contains(h) && Owner(h) == pid && Cls(h) == cls && InApp(pid);
  }
  public static int[] Frame(IntPtr h) {   // DWMWA_EXTENDED_FRAME_BOUNDS: physical pixels, whatever the caller's DPI
    RECT r; if (DwmGetWindowAttribute(h, 9, out r, Marshal.SizeOf(typeof(RECT))) != 0) return null;
    return new int[] { r.L, r.T, r.R - r.L, r.B - r.T };
  }
}
"@
[void][Desk]::SetProcessDpiAwarenessContext([IntPtr]-4)   # per-monitor v2: UI Automation answers in physical pixels

$ask = Get-Content -Raw -Encoding UTF8 $Request | ConvertFrom-Json
$out = $ask.out
$report = [ordered]@{ phase = "starting"; main = $null; size = $null; states = @(); steps = @(); refusal = $null; error = $null }
$UTILITY = @("UAC_InputIndicatorOverlayWnd", "UAC Input Indicator", "IME", "MSCTFIME UI")
$MAX_ITEMS = 2000   # a list's items counted at most: more is an unknown count, so its tree is not taken as whole
$clock = [System.Diagnostics.Stopwatch]::StartNew()   # each state's `at` and `done`, each step's `acted_at`, in ms
$MAX_WAIT_MS = 120000   # a control's wait at most: the driver's `MAX_WAIT_MS`
$MAX_VALUE = 1000   # a control's value recorded at most, in characters: a check's expected value is no longer
$script:seq = 0

function Put([string]$name, $value) {   # UTF-8 without a byte order mark, replaced whole
  $part = Join-Path $out "$name.part"
  [System.IO.File]::WriteAllText($part, ($value | ConvertTo-Json -Depth 12), (New-Object System.Text.UTF8Encoding $false))
  for ($try = 1; ; $try++) {   # the server may be reading the last copy: a replace waits for it, briefly
    try { Move-Item -Force $part (Join-Path $out $name); return }
    catch { if ($try -ge 100) { throw }; Start-Sleep -Milliseconds 20 }
  }
}

function Save { Put "driver.json" $report }

function Enter([string]$op) {   # the next operation, named before it starts: its deadline starts here
  $script:seq++; $report.phase = $op
  Put "progress.json" ([ordered]@{ seq = $script:seq; op = $op })
}

function Settle { Enter "settle"; Start-Sleep -Milliseconds ([int]$ask.settle_ms) }

function Refuse([string]$why) { $report.refusal = $why; $report.phase = "refused"; Save; exit 3 }

function Current($window) { [Desk]::Same([IntPtr]$window.hwnd, [uint32]$window.pid, [string]$window.class) }

function Main {   # the main window, proven still the one chosen before every use
  if (-not (Current $report.main)) { Refuse "the main window is gone or no longer the app's" }
  [IntPtr]$report.main.hwnd
}

function Windows {   # every visible top-level window of the app's job on this desktop, input indicators set apart
  @([Desk]::Visible() | Where-Object { [Desk]::InApp([Desk]::Owner($_)) } | ForEach-Object {
    [ordered]@{ hwnd = [int64]$_; pid = [Desk]::Owner($_); class = [Desk]::Cls($_); title = [Desk]::Title($_);
                frame = [Desk]::Frame($_); utility = $UTILITY -contains [Desk]::Cls($_);
                owner = [int64][Desk]::GetWindow($_, 4) } })   # GW_OWNER: the window that owns it, or 0
}

function Candidates {   # a main window: not an input indicator, a menu (#32768) or a window another one owns
  @(Windows | Where-Object { -not $_.utility -and -not $_.owner -and $_.class -ne "#32768" })
}

function Shoot($window, [string]$state) {   # the whole window, then cut to its visible frame
  $h = [IntPtr]$window.hwnd
  if (-not (Current $window)) { return [ordered]@{ hwnd = $window.hwnd; drawn = $false; error = "the window is gone or no longer the app's" } }
  $frame = [Desk]::Frame($h); $outer = New-Object Desk+RECT
  if (-not $frame -or -not [Desk]::GetWindowRect($h, [ref]$outer)) {
    return [ordered]@{ hwnd = $window.hwnd; drawn = $false; error = "its frame could not be read" }
  }
  $bmp = New-Object System.Drawing.Bitmap ([Math]::Max(1, $outer.R - $outer.L)), ([Math]::Max(1, $outer.B - $outer.T))
  try {
    $g = [System.Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc()
    try { $drawn = [Desk]::PrintWindow($h, $dc, 2) } finally { $g.ReleaseHdc($dc); $g.Dispose() }   # PW_RENDERFULLCONTENT
    if (-not $drawn) { return [ordered]@{ hwnd = $window.hwnd; drawn = $false; error = "PrintWindow drew nothing" } }
    $cut = New-Object System.Drawing.Rectangle ($frame[0] - $outer.L), ($frame[1] - $outer.T), $frame[2], $frame[3]
    $visible = $bmp.Clone($cut, $bmp.PixelFormat)
  } finally { $bmp.Dispose() }
  $file = "$state-$($window.hwnd).png"
  try { $visible.Save((Join-Path $out $file)) } finally { $visible.Dispose() }
  [ordered]@{ hwnd = $window.hwnd; drawn = $true; file = $file; size = @($frame[2], $frame[3]) }
}

function Rid($element) { ($element.GetRuntimeId()) -join "." }   # the element's identity while it lives

function Items([string]$at) {   # each open item container's items, realized or not, against the nodes the walk read
  # `items`: how many its provider holds; `unread`: how many of them the walk did not read, null when the
  # provider will not say, holds more than the driver counts, or the container (an expanded tree item) has none.
  # The walk's identities, then each container, are operations of their own: none grows with the whole tree.
  if (-not $script:lists.Count) { return }
  Enter "identify $at"
  $seen = New-Object 'System.Collections.Generic.HashSet[string]'
  foreach ($e in $script:walked) { try { [void]$seen.Add((Rid $e)) } catch { } }   # unread: its items stay unread
  $n = 0
  foreach ($pair in $script:lists) {
    $element, $node = $pair
    Enter "items $at $(($n++))"
    $node.items, $node.unread = $null, $null
    try {
      $container = $element.GetCurrentPattern([System.Windows.Automation.ItemContainerPattern]::Pattern)
      $count, $unread, $item = 0, 0, $null
      do {   # one call past the bound: a container of exactly $MAX_ITEMS is seen to end
        $item = $container.FindItemByProperty($item, $null, $null)
        if ($item) {
          $count++
          try { if (-not $seen.Contains((Rid $item))) { $unread++ } } catch { $unread++ }
        }
      } while ($item -and $count -le $MAX_ITEMS)
      if (-not $item) { $node.items, $node.unread = $count, $unread }
    } catch { }
  }
}

function Nodes([System.Windows.Automation.AutomationElement]$element, [int64]$hwnd, [int]$depth = 0, [bool]$scrolled = $false) {
  # `scrolled`: inside a container that scrolls, so a person reaches what lies past its edge by scrolling.
  # The process first, and nothing else of an element outside the app's job: an embedded fragment of another
  # process is marked by its pid alone and never walked. `Current` reads each property when it is asked for.
  $c = $element.Current; $owner = $c.ProcessId
  if (-not [Desk]::InApp([uint32]$owner)) {
    $script:nodes += [ordered]@{ window = $hwnd; depth = $depth; foreign = $true; pid = $owner }
    return
  }
  # A window the app owns shows under its owner too: it is read as a top-level window of its own, against its frame.
  if ($depth -gt 0 -and $script:tops -contains [int64]$c.NativeWindowHandle) { return }
  $b = $c.BoundingRectangle
  $patterns = @($element.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName -replace "PatternIdentifiers\.Pattern", "" })
  $node = [ordered]@{ window = $hwnd; depth = $depth; foreign = $false; type = $c.ControlType.ProgrammaticName -replace "ControlType\.", "";
    name = $c.Name; id = $c.AutomationId; enabled = $c.IsEnabled; offscreen = $c.IsOffscreen;
    rect = if ($b.IsEmpty) { $null } else { @([int]$b.X, [int]$b.Y, [int]$b.Width, [int]$b.Height) };
    patterns = $patterns; scrolled = $scrolled }
  # What a check's postcondition may read beside the name: the value, null past $MAX_VALUE characters, so a cut
  # one never matches; and the toggle state.
  if ($patterns -contains "Value") {
    try {
      $v = [string]$element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value
      $node.value = if ($v.Length -gt $MAX_VALUE) { $null } else { $v }
    } catch { $node.value = $null }
  }
  if ($patterns -contains "Toggle") {
    try { $node.toggle = [string]$element.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Current.ToggleState }
    catch { $node.toggle = $null }
  }
  $script:walked.Add($element)   # its identity is read by `Items`, under that operation's own deadline
  $open = $patterns -notcontains "ExpandCollapse"   # a container that expands holds its items only while expanded
  if (-not $open) {
    try {
      $open = $element.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Current.ExpandCollapseState -in
        @([System.Windows.Automation.ExpandCollapseState]::Expanded, [System.Windows.Automation.ExpandCollapseState]::PartiallyExpanded)
    } catch { $open = $true }   # its state unread: its items are owed
  }
  if ($open -and ($patterns -contains "ItemContainer" -or $node.type -eq "TreeItem")) { $script:lists += , @($element, $node) }
  $script:nodes += $node
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $inner = $scrolled -or $patterns -contains "Scroll"
  $child = $walker.GetFirstChild($element)
  while ($child) { Nodes $child $hwnd ($depth + 1) $inner; $child = $walker.GetNextSibling($child) }
}

function State([string]$name) {   # every window of the app in this state, shot and read
  $id = "s{0:D2}" -f $report.states.Count   # unique and plain: it names the shots; the label stays in the report
  $at = $clock.ElapsedMilliseconds
  Enter "list $id"
  $windows = @(Windows | Where-Object { -not $_.utility })
  $script:tops = @($windows | ForEach-Object { [int64]$_.hwnd })
  $state = [ordered]@{ id = $id; name = $name; at = $at; windows = $windows; shots = @(); reads = @(); nodes = @() }
  foreach ($w in $windows) {
    Enter "shoot $id $($w.hwnd)"
    $shot = Shoot $w $id
    $state.shots += $shot
    if (-not $shot.drawn) { continue }
    Enter "read $id $($w.hwnd)"
    $script:nodes, $script:lists = @(), @()
    $script:walked = New-Object 'System.Collections.Generic.List[object]'
    $read = [ordered]@{ hwnd = $w.hwnd; read = $false; error = "the window is gone or no longer the app's" }
    if (Current $w) {
      try {
        Nodes ([System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$w.hwnd)) $w.hwnd
        Items "$id $($w.hwnd)"
        if (-not $script:nodes.Count -or $script:nodes[0].foreign) { $read.error = "the tree's root is not the app's" }
        else { $read = [ordered]@{ hwnd = $w.hwnd; read = $true } }
      } catch { $read.error = "the tree could not be read: $($_.Exception.Message)" }
    }
    $state.reads += $read
    $state.nodes += $script:nodes
  }
  $state.done = $clock.ElapsedMilliseconds   # every property of the state read by now
  $report.states += $state; Save
  $state
}

function Nested($element, [int64]$root, $others) {   # another top-level window shown under `root`, or inside one
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $e = $element
  while ($e) {
    $h = [int64]$e.Current.NativeWindowHandle
    if ($h -eq $root) { return $false }
    if ($others -contains $h) { return $true }
    $e = $walker.GetParent($e)
  }
  $false
}

function Target($step) {
  # By AutomationId, or by a name only one element carries, within one window and none it shows nested: the main
  # window, or with `in: owned` the one window the main window owns.
  $root = [int64](Main)
  if ($step.in -eq "owned") {
    $owned = @(Windows | Where-Object { $_.owner -eq $root })
    if ($owned.Count -ne 1) { return [ordered]@{ error = "the main window owns $($owned.Count) windows, not one" } }
    $root = [int64]$owned[0].hwnd
  }
  $others = @(Windows | ForEach-Object { [int64]$_.hwnd } | Where-Object { $_ -ne $root })
  $A = [System.Windows.Automation.AutomationElement]
  $property, $value = if ($step.id) { $A::AutomationIdProperty, $step.id } else { $A::NameProperty, $step.control }
  $all = $A::FromHandle([IntPtr]$root).FindAll([System.Windows.Automation.TreeScope]::Descendants,
                       (New-Object System.Windows.Automation.PropertyCondition $property, $value))
  $mine = @($all | Where-Object { -not (Nested $_ $root $others) })
  if ($mine.Count -ne 1) { return [ordered]@{ error = "$($mine.Count) elements match $value" } }
  if (-not [Desk]::InApp([uint32]$mine[0].Current.ProcessId)) { return [ordered]@{ error = "the element is not the app's" } }
  [ordered]@{ element = $mine[0] }
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

try {
  [Desk]::App = [Desk]::OpenJobObjectW(0x4, $false, [string]$ask.job)   # JOB_OBJECT_QUERY
  if ([Desk]::App -eq [IntPtr]::Zero) { Refuse "cannot open the app's job: Windows error $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }

  # The main window, chosen once: the one visible top-level window of the app that no other window owns and that
  # is not an input indicator or a menu. A dialog, an overlay or a flyout the app opens over it at startup is a
  # window it owns: shot and read with each state, never a candidate.
  Enter "wait for a window"
  $deadline = (Get-Date).AddSeconds([double]$ask.wait_seconds)
  do { $candidates = @(Candidates); if ($candidates.Count) { break }   # @(): a single window stays a list
       Start-Sleep -Milliseconds 200 } while ((Get-Date) -lt $deadline)
  if ($candidates.Count -eq 0) { Refuse "the app showed no window on the owned desktop" }
  Settle
  Enter "choose"
  $candidates = @(Candidates)
  if ($candidates.Count -ne 1) { $report.candidates = $candidates; Refuse "$($candidates.Count) candidate main windows at startup" }
  $report.main = $candidates[0]; Save

  # Sized to S1's window without activation or a z-order change, and read back. SetWindowPos sizes the window with
  # its invisible resize borders; the frame read back is without them.
  Enter "resize"
  $width, $height = [int]$ask.window[0], [int]$ask.window[1]
  $h = Main
  $outer = New-Object Desk+RECT; [void][Desk]::GetWindowRect($h, [ref]$outer)
  $inner = [Desk]::Frame($h)
  $moved = $false
  if ($inner) {
    $extraW, $extraH = (($outer.R - $outer.L) - $inner[2]), (($outer.B - $outer.T) - $inner[3])
    $moved = [Desk]::SetWindowPos($h, [IntPtr]::Zero, 0, 0, ($width + $extraW), ($height + $extraH), 0x0014)
  }
  Settle
  Enter "read size"
  $report.size = [ordered]@{ asked = @($width, $height); moved = $moved; frame = [Desk]::Frame((Main)) }
  Save

  [void](State "start")
  foreach ($step in $ask.steps) {
    $done = [ordered]@{ name = $step.name; action = $step.action; acted = $false; error = $null; state = $null; expect = $step.expect }
    $report.steps += $done
    if ($step.action -eq "wait") {
      # The server's control: nothing acted on, then the state read once `after[k]` ms have passed since each
      # anchor k (the start state's read, then each step's action), and the settle at least; `MAX_WAIT_MS` at most.
      $done.acted = $true; Save
      $anchors = @([int64]$report.states[0].at) + @($report.steps | Where-Object { $null -ne $_.acted_at } |
                                                     ForEach-Object { [int64]$_.acted_at })
      $now = $clock.ElapsedMilliseconds
      $until = $now + [int64]$ask.settle_ms
      for ($k = 0; $k -lt [Math]::Min($anchors.Count, @($step.after).Count); $k++) {
        $until = [Math]::Max($until, $anchors[$k] + [int64]@($step.after)[$k])
      }
      $until = [Math]::Min($until, $now + $MAX_WAIT_MS)
      Enter "wait $($until - $now)"
      while ($clock.ElapsedMilliseconds -lt $until) { Start-Sleep -Milliseconds 50 }
      $done.state = (State "after-$($step.name)").id
      Save; continue
    }
    Enter "find $($step.name)"
    $found = Target $step
    if ($found.error) { $done.error = $found.error; Save; continue }
    Enter "act $($step.name)"
    try { Act $found.element $step; $done.acted = $true; $done.acted_at = $clock.ElapsedMilliseconds }
    catch { $done.error = "$($_.Exception.Message)"; Save; continue }
    Save
    Settle
    $done.state = (State "after-$($step.name)").id   # the server reads the postcondition off this state
    Save
  }
  Enter "done"; Save
} catch {
  $report.error = "$($_.Exception.Message)"; Save; exit 4
}
