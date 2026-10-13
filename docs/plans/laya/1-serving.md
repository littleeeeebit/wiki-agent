# Stage 1 — serving, per-kind provider and zero-shot baseline

See the [overview](0-overview.md). There is no preceding stage.

Purpose. Answer the current decision requests from a local Laya server with
no paid call, and measure whether the rest of the plan is worth paying for.

## Reserve the gate test first

Before anything here reads a labelled case, stage 2's gate test is reserved
(its "Gate test" section): the candidate real states are chosen, hashed and
written to `eval/laya/tests.json`, and nothing in stages 1–5 reads their
labels or answers. Every measurement below runs on the remaining cases, the
development pool.

Done 2026-10-13 by `laya/gate.py`: the 329 pooled states collapse to 288
units (word 5-shingle Jaccard ≥ 0.8), and two thirds of the units, 192, are
reserved with a fixed seed (owner: the pilot kind has only 185 real states,
so reserving all of them would leave no real development state). A unit is a
state, so the kinds asked of it stay on one side; the gate holds 104 units
asking `useful`, `conflict`, `redirect` and `coverage`.

That reservation compared whole states only. A passage view of a
development state can be a near-duplicate of a gate state's view while the
whole states are not, when the same query was asked again over different
passages: 28 development states are, all with the gate state's query. The
gate is not drawn again. `gate.development` leaves those states out, along
with any state in a unit (by whole state or by view) that holds a gate
state, so the development pool is 81 states. A training, validation or
calibration state is dropped by the same rule (stage 2).

## Contracts

Today `decision.decide` drops each question's kind before the transport
(`tool/decision/contract.py`, `questions = {name: q["question"]}`), the
request and cache key carry one `model` and one `policy_version`, the policy
artifact is bound to one model (`decision.policy`), and `decide`'s `spent()`
and `knowledge`'s tape read one trace entry per request. The contract keeps
those shapes and fills them as follows.

- Provider map. A setting `WIKI_JEV_PROVIDER` takes `typesafe` (default),
  `laya`, or a per-kind map (`route=laya,*=typesafe`). Laya is reached only
  at a local base URL (`http://127.0.0.1:<port>`); any other host is refused.
- Kinds reach the transport. `Evaluate` gains a keyword `kinds` (question
  name → decision kind) that `decide` passes from `req["questions"]`. A
  stand-in evaluator that ignores it still works.
- Decider identity. The request's `model` becomes the identity of the
  deciders for the kinds in that request: `jev-1.13.0` when they are all
  TypeSafe (unchanged), else `mix-<hash>` over the sorted
  `{kind: provider, checkpoint, version, temperature}` of those kinds only.
  The cache key already contains `model`, so changing one kind's checkpoint
  invalidates exactly the requests that ask that kind.
- Policy per kind. Each consumer keeps loading its own artifact with its
  own prompt and kind-version binding, as today: retrieval
  `eval/jev/policy.json`, claims `eval/jev/relation-policy.json`
  (`decision.claims`), actions `eval/jev/action-policy.json`
  (`main.decisions`). For a kind switched to Laya, that consumer's policy
  takes the kind's rule from `eval/laya/policy/<kind>.json` instead, bound
  to its checkpoint, version and temperature; without one the kind is
  provisional, as today. With no kind on Laya, every consumer's policy,
  `policy_version` and verdicts are exactly today's. With some on Laya,
  `policy_version` is a hash over today's version and the Laya kinds' rule
  sources. A consumer's policy covers all its kinds, so switching one kind
  also changes the `policy_version`, and so the cache keys, of that
  consumer's other requests; the cache is in memory and bounded, so this
  costs repeat calls, not correctness.
- One call per provider. Questions are grouped by provider, not by kind: all
  TypeSafe questions of a request stay one call, so with every kind on
  TypeSafe nothing changes. Laya questions are grouped by kind (one model
  each) and sent to the local server in one batch where the kinds share a
  checkpoint, otherwise one local request per kind.
- Budget. A logical request still counts one call against `QUESTION`'s six.
  Its groups run concurrently under the same deadline (`CALL_SECONDS`); a
  group that cannot finish inside it is not sent.
- One trace entry. `evaluate` writes one entry per logical request at
  `mark`, as today, with `model` the decider identity, `usage` the sum of the
  groups that were sent, `sent` true if any group was sent, and `groups`
  listing each group's kinds, provider, model, usage, sent and error. A group
  that fails fails the request with its `JevError` category, and the groups
  already sent keep their usage in the entry. A cancel aborts every in-flight
  group and sends none of the rest. Replay tapes keep the logical request's
  answers and the entry, unchanged in shape.
