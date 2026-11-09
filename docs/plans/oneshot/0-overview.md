# oneshot — From an empty folder and one sentence to a finished first pull request

What was decided with the owner is under "Decided with the owner"; the text
follows those decisions.

## Why

The app's cycle today assumes a repository that already exists: `next`
proposes candidates from its plans, a spec binds one task to one worktree and
one pull request, and the review loop carries it to merge
([loop overview](../loop/0-overview.md)). Starting a project from nothing is
not covered, and doing it by hand spends the owner's time in two places:

- Dozens of small setup choices (environment manager, OS, frontend stack,
  repository visibility, CI) are made again for every project, each one a turn.
- Frontend time goes mostly not to visual quality but to screens where
  everyday features and rarely used ones are mixed together.

## One-line goal

The owner types what they want in a sentence or two. The app talks the spec
into shape in a fixed order. The owner reviews exactly three times. The first
pull request already contains a working, tested backend and a frontend the
agent has looked at and fixed itself; the review loop then checks it once
before merge, as it does every pull request.

## Fixed by the owner

These come from the request and are not open.

| What | Fixed |
| --- | --- |
| Start | An empty folder and a minimal statement of what the user wants |
| Scope | Design, backend implementation and frontend implementation |
| Philosophy | The spec is built by talking with the agent, inside a fixed order of stages |
| Human reviews | Exactly three: R1 after backend design, R2 after frontend design, R3 after frontend implementation |
| No review | Backend implementation (nothing for a person to look at) |
| Before frontend design | The agent presents features split into frequently and rarely used, and the user decides the split |
| Between reviews | The agent works alone; the frontend is improved recursively by looking at its own screenshots |
| First pull request | Functionally complete when opened: backend, frontend, tests and README all present and the gate green |
| After the pull request | The existing review loop runs on it, started by the owner as for any pull request |
| Language | Progress, options and results in Korean (Orca) |

## The workflow

### Shape

Conversation is front-loaded so that the stretches between reviews need no
person. Every stage that talks to the owner sits before a review gate, never
inside an autonomous stretch.

```
 ┌─ talk ────────────────────────────┐   ┌─ alone ──────────────┐
 S0 intake → S1 environment → S2 spec → S3 backend design ──▶ R1
                                                              │ approve
 ┌─ alone ──────────────────────────┐   ┌─ talk ─────────┐     ▼
 S4 backend build + agent review ──▶ S5 feature split → S6 frontend design ──▶ R2
                                                                                  │ approve
 ┌─ alone ─────────────────────────────────────────┐                              ▼
 S7 frontend build ⇄ screenshot ⇄ critique ⇄ fix  ──▶ R3 ──▶ S8 first pull request ──▶ review loop
```

### Stages

| Stage | Talks to the owner | Agent does | Leaves behind | Exits when |
| --- | --- | --- | --- | --- |
| S0 Intake | One turn | Restates the request in the owner's words, names the product type (web app, CLI, desktop, API only) | `intent` with the owner's original sentence kept verbatim | The owner confirms the restatement |
| S1 Environment | One card | Prefills every setup choice from the saved profile, asks only the changed or unknown ones | `profile` for this project | Every required choice is filled |
| S2 Spec | A few turns of options | Asks scope, users, core scenarios, exclusions; fixes the domain vocabulary | Scenarios in Given/When/Then, glossary, exclusions | No scenario is marked undecided |
| S3 Backend design | None | Domain model, storage schema, API contract, main flows, test plan from scenarios | The R1 sheet | Its own design lint passes |
| R1 | Review | — | Approval or per-item edits | Approved |
| S4 Backend build | None | Scaffolds, writes contract tests from the scenarios first, implements, runs the gate, has an independent read-only session review the diff; nothing is pushed | Passing backend with tests | Gate green and the review approves the current HEAD |
| S5 Feature split | One or two turns | Lists every user-facing action from the built API with a proposed frequency class | Frequency map | The owner submits the split |
| S6 Frontend design | None | Information architecture, low-fidelity screens rendered as screenshots, tokens, states | The R2 sheet | Every frequent feature sits on its primary screen |
| R2 | Review | — | Approval or per-item edits | Approved |
| S7 Frontend build | None | Builds, then loops: screenshot, critique, fix, screenshot again | Screens, end-to-end tests, before and after screenshots | Stop rule met (below) |
| R3 | Review | — | Approval or a fix list | Approved |
| S8 First pull request | None | README for the chosen OS, CI only if public, final gate, pull request | The pull request, attached to the run's spec so Review Loop can find it | Opened with gate green |

