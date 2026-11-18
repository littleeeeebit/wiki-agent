// Windows Graphics Capture's window API, measured on the look's owned desktop (9d2b3). It refuses to run on any
// desktop but the one it is told, and captures only a window of the fixture's own job, so it can never read a
// window of the owner's. It stops at the item's size check when the item is empty, before any frame pool.
using System; using System.Runtime.InteropServices; using System.Threading; using System.IO;
using System.Runtime.InteropServices.WindowsRuntime;
using Windows.Graphics.Capture; using Windows.Graphics.DirectX; using Windows.Graphics.DirectX.Direct3D11;
using Windows.Graphics.Imaging; using Windows.Storage.Streams;
[ComImport, Guid("3628E81B-3CAC-4C60-B7F4-23CE0E0C3356"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IGraphicsCaptureItemInterop { IntPtr CreateForWindow(IntPtr window, ref Guid iid); IntPtr CreateForMonitor(IntPtr monitor, ref Guid iid); }
public static class Wgc {
  [DllImport("d3d11.dll")] static extern int D3D11CreateDevice(IntPtr a, int type, IntPtr sw, uint flags, IntPtr levels, uint n, uint sdk, out IntPtr dev, out int level, out IntPtr ctx);
  [DllImport("d3d11.dll")] static extern int CreateDirect3D11DeviceFromDXGIDevice(IntPtr dxgi, out IntPtr device);
  delegate bool Visit(IntPtr h, IntPtr l);
  [DllImport("user32")] static extern bool EnumWindows(Visit v, IntPtr l);
  [DllImport("user32")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32", CharSet=CharSet.Unicode)] static extern int GetWindowTextW(IntPtr h, System.Text.StringBuilder s, int n);
  [DllImport("user32")] static extern IntPtr GetThreadDesktop(uint thread);
  [DllImport("kernel32")] static extern uint GetCurrentThreadId();
  [DllImport("user32", CharSet=CharSet.Unicode)] static extern bool GetUserObjectInformationW(IntPtr o, int index, System.Text.StringBuilder s, int n, out int needed);
  [DllImport("kernel32", CharSet=CharSet.Unicode)] static extern IntPtr OpenJobObjectW(uint access, bool inherit, string name);
  [DllImport("kernel32")] static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
  [DllImport("kernel32")] static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool inside);
  [DllImport("kernel32")] static extern bool CloseHandle(IntPtr h);
  public static string Supported() { return GraphicsCaptureSession.IsSupported().ToString(); }
  [DllImport("user32", EntryPoint="GetUserObjectInformationW")] static extern bool GetUserObjectFlag(IntPtr o, int index, out int value, int n, out int needed);
  public static string Desktop() {   // the name of the desktop this thread runs on
    var s = new System.Text.StringBuilder(256); int needed;
    return GetUserObjectInformationW(GetThreadDesktop(GetCurrentThreadId()), 2, s, 256, out needed) ? s.ToString() : null;
  }
  // Whether this thread's desktop is receiving input (UOI_IO); null when it cannot be read.
  public static bool? Receiving() {
    int value, needed;
    return GetUserObjectFlag(GetThreadDesktop(GetCurrentThreadId()), 6, out value, 4, out needed) ? (bool?)(value != 0) : null;
  }
  // Why a desktop is not one the probe may look at, or null: it must be the look's (a `wiki-look-` name equal to
  // the one the runner gave) and must not be receiving input, which the owner's screen always is.
  public static string Guard(string name, bool? receiving, string expected) {
    if (name == null || name != expected || !name.StartsWith("wiki-look-", StringComparison.Ordinal)) return "not on the look's owned desktop";
    if (receiving != false) return "the desktop is, or may be, receiving input";
    return null;
  }
  // The one visible top-level window on this thread's desktop whose process is in the fixture's job `job` and
  // whose title is `title`; zero when there is none or more than one. Membership is checked first, so no other
  // process's window has its title read.
  public static IntPtr Find(string title, string job) {
    IntPtr jobHandle = OpenJobObjectW(0x4, false, job);   // JOB_OBJECT_QUERY
    if (jobHandle == IntPtr.Zero) return IntPtr.Zero;
    var found = new System.Collections.Generic.List<IntPtr>();
    try {
      EnumWindows((h, l) => {
        if (!IsWindowVisible(h)) return true;
        uint pid; GetWindowThreadProcessId(h, out pid);
        IntPtr p = OpenProcess(0x1000, false, pid); bool inside = false;
        if (p != IntPtr.Zero) { IsProcessInJob(p, jobHandle, out inside); CloseHandle(p); }
        if (!inside) return true;
        var s = new System.Text.StringBuilder(512); GetWindowTextW(h, s, 512);
        if (s.ToString() == title) found.Add(h);
        return true;
      }, IntPtr.Zero);
    } finally { CloseHandle(jobHandle); }
    return found.Count == 1 ? found[0] : IntPtr.Zero;
  }
  public static string Capture(IntPtr hwnd, string file) {
    IntPtr dev, ctx; int level;
    int hr = D3D11CreateDevice(IntPtr.Zero, 1, IntPtr.Zero, 0x20, IntPtr.Zero, 0, 7, out dev, out level, out ctx);
    if (hr != 0) return "D3D11CreateDevice " + hr.ToString("X");
    IntPtr dxgi; Guid dxgiId = new Guid("54ec77fa-1377-44e6-8c32-88fd5f44c84c");
    Marshal.QueryInterface(dev, ref dxgiId, out dxgi);
    IntPtr raw; hr = CreateDirect3D11DeviceFromDXGIDevice(dxgi, out raw);
    if (hr != 0) return "CreateDirect3D11DeviceFromDXGIDevice " + hr.ToString("X");
    var device = (IDirect3DDevice)Marshal.GetObjectForIUnknown(raw);
    var factory = (IGraphicsCaptureItemInterop)WindowsRuntimeMarshal.GetActivationFactory(typeof(GraphicsCaptureItem));
    Guid itemId = new Guid("79C3F95B-31F7-4EC2-A464-632EF5D30760");
    IntPtr itemRaw = factory.CreateForWindow(hwnd, ref itemId);
    var item = (GraphicsCaptureItem)Marshal.GetObjectForIUnknown(itemRaw);
    string seen = "item '" + item.DisplayName + "' " + item.Size.Width + "x" + item.Size.Height + "; ";
    if (item.Size.Width <= 0 || item.Size.Height <= 0) return seen + "empty item";
    var pool = Direct3D11CaptureFramePool.CreateFreeThreaded(device, DirectXPixelFormat.B8G8R8A8UIntNormalized, 1, item.Size);
    var session = pool.CreateCaptureSession(item);
    session.StartCapture();
    Direct3D11CaptureFrame frame = null;
    for (int i = 0; i < 50 && frame == null; i++) { frame = pool.TryGetNextFrame(); if (frame == null) Thread.Sleep(100); }
    if (frame == null) { session.Dispose(); pool.Dispose(); return seen + "no frame within 5 s; item " + item.Size.Width + "x" + item.Size.Height; }
    var bmp = SoftwareBitmap.CreateCopyFromSurfaceAsync(frame.Surface).AsTask().Result;
    var stream = new InMemoryRandomAccessStream();
    var enc = BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, stream).AsTask().Result;
    enc.SetSoftwareBitmap(bmp); enc.FlushAsync().AsTask().Wait();
    var bytes = new byte[stream.Size]; var reader = new DataReader(stream.GetInputStreamAt(0));
    reader.LoadAsync((uint)stream.Size).AsTask().Wait(); reader.ReadBytes(bytes); File.WriteAllBytes(file, bytes);
    string size = frame.ContentSize.Width + "x" + frame.ContentSize.Height;
    frame.Dispose(); session.Dispose(); pool.Dispose();
    return seen + "ok " + size;
  }
}

public static class Program {
  public static int Main(string[] args) {
    // wgc.exe <out> <expected desktop> <fixture job> <title>: refuses unless it runs on the look's owned desktop.
    // wgc.exe --check: checks the desktop guard on modeled desktops and opens nothing.
    if (args.Length == 1 && args[0] == "--check") {
      string input = "the desktop is, or may be, receiving input", other = "not on the look's owned desktop";
      bool passed = Wgc.Guard("wiki-look-ab", false, "wiki-look-ab") == null
        && Wgc.Guard("wiki-look-ab", true, "wiki-look-ab") == input && Wgc.Guard("wiki-look-ab", null, "wiki-look-ab") == input
        && Wgc.Guard("Default", false, "Default") == other && Wgc.Guard("Custom", false, "Custom") == other
        && Wgc.Guard("wiki-look-ab", false, "wiki-look-cd") == other && Wgc.Guard(null, false, "") == other;
      Console.WriteLine(passed ? "guard check passed" : "guard check FAILED");
      return passed ? 0 : 1;
    }
    string outDir = args[0], desktop = args[1], job = args[2], title = args[3];
    var result = new System.Text.StringBuilder();
    try {
      string here = Wgc.Desktop(); bool? receiving = Wgc.Receiving();
      result.Append("desktop=" + here + "\nreceiving_input=" + receiving + "\n");
      string refusal = Wgc.Guard(here, receiving, desktop);
      if (refusal != null) {   // before any window is enumerated
        result.Append("refused=" + refusal + "\n");
        File.WriteAllText(Path.Combine(outDir, "wgc-" + title + ".txt"), result.ToString());
        return 3;
      }
      result.Append("supported=" + Wgc.Supported() + "\n");
      IntPtr h = IntPtr.Zero;
      for (int i = 0; i < 50 && h == IntPtr.Zero; i++) { h = Wgc.Find(title, job); if (h == IntPtr.Zero) Thread.Sleep(200); }
      result.Append("hwnd=" + h.ToInt64() + "\n");
      if (h != IntPtr.Zero) result.Append("capture=" + Wgc.Capture(h, Path.Combine(outDir, "wgc-" + title + ".png")) + "\n");
    } catch (Exception e) { result.Append("error=" + e + "\n"); }
    File.WriteAllText(Path.Combine(outDir, "wgc-" + title + ".txt"), result.ToString());
    return 0;
  }
}
