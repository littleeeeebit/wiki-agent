# Step 11 — Real outside calls, no fakes

The overall design is in the [overview](0-overview.md). This step makes a
oneshot's tests reach the services its product calls, with what the owner
gives at design approval, and removes every fake from the tests.

Goal. Every test the gate runs exercises real code against real services.
What only the owner can give, such as an API key, an OAuth client, an account
or an installed tool, is asked for once, on the design sheet, before any test
is written. A run never proceeds on a stand-in for it.

## Why this step exists

The `vtuber-ko-dub` run of 2026-11-19 wrote its 28 contract tests against
fakes, as `oneshot-tests-kinds.md` requires: "A call that leaves the app is
replaced by a fake in the tests; none goes to the network." Google sign-in,
the Gemini API, the Twitch and YouTube ingest and the Python workers were all
stand-ins. The core build passed every test with none of them working, and
the build turn said so: each item passed "because its core contract test
passes", and those tests "use stand-ins for the Python worker and Google".

The backend review then refused five rounds in a row while the diff grew from
8,191 to 13,821 added lines. Its findings were about what the fakes hid: a
worker that fails to start, a download that installs nothing, a reconnect
that never retries. No test could show any of them, so the reviewer reasoned
about them from the code, round after round (see
[3-spec-backend.md](3-spec-backend.md), "Backend review", for the rules that
step adds).

The owner's experience is that fake tests never helped and only became debt.
On 2026-11-20 they decided the four questions below.

## Decided with the owner (2026-11-20)

| Question | Decision |
| --- | --- |
| When the owner is asked | At design approval: the design lists, for each outside call, what the owner gives, and the sheet collects it |
| When the owner cannot give it yet | The run waits at the sheet with the list shown; `[재개]` after it is given. No fake fills the gap |
| Where real calls run | The owner's local gate only. Secrets live in Windows Credential Manager and reach only the gate's environment. CI skips those tests and says so |
| How far fakes go | None at all. A check that needs a device or a screen is not a contract test; it waits for that kind's device stage |
| Whether the build agent gets the secrets | Yes, in its own gate runs' environment; its prompt forbids printing them, and the server refuses a commit or a turn answer that contains a secret's value |
| What real calls cost | The card shows, per run, how many times each outside call was made; no limit until the owner has seen the numbers |

## Design

### The design names what the owner gives

Every design prompt returns `oneshot-outside`, the single-kind web design
included (today only the native and several-kind designs have it). Each
outside call gains `needs`:

```json
{"id": "X3", "target": "Gemini API generateContent", "does": "…", "failure": "…",
 "depends_on": ["R32"],
 "needs": [{"id": "N1", "what": "Gemini API 키", "kind": "secret", "env": "GEMINI_API_KEY"}]}
```

| `kind` | The owner gives | Kept |
| --- | --- | --- |
| `secret` | A value typed on the sheet | Credential Manager, `wiki-agent/oneshot/<repo>/<env>`; never in the spec, the record or Git |
| `account` | Confirms an account or project exists (a Google Cloud OAuth client, a Twitch login) | The confirmation in the spec |
| `tool` | Confirms a program is installed, which the server then finds on `PATH` | The resolved path in the spec |
| `file` | A path to a file the product reads (a model, a sample) | The path in the spec, checked to exist |

A call that needs nothing from the owner (a public page, a local program the
server installs) has `needs: []`. Two calls that need the same thing list it
under the same id with the same fields. The server refuses a `secret` without
an `env` name, one id for two different needs, and one `env` under two ids.

### The sheet waits for them

R1 shows a section "주인이 줄 것" under the outside calls: each need with its
call and the requirement it serves. A `secret` is a password field that is
written to Credential Manager when typed and never shown again; the sheet
shows only whether it is set. Approval is refused while a need is missing,
with the list of what is missing. The run stays at the sheet, and nothing
stops: the owner gives the rest and approves.

### What 11a1 builds: the record, the store and the gate

11a is split in two. 11a1 is the server side: a design may carry `needs`, the
owner can give each one, and approval waits for all of them. 11a2 adds the
screen's inputs and the prompts, and gives the single-kind web design its
`oneshot-outside`.

`oneshot_needs.parsed` checks `needs` where the outside call is checked. A
call without `needs` needs nothing, so a design from before this step still
parses.

