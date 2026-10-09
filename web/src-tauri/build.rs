// The page is served by the Python sidecar, a remote origin to Tauri, and a
// remote origin may call only the commands a manifest declares and a
// capability grants. Without this, `pty_open` came back "not allowed by ACL".
const COMMANDS: &[&str] = &["pty_open", "pty_write", "pty_resize", "pty_close", "open_url", "restart"];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("tauri-build failed");
}
