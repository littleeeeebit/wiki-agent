---
scope: craft
severity: landmine
repeat: rule
triggers: ["화면(을|이|에|은|의)?\\s*(만들|고치|붙|추가|바꾸|띄)", "프런?트\\s*엔드|frontend", "\\.tsx|\\.jsx|리액트|React", "모달|다이얼로그|팝업", "저장(이|은|을|하면)?\\s*(안 ?되|버튼|실패|거절)", "로그인|로그아웃|계정\\s*(전환|바꾸|경계)", "입력\\s*(칸|창|폼)|드롭다운|셀렉트"]
slots: []
sources: []
sources_withheld: true
links: [pick-up-async-results, diagnose-from-what-ran, verify-narrow-then-wide, client-lifecycle-in-one-scope]
---

# Decide the account boundary and async ownership before wiring a screen

Rule. If a screen writes to a server or reads back from it, decide and write
down three things before a line of it is built: whose account a request is,
with the ticket taken once when the screen opens and never re-read; what
decides that a late result is still this screen's — ask "is this my
generation" of every arriving result and drop it without touching state,
since `alive` is true during retirement too; and that "may this be written"
and "may this be stored" are different questions. Ignore a late result rather
than locking. A failed re-query does not answer success, and rebuilding or
opening a different target clears the input state. (What the screen should show in the first place, and the order
it gets polished in, is held by [[screen-follows-the-purpose]]. This page is
the step after: the premise of the wiring.)

| What | What has to be decided |
| --- | --- |
| Account boundary | Whose account is this request. When is that ticket taken |
| Async ownership | What decides that a late result is still this screen's |
| Validity | "May this be written" and "may this be stored" are different questions |

The ticket is taken when the screen opens, not when a button is pressed.
Retirement raises the generation first and then waits for the logout, so a
save pressed in between reads the already-raised value and passes as its own.
Ignore a late result rather than locking — the request may already have
reached the server, so it cannot be cut anyway, and a lock means one request
with no time limit traps the user in a modal. Where the repository already has
a generation-ownership invariant, hang the new screen on that one.

What goes wrong. Each round of repair opens one adjacent hole, the reverts
continue, and the feature comes out of the PR in the end. The offline suite is
green throughout.

## How to hold it — four lines per screen

- Pin the moment the ticket is taken as a constant. Once on open, never
  re-read after.
- Ask "is this my generation" of every arriving result, and drop it without
  touching state when it is not. Checking `alive` is not enough — a screen is
  alive during retirement too.
- If the re-query fails, do not answer success. A stale list does not contain
  what was just saved.
- Rebuilding, or opening a different target, clears the input state. A draft
  has an owner too.

How a late result is picked up is held by [[pick-up-async-results]]. What this
page is about is whose it is once it arrives.

Screen or client, there is one thing to decide —
**ownership is not a layer added later.**
