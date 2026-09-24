#!/bin/sh
# app - open the wiki-agent window. The macOS/Linux twin of app.cmd.
#
# The .command extension is what makes Finder run it on a double-click. On
# Linux, or from any terminal, run it by path: tool/app.command
#
# Builds the Tauri shell when its source changed (the first build takes a few
# minutes), then starts it. The shell starts the Python server itself and takes
# it down when it closes. The window is kept open on any failure, so a one-line
# error stays readable.

halt() {
  [ -n "$1" ] && echo "$1"
  echo
  printf '(stopped. press enter to close this window)'
  read -r _
  exit 1
}

cd "$(dirname "$0")/.." || exit 1

if [ ! -f web/dist/index.html ]; then
  halt "Run: python3 tool/setup_chat.py install --agent both
See docs/chat-setup.md."
fi

CARGO=$(command -v cargo || echo "$HOME/.cargo/bin/cargo")
"$CARGO" --version >/dev/null 2>&1 || halt "Rust is needed to build the window: https://rustup.rs"

"$CARGO" build --release --quiet --manifest-path web/src-tauri/Cargo.toml || halt ""
nohup web/src-tauri/target/release/wiki-agent >/dev/null 2>&1 &
