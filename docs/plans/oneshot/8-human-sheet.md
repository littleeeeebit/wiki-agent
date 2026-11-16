# Step 8 — A spec the owner reads

The overall design is in the [overview](0-overview.md). This step keeps the
spec the agent works from as it is and generates a second one from it,
written for the owner, which each of R1, R2 and R3 shows first.

Goal. At each review the owner reads one short page in plain Korean, answers
what the agent decided on its own, and can trust that approving the page
approves what the agent builds from.

## Why this step exists

On 2026-11-16 the owner said the spec of `realtime-screen-translator` came out
well but reads as "a spec nobody reads": dense and full of hard words. The run
record agrees.

| Measure | Value |
| --- | --- |
| R1 sheet | 24,794 characters, 309 lines |
| Items to decide one by one | 99: 32 requirements, 6 entities, 11 endpoints, 6 flows, 7 assumptions, 37 tests |
| Time the sheet was open | 480 s, about 5 s per item |
| Items the owner edited | 0 |

A contradiction went through unseen. The restated goal says the app runs in a
web browser. The stack is an installed Tauri app, and R14 says no browser form
is built.

The sheet is the agent's spec laid out for approval (`oneshot_sheet.sheet`).
Five things make it unreadable.

- It is in the agent's order. It opens with the stack table and the agent's
  assumptions, written in implementation terms (`safetySettings`, a 9×8
  hash, Windows DPAPI). Then come storage keys, an ER diagram with `i64` and
  `u32` fields, and an HTTP endpoint table for an app whose assumption A1
  says it has no HTTP server.
- The owner's own questions are answered nowhere in one place. The sentence
  asked three. How scroll and page turns are told apart is spread over R2,
  R24, A2, P6, T3 and T4. How a scroll is tracked is spread over R3, R22,
  R31, A2 and F3. How a translation already made is recognised is spread
  over R4, R23, A3, P7, T6 and T25.
- Every item weighs the same. What needs the owner's judgement is what the
  agent decided alone: six `assumed` requirements and seven assumptions. The
  sheet shows no status, so these sit among restatements of the owner's own
  words. "Build the Windows version too" (R11), which roughly doubles the
  work, is one line like any other.
- It repeats itself. A requirement comes back as a scenario, a flow step, an
  endpoint's description and a test. The glossary is appended whole.
- The words are the agent's. The glossary's banned synonyms leave stilted
  terms ("흰 shape", "크롭"), and code names sit in the prose
  (`gemini_requests`, test names). The tests read only by jumping between
  numbers ("P6에 보내고 P8을 읽는다").

A hand-written draft of the owner's page for that run is 3,477 characters,
about a seventh of the sheet. The owner read it and asked for this plan.

### How this differs from the retired two views

The spec-views experiment, retired on 2026-11-09, went the other way: it kept
the spec the owner sees and made a second spec only for the agent. The main
experiment showed almost no difference in the agent's results, so it was
retired (`docs/research/spec-views-main.md`).

This step leaves the agent's input untouched. The current spec stays the
agent's, and what is generated is a page for the person. Success is measured
on the person: whether the owner reads it and decides, not whether the agent
builds better.

## Design

### Two specs, one source

The agent's spec stays exactly as S2, S3 and S6 make it. Every later turn
reads only that. The owner's page, the brief, is generated from it after each
turn that ends at a review. It is stored in its own field, `oneshot.brief`,
keyed by review, and shown at that review.

The existing sheet renderers do not change. `oneshot_sheet.sheet` still
writes the design files at R1 approval, `oneshot_screens.sheet` is still
recorded at R2 and still reaches the critic, and `r3_sheet` still renders R3.
The brief is rendered beside them from `oneshot.brief` alone. Nothing reads
that field except the owner's view: no agent's prompt, no design file, no
pull request text and no S8 summary. The owner's own edit comments are not
the brief; they still reach the redo turn and the PR's history as today.

| Review | Brief written after | Approving the brief approves |
| --- | --- | --- |
| R1 | `design`, `redesign` | Every requirement and design item not on a confirmation line |
| R2 | The S6 screen design and `refront` | Nothing extra: every R2 item is on a confirmation line; the palette is still picked |
| R3 | The critique loop's last round, and the phone round on Android | Every screen with no finding left |

### The gate's identity

Each review has one snapshot: the exact data its brief is written from, built
by the server. Every fact a section states must come from it, since the
writer has no tools. The snapshot is the writer's whole input besides the
mandatory lines, and the review's identity, `source`, is a hash of it.

