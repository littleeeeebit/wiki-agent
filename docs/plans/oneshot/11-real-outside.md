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
server installs) has `needs: []`. The server refuses a `secret` without an
`env` name and an `env` name two needs share.

### The sheet waits for them

R1 shows a section "주인이 줄 것" under the outside calls: each need with its
call and the requirement it serves. A `secret` is a password field that is
written to Credential Manager when typed and never shown again; the sheet
shows only whether it is set. Approval is refused while a need is missing,
with the list of what is missing. The run stays at the sheet, and nothing
stops: the owner gives the rest and approves.

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

## Open questions

- The build agent runs the gate itself while it works. Giving it the secrets
  lets it run the real tests; it also puts the values in its environment.
  The proposal is to give them, forbid printing them in the prompt, and have
  the server refuse a commit or a turn answer that contains one of the values.
- Real calls cost money on every gate run. A per-run count of calls to each
  outside target, shown on the card, is proposed before any limit.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 11a | Every design returns `oneshot-outside` with `needs`; R1 shows them, keeps secrets in Credential Manager and refuses approval while one is missing | Not started |
| 11b | Tests and build prompts without fakes; the gate's environment carries the secrets; CI skips name the need | Not started |
| 11c | The review refuses a fake inside a test file | Not started |
| 11d | A real run of a product with a keyed outside call, end to end | Not started |
