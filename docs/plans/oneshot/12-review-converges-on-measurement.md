# Step 12 — The backend review converges on what was measured

The overall design is in the [overview](0-overview.md). This step stops the
backend review from refusing without end. It does that in three ways. The
builder's answers reach the reviewer. A conflict the two cannot settle goes to
the owner. And before any review, the built backend runs once against the real
services.

Goal. A backend build opens its pull request as soon as its gate passes. It
then runs on the owner's PC against real outside services, for example a real
Google sign-in and a real live broadcast. The owner follows steps that
wiki-agent shows, one window at a time, to get each account and address. The
review reads that pull request together with the measurement. Each round
either converges or reaches the owner with a decision only they can make. All
of it ships in one pull request, so a run resumed after the merge goes through
it with no other change.

## Why this step exists

The `vtuber-ko-dub` run's backend review refused 46 times between 2026-11-20
09:24 and 2026-11-22 15:55. It stopped at `cc333ede`. The record is in
`raw/specs/vtuber-ko-dub/oneshot-initial.json` under `oneshot.reviews`, and the
build transcript is in the builder's Claude session for
`vtuber-ko-dub-worktrees/oneshot-initial`.

| Cause | What the record shows |
| --- | --- |
| The builder's answers never reach the reviewer | From round 33 on, each build turn answers four to six findings "with evidence instead of a change". `earlier_text` gives the reviewer only the refusals. The finding in `session file cleanup` at round 46 repeats round 21's finding, repro included |
| Requirements conflict and nothing asks the owner | The builder answered: "every change I could find breaks another requirement the design approved". Here is one case. A body that ends when its connection closes reads the same to ffmpeg as one cut short. Accepting it would bring back round 23's P0 |
| Findings outside this stage block it | `Android service integration` (16 rounds) and `desktop command integration` (15 rounds) need the Gradle project and the `src-tauri` crate. The build turn's own text says those belong to each kind's later build |
| An unchanged HEAD is reviewed again | Rounds 40 to 43 read `db40989d` four times, ten minutes apart, and refused it with the same six findings |
| "Replace the mechanism" fires every round | Once every area had three refusals (`STUCK`), every build turn rewrote mechanisms. Every commit from round 29 on is titled "Replace the mechanisms…", and each rewrite brought new cases |
| Nothing was measured | 245 tests passed on fakes. The crate has no `fn main`, so the backend cannot run. Findings about how a real replay ends were reasoned from code, round after round |

`[재개]` resetting `refused` is intended. A resumed review still reads every
earlier refusal (`earlier_text` reads all `reviews`), and a resumed build turn
gets the standing refusal and its areas' history (`build_turn`, `recurring`).

## Decided with the owner (2026-11-22)

| Question | Decision |
| --- | --- |
| The builder's answers | Each answer "with evidence instead of a change" goes into the next review's prompt. The reviewer leaves the finding out, or raises it again with a `rebuttal` saying why the evidence does not hold |
| A build turn that answers a finding without a change | It commits a pinning test of the behaviour it defends, and names it in its answer. A build turn that ends without a commit is a bug of the turn: it goes back to the builder to commit, never to the owner and never to a stop. "A commit" means one since the refusal: a turn resumed on a commit an interrupted turn made after the refusal passes, and only a HEAD already refused goes back |
| An unchanged HEAD | It is never reviewed again |
| A conflict the builder and the review cannot settle | It goes to the owner. This applies when the builder answered an area in two rounds, naming a requirement a change would break, or when an area is refused again after its one mechanism replacement. The owner keeps the review's side, the builder's side, or writes their own decision |
| Findings that belong to a later stage | They are not blocking findings of the backend. Each goes to that kind's own build turn |
| "Replace the mechanism" | Once per area |
| What the measurement's keeping guards against | Mistakes, not an attacker. The measurement script and the backend are this run's own code and already hold the secrets in their environment. Keeping masks what they leak by accident and survives ordinary failures: a full disk, a read-only file, a failed read, a leftover folder. A link, junction or hard link they plant, or a file they swap between a check and its read, is outside what the keeping promises. The link checks that exist stay, as a courtesy, not as a guarantee (owner, 2026-11-23) |
| A measurement input named after a variable the launcher or the shell decides | Refused by the list `oneshot_kept.LAUNCHER` and every `BASH_` name: the server's own launch names, PowerShell 7's `PSMODULEPATH`, and what dash and bash set or reset as they start. A name some other shell decides, outside that list, is an accepted limit, not a finding: the plan's author picks the name, and a value it changes shows in the measurement it spoils (owner, 2026-11-23) |
| A result set aside for a secret change the store then refused | Put back only while no save to the spec, by any route, has come since that change set it aside. Anything saved since, even a save that returns the record to how it was, leaves it set aside, and the owner measures again: a measurement repeated is preferred to a verdict on a result whose inputs may have moved (owner, 2026-11-23) |
| The round cap on `[재개]` | It resets, as before |
| Order after the build | Build gate → pull request #1 (a draft) → real measurement → review. Fix commits stack on the same pull request, and the last stage makes it ready |
| What the measurement runs | The backend ships a runnable headless server, with its API, its workers and the real sign-in, as a required product of the build. The measurement starts it with real accounts and real addresses |
| Who passes the measurement | The server measures automatically on the owner's PC. Then the owner listens to the converted samples and approves or refuses |
| Where the owner learns what to give | In wiki-agent's measurement screen. Every step names the window, what to press or type, what then appears, and what to copy into which box |
| Who writes those steps | The build agent, in the same build turn that writes the backend, through the existing agent turn. `[재개]` on a stopped run sends that turn |
| How it ships | One pull request. Once it is merged, a resumed run reaches the measurement with no other change |