| Review | Snapshot |
| --- | --- |
| R1 | The owner's sentence, the restated goal, requirements, glossary, stack, design, the profile (kind, visibility, login), the round and its `marks` |
| R2 | The owner's sentence, `front` (screens, states, palettes), the features with their names, the frequency map, the first screen, the profile, the round and its `marks` |
| R3 | The R2 snapshot's fields with the final `front`, the findings left from the last critique and the last look, and on Android the phone round's findings, timing, memory and device (model, size, dpi), and the round |

The snapshot leaves out the brief itself, the reading record and anything
that changes without changing the gate, such as spend and timestamps. A
check before generation refuses a snapshot that lacks a field a required
section reads, such as an Android R3 whose phone round recorded no
measurements; the section then says the value was not read.

- The brief is stored with the `source` it was written from. A generation
  result whose `source` no longer matches the gate is dropped, not stored.
- The owner's view returns the current `source`, with or without a brief.
  Every submission carries it, from the brief or from the full sheet, and a
  submission whose `source` differs from the gate's is refused with 409 and
  a notice to reload. So a tab left open on round 1 cannot approve round 2.
- Bulk approval expands against the full item set of the gate that `source`
  names, the same list `decidable` gives today. Explicit edits stay as they
  are, and `marked` still resets the approvals of edited, changed and
  re-shown items.

### Confirmation lines

A confirmation line is one entry under `확인해 주세요`. The server, not the
brief's writer, fixes which lines must exist and which items each covers.
The writer only words them.

| Review | Mandatory lines, by key | Each line covers |
| --- | --- | --- |
| R1 | One per `assumed` requirement and one per assumption, plus one per item `marked` since the last round, keyed by that item's id | That item |
| R2 | One per screen, keyed by the screen's id | `S<n>` and `S<n>.states` |
| R3 | One per finding left, keyed `L<n>` | The screen that finding belongs to |

The writer may mention other ids in a line's wording, but they decide
nothing: a line covers only what this table gives it.

R2 has no record of which screens or states the owner asked for: the
frequency map records how often a feature is used, not an approved layout.
So every screen and its states are confirmed on a line, and the R2 brief's
gain is its wording, not fewer decisions.

At R3 the server numbers the findings left in one snapshot: the last
critique's findings, the last look's mechanical findings and the phone
round's findings, in the order `r3_sheet` lists them. Each gets a reference
`L<n>` and a screen, resolved in this order, the first that matches:

1. a `screen` field naming a screen id: a mechanical finding
   (`oneshot_look`) and a phone finding carry one;
2. on Android, a critic finding's `route`, which the critic's check already
   holds to a screen id;
3. on a web app, a critic finding's `route` matched against the screens'
   routes;
4. otherwise the first screen, and the line says the finding named no
   screen.

A brief submission is checked before anything is reduced. It must carry one
decision for every mandatory line of the current `source`, no unknown line
key, and each decision valid: an approval, or an edit with a comment. A
missing line is refused with 400 even when another line covering the same
screen is approved, and even when the submission asks for bulk approval. A
full-sheet submission carries no line keys and is checked as today.

Line decisions then reduce to item decisions, and `decided` runs on the
result with its usual check that every item has a decision:

- every item a line covers takes that line's decision;
- an item covered by several lines, or by a line and the folded sheet, takes
  every edit, joined, each comment kept whole and prefixed with its line;
- an edit always wins over an approval of the same item;
- at R3 the screen's builder finding carries each joined comment, one per
  finding edited.

### The R1 brief

The R1 brief has fixed sections, in this order. Every entry names the ids it
comes from, so the brief and the spec can be checked against each other.

| Section | What it holds | From |
| --- | --- | --- |
| 한 줄 | What the app is, in one or two sentences | The restated goal |
| 쓰는 장면 | The app in use, five to ten numbered steps | Scenarios with postconditions |
| 물어보신 것 | Each question the owner's sentence asks, answered in a paragraph, with the alternatives the agent dropped | Requirements and assumptions |
| 돌아가는 순서 | The pipeline in four to six steps, when the app has one | Flows and assumptions |
| 확인해 주세요 | The mandatory lines above, one each | `assumed` requirements, assumptions, marked items |
| 빼기로 한 것 | One line each | `not_applicable` requirements |
| 알아 두실 것 | Costs, risks, a public repository, what only a real device can check, and any contradiction between the restated goal and the requirements | Profile, stack, requirements |
| 다 됐다고 보는 기준 | Five to ten observable outcomes | Tests, rewritten without numbers or names |

