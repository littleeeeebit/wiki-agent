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
//   owner_capture surface <hwnd> <pid> <x> <y> <width> <height> <marks> <dir>   A surface read of exactly that
//       region, the fixture's client area, which it paints whole. It makes its bitmap before it says it is ready.
//       On `go` it checks the fixture right before and right after the blit: the window `hwnd`, visible, not
//       minimized, of process `pid`, titled `oneshot-fixture`, its client area exactly that region and its window
//       region, which the fixture sets itself, one rectangle of exactly that client area. The frame is kept only when every pixel of every one of `<marks>` (the server's random
//       colours, which the fixture paints along its client edges) is there: proof from the sampled pixels
//       themselves that the fixture covered the region in that frame, however it was moved or put back around
//       it, by a pixel included. Marks missing leave the check pending;
//       a check of the window failing fails it. The fixture also keeps itself steady (oneshot_fixture.py).
//       The blit is GDI's BitBlt with CAPTUREBLT, so layered windows over the fixture are composed in.
//   owner_capture --check   Builds, starts, reads marks off a memory bitmap and blits between two memory bitmaps
//       with the same flags; reads no screen.
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
  [DllImport("user32")] static extern bool GetClientRect(IntPtr hwnd, out RECT rect);
  [DllImport("user32")] static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
  [DllImport("user32")] static extern IntPtr GetDC(IntPtr hwnd);
  [DllImport("user32")] static extern int ReleaseDC(IntPtr hwnd, IntPtr dc);
  [DllImport("gdi32")] static extern IntPtr CreateCompatibleDC(IntPtr dc);
  [DllImport("gdi32")] static extern IntPtr CreateCompatibleBitmap(IntPtr dc, int width, int height);
  [DllImport("gdi32")] static extern IntPtr SelectObject(IntPtr dc, IntPtr obj);
  [DllImport("gdi32")] static extern bool BitBlt(IntPtr dst, int x, int y, int w, int h, IntPtr src, int sx, int sy, uint rop);
  [DllImport("gdi32")] static extern bool DeleteDC(IntPtr dc);
  [DllImport("gdi32")] static extern bool DeleteObject(IntPtr obj);
  [StructLayout(LayoutKind.Sequential)] struct POINT { public int X, Y; }
  [DllImport("user32")] static extern bool IsIconic(IntPtr hwnd);
  [DllImport("user32")] static extern bool ClientToScreen(IntPtr hwnd, ref POINT point);
  [DllImport("user32")] static extern int GetWindowRgnBox(IntPtr hwnd, out RECT box);
  const int SIMPLEREGION = 2;
  const int TOLERANCE = 8;   // per channel: what the composition may change in a solid colour

  static int closed;   // set by the item's Closed event on another thread, read with a barrier
  static bool Closed() { return Volatile.Read(ref closed) != 0; }
  static readonly System.Collections.Generic.List<string> unreleased = new System.Collections.Generic.List<string>();

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

  static int[][] Marks(string file) {   // `x y width height rrggbb` per line, in the fixture's client pixels
    var marks = new System.Collections.Generic.List<int[]>();
    foreach (string line in File.ReadAllLines(file)) {
      string[] p = line.Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
      if (p.Length != 5) continue;
      marks.Add(new[] { int.Parse(p[0]), int.Parse(p[1]), int.Parse(p[2]), int.Parse(p[3]), Convert.ToInt32(p[4], 16) });
    }
    if (marks.Count == 0) throw new InvalidDataException("no marks");
    return marks.ToArray();
  }

  // Why the sampled frame does not carry every mark where the fixture paints it, its client area starting at
  // (ox, oy) in the frame, or empty: every pixel of every mark within TOLERANCE of its colour. The server keeps
  // each mark's colour away from every other's and from the page's, so a fixture displaced by a pixel fails here.
  static string Marked(Bitmap frame, int[][] marks, int ox, int oy) {
    foreach (int[] m in marks) {
      if (ox + m[0] < 0 || oy + m[1] < 0 || ox + m[0] + m[2] > frame.Width || oy + m[1] + m[3] > frame.Height)
        return "a mark lies outside the frame";
      for (int px = ox + m[0]; px < ox + m[0] + m[2]; px++)
        for (int py = oy + m[1]; py < oy + m[1] + m[3]; py++) {
          Color c = frame.GetPixel(px, py);
          if (Math.Abs(c.R - (m[4] >> 16 & 0xFF)) > TOLERANCE || Math.Abs(c.G - (m[4] >> 8 & 0xFF)) > TOLERANCE ||
              Math.Abs(c.B - (m[4] & 0xFF)) > TOLERANCE) return "the fixture's marks are not in the sampled frame";
        }
    }
    return "";
  }

  // Why the fixture's client area, what it paints, is not exactly the region x, y, w, h now, or "".
  static string Fixture(IntPtr hwnd, uint pid, int x, int y, int w, int h) {
    uint owner; RECT r, box, frame; var title = new StringBuilder(64); var origin = new POINT();
    if (!IsWindow(hwnd) || !IsWindowVisible(hwnd) || IsIconic(hwnd)) return "the fixture is gone, hidden or minimized";
    GetWindowThreadProcessId(hwnd, out owner);
    GetWindowTextW(hwnd, title, title.Capacity);
    if (owner != pid || title.ToString() != FIXTURE) return "the handle names another window";
    if (!GetClientRect(hwnd, out r) || !ClientToScreen(hwnd, ref origin) || !GetWindowRect(hwnd, out frame))
      return "the fixture is gone";
    if (origin.X != x || origin.Y != y || r.Right != w || r.Bottom != h) return "the fixture moved";
    // The fixture gives itself a window region of exactly its client area. Only Windows saying so, one rectangle
    // with those bounds, proves its shape: ERROR, which is also what a failed query gives, never does.
    int shape = GetWindowRgnBox(hwnd, out box);
    int left = origin.X - frame.Left, top = origin.Y - frame.Top;
    if (shape != SIMPLEREGION || box.Left != left || box.Top != top || box.Right != left + w || box.Bottom != top + h)
      return "the fixture's shape changed";
    return "";
  }

  static string Surface(IntPtr hwnd, uint pid, int x, int y, int w, int h, string file, string dir) {
    if (w <= 0 || h <= 0) return Not("an empty region");
    int[][] marks = Marks(file);
    IntPtr screen = IntPtr.Zero, dc = IntPtr.Zero, bits = IntPtr.Zero, old = IntPtr.Zero;
    Bitmap kept = null;
    try {
      screen = GetDC(IntPtr.Zero);   // the bitmap before the helper says it is ready: only the blit is left for `go`
      if (screen != IntPtr.Zero) { dc = CreateCompatibleDC(screen); bits = CreateCompatibleBitmap(screen, w, h); }
      if (dc == IntPtr.Zero || bits == IntPtr.Zero) {   // nothing to read it with here: pending
        Write(dir, "item.json", "{\"empty\": true, \"why\": \"no bitmap to blit into\"}");
        return Not("no bitmap to blit into");
      }
      old = SelectObject(dc, bits);
      Write(dir, "item.json", "{\"empty\": false}");
      string answer = Answer(dir);
      if (answer != "go") return Not(answer == "stop" ? "the binding was not proven" : "no answer from the server");
      string why = Fixture(hwnd, pid, x, y, w, h);
      if (why != "") return Not(why);
      bool blitted = BitBlt(dc, 0, 0, w, h, screen, x, y, BLIT);
      why = Fixture(hwnd, pid, x, y, w, h);   // the fixture still that frame once sampled
      // No pixels from this desktop (not the input one, or locked): no fault of the app's, so the check stays pending.
      if (!blitted) return "{\"read\": false, \"unavailable\": true, \"why\": \"BitBlt failed\"}";
      if (why != "") return Not(why);
      SelectObject(dc, old); old = IntPtr.Zero;
      kept = Image.FromHbitmap(bits);
      // The proof is the frame itself: every mark the fixture paints, where it paints it, in the pixels sampled.
      // A frame without them (covered, moved or put back, see-through, colours changed on the way) keeps nothing
      // and leaves the check pending. The region is the client area itself, as both checks found it.
      string unmarked = Marked(kept, marks, 0, 0);
      if (unmarked != "") return "{\"read\": false, \"unavailable\": true, \"why\": " + Quote(unmarked) + "}";
      kept.Save(Path.Combine(dir, "surface.png"), ImageFormat.Png);
      return "{\"read\": true, \"width\": " + w + ", \"height\": " + h + "}";
    } finally {
      IntPtr d = dc, b = bits, o = old, s = screen;
      Free("the saved bitmap", () => { if (kept != null) kept.Dispose(); });
      Free("the bitmap's selection", () => o == IntPtr.Zero || SelectObject(d, o) != IntPtr.Zero);
      Free("the memory DC", () => d == IntPtr.Zero || DeleteDC(d));
      Free("the bitmap", () => b == IntPtr.Zero || DeleteObject(b));
      Free("the screen DC", () => s == IntPtr.Zero || ReleaseDC(IntPtr.Zero, s) == 1);
    }
  }

  static bool Check() {   // the marks' reading and the blit's flags, on memory bitmaps only: no screen
    int[][] marks = { new[] { 2, 3, 8, 4, 0x112233 }, new[] { 20, 30, 6, 6, 0xABCDEF } };
    using (var frame = new Bitmap(64, 64)) {
      using (var g = Graphics.FromImage(frame)) {
        g.Clear(Color.White);
        foreach (int[] m in marks) using (var brush = new SolidBrush(Color.FromArgb(m[4] | unchecked((int)0xFF000000))))
          g.FillRectangle(brush, m[0] + 10, m[1] + 12, m[2], m[3]);   // a client area starting at (10, 12)
      }
      if (Marked(frame, marks, 10, 12) != "" || Marked(frame, marks, 0, 0) == "") return false;   // elsewhere: none
      if (Marked(frame, marks, 11, 12) == "" || Marked(frame, marks, 10, 11) == "") return false;   // a pixel off
      frame.SetPixel(10 + 20 + 3, 12 + 30 + 3, Color.FromArgb(0xAB, 0xCD, 0xEF - 12));   // one mark off its colour
      if (Marked(frame, marks, 10, 12) == "") return false;
    }
    IntPtr src = CreateCompatibleDC(IntPtr.Zero), dst = CreateCompatibleDC(IntPtr.Zero);
    IntPtr a = CreateCompatibleBitmap(src, 4, 4), b = CreateCompatibleBitmap(dst, 4, 4);
    IntPtr oa = SelectObject(src, a), ob = SelectObject(dst, b);
    try { return BitBlt(dst, 0, 0, 4, 4, src, 0, 0, BLIT); }
    finally { SelectObject(src, oa); SelectObject(dst, ob); DeleteObject(a); DeleteObject(b); DeleteDC(src); DeleteDC(dst); }
  }

  public static int Main(string[] args) {
    SetProcessDpiAwarenessContext((IntPtr)(-4));   // per-monitor v2: physical pixels, as the driver reads them
    if (args.Length == 1 && args[0] == "--check") {
      if (!Check()) { Console.Error.WriteLine("the marks' reading or BitBlt with CAPTUREBLT failed"); return 1; }
      Console.WriteLine("owner_capture ok");
      return 0;
    }
    string dir = args[args.Length - 1], result;
    try {
      if (args.Length == 3 && args[0] == "window") result = Window((IntPtr)long.Parse(args[1]), dir);
      else if (args.Length == 9 && args[0] == "surface")
        result = Surface((IntPtr)long.Parse(args[1]), uint.Parse(args[2]), int.Parse(args[3]), int.Parse(args[4]),
                         int.Parse(args[5]), int.Parse(args[6]), args[7], dir);
      else { Console.Error.WriteLine("usage: owner_capture window <hwnd> <dir> | surface <hwnd> <pid> <x> <y> <w> <h> <marks> <dir> | --check"); return 2; }
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