## Design

### The builder's answers reach the review

A build turn sent back by a refusal answers the findings it does not change
in one `oneshot-answers` block. Each entry names the finding's `area`, the
`location`, the evidence, the requirements a change would break (`breaks`),
and the test that pins the defended behaviour, as `path::name`. The build
check (`oneshot_answers.answered`) refuses an entry whose area the refusal
did not name, whose `breaks` are not design items or requirements, or whose
test (`path::name`, or `path::Class::name` inside a class) lies in a file
this turn's commits did not change, or one HEAD does not hold as a file. No source is parsed: whether the test is
new is read from the test runs themselves. After the gate, that test must
have run and passed in the build's own test run as a new case
(`oneshot_answers.ran`):

- its name is the selector's;
- a class the selector names is a word of the case's class name;
- where the report names a file, that file is the answer's, read as the
  contract gate reads it (`oneshot_build.source`: an absolute path inside the
  worktree, or a relative one from the runner's root);
- the refused build's own run did not report it, so an old test edited is
  not new. Both runs' files are read through the same rule, so `./a.py`,
  `a.py` and an absolute path are one file. Where either run left the
  case's file out, its class name and name alone say it ran before.

Each build's run leaves its cases on the spec (`run_cases`) for the next
round to compare with. The root is saved at the contract commit, or
recovered as the one folder, of every folder HEAD tracks a file in, from
which every pinned case's file is a contract file (`runner_root`; `../`
included, an exact path being the root itself). The stated limits:

- a report without files matches by name and classes;
- a run committed before `run_cases` was saved has no newness check;
- the review reads the answer and its test, and judges whether the test pins
  the defended behaviour. It saves the entries on the spec under `answers`,
with the round they answer.

`review_text` shows the next reviewer every answer (`answers_text`). In an
area answered in the last round, a finding needs a `rebuttal`; without one
the verdict is refused and the reviewer is asked again (`held`).

### A turn without a commit goes back

The build check reads HEAD first (`oneshot_answers.unchanged`). When HEAD
is any HEAD a review refused, or whose measurement failed or the owner
refused, the last one or an earlier one gone back to, the turn is refused
and sent back to the build agent with that reason (`oneshot_answers.Again`).
Per the owner decision it never counts against `MAX_ATTEMPTS` and never
stops the run; only the owner's `[멈춤]` ends such a loop. The same holds
for any backend build turn that ends on the HEAD it started on (`send`
records it as `turn_head`, a resumed turn included). Only a build turn that
moved HEAD and still failed is counted. The contract-test turn and the
frontend build keep their own caps: every refusal of theirs counts, as
steps 3 and 4 require. `[재개]` at the round cap marks the turn as sent
back by the refusal (`fix`), so the same check holds there.

### A conflict goes to the owner

