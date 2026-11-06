---
scope: project
severity: landmine
repeat: rule
triggers: ["python tool/main", "서버를? ?(다시|재|새로)? ?(시작|띄우|켜|실행)", "server\\.lock", "서버 재시작", "두 ?번째 (창|서버)", "개발 서버", "dev server", "8787", "포트"]
slots: []
sources: [docs/research/task-lifecycle-recovery.md#L50, 5520c8c (#71), tool/main/runtime.py#L159, tool/main/app.py#L67]
links: []
---

# One app server per hub checkout; the window picks its own port, the browser path uses 8787

Rule. Do not start a second `python tool/main` against the same hub checkout
while the window or another server runs. Startup takes the OS lock on
`raw/server.lock` before recovering tasks, and a second server must fail
there. Never weaken that lock, delete the file, or move recovery before it.
To test the server, stop the running one or use a separate clone (its own
`raw/`). Ports: the Tauri window starts the server on a free port
(`web/src-tauri/src/main.rs:60`). `python tool/main` defaults to 8787
(`tool/main/app.py:556`), and Vite's dev proxy expects 8787
(`web/vite.config.ts:13`). Do not assume the window is on 8787.

Why. A saved task stopped with `서버 재시작` while the desktop server was
still running. A second server's unconditional startup recovery could
rewrite the first server's live tasks, and an isolated reproduction
confirmed it (`docs/research/task-lifecycle-recovery.md:50-59`). The lock
was added for this (`tool/main/runtime.py:159-170`, taken in
`tool/main/app.py:67`).

What goes wrong. Live review loops and tasks are marked stopped or
restarted under the person's feet, and their worktrees are taken over.
