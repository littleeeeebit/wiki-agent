# Stage 1 — serving, per-kind provider and zero-shot baseline

See the [overview](0-overview.md). There is no preceding stage.

Purpose. Answer the current decision requests from a local Laya server with
no paid call, and measure whether the rest of the plan is worth paying for.

## Contracts

- Provider per kind. A setting `WIKI_JEV_PROVIDER` takes `typesafe`,
  `laya`, or a per-kind map (`route=laya,*=typesafe`). The default stays
  `typesafe`, so nothing changes until a kind is switched.
  `tool/decision/__init__.py` keeps `HOST` for TypeSafe and adds the local
  base URL (`http://127.0.0.1:<port>`, never a remote host) for Laya.
- Request split. `evaluate` groups a request's questions by kind, sends each
  group to its provider and model, and joins the answers in question order.
  Usage adds up; the trace records each group's model. A group's failure
  fails the request with the existing `JevError` categories.
- Model per kind. The request body names `laya-<kind>-<version>`. Before
  stage 4 every kind names the base checkpoint.
- Server. Use `laya-serve` if its HTTP body accepts a model per request (L2
  confirms this only for its MCP tools). Otherwise add a small local server
  over `laya.load(...).predict` with the same `POST /v1/systemone` shape. It
  keeps an LRU of resident checkpoints on the GPU; the size is set from the
  measured memory, since all fourteen do not fit in 10 GB.
- The server starts and stops with the app like other owned processes
  (no console window, reliability PR 1).

## Measurements

1. Length. Every recorded request state under `raw/eval/jev` and the frozen
   evaluation sets, tokenized with Laya's tokenizer, per kind: median, p95,
   max. A kind whose p95 exceeds 1,024 is marked for longer training or a
   per-passage split in stage 4.
2. Zero-shot accuracy. Base `convaiinnovations/laya` and
   `laya-typed-decisions` on every existing labelled set: `calibration.json`,
   `reliability-v8`, `review-routing*.json`, `actions.json`, `faithful.json`,
   `relation.json`, `answers.json`. Per kind, beside Jev's recorded answers.
3. Label swap. Every Noul and Choice question asked again with option
   labels reversed; the share of answers that follow the labels instead of
   the state (Laya issue #156).
4. Latency. Per question, warm and after an LRU swap, on the RTX 3080.

## Acceptance

- With every kind on `typesafe`, the full gate passes unchanged and replays
  are identical.
- With `route` on `laya`, a route request is answered locally, recorded,
  cached and replayed like a Jev one.
- The four measurements are written to
  `raw/eval/laya/stage1-<timestamp>.json` and summarized in the plan.
- The owner decides from them whether to pay for stage 3. If the label swap
  dominates or swap latency breaks the per-question budget, the plan stops
  here with that evidence.

## Tests

`tool/test_decision_provider.py`, no network: per-kind routing, request
split and join order, a mixed request with one failing group, usage sum,
the default staying TypeSafe, a non-local Laya URL refused.

## Rollback

Set `WIKI_JEV_PROVIDER=typesafe` or remove it. The server is not started
when no kind uses it.
