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
//       fixture's frame. On `go` it checks the fixture right before and right after the blit: the window `hwnd`,
//       visible, of process `pid`, titled `oneshot-fixture`, with exactly that frame. Either check failing keeps
//       nothing. The blit is GDI's BitBlt with CAPTUREBLT, so layered windows over the fixture are composed in.
//   owner_capture --check   Builds, starts and blits between two memory bitmaps with the same flags; reads no screen.
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

  static int closed;   // set by the item's Closed event on another thread, read with a barrier
  static bool Closed() { return Volatile.Read(ref closed) != 0; }

  static void Write(string dir, string name, string json) {   // replaced whole, so the server never reads half
    string part = Path.Combine(dir, name + ".part");
    File.WriteAllText(part, json);
    File.Copy(part, Path.Combine(dir, name), true);
    File.Delete(part);
  }
  static string Quote(string s) { return "\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", " ").Replace("\r", " ") + "\""; }
  static string Not(string why) { return "{\"read\": false, \"why\": " + Quote(why) + "}"; }
  static void Free(Action release) { try { release(); } catch (Exception) { } }   // each release whatever the others do
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
      Free(() => { if (frame != null) frame.Dispose(); });
      Free(() => { if (session != null) session.Dispose(); });
      Free(() => { if (pool != null) pool.Dispose(); });
      Free(() => { if (device != null) Marshal.ReleaseComObject(device); });
      Free(() => { if (item != null) Marshal.FinalReleaseComObject(item); });
      Free(() => { if (factory != null) Marshal.ReleaseComObject(factory); });
      foreach (IntPtr p in new[] { made, raw, dxgi, ctx, dev }) { IntPtr q = p; Free(() => { if (q != IntPtr.Zero) Marshal.Release(q); }); }
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

  static bool Blit(IntPtr source, int x, int y, int w, int h, Action<IntPtr> keep) {   // BitBlt into a fresh bitmap
    IntPtr dc = IntPtr.Zero, bits = IntPtr.Zero, old = IntPtr.Zero;
    try {
      dc = CreateCompatibleDC(source);
      bits = CreateCompatibleBitmap(source, w, h);
      if (dc == IntPtr.Zero || bits == IntPtr.Zero) return false;
      old = SelectObject(dc, bits);
      if (!BitBlt(dc, 0, 0, w, h, source, x, y, BLIT)) return false;
      SelectObject(dc, old); old = IntPtr.Zero;
      if (keep != null) keep(bits);
      return true;
    } finally {
      IntPtr d = dc, b = bits, o = old;
      Free(() => { if (o != IntPtr.Zero) SelectObject(d, o); });
      Free(() => { if (d != IntPtr.Zero) DeleteDC(d); });
      Free(() => { if (b != IntPtr.Zero) DeleteObject(b); });
    }
  }

  static string Surface(IntPtr hwnd, uint pid, int x, int y, int w, int h, string dir) {
    if (w <= 0 || h <= 0) return Not("an empty region");
    Write(dir, "item.json", "{\"empty\": false}");
    string answer = Answer(dir);
    if (answer != "go") return Not(answer == "stop" ? "the binding was not proven" : "no answer from the server");
    string why = Fixture(hwnd, pid, x, y, w, h);
    if (why != "") return Not(why);
    IntPtr screen = GetDC(IntPtr.Zero);
    try {
      Bitmap kept = null;
      try {
        if (!Blit(screen, x, y, w, h, bits => { kept = Image.FromHbitmap(bits); })) return Not("BitBlt failed");
        why = Fixture(hwnd, pid, x, y, w, h);   // the fixture still that frame once sampled, or nothing is kept
        if (why != "") return Not(why);
        kept.Save(Path.Combine(dir, "surface.png"), ImageFormat.Png);
      } finally { if (kept != null) kept.Dispose(); }
    } finally { ReleaseDC(IntPtr.Zero, screen); }
    return "{\"read\": true, \"width\": " + w + ", \"height\": " + h + "}";
  }

  static bool Check() {   // the blit's flags accepted, between memory bitmaps: no screen is read
    IntPtr dc = CreateCompatibleDC(IntPtr.Zero), bits = CreateCompatibleBitmap(dc, 4, 4), old = IntPtr.Zero;
    try { old = SelectObject(dc, bits); return Blit(dc, 0, 0, 4, 4, null); }
    finally { SelectObject(dc, old); DeleteDC(dc); DeleteObject(bits); }
  }

  public static int Main(string[] args) {
    SetProcessDpiAwarenessContext((IntPtr)(-4));   // per-monitor v2: physical pixels, as the driver reads them
    if (args.Length == 1 && args[0] == "--check") {
      if (!Check()) { Console.Error.WriteLine("BitBlt with CAPTUREBLT failed"); return 1; }
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
        Free(() => Write(dir, "item.json", "{\"empty\": true, \"why\": " + Quote(e.GetType().Name + ": " + e.Message) + "}"));
    }
    Write(dir, "result.json", result);
    return 0;
  }
}
