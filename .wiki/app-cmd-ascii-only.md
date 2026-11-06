---
scope: project
severity: landmine
repeat: rule
triggers: ["app\\.cmd", "\\.cmd\\b", "배치 ?파일", "batch file", "cp949", "chcp", "런처", "launcher"]
slots: []
sources: [tool/app.cmd#L4]
links: []
---

# `tool/app.cmd` and every `.cmd` stay pure ASCII, and keep the window open on failure

Rule. Write `.cmd` files in ASCII only: comments, `echo` text, paths, all of
it. cmd.exe reads a batch file as CP949 before `chcp 65001` takes effect.
Korean belongs in the Python or the screen the script starts, not in the
script. Keep the `:halt` path that waits for a key: a double-clicked `.cmd`
closes the instant it ends, and the error vanishes with it.

Why. "One non-ASCII character has killed a hook in this repo before", and a
launcher that closed on a one-line error looked like "it just closed by
itself" (`tool/app.cmd:4-13`).

What goes wrong. The launcher or hook dies before printing anything a
person can read. The window builds from `tool/app.cmd`, so nothing opens
and nothing says why.
