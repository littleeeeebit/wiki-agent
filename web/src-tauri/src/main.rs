//! The wiki-agent window. Rust does three things here and nothing else:
//! starts the Python server (`tool/main`) as a sidecar, opens a window on it,
//! and holds the terminals. Everything the screen knows besides the terminal
//! comes from that server, so the page is the same page a browser gets.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::HashMap;
use std::fs::File;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, Url, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_updater::UpdaterExt;

/// How long the server gets to answer before the window says it did not.
/// The first start imports FastAPI and reads the wiki; seconds, not minutes.
const BOOT: Duration = Duration::from_secs(40);

struct Sidecar(Mutex<Option<Child>>);
struct ServerPort(u16);
struct Layout { root: PathBuf, data: PathBuf }
static UPDATING: AtomicBool = AtomicBool::new(false);

struct Pty {
    writer: Box<dyn Write + Send>,
    master: Box<dyn MasterPty + Send>,
    child: Box<dyn portable_pty::Child + Send + Sync>,
    /// The label of the window that opened it: that window's output, and ended with it.
    window: String,
}

#[derive(Default)]
struct Ptys {
    next: AtomicU32,
    open: Mutex<HashMap<u32, Pty>>,
}

/// The numbers of windows opened after the first (`main-2`, ...).
static WINDOWS: AtomicU32 = AtomicU32::new(1);

/// The repository this was built from. A dev-run app, so the build's own
/// location is where the wiki is.
#[cfg(not(desktop_bundle))]
fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// `.venv`'s Python when the setup made one, else whatever `python` is.
fn python(root: &Path) -> PathBuf {
    if cfg!(desktop_bundle) {
        return root.parent().unwrap().join("python").join("python.exe");
    }
    let venv = if cfg!(windows) {
        root.join(".venv").join("Scripts").join("python.exe")
    } else {
        root.join(".venv").join("bin").join("python")
    };
    if venv.is_file() { venv } else { PathBuf::from("python") }
}

/// Start `python tool/main` on a free port. Its stdin stays with us: when this
/// process goes, however it goes, the pipe closes and the server shuts down
/// with its agent processes.
fn spawn_sidecar(root: &Path, data: &Path, log: &Path, port: Option<u16>) -> std::io::Result<(Child, u16)> {
    let port = match port { Some(port) => port, None => TcpListener::bind("127.0.0.1:0")?.local_addr()?.port() };
    std::fs::create_dir_all(log.parent().unwrap())?;
    let mut cmd = Command::new(python(root));
    cmd.arg(root.join("tool").join("main"))
        .args(["--port", &port.to_string(), "--exit-with-stdin"])
        .current_dir(data)
        .env("WIKI_ROOT", root)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::from(File::create(log)?))
        .stderr(Stdio::from(File::create(log.with_extension("err.log"))?));
    if cfg!(desktop_bundle) { cmd.env("WIKI_DATA_HOME", data); }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // No console window for the server. The agent CLIs it starts inherit
        // the hidden console instead of each opening one.
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    Ok((cmd.spawn()?, port))
}

