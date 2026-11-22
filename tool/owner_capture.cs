// The owner's desktop round's capture helper (plan 9e5, docs/plans/oneshot/9-kinds.md "The owner's desktop round").
// The server builds it with the .NET Framework's csc against the system's WinMetadata (`owner_round.helper`) and
// starts it in a job of the round's own. What it reads is limited where the frame is sampled, never afterwards.
// Each read has two phases: the helper says it is ready (`item.json`), the server proves its binding again and
// writes `go` or `stop`, and only on `go` is anything sampled. `result.json` says what happened, on every exit.
//
//   owner_capture window <hwnd> <dir>   A window-bound read. It creates a Windows Graphics Capture item for the
//       handle, which is tied to that one window instance and closes when the window is destroyed. Only on `go`,
//       with the item still open, does it start the frame pool, and it keeps the first frame as `window.png` only
//       while the item stays open through the copy. An item Windows refuses or gives empty is reported in
//       `item.json` as `empty`, so the check stays pending.
//   owner_capture surface <hwnd> <pid> <x> <y> <width> <height> <dir>   A surface read of exactly that region, the
//       fixture's frame. Before it says it is ready it watches the fixture (a WinEvent hook on its process) and
//       makes its bitmap, so nothing but the blit lies between the checks. On `go` it checks the fixture right
//       before and right after the blit: the window `hwnd`, visible, of process `pid`, titled `oneshot-fixture`,
//       with exactly that frame. Either check failing, or any move, hide or destruction of the fixture seen
//       between the watch and the end, one put back included, keeps nothing. A fixture it cannot watch is
//       unavailable, so the check stays pending. The blit is GDI's BitBlt with CAPTUREBLT, so layered windows
//       over the fixture are composed in.
//   owner_capture --check   Builds, starts, watches its own process and blits between two memory bitmaps with the
//       same flags; reads no screen.
// Every release is tried on its own; one that fails is named in the result's `unreleased`.
using System; using System.IO; using System.Runtime.InteropServices; using System.Text; using System.Threading;
using System.Runtime.InteropServices.WindowsRuntime; using System.Drawing; using System.Drawing.Imaging;
using Windows.Foundation; using Windows.Graphics.Capture; using Windows.Graphics.DirectX;
using Windows.Graphics.DirectX.Direct3D11; using Windows.Graphics.Imaging; using Windows.Storage.Streams;