The owner gives a need with `POST /api/oneshot/spec/{repo}/needs/{id}` and
removes it with `DELETE` on the same path. Both work only while the run
waits at R1 with no turn running. Both name the sheet the owner saw (its
`source`, 409 otherwise), because after a redesign the same id may name
another need. No answer or error repeats what the owner typed, and a body
the server cannot read comes back without its input, on every route.

| `kind` | Body | Kept |
| --- | --- | --- |
| `secret` | `value`, trimmed, at most 2560 bytes as UTF-16, what one credential holds | Credential Manager, `wiki-agent/oneshot/<repo>/<env>`; the spec and the answer never hold it |
| `account` | `confirmed: true` | `given[id]`, with the need's `what` |
| `tool` | `value`, a program name or path | `given[id]`, with the path `shutil.which` resolves; refused when there is none |
| `file` | `value`, an absolute path | `given[id]`, with the resolved path; refused unless a file is there |

A need counts as given in three cases:

- A `secret` counts while Credential Manager holds its `env`.
- An `account` counts while `given[id]` was given for the same `what`.
- A `tool` or `file` counts while it was given for the same `what` and its
  path is still a file.

A redesign that changes what a need is therefore asks for it again. A secret
stays under its `env` for the repository's next run until the owner removes
it. The view lists every `env` Credential Manager holds a secret of the
repository under (`secrets_kept`), read from the store itself rather than
the design. `DELETE /api/oneshot/spec/{repo}/secrets/{env}` removes one at
any stage, including one a redesign renamed or dropped. A host without
Credential Manager shows the sheet with every secret missing and refuses
to store one.

`POST …/r1` refuses an approval while any need is missing, with 409 naming
each missing id and `what`. An edit with comments still goes to the
redesign. The sheet gains "주인이 줄 것": each need with its kind, the calls
that need it and the requirements those calls depend on. The sheet is also
the committed design document, so it never says whether a need is given.
The spec's view carries `needs`, each with `given`, for the screen.

### What 11a2 builds: the prompts and the screen

Every design prompt returns `needs` on each outside call, with the four
kinds, the `env` rule and the instruction that tests run against the real
service. The single-kind web design gains `oneshot-outside` as its sixth
block. Its server has no modules, so an outside call names the requirement
it serves and the endpoint that makes it in `depends_on`, the way a test
names both. An edit of either reaches the other in a redesign, as a module's
`calls` do. The R1 brief names every outside call in "알아 두실 것" on every
kind, the web included. The web sheet lists the server's outside calls under
its API, with "주인이 줄 것" after them. A web design stored before this step has no
outside calls and shows none.

The R1 screen shows "주인이 줄 것" above the sheet. Each need has its kind,
its `env`, the calls that use it and whether it is given:

- a `secret` is typed into a hidden field and is never shown again;
- an `account` is confirmed with one button;
- a `tool` takes a program name or path;
- a `file` takes an absolute path.

Each give or take-back sends the `source` of the sheet on screen, and the
panel is keyed on it, so a draft never outlives the sheet it was typed on. At
any
stage, the screen lists each secret Credential Manager still holds for the
repository that no need of today's design names, and removes it on request.

### Tests and builds use the real thing

`oneshot-tests*.md` and `oneshot-build*.md` lose the fake rule and gain its
opposite: no fake, mock, stub or stand-in of an outside call, which is made
for real with the environment the server sets. The one stand-in a test may
use is for the device platform a thin platform module wraps (the Android
framework, a Windows API), decided with the owner on 2026-11-21: that
module's contract is tested on this PC against it, and the kind's device or
screen stage checks the real one. A web app's tests have no stand-in at all. The server sets every
`secret` need's `env` in the environment of the gate it runs
(`oneshot_build.built_env`). The gate already fails a run with a skipped
contract case, so a test that skipped for want of a key fails locally.

