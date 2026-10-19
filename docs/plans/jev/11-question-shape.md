# Stage 11 — ask Jev the way its documentation says it works

See the [overview](0-overview.md). After [stage 10](10-evaluation-rollout.md).

Purpose. Reshape the questions this repository sends Jev so that they stop
landing on the failure modes Jev's own model page lists, measured old against
new on the same labelled cases before anything changes in production (owner,
2026-10-19).

## Why

Jev's model page
([jev-1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13),
reviewed 2026-10-02) lists nine failure modes, each with a fix. Read against
our questions (`tool/main/knowledge.py` `PROMPTS`, `ASK`, `ANALYSIS`;
`.wiki/laya-insight-jev-usage.md`):

| Weak spot (the page's fix) | Our questions |
| --- | --- |
| Large state with irrelevant detail (filter in code; send only what the question needs) | One judge request carries useful, conflict and redirect for every passage, plus coverage and repair: a median of 28 questions (max 81) over one state of about 15,000 characters and 13 passages. |
| Indirection, multi-hop (name the relevant parts of state) | "passage {id}", "requirement {id}", "the passages listed in complete_passages": every question points into the state by id. coverage asks about up to 12 passages taken together. |
| Literal reading, implied conditions (state the exact condition) | conflict: "contradict a premise or assumption of the query itself", with a negated exception clause. source: "Could source … help", a vague condition. |
| Contradictions, double negatives (align the wording) | analysis stacks "is not analysis … Nor is …"; ask is one "rather than … even when" clause. |
| Choice option order (reorder, confirm) | repair's options keep one fixed order, defer last. |

The fitted policy (`eval/jev/policy.json`, 89 calibration cases) shows where
it costs:

- conflict: 49 of 151 negatives uncertain, 2 of 8 positives said no.
- source: 113 of 192 uncertain.
- route, useful, coverage, redirect: almost all right. On the labelled
  coverage gate states (Laya stage 6), Jev scored 0.967, finding 10 of 10
  covered and 192 of 199 not covered.

## Changes, one arm each

Each change is its own arm, so its effect is attributed to it alone.

| Arm | Change | Kinds |
| --- | --- | --- |
| A. View | A passage question goes in its own request, whose state holds only that passage, the query and the requirements. coverage gets only its listed passages. | useful, conflict, redirect, coverage |
| B. Direct | The requirement's or passage's own words in the question instead of its id. conflict is split into literal questions combined in code. source names the concrete condition. | coverage, conflict, source |
| C. Order | repair is asked again with its options in the other order, in the same judge request; an answer that changes with the order defers. | repair |
| D. Plain | ask and analysis are rewritten without stacked negations, keeping their meaning. | ask, analysis |

Each arm's wording is reviewed before any paid call. Answers stay read by
`decision.policy`. A kind's prompt digest changes, so it is refitted, never
reused.

## Measurement

- Paired, old against new, on the same cases, one arm at a time:
  - the labelled calibration split (`eval/jev/calibration.json`, 89 cases);
  - the labelled coverage gate states (`laya/data/coverage/gate.jsonl`),
    one state per reserved unit: the 180 questions Laya stage 6 evaluates.
    There coverage is asked alone, so B's gate numbers carry part of A's
    change and are a diagnostic;
  - for conflict and source, their labelled cases in the calibration split;
  - for ask and analysis, which the calibration split does not label, the
    v7 intents' calibration split (`eval/jev/reliability-v8/intents.json`,
    72 intents).
