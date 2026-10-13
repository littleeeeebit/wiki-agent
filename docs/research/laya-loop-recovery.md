# Laya loop recovery — one journal, torn tails, replayed views

PR #137's review found the same P1 in two consecutive rounds: an interruption
between two files of the labelling pipeline or the loop left them disagreeing,
and a rerun then over-counted agreement, lost notes, or reused a split for
another slice. Reliability PR 8 requires research before the next fix of a
repeated finding; this page is that research.

## Failed hypotheses

| Round | Hypothesis | What the review showed |
| --- | --- | --- |
| 2 | Recording each judged state's counts beside the labelled output keeps resumed statistics right | Output written, counts not: agreement over-stated (`30/53` reported, `30/61` true) |
| 3 | Writing the verdict first and settling output and notes from it closes the gap | A verdict line torn mid-write made every later read fail; a status written to a page before the round's outcome was journalled made the rerun aim the same round number at another slice |
| 4 (cycle 1 of the research) | One journal per step, torn tails cut, pages replayed | Two places still broke the principle: a tail torn inside a UTF-8 character failed decoding before framing, and a paid diagnosis lived only in its page, so a page cut before replacement paid for it again; note ids of the earlier format were compared as strings |

Each fix closed the gap it was shown and left the next one: the pipeline had
several files that were each partly authoritative.

## Core theory

- Write-ahead logging: an intent is made durable in one log before the effect
  it describes, and recovery replays the log forward; replay must be
  idempotent, so a crash during recovery is harmless (R1, R2).
- A torn tail: a record cut by an interruption was never committed. Readers
  stop at the first invalid frame and recovery truncates after the last good
  record (R3, R4). A complete record that fails its check is corruption, not
  a tear, and is not discarded silently (R3).
- Derived files are replaced whole: write a temporary file in the same folder,
  then rename it over the target (R5). Rename does not guarantee durability
  after a power loss without fsync, and Windows gives weaker guarantees (R5,
  R6).

## Why it applies here

The loop and labelling are a small transaction system: paid turns are the
non-repeatable effects, the files are the state. Applied:

| Journal (source of truth) | Views rebuilt from it |
| --- | --- |
| `<split>.ledger.jsonl` — each labeller turn | the judging of a state not yet journalled |
| `<split>.judged.jsonl` — each state's verdict, with its labelled row and notes | `<split>.jsonl`, notebook ledger lines (by full-hash id) |
| `rounds.jsonl` — each round's intent (`started`, before any paid turn) and outcome, with the page status it sets | incumbent splits, stalled features, the next round, every page status (replayed on start) |
| `notes/<kind>/diagnoses.jsonl` — each paid diagnosis with the error count it saw | a page's Cause, Next and `diagnosed` |
| `notes/<kind>/ledger.jsonl` — each error, identified by its fields | cluster pages' cases and counts |

`laya/store.py` gives every journal one framing: one JSON record per line,
written with its newline in one write. Frames are found in bytes before any
decoding, so a tail torn inside a multi-byte character is a torn tail like
any other. A last line without its newline is a torn record: `read` leaves
it out and `append` truncates it, or ends it when it is whole. A complete
line that does not decode or parse raises. Pages and the index are replaced
through a temporary file and are rebuilt from the journals alone.

A paid call's result is safe once journalled. A call whose turn ended before
its result was journalled is paid again: R1 notes that an external action
needs its own record of whether it was done, and a host turn offers none.
The plan bounds that loss by the turn budget, not by the files.

## Alternatives

- SQLite with transactions: atomic by construction, but another store for
  data the rest of the pipeline reads as JSONL in Laya's format. Rejected as
  more than the problem needs.
- Checksums per record (R3): catch silent corruption as well as tears. A JSON
  line that parses is the frame check here; corruption of a complete line is
  out of scope, and a line that does not parse is still raised.
- fsync on every append: covers power loss as well as process interruption.
  Not done (marked in `store.py`); a power-loss tear still lands in the torn
  tail rule unless the filesystem reorders writes.

## Counterevidence and limits

- Without fsync a power loss can lose committed records, not only tear the
  last one; paid turns already made would then be paid again.
- `os.replace` on Windows is atomic for a process interruption; R6 notes
  that atomic replacement on Windows is not fully reliable after a crash.
- The generator (plan stage 2) is not built; it must resume by its own
  journal under the same rule, and the loop checks only that every candidate
  carries the slice it was aimed at.

## Experiment and result

Interruptions injected in `laya/test_laya.py` on 2026-10-13:

- before a verdict;
- before its output;
- mid-write of a verdict;
- before a round's outcome;
- between the outcome and its page status.

Each rerun finishes with the true counts, one note per error and no extra
paid turn, and resumes the same round on its own slice. Records written by
the earlier review rounds' format still resume. All 19 tests pass.

Cycle 2 (after round 4) added three more cases:

- a tail torn inside a UTF-8 character;
- a committed line that does not decode, which raises;
- a page cut after its diagnosis was journalled, rebuilt without a second
  writer call.

Notes stored under the earlier short id are matched by their fields and not
written again. 21 tests pass. This is the second and last research-and-fix
cycle that reliability PR 8 allows for this issue.

Round 5 found one more case: a page whose diagnosis an earlier format of
this branch wrote only to the page would be overwritten. No such page
exists: `laya/notes/` was never created and no paid diagnosis ever ran. The
owner chose a boundary over a migration (2026-10-13): `write_pages` refuses
to write while any page holds a diagnosis `diagnoses.jsonl` lacks, and
leaves the page untouched. 22 tests pass.

## Sources

| # | Source | Used for |
| --- | --- | --- |
| R1 | MIT 6.033 (2017), [lecture 16: logging](https://web.mit.edu/6.033/2017/wwwdocs/lec/l16.pdf) | Write-ahead rule, checkpoints |
| R2 | [Write-ahead logging and crash recovery in database engines](https://geosurge.ai/corpus/gen-4104/database-engine/write-ahead-logging-wal-and-crash-recovery-mechanisms-in-database-engines.html) | Idempotent forward replay |
| R3 | [corium-log](https://docs.rs/crate/corium-log/0.1.67) | Torn tail discarded; changed complete frame rejected |
| R4 | [xfs: detect and trim torn writes during log recovery](https://android-kvm.googlesource.com/linux/+/7088c4136fa1cba26531fde40bdcfcf3d2ccd533); Go WAL `Repair` ([package docs](https://beta.pkg.go.dev/github.com/hagarmeir/consensus/pkg/wal)) | Truncate after the last good record |
| R5 | [google/renameio](https://pkg.go.dev/github.com/google/renameio) | Temporary file in the same folder, then rename; fsync caveat |
| R6 | [LWN: POSIX and rename durability](https://lwn.net/Articles/351708/) | Rename durability is not guaranteed by POSIX |

The sources were found by web search on 2026-10-13 and summarised from the
search results. They were not read in full.
