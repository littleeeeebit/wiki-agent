# Audit — current evidence and gaps

Inspected at `7a6f45b` on 2026-09-28. This is a source and focused-test audit,
not a fresh certification of every plan criterion. No live paid evaluation,
desktop reproduction, or production graph snapshot was run.

## Original plans

| Plan | Evidence inspected | Assessment |
| --- | --- | --- |
| [English first](../done/english-first/0-overview.md) | Translation package, hook/main composition, historical mirror verification, current normalization and analysis notes | Core translation exists. Old mirror paths were superseded by the desktop program, not necessarily accidentally deleted. Historical completion does not establish current Korean/Jev equivalence. The overview's old session-plan scan description differs from current recursive scanning. |
| [wiki-agent](../done/wiki-agent/0-overview.md) | Public pipeline packages, main composition, agent stream adapters, Tauri launcher, historical step 7 window record | Substantial implementation and historical live evidence exist. Later loop/Jev changes require new live checks; the old 298-test record is not current verification. |
| [Jev](../jev/0-overview.md) | Typed decisions, Flow, grounding, action ownership, SQLite graph, evaluation/rollout record | Stages 1–9 have concrete implementation. Stage 10 is explicitly unfinished. Current analysis/input partition decisions are provisional and not covered by the recorded passing answer evaluation. |
| [loop](../loop/0-overview.md) | Specification, loop state machine, connection/survey modules, review prompt, focused loop tests | Review and connection machinery exists. Steps 4–6 still need live confirmation and step 7 is not started. Plan generation, distinct plan review, and mandatory research recovery are additional work. |

Read [Jev stage 10](../jev/10-evaluation-rollout.md) before changing thresholds.
The owner accepted gate version 3 after seeing results and enabled active mode for
all projects. These are recorded choices, not accidental settings to reverse.
The old English answer comparison reports coverage 0.892 versus 0.996, and no
observed unsupported published claims in arm D. The passing margin was selected
after results were seen, so a fresh holdout is needed for the new reliability claim.
Those numbers are historical results, not measurements repeated in this audit.

## Concrete findings

| Finding | Code evidence | Consequence and next stage |
| --- | --- | --- |
| Background descendants may create consoles | `web/src-tauri/src/main.rs:76` already hides the Python sidecar; `tool/session_state.py:39`, `tool/agent/chat_local.py:64`, and `tool/agent/chat_session.py:267` launch children without corresponding Windows suppression | Credible flicker candidates, not a confirmed root cause. Capture the process tree during an app question in PR 1. |
| Full repository gate can repeat per repair | `tool/main/loop.py:599` runs `specs.judge` when HEAD/gate changes; the documented adapter gate is `python -m pytest tool` | Implement the owner's targeted-round/final-full policy in PR 2, retaining merge binding. |
| Wiki scopes are only partly the requested division | `SCHEMA.md` separates operator/craft/project; `docs/hooks-setup.md`, `docs/development.md`, and Jev plans serve different audiences | Existing project isolation is valuable, but it does not itself express the three maintenance audiences. Add explicit navigation and retrieval scope in PR 3. |
| Jev is not a controller of host-internal tools | `tool/main/decisions.py` names work.start, specs.check, loop.fix, specs.candidates and explicitly excludes host tool choices | This is compatible with the chosen bounded router. Publish coverage and prevent duplicate routing in PR 4. |
| Nested retrieval can ask about retrieval again | `decisions.gathered:455` calls `knowledge.prepare` without `require=True`; Flow.route still judges retrieval need | A candidate for eliminating repeated retrieval-need judgment or enforcing the accepted action. Preserve source selection and evidence checks; trace it before editing. |
| Additional generation has separate purposes | `knowledge.Flow.split` decomposes questions; `query.drafting` generates; `query.ask` then calls `explain`; graph extraction proposes relationships before Jev validates support | Multiple calls exist, but not all are duplicate routing. No blanket removal is justified. Account for calls and any host-initiated duplicate search in PR 4. |
| Graph verification already exists | `search.knowledge_graph.verify`, `main.knowledge.check_graph`, and graph generation/deletion code | Checks include dangling edges, scope violations and unresolved spans. Current production graph health is unknown until a snapshot is checked. Reuse these functions. |
| Review recovery is local context, not the requested research cycle | `decisions.fix_offer:663` offers fix/context; `fix_turn:670` gathers local evidence; `prepare` defaults external off and its external option is arXiv | No mandatory general-web research or durable theory record is wired into this branch. Add PR 8. |
| Convergence tracking is insufficient for oscillation | `loop.shrinking:228` compares counts; `step:647` checks dispositions for repeated disagreement | Equal counts do not identify the same defect, and alternating defects can look productive. Persist issue identities and recurrence evidence. |
| Research promotion exists but is not recovery integration | `knowledge.page:2220` renders adoption data and `promote:2247` creates a research worktree/specification | Reuse the content contract; recovery should write into its existing fix worktree under the same PR, not invoke a second promotion worktree. |
| Plan review lacks a distinct rubric | `tool/prompts/review-round.md` judges existing code defects; one loop builds the instructions | Add a profile for implementability, completeness, sources, dependencies and testable acceptance in PR 6. |
| New decisions escape the older prompt digest | `knowledge.py:185–198` keeps ASK/ANALYSIS outside PROMPTS and documents provisional thresholds | Cache keys include question text, but evaluation versioning must also identify the complete behavior. Address in PRs 4–5. |

The existing page renderer already carries claims, scope, rationale,
counterevidence, validation conditions, and source metadata.

## Jev ownership

| Decision or operation | Current owner | Intended rule |
| --- | --- | --- |
| Whether/source families to retrieve; sufficiency and repair choice | Jev plus bounded Flow | Keep Jev as the semantic router |
| Legal transitions, budgets, candidate identity and execution permission | Code | Never outsource authority |
| Each graph edge visited | Bounded retrieval code | Jev chooses expansion strategy, not every edge |
| Rule injection before the host model | Hook matching | Always preserve required rules |
| Writing a plan, explanation, implementation or research synthesis | Generative model | No routine second selection of Jev's chosen transition |
| Tools inside a running Claude/Codex session | Host model/runtime | Explicitly outside current server control |
| Merge approval and final action | Review result plus server guards and human action | Jev cannot authorize merge |

## Checks performed

- `python -m pytest --collect-only -q tool`: 1,022 tests collected in 2.26 s.
- 43 top-level `tool/test_*.py` files contain 19,368 lines. Size alone does not
  establish redundancy.
- `python -m pytest -q tool/test_agent_decisions.py tool/test_knowledge_graph.py tool/test_loop.py tool/test_session_state.py --durations=8`:
  93 passed in 125.55 s, one Starlette/httpx deprecation warning.
- The slowest reported calls were loop scenarios: 7.00 s for the after-merge table,
  4.73 s for queued merge cleanup, and 4.65 s for loop concurrency.
- No full-suite runtime baseline, plugin A/B measurement, live API quality test,
  real host hook event, or current desktop/graph acceptance is claimed.

## Practical conclusion

The foundation is substantial; replacing the whole pipeline would discard working
contracts and regression coverage. The missing work is explicit ownership and
versioning, measured English reliability, role-specific document review, a real
planning workflow, and research recovery that preserves its evidence. Korean
differences should then be isolated against that frozen English baseline.
