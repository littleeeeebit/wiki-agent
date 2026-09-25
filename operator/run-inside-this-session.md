---
scope: operator
severity: landmine
repeat: rule
triggers: ["pwsh", "powershell", "파워\\s*셸", "파워쉘", "Start-Process", "서버를? (띄|올|켜|실행|재시작)", "server 를? (띄|올|켜)", "uvicorn", "npm run dev", "런처", "\\.cmd 를?", "이 셀(에서|에|안)", "백그라운드로 (띄|돌)"]
slots: [server_stop]
enforce:
  deny: ["Bash(powershell -*)", "Bash(powershell.exe*)"]
sources: []
sources_withheld: true
links: [after-merge-cleanup, pick-up-async-results, hooks-fail-open, name-the-build-on-screen]
---

# The shell is `pwsh`, and servers start inside this cell

Rule. When PowerShell is needed, call `pwsh` — `powershell` is a different
product. Start servers inside this cell — not with `Start-Process`, not in a
detached window, not through a `.cmd` called from Git Bash; in the
background, as this cell's own background task. What the user has to run,
they type in this cell with the `!` prefix. Tell servers apart by port and
command line, never by process name, and never run `wsl --shutdown` to free a
port — start on a different port, and check not that `/health` returned 200
but that the response came from this project.

What goes wrong. **A failure does not look like one.** All three cases below
have that shape. The exit code, the log and the screen all look normal.

## What it burned — Git Bash calling a `.cmd` returns `exit 0`

The launcher never started and the exit code is still 0. The caller reads
success and moves on. Believing the server is up and starting a live run
throws that whole run away.

There is one more way a run gets thrown away, and it comes after the start.
This page covers what starts it and where — handing the running screen to a
person is held by [[name-the-build-on-screen]].

[[hooks-fail-open]] and `declared-continuation` reduce to the same sentence:
worse than something not running is something believed to be running. Here it
is the exit code telling that lie.

## `powershell` is not the old name for `pwsh`

The names look alike enough to read as aliases. They are not. The default
depth of `ConvertTo-Json`, how `-ErrorAction` propagates, `?.`, `Test-Json`
and the default encoding of `Invoke-RestMethod` all differ. A read-only query
sits in the intersection and happens to work, and that coincidence does not
survive to the next command.

## This cell owns the server

Start it with this cell's tools, through `pwsh`. If it has to run in the
background, run it as this cell's background task — then a notice arrives when
it ends, it dies when this cell dies, and the cleanup step recognises it as
its own. The discipline for receiving that notice is held by
[[pick-up-async-results]]: arm the wait first, and do not end the turn before
arming it.

What is forbidden is attaching it somewhere this cell cannot track:
`Start-Process`, a separate window, a detached process that outlives the
session. If something has to be run by the user, do not hand it over — say to
type it in this cell with the `!` prefix.

### Confirm by port, not by process name

```
{server_stop}
```

The "stop the server" step of the post-merge cleanup hangs here —
[[after-merge-cleanup]].

```powershell
Get-CimInstance Win32_Process -Filter 'ProcessId=<pid>' |
  Select-Object -ExpandProperty CommandLine
```

### `wsl --shutdown` is not a way to free one port

On Windows, Docker Desktop sits on WSL2, so that one line stops every
container on the machine — including other projects'.

Start on a different port instead, and check not that `/health` returned 200
but that the response came from this project.

## What layer 1 blocks and what it cannot

Only `powershell` is shaped like a command. `permissions.deny` stops it by
prefix, and the ladder ends there.

The other two cannot ride on layer 1. `Start-Process` goes inside
`pwsh -Command "..."`, where a prefix rule cannot reach. Calling a `.cmd`
through Git Bash is invisible to any command shape, because the `.cmd` itself
is a perfectly normal file and nothing says "this went through MSYS".

So those two are prose this page carries at layer 2. The point is not
pretending they can be blocked — a deny rule that does not bite would add a
fourth silent lie to the three this page counts.
