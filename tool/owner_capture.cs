// The owner's desktop round's capture helper (plan 9e5, docs/plans/oneshot/9-kinds.md "The owner's desktop round").
// The server builds it with the .NET Framework's csc against the system's WinMetadata (`owner_round.helper`) and
// starts it in a job of the round's own. What it reads is limited where the frame is sampled, never afterwards:
//
//   owner_capture window <hwnd> <dir>   A window-bound read. It creates a Windows Graphics Capture item for the
//       handle, which is tied to that one window instance and closes when the window is destroyed, and writes
//       `item.json`. It then waits for the server, which proves its binding again, to write `go` or `stop`. Only on
//       `go`, with the item still open, does it start the frame pool and save the first frame as `window.png`. An
//       empty or closed item, a `stop`, or no answer in time reads nothing. The item, session and pool are released
//       on every exit, and `result.json` says what happened.
//   owner_capture surface <x> <y> <width> <height> <dir>   A surface read of exactly that region of the screen,
//       layered windows included, saved as `surface.png`, with `result.json`.
//   owner_capture --check   Builds and starts; reads nothing.
using System; using System.IO; using System.Runtime.InteropServices; using System.Threading;
using System.Runtime.InteropServices.WindowsRuntime; using System.Drawing; using System.Drawing.Imaging;
using Windows.Foundation; using Windows.Graphics.Capture; using Windows.Graphics.DirectX;
using Windows.Graphics.DirectX.Direct3D11; using Windows.Graphics.Imaging; using Windows.Storage.Streams;

