# Stage 1 — determine required criteria and evidence

See the [overview](0-overview.md). Output feeds
[stage 2](2-jev-shadow.md) and [stage 3](3-composition-provenance.md).

Purpose. Establish a deterministic minimum before asking a model for advice.
Criteria describe the reviewer's questions; evidence describes the observations
needed to answer them. Neither set is an execution permission.

## Current completion boundary

The v1 baseline is implemented in
[`review_contract.py`](../../../tool/main/review_contract.py), integrated through
[`specs.py`](../../../tool/main/specs.py) and
[`loop.py`](../../../tool/main/loop.py). It derives obligations from explicit
specification declarations, the effective profile, refactoring ownership, actual
changed paths and registered flows. Its tests passed on 2026-10-07.

It does not semantically infer every undeclared API/native requirement from prose.
An ordinary local spec without runtime declarations does not automatically opt
into a manifest just because one exists. Contextual gap detection belongs to
stage 2; collected evidence belongs to stage 4. Do not claim that this baseline
guarantees completeness of a repository's manifest or authored acceptance.

## Inputs and trust boundaries

| Input | Source and validation |
| --- | --- |
| Spec | Existing spec owner; goal, acceptance, exclusions, grounds, revision, `review_profile`, `artifact_root`, optional `review` |
| Refactor origin | Existing run/step identity; frozen characterization specification belongs to the refactoring owner |
| Diff | Existing loop's actual reviewed head and merge base; unreadable paths are unknown, not an empty prose diff |
| Registered flows | Committed `verification.json`; existing strict manifest validation and unique flow/assertion IDs |
| Rubrics/catalog | Code-owned plan/code prompts and closed additional facet catalogs |

The current declaration schema is:

```json
{
  "review_profile": "code",
  "review": {
    "criteria": ["security", "async"],
    "evidence": ["api"],
    "flows": ["save-record"]
  }
}
```

This is a format example; `save-record` must exist in the target's manifest.
`review` accepts only `criteria`, `evidence` and `flows`, each a string list.
Unknown fields/facets and wrong types fail validation; duplicates are normalized.
Flow membership is checked against the current manifest during selection. No
model-authored shell command, tool or environment identifier belongs here.

Additional criteria are `refactor`, `documentation`, `security`, `data`, `async`
and `performance`. Base criteria are `plan` and `code`; `mixed` includes both.
Evidence facets are `offline`, `api`, `browser`, `desktop` and `differential`.
A `command` flow is a registered check, not a sixth runtime evidence facet.

## Deterministic selection rules

Apply these rules in order, accumulating obligations rather than choosing one
mutually exclusive task label.

| Rule | Condition | Required result |
| --- | --- | --- |
| F01 | Missing legacy profile | Retain `code` |
| F02 | Plan with a nonempty readable prose diff entirely under `artifact_root` | Retain `plan`; review future acceptance without claiming future execution |
| F03 | Plan leaves that root, touches executable Markdown, has unknown/empty impact, or explicitly requires preservation | Widen to `plan` + `code` |
| F04 | All changed files are ordinary prose, but profile is legacy code | Add `documentation`; keep `code` |
| F05 | Any accepted explicit criteria/evidence | Add them; never subtract inherited obligations |
| F06 | Ordinary refactor production step or explicit refactor/differential requirement | Require `code`, `refactor`, `differential` and owner-frozen preservation inputs |
| F07 | Characterization authoring or recognized cleanup/ratchet finishing step | Keep that owner's existing checks; do not require a new test to preserve itself |
| F08 | Any task | Require current-head round checks; retain the final full gate before merge |
| F09 | Cloud runtime path | Retain its complete major-flow catalog and existing approved prose exemption |
| F10 | Explicit local flow IDs | Resolve those IDs; add the selected flows' evidence kinds |
| F11 | API/browser requested without explicit flow IDs | Select impacted registered flows; widen to all on shared, unreadable or unmapped impact |
| F12 | Browser evidence under the current receipt schema | Also require actual API request observations |
| F13 | Selected evidence kind has no matching registered flow/validator | Record a preparation problem; do not substitute an offline pass |