The tests and the build are told every need: a secret by its variable, a
program or a file by the path it was given at, an account by what it is,
never a value (`oneshot_needs.told`). Every gate the server runs for the run
has each given secret, read from Credential Manager for that gate
(`oneshot_needs.gate_env`, through `toolchain` and `built_env`). That covers
the tests stage, the build's gate, the front build's gate and the pull
request's gate, and the web app the front build starts for its screenshots.
Each gate's output shows `[N1]` where a secret's value or its URL-quoted form
would stand (`common.masked`, `oneshot_needs.hidden`). The mask is read off
the environment the gate starts with, never from the store a second time, so
the value launched is always the value masked. Every JUnit report, the
pinned cases' and the one the adapter's gate writes, goes outside the tree,
is read once and is deleted (`oneshot_build.judged`), and a case whose name
holds a secret is refused. A secret the design names but the store does not
hold is removed from the environment, so a variable of that name the server
has never stands in.

The web look masks only each finding's text, so a secret as short as a JSON
delimiter never changes its shape. Each screen is read and shot while no
script or animation of it or of its frames runs (`oneshot_look.still`), and
the picture is kept only if every frame still holds the document read, since
a refresh navigates with scripts off. An animation not started yet is let
start before it is held, since one held while pending starts later in its
before phase.

What is read is the text the page carries, drawn or not, each stream matched
on its own: the text and CDATA nodes outside code of every frame's document
and of each open shadow root, joined so a value split across elements reads
as one. Beside each is its rendered text, so hidden text between two
visible parts never splits a value the screen joins: the nodes `innerText`'s
rule keeps (laid out, their element's `visibility` visible), read node by
node, since the getter on a hidden root falls back to text that includes
stylesheets. The value, placeholder and alt of fields and images are read
too; never markup or a stylesheet. Matching is a union over the streams and
ignores case, decided with the owner on 2026-11-22: `text-transform` can
draw any casing of a value, and a casing of a secret counts as the secret.
One fold serves matching and masking (`common.folded`): case folding plus
the Turkish dotted and dotless i a page's `lang` can draw, a dot above
folding away only after an `i`. Masking finds each folded secret in the
folded text and replaces the original characters it came from, so the
critic's findings and stop reasons are masked in every casing the look
matches, one that changes length included. A secret is matched and masked
as spelled too, since a character's fold can depend on the one before it,
and a secret that folds to nothing matches only as spelled. Masking claims
each character once and joins the result in one pass, so a short secret
in long gate output costs time in proportion to its matches. Within one stream a value broken across lines still matches, as spelled
and folded alike, with whitespace removed both before and after the fold,
since a fold can depend on the character a line break stood next to. A secret in hidden text has reached the browser all the same, so it
is the app's leak to fix, and whether the browser draws it is never asked. A
screen whose text carries a secret, or has a frame that cannot be read, is a
high finding and is never shot. The critic sends its pictures to a model,
and a high finding sends the build back before the critic runs.

The guard stops at text, decided with the owner on 2026-11-21. A secret
drawn as pixels, into a canvas, an image or a stylesheet's `content`, can
reach the critic, the same trust the build agent gets in 11b2a. What the
critic writes, its findings and the reason it stopped, is kept with `[N1]`
in each secret's place, from the first write on (`oneshot.turned` takes
`hide`). The mask is the one the look's web app was started with
(`oneshot_front_build.MASKS`), held in memory only. It is released once no
critique reads those shots again: R3 opens, the run saves itself back to a
stage before the critique, or it drops its frozen PC build. A server started
since has none, and a run with a secret takes
those shots again before any critique.

The build agent's own turns carry the secrets too (11b2a, below). The PC
app's screen check and the Android device stages start the product with them
as well (11b2b), read from the same environment read the web look's mask
comes from:

- The PC app gets each secret's variable at every start, over any spelling of
  that name its own launch settings carry. A secret the design names but the
  store does not hold is dropped from the start's environment, so the
  server's variable of that name never stands in for it.
- The Android app has no environment to receive one. On the emulator and on
  the owner's phone, the debug build gets `oneshot-secrets.json` in its own
  storage (`Context.getFilesDir()`): a JSON object keyed by each secret's
  variable. It is written through `run-as` with the values on standard input
  only, never on a command line or in the APK, before the screens and the
  instrumented tests run. It is removed when the stage ends, whichever way it
  ends. A removal that fails fails the stage, so a phone round never passes
  with the file left on the owner's phone. Decided with the owner on
  2026-11-23: instrumentation arguments (`am instrument -e`) would show the
  values on adb's command line and in the device's process list.
