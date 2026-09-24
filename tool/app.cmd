@echo off
rem app - open the wiki-agent window.
rem
rem ASCII only, on purpose. cmd.exe reads this file as CP949 and one non-ASCII
rem character has killed a hook in this repo before.
rem
rem Builds the Tauri shell when its source changed (the first build takes a few
rem minutes), then starts it and lets this window go. The shell starts the
rem Python server itself, on a free port, and takes it down when it closes.
rem
rem The window is kept open on any failure. Double-clicking a .cmd closes the
rem window the instant the script ends, so a one-line error looks like "it just
rem closed by itself". It did that once.

setlocal
chcp 65001 >nul
set "ROOT=%~dp0.."

if not exist "%ROOT%\web\dist\index.html" (
  echo Run: python "%~dp0setup_chat.py" install --agent both
  echo See docs\chat-setup.md.
  goto :halt
)

set "CARGO=cargo"
where cargo >nul 2>nul || set "CARGO=%USERPROFILE%\.cargo\bin\cargo.exe"
"%CARGO%" --version >nul 2>nul || (
  echo Rust is needed to build the window: https://rustup.rs
  goto :halt
)

"%CARGO%" build --release --quiet --manifest-path "%ROOT%\web\src-tauri\Cargo.toml"
if errorlevel 1 goto :halt
start "" "%ROOT%\web\src-tauri\target\release\wiki-agent.exe"
exit /b 0

:halt
echo.
echo ^(stopped. press any key to close this window^)
pause >nul
exit /b 1