The review loop after S8 is code review between agents, not a fourth human
review: its rounds fix findings without asking the owner, and the owner's only
act is starting it and pressing merge, as for every pull request.

### What happens when the agent is stuck between gates

The owner allowed exactly three reviews, so the autonomous stretches need a
rule for questions that come up mid-work.

| Option | Behaviour | Cost |
| --- | --- | --- |
| A. Decide and flag | The agent takes the reversible choice, records it as an assumption with what it rejected, and the next gate shows it at the top | A wrong guess is redone after the gate |
| B. Narrow emergency stop | As A, but money, security, data deletion or an irreversible external action stops and asks | A fourth touch, but only for things that cannot be undone |
| C. Never stop | Everything is decided and flagged | Risk of an expensive or unsafe action with no person |

The run uses A, with B's list as the only exceptions. Stops are counted and
shown so the owner can see if the "three reviews" promise is eroding.

### Rejection at a gate

| Option | Behaviour |
| --- | --- |
| Per-item edits (recommended) | Each item on the sheet has approve or edit; only edited items are redone, and the re-shown sheet shows the difference only |
| Whole-sheet reject | The stage reruns with the owner's comment |
| Rollback | R2 rejection may reopen S5 (feature split) when the problem is placement, not looks |

A re-shown sheet after edits is the same review, not a new one; the count of
three is the number of gates, not of rounds.

## Environment choices (S1)

The owner named four; the full set is below. The key proposal is a saved
profile: the first project asks everything, every later project shows one card
"same as last time?" with only the differences highlighted.

The table is the full schema. This version runs one stack end to end —
`webapp`, FastAPI, Vite and React — and shows the other kinds and stacks
disabled until a later plan gives them their own stages, gate and review
deliverables ([step 2](2-bootstrap.md), "Supported combinations").

| Choice | Options | Proposed default | What it changes downstream |
| --- | --- | --- | --- |
| OS now | Windows / macOS / both | The machine running the app | Script language, path handling, README commands |
| OS later | None / the other one planned | Planned | Forbids shell-only scripts; `.gitattributes` line endings; paths through `pathlib` |
| Python environment | `uv`-managed `.venv` / stdlib `venv` / conda | `uv` `.venv` | conda only when native scientific or GPU packages (CUDA, GDAL) are needed, so the agent asks only then |
| Backend language and framework | Python FastAPI / Node / none | FastAPI | Contract format (OpenAPI), test runner |
| Frontend kind | None (API or CLI) / server-rendered (Jinja and htmx) / SPA (Vite and React) / desktop (Tauri) | Vite and React, same as this app | Lets the agent reuse this app's design skills and patterns |
| Styling | Tailwind / plain CSS tokens / component library | Tailwind with tokens on `:root` | Theming, dark mode |
| Data store | SQLite / JSON files / Postgres | SQLite | Migrations, test fixtures |
| Authentication | None (single local user) / local password / OAuth | None | Whole screens and tables |
| Deployment | Local only / Docker / a cloud | Local only | Dockerfile, env handling |
| Repository visibility | Public / private | Private | Public: GitHub Actions CI and a license; private: no hosted CI, the local gate is the only gate |
| License (public only) | MIT / Apache-2.0 / none | MIT | `LICENSE` file |
| Package managers | npm / pnpm; `uv` / pip | npm, `uv` | Lockfiles |
| Tests | pytest, Vitest, Playwright | All three | Playwright also drives S7's screenshots |
| Lint and format | ruff; Prettier or Biome | ruff, Prettier | Gate command |
| UI language | Korean / English / both | Korean | Copy, i18n scaffolding only when both |
| Docs and commit language | Korean / English | Asked once, saved | See the readability section: this interacts with the hub's English rule |
| Responsive | Desktop only / desktop and mobile | Desktop and mobile | S7 viewport count |
| Uses an LLM | No / yes with which provider | No | `.env.example`, key handling |
| Budget | USD and hours ceiling for the whole run | Asked every time | Checked before every model turn against the run's ledger; stops the run, keeps what was built |