Executable Markdown includes `AGENTS.md`, `CLAUDE.md`, `SKILL.md` and documents
under code/config/prompt/policy roots excluded by `prose()`. A Markdown suffix
alone is not a docs-only exemption. Repository-declared Cloud prose exemptions
must also exclude referenced contracts and runtime impact paths.

In v1, explicit local flow IDs are authoritative selections; diff expansion is
used when API/browser is declared without IDs. Do not describe v1 as widening
every explicit list automatically. Stage 3 must record that distinction; any
future stricter expansion is a versioned rule with new regression cases.

## Refactoring proof selection

Resolve the spec's refactor run and step before reading its frozen inputs.
Missing run, missing production step or absent frozen file is a preparation
failure. For a production step, compare every protected test path between the
step baseline and reviewed head with literal Git pathspecs. An empty test set,
changed protected test or failed comparison cannot prove preservation.

The round and final-gate command reruns the existing `refactor_profile preserve`
operation with the frozen specification's SHA-256. The command identity changes
when frozen inputs change; the executable rejects an obsolete digest before
dispatch. This prevents targeted-check reuse from hiding a missing preservation
check. Cleanup/test-strength and debt measurement still belong to the existing
finishing/refactor owners, not a second preservation engine.

## Output and interfaces

Current interfaces:

```text
profile_fields(repo, block) -> validated profile + optional declarations
effective(spec, changed_paths) -> plan | code | mixed
select(repo, checkout, spec, profile, changed_paths, head, base_oid) -> contract v1
```

The current contract records `version`, `head`, `base_oid`, `spec_signature`,
`profile`, sorted `criteria`/`evidence`, resolved `flows`, `manifest_digest`,
`frozen_digest`, `preservation`, `problems`, `rubric_digest`, `catalog_digest`
and `digest`. Per-item provenance is not already present; stage 3 adds it.
Selection performs no live verification or reviewer dispatch.

The spec signature binds the relevant task revision/content and origin. The
contract digest binds the selected obligations and rule inputs. Jev's later
shadow observation is outside that deterministic digest. A missing merge base
is rejected at readiness; an unknown flow ID is never silently ignored as valid.

## Verification and evidence

```powershell
python -m pytest -q tool/test_review_contract.py
```

Recorded on 2026-10-07: 14 passed, one dependency deprecation warning, 29.57 s.
This used the working tree based on `ecf04cc4156a1c98694b9897637cfccef600b97e`,
not a committed production release. The suite combines temporary Git fixtures,
controlled reviewer replies and a real isolated local HTTP API. It does not run
an actual independent reviewer or native desktop acceptance.

Covered boundaries include invalid declarations, plan/executable Markdown,
legacy prose, explicit runtime/host/preservation preparation failures, refactor
round/final checks, changed obligations, stale receipts, unchanged review tools,
shadow non-interference and legacy approval compatibility. Broader selection
regressions should add mapped versus shared/unmapped diff, renames/deletions,
multiple runtime dependencies and incomplete manifest counterexamples. Those
future checks must not be reported as having passed in this record.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Declare | Closed facets and registered flow references | Done |
| 2 | Select | Profile/diff/refactor/manifest-derived deterministic v1 floor | Done |
| 3 | Integrate | Round contract, preservation command and conservative preparation guards | Done |
| 4 | Verify baseline | Focused contract suite and recorded scope limits | Done — 14 passed |

## Compatibility and rollback

Old specs retain code defaults. Old ordinary approvals remain compatible only
when no new explicit obligations/refactor origin or changed effective profile
requires a fresh review. Do not retrofit fabricated grounds into archived rounds.
Rollback of new declarations requires an explicit spec revision; turning Jev
off cannot remove these deterministic floors or bypass an existing gate.
