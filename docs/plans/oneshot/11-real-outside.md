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
owner can give each one, and approval waits for all of them. No prompt asks
for `needs` yet, so a run today sees no change. 11a2 adds the sheet's inputs
and the prompts, and gives the single-kind web design its `oneshot-outside`.

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

### Tests and builds use the real thing

`oneshot-tests*.md` and `oneshot-build*.md` lose the fake rule and gain its
opposite: no fake, mock, stub or stand-in of anything, an outside call made
for real with the environment the server sets, and a check that needs a
device or a screen left to that kind's device stage. The server sets every
`secret` need's `env` in the environment of the gate it runs
(`oneshot_build.built_env`). The gate already fails a run with a skipped
contract case, so a test that skipped for want of a key fails locally.

In CI the variables are absent. Each test that needs one skips with the need's
id in its reason, and the run's CI summary lists those skips as owed to the
local gate. A public repository never receives a secret.

### The review reads fakes as weakened contracts

The review prompts already refuse "a contract test made weaker by something
outside its file (a fake, a configuration, a mock that hides the
behaviour)". They gain a fake inside a test file too: a P1 whose `breaks`
names the test.

### What the agent holds, and what it costs

The build agent runs the gate itself while it works, so its turns get the
secrets in the same environment the server's gate has. The build prompt
forbids printing them. Before a turn's commit is accepted, and before its
answer is saved, the server looks for each secret's value in the commit's
diff and in the answer; one found refuses the turn with the need's id,
never the value. A transcript the agent's own tools write may still hold a
value; that is the cost of the agent running real tests.

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
| 11a1 | A design's outside calls may carry `needs`; the owner gives each (secrets to Credential Manager), the sheet lists them, and R1 approval waits for all of them | In progress |
| 11a2 | Every design prompt returns `oneshot-outside` with `needs`, the single-kind web design included; the R1 screen takes each need and shows whether it is given | Not started |
| 11b | Tests and build prompts without fakes; the gate's environment carries the secrets; CI skips name the need | Not started |
| 11c | The review refuses a fake inside a test file | Not started |
| 11d | A real run of a product with a keyed outside call, end to end | Not started |