- Length. Each Laya kind has a token limit (its training `max_len`) for
  the state it sees plus that question's head, measured with Laya's
  tokenizer. The state is never cut or truncated: `current_state`,
  passage ids and headings define what a question means, so a cut state
  can make two different questions identical. The one reduction is the
  passage view (`laya/kinds.py`, `view`): a `useful`, `conflict` or
  `redirect` question names one passage and asks about it against the
  query, so its model sees the whole state with only that passage, the
  same view in labels, training and serving. A whole grade-evidence state
  is a median 4,300 tokens and past ModernBERT's 8,192 at p95 (measurement
  1); its view is a median 900 and at most 2,250 (owner, 2026-10-13). The
  `useful` question's "bridging fact that connects the query to other
  evidence" is then judged without the other passages; stage 6's gate
  measures what that costs.
- Overflow is per request, decided before the request is built. The
  consumer checks every Laya question's length when it chooses the
  request's `model` and policy. If any is over its limit and TypeSafe is
  configured, the whole request is built as an all-TypeSafe request: model
  `jev-1.13.0`, the consumer's existing TypeSafe policy and prompt binding,
  one TypeSafe call. The trace entry marks it `overflow`. So the model that
  answers and the rule that judges always come from the same request, and
  `decide`, the cache key, tapes and replays carry that pairing unchanged.
  Where TypeSafe is not configured, the request is built for Laya and fails
  with the existing `state_too_large` category, so `decide` returns
  `unavailable` and every owner takes its existing unavailable path
  (baseline retrieval, unverified claims, the action baseline). Every
  question is therefore answered with a valid answer or the request is
  unavailable; no new answer form is added. The overflow and unavailable
  rates per kind are reported.
- Judging with the request's policy. A consumer that judges answers again
  after `decide` (claims re-judge relations with `Flow.pol`) uses the
  policy of the request that produced them, kept with the result, never
  the flow's current one.
- Grouping with views. Questions of a passage kind have different views,
  so each is one Laya request (`predict` encodes one state per call); they
  run concurrently inside the group's deadline.
- Server. `laya-serve` takes a `model` per request and registers extra
  names with `LAYA_EXTRA_MODELS` (L2); stage 1 uses it if its LRU and
  preload settings hold the measured memory. Only if they do not, a small
  local server over `laya.load(...).predict` with the same shape replaces
  it. All fourteen checkpoints do not fit in 10 GB at once.
- Lifecycle. The app starts the server when a kind needs it, owns it,
  stops it on exit, and a crash is the `unavailable` category (no console
  window, reliability PR 1).

## Measurements (development pool only)

1. Length. Every recorded request and development case, state plus question
   head with Laya's tokenizer, per kind: median, p95, max, and the share over
   1,024, 2,048 and 4,096.
2. Zero-shot accuracy. Base `convaiinnovations/laya` and
   `laya-typed-decisions` per kind, beside Jev's recorded answers.
   Synthetic sets are reported separately from real states.
3. Label swap. Every Noul and Choice question asked again with option
   labels reversed; the share of answers that follow the labels instead of
   the state (Laya issue #156).
4. Latency and memory. Per question, warm and after an LRU swap, with the
   longest measured state and four requests in flight, on the RTX 3080;
   peak GPU memory.
5. `option_layout="parallel"`'s requirements (transformers ≥ 5, eager
   backend) checked against the installed stack.

## Results, 2026-10-13

`laya/stage1.py`, laya 0.4.6 at the reviewed commit, torch 2.11 (CUDA
12.8), transformers 5.19, RTX 3080 with about 5 GB held by desktop apps.
Files in `raw/eval/laya/stage1-*-20261013T*.json`. The two checkpoints
share ModernBERT-large's tokenizer, so lengths are the same for both;
`base` was trained at `max_len` 512, `typed-decisions` at 1,024.

1. Length, tokens per question (state plus head, nothing cut):

   | Kinds | Questions | Median | p95 | Max | Over 4,096 |
   | --- | --- | --- | --- | --- | --- |
   | `useful`, `conflict`, `redirect` (whole state) | 1,521 each | 4,300 | 9,970 | 11,480 | 53% |
   | `useful`, `conflict`, `redirect` (passage view) | 1,521 each | 900 | 1,390 | 2,250 | 0% |
   | `coverage` | 391 | 4,071 | 7,625 | 11,472 | 48% |
   | `action` | 339 | 7,901 | 8,581 | 9,794 | 100% |
   | `answers`, `relation`, `repair`, `faithful` | 652 | 2,400–3,900 | 4,970–5,180 | 5,890 | 10–39% |
   | `route`, `source`, `analysis`, `ask`, `progress` | 586 | 650–800 | 1,070–1,630 | 2,300 | 0% |

   ModernBERT has 8,192 positions, so whole grade-evidence states past
   that could not be encoded at all. This is why the passage view was
   chosen. `coverage` and `action` stay on TypeSafe for now. The claim kinds
   are under 100 states anyway.

