---
scope: project
severity: preference
triggers: ["(?i)laya.*(retrieval|검색 10종|diverse|다양).*(pilot|파일럿)"]
reads: [laya/generate.py, laya/guided.py, laya/prompts/generate.md, docs/plans/laya/2-data.md]
slots: []
sources: []
links: [laya, laya-exp-guided-labelling, laya-insight-cold-index]
---

# Diverse retrieval pilot — 50 questions per kind for ten kinds, then guided labels, 2026-10-14

Part of [laya](laya.md). The owner approved a $3 cap for generation plus guided
labelling. The generator is the one merged in #146 (0417a60); guided labelling
follows [laya-exp-guided-labelling](laya-exp-guided-labelling.md).

## Generation

The command was `python laya/generate.py train --target 50 --teacher sonnet --max-turns 7`.
All eight corpus repositories were warmed first; see
[laya-insight-cold-index](laya-insight-cold-index.md).

- Seven teacher turns, $0.319 in total, about $0.046 a turn. The teacher was
  claude-sonnet-5-5.
- 53 queries were written. Every one was captured with a route request and a
  grade-evidence request.
- No query was lost to the near-duplicate gate (0), the other-split rule (0)
  or an unavailable capture.
- It stopped at `target`, with all ten kinds at 50 questions.
- Repositories: ai-coding-wiki 24 queries, wiki-agent 16, ai-generation 8,
  project-codeit-mid 5. Seven turns cannot reach all eight repositories; the
  full run spreads by the sqrt weighting.
- Content types: unanswered 12, answered 11, partial 11, bridging 10,
  false_premise 9.
- Intents: lookup 11, pasted 9, analysis 9, progress 9, general 8,
  compound 7.

Rows, questions and distinct queries per kind:

| Kind | Rows | Questions | Queries |
| --- | --- | --- | --- |
| route, analysis, progress, repair | 50 | 50 | 50 |
| useful, conflict, redirect | 50 | 50 | 25 |
| source, ask | 25 | 50 | 25 |
| coverage | 32 | 50 | 32 |

- The passage kinds (`useful`, `conflict`, `redirect`) and `source` and `ask`
  hit 50 questions from 25 queries, because of `CAP=2`.
- At the full target of 1,000, those kinds draw from about 500 queries, while
  the one-per-query kinds need 1,000. So the run's length is set by `route`,
  `analysis`, `progress` and `repair`.

## Guided labelling

Command for each kind: `python laya/guided.py <kind> div1 --rows train.candidates.jsonl --labeller haiku --verifier codex:gpt-6.1-sol --rounds 3 --per-round 17 --max-usd 0.25`.
The kinds ran in two parallel streams of five.

All ten finished `done` with no failed turns, 22:13–22:28. The Haiku turns cost
$0.288 in all; the verifier's turns record no cost. Every guide stayed at
version 0:

- Only `redirect` and `coverage` reached 5 rejections in a round, so only they
  tried a lesson.
- `redirect`: the candidate was 0.941 → 0.824, a gain of −0.12.
- `coverage`: the candidate was 0.24 → 0.36, a gain of +0.12 against a floor
  of 0.17.
- Neither candidate was adopted.

Per kind: the labeller's answers over every question asked (both guides in a
trial round), then the kept labels:

| Kind | Kept / 50 | Labeller said | Kept labels | Cost $ |
| --- | --- | --- | --- | --- |
| route | 43 | yes 43, no 7 | true 43 | 0.015 |
| source | 30 | yes 34, no 16 | true 23, false 7 | 0.008 |
| useful | 33 | yes 32, no 18 | true 18, false 15 | 0.020 |
| conflict | 46 | no 44, yes 6 | false 44, true 2 | 0.021 |
| redirect | 39 | no 60, yes 7 | false 36, true 3 | 0.028 |
| coverage | 24 | no 40, yes 35 | true 19, false 5 | 0.075 |
| ask | 48 | yes 42, no 8 | true 40, false 8 | 0.011 |
| analysis | 40 | no 29, yes 21 | false 29, true 11 | 0.017 |
| progress | 46 | no 42, yes 8 | false 38, true 8 | 0.015 |
| repair | 15 | defer 26, context 16, subqueries 8 | defer 13, subqueries 1, context 1 | 0.077 |

## Insights

- Most kinds keep a lot. `ask`, `conflict`, `progress`, `route`, `analysis`
  and `redirect` keep 78–96%.
- The kept minority class is the problem, not the keep rate.
  - `route`: the labeller said no 7 times, and none of those were kept. Every
    teacher query is a repository question, so `route` gets no "does not need
    retrieval" state at all. The generator needs greetings and rewrites that
    `current_state` already answers.
  - `redirect` (3 true) and `conflict` (2 true) are rare by nature. At 1,000
    labels that is only about 75 and 40 positives. Planted instruction
    passages and more false-premise slots would raise them.
  - `coverage`: the labeller split about evenly, but the verifier rejected most
    "no" answers, so the kept labels lean true 19:5.
- `repair` loses to confidence, not to the verifier.
  - 30 of its 50 labeller answers were under `AGREE` (0.7), most at 0.6, and
    the verifier rejected only 1.
  - "Choose defer when none clearly is" invites a hedge. The kept labels are
    13 of 15 defer.
  - The question wording or criteria need work before a full run. A guide
    cannot help, because no lesson is written from `unsure` answers, only
    from rejections.
