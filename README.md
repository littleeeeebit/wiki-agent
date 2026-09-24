# ai-coding-agent-wiki

This private copy is becoming a coding agent program with asking the wiki as
its main feature. The plan is
[`docs/plans/wiki-agent-0-overview.md`](docs/plans/wiki-agent-0-overview.md).

A wiki that stops a coding agent making the same mistake again as it moves
between projects, and the adapters that attach it to a project.

Adapted for coding agents from Karpathy's
[LLM wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
spec. Where the original's raw sources are papers and articles, here they are
conversation logs and execution records, so ingesting is not "read and
summarise" but "count and compare".

The bottleneck was neither storage nor search. It was that what is written did
not govern behaviour. So this wiki is not a tool for making more pages; it
does two things.

1. It sets a repeated instruction into a habit — the instruction becomes one
   skill
2. It turns the rules that can be into checks — the rules actually held were
   not prose, they were guards that threw and gates that went red

The schema is [`SCHEMA.md`](SCHEMA.md), the enforcement is
[`ENFORCEMENT.md`](ENFORCEMENT.md), and the diagnosis and update procedures
are [`MAINTENANCE.md`](MAINTENANCE.md). Read them before writing or changing a
page.

## Layout

```
tool/        census · intersect · apply · inject · english_progress
             trigger_audit · lint · repo_lint · graph · repo_graph · mirror
  markers/   census markers, per language
operator/    knowledge that follows the person — the review loop · edit as diffs · English progress
craft/       knowledge that follows the technique — async pickup · finish the instruction · git blocks
skills/      repeated instructions set into procedures (enforcement layer 3) — review loop · after merge · retrospect
adapters/    per-project slot values
raw/         census output and measurements. Immutable
graph.json   the policy graph `graph` produces. The views consume it
web/         the chat screen and the wiki map. `chat.py` serves data through `/api/graph`
```

`project` scope knowledge is not here. It lives in the target repository, and
an adapter only reads and composes it. Other people's landmines do not come in
— that is itself an incident.

## How to use it

Attaching a new project starts with a census. Not knowing what breaks means
not knowing what to write down.

```bash
python tool/census.py --project ~/PycharmProjects/<name> --out raw/census-<name>.jsonl
python tool/intersect.py raw/census-*.jsonl
```

The output becomes that project's draft adapter. Fill the slot values into the
target checkout's `.wiki/adapter.toml` and attach. An existing install with no
local adapter keeps falling back to `adapters/<name>.toml`.

```bash
python tool/apply.py --project ~/PycharmProjects/<name>            # preview
python tool/apply.py --project ~/PycharmProjects/<name> --write    # write
```

By default `apply` writes one file, `.claude/settings.json` — the
`enforce.deny` a page declared (layer 1) and the injection hook (layer 2).
Existing settings are merged, never overwritten.

### The team installer

`tool/setup_agents.py` checks the environment, the wiki version and the host,
then installs and verifies through the existing `apply.py`. It depends on
neither the project name nor a sibling folder name, and it does not copy an
adapter into the hub. It needs Python 3.11 or later, Git, the packages in
`requirements-hooks.txt`, and the chosen host's CLI. On Windows this wiki's
commands use Git Bash for Claude and PowerShell for Codex.

```powershell
python tool/setup_agents.py --project "D:/팀 작업/checkout" --agent both
python tool/setup_agents.py --project "D:/팀 작업/checkout" --agent both --check
```

On a machine that opens worktrees or updates its CLIs often, install at the
user level instead: `python tool/setup_agents.py --global --trust-codex`.
Every checkout, worktrees included, then reads the same hooks, and
`--global --check` after an update tells you whether they still run. See
[docs/hooks-setup.md](docs/hooks-setup.md).

The target holds `.wiki/wiki-revision` (the 40-character commit SHA of the
wiki to use) and `.wiki/adapter.toml`. Set `agents = ["claude", "codex"]` and
the `[slots]` values `review_dir`, `gate_cmd`, `live_cmd`, `server_stop` and
`scratch_dirs` to fit the project. Codex needs `[features] hooks = true` in
`.codex/config.toml`. Project rule entry points such as `AGENTS.md` and
`CLAUDE.md` are owned by the project. The tool does not download dependencies,
change versions or grant trust on its own.

The hosts chosen with `--agent claude|codex|both`, and those already
installed, are preserved in the target's `.wiki/installed-agents.json`. That
file and the generated hook settings are excluded from the project's git.
Existing user hooks and settings are merged, and on failure every file this
install wrote is restored to its original bytes. A reinstall, a renamed folder
and a same-named checkout each use their own local adapter. After moving,
reinstall against the new path. Success exits 0 and failure exits 2.
`--check` is read-only.

A default install refuses a working copy whose code or rules differ from the
pinned SHA. `--allow-dirty-wiki` exists for verifying uncommitted development
and still requires the SHA to match. A project's pinned SHA is set to a commit
that contains the shared code and passed the install verification. Updating
the wiki also means confirming the candidate commit installs, then updating
the pinned SHA with it.

The install check and the host's automatic events and question UI are separate
evidence. In a new session, review and trust the project and the hooks
yourself, and confirm all four events are actually delivered. Codex's project
trust and hook-definition trust follow the
[official hook guide](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks).
Confirm the options UI directly with the `request_user_input` permitted in
that session, and do not route around it with the async form.

### Attach it to this repository too

The session that edits the wiki has to have the wiki loaded.

Both agents are install targets on the hub as well. Attach only Claude and a
Codex cell running in this repository runs with no wiki hook at all.

```bash
python tool/apply.py --project . --write                 # Claude
python tool/apply.py --project . --agent codex --write   # Codex
```

`.claude/` and `.codex/` are not committed. Interpreter paths differ per
machine, so run these commands again on the clone.

Installed and running are different evidence. These commands succeeding means
the settings file exists, not that the host actually delivers the event. Codex
runs only once trusted in `/hooks`, and `apply` does not write that trust.
Whether it fired is confirmed by a line for that session actually appearing in
`.wiki/trajectory.jsonl`.

There is a separate check for the install state alone — it answers with an
exit code.

```bash
python tool/apply.py --project . --check                 # this repository's Claude wiring
python tool/apply.py --project . --agent codex --check   # the same for Codex
python tool/lint.py --check                              # every expected agent
```

`CLAUDE.md` is not touched. It holds project-scope knowledge this wiki does
not own, and adding sentences to an always-loaded file is the very problem
this wiki was built for. Skills are `operator` scope, so hanging them once in
`~/.claude/skills/` is enough.

### Connecting to Codex

This mode merges into the target's `.codex/hooks.json` alone. The interpreter
and the wiki path are decided at install time, so another machine installs
again. Existing Claude settings and the Orca hooks in the user's `CODEX_HOME`
are left alone. Codex's hooks feature and project trust have to be on, and
after installing, the five new wiki hooks must be reviewed and trusted in
`/hooks` before they run. The installer does not write the trust hash.

Utterance injection, session state and automatic updating use the same tools
as Claude. The stop check reads Codex's official `last_assistant_message` and
does not guess at the transcript file format. `codex_pretool.py` applies the
wiki's tool-name blocks and `Bash(...)` argument patterns along with the
existing diff and progress-language checks. `request_user_input_async` and
`functions.request_user_input_async` are forbidden outright by the existing
`enforce.deny` declaration, regardless of whether options are present, and the
permitted `request_user_input` is used instead. When Codex supplies no tool
description, the progress-language check has no value to judge. The shell
check is limited to known direct command shapes. It is not a security boundary
covering nested shells, dynamic commands and every PowerShell write, and it
does not port the whole of `permissions.deny`.

The threshold at which one injection switches to a file preview is 12,000
tokens. Above that, Codex is given the path to the full text.

Verification: `python -m pytest -q tool/test_codex_hooks.py`. It installs into
a temporary repository, runs the commands, and confirms real injection,
blocking, passing and updating, and that existing settings survive. On Windows
it checks with `pwsh`. Codex commands take PowerShell's call operator `&`, and
an existing install is updated with the `--write` commands above and then
trusted again in `/hooks`. Codex running events automatically has to be
confirmed separately, after the trust is granted.

### What a census counts

| Class | What |
| --- | --- |
| Repeated instruction | How many times the same thing was retyped. Retyped although already written means that rule does not work |
| Correction | Where the user said the agent was wrong |
| Resume demand | Where it stopped somewhere it should not have and had to be pushed again |
| Partial completion | Where part of the instruction was done and the rest had to be asked for again |

It prints the samples no marker matched. Without that section, a census
measures the bias of whoever wrote the markers.

It produces no percentages. String matching cannot catch the same rule written
in different words. It extracts the sentences to compare instead, and a person
judges.

## Looking at it

```bash
python tool/graph.py --project ~/PycharmProjects/<name> --project ...
tool/chat.cmd                      # http://127.0.0.1:8787 · "위키 지도" at the lower left
tool/chat.command                  # on macOS and Linux
```

The map lives in `web/`. The live force layout, dragging, zoom and pan,
neighbour highlighting and search are taken from Obsidian; what a node means
was changed. An Obsidian node is a thought someone wrote and its size is the
number of links. A node here is a rule built by counting session logs, and its
size is how many characters that rule loads in one turn — put there so that
the cost of adding pages is visible on screen.

Two axes Obsidian does not have.

- Co-injection. How often two pages rode in the same turn of a real utterance,
  which is the thickness of the dotted line.
- The project. Switching tabs reads that repository's
  `.claude/settings.json`, so only rules actually attached appear solid.
  Unattached ones look hollow, and slots the adapter has not filled show up
  with them. An Obsidian graph is inside one vault, so it never asks this.

## Chat: the exact answer and the plain explanation

Team members follow the
[chat install and sign-in guide](docs/chat-setup.md). Prepare with
`python tool/setup_chat.py install --agent codex`, or `--agent claude|both`.
It uses no personal path or login of the author's; each machine uses its own
CLI and account. Projects default to the wiki's parent folder, changeable at
install time with `--workspace`.

Start it with `tool/chat.cmd` (`tool/chat.command` on macOS and Linux) and
choose the project, the model and the reasoning effort. The chosen CLI has to
be signed in first. Codex models are not hard-coded; they come from the
installed CLI's `model/list`. Only the efforts a model supports are shown, and
the chosen model id is passed explicitly as `--model` on both calls. If the
list cannot be read, an error is shown. After changing an account or a CLI,
restart the server to refresh the list. The query format follows
[the Codex App Server model list](https://learn.chatgpt.com/docs/app-server#models).

The buttons above an answer switch between the exact answer and the plain
explanation. The original comes first and the explanation is generated
afterwards, so the second call costs more time and usage. Both are stored, and
if generating the explanation fails the original survives. Old records are not
back-filled with explanations.

- [The answer prompt](tool/prompts/chat-answer.md): search the documents,
  confirm the sources, compare conflicts, separate what was verified. It reads
  and cites evidence inside the repository and distinguishes what it could not
  actually confirm. What an internal work name means, and what a number
  counted, are confirmed from the source and included in the reply.
- [The explanation prompt](tool/prompts/chat-explain.md): receives only the
  finished original, as JSON, and restates it preserving the meaning. The
  answer prompt, the question, the search history and the channel instructions
  are not passed. It runs in a separate temporary folder, in a new session,
  with no search tools. Numbers, conditions, negations, unconfirmed points and
  sources are preserved, and jargon is unpacked. The reader it writes for
  joined today. Instead of phrasing that assumes earlier conversation or
  development knowledge, it starts from what the work is, what is finished and
  what needs confirming. It invents no background that is not in the original.

Each prompt is written in English and specifies Korean output. They focus on
the order of steps, the input and output boundaries, what must be preserved,
and examples of faithful and unfaithful transformation, rather than on role
declarations. They draw on the
[prompting guide](https://developers.openai.com/api/docs/guides/prompt-engineering)
and the
[reasoning model guide](https://developers.openai.com/api/docs/guides/reasoning-best-practices).
The plain explanation is not a separate fact-check of the original. The
`file:line` in each answer opens the evidence directly.

The "common project" is shared by all five channels — progress, diagnosis,
retrospect, review and wiki. Conversation context and records are kept per
project and per channel, so leaving for another project and coming back
continues where it left off. A server restart restores the project selection
and the CLI sessions left in the records. "Clear context" applies to the
selected project's channel alone and does not delete records. Older records
with no project information are under "records from before projects" and are
not mixed into a handover. `chat_post.py`, which posts external briefings,
also requires `--project <path>`. Within one CLI, changing the model continues
the original conversation; switching between Claude and Codex starts a new
context. "Related rules" on screen is a display-time comparison, not evidence
that the host delivered a hook, and hooks are never re-run to produce it.

Checks: `python -m pytest -q tool/test_chat.py`,
`npm --prefix web run build`, `npm --prefix web run lint`. They confirm the
CLI events, the session separation, the original surviving a failure, and the
model and effort validation. These automatic checks guarantee neither the
accuracy of every model response nor the quality of an explanation. To test
the quality of a real progress answer, press "clear this channel's context" on
screen first, wait for it to finish, then ask again. A check that re-explains
an old original is distinct from this whole-flow check. When reviewing,
compare two things separately: whether the body alone makes the purpose, the
current state and what remains understandable, and whether the original's
numbers, conditions and unconfirmed points survived unchanged. A response that
only adds a parenthetical gloss to a term, or turns "an instruction was
missed" into "the answer was wrong", does not pass.

## Maintenance

Before a Codex hook times out, its execution stack and process information are
collected locally and automatically. The install state, log location and
retention are in [hook self-diagnosis](tool/hook-diagnostics.md).

```bash
python tool/lint.py --repo ~/PycharmProjects/<name>      # the hub wiki
python tool/lint.py --check                              # including the real wiring (exits 1 on failure)
python tool/repo_lint.py --repo ~/PycharmProjects/<name> # an attached repository
python tool/trigger_audit.py raw/census-*.jsonl          # shared rules only — the knowledge axis is unmeasured
```

By default `trigger_audit` measures the shared rules alone. Measuring the
knowledge axis (the decision records) as well needs that repository's census
and `--project` together.

```bash
python tool/trigger_audit.py raw/census-<name>.jsonl --project ~/PycharmProjects/<name>
```

In output from a run without `--project`, the knowledge axis is unmeasured,
not `0`.

`lint` catches where the structure rots and re-running a census catches where
the content rots — a page written and the failure not falling means going up
the ladder, not rewriting the prose. When to run what, and what to do with the
findings, is in [`MAINTENANCE.md`](MAINTENANCE.md).

`tool/test_lint.py` watches that the checks actually go red. A green with zero
findings is not evidence that a check ran.

## State

Standing and attached are different things. Every layer standing means the
machinery exists in this repository; which repository it actually hangs on is
known only to `lint --check`.

`python` has to be able to read `yaml` and `tomllib`. `apply` confirms that
with the target interpreter before writing a hook — unable to read them, a
hook quietly does nothing, and that is the worst failure shape an enforcement
layer has.

## Grounds in the public copy

The original's rules, skills, applicability and severities are kept; only the
actual conversations, cases and measurements are withheld.
`sources_withheld: true` marks a rule whose grounds are private. `lint`
exempts the missing grounds only for existing rules carrying that mark, and it
does not substitute for a new rule's grounds. Installing and signing in with
your own CLI follows the [install guide](docs/chat-setup.md); managing the
public copy follows the [update guide](docs/publishing.md); what was verified
follows the [check record](docs/verification.md). The Korean mirror follows
the [mirror guide](docs/mirror-setup.md).