After a refusal, `conflicted` looks at each blocking area the owner has not
ruled on. It is a conflict when the builder answered it in two rounds with a
broken requirement, or when its mechanism was already replaced once
(`replaced`). The build turn told to replace it saves it as `replacing`. It
counts as replaced only once a build passes whose commits changed the file
a finding of that area points at, any byte of it (`changed_files`), and did
not answer it instead; a turn that never ran, or
left the area as it was, is told again. This check is deliberately coarse:
the review, not a line diff, judges whether the mechanism changed, and the
check only rules out a turn that left the finding's file alone. A line diff
cannot attribute an edit between repeated lines or inside a literal, so it
is not used. A refusal with a conflict
does not go to the build or count towards the cap: the run waits at the
`conflict` stage. The owner's board shows the review's findings beside the
builder's answers, and for each area offers three rulings: the review's side,
the builder's side, or the owner's own words. The rulings are saved on the
spec (`rulings`) and shown to every later review and build turn. A P1 in an
area ruled for the builder is refused, one handed to a later build included;
a P0 there still blocks. The rulings,
the cleared board and the next stage are saved in one write. When nothing is
left to fix, the same HEAD is reviewed again under the rulings; otherwise the
build turn gets the remaining findings and the rulings.

### Later stages' findings wait

A P1 may carry `later: {kind, what}` for a kind the run builds. It blocks
nothing now. It is added to the spec's `later`, which every verdict keeps
(approval, refusal, conflict and cap), and that kind's front-end build turn
receives it (`later_text`). A `later` for a kind the run does not build is
refused.

### The backend runs, and is measured before the review

Every build turn ends on the measurement contract
(`tool/prompts/oneshot-measure-contract.md`). The build agent commits
`.wiki/measure.json` with five parts.

- `serve`: a command that starts the backend headless, and its local health
  address.
- `measure`: a committed script that drives the running backend through the
  requirements against the real services, and writes `{out}/report.json` and
  a few clips.
- `inputs`: what only the owner can give, each a secret or a plain value,
  with its guide.
- `during`: what the owner does while it runs, such as finishing a Google
  sign-in. The measurement screen shows nothing the script prints or writes
  before it ends, so what the owner must read meanwhile, such as a device
  code, the script opens for them in the browser, on a page served on
  `127.0.0.1`; that step has no `url` and no `copy` (owner, 2026-11-24: the
  script shows it, wiki-agent reads nothing while it runs).
- `metrics`: each with its requirements, the design's outside calls it
  reaches, and its threshold.

A guide is at least three Korean steps, each naming the window, what to do
there and what then appears; the last names what to copy.

The build check reads the plan (`oneshot_measure.checked`). It refuses a
guide step without its window, action or result, and a guide whose last step
copies nothing. It refuses an outside call of the design that no metric
reaches, a command a PC app's confirmed tools do not cover, and a secret
need of the design given as plain text. It then starts `serve` in a job of
its own, waits for its health address to answer 200, and stops it with
everything it started. A health port something already answers before the
start is refused. So is one where a process that accepts a connection to it
is outside the backend's process tree. The server connects once to each
address its host resolves to (both for `localhost`) and reads the accepted
socket's owner, so whichever listener the system picks is the one judged.
The accepted socket is matched on both ends' address and port (an
IPv4-mapped address read as IPv4), and read again for up to three seconds
while it is still queued for `accept` and has no owner yet. A
redirect is
the health address's own status, never followed. The build reads the plan
committed at HEAD, as the screen does, never an ignored file. A tree the
backend wrote into is refused.