The private-repository rule follows the owner's words: private means no
GitHub Actions by default. The local gate then has to be strong enough alone,
so it runs the full test suite and the end-to-end tests, not a subset.

## Spec readability

The owner's problem: specs nobody reads, and Korean that reads as a
translation. The proposals below attack three different causes: too much text,
the wrong kind of text, and text written in the wrong language first.

### Cause 1 — the reader is given everything

- One source, several views. The spec stays one structured record; each gate
  renders only what that gate decides. This is what the spec-views experiment
  measures for the agent side ([main-2 report](../../research/spec-views-main.md));
  the human side is the same idea.
- A gate sheet, not a document. R1 shows at most one screen: decisions taken,
  assumptions made, what is irreversible, and what to check. Everything else is
  folded behind it. The server refuses a sheet over its length budget, the
  same way it refuses a broken `spec` block today.
- Assumptions first. Every line carries `confirmed` or `assumed` (the status
  `spec_views` already uses). A reader in a hurry reads only the assumed lines.
- Difference only on re-show. After per-item edits the sheet shows what
  changed, never the whole again.

### Cause 2 — prose where a picture or an example would do

- Backend design as a diagram and two tables: an entity diagram, an API table
  (method, path, what it does in one line), and the main flow as a sequence.
- Frontend design as screenshots of low-fidelity screens, not descriptions of
  them.
- Behaviour as scenarios. "When the user presses Save on an empty title, the
  field turns red and nothing is saved." The same scenarios become the contract
  tests in S4 and the end-to-end tests in S7, so the owner reads the tests
  without reading code.
- The owner's own words. The glossary fixed in S2 uses the terms from the
  intake sentence; the agent may not rename the owner's concepts.

### Cause 3 — translationese

| Option | How | Trade-off |
| --- | --- | --- |
| A. Write Korean directly (used for gate sheets) | The human view is generated in Korean by a prompt with Korean style rules, not translated from English | Conflicts with `operator/english-progress` for documents written in this repository; gate sheets are screen text, which that rule already allows in Korean |
| B. English source, translation overlay | Keep the hub's current pipeline | The translationese the owner complains about comes from here |
| C. Both, with a Korean style pass | Translate, then a rewrite pass against a checklist | Two model calls per sheet |

A deterministic check backs it: a short list of
translationese patterns (passive "~되어진다", noun chains "~의 ~의 ~의",
"~에 대해", "~하는 것이다" endings, English word order with subject first) is
counted, and a sheet over the threshold is rewritten before it is shown.

The documents committed into the new project (README, design notes) follow the
docs-language choice in S1, so a Korean project can have Korean docs without
touching the hub's rule for its own repository.

### Measuring it

A sheet is readable if the owner acts on it. The app records per gate how long
the sheet was open, which items were edited, and which assumptions were later
found wrong after approval. A gate where nothing is ever edited and assumptions
later fail is a sheet nobody read.

## Feature split (S5)

This stage exists because of where the owner's frontend time went.

