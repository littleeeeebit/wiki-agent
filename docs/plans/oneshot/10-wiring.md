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

The owner then confirms on `[연결]` and presses `[재개]`.

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