On Android the design is `core_sheet`'s: modules (`M`), outside calls (`X`)
and platform permissions. Its brief adds every permission and outside call
to `알아 두실 것`, and the item set bulk approval expands to is
`decidable`'s for that design.

The brief is plain Korean in the owner's words. It holds no code names, field
types, endpoint paths, test names or cross-references between numbers. The
ids appear only in parentheses at each entry's end.

### The R2 and R3 briefs

R2 and R3 already lead with screenshots, so their briefs keep the pictures
and replace what surrounds them. Today the R2 sheet puts feature ids in code
spans and the palettes as hex codes. The R3 sheet lists findings as
`[severity] route viewport state` and the phone round as frame-time
percentiles and PSS memory.

| Section | R2 | R3 |
| --- | --- | --- |
| 한 줄 | What the screens let the owner do | What the finished screens do, and what changed since R2 |
| 화면 | Each screen's picture with one sentence on what it is for | The first screen's pictures, before and after |
| 기능이 놓인 자리 | Each feature by name and where it sits, in a sentence where it differs from the owner's map | The same, only where it moved since R2 |
| 확인해 주세요 | One line per screen, on the screen and its states | One line per finding left, in words, with its likely effect on use |
| 색 | The palettes as named swatches | Not shown |
| 휴대폰에서 | Not shown | On Android: whether it felt smooth and how much memory it used, in words, beside the measured values |

### Who writes it

One turn after each turn that ends at a review, with no tools and no
worktree, on the run's review model (`run["models"]`). That model did not
write the design, so it reads the spec afresh rather than echoing it. Its
input is the review's snapshot and the mandatory lines with what each
covers. Its output is one `oneshot-brief` block. The server
checks the block before the review shows it:

- every section is present and within its length, and the whole brief stays
  under a ceiling, 4,000 characters by default;
- every mandatory line is present, exactly once, and no other line is under
  `확인해 주세요`;
- every cited id exists in the snapshot;
- at R1, `빼기로 한 것` names every `not_applicable` requirement;
- no backticked code, test name or endpoint path;
- the translationese check passes, as on every gate sheet.

A failed check sends the turn back once with the reasons. A brief that still
fails leaves the review on today's sheet with its full decision set, every
item decided one by one, and a notice saying why. The run never stops because
of the brief.

### Deciding on the brief

Each review opens on its brief. Each line under `확인해 주세요` is decided on
its own: approve, or edit with a comment, as items are today. Approving the
brief approves the rest of that review's items at once. Today's full sheet
stays below, folded, for an owner who wants it, and any item there can still
be edited. At R2 the owner still picks the palette, as today.

The reduced edits go to the review's existing redo path exactly as an item's
edit does now: `redesign` at R1, `refront` at R2, and at R3 the builder's fix
turn with the loop run again. The brief is then written again from the
result, under the new `source`. The reading record (`oneshot.reading`) keeps
the seconds, the folds opened and the edits, so the next real run shows
whether the owner read the page.

## Acceptance

Every case runs offline on mocked model output; none needs a real run.

- Isolation. A brief carrying a unique marker string: the marker appears in
  the owner's view and in `oneshot.brief`, and nowhere in any later turn's
  prompt, the critic's input, `docs/design/`, the PR-writing input, the PR
  body or the S8 summary. An owner's edit comment still reaches the redo
  turn.
- Stale approval. The owner's view is read, a redesign changes an item under
  the same ids, and the old `source` is submitted: refused with 409, and
  nothing is approved. A brief generated for the old `source` and arriving
  late is dropped.
- R2 coverage. A brief missing the line for one screen fails its check, and
  bulk approval is not offered until a brief with every screen's line passes
  or the review falls back to the full sheet.
- R3 reduction. Two findings on one screen, one approved and one edited,
  reduce to an edit carrying that comment. Two edits keep both comments. A
  phone finding on Android reaches its screen.
- R3 mapping. A web mechanical finding on `S2` (its `screen` field), an
  Android critic finding with `route: S2`, and a web critic finding on the
  second screen's route all reach `S2`, not the first screen; only a finding
  naming no screen falls back, with its note.