fn answers(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

/// A page saying why there is no screen. A window that just never appears is
/// the failure this repository keeps being burned by.
fn failure(reason: &str, log: &Path) -> Url {
    let tail = std::fs::read_to_string(log.with_extension("err.log")).unwrap_or_default();
    let tail: String = tail.chars().rev().take(3000).collect::<Vec<_>>().into_iter().rev().collect();
    let html = format!(
        "<meta charset=utf-8><body style='font:14px system-ui;padding:24px;background:#14161a;color:#eceae5'>\
         <h3>서버가 뜨지 않았다</h3><p>{}</p><p>기록: <code>{}</code></p><pre style='white-space:pre-wrap'>{}</pre>",
        escape(reason), escape(&log.with_extension("err.log").display().to_string()), escape(&tail)
    );
    let encoded: String = html.bytes().map(|b| match b {
        b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (b as char).to_string(),
        _ => format!("%{b:02X}"),
    }).collect();
    Url::parse(&format!("data:text/html;charset=utf-8,{encoded}")).unwrap()
}

fn escape(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

fn open_window(app: AppHandle, port: u16, log: PathBuf) {
    let started = Instant::now();
    let url = loop {
        if answers(port) {
            break Url::parse(&format!("http://127.0.0.1:{port}/")).unwrap();
        }
        let exited = app.state::<Sidecar>().0.lock().unwrap().as_mut()
            .and_then(|child| child.try_wait().ok().flatten());
        if let Some(status) = exited {
            break failure(&format!("python tool/main 이 끝났다 ({status})"), &log);
        }
        if started.elapsed() > BOOT {
            break failure(&format!("{}초 안에 답이 없다", BOOT.as_secs()), &log);
        }
        thread::sleep(Duration::from_millis(150));
    };
    if let Err(err) = window(&app, "main", url) {
        eprintln!("창을 못 열었다: {err}");
        app.exit(1);
    }
}

fn window(app: &AppHandle, label: &str, url: Url) -> tauri::Result<tauri::WebviewWindow> {
    WebviewWindowBuilder::new(app, label, WebviewUrl::External(url))
        .title("wiki-agent")
        .inner_size(1480.0, 920.0)
        .min_inner_size(960.0, 600.0)
        .build()
}

/// Another window on the same server page. It starts on the server's default
/// project and keeps its own after (`sessionStorage`). Async: a window built
/// inside a synchronous command deadlocks on Windows.
#[tauri::command]
async fn new_window(app: AppHandle, from: tauri::WebviewWindow) -> Result<(), String> {
    let mut url = from.url().map_err(|e| e.to_string())?;
    url.set_path("/");
    url.set_query(None);
    url.set_fragment(None);
    let label = format!("main-{}", WINDOWS.fetch_add(1, Ordering::SeqCst) + 1);
    window(&app, &label, url).map(|_| ()).map_err(|err| format!("창을 못 열었다: {err}"))
}

/// A closed window's terminals end with it. With another window still open
/// the app goes on, so neither `pty_close` nor `RunEvent::Exit` runs for them.
fn end_terminals(app: &AppHandle, label: &str) {
    let ptys = app.state::<Ptys>();
    let mut open = ptys.open.lock().unwrap();
    let ids: Vec<u32> = open.iter().filter(|(_, pty)| pty.window == label).map(|(id, _)| *id).collect();
    for id in ids {
        if let Some(mut pty) = open.remove(&id) {
            let _ = pty.child.kill();
        }
    }
}

/// Close the pipe and give the server time to close its sessions; kill it
/// only if it does not go.
fn stop_sidecar(app: &AppHandle) {
    let Some(mut child) = app.state::<Sidecar>().0.lock().unwrap().take() else { return };
    drop(child.stdin.take());
    let started = Instant::now();
    while started.elapsed() < Duration::from_secs(10) {
        if let Ok(Some(_)) = child.try_wait() {
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
}

/// The update card's restart. The page has already asked about running work;
/// `exit` does not ask again. The launcher starts first and waits for this
/// process to go, then rebuilds the shell when the pull changed it and opens
/// the window again: a relaunch left to the end of shutdown never ran when
/// the shutdown was cut short, and the shell cannot be relinked while it runs.
#[tauri::command]
fn restart(app: AppHandle) -> Result<(), String> {
    if cfg!(desktop_bundle) {
        stop_children(&app);
        app.restart();
    }
    let tool = app.state::<Layout>().root.join("tool");
    let pid = std::process::id();
    #[cfg(windows)]
    let launched = {
        use std::os::windows::process::CommandExt;
        // `start` gives it a console of its own, so its message and pause
        // work as they do on a double-click. `cmd /c`, not the `.cmd` itself,
        // which `start` would leave open behind `cmd /k`; `.\` because cmd
        // may be told not to look in the current folder.
        Command::new("cmd")
            .raw_arg(format!("/c start \"\" /d \"{}\" cmd /c .\\app.cmd --after {pid}", tool.display()))
            .spawn()
    };
    #[cfg(not(windows))]
    let launched = Command::new("sh").arg(tool.join("app.command")).args(["--after", &pid.to_string()]).spawn();
    launched.map_err(|err| format!("다시 띄우지 못했다: {err}"))?;
    app.exit(0);
    Ok(())
}

fn stop_children(app: &AppHandle) {
    for (_, mut pty) in app.state::<Ptys>().open.lock().unwrap().drain() {
        let _ = pty.child.kill();
        let _ = pty.child.wait();
    }
    stop_sidecar(app);
}

#[tauri::command]
async fn package_update(app: AppHandle) -> Result<serde_json::Value, String> {
    if !cfg!(desktop_bundle) { return Ok(serde_json::json!({"packaged": false})); }
    let updater = app.updater_builder().timeout(Duration::from_secs(30)).build().map_err(|e| e.to_string())?;
    let update = updater.check().await.map_err(|e| e.to_string())?;
    Ok(match update {
        Some(update) => serde_json::json!({"packaged": true, "version": update.version, "notes": update.body}),
        None => serde_json::json!({"packaged": true}),
    })
}

#[tauri::command]
async fn package_install(app: AppHandle, version: String) -> Result<(), String> {
    if !cfg!(desktop_bundle) { return Err("개발 실행에는 설치 업데이트를 적용하지 않는다".into()); }
    if UPDATING.swap(true, Ordering::SeqCst) { return Err("다른 창에서 업데이트 중이다".into()); }
    let result = async {
        let closing = app.clone();
        let updater = app.updater_builder().timeout(Duration::from_secs(600))
            .on_before_exit(move || { stop_children(&closing); })
            .build().map_err(|e| e.to_string())?;
        let update = updater.check().await.map_err(|e| e.to_string())?.ok_or("게시된 업데이트가 없다")?;
        if update.version != version { return Err("업데이트가 바뀌었다. 다시 확인하세요".into()); }
        let mut received: u64 = 0;
        let bytes = update.download(|length, total| {
            received += length as u64;
            let _ = app.emit("package-download", serde_json::json!({"received": received, "total": total}));
        }, || {}).await.map_err(|e| e.to_string())?;
        // The plugin verifies the signature before closing anything. A failed download keeps the app usable.
        if let Err(error) = update.install(bytes) {
            // Windows may fail to launch the installer after the shutdown callback.
            let layout = app.state::<Layout>();
            let log = layout.data.join("raw/main.log");
            if app.state::<Sidecar>().0.lock().unwrap().is_none() {
                match spawn_sidecar(&layout.root, &layout.data, &log, Some(app.state::<ServerPort>().0)) {
                    Ok((child, _)) => { app.state::<Sidecar>().0.lock().unwrap().replace(child); }
                    Err(recovery) => return Err(format!("{error}; 서버 재시작 실패: {recovery}. 앱을 다시 여세요")),
                }
            }
            return Err(error.to_string());
        }
        Ok(())
    }.await;
    UPDATING.store(false, Ordering::SeqCst);
    result
}

// -- The terminal ---------------------------------------------------------
//
// A shell for the person, in the worktree they picked. Output goes out as
// bytes, not text: a chunk can end in the middle of a Korean character, and
// xterm.js joins the halves only if it gets them as bytes. One event name for
// every terminal, carrying the id, so the screen can listen before it knows
// which id it will get and keep the first prompt.

fn shell(app: &AppHandle) -> Result<CommandBuilder, String> {
    if cfg!(windows) {
        // Ask the running server's resolver, including its post-install probe.
        let port = app.state::<ServerPort>().0;
        let addr = SocketAddr::from(([127, 0, 0, 1], port));
        let mut stream = TcpStream::connect_timeout(&addr, Duration::from_secs(3)).map_err(|e| e.to_string())?;
        stream.set_read_timeout(Some(Duration::from_secs(25))).map_err(|e| e.to_string())?;
        write!(stream, "GET /api/shell/terminal HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n")
            .map_err(|e| e.to_string())?;
        let mut response = String::new();
        stream.read_to_string(&mut response).map_err(|e| e.to_string())?;
        let (headers, body) = response.split_once("\r\n\r\n").ok_or("셸 응답을 읽지 못했다")?;
        if !headers.starts_with("HTTP/1.1 200 ") && !headers.starts_with("HTTP/1.0 200 ") {
            return Err(body.to_string());
        }
        let data: serde_json::Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
        let argv = data["argv"].as_array().ok_or("셸 실행 경로가 없다")?;
        let mut words = argv.iter().map(|v| v.as_str().ok_or("잘못된 셸 인수"));
        let executable = words.next().ok_or("셸 실행 경로가 없다")??;
        let mut cmd = CommandBuilder::new(executable);
        for word in words { cmd.arg(word?); }
        return Ok(cmd);
    }
    Ok(CommandBuilder::new_default_prog())
}

#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    let parsed = Url::parse(&url).map_err(|_| "잘못된 링크다")?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none() {
        return Err("웹 링크만 기본 브라우저에서 연다".into());
    }
    let launcher = if cfg!(windows) { "explorer.exe" } else if cfg!(target_os = "macos") { "open" } else { "xdg-open" };
    let mut child = Command::new(launcher).arg(parsed.as_str()).spawn().map_err(|e| e.to_string())?;
    thread::spawn(move || { let _ = child.wait(); });
    Ok(())
}

#[tauri::command]
fn pty_open(app: AppHandle, from: tauri::WebviewWindow, ptys: State<Ptys>, cwd: String, cols: u16, rows: u16)
    -> Result<u32, String> {
    if !Path::new(&cwd).is_dir() {
        return Err(format!("그런 폴더가 없다: {cwd}"));
    }
    let pair = native_pty_system()
        .openpty(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })
        .map_err(|e| e.to_string())?;
    let mut cmd = shell(&app)?;
    cmd.cwd(&cwd);
    let child = pair.slave.spawn_command(cmd).map_err(|e| e.to_string())?;
    drop(pair.slave);
    let mut reader = pair.master.try_clone_reader().map_err(|e| e.to_string())?;
    let writer = pair.master.take_writer().map_err(|e| e.to_string())?;
    let id = ptys.next.fetch_add(1, Ordering::SeqCst) + 1;
    let window = from.label().to_string();
    ptys.open.lock().unwrap().insert(id, Pty { writer, master: pair.master, child, window: window.clone() });

    thread::spawn(move || {
        let mut buf = [0u8; 16 * 1024];
        loop {
            match reader.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    let _ = app.emit_to(window.as_str(), "pty-out", (id, buf[..n].to_vec()));
                }
            }
        }
        let _ = app.emit_to(window.as_str(), "pty-exit", id);
    });
    Ok(id)
}