- `source` loses 17 of 50 to confidence for the same reason, but its labels
  are balanced enough (23:7).
- Rounds of 17 are too small to show a guide helping. Only 2 of 10 kinds
  tried a lesson, and the noise floor at 17 states is 0.16–0.17.

Total spent on this pilot: $0.319 generation + $0.288 labels = $0.607 of the
$3 cap.

## Second pilot — after the balance change (#147, 8284bbf), 2026-10-15

The owner chose to strengthen the generator and run the pilot again before
the full run. The first pilot's train candidates were moved aside to
`<kind>/pilot1-train.candidates.jsonl`, so `have` started at 0.

- `repair`'s wording was left alone. It is a Jev prompt, and changing it
  would invalidate the fitted Jev policy, so it got a Sonnet labeller instead.
- After the PC restarted, the search daemon's first start took over 120 s,
  so the warm-up script's first ask returned False. Once it answered, all
  eight indexes were complete within 34 s (they had been built before the
  restart).

Generation used the same command with `--max-turns 8`:

- Seven turns, $0.402 in all, $0.042–0.073 a turn. Turns cost more now that
  they also write `plants`.
- 55 queries. Three were the unrun leftovers of a natural turn from pilot 1
  (seed 6), which train may use.
- Nothing was lost to the near-duplicate gate, the other-split rule or an
  unavailable capture, and it stopped at `target`.
- Repositories: ai-companion-voice-agent 31, ai-coding-wiki 21,
  project-codeit-mid 3. That is the seed draw clustering; seeds 9–12 all
  drew the same repository.
- Types: false_premise 18 (33%, against 17% in pilot 1), answered 12,
  partial 10, bridging 9, unanswered 6.
- Intents: pasted 11, compound 11, analysis 9, general 9, progress 6,
  lookup 6, chat 3. `chat` came up only 3 times by the draw.
- 50 of 55 queries name the pages they are about (`about`).
- `redirect`: 15 of its 50 rows carry a plant (30%).

Labelling used split `div2`:

- Nine kinds with Haiku at a $0.12 cap each.
- `repair` with `--labeller sonnet` at a $0.8 cap.
- Same verifier, rounds of 17.

| Kind | Kept / 50 (pilot 1) | Kept labels (pilot 1) | Cost $ |
| --- | --- | --- | --- |
| route | 44 (43) | true 41, false 3 (43, 0) | 0.015 |
| source | 30 (30) | true 19, false 11 (23, 7) | 0.009 |
| useful | 41 (33) | false 24, true 17 (15, 18) | 0.021 |
| conflict | 47 (46) | false 39, true 8 (44, 2) | 0.022 |
| redirect | 49 (39) | false 33, true 16 (36, 3) | 0.019 |
| coverage | 33 (24) | true 24, false 9 (19, 5) | 0.066 |
| ask | 47 (48) | true 38, false 9 (40, 8) | 0.011 |
| analysis | 38 (40) | false 25, true 13 (29, 11) | 0.024 |
| progress | 48 (46) | false 40, true 8 (38, 8) | 0.015 |
| repair (Sonnet) | 10, stopped at the cap after round 0 (15) | defer 8, context 1, subqueries 1 (13, 1, 1) | 0.980 |

## Insights from the second pilot

- The rare yes came up where the generator put it.
  - `conflict` true rose from 2 to 8 of the kept labels (4% → 17%).
  - `redirect` true rose from 3 to 16. All 15 planted passages were kept as
    yes, one natural passage was kept as yes, and all 33 unplanted kept
    labels were no.
  - `route` false rose from 0 to 3. That is only as many as the 3 `chat`
    queries the slot draw gave. With 7 intents doubled and 8 drawn, a turn
    expects about 1.1 `chat`; this run drew fewer.
- Planted passages are easy cases: labeller and verifier agreed on all 15.
  Whether a model trained on them catches subtler instructions is a question
  for training, not for labels.
- A Sonnet labeller does not fix `repair`.
  - It kept 10 of 17 in round 0, where Haiku kept 5 of 17. It was still
    unsure on 7, and 8 of its 10 labels are defer.
  - It cost $0.028 a turn, 13× Haiku, and $0.98 for 35 turns.
  - The question itself ("choose defer when none clearly is") makes defer
    the hedge. This needs a design decision, not a stronger labeller.
- Guides were tried three times and none was adopted. Every candidate kept
  fewer than the empty guide:
  - `coverage`: 0.619 → 0.524
  - `analysis`: 0.938 → 0.688
  - pilot 1 `redirect`: 0.941 → 0.824
  At rounds of 17 a lesson is written from 5–6 rejections and overfits them.
- Spent: pilot 1 $0.607, pilot 2 generation $0.402 and labels $1.20 ($0.98
  of it `repair` on Sonnet), about $2.21 of the $3 cap in all.
- The owner then approved the full run for the nine kinds other than
  `repair`, capped at $22 (2026-10-15).
- `max_usd` can be overshot by the turns already in flight: up to
  `LABELLERS` (8) concurrent calls start before the cap is seen. With Sonnet
  that was $0.18 over a $0.8 cap; with Haiku it is under a cent.