- Each screen's read text gets the web look's check: every UI Automation
  string of the PC app's start state, and every attribute of the Android
  hierarchy read before and after its picture. A screen whose text holds a
  secret is a high finding and its picture is never kept. Every finding and
  refusal of these stages is masked. A PC look whose app carried secrets
  keeps only the shots it checked and copied: the desktop session removes its
  `evidence` folder (the driver's raw shots and reports, the app's output)
  with its data folders, and recovery does the same. On Android each
  hierarchy dump is removed from the device as soon as it is read. A phone
  round's record owes the dump from its first save, before any read can leave
  one, so a round cut by a failed removal or by a restart keeps it owed, the
  kept app's included. The card shows it until the phone is listed again and
  a removal succeeds.
- A secret cannot take a name a look sets when it starts the app (`PORT`,
  `APP_DATA_DIR`, `ONESHOT_SCREEN`, `APPDATA`, `TEMP` and the rest of
  `oneshot_needs.STARTED`): the look's value would reach the app in its
  place. The design refuses it, and a design saved before the rule stays
  at R1.

The check stops at text here too. A value drawn as pixels, or shown on
Android only between the two reads, is not seen. The phone round's frame and
memory measurement runs after the file is removed.

In CI the variables are absent. Each test that needs one skips with the need's
id in its reason, and the run's CI summary lists those skips as owed to the
local gate (11b2c). A public repository never receives a secret.

### The review reads fakes as weakened contracts

The review prompts already refuse "a contract test made weaker by something
outside its file (a fake, a configuration, a mock that hides the
behaviour)". They gain a fake inside a test file too: a P1 whose `breaks`
names the test.

### What the agent holds, and what it costs

The build agent runs the gate itself while it works, so its turns get the
secrets in the same environment the server's gate has. The build prompt
forbids printing them.

The worktree's session is started with each given secret set, and a secret
the design names but the store does not hold is dropped, as in a gate
(`oneshot_needs.agent`). A session whose secrets changed, given or removed,
starts again before its next turn and resumes its conversation. The session
is the worktree's, so a turn the owner types there carries them too.

The screen and the turn's record show `[N1]` in each value's place, in the
answer, the steps and the tool diffs. The check reads the answer as it was
given. Before any stage check reads it, and so before the server commits
anything for the turn, the server looks for each value in three places, in
any casing:

- the answer;
- every commit since the design commit, which is the history the pull
  request pushes;
- what is not committed, untracked files included.

A file Git ignores is the agent's own and is not read. One value found
refuses the turn with the need's id and the place, never the value. A value
in a commit is removed only by rewriting those commits.

A transcript the agent's own tools write may still hold a value, and so may
a committed binary file. That is the cost of the agent running real tests.

The card counts, per run, the gate runs that reached each outside call (the
tests that name a need), shown beside the run's spending. Nothing is limited
until the owner has seen those numbers.

### A credential the build commits anyway

The backend review grades a committed credential P0. A repair that deletes
it leaves it in the history the first pull request pushes, and a later
review that sees only the aggregate diff cannot tell. The leak check above
covers the values the owner gave. A value the agent obtained some other way
is this step's to handle too. PR #410 tried a hold the owner cleared by
revoking, and its review found new cases six rounds in a row:

- a value in a pinned file;
- a replacement key on the same line;
- a case alias of a path on Windows.

The owner moved it here on 2026-11-20. The check belongs where the server
knows the values, at commit time, rather than in the reviewer's reading.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 11a1 | A design's outside calls may carry `needs`; the owner gives each (secrets to Credential Manager), the sheet lists them, and R1 approval waits for all of them | Done — #416 |
| 11a2 | Every design prompt returns `oneshot-outside` with `needs`, the single-kind web design included; the R1 screen takes each need and shows whether it is given | Done — #417 |
| 11b1 | Tests and build prompts without fakes, told every need; every gate the server runs carries the given secrets | Done — #418 |
| 11b2a | The build agent's own turns carry the secrets under the server's leak check | Done — #424 |
| 11b2b | The PC app's screen check and the Android device stages get the secrets | In progress |
| 11b2c | The CI summary lists the skips owed to the local gate; the card counts the gate runs that reached each call | Not started |
| 11c | The review refuses a fake inside a test file | Not started |
| 11d | A real run of a product with a keyed outside call, end to end | Not started |