1. The agent lists every user-facing action from the built backend (each
   endpoint or command, named in the owner's words).
2. It proposes a class for each.

   | Class | Placement rule |
   | --- | --- |
   | Every time | On the primary screen, visible without a click |
   | Sometimes | One click away (a menu, a drawer, a secondary tab) |
   | Rarely | Settings or an admin area |
   | Never in the UI | API or CLI only |

3. The owner moves items between classes with options, and answers one more
   question: "what is the one thing the first screen is for?"
4. The map becomes a hard constraint on S6 and S7: a design or build that puts
   a "rarely" feature on the primary screen fails the stage's own check.

The owner answers on a drag board: four columns, one per class, with every
feature as a card placed in the agent's proposed column. Moving a card is the
answer; submitting the board ends S5. The board is new screen work (step 5).

## The frontend self-review loop (S7)

The owner wants the agent to look, fix, and look again. The loop below makes
"looked" mean something checkable.

1. Build against the R2 sheet.
2. Shoot every screen at each viewport with Playwright, plus every state
   (empty, loading, error, full).
3. Run the mechanical checks: console errors, horizontal overflow, elements
   outside the viewport, accessibility (axe), contrast, the end-to-end
   scenarios clicking through.
4. Critique from the screenshots in a fixed order (the `design-pass` order:
   purpose and layout, measured spacing and alignment, typography, palette,
   whitespace), plus the feature-split constraint.
5. Fix the highest-severity findings, then go to 2.

| Question | Options | Recommendation |
| --- | --- | --- |
| Who critiques | A separate read-only session, so the builder does not approve its own work | Decided |
| Stop rule | No high-severity findings / a fixed number of rounds / budget | No high-severity findings in two rounds in a row, capped at N rounds and the budget |
| What R3 receives | Final screenshots / before and after per round / a recorded walkthrough of each scenario | Final screenshots and one walkthrough per core scenario; round history folded |
| Palette | Agent picks / proposed at R2 for the owner to choose | Proposed at R2, since the owner is looking then anyway |

## Where it lives in the app

| Option | What | Trade-off |
| --- | --- | --- |
| A. A new mode in the app (used) | A `oneshot` run record with stage states, reusing specs, worktrees, the gate and the review loop | Most work, but resumes after restarts like the loop does |
| B. A planner-style phased server workflow | Like `plan-planner`: the server runs phases and validates blocks | Good fit for S2–S3, weak for long builds |
| C. A Claude Code skill only | A skill that walks the stages in one session | Quick to try; no resume, no gate enforcement, no screen |

### What the empty folder needs before a spec can exist

Today a spec requires the repository's adapter `gate_cmd`. An empty folder has
none, so S1 ends by creating the repository itself, after reserving its name
against existing folders, connection records, other runs and GitHub:

1. A new Git repository with `.gitignore`, `.gitattributes`, a README stub
   and `.wiki/adapter.toml` whose `gate_cmd` is built from the profile.
2. One base commit on `main`, so the first pull request has a base.
3. The GitHub repository, created through `gh` with the chosen visibility,
   and `main` pushed.
4. Everything after that happens in a linked worktree on one branch,
   `oneshot-initial`, which becomes the first pull request; the original
   checkout stays on `main`.

Each of these checks whether its effect already exists before acting, so a
restart repeats nothing ([step 2](2-bootstrap.md)).

### Run states

`intake → environment → spec → backend design → R1 → backend build → feature
split → frontend design → R2 → frontend build → R3 → pull request`, with
`stopped` reachable from anywhere carrying a reason, as specs do today.

## Decided with the owner

2026-11-09, by options.

| Question | Decision |
| --- | --- |
| Where it lives | The app mode directly (option A), no skill prototype |
| Mid-stretch questions | Decide and flag; stop and ask only for money, security, data deletion or an irreversible external action |
| Gate sheet language | Korean generated directly with Korean style rules and the translationese check; not translated from English |
| Frontend critic | A separate read-only session; the builder never approves its own screens |
| Feature split input | A drag board with four columns, prefilled with the agent's proposal |
| First pull request | The existing review loop runs on it before merge, started by the owner |
| Saved profile | One per kind of project (web app, CLI, data tool); S0's product type picks which one prefills S1 |

## Steps

| # | Stage | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Decisions | The owner's answers above | Done — 2026-11-09 |
| 2 | [Run record and bootstrap](2-bootstrap.md) | `oneshot` run states, S0–S1 with profiles per project kind, empty-folder repository creation with adapter and base commit | Not started |
| 3 | [Spec and backend](3-spec-backend.md) | S2–S4: scenarios and glossary, the R1 sheet in Korean with length and translationese checks, contract-test-first build with an independent read-only review | Not started |
| 4 | [Frontend](4-frontend.md) | S5–S7: frequency map, the R2 sheet, image input in the app's sessions, the screenshot, critique and fix loop with a separate critic and the stop rule | Not started |
| 5 | [Screen and pull request](5-screen-pr.md) | Oneshot entry, gate sheets with per-item approval, the drag board, S8 handing the pull request to Review Loop | Not started |
