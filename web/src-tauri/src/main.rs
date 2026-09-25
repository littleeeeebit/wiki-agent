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
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, Url, WebviewUrl, WebviewWindowBuilder};

/// How long the server gets to answer before the window says it did not.
/// The first start imports FastAPI and reads the wiki; seconds, not minutes.
const BOOT: Duration = Duration::from_secs(40);

struct Sidecar(Mutex<Option<Child>>);

struct Pty {
    writer: Box<dyn Write + Send>,
    master: Box<dyn MasterPty + Send>,
    child: Box<dyn portable_pty::Child + Send + Sync>,
}

#[derive(Default)]
struct Ptys {
    next: AtomicU32,
    open: Mutex<HashMap<u32, Pty>>,
}

/// The repository this was built from. A dev-run app, so the build's own
/// location is where the wiki is.
fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// `.venv`'s Python when the setup made one, else whatever `python` is.
fn python(root: &Path) -> PathBuf {
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
fn spawn_sidecar(root: &Path, log: &Path) -> std::io::Result<(Child, u16)> {
    let port = TcpListener::bind("127.0.0.1:0")?.local_addr()?.port();
    std::fs::create_dir_all(log.parent().unwrap())?;
    let mut cmd = Command::new(python(root));
    cmd.arg(root.join("tool").join("main"))
        .args(["--port", &port.to_string(), "--exit-with-stdin"])
        .current_dir(root)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::from(File::create(log)?))
        .stderr(Stdio::from(File::create(log.with_extension("err.log"))?));
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
    let built = WebviewWindowBuilder::new(&app, "main", WebviewUrl::External(url))
        .title("wiki-agent")
        .inner_size(1480.0, 920.0)
        .min_inner_size(960.0, 600.0)
        .build();
    if let Err(err) = built {
        eprintln!("창을 못 열었다: {err}");
        app.exit(1);
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

// -- The terminal ---------------------------------------------------------
//
// A shell for the person, in the worktree they picked. Output goes out as
// bytes, not text: a chunk can end in the middle of a Korean character, and
// xterm.js joins the halves only if it gets them as bytes. One event name for
// every terminal, carrying the id, so the screen can listen before it knows
// which id it will get and keep the first prompt.

fn shell() -> CommandBuilder {
    if cfg!(windows) {
        for name in ["pwsh.exe", "powershell.exe"] {
            if which(name) {
                return CommandBuilder::new(name);
            }
        }
    }
    CommandBuilder::new_default_prog()
}

fn which(name: &str) -> bool {
    std::env::var_os("PATH").is_some_and(|path| std::env::split_paths(&path).any(|dir| dir.join(name).is_file()))
}

#[tauri::command]
fn pty_open(app: AppHandle, ptys: State<Ptys>, cwd: String, cols: u16, rows: u16) -> Result<u32, String> {
    if !Path::new(&cwd).is_dir() {
        return Err(format!("그런 폴더가 없다: {cwd}"));
    }
    let pair = native_pty_system()
        .openpty(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })
        .map_err(|e| e.to_string())?;
    let mut cmd = shell();
    cmd.cwd(&cwd);
    let child = pair.slave.spawn_command(cmd).map_err(|e| e.to_string())?;
    drop(pair.slave);
    let mut reader = pair.master.try_clone_reader().map_err(|e| e.to_string())?;
    let writer = pair.master.take_writer().map_err(|e| e.to_string())?;
    let id = ptys.next.fetch_add(1, Ordering::SeqCst) + 1;
    ptys.open.lock().unwrap().insert(id, Pty { writer, master: pair.master, child });

    thread::spawn(move || {
        let mut buf = [0u8; 16 * 1024];
        loop {
            match reader.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    let _ = app.emit("pty-out", (id, buf[..n].to_vec()));
                }
            }
        }
        let _ = app.emit("pty-exit", id);
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
    let root = repo();
    let log = root.join("raw").join("main.log");
    let app = tauri::Builder::default()
        .manage(Ptys::default())
        .manage(Sidecar(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![pty_open, pty_write, pty_resize, pty_close])
        .setup(move |app| {
            let (child, port) = spawn_sidecar(&root, &log)?;
            app.state::<Sidecar>().0.lock().unwrap().replace(child);
            let handle = app.handle().clone();
            thread::spawn(move || open_window(handle, port, log));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("tauri 를 못 띄웠다");
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            for (_, mut pty) in app.state::<Ptys>().open.lock().unwrap().drain() {
                let _ = pty.child.kill();
            }
            stop_sidecar(app);
        }
    });
}