[ComImport, Guid("3628E81B-3CAC-4C60-B7F4-23CE0E0C3356"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IGraphicsCaptureItemInterop { IntPtr CreateForWindow(IntPtr window, ref Guid iid); IntPtr CreateForMonitor(IntPtr monitor, ref Guid iid); }

public static class Program {
  const int ANSWER_MS = 10000, FRAME_MS = 5000;
  const uint BLIT = 0x00CC0020 | 0x40000000;   // SRCCOPY | CAPTUREBLT
  const string FIXTURE = "oneshot-fixture";
  [StructLayout(LayoutKind.Sequential)] struct RECT { public int Left, Top, Right, Bottom; }
  [DllImport("d3d11.dll")] static extern int D3D11CreateDevice(IntPtr a, int type, IntPtr sw, uint flags, IntPtr levels, uint n, uint sdk, out IntPtr dev, out int level, out IntPtr ctx);
  [DllImport("d3d11.dll")] static extern int CreateDirect3D11DeviceFromDXGIDevice(IntPtr dxgi, out IntPtr device);
  [DllImport("user32")] static extern bool SetProcessDpiAwarenessContext(IntPtr value);
  [DllImport("user32")] static extern bool IsWindow(IntPtr hwnd);
  [DllImport("user32")] static extern bool IsWindowVisible(IntPtr hwnd);
  [DllImport("user32")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
  [DllImport("user32", CharSet = CharSet.Unicode)] static extern int GetWindowTextW(IntPtr hwnd, StringBuilder text, int max);
  [DllImport("user32")] static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
  [DllImport("user32")] static extern IntPtr GetDC(IntPtr hwnd);
  [DllImport("user32")] static extern int ReleaseDC(IntPtr hwnd, IntPtr dc);
  [DllImport("gdi32")] static extern IntPtr CreateCompatibleDC(IntPtr dc);
  [DllImport("gdi32")] static extern IntPtr CreateCompatibleBitmap(IntPtr dc, int width, int height);
  [DllImport("gdi32")] static extern IntPtr SelectObject(IntPtr dc, IntPtr obj);
  [DllImport("gdi32")] static extern bool BitBlt(IntPtr dst, int x, int y, int w, int h, IntPtr src, int sx, int sy, uint rop);
  [DllImport("gdi32")] static extern bool DeleteDC(IntPtr dc);
  [DllImport("gdi32")] static extern bool DeleteObject(IntPtr obj);
  delegate void WinEvent(IntPtr hook, uint ev, IntPtr hwnd, int obj, int child, uint thread, uint time);
  [DllImport("user32")] static extern IntPtr SetWinEventHook(uint min, uint max, IntPtr module, WinEvent proc, uint pid, uint thread, uint flags);
  [DllImport("user32")] static extern bool UnhookWinEvent(IntPtr hook);
  [StructLayout(LayoutKind.Sequential)] struct MSG { public IntPtr hwnd; public uint message; public IntPtr wParam, lParam; public uint time; public int x, y; }
  [DllImport("user32")] static extern bool PeekMessageW(out MSG msg, IntPtr hwnd, uint min, uint max, uint remove);
  [DllImport("user32")] static extern IntPtr DispatchMessageW(ref MSG msg);
  const uint EVENT_OBJECT_DESTROY = 0x8001, EVENT_OBJECT_HIDE = 0x8003, EVENT_OBJECT_LOCATIONCHANGE = 0x800B;

  static int closed;   // set by the item's Closed event on another thread, read with a barrier
  static bool Closed() { return Volatile.Read(ref closed) != 0; }
  static readonly System.Collections.Generic.List<string> unreleased = new System.Collections.Generic.List<string>();

  // The fixture's watch: an out-of-context hook queues each event to this thread as Windows makes the change, and
  // `Stirred` delivers every one queued so far. Kept in a field, so the collector never takes the callback.
  static WinEvent watcher; static IntPtr watched; static int stirred;
  static void Seen(IntPtr hook, uint ev, IntPtr hwnd, int obj, int child, uint thread, uint time) {
    if (hwnd == watched && obj == 0 && (ev == EVENT_OBJECT_DESTROY || ev == EVENT_OBJECT_HIDE || ev == EVENT_OBJECT_LOCATIONCHANGE))
      Interlocked.Exchange(ref stirred, 1);
  }
  static IntPtr Watch(IntPtr hwnd, uint pid) {
    watched = hwnd; watcher = Seen;
    return SetWinEventHook(EVENT_OBJECT_DESTROY, EVENT_OBJECT_LOCATIONCHANGE, IntPtr.Zero, watcher, pid, 0, 0);   // WINEVENT_OUTOFCONTEXT
  }
  static bool Stirred() {
    MSG m;
    while (PeekMessageW(out m, IntPtr.Zero, 0, 0, 1)) DispatchMessageW(ref m);   // PM_REMOVE
    return Volatile.Read(ref stirred) != 0;
  }

  static void Write(string dir, string name, string json) {   // replaced whole, so the server never reads half
    string part = Path.Combine(dir, name + ".part");
    File.WriteAllText(part, json);
    File.Copy(part, Path.Combine(dir, name), true);
    File.Delete(part);
  }
  static string Quote(string s) { return "\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", " ").Replace("\r", " ") + "\""; }
  static string Not(string why) { return "{\"read\": false, \"why\": " + Quote(why) + "}"; }
  static void Free(string what, Func<bool> release) {   // each release whatever the others do, a failed one named
    try { if (!release()) unreleased.Add(what); }
    catch (Exception e) { unreleased.Add(what + ": " + e.GetType().Name); }
  }
  static void Free(string what, Action release) { Free(what, () => { release(); return true; }); }
  static T Wait<T>(IAsyncOperation<T> op) {   // the system metadata alone: no AsTask, which needs the SDK's
    for (int i = 0; i < 2000 && op.Status == AsyncStatus.Started; i++) Thread.Sleep(5);
    if (op.Status != AsyncStatus.Completed) throw new TimeoutException("a Windows operation did not complete");
    return op.GetResults();
  }
  static void Wait(IAsyncAction op) {
    for (int i = 0; i < 2000 && op.Status == AsyncStatus.Started; i++) Thread.Sleep(5);
    if (op.Status != AsyncStatus.Completed) throw new TimeoutException("a Windows operation did not complete");
  }

  static string Answer(string dir) {   // `go`, `stop`, or null when the server said nothing in time
    for (int waited = 0; waited < ANSWER_MS; waited += 20) {
      if (File.Exists(Path.Combine(dir, "stop"))) return "stop";
      if (File.Exists(Path.Combine(dir, "go"))) return "go";
      Thread.Sleep(20);
    }
    return null;
  }

  static string Window(IntPtr hwnd, string dir) {
    object factory = null; GraphicsCaptureItem item = null; Direct3D11CaptureFramePool pool = null;
    GraphicsCaptureSession session = null; Direct3D11CaptureFrame frame = null; IDirect3DDevice device = null;
    IntPtr dev = IntPtr.Zero, ctx = IntPtr.Zero, dxgi = IntPtr.Zero, raw = IntPtr.Zero, made = IntPtr.Zero;
    try {
      try {
        if (!GraphicsCaptureSession.IsSupported()) throw new NotSupportedException("capture is not supported");
        factory = WindowsRuntimeMarshal.GetActivationFactory(typeof(GraphicsCaptureItem));
        Guid itemId = new Guid("79C3F95B-31F7-4EC2-A464-632EF5D30760");
        made = ((IGraphicsCaptureItemInterop)factory).CreateForWindow(hwnd, ref itemId);
        item = (GraphicsCaptureItem)Marshal.GetObjectForIUnknown(made);
        item.Closed += (s, e) => { Interlocked.Exchange(ref closed, 1); };
        if (item.Size.Width <= 0 || item.Size.Height <= 0) throw new InvalidOperationException("the item is empty");
      } catch (Exception e) {   // no item here: unavailable, never a failed read, and no pool is ever made
        string why = e.GetType().Name + ": " + e.Message;
        Write(dir, "item.json", "{\"empty\": true, \"why\": " + Quote(why) + "}");
        return Not(why);
      }
      Write(dir, "item.json", "{\"empty\": false, \"width\": " + item.Size.Width + ", \"height\": " + item.Size.Height + "}");
      string answer = Answer(dir);
      if (answer != "go") return Not(answer == "stop" ? "the binding was not proven" : "no answer from the server");
      if (Closed()) return Not("the window was destroyed: its item closed");
      int level;
      int hr = D3D11CreateDevice(IntPtr.Zero, 1, IntPtr.Zero, 0x20, IntPtr.Zero, 0, 7, out dev, out level, out ctx);
      if (hr != 0) return Not("D3D11CreateDevice " + hr.ToString("X"));
      Guid dxgiId = new Guid("54ec77fa-1377-44e6-8c32-88fd5f44c84c");
      Marshal.QueryInterface(dev, ref dxgiId, out dxgi);
      hr = CreateDirect3D11DeviceFromDXGIDevice(dxgi, out raw);
      if (hr != 0) return Not("CreateDirect3D11DeviceFromDXGIDevice " + hr.ToString("X"));
      device = (IDirect3DDevice)Marshal.GetObjectForIUnknown(raw);
      pool = Direct3D11CaptureFramePool.CreateFreeThreaded(device, DirectXPixelFormat.B8G8R8A8UIntNormalized, 1, item.Size);
      session = pool.CreateCaptureSession(item);
      session.StartCapture();
      for (int waited = 0; waited < FRAME_MS && frame == null && !Closed(); waited += 50) {
        frame = pool.TryGetNextFrame();
        if (frame == null) Thread.Sleep(50);
      }
      // A frame the pool returned as the item closed is never copied, nor one whose item closed during the copy.
      if (frame == null || Closed()) return Not(Closed() ? "the window was destroyed: its item closed" : "no frame within 5 s");
      byte[] bytes;
      using (var bitmap = Wait(SoftwareBitmap.CreateCopyFromSurfaceAsync(frame.Surface)))
      using (var stream = new InMemoryRandomAccessStream()) {
        var encoder = Wait(BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, stream));
        encoder.SetSoftwareBitmap(bitmap);
        Wait(encoder.FlushAsync());
        bytes = new byte[stream.Size];
        using (var reader = new DataReader(stream.GetInputStreamAt(0))) {
          Wait(reader.LoadAsync((uint)stream.Size));
          reader.ReadBytes(bytes);
        }
      }
      if (Closed()) return Not("the window was destroyed: its item closed");
      File.WriteAllBytes(Path.Combine(dir, "window.png"), bytes);
      return "{\"read\": true, \"width\": " + frame.ContentSize.Width + ", \"height\": " + frame.ContentSize.Height + "}";
    } finally {   // on every exit, the read or not, each whatever the others do
      Free("the frame", () => { if (frame != null) frame.Dispose(); });
      Free("the capture session", () => { if (session != null) session.Dispose(); });
      Free("the frame pool", () => { if (pool != null) pool.Dispose(); });
      Free("the device", () => { if (device != null) Marshal.ReleaseComObject(device); });
      Free("the item", () => { if (item != null) Marshal.FinalReleaseComObject(item); });
      Free("the item factory", () => { if (factory != null) Marshal.ReleaseComObject(factory); });
      string[] names = { "the item pointer", "the device pointer", "the DXGI device", "the D3D context", "the D3D device" };
      IntPtr[] pointers = { made, raw, dxgi, ctx, dev };
      for (int i = 0; i < pointers.Length; i++) { IntPtr q = pointers[i]; Free(names[i], () => { if (q != IntPtr.Zero) Marshal.Release(q); }); }
    }
  }

  static string Fixture(IntPtr hwnd, uint pid, int x, int y, int w, int h) {   // why the fixture is not that frame now
    uint owner; RECT r; var title = new StringBuilder(64);
    if (!IsWindow(hwnd) || !IsWindowVisible(hwnd)) return "the fixture is gone or hidden";
    GetWindowThreadProcessId(hwnd, out owner);
    GetWindowTextW(hwnd, title, title.Capacity);
    if (owner != pid || title.ToString() != FIXTURE) return "the handle names another window";
    if (!GetWindowRect(hwnd, out r) || r.Left != x || r.Top != y || r.Right - r.Left != w || r.Bottom - r.Top != h) return "the fixture moved";
    return "";
  }

  static string Surface(IntPtr hwnd, uint pid, int x, int y, int w, int h, string dir) {
    if (w <= 0 || h <= 0) return Not("an empty region");
    IntPtr hook = IntPtr.Zero, screen = IntPtr.Zero, dc = IntPtr.Zero, bits = IntPtr.Zero, old = IntPtr.Zero;
    Bitmap kept = null;
    try {
      // The watch and the bitmap before the helper says it is ready: between the checks there is only the blit.
      hook = Watch(hwnd, pid);
      screen = GetDC(IntPtr.Zero);
      if (screen != IntPtr.Zero) { dc = CreateCompatibleDC(screen); bits = CreateCompatibleBitmap(screen, w, h); }
      if (hook == IntPtr.Zero || dc == IntPtr.Zero || bits == IntPtr.Zero) {   // nothing to read it with here: pending
        string none = hook == IntPtr.Zero ? "the fixture cannot be watched" : "no bitmap to blit into";
        Write(dir, "item.json", "{\"empty\": true, \"why\": " + Quote(none) + "}");
        return Not(none);
      }
      old = SelectObject(dc, bits);
      Write(dir, "item.json", "{\"empty\": false}");
      string answer = Answer(dir);
      if (answer != "go") return Not(answer == "stop" ? "the binding was not proven" : "no answer from the server");
      string why = Stirred() ? "the fixture moved or was hidden" : Fixture(hwnd, pid, x, y, w, h);
      if (why != "") return Not(why);
      bool blitted = BitBlt(dc, 0, 0, w, h, screen, x, y, BLIT);
      why = Fixture(hwnd, pid, x, y, w, h);   // the fixture still that frame once sampled
      Thread.Sleep(50);   // every event of a change made before the second check is queued by now
      if (why == "" && Stirred()) why = "the fixture moved or was hidden while it was sampled";   // put back included
      // No pixels from this desktop (not the input one, or locked): no fault of the app's, so the check stays pending.
      if (!blitted) return "{\"read\": false, \"unavailable\": true, \"why\": \"BitBlt failed\"}";
      if (why != "") return Not(why);
      SelectObject(dc, old); old = IntPtr.Zero;
      kept = Image.FromHbitmap(bits);
      kept.Save(Path.Combine(dir, "surface.png"), ImageFormat.Png);
      return "{\"read\": true, \"width\": " + w + ", \"height\": " + h + "}";
    } finally {
      IntPtr d = dc, b = bits, o = old, s = screen, k = hook;
      Free("the saved bitmap", () => { if (kept != null) kept.Dispose(); });
      Free("the bitmap's selection", () => o == IntPtr.Zero || SelectObject(d, o) != IntPtr.Zero);
      Free("the memory DC", () => d == IntPtr.Zero || DeleteDC(d));
      Free("the bitmap", () => b == IntPtr.Zero || DeleteObject(b));
      Free("the screen DC", () => s == IntPtr.Zero || ReleaseDC(IntPtr.Zero, s) == 1);
      Free("the fixture's watch", () => k == IntPtr.Zero || UnhookWinEvent(k));
    }
  }

  static bool Check() {   // the watch and the blit's flags, on its own process and between memory bitmaps: no screen
    IntPtr hook = Watch(IntPtr.Zero, (uint)System.Diagnostics.Process.GetCurrentProcess().Id);
    IntPtr src = CreateCompatibleDC(IntPtr.Zero), dst = CreateCompatibleDC(IntPtr.Zero);
    IntPtr a = CreateCompatibleBitmap(src, 4, 4), b = CreateCompatibleBitmap(dst, 4, 4);
    IntPtr oa = SelectObject(src, a), ob = SelectObject(dst, b);
    try { return hook != IntPtr.Zero && BitBlt(dst, 0, 0, 4, 4, src, 0, 0, BLIT) && !Stirred(); }
    finally {
      SelectObject(src, oa); SelectObject(dst, ob); DeleteObject(a); DeleteObject(b); DeleteDC(src); DeleteDC(dst);
      if (hook != IntPtr.Zero) UnhookWinEvent(hook);
    }
  }

  public static int Main(string[] args) {
    SetProcessDpiAwarenessContext((IntPtr)(-4));   // per-monitor v2: physical pixels, as the driver reads them
    if (args.Length == 1 && args[0] == "--check") {
      if (!Check()) { Console.Error.WriteLine("the WinEvent watch or BitBlt with CAPTUREBLT failed"); return 1; }
      Console.WriteLine("owner_capture ok");
      return 0;
    }
    string dir = args[args.Length - 1], result;
    try {
      if (args.Length == 3 && args[0] == "window") result = Window((IntPtr)long.Parse(args[1]), dir);
      else if (args.Length == 8 && args[0] == "surface")
        result = Surface((IntPtr)long.Parse(args[1]), uint.Parse(args[2]), int.Parse(args[3]), int.Parse(args[4]),
                         int.Parse(args[5]), int.Parse(args[6]), dir);
      else { Console.Error.WriteLine("usage: owner_capture window <hwnd> <dir> | surface <hwnd> <pid> <x> <y> <w> <h> <dir> | --check"); return 2; }
    } catch (Exception e) {
      result = Not(e.GetType().Name + ": " + e.Message);
      if (!File.Exists(Path.Combine(dir, "item.json")))   // a failure before the item phase: nothing could be made here
        Free("the item answer", () => Write(dir, "item.json", "{\"empty\": true, \"why\": " + Quote(e.GetType().Name + ": " + e.Message) + "}"));
    }
    if (unreleased.Count > 0) {   // the read stands as it is, and the server learns what was not let go
      var names = new StringBuilder();
      foreach (string what in unreleased) names.Append(names.Length > 0 ? ", " : "").Append(Quote(what));
      result = result.Substring(0, result.LastIndexOf('}')) + ", \"unreleased\": [" + names + "]}";
    }
    Write(dir, "result.json", result);
    return 0;
  }
}