- Missing line. With `L1` and `L2` both on `S1`, a submission approving `L1`
  and omitting `L2` is refused with 400, with and without bulk approval, and
  nothing is approved.
- Snapshot. Changing only the profile's visibility changes R1's `source`
  and the brief's `알아 두실 것`; changing only spend changes neither.
- Fallback. A brief invalid twice leaves the full sheet with every item of
  `decidable`, and approving only part of it is refused as today.
- Android. The R1 brief of a `core_sheet` design names every permission and
  outside call, and bulk approval covers its `M` and `X` items.

## How 8a builds it

- `tool/main/oneshot_brief.py` holds R1's snapshot, its `source`, the
  mandatory lines and the block check. The writer's prompt is
  `tool/prompts/oneshot-brief.md`, one file for every kind.
- `design` and `redesign` save their result under `working: brief`, then
  write the brief in the same worker, so R1 is not decided while it is
  written. `working` is cleared whatever happens.
- The writer's turn has its own two-try loop instead of `oneshot.turned`,
  because `turned` stops the run on a second refusal or a broken turn. It
  is charged and recorded in flight like any turn.
- The block is `{"sections": [{name, text}], "confirm": [{key, text}]}`.
  `물어보신 것`, `돌아가는 순서` and `빼기로 한 것` may be left out, the last
  only when no requirement is `not_applicable`.
- A citation is parentheses holding ids and nothing else, `(R3, A2)`, and
  every id in it must exist. `(갤럭시 S21)` and `(A4 용지)` are words, not
  citations. The design's endpoint paths are refused even without their
  method.
- The writer's turn runs with no tools on either provider (`oneshot.ask`
  with `tools=""`, isolated), in the run's own folder.
- Any failure reading or saving around the turn records a failed brief and
  clears `working`; only a save that fails again reaches the worker's guard.
  A charge that could not be saved is settled once in the save that clears
  `working`. A brief turn still in flight with no brief being written is
  charged as cut and cleared on resume, when no worker is alive, or at
  start-up.
- Translationese refuses the first answer and is kept as a warning on the
  second, as the design turn does.
- At start-up, a run left under `working: brief` gets its brief marked
  failed before `oneshot.restarted` charges the cut turn and stops the stage
  with `restart`; `[재개]` returns to the sheet.

## How 8b shows it

- The owner's view carries `brief` (`oneshot_brief.shown`): always the gate's
  current `source`; the sections, lines and their coverage when the stored
  brief was written from that `source`; otherwise `failed` with why. A brief
  written for an earlier gate is not shown.
- An R1 submission carries `source`, `lines` (one decision per line) and
  `rest`. A `source` other than the gate's is refused with 409 before
  anything else, and so is a missing one. `lines` or `rest` without a shown
  brief is refused with 409.
- `oneshot_brief.reduced` checks every line first, then reduces. Edits are
  joined with `[확인 줄 R3]` and `[전체 시트]` prefixes when an item has more
  than one, and a single edit is kept as written. A sheet decision that is
  not valid passes through for `decided` to refuse.
- The reading record notes `brief: true` when the submission was made on
  the brief.
- The screen opens on the brief with its lines beside it. The full sheet
  and its items stay folded below, and only items the owner touched there
  are sent. A failed brief shows the full sheet with its reason above.
- On Android, `알아 두실 것` must cite every requirement with a
  `capability` and every outside call.

## Decided

The owner decided these on 2026-11-16, by options.

| Question | Decision |
| --- | --- |
| What approving the brief does | Approves every other item of that review at once; the `확인해 주세요` lines are decided one by one |
| Which reviews | R1, R2 and R3 |
| Who writes the brief | The run's review model |

## Steps

| # | Stage | Deliverable | Status |
| --- | --- | --- | --- |
| 8a | The brief | The `oneshot-brief` turn on the review model, the snapshot and its `source`, the mandatory lines and the checks, `oneshot.brief` kept out of every agent input; written after `design` and `redesign`; the isolation, stale, snapshot and fallback cases | Done — `oneshot_brief` |
| 8b | R1 on the brief | The brief first with every line's decision checked, then reduced to items, one approval for the rest, `source` required on every R1 submission, today's sheet folded below; the Android case | Done — R1 opens on the brief |
| 8c | R2 and R3 on the brief | Their briefs after S6 and `refront`, and after the critique loop and phone round; R3's finding references, their screens and the reduction; the R2 coverage, R3 mapping, R3 reduction and missing-line cases | Planned |
