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
turn that ends at a review. It is stored beside it, shown at that review, and
never put in any agent's prompt, the design files, the pull request or the S8
summary.

| Review | Brief written after | Approving the brief approves |
| --- | --- | --- |
| R1 | `design`, `redesign` | Every requirement and design item |
| R2 | The S6 screen design and `refront` | Every screen and its states; the palette is still picked |
| R3 | The critique loop's last round, and the phone round on Android | Every screen |

### The R1 brief

The R1 brief has fixed sections, in this order. Every entry names the ids it
comes from, so the brief and the spec can be checked against each other.

| Section | What it holds | From |
| --- | --- | --- |
| 한 줄 | What the app is, in one or two sentences | The restated goal |
| 쓰는 장면 | The app in use, five to ten numbered steps | Scenarios with postconditions |
| 물어보신 것 | Each question the owner's sentence asks, answered in a paragraph, with the alternatives the agent dropped | Requirements and assumptions |
| 돌아가는 순서 | The pipeline in four to six steps, when the app has one | Flows and assumptions |
| 확인해 주세요 | Every decision the owner did not make, one line each | `assumed` requirements and every assumption |
| 빼기로 한 것 | One line each | `not_applicable` requirements |
| 알아 두실 것 | Costs, risks, a public repository, what only a real device can check, and any contradiction between the restated goal and the requirements | Profile, stack, requirements |
| 다 됐다고 보는 기준 | Five to ten observable outcomes | Tests, rewritten without numbers or names |

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
| 확인해 주세요 | Every screen, state wording and placement the owner did not ask for | Every finding the stop rule left, in words, with its likely effect on use |
| 색 | The palettes as named swatches | Not shown |
| 휴대폰에서 | Not shown | On Android: whether it felt smooth and how much memory it used, in words, beside the measured values |

### Who writes it

One turn after each turn that ends at a review, with no tools and no
worktree, on the run's review model (`run["models"]`). That model did not
write the design, so it reads the spec afresh rather than echoing it. Its
input is the review's sheet data and the owner's sentence; its output is
one `oneshot-brief` block. The server checks the block before the review
shows it:

- every section is present and within its length, and the whole brief stays
  under a ceiling, 4,000 characters by default;
- every cited id exists in the spec;
- in R1, `확인해 주세요` names every `assumed` requirement and every
  assumption, and `빼기로 한 것` every `not_applicable` requirement;
- in R3, `확인해 주세요` names every finding left;
- no backticked code, test name or endpoint path;
- the translationese check passes, as on every gate sheet.

A failed check sends the turn back once with the reasons. A brief that still
fails leaves the review on today's sheet, with a notice saying why. The run
never stops because of the brief.

### Deciding on the brief

Each review opens on its brief. Each `확인해 주세요` line is decided on its
own: approve, or edit with a comment, as items are today. Approving the brief
approves every other item of that review at once. Today's full sheet stays
below, folded, for an owner who wants it, and any item there can still be
edited. At R2 the owner still picks the palette, as today.

An edit on a brief line is a comment on the ids it cites. It goes to the
review's existing redo path exactly as an item's edit does now: `redesign`
at R1, `refront` at R2, and at R3 the builder's fix turn with the loop run
again. The brief is then written again from the result. The reading record
(`oneshot.reading`) keeps the seconds, the folds opened and the edits, so the
next real run shows whether the owner read the page.

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
| 8a | The brief | The `oneshot-brief` turn on the review model, its checks, storage beside the spec, kept out of every agent prompt; written after `design` and `redesign` | Planned |
| 8b | R1 on the brief | The brief first with per-line decisions on `확인해 주세요`, one approval for the rest, today's sheet folded below | Planned |
| 8c | R2 and R3 on the brief | Their briefs after S6 and `refront`, and after the critique loop and phone round, decided the same way | Planned |
