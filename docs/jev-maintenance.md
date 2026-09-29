# Jev maintenance — decisions, graphs, diagnostics and rollback

For whoever keeps Jev working in this program. Why Jev is built as it is lives
in the [Jev plan](plans/jev/0-overview.md); this page says where each part is
now and how to check it, calibrate it and turn it back. Measured values stay in
the plan and the evaluation artifacts; this page links to them and copies none.

Jev chooses among moves code offers; code owns permissions, budgets and
execution. Nothing here changes that.

## Where the decisions are

| Decision | Code | What code keeps |
| --- | --- | --- |
| Retrieve or answer directly; which source families | `Flow.route` in `tool/main/knowledge.py` | The families offered, the direct-answer restrictions |
| Relevance and conflicts of found evidence | `Flow.judge` | The candidate ids, the allowance |
| Enough evidence, or which repair | `Flow.repaired` | `REPAIR_ORDER`, rounds (`retrieval.MAX_ROUNDS`), the call reserve |
| Claim support in an answer | `Grounding` in the same file, `tool/decision/claims.py` | Span checks, what is published |
| Agent actions (`work.start`, `specs.check`, `loop.fix`, …) | `tool/main/decisions.py` | `OPERATIONS`, authorization, staleness keys |

`GET /api/jev` reports the live settings and `decisions.coverage()`: the
operations Jev may choose among and the host-internal choices it does not see.

## Configuration

| Setting | Where | Effect |
| --- | --- | --- |
| `TYPESAFE_API_KEY` | The hub's `.env` | The key; never displayed or traced |
| `WIKI_JEV_MODE` | `.env`, overridden by the app's saved settings | `off`, `shadow` or `active` |
| `WIKI_JEV_MODEL` | `.env` | The Jev model |
| `WIKI_GRAPH_RETRIEVAL` | `.env` | `off` restores RRF alone |
| Mode, disabled source families, limits, canary checkouts | `raw/jev/settings.json`, written by the app's settings and `tool/eval/rollout.py` | Read by each new run; a run in flight keeps its own |
| `LANGFUSE_*` | `.env` | Where run traces go (`tool/main/tracing.py`) |

## Graphs and data revisions

Retrieval walks the evidence knowledge graph; runs move through the workflow
transition graph. The [architecture page](architecture.md#the-four-graphs)
tells the four graphs apart.

A run records what it ran under, so a result can be tied to its inputs:

- the store generation it read (`generation` on every retrieval result);
- prompt, policy, model and normalization versions (`dossier.versions`);
- the audience scope it was narrowed to, if any (`dossier.audiences`);
- the evaluation dataset versions in `eval/jev/*.json`.

## Diagnostics

| Question | Command | Sends |
| --- | --- | --- |
| Is Jev configured? | `python tool/jev_probe.py` | Nothing |
| Does it answer? | `python tool/jev_probe.py --live` | One synthetic request (paid) |
| Is the knowledge graph sound? | `python tool/relations.py [--project <repo>] check` | Nothing; exit 1 on a problem |
| What did one question do? | `python tool/jev_search.py "<question>" --project <repo>` | As the app's mode sends |
| What did a stored run do? | `python tool/jev_search.py --run <id>`, `--export <id>` | Nothing |
| Does the code still decide a recorded run the same way? | `python tool/jev_search.py --replay <tape>` | Nothing |

Each app question is also a Langfuse trace with every Jev request under it.

## Calibration and evaluation

The frozen datasets, gates, policy fit and the exact command order are in
[stage 10, reproduction](plans/jev/10-evaluation-rollout.md#reproduction). The
code is under `tool/eval/`. Read that stage's recorded choices before changing
a threshold: accepted gate versions are owner decisions, not accidents.

## Rollback

Each switch is independent and touches no document, index generation or trace.

| To undo | Do |
| --- | --- |
| Jev entirely | `python tool/eval/rollout.py off` |
| A canary or a saved mode | `python tool/eval/rollout.py follow` (back to `.env`) |
| The graph lane | `WIKI_GRAPH_RETRIEVAL=off` in `.env` |
| Extracted relationships | `python tool/relations.py [--project <repo>] retire` |
| An external source family | Disable it in the app's Jev settings |
| An audience scope | Send none: the screen's "모든 문서" |

`python tool/eval/rollout.py rehearse` rehearses an outage and rollback in a
scratch hub and sends nothing.
