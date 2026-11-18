
using System; using System.Runtime.InteropServices; using System.Threading; using System.IO;
using System.Runtime.InteropServices.WindowsRuntime;
using Windows.Graphics.Capture; using Windows.Graphics.DirectX; using Windows.Graphics.DirectX.Direct3D11;
using Windows.Graphics.Imaging; using Windows.Storage.Streams;
[ComImport, Guid("3628E81B-3CAC-4C60-B7F4-23CE0E0C3356"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IGraphicsCaptureItemInterop { IntPtr CreateForWindow(IntPtr window, ref Guid iid); IntPtr CreateForMonitor(IntPtr monitor, ref Guid iid); }
public static class Wgc {
  [DllImport("d3d11.dll")] static extern int D3D11CreateDevice(IntPtr a, int type, IntPtr sw, uint flags, IntPtr levels, uint n, uint sdk, out IntPtr dev, out int level, out IntPtr ctx);
  [DllImport("d3d11.dll")] static extern int CreateDirect3D11DeviceFromDXGIDevice(IntPtr dxgi, out IntPtr device);
  [DllImport("user32", CharSet=CharSet.Unicode)] static extern IntPtr FindWindowW(string cls, string title);
  public static string Supported() { return GraphicsCaptureSession.IsSupported().ToString(); }
  public static IntPtr Find(string title) { return FindWindowW(null, title); }
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
    string outDir = args[0], title = args[1];
    var result = new System.Text.StringBuilder();
    try {
      result.Append("supported=" + Wgc.Supported() + "\n");
      IntPtr h = IntPtr.Zero;
      for (int i = 0; i < 50 && h == IntPtr.Zero; i++) { h = Wgc.Find(title); if (h == IntPtr.Zero) Thread.Sleep(200); }
      result.Append("hwnd=" + h.ToInt64() + "\n");
      if (h != IntPtr.Zero) result.Append("capture=" + Wgc.Capture(h, Path.Combine(outDir, "wgc-" + title + ".png")) + "\n");
    } catch (Exception e) { result.Append("error=" + e + "\n"); }
    File.WriteAllText(Path.Combine(outDir, "wgc-" + title + ".txt"), result.ToString());
    return 0;
  }
}