2. Zero-shot agreement with Jev's recorded answers, on the 81 development
   states and questions up to 2,304 tokens, each question whole (no
   `head_max_len` cut), noul by the side of 0.5. Agreement, not accuracy:
   neither side is a label.

   | Kind | n | `base` | `typed` |
   | --- | --- | --- | --- |
   | `useful` | 294 | 39% | 40% |
   | `conflict` | 294 | 90% | 99% |
   | `redirect` | 294 | 52% | 48% |
   | `route` | 26 | 65% | 69% |
   | `source` | 78 | 71% | 78% |
   | `analysis` | 26 | 69% | 62% |
   | `ask` | 46 | 50% | 63% |

   Jev says `useful` for 31% of passages; the unchanged checkpoints say it
   for most, so they disagree on most `useful` questions. `answers` agreed
   on 0 of 7; the other choice kinds had 1–2 questions under the limit.

3. Option order (Laya issue #156). The same questions with the options
   listed in reverse; the share whose answer changed:

   | Kind | `base` | `typed` |
   | --- | --- | --- |
   | `useful` | 4% | 13% |
   | `conflict` | 40% | 50% |
   | `redirect` | 14% | 27% |
   | `route`, `source`, `analysis`, `ask` | 0–20% | 6–23% |

   The unchanged models follow option position on a large share of
   `conflict` questions. Fine-tuning has to remove this; stage 4 measures
   the same share on its checkpoints.

4. Latency and memory, `typed`, on a filler state (no pooled state),
   warm, median of three:

   | Tokens | 1 question | 8 questions | Peak GPU |
   | --- | --- | --- | --- |
   | 504 | 0.04 s | 0.13 s | 2.3–2.4 GB |
   | 985 | 0.04 s | 0.25 s | 2.3–2.6 GB |
   | 2,168 | 0.09 s | 0.59 s | 2.4–3.0 GB |

   Loading a checkpoint from the local cache, which an LRU swap does, takes
   4.7 s. A first attempt at 8,192 tokens and 8 questions did not fit in
   10 GB. Laya then retried on the CPU and filled the RAM, so `latency` now
   stops at the first error or CPU fallback, and `swap` and `zeroshot` raise
   on one. Four requests in flight through `laya-serve` are measured with
   the server, in the provider PR.

5. `option_layout="parallel"` needs transformers ≥ 5 (installed: 5.19.0)
   and the eager backend. No published checkpoint uses it, so a fine-tune
   would opt in.

## Acceptance

- With every kind on `typesafe`, the full gate passes unchanged, requests
  send the same bodies, every consumer's policy version and verdicts are
  today's (the claim policy's relation threshold among them), and recorded
  replays are identical.
- With `route` on `laya`, a route request is answered locally, recorded,
  cached and replayed like a Jev one; changing route's checkpoint changes
  the decider identity only of requests that ask `route`.
- A judge request and a route request with one Laya question over its
  limit: the whole request goes to TypeSafe with the existing policy and is
  marked `overflow`; without TypeSafe it is `unavailable` with
  `state_too_large`.
- With `relation` on Laya and no Laya relation rule yet, an overflowed
  claim request whose TypeSafe answer is `supports` 0.7 (others 0.15 each)
  stays `uncertain` under the existing relation policy (threshold 0.9), in
  `decide`, in the claim re-judgment, on a cache hit and on replay.
- A mixed request where the second group fails keeps the first group's usage
  in the trace and the result's `call`.
- The measurements are written to `raw/eval/laya/stage1-<timestamp>.json`
  and summarized in the plan.
- The owner decides from them whether to pay for stage 2's generation. If
  the label swap dominates, swap latency or memory breaks the six-second call
  limit, or a kind's share over 4,096 tokens would leave it mostly on
  TypeSafe, the plan stops or drops that kind with that evidence.

## Tests

`tool/test_decision_provider.py`, no network: kinds passed to the
transport; all-TypeSafe bodies, traces, policy versions and verdicts
identical to today for retrieval, claims and actions; grouping by
provider; decider identity changing only for requests asking a kind whose
checkpoint changed; a consumer's policy taking a Laya kind's rule; one
`Budget.call()` per logical request whatever its groups, with failed and
cancelled groups' known usage kept and unknown usage not recorded as zero;
a mixed request with the second group failing; cancel mid-request; a cache
hit spending no call; replay of a mixed request with the same call and
token deltas; whole-request overflow to TypeSafe with its existing policy
and `state_too_large` without it; the overflowed relation case above
through `decide`, claim re-judgment, cache hit and replay; a non-local
Laya URL refused.

## Rollback

Set `WIKI_JEV_PROVIDER=typesafe` or remove it. The server is not started
when no kind uses it.