After the check passes, the run is at `measure`. The server pushes the HEAD
and opens pull request #1 as a draft, or finds and updates it
(`published`). The measurement screen shows each input's steps, a box for
each value (a secret goes to Windows Credential Manager and never comes
back), the owner's steps for the run, and the metrics with their thresholds.
The screen shows the plan committed at the built HEAD. Each value is sent
with that HEAD and the input's `env` and kind as the screen showed them; a
plan rebuilt since answers 409. A result waiting for the owner's verdict is
set aside (`stale`) when an input changes. A secret's change sets the
result aside before Credential Manager changes; if that save, or the spec
read before it, fails, the secret is left as it was and the owner tries
again. A request that changes nothing (a value held already, given again,
or a removal of a secret already gone) sets nothing aside. When Credential
Manager cannot be read to tell, the request changes nothing and the owner
tries again. A value too long for Credential Manager is refused before
anything changes, and a write or removal the store refuses puts the result
it set aside back. When that put-back fails too, the result stays set aside
with no verdict on it, the answer says so, and the next secret request puts
it back first, but only while the spec file is still the one the refused
change's set-aside wrote. Every save, by any route, the task editor
included, swaps in a new file, so a save that left the record as it was
counts too. The set-aside, that file's reading, the check and the put-back
each hold the spec file lock every save takes. When the file cannot be read
right after the set-aside, the set-aside is undone at once and the request
stops for a retry before Credential Manager is touched; an undo that fails
too leaves the result set aside, measured again, as the owner decision
prefers. A file that cannot be read later, at the check, keeps what is to be
put back for the next request; a file gone, or another write, drops it. Anything saved since (an input, a new run, any field)
leaves the result set aside, and the owner measures again. What is still to
be put back is held in memory: after a restart the result stays set aside,
and the owner measures again. A value Credential Manager returns but cannot be decoded counts
as different, so it can be replaced. [실측 시작] holds the worktree,
starts `serve`, runs the script with the inputs in its environment and every
secret masked from its output. A plan cannot name an input after a variable
the launcher sets (`oneshot_kept.LAUNCHER`): the server's own (`PATH`,
`TMP`, `TEMP`, `TMPDIR`, `PYTHONUNBUFFERED`), `PSMODULEPATH`, which
PowerShell 7 prefixes as it starts, and those a POSIX shell sets or resets
as it starts (`PWD`, `IFS`, `OPTIND`, `PPID`, `SHLVL`, every `BASH_` name and
the rest of bash's). So the script and the backend get the owner's value as given. It keeps only the clips it accepts, the
report and the backend's log, masked (`oneshot_kept`). The script and the
backend write into a folder of their own (`m<n>-<head>.raw`). `kept_from`
copies out of it into the kept folder only those three:

- a file or folder that is a link, a junction or a second hard link is never
  read, copied or served (`plain`, checked again by the clip route);
- the report is decoded as strict UTF-8, each field masked (a number that is
  a secret's value included), and written once; one that is not UTF-8 JSON is
  not kept.

The raw folder itself is checked the same way before anything in it is
read, `report_of` included, so a linked folder or report supplies nothing.
The kept folder is made new for each run, and each kept file, a clip
included, appears only once all of it is written (`staged`). A failed read
of the report while it is kept, or a failed look at any entry (`plain`
answers "no" only for a missing one), stops the run like a failed copy, and
so does any unforeseen error of the keeping; only invalid UTF-8 JSON is left
out. Every mask of a run comes from `env_of`, which labels the design's
secrets and the plan's own together (`oneshot_needs.hidden`), so no label
spells any of their values. It holds each secret in
its literal, URL-quoted and JSON-escaped spellings (`spelled`), each also
with its line endings as a text-mode read turns them. So the script's
output, the report's metrics and notes, the backend's log, the owner's note
and every error, an unforeseen one included, mask the same forms. The line
endings include those a Windows text-mode write and a text-mode read give
together. One read of each secret, just before the launch, gives its
presence, the child's environment and the mask; the store is not read
again for it. A plan input the design names too takes the design's read.
A design secret the plan does not list is read the same strict way, so a
failed read of it stops the run too. A read that fails stops the
run before its folder is made. A secret the store does not hold is left out
of the environment, so a variable of its name the server has never stands
in for it. A note is kept only behind a mask holding every secret of the
plan and of the design. That mask is read apart from the launch toolchain,
so a toolchain gone since never blocks a verdict. A design secret that
cannot be read at the verdict counts as unread, never as absent. When the
plan or a secret cannot be read at the verdict, a note is refused (409), and
the owner approves without one or tries again. A failed PR comment, its
temporary file included, is kept on the record. If that record cannot be
saved, the notice says so. The review starts all the same. A report that
exists but cannot be read off the disk stops the run for the owner, and the
build is not charged; a missing or invalid report is the build's. When the backend fails to
start, its log is masked whole before its end is cut for the error, so the
cut never splits a secret. The removal counts a
folder as gone only when looking at it finds nothing; one that cannot be
looked at is still there and stops the run. Then the raw folder is removed whole, on
every exit, a failed copy included:

- a link or junction at its root is removed and never followed;
- inside, only a failed removal is retried, once, with the entry made
  writable;
- success is read from the disk.

If the copy fails or the folder remains, the run stops for the owner with
that reason, an unforeseen error of the run included, and records no
result. The next start removes every leftover raw folder before it runs,
and stops again if one remains or the folder cannot be listed. The raw
folder is made only after the inputs are checked again under the lock, so
an input taken back at that moment leaves none. A link, junction or hard
link the script or the backend plants, and a file swapped between a check
and its read, are outside what the keeping promises (owner decision above). The plan and the report are
read strictly: `NaN`, `Infinity`, an overflowing float or a lone surrogate
escape is refused, and so is a byte that is not UTF-8. The plan is read as
the built HEAD holds it, byte for byte, by the screen, [실측 시작] and the
worker alike, never from the tree, so all three use and refuse the same
plan, and [실측 시작] sends a refused one back to the builder. Git writes it
into a file, not a pipe, so a timeout holds on Windows. A metric equal to a secret's value, in any numeric
spelling, never counts as a measurement. An integer is compared exactly; a
float, already rounded by the reader, is compared with the secret rounded the
same way. A refused report's error never quotes its token, and is masked. The inputs are checked again under
that lock right before the run, so one taken back meanwhile stops for the
owner without a failed run. The copy and the removal run on every exit,
a failed read of the report included. A stored
value changed at any moment of the run sets that result aside: the values
and the change count are read together, and compared and saved together,
under the lock every change takes. The count moves only once Credential
Manager changed, so a refused change, a removal of a value already gone, or
the same value given again leaves the run's result standing. The
run then stops for the owner to measure again. The health check goes through no proxy.
Its stop is made before the worker starts and also
holds while the server shuts down. A plan that no longer holds sends the run
back to the build agent from the same button. A failed metric, a missing report or a backend
that does not come up sends the result back to the build agent. At the
review's round cap it stops for the owner instead; their [실측 시작] then
sends that failure to the build agent with a fresh budget, and never
measures the failed HEAD again as it is. That choice and the worker's
launch are made in one lock scope. The button reads "빌드 에이전트에게
고치게 하기" there and needs no input, since nothing is measured. An input the
script calls wrong stops for the owner, who fixes it and presses
[실측 시작] again. A run of passed metrics waits for the owner. They listen to
the clips, then approve, which comments the record on the pull request and
starts the review, or refuse with what they heard, which goes to the build
agent. The verdict names the run's number and HEAD. A tree that is no longer
that HEAD, or holds uncommitted changes, sets the result aside and sends the
run back through the build check. The worktree stays held from that check
until the verdict is saved, and the comment goes only to a pull request #1
whose head, saved and as GitHub shows it just before the comment, is the
approved HEAD. A comment GitHub refuses is kept on the
run as `comment_error` and told to the owner. The review reads the approved measurement of its
HEAD (`measured_text`). A finding the measurement contradicts is P2.

No review runs without that approval. A run that reaches the review another
way goes back to `measure`. The reviewer reads its own detached checkout of
`built_head`, made with hooks off and removed after the turn, so nothing
the worktree holds meanwhile reaches it. A worktree that moved off
`built_head` goes back through the build check first. A comment GitHub
refuses is masked before it is kept or told. Any route
that changes a stored value (`oneshot_needs.stored`) also sets a waiting
result aside. Review Loop neither lists, takes, asks again for
nor dispatches (`kick`, `rereview`) the pull request of a oneshot run that has
not finished (`oneshot_profile.in_run`). The ownership check needs `psutil`,
declared in `requirements-chat.txt`.
S8 makes the draft ready; when GitHub refuses, S8 stops instead of finishing.
It does so only for the publication of this hand-over: a pull request record
left by an earlier one, after a push that failed since or a text turn that
moved HEAD, stops S8 with that fault instead. Once pull request #1 exists,
every publication goes to that draft, the one measured and reviewed: a
repair's draft publication and S8's final one alike. That draft is checked
to be the branch's open pull request before the push, since a push moves
whichever pull request is open on the branch, and its number again at the
pull request write, into the base the draft was opened into, whatever the
repository's default branch is now. When it is not, or the check cannot be
made, the publication stops with that reason, pushes nothing and opens no
replacement. The owner reopens the draft and
resumes.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 12a | The builder's answers reach the review with pinning tests; a turn without a commit goes back to the builder; an unchanged HEAD is never reviewed twice | In this pull request |
| 12b | Conflicts go to the owner's board; "replace the mechanism" once per area; later stages' findings go to that kind's build | In this pull request |
| 12c | The build agent's `.wiki/measure.json` and its check, the headless backend started at the gate, and pull request #1 right after it | In this pull request |
| 12d | The real measurement before the review: the owner's step-by-step inputs, the automatic metrics and clips, the owner's verdict, the record on the pull request and in the review | In this pull request |
| 12e | The stopped `vtuber-ko-dub` run: `[재개]` sends its build agent, which commits the headless server, the measurement and its guides; then measure against a real Google sign-in and a real Japanese VTuber live | After the merge |