#[tauri::command]
fn pty_write(ptys: State<Ptys>, id: u32, data: String) -> Result<(), String> {
    let mut open = ptys.open.lock().unwrap();
    let pty = open.get_mut(&id).ok_or("그 터미널이 없다")?;
    pty.writer.write_all(data.as_bytes()).map_err(|e| e.to_string())
}

#[tauri::command]
fn pty_resize(ptys: State<Ptys>, id: u32, cols: u16, rows: u16) -> Result<(), String> {
    let open = ptys.open.lock().unwrap();
    let pty = open.get(&id).ok_or("그 터미널이 없다")?;
    pty.master
        .resize(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn pty_close(ptys: State<Ptys>, id: u32) {
    let pty = ptys.open.lock().unwrap().remove(&id);
    if let Some(mut pty) = pty {
        let _ = pty.child.kill();
        // Answer only once it has exited: the screen removes the worktree
        // next, and Windows keeps a folder a process still stands in.
        let _ = pty.child.wait();
    }
}

fn main() {
    let app = tauri::Builder::default()
        // Stands behind the page's `window.Notification`, which a webview
        // alone does not have.
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Ptys::default())
        .manage(Sidecar(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![pty_open, pty_write, pty_resize, pty_close, open_url, restart, new_window, package_update, package_install])
        .on_window_event(|window, event| {
            if let WindowEvent::Destroyed = event {
                end_terminals(window.app_handle(), window.label());
            }
        })
        .setup(move |app| {
            #[cfg(desktop_bundle)]
            let root = dunce::simplified(&app.path().resource_dir()?).join("payload/app");
            #[cfg(not(desktop_bundle))]
            let root = repo();
            let data = if cfg!(desktop_bundle) {
                std::env::var_os("WIKI_DATA_HOME").map(PathBuf::from)
                    .unwrap_or_else(|| PathBuf::from(std::env::var_os("LOCALAPPDATA").expect("LOCALAPPDATA is missing")).join("wiki-agent-data"))
            } else { root.clone() };
            let log = data.join("raw/main.log");
            std::fs::create_dir_all(&data)?;
            app.manage(Layout { root: root.clone(), data: data.clone() });
            let (child, port) = match spawn_sidecar(&root, &data, &log, None) {
                Ok(started) => started,
                Err(error) => {
                    app.manage(ServerPort(0));
                    window(app.handle(), "main", failure(&format!("서버 실행 실패: {error}"), &log))?;
                    return Ok(());
                }
            };
            app.manage(ServerPort(port));
            app.state::<Sidecar>().0.lock().unwrap().replace(child);
            let handle = app.handle().clone();
            thread::spawn(move || open_window(handle, port, log));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("tauri 를 못 띄웠다");
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            stop_children(app);
        }
    });
}
