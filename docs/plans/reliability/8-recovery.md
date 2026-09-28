# PR 8 — research before another nonconverging fix

When serious findings repeat or alternate, stop the normal fix dispatch and
research the cause before editing again.

## Work

Persist finding identity, affected invariant/component, occurrence and disposition.
Initial trigger proposal: the same P0/P1 in two consecutive valid rounds; an A-B-A
reappearance across three rounds; or a refused round 7 that has not already had
recovery. Exclude stale results, dismissed findings with evidence, and deferred P2.
Record the evidence for a suspected match; do not equate identical counts with
identical issues. PR 6's identity contract is authoritative.

On trigger, code removes ordinary fix from the allowed candidates. Jev chooses
among legal research/context/clarification transitions; it cannot bypass required
research. Existing implementation session searches host web tools before patching,
or stops visibly if search is unavailable. Research tools retain existing execution
permissions and budgets.

Preserve an evidence page in the current fix worktree using existing adoption
fields, extended only where needed: problem and failed hypotheses, core theory,
why it applies, exact source locators, interpretation versus source facts,
alternatives, counterevidence, constraints, proposed experiment, observed result,
and remaining uncertainty. Keep original quotations/identifiers as provenance;
summaries must not erase exceptions. Link it from the review disposition and plan.

The fix and research page enter the same PR. Draft research is not automatically
a shared rule or verified fact. Review both the reasoning and implementation before
adoption. Reuse source records/page rendering rather than creating another research
database or automatically opening a promotion PR.

## Limits and exit

At most two research-and-fix cycles per recurring issue. Persist counts across
refresh/restart; renamed headlines do not reset them. If unresolved, pause with
evidence and options. Overall time/token/cost and round limits still apply; reaching
a lower run cap pauses earlier. Failed research never silently falls through to fix.

Check repeated issue, oscillation, improving distinct findings, round 7, missing
search, stale rounds, restart and budget exhaustion. Show ordering evidence that
research precedes the next edit and the wiki page is included in the fix PR.

## Implementation blueprint

### Persisted issue and recovery state

PR 6 owns finding identities. Add `recovery` to each issue, not one counter shared
by every issue:

```json
{
  "issue_id": "<server-id>", "phase": "required",
  "trigger": {"kind": "repeat", "rounds": [3, 4], "heads": ["...", "..."]},
  "cycles_started": 0, "active_cycle": null,
  "limits": {"seconds": 0, "calls": 0, "tokens": 0},
  "attempts": []
}
```

A cycle attempt records source manifest, research artifact path/hash, hypothesis,
proposed experiment, fix HEAD, following review outcome and spend. Require positive
finite limits before research starts, as the owner selected. If absent, stop for
input; do not select arbitrary defaults. The effective allowance is the minimum
of submitted research limits and remaining enclosing run limits.

### Trigger algorithm

Operate only on counted, non-stale rounds with validated issue identity.

1. An unresolved P0/P1 on the same ID in two consecutive valid rounds triggers.
2. The same ID on rounds n-2 and n, with a purported fix/resolution and another
   serious issue in between, triggers oscillation.
3. A refusal at round 7 or later with serious findings and no applicable completed
   recovery triggers the backstop. An unrelated new issue is evaluated separately.
4. Deferred P2, evidence-accepted dismissal and stale rounds do not trigger.
5. Uncertain identity does not silently merge findings. Pause for identity
   clarification or use the round backstop; keep the evidence.

The server decides that research is mandatory. Jev selects legal preparation
steps within that requirement; it never re-enables direct fix as an alternative.
The two-cycle cap remains per issue across restarts and headline changes.

### Wiring and dispatch barrier

| Location | Change |
| --- | --- |
| `loop.step` | After valid refused review, persist findings and evaluate trigger before calling fix_turn |
| `decisions.fix_offer/fix_turn` | Accept recovery-required state; remove direct fix and expose context/research/clarify candidates as available |
| `loop.told` / work dispatch | Refuse a normal correction turn while required research has not completed |
| `specs` storage | Atomically claim a cycle before dispatch so two workers cannot start it |
| Proposed `main/recovery.py` | Research phase orchestration and artifact validation; reuse PR 7's host source/artifact contracts |
| `loop.recover/proceed` | In-flight attempt becomes interrupted; explicit resume retains cycle count |
| Review UI | Show trigger evidence, cycle 1/2 or 2/2, research page and stopped alternatives |

The barrier is at the correction dispatch boundary, not merely in a prompt.
Mandatory research runs with read/search capabilities and no code editing. After
its output is validated and the evidence page is materialized, the ordinary write
session receives the experiment and findings. Do not force one runtime session to
switch permissions unsafely: role identity can be the same configured implementation
model while the research phase uses a separate bounded read-only turn.

### Research page and semantic preservation

Write `docs/research/review-<spec>-<issue>-<cycle>.md` in the existing worktree.
Use the existing adoption/page contract for source fields and these sections:
problem/invariant; failed fixes; sources; core theory; applicability; counterevidence;
alternatives; experiment; result; unresolved limits. Require claim-to-source IDs and
original source locators. A source summary must retain qualifying conditions and
counterexamples. Generated interpretation is explicitly marked as interpretation.

Before fixing, result is pending and adoption is draft. After the experiment,
append observed results and link to tests/commit. Stage it with the code fix, so
review evaluates both. A draft cannot become verified memory or a mandatory shared
rule automatically. This workflow does not call `knowledge.promote`, which would
create another worktree/PR.

### Failure and two-cycle handling

Increment cycles_started when a research dispatch is committed, not after it
succeeds; interrupted retries cannot reset the allowance. A resumed same attempt
continues only unfinished work against unchanged inputs and remaining budget.
A changed hypothesis/new research attempt consumes the next cycle.

If research is unavailable, malformed, empty or exhausted, stop with reason and
retain sources; never fall through to ordinary fix. After the first researched fix
is refused for the same issue, permit the second cycle. After the second refusal,
stop and present: revise goal/constraint, choose a documented alternative, or defer
the issue. Existing round cap may stop earlier; no automatic extra rounds.

### Acceptance scenarios

Use reviewer transcripts for A-A, A-B-A, A-B-C improving distinct issues, stale A,
dismissed A and round-7 new issue. Assert exact trigger round and issue ID. Intercept
the work dispatch to prove no edit-capable turn precedes the persisted research
artifact. Restart between claim/dispatch, between research/write, and after fix;
counts and artifacts remain coherent. Simulate two failures and assert a third
automatic cycle cannot start. Check the PR diff contains both evidence and fix.

## Steps

| # | Step | Status |
| --- | --- | --- |
| 1 | Detect recurrence and gate ordinary fix dispatch | Not started |
| 2 | Research and preserve evidence in the same PR | Not started |
| 3 | Verify two-cycle escalation and restart behavior | Not started |

## Sources

[Workflow routing and evaluator patterns](https://docs.langchain.com/oss/python/langgraph/workflows-agents).
The recurrence thresholds are this product's proposed policy, not externally
validated universal thresholds.