- The old side is already recorded (`eval/jev/calibration.scores.json`;
  the gate's from Langfuse), so only the new arm is paid. The new arm is
  asked in exactly the recorded requests but for its own change: the route
  gained analysis and progress questions after the scores were collected,
  so B's route leaves them out; C asks the whole recorded judge request
  again with only the repair's options flipped, and combines that with the
  recorded answer (the mean over every option either order named). Only the
  arm's own kinds replace recorded answers, so a resampled answer to an
  unchanged question is not credited to it (PR #182 review). Ask and
  analysis were never asked alone, so D asks both wordings, in the same
  reduced route request.
- Each kind is fitted on both sides with `tool/eval/policy.py`'s costs.
  Analysis skips the claim check only on a sure yes. Ask is costed an
  intent at a time as `Flow.route` reads its parts: beside a sure ask an
  uncertain part is dropped as material, and a dropped question costs 10, a
  kept piece of material 1.
- Tool: `tool/eval/arms.py <arm>`, `--collect` to ask, `--answers` to
  compare a saved record again; records under `raw/eval/jev/`. Each answer
  goes to the arm's journal as it arrives: a stop or failure keeps what was
  paid for, a rerun asks only the rest, and an incomplete record is never
  compared.
- An arm is adopted when its policy cost on the calibration split falls and
  no kind's cost rises beyond one case. Then the policy is refitted on the
  adopted questions.
- Stage 10's held-out gates (`eval/jev/gates.json`) run once on the adopted
  set and must still pass. A failing gate rolls the arm back.

## Cost and approval

Jev's price is unknown, so the allowance is in Jev tokens.

- One refit collection: 172 requests, 126,572 input tokens.
- Arm A multiplies requests by the passage count while keeping input tokens
  about level.
- Allowance: at most 1,000,000 Jev tokens for all four arms, plus one
  held-out gate run (about 750,000 tokens, USD 13 of host spend in stage 10).
- Each paid run needs the owner's approval.
- Estimate, from request sizes at the recorded 0.23 tokens per character:
  A 242 requests, about 67,000 input tokens; B 172, about 114,000; C 29,
  about 19,000; D both wordings 144, about 32,000; the gate's coverage,
  A about 360,000 and B about 445,000 at most. Order: the calibration
  arms, then A on the gate, then B on the gate only with what remains of
  the allowance.
- One journal holds every arm's spending, failed requests too, and
  `--tokens` caps the stage's total, 900,000 by default. A request is sent
  only when its reservation fits in what is left:
  a token per byte of the body as sent, Jev's own instructions (at most 736
  tokens a request on the recorded side, if they are the same in every
  request; taken as 4,000 until measured) and twice the largest output per
  question seen (200 until measured). This is an estimate, not a guarantee:
  Jev takes no output limit and its tokenizer is not ours to check. A
  failed request is charged its reported usage (a non-negative count, as
  the transport requires), or its reservation once sent.
- A response past its reservation, answered or failed, or one sent with
  no usable count, halts the whole stage: no arm sends anything until the
  owner has seen the journal's spending and accepts that halt
  (`--accept-halts`), setting the stage's total again. `--tokens` is that
  cumulative total, never what remains.
- Runs go one at a time. With known counts, collection then passes
  900,000 by at most one response's excess, and the 100,000 left of the
  approved 1,000,000 covers it only if that one excess is under 100,000.
  A request sent with no usable count has a cost not yet known, so its
  reservation does not bound it. The client cannot promise either: a
  strict cap would need a spending limit on Jev's side. The owner accepted
  collection on these terms, 900,000 and a halt (2026-10-19).

## Results (2026-10-19)

Calibration split, 89 cases, policy cost old → new. 861,078 Jev tokens
spent of the 900,000 cap (817,107 input, 43,971 output), no halt. Records:
`raw/eval/jev/arms-<arm>-20261019T*.json`; the later view and direct
records hold the coverage gate runs.

| Arm | Cost | Verdict |
| --- | --- | --- |
| A view | 83.5 → 88.0 | rejected: useful 7 → 11, conflict 34.5 → 37.5, coverage 2 → 0; coverage gate 173/180 → 163/180 |
| B direct | 83.5 → 44.5 | adopted: source 37.5 → 20, conflict 34.5 → 14, coverage 2 → 1; no kind rose |
| C order | 5 → 6 | rejected: accepted right 16 → 11 |
| D plain | 0 → 0 | no difference: neither wording erred on the v7 intents |

B's gain is the uncertain band collapsing: conflict negatives uncertain
49 → 8, source negatives uncertain 75 → 40. Conflict's two positives said
no stay no. B's coverage gate run stopped at the cap and is incomplete, so
it is not compared. A shows Jev judges a passage better beside the others
than alone.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 1 | Wording for arms A–D, reviewed | Done — reviewed, PR #182 |
| 2 | Collect each arm on the calibration split and the coverage gate states; compare | Done — B adopted; A, C, D rejected (Results) |
| 3 | Refit the policy on the adopted arms; held-out gates | In progress — B's wording into production |
| 4 | Production switch through the existing modes, reversible per kind | Planned |