[ComImport, Guid("3628E81B-3CAC-4C60-B7F4-23CE0E0C3356"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IGraphicsCaptureItemInterop { IntPtr CreateForWindow(IntPtr window, ref Guid iid); IntPtr CreateForMonitor(IntPtr monitor, ref Guid iid); }

public static class Program {
  const int ANSWER_MS = 10000, FRAME_MS = 5000;
  [DllImport("d3d11.dll")] static extern int D3D11CreateDevice(IntPtr a, int type, IntPtr sw, uint flags, IntPtr levels, uint n, uint sdk, out IntPtr dev, out int level, out IntPtr ctx);
  [DllImport("d3d11.dll")] static extern int CreateDirect3D11DeviceFromDXGIDevice(IntPtr dxgi, out IntPtr device);
  [DllImport("user32")] static extern bool SetProcessDpiAwarenessContext(IntPtr value);

  static void Write(string dir, string name, string json) {   // replaced whole, so the server never reads half
    string part = Path.Combine(dir, name + ".part");
    File.WriteAllText(part, json);
    File.Copy(part, Path.Combine(dir, name), true);
    File.Delete(part);
  }
  static string Quote(string s) { return "\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", " ") + "\""; }
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
    GraphicsCaptureItem item = null; Direct3D11CaptureFramePool pool = null; GraphicsCaptureSession session = null;
    Direct3D11CaptureFrame frame = null; bool closed = false;
    try {
      if (!GraphicsCaptureSession.IsSupported()) { Write(dir, "item.json", "{\"empty\": true, \"why\": \"capture is not supported\"}"); return "{\"read\": false, \"why\": \"capture is not supported\"}"; }
      var factory = (IGraphicsCaptureItemInterop)WindowsRuntimeMarshal.GetActivationFactory(typeof(GraphicsCaptureItem));
      Guid itemId = new Guid("79C3F95B-31F7-4EC2-A464-632EF5D30760");
      item = (GraphicsCaptureItem)Marshal.GetObjectForIUnknown(factory.CreateForWindow(hwnd, ref itemId));
      item.Closed += (s, e) => { closed = true; };
      if (item.Size.Width <= 0 || item.Size.Height <= 0) {   // unavailable: no pool is ever made for it
        Write(dir, "item.json", "{\"empty\": true, \"why\": \"the item is empty\"}");
        return "{\"read\": false, \"why\": \"the item is empty\"}";
      }
      Write(dir, "item.json", "{\"empty\": false, \"width\": " + item.Size.Width + ", \"height\": " + item.Size.Height + "}");
      string answer = Answer(dir);
      if (answer != "go") return "{\"read\": false, \"why\": " + Quote(answer == "stop" ? "the binding was not proven" : "no answer from the server") + "}";
      if (closed) return "{\"read\": false, \"why\": \"the window was destroyed: its item closed\"}";
      IntPtr dev, ctx, dxgi, raw; int level;
      int hr = D3D11CreateDevice(IntPtr.Zero, 1, IntPtr.Zero, 0x20, IntPtr.Zero, 0, 7, out dev, out level, out ctx);
      if (hr != 0) return "{\"read\": false, \"why\": \"D3D11CreateDevice " + hr.ToString("X") + "\"}";
      Guid dxgiId = new Guid("54ec77fa-1377-44e6-8c32-88fd5f44c84c");
      Marshal.QueryInterface(dev, ref dxgiId, out dxgi);
      hr = CreateDirect3D11DeviceFromDXGIDevice(dxgi, out raw);
      if (hr != 0) return "{\"read\": false, \"why\": \"CreateDirect3D11DeviceFromDXGIDevice " + hr.ToString("X") + "\"}";
      var device = (IDirect3DDevice)Marshal.GetObjectForIUnknown(raw);
      pool = Direct3D11CaptureFramePool.CreateFreeThreaded(device, DirectXPixelFormat.B8G8R8A8UIntNormalized, 1, item.Size);
      session = pool.CreateCaptureSession(item);
      session.StartCapture();
      for (int waited = 0; waited < FRAME_MS && frame == null && !closed; waited += 50) {
        frame = pool.TryGetNextFrame();
        if (frame == null) Thread.Sleep(50);
      }
      if (frame == null) return "{\"read\": false, \"why\": " + Quote(closed ? "the window was destroyed: its item closed" : "no frame within 5 s") + "}";
      var bitmap = Wait(SoftwareBitmap.CreateCopyFromSurfaceAsync(frame.Surface));
      var stream = new InMemoryRandomAccessStream();
      var encoder = Wait(BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, stream));
      encoder.SetSoftwareBitmap(bitmap);
      Wait(encoder.FlushAsync());
      var bytes = new byte[stream.Size];
      var reader = new DataReader(stream.GetInputStreamAt(0));
      Wait(reader.LoadAsync((uint)stream.Size));
      reader.ReadBytes(bytes);
      File.WriteAllBytes(Path.Combine(dir, "window.png"), bytes);
      return "{\"read\": true, \"width\": " + frame.ContentSize.Width + ", \"height\": " + frame.ContentSize.Height + "}";
    } finally {   // on every exit, the read or not
      if (frame != null) frame.Dispose();
      if (session != null) session.Dispose();
      if (pool != null) pool.Dispose();
      if (item != null) Marshal.FinalReleaseComObject(item);   // an item is not closable: its reference is let go
    }
  }

  static string Surface(int x, int y, int width, int height, string dir) {
    if (width <= 0 || height <= 0) return "{\"read\": false, \"why\": \"an empty region\"}";
    using (var bitmap = new Bitmap(width, height, PixelFormat.Format32bppArgb))
    using (var g = Graphics.FromImage(bitmap)) {
      g.CopyFromScreen(x, y, 0, 0, new Size(width, height), CopyPixelOperation.SourceCopy | CopyPixelOperation.CaptureBlt);
      bitmap.Save(Path.Combine(dir, "surface.png"), ImageFormat.Png);
    }
    return "{\"read\": true, \"width\": " + width + ", \"height\": " + height + "}";
  }

  public static int Main(string[] args) {
    SetProcessDpiAwarenessContext((IntPtr)(-4));   // per-monitor v2: physical pixels, as the driver reads them
    if (args.Length == 1 && args[0] == "--check") { Console.WriteLine("owner_capture ok"); return 0; }
    string dir = args[args.Length - 1], result;
    try {
      if (args.Length == 3 && args[0] == "window") result = Window((IntPtr)long.Parse(args[1]), dir);
      else if (args.Length == 6 && args[0] == "surface")
        result = Surface(int.Parse(args[1]), int.Parse(args[2]), int.Parse(args[3]), int.Parse(args[4]), dir);
      else { Console.Error.WriteLine("usage: owner_capture window <hwnd> <dir> | surface <x> <y> <w> <h> <dir> | --check"); return 2; }
    } catch (Exception e) { result = "{\"read\": false, \"why\": " + Quote(e.GetType().Name + ": " + e.Message) + "}"; }
    Write(dir, "result.json", result);
    return 0;
  }
}
