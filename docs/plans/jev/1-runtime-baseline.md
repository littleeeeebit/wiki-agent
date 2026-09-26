# Stage 1 — runtime configuration and baseline

See the [overall design](0-overview.md). There is no preceding stage.

Purpose. Use the key already registered in `.env` to verify real Jev requests,
give the app and CLI consistent settings, and freeze a reproducible baseline
before changing retrieval quality.

## Current mismatch

`search/controller.py` reads `TYPESAFE_API_KEY` from `os.environ` only.
`translate.setting()` separately reads the hub's `.env`, with file values
taking priority, including an explicit empty value. Importing translate does
not populate process variables. Registration alone therefore does not establish
that the prototype can authenticate.

## Configuration contract

Move the minimal shared reader into `tool/common/settings.py`, preserving
translation behavior. Search must not import translate's private helper.

| Setting | Contract |
| --- | --- |
| File | Hub `.env` by default; `JEV_ENV` selects an isolated test or batch file |
| Precedence | Explicit file entry, process environment, default; an empty file value wins |
| Key | `TYPESAFE_API_KEY`; never copied into global process environment |
| Model | `WIKI_JEV_MODEL`, initially `jev-1.13.0`; record the responding model |
| Mode | `WIKI_JEV_MODE=off\|shadow\|active`; unspecified means shadow with a key, off without one |
| Compatibility | Existing `WIKI_JEV=on` means active unless the new mode is specified |
| Health | Distinguish disabled, configured, reachable, auth_failed, and unavailable |

Define whitespace, quotes, comments, duplicate entries, and empty-value behavior.
Never execute shell expressions or variable substitutions. Status exposes only
whether a key exists; neither fragments nor hashes of secrets enter logs.
Preserve `TRANSLATE_ENV` test isolation and its established precedence.

Create the minimal `tool/decision/` transport now, moving rather than duplicating
the prototype's request code. Add its public boundary to pipeline lint. Stage 6
extends this foundation with typed policy and workflow decisions.

## Live connection check

Add `tool/jev_probe.py` using the public decision transport. Send synthetic
English state with a simple Noul and Choice question; repository content is
unnecessary for connectivity checks.

Record model, HTTP error category, schema validity, usage, elapsed time, and
configuration-source category. Key absence, bad credentials, timeout, and quota
failure have distinct results. Live requests require `--live`; ordinary tests
never load the user's actual key or silently call a provider.

Verify both CLI and a newly started app process. A successful HTTP response
establishes connectivity, not general semantic accuracy.

## Baseline and evaluation artifacts

Create `tool/eval/` for runners and `eval/jev/` for versioned synthetic fixtures
and manifests. Keep private real-source snapshots and results under
`raw/eval/jev/`, excluded from distribution.

Record code revision, corpus snapshot hash, query ID, model versions, retrieval
options, tokens, and timing. Preserve current retrieval and answering results
before changing them. Comparing different answering models or budgets is not a
controlled retrieval comparison.

[Stage 10](10-evaluation-rollout.md) owns final dataset sizes, splits, and gates.
This stage supplies the smoke set, manifest schema, and first baseline run.

## Initial budget proposal

These are design defaults, not measured performance promises. Changing a value
changes the recorded policy version.

| Operation | Initial ceiling |
| --- | --- |
| Connectivity smoke | Two API calls and 15 seconds total |
| Added retrieval and decision work per question | 15 seconds, six Jev requests, 40 candidate chunks |
| Full answer including generation and verification | 60 seconds, with cancellation |
| One automated live evaluation | USD 10 equivalent API spend or 60 minutes, whichever comes first |

One request carries a shared deadline and remaining budget through all steps.
An unknown price is not zero: use token ceilings and label monetary cost unknown
until a dated provider price is available. Print estimated call and token counts
before a large run. Resuming an experiment preserves already-consumed allowance.

## Plan tooling compatibility

Session-start reporting must recognize English `Steps` and `Status` tables,
and skip `Complete` and `Cancelled` rows while retaining `Not completed`.
Preserve existing Korean plan compatibility.

`main/specs.py::row_done` and its completion instruction currently use only
the Korean marker. Extend them to preserve the source plan's language, accept
`Complete — PR #n` for this series, and bind completion to the correct row and PR.
Do not translate existing plans or mark any implementation complete as part of
this compatibility change.

## Files and order

1. Shared reader and transport; isolate all tests from real settings.
2. App and CLI configuration snapshots and lifecycle; no permanent stale key in a daemon.
3. Live probe, typed response validation, and distinct error results.
4. Baseline runner, source snapshot manifest, and initial recorded results.
5. English specification completion compatibility and regression checks.

## Failure and rollback

Unreadable configuration must not appear as configured. Off mode restores
baseline retrieval without changing translation configuration. Removing the
key does not make old non-secret evaluation results unreadable.

## Completion gate

- App and CLI authenticate using only the registered file configuration.
- Noul and Choice responses have verified shapes, model IDs, and usage.
- Missing/empty keys, 401, 429, timeout, and cancellation have passing checks.
- No secret appears in logs, status responses, snapshots, or subprocess arguments.
- Existing translation and search tests pass; baseline replay is reproducible.
- English and Korean plan completion is handled correctly.
- Existing repository lint failures are reproduced and distinguished from new
  failures; stage 10 resolves all release-gate failures.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Settings | Shared reader, file configuration, and mode compatibility | Not started |
| 2 | Transport | Shared decision boundary, live probe, and error categories | Not started |
| 3 | Baseline | Evaluation manifests and original retrieval results | Not started |
| 4 | Plan workflow | English completion markers and legacy compatibility | Not started |
| 5 | Verification | Matching app/CLI behavior and regression checks | Not started |
