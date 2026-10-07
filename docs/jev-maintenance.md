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
| Explicit claim-verification checks | `Grounding` in the same file, `tool/decision/claims.py` | Span checks, accepted claims; callers opt in with `verify_claims=True` |
| Agent actions (`work.start`, `specs.check`, `loop.fix`, …) | `tool/main/decisions.py` | `OPERATIONS`, authorization, staleness keys |

`GET /api/jev` reports the live settings and `decisions.coverage()`: every
decision point with its owner (Jev, code, a generative role or the host), the
operations it may choose among, the authority code keeps, its policy version,
and whether each Jev decision kind is fitted or still provisional. The host's
own tool choices are listed as not covered. Its `manifest` is the behavior
manifest below.

Retrieval an admitted action already chose is not judged again. When
`work.start` or `loop.fix` runs `retrieve_evidence`, `decisions.gathered`
passes the proposal as `prepare(cause=...)`: the route request leaves out
"retrieve or not", still asks sources, analysis and the question's parts, and
the transition's reason is `retrieval_required_by_action`. An answer's return
to retrieval (`require=True`) skips the same question.

## Answer publication

The three conversation focuses and `jev_search.py --answer` use the host's
ordinary synthesis over retrieved evidence. Retrieval coverage and the
analysis/fact classification are diagnostics, not publication permission.
The host may interpret plans, combine observations, recommend work, and read
additional repository records. Missing evidence requires a specific limit on
the conclusion; it does not require refusing the whole question.

The isolated CLI host exposes only Read, Glob and Grep. Before answering,
the server supplies bounded read-only observations of the checkout, the latest
20 commits across local refs, and up to 20 recent pull requests with merge state.
Each fixed command has a ten-second timeout and a 12,000-character output cap.
Git refs are not fetched; missing tools or unavailable GitHub leave a specific
observation gap without suppressing the answer. The snapshot is included in
the host brief and durable run summary, and is shown in the existing evidence
drawer. A bare export keeps its timestamp and removes its text using the same
redaction as other source snapshots. It grants no shell or write access.

`grounded` records this answer as `unverified`, never as independently checked.
Internal evidence marks are removed from the body; locators, revisions,
snapshots and unknown citation ids stay in "View evidence and judgment".
Spec blocks keep known evidence references and remain proposals. Choices and
retrospective candidates retain their interaction format. Empty host responses
and transport errors are failures, not successful answers. The explicit claim
validator remains available for checks; it does not gate normal conversation.

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
- the behavior digest (`dossier.versions.behavior`, `knowledge.behavior()`)
  over the retrieval prompts, `ASK`, `ANALYSIS`, the grounding prompts, the
  normalization version and graph extraction — a tape's replay says
  `behavior_changed`, unknown for a tape from before it;
- its call totals (`tracing.totals`) — a replay says `calls_match`, unknown
  for a tape from before them: each decision's tape entry keeps whether it
  was sent or a cache hit, a failed one included;
- the admitted action that required it, if any (`dossier.cause`);
- the evaluation dataset versions in `eval/jev/*.json`.

A fitted policy covers the prompts it was fitted on. `ASK` and `ANALYSIS`
lie outside `PROMPT_VERSION`, so each is bound to its own digest
(`knowledge.KIND_VERSIONS`): a rule for either counts as fitted only when the
artifact's `kind_versions` names the same digest. Neither is fitted today.

### Call records

Every operation that may cost something is a call record
(`tracing.call`, `call-record/1`): a Jev request, a translation, a question
split, an arXiv repair, a host drafting turn, an explanation. It names its
purposes (`tracing.PURPOSES`), owner, provider, model, a digest of what was
sent, tokens, duration, retry and outcome. A Jev request asking several
questions is one call with several purposes, counted once the transport has
written it (a failed one keeps the tokens it spent; one never sent — no key,
no slot, stopped first — is no call); a translation is one call per
request the translator actually sent (its outcomes' `request` id), none for a
text no request carried; an arXiv repair is the search and, apart, Jev's
grading of the papers; a drafting turn that failed or was stopped is still a
call. A cache hit has provider `cache` and costs nothing. A cost the provider does not report — Jev and the
translator today — is `null` with `cost_known: false`, never zero.

A question's records are `call` events of its run; the summary's `calls` adds
them up, the known cost apart from the count of unknown ones. An action's
retrieval keeps them on its outcome in `raw/actions/`. A host drafting turn
counts `host_searches`, its own search commands after the server retrieved:
the host's tools are traced, not controlled.

## Diagnostics

| Question | Command | Sends |
| --- | --- | --- |
| Is Jev configured? | `python tool/jev_probe.py` | Nothing |
| Does it answer? | `python tool/jev_probe.py --live` | One synthetic request (paid) |
| Is the knowledge graph sound? | `python tool/relations.py [--project <repo>] check` | Nothing; indexes first; exit 1 on a problem |
| Is the graph as indexed now sound, without rebuilding it? | `python tool/relations.py [--project <repo>] health`, `GET /api/knowledge/graph/health` | Nothing; exit 1 unless `healthy` |
| What did one question do? | `python tool/jev_search.py "<question>" --project <repo>` | As the app's mode sends |
| What did a stored run do? | `python tool/jev_search.py --run <id>`, `--export <id>` | Nothing |
| Does the code still decide a recorded run the same way? | `python tool/jev_search.py --replay <tape>` | Nothing |

Each app question is also a Langfuse trace with every Jev request under it.

`health` reads the index as it stands, through read-only connections that
create no file and take no write lock, and reports `{repo_id, generation,
checked_at, versions, counts, violations, status}`. `not_indexed`: no
published index, and none is built. `stale`: the files or the store moved on
from what the graph was built from, before or during the check (the files
are listed again last) — `reason` says which — and then a span
of a changed file may not resolve. `failing`: an invalid, dangling or
out-of-scope edge, or an unresolved span. `empty`: no edge; no edge is not
health. `healthy` is structural only: whether edges help a question is
measured by evaluation (`semantic_evaluation`, reliability PR 5).

## Calibration and evaluation

The frozen datasets, gates, policy fit and the exact command order are in
[stage 10, reproduction](plans/jev/10-evaluation-rollout.md#reproduction). The
code is under `tool/eval/`. Read that stage's recorded choices before changing
a threshold: accepted gate versions are owner decisions, not accidents.

For review selection research, see
[criteria, evidence and measured limits](research/review-routing-and-evidence.md).
`python tool/eval/review_routing.py` evaluates synthetic cases offline;
`--live` measures Jev recommendations without starting review or changing
product settings. Its datasets and durable measurements are under `eval/jev/`.
The experimental policy is provisional and is not a production rollout gate.
The first production increment stores observations under each spec's
`rounds[].review_contract.shadow`, beside the actual deterministic criteria and
required flows. Global active mode still leaves this decision shadow-only;
mode off disables observations without removing mandatory evidence checks.
Its prompt is different from the synthetic experiment and has no fitted active
policy. See the investigation's production section for collection limitations.

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
