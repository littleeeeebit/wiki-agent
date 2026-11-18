# Step 10 — This PC's wiring, confirmed with the card

The overall design is in the [overview](0-overview.md). This step moves the
one confirmation a oneshot still asked for outside its own window, the
machine's hook wiring and Codex trust, onto the environment card.

Goal. A oneshot on a PC whose user-level wiring does not yet call this hub
runs through Register without stopping. The owner sees every line the move
writes before confirming, and nothing is written that the owner did not see.

## Why this step exists

On 2026-11-18 the `vtuber-ko-tts` run stopped at Register with `connect`:
"이 기계의 hook 배선이나 Codex 신뢰를 바꿔야 한다 — [연결]로 확인한 뒤
[재개]". The owner asked why a oneshot, which sets up its repository from
nothing, does not set up the wiring as well.

That stop had two causes. A Codex `app-server` that answered slower than the
25-second deadline read as missing trust, so a check that could not be made
asked for a move. #384 fixed that: an unanswered check is retried and named,
never read as a move. The other cause is by design. The wiring is user-level
(`~/.claude`, `~/.codex`, Orca's Codex homes), so it moved only on the
`[연결]` dialog's confirmation, outside the oneshot window. The owner chose
on 2026-11-18 to confirm it with the card instead.

## Design

The card shows the move `[연결]` would ask for. A oneshot's adapter names both
hosts (`connect.adapter_text`), so the move is the one the routine read
already keeps, `wiring.hub_seen()`. The card never waits on Codex, and it
reads again while that check is `checking`.

| The check says | The card shows | Confirming |
| --- | --- | --- |
| Checking, nothing found yet | `이 PC의 hook 배선을 확인하는 중…` | Allowed; Register checks again |
| Nothing to move | `이 PC의 hook 배선은 이미 맞다` | Writes nothing |
| A move | Every line, link and trust record, as `[연결]` shows them (`HubMove`) | Records the digest of that list in the run (`wiring`) |
| Refused | The refusal | Allowed; Register checks again |

Register keeps its order: check the move, retrying one that cannot be made,
then write, then run the hosts' tests. It writes the move through
`connect.move(digest)` only when the run's `wiring` is the digest of the move
needed now. `connect.move` checks the digest again as it writes. Every other
case stops with `connect` as before:

- No confirmed list. This covers runs confirmed before this step, and a card
  confirmed while the check had nothing yet.
- A confirmed list that differs from the move needed now. The stop says so.
- A move that cannot be written, with its reason.

The owner then confirms on `[연결]` and presses `[재개]`. A move that a server
going down refused before its first write stops with `restart` instead. The
run keeps its approval, and `[재개]` checks and moves again.

The card's approval is single use. Register clears the run's `wiring` on
disk before the move may write anything, and puts it back only when the move
refuses before its first write. A move cut once it may have written stops
with `connect`, whatever cut it: a failure, shutdown, a Codex that took a
trust write and died before answering, or the process itself dying. What
landed is not knowable in every such case, so the approval stays spent. The
stop names what the move confirmed. `[재개]` alone never writes what is left:
`[연결]` shows the list, and `[재개]` goes on once nothing is left to move.

The digest binds what is written, not only the lines shown. A line names a
hook but not its command, and the same lines under another interpreter write
other commands. So the digest also covers:

- each file's whole new content;
- the interpreter, which the list shows;
- the Codex homes the trust is recorded in, which the list also shows.

The move writes trust to that captured list of homes and never reads the
homes again.

Each settings file is written whole to a staging file of that attempt's own,
created exclusively beside it, which then replaces the original. A write cut
partway leaves the owner's file as it was. Another writer, such as
`setup_agents.py --global` running beside the server, never installs this
attempt's bytes.

Trust counts as done only when Codex lists back every hook this install
wrote, every listed entry trusted (`setup_agents.codex_trusted`). The
routine trust check holds the same bar. Anything less is a refusal with its
reason, never a finished move. Examples are hooks switched off, `SessionStart`
loaded without `PreToolUse`, or a trusted twin beside an untrusted entry.

A skill link is never removed before its replacement stands. The working
link moves into a fresh private folder of that attempt, under `relink`'s own
recovery folder `~/.claude/.wiki-relink`. Nothing is there to clobber, and no
host loads it as a skill. It moves back when the new link cannot be made. If
even that fails it stays kept, whole, and the error names where, also in
`[모두 다시 연결]`'s report.

The next plan lists a kept link with nothing at its place, as a crash leaves
it. It moves here when this hub has the skill, and goes back where it pointed
otherwise. Recovery reads only that recovery folder, never a link the owner
keeps elsewhere. A link is moved only while it still points where the
confirmed plan saw it point. One pointing elsewhere by then is refused.

Shutdown is checked in four places:

- `connect.move` writes nothing once shutdown has begun.
- It starts no further settings file, link or Codex home.
- `write_plan` installs no staged file once shutdown began while it was
  staged.
- `codex_hooks` sends no trust write once a read Codex answered lands after
  shutdown began.

A move cut partway still drops the routine read's last check and every
repository's probe record, as a finished move does. Each record is cleared on
its own, and one that cannot be cleared hides neither the others nor the
move's own error.

The screen applies a read of the run only when no other answer landed after
the read was asked. A poll asked before the confirm can then never put the
confirmed card back.

Writing at Register rather than in the confirm request keeps the slow part out
of the request. The move re-reads Codex trust, and an `app-server` may take
minutes. The bootstrap worker already handles a wait, a retry and a shutdown.

## Acceptance

- The card of a run at `environment` carries `hub`, and the screen lists the
  move before the confirm button.
- A confirm records the shown digest. Register writes exactly that move and
  then runs the tests; a different or missing digest stops with `connect`.
- A move refused at writing stops Register unrecorded with the reason.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 10a | The card shows the move, its confirm records the digest, Register writes it | In progress |
