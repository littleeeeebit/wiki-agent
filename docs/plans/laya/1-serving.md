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
- Policy per kind. A Laya kind's rules come from
  `eval/laya/policy/<kind>.json`, bound to its checkpoint and version; a
  TypeSafe kind's from `eval/jev/policy.json` as today. `decision.policy`
  composes them; `policy_version` is a hash over each kind's (model, rule
  source, rule). A kind without a fitted rule for its checkpoint is
  provisional, as today.
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
- Length. Each kind has a token limit for state plus question head, measured
  with Laya's tokenizer. Over the limit:

  | Kinds | Over-long state |
  | --- | --- |
  | `useful`, `conflict`, `redirect` (one passage each) | Asked with the query and that passage only; the answer keeps its meaning |
  | `coverage`, `relation`, `answers`, `faithful` (read passages or claims together) | No answer: the question is `uncertain`, which code already treats safely (coverage missing, claim unverified) |
  | `route`, `source`, `ask`, `analysis`, `progress`, `repair`, `action` | No answer: `uncertain`, so route retrieves, a source is searched, repair takes code's order, an action runs the baseline |

  No per-passage answers are combined into one; nothing is truncated
  silently. The share of over-long states per kind is reported.
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

## Acceptance

- With every kind on `typesafe`, the full gate passes unchanged, requests
  send the same bodies, and recorded replays are identical.
- With `route` on `laya`, a route request is answered locally, recorded,
  cached and replayed like a Jev one; changing route's checkpoint misses the
  cache for route requests only.
- A mixed request where the second group fails keeps the first group's usage
  in the trace and the result's `call`.
- The measurements are written to `raw/eval/laya/stage1-<timestamp>.json`
  and summarized in the plan.
- The owner decides from them whether to pay for stage 2's generation. If
  the label swap dominates, swap latency or memory breaks the six-second call
  limit, or a kind's over-long share makes it mostly `uncertain`, the plan
  stops or drops that kind with that evidence.

## Tests

`tool/test_decision_provider.py`, no network: kinds passed to the
transport; all-TypeSafe bodies and traces byte-identical to today; grouping
by provider; decider identity changing only for the kinds whose checkpoint
changed; composed policy version; a mixed request with the second group
failing; cancel mid-request; usage sum; replay of a mixed request; each
over-long rule in the table; a non-local Laya URL refused.

## Rollback

Set `WIKI_JEV_PROVIDER=typesafe` or remove it. The server is not started
when no kind uses it.
