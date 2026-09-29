# Reliability — implementation roadmap

Ten implementation PRs, plus this planning package if submitted separately.
The [audit](audit.md) records current evidence and limits at `7a6f45b`.
The linked stage documents now define implementation blueprints. Proposed APIs
and fields are distinguished from existing entry points; product code is unchanged.

## Agreed design

Jev chooses allowed transitions; code owns permissions, budgets and execution.
Keep one repository, existing SQLite/search, deterministic hooks and one loop engine
with different plan/code review criteria. Avoid routine LLM redecision of Jev routing.

An explicit Plan action starts configurable planner, reviser and reviewer roles.
Planner A researches and creates only a new plan folder. The implementation model
revises documents; an independent reviewer checks them. Planning and recovery
require submitted time/call/token limits before starting.

Recurring P0/P1 or oscillation requires research before the next fix; round 7 is
the backstop. Allow two research-and-fix cycles per issue, then pause. Research and
fix share one PR. Existing host web tools provide research; missing capability
stops visibly.

Run affected tests per repair and the full gate on final reviewed HEAD. Freeze a
new English baseline before Korean changes. Preserve accepted English gate v3.
Korean fixtures permit no required negation/literal loss; paired recall/coverage
use a -5 percentage-point lower confidence bound, as selected by the owner.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | [Quiet processes](1-processes.md) | Trace and correct flashing child windows | Done |
| 2 | [Test burden](2-tests.md) | Profile/refactor and bind final gates to HEAD | Done — loop/spec runtime target missed |
| 3 | [Wiki scopes](3-wiki-scopes.md) | Documentation authorities and retrieval filtering | Done |
| 4 | [Jev contract](4-jev-contract.md) | Routing, call accounting, versions and graph health | Done |
| 5 | [English reliability](5-english-baseline.md) | Frozen current-path evaluation and baseline | Not started |
| 6 | [Review profiles](6-review-profiles.md) | Rubrics, stable findings and independent roles | Not started |
| 7 | [Planner](7-planner.md) | Bounded artifacts, persisted workflow, PR and handoff | Not started |
| 8 | [Recovery](8-recovery.md) | Recurrence barrier, research and two-cycle escalation | Not started |
| 9 | [Korean comparison](9-korean.md) | Isolate input/evidence translation effects | Not started |
| 10 | [Live acceptance](10-acceptance.md) | Desktop/host scenarios and original-plan evidence | Not started |

Merge order: 1–10. Dependencies: 4 after 3; 5 after 4; 6 after 2–3;
7 after 5–6; 8 after 7; 9 after 5; 10 after all. No automatic implementation or
merge follows plan approval. Existing active-mode settings remain unchanged.

## Research adopted

| Source | Adopted fragment | Application / limit |
| --- | --- | --- |
| R1–R2 | Profile collection/calls and control plugin loading | PR 2; preserve required plugins |
| R3 | Suppress background Windows consoles at process creation | PR 1; exclude PTYs and incompatible detached flags |
| R4, R7 | Closed candidates and confidence/fallback policy | PR 4; confidence still needs evaluation |
| R5 | Separate generation, revision and evaluation | PRs 6–8; reuse current runtime |
| R6 | Retrieve connected source evidence | PRs 4–5; graph size is not a quality measure |

Unfinished [loop](../loop/0-overview.md) and [Jev](../jev/0-overview.md) criteria
remain open until PR 10 verifies them. SessionStart shows two open plans at
most, and these two series sit behind this one; missing from that list does
not mean finished.

## Sources

- R1: [pytest profiling](https://docs.pytest.org/en/stable/how-to/usage.html).
- R2: [pytest plugins](https://pytest.org/en/stable/how-to/plugins.html).
- R3: [Python subprocess](https://docs.python.org/3/library/subprocess.html).
- R4: [TypeSafe router](https://github.com/TypeSafeAI/typesafe-router).
- R5: [Workflow patterns](https://docs.langchain.com/oss/python/langgraph/workflows-agents).
- R6: [GraphRAG local search](https://microsoft.github.io/graphrag/query/local_search/).
- R7: [Confidence routing](https://docs.typesafe.ai/patterns/confidence-routing).
