# PR 2 — reduce test and gate burden

Reduce runtime, maintenance duplication, and repeated full-suite runs while
retaining meaningful behavior coverage.

## Work

Measure collection, fixtures and calls separately; record Python, pytest, plugins,
hardware and warm/cold conditions. Compare plugin autoload on/off with required
plugins explicitly preserved. Run the full timing baseline once at this stage,
not during every repair. The audit's 93-test sample took 125.55 s.

Classify expensive tests by contract, real Git/process integration, waiting, and
duplicate setup. Retain real integration witnesses for process and Git behavior.
Parameterize equivalent cases only when it reduces duplication; remove redundant
tests with a mapping to the surviving behavior check. Do not replace pytest or
mock away integration boundaries solely to make the count smaller.

Update the loop gate contract: affected checks run during repair; the final full
gate runs after review allows the exact commit, before merge becomes available.
Store targeted and full results separately. New commits invalidate the final
result. Shared-file changes include every consumer; uncertain impact or unexplained
failures widen checks. A failed final gate returns to repair and review.

Document the intentional change from the original loop's per-round repository
gate. Keep review approval and test approval bound to the same HEAD.

## Evidence and exit

Compare like-for-like runtime and maintenance diff, with retained contract coverage.
A focused loop check must reject merge when the final gate is missing, failed,
cancelled or attached to a different commit. Do not choose an arbitrary test-count
reduction target. Set the timing target after profiling and before refactoring.

## Scope and rollback

Keep pytest and current dependencies. Parallel test execution is a later option
only after shared state is isolated. Roll back gate scheduling independently from
safe test deduplication.

## Implementation blueprint

The performance change and gate behavior are separate measurements in one PR.
Keep the required full command in adapter `slots.gate_cmd`.

### Gate contract

Add a proposed `validation` object to specifications; `specs` is its only writer:

```json
{
  "version": 1,
  "round": {
    "head": "<oid>", "base_oid": "<oid>", "commands": ["<registered command>"],
    "selection": "mapped", "ok": true, "finished_at": 0
  },
  "final": null
}
```

A final result uses the same identity plus `command`, `environment_digest`,
`ok`, `code`, `reason` and `finished_at`. The digest includes the actual
registered command, Python/runtime version and available dependency-lock/config
hashes. Store no credentials. Do not claim the digest captures every machine
property; environmental changes known to the application invalidate reuse.

Keep legacy `gate` as the latest check display during migration, but never use a
targeted result as proof of final validation. Old specifications without
`validation.final` need a new final gate before merge. Do not retroactively treat
their latest result as final.

### Selection and execution

Use adapter `[checks]` records already read by `decisions.registered`; add optional
`paths` glob lists. Example proposed adapter shape:

```toml
[checks.loop]
cmd = "python -m pytest -q tool/test_loop.py tool/test_specs.py"
about = "Review state, publication and merge invariants"
paths = ["tool/main/loop.py", "tool/main/specs.py", "tool/prompts/review-*.md"]

[checks.agent]
cmd = "python -m pytest -q tool/test_agent.py tool/test_main.py"
about = "Agent lifecycle and application integration"
paths = ["tool/agent/**", "tool/main/work.py"]
```

Compute changed paths from merge base to HEAD, including both rename names and
deletions. Union all matching checks. Each changed production path must be covered;
an unmapped path, malformed map, missing merge base, shared configuration change,
or unexplained failure selects the full gate. Registered commands remain trusted
adapter input; Jev does not invent them or remove a required consumer check.
Documentation-only changes use registered document/link/lint checks when mapped.

After a valid passing result, reuse it only for the same identity. Do not run it
again merely because a screen refreshed. Optional Jev-selected checks remain
additive and cannot replace required mapping checks.

### Integration points and state transitions

| Owner | Required change |
| --- | --- |
| `specs._check` | For a new PR, run selected round checks, not unconditional full gate |
| `specs.judge` | Capture HEAD, base and clean status before AND after running; reject a command that changes files or HEAD |
| `loop.shipped` | Reuse or run round checks for changed HEAD, then push |
| `loop.step`, allow branch | Persist review allow, run full final gate before state becomes mergeable |
| `loop.merge` | Require current successful final result matching allowed HEAD/base/config, plus existing GitHub atomic match |
| `specs.view/summary`, web API/types and Review | Expose targeted versus final pending/pass/fail; never show merge enabled while pending |
| `loop.recover` | Interrupted final gate becomes incomplete; resume revalidates identity before retry |

Use a `validation.phase = "final_running"` substate while the existing review
state remains non-mergeable. On success set mergeable. On failure send gate findings
through repair, invalidate old review/final records for eligibility, then review the
new commit. Cancellation stops the child and retains an incomplete result, never
an `ok` result. Keep existing human Merge action.

### Profiling and refactoring procedure

Capture `python -m pytest --collect-only -q tool` and one full
`python -m pytest -q tool --durations=25` baseline. Inspect setup durations as
well as call durations. Compare plugin loading using
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` in a child environment only. Preserve required
plugins explicitly; do not modify the user's global shell.

Start with `test_loop.py` and its real Git setup. Move only immutable expensive
setup to broader fixtures. Each test gets its own mutable worktree/spec storage.
Replace sleeps used solely for synchronization with observable events; keep actual
timeout behavior tests. Consolidate repeated cases only when their assertions
exercise the same contract. Leave a table of removed cases and surviving witnesses.

### Acceptance matrix

Test: targeted pass cannot merge; final fail cannot merge; final pass on HEAD A
cannot merge B; gate-mutated worktree fails; restart during final run stays blocked;
unknown path runs full; shared path selects all consumers; same unchanged identity
reuses results. Use existing `test_specs.py`/`test_loop.py` rather than a new
test framework. Compare full elapsed time after the last change. A reduction in
collected count alone is not success.

Rollback can restore full checks each round without relaxing final merge guards.

## Result

Measured on 2026-09-29 at `b1de267`: Python 3.13.9 (Anaconda), pytest 8.3.5,
Windows 11 26200, Intel family 6 model 151 with 8 logical cores. Runs were warm,
from Git Bash, with `-p no:cacheprovider`.

### Profile

| Measurement | Result |
| --- | --- |
| `python -m pytest --collect-only -q tool` | 1,023 tests in 1.62 s |
| Same, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` in the child only | 1.37 s |
| Same, autoload off plus `-p anyio` | 1.32 s |
| `python -m pytest -q tool --durations=60` | 1,021 passed, 2 skipped in 551.28 s |
| `tool/test_loop.py` and `tool/test_specs.py`, `--durations=0` | 52 passed in 95.96 s; `test_loop` setup 20.1 s, call 65.0 s |

`anyio` is the only autoloaded plugin, and no test uses it. At 0.25 s, plugin
loading is not worth changing, so autoload stays on.

The slowest tests are real Git and process integration. `cProfile` of
`test_a_head_that_moved_while_it_was_read_throws_the_round_away` shows about 60
`subprocess.run` calls at 100–150 ms each. Two sources of those spawns were
test machinery rather than the behavior under test:

- The per-test `world` fixture ran eleven Git commands to build the same
  project and bare origin.
- The GitHub stand-in ran `git rev-parse` for every `gh pr view`.

| Kind | Examples | Decision |
| --- | --- | --- |
| Real Git/process integration | loop rounds, merge, adopt, prune; `test_worktrees`; `test_connect` | Kept as witnesses |
| Duplicate setup | `test_loop.world` | Build once per session as an immutable template, copy per test |
| Stand-in overhead | `Hub.head` | Read the loose ref; `rev-parse` stays the fallback |
| Waiting | seat wait, `test_agent`, `test_evidence`, `test_hook_diagnostics`, `test_keepalive`, `test_decision_flow` | Reviewed one by one. Sleeps that only synchronized became events; real waits were shortened where the bound under test allows it. Deadline, trickle, bounded-polling and negative-wait sleeps stay |
| Other hotspots | `test_hook_diagnostics`, `test_markdown_emphasis`, `test_evaluation`, `test_agent`, `test_main`, `test_wiki_health`, `test_codex_hooks`, `test_connect`, `test_translate_check` | Profiled and refactored; see the removal table |

Target, set before refactoring: `test_loop.py` plus `test_specs.py` at 72 s
or less (−25%), new acceptance tests included.

### Changes

The gate contract is implemented as the blueprint describes:

- `decisions.registered` reads `paths`.
- `specs.selected` picks round checks.
- `specs.judge` runs a list of commands and fails on a changed HEAD or worktree.
- `specs.validate` is the only writer of `validation`.
- `loop.finalized` runs the full gate on the allowed head.
- `specs.proven` binds `[머지]` to head, merge base and `specs.digest`.
  Every proof reads the merge base after fetching the base
  (`specs.current_merge_base`), so a base that moved onto the branch's own
  commits is seen; a base that cannot be fetched proves nothing.
- `loop.recover` clears `final_running`, and the Review tab shows targeted
  and final state. The tab takes merge eligibility from the server's
  `proven` (`unproven` in `specs.view`), never from the saved result alone.

`specs._check` and `knowledge.submitted` read the base locally (`local_base`),
so nothing reaches GitHub before the checks pass. A spec that was already
allowed but has no final result standing goes back to the loop, and only the
final gate runs; the review is not asked again.

The loop and spec consolidations:

| Change | Surviving witness |
| --- | --- |
| `world` builds the repository and origin per test | Session `template`, copied per test; `world` asserts the copy's origin is its own |
| `Hub.head` spawns `git rev-parse` | Loose ref read, `rev-parse` fallback; product Git calls unchanged |
| Four end-to-end merge refusals (drafted in this PR) | `test_only_a_passing_final_gate_on_the_same_identity_stands` plus one end-to-end refusal |

### Suite-wide cleanup

Every test file was then read for tests that no longer earn their place:
obsolete or dead checks, smoke tests, duplicates, asserts that cannot fail,
and setup repeated per test. An AST scan found no test without an assert and
no assert on a constant. Every removal below names the check that still covers
its behavior.

Removed tests:

| Test | Why | Surviving witness |
| --- | --- | --- |
| `test_main::test_the_draft_and_the_bare_worktree_are_gone` | Asserted that routes removed in loop stage 6 stay removed; nothing defines them | `test_work_opens_only_its_own_worktrees` |
| `test_main::test_map_words_go_but_identifiers_stay` | Pass-through smoke test repeating `translate.protect` | the `protect` tests in `test_translate` |
| `test_decision_flow` test of `knowledge.migrated` | Dead code with no production caller; removed with it | none needed |
| `test_sources::test_live_arxiv_paper_becomes_evidence` | Live smoke test behind `WIKI_LIVE_ARXIV=1` that asserted only "indexed" | `test_an_arxiv_abstract_is_never_taken_for_the_full_text`, `test_the_arxiv_feed_and_its_error_entries` |
| `test_markdown_emphasis::test_prose_outside_a_fence_is_still_counted` | Same input and assertion as the density test | `test_density_over_the_limit_is_refused` |
| `test_markdown_emphasis::test_other_tools_pass` | Merged | `test_only_a_markdown_write_is_judged` |
| `test_inject::test_with_no_budget_nothing_is_trimmed` | A whole hook subprocess for one assert on the same build | `test_the_knowledge_budget_trims_only_the_decisions` |
| `test_inject::test_the_rendering_comes_before_the_rules` | Same ordering, asserted with a longer rendering | `test_every_rule_sentence_lands_inside_the_2kb_preview` |
| `test_inject::test_a_turn_under_the_ceiling_is_not_squeezed` | The first turn going out whole is already asserted | `test_the_second_turn_carries_the_rule_paragraph_whole_instead_of_the_page`, `test_the_rule_index_rides_only_on_a_turn_still_over_the_ceiling` |
| `test_inject::test_every_rule_already_seen_still_sends_each_rule_paragraph` | Duplicate paragraph assert; its header assert moved | `test_the_second_turn_carries_the_rule_paragraph_whole_instead_of_the_page` |
| `test_knowledge_graph::test_a_newer_date_alone_supersedes_nothing` | The exact edge set already excludes it | `test_structure_has_exactly_the_expected_endpoints_and_every_span_resolves` |
| `test_agent_decisions::test_the_baseline_correction_goes_when_jev_is_unavailable` | A full review loop for what three tests cover | `test_a_doubt_or_an_outage_runs_the_baseline_and_says_why[unavailable]`, `test_a_refused_round_sends_the_context_jev_chose_to_gather_with_the_findings`, the refusal tests in `test_loop` |
| `test_session_state::test_an_english_plan_reads_the_same` | Its statuses overlapped; `Done` and `In progress` moved over | `test_english_plan_statuses_and_discovery` |
| `test_trajectory::test_the_stream_does_not_go_into_git` | "The entry is there" is implied by "exactly one entry" | `test_the_stream_stays_out_of_git_on_one_ignore_line` |
| `test_repo_lint`: `test_a_preference_may_have_no_triggers`, `test_a_project_scope_page_belongs_in_the_repository`, `test_the_hubs_findings_are_not_mixed_in` | Each negative is implied by a clean repository having no finding at all; the reasons moved into its comment | `test_a_clean_repository_says_nothing` |

Rewritten tests and asserts:

| Change | Why | Now |
| --- | --- | --- |
| `test_inject::test_the_rendering_goes_out_even_when_no_rule_matched` called `rendering()` alone | Could not catch the `if not parts: return 0` bug its docstring names | Runs through `inject.main` with a prompt that matches nothing |
| `test_hook`: four `if not shutil.which(...): return` | A missing host passed silently as green | `needs()` skips with the reason, before the subprocess |
| `test_main::test_a_delete_takes_the_rows_by_place_and_an_append_waits_for_it` | Its `keep: memory` reset started the real host CLI for a summary nothing asserted on | `main.memory.oneshot` stubbed as its neighbours do. A suite-wide spy on process starts found no other test reaching a real `claude` or `codex` |
| `test_decision_flow`: `assert out["budget"] if "budget" in out else True` | Always true | The asserts around it |
| `test_decision_flow`, `test_grounded_answer`: an assert implied by the exact assert above it | Cannot fail independently | The exact assert |
| `test_retrieval`: `== [] or all(...)` in the repair test | Vacuous when empty | `test_adjacent_sections_come_back_for_missing_context` |
| `test_harvest`: six stray Korean string statements after the docstrings | Dead code left by the English-first translation | The one incident only the Korean named moved into its docstring |
| `if __name__ == "__main__"` runners in seven files; `test_local_adapter`'s exited 1 | Duplicated pytest collection | pytest |

Setup and waiting:

| Change | Why | Now |
| --- | --- | --- |
| `test_main._repo`: six Git processes per call, about 40 callers across files | Duplicate setup | Built once per process, copied per call |
| `test_connect.original`: bare origin, push and a second clone per test | Duplicate setup | Session `remote` template, copied per test; `original` asserts the copies point at their own origin |
| `test_translate_check`: five Git commands per test for the same committed page | Duplicate setup | Module `committed` repository, copied per test |
| `test_codex_hooks`: three `apply.py` runs per test for 11 tests that only read hook answers | Duplicate setup | Module fixture `installed`; `connected` stays per test for the two that write |
| `test_evaluation`: the report test recorded the run the previous test had just recorded | Duplicate setup | Module fixture `ran` |
| `test_evaluation`: two refused connects at about 2 s each of Windows SYN retries | The outage reason is the contract, not the retries | `Pinned.connect` raises the same `ConnectionRefusedError` at once, 5.4 s → 2.7 s |
| `test_evidence`, `test_decision_flow`: a sleep before a concurrent delete or an abort | Synchronization only | An event set inside the paused read; the server accepts and reads the first byte |
| `test_keepalive`: the real 3 s `SPAWN_WAIT` in the stale-state budget test | The wait adds equally to both sides of the bound | The real constants are asserted against the 5 s bound first, then `SPAWN_WAIT = 0.3`, 3.3 s → 0.7 s |
| `test_hook_diagnostics`: the installed pretool waited out the real 8 s watchdog | Only the delay is shortened; the real entry point and threshold are still exercised, and the header must still say 8 | 8.1 s → 0.3 s |
| `test_agent`: the `slow` resume stand-in slept 30 s, so `close()` spent its 5 s grace | The stand-in blocks on stdin instead and still never answers | 6 s → 1.05 s |
| `test_markdown_emphasis`: the `--repo` test linted the real hub | An empty throwaway hub isolates the path | 7.5 s → 0.17 s |
| `apply.declared` re-parsed every page's YAML on every call | A page is re-parsed only when its mtime or size changes | `test_wiki_health::test_wiring` 5.5 s → 2.4 s, and every `apply --check` in the gates |

Gate coverage: `test_apply`, `test_lint` and `test_declared_continuation` were
script-only `main()` suites, so `python -m pytest tool` collected nothing from
them. They are pytest cases now, every check kept (16, 53 and 23 cases). The
six `python tool/test_*.py` lines in `docs/development.md` that ran cases a
second time are gone.

### Comparison

| Measurement | Before | After the gate work | After the cleanup |
| --- | --- | --- | --- |
| Full suite, same command | 1,021 passed, 2 skipped in 551.28 s | 1,029 passed in 434.44 s | 1,105 passed, 1 skipped in 380.93 s |
| The 44 unchanged loop/spec tests, summed | 94.8 s | 90.4 s in the full run, 84.6 s run alone | — |
| `test_loop` setup | 20.1 s | 4.8 s | — |
| Loop/spec files, new tests included | 95.96 s | 105.3 s | 102.74 s |

The loop/spec target was missed. Setup time fell by three quarters. But each
allow now also runs the final gate, and the new acceptance tests add about
20 s. The rest is product Git spawns inside the loop thread, which are the
integration under test. Cutting it further means fewer Git calls in
`main/loop.py`, not a test change. The loop tests' remaining sleeps are the
bounded 50 ms polls in `waited` and one negative wait.

The whole suite runs 31% faster while collecting 83 more cases (1,106 against 1,023). The extra
cases come from the three script-only suites the gate now runs. One run-to-run
comparison is noisy: an intermediate full run took 487 s. The per-test
reductions in the tables above were each measured alone.

The burden this PR removes is in the loop, not the suite. A repair round runs
only the checks its changed paths map to, not the whole `gate_cmd` (about
seven to nine minutes here). The whole gate runs once, on the allowed head.
That needs `paths` in the repository's own `.wiki/adapter.toml`, which is not
committed.

### Acceptance

| Case | Test |
| --- | --- |
| Targeted pass cannot merge | `test_a_targeted_pass_cannot_merge_and_the_loop_runs_only_the_final_gate` |
| Final fail, running, other head, other base or environment cannot merge | `test_only_a_passing_final_gate_on_the_same_identity_stands` |
| Failed final returns to repair and review | `test_a_failed_final_gate_goes_to_repair_and_a_new_review` |
| Gate-mutated worktree fails | `test_a_gate_that_changes_what_it_checked_fails_whatever_it_exits_with` |
| Restart during the final run stays blocked | `test_a_restart_during_the_final_gate_stays_blocked_and_resume_reruns_only_it` |
| Unknown path, shared file, malformed map or no merge base runs full; a shared source selects every consumer; renames and deletions count | `test_round_checks_follow_the_changed_paths_and_widen_when_unsure` |
| Same unchanged identity reuses results | `test_a_mapped_round_then_the_full_gate_once_and_the_same_identity_reuses_both` |

### Follow-up: pure shadow-audit setup

On 2026-10-08, 20 parameterized cases in `test_review_contract.py` created
temporary Git PRs even though their assertions only exercise composition,
frozen-request replay, provenance and non-authoritative candidate identity.
Seven also started an HTTP server. The measured baseline was 94.38 s, including
260 Git subprocess calls taking 87.65 s in total. Before changing setup, the
target was zero subprocess launches for these cases, without removing a case,
parameter or logical assertion.

The cases now live in `test_review_audit.py`, using synthetic spec/head/catalog
inputs and the real selection/composition functions. A subprocess guard fails
if this unit group starts a process. Existing audit-record and provenance helpers
are reused. The same 20 cases passed in 0.42 s with zero subprocess calls;
AST comparison preserved their assertions and parametrization, apart from the
fixture path arguments. Full collection remains 1,757 cases. These are warm,
same-host PowerShell runs with the same tracing plugin and `-p no:cacheprovider`,
Python 3.13.9, pytest 8.3.5, Windows 11 build 26200 and an i7-12700F reporting
eight logical processors. Plugin autoload was unchanged; no xdist workers were
requested. Concurrent host workload was not controlled. This is a scoped setup
comparison, not a full-suite speed claim.

| Unit group, all cases retained | Integration witness retained in `test_review_contract.py` |
| --- | --- |
| Candidate closure, rejection and identity variants | `test_v2_multiple_grounds_stable_order_and_replay_do_not_mutate_baseline`; `test_mandatory_snapshot_changes_cannot_be_persisted` |
| Registered flow closure and assertion origins | `test_shadow_registered_flow_is_persisted_and_rendered_without_a_collector_or_extra_review_tools`; `test_local_api_receipts_are_not_substituted_by_offline_gate_and_stale_receipts_fail` |
| Malformed command/assertion/kind catalog | `test_offered_catalog_changes_are_audit_rejections_without_execution_effects`; `test_changed_candidate_manifest_rejects_only_audit_union_and_preserves_approval` |

Actual manifest tracking, collector receipts, persistence freshness and reviewer
dispatch remain real Git/process/HTTP integration tests. The affected contract,
routing and audit modules passed all 83 cases in 386.55 s. No production code,
test wait budget, final-gate identity or ratchet ceiling changes in this cleanup.
At that revision, the earlier failed full receipt and five inherited ratchet
violations remained blockers; scoped passes did not replace a successful final gate.

The published debt-cleanup commit `7e83d3a` was subsequently integrated into
this branch as `3498707`, without changing sibling checkouts or any ceiling.
The combined source passed the debt gate, 167 affected integration cases and
all four affected browser scripts. The synchronization script retained a
Windows connection-reset warning during shutdown. Collection is now 1,758:
the preserved cases plus the debt cleanup's fixture-startup regression case.
Independent review and the final full gate are still required for this combined
revision; the earlier unsuccessful full receipts remain historical evidence.

### Follow-up: fresh runtime Git reads

The reviewed `95233d5` full gate subsequently completed with 1,735 passed,
19 failed, four skipped and exit 1. The debt gate passed. These failures remain
blocking; neither source approval nor a focused pass replaces that receipt.

One real external-owner API loop was traced without changing its 30 s wait.
Its first loop step made 12 manifest-tracking queries taking 3.42 s, across
six validations. The failed step stopped during verification, so this is not
a complete successful-drive profile. Before refactoring, the bounded target
was one fresh index query per manifest validation and no prompt-only fetch
when the round already supplies an immutable merge-base/head pair.

Manifest validation now reads a NUL-delimited index snapshot once per call,
retaining literal-path membership, file/scope validation and budget checks.
It never reuses that snapshot across selection, publication, execution or
merge boundaries. Checkout proof also combines clean-state and HEAD checks
in one fresh branch-aware status query, instead of two processes per call.
Prompt statistics use supplied commit IDs; callers without a merge-base retain
the fetching fallback. Remote-base freshness, collector execution, cancellation,
final-gate identity and merge guards are not removed or cached.

The real-Git regression checks verify one query per manifest/proof, spaced
and Korean paths, literal brackets, removed index entries, outside paths,
staged/untracked source changes, moved and detached HEADs, rename records,
missing status identity and command failure. Existing deadline/cancellation
tests now bind to the single index read and still require interruption.
Collection is 1,760 cases: the previous 1,758 plus two new regression cases.
No wait, dependency, ratchet ceiling or sibling checkout changes were made.

Both repair probes passed the two new checks but the external-owner loop
still timed out: the first probe was two passed/one failed in 50.48 s, and
the checkout-batching probe was two passed/one failed in 86.00 s; both naturally
exited 1. Git call counts are reduced, but uncontrolled host timings and
different stop phases do not demonstrate an end-to-end speed improvement.
The affected safety selection naturally exited 1 with 40 passed, two failed
and 41 deselected in 382.99 s. The unchanged moved-base shadow case observed
`exhausted` rather than reaching its expected `stale` result; the native
read-only handoff case exceeded the unchanged 30 s loop wait. Passing
deadline/cancellation and mandatory-snapshot checks do not erase those two
failures or establish live acceptance. Ruff, wiki lint and the unchanged
debt gate passed. The final full gate has not been retried. Independent
review and a successful current-head final receipt remain required.

After round 9, an additional real-Git witness showed that resolving contract
paths could accept an untracked directory alias to a tracked contract.
Membership now uses lexical path normalization, not symlink resolution;
`within()` still checks actual filesystem containment separately. The new
witness failed before this one-line correction. The affected manifest/index
selection then passed nine cases in 88.29 s, with natural exit 0 and 53
deselected. It adds one case, so current collection is 1,761. This correctness
correction invalidates round-9 source
approval for the new head; the timing and safety failures above remain open.

Round 10 found a P1 in that normalization: collapsing `alias/../api.md`
can compare a tracked lexical path with a different in-repository file after
directory-link traversal. The Windows real-Git witness accepted this ambiguous
declaration before repair; it does not establish the POSIX-specific target
substitution as a Windows runtime observation. Membership now uses
`Path(ref).as_posix()`, which retains `..` instead of collapsing it. Such a
reference cannot match an index entry; `./` remains supported. The same alias
witness now covers both direct and parent-traversal references, adding one
parameter case for a total collection of 1,762. Timing blockers remain open.
The ten-case manifest/checkout/prompt selection passed in 29.50 s, natural
exit 0, with 53 deselected. That selection does not rerun the timed-out loop
or the failed native/shadow acceptance cases, and is not a speed comparison.

The parser follows Git's documented
[porcelain v2 branch headers and NUL-delimited records](https://git-scm.com/docs/git-status#_porcelain_format_version_2).

### Follow-up: membership reads and shadow fixture preparation

Full subprocess attribution on `3a665fb` exposed work missed by the earlier
`specs.sh`-only trace. One external API probe failed: its partial loop step
took 40.37 s, including 57 real Git processes taking 15.80 s. The test reported
one failure in 153.27 s and eventually exited 1 after process shutdown.
An eight-worker full suite and another focused suite were concurrently active
on the host; neither was changed. This is not a controlled timing comparison.

A second diagnostic added phase attribution without changing commands or waits.
The external API case passed in 43.71 s and naturally exited 0. Its completed
step took 27.43 s, with 83 real Git processes taking 25.41 s. Redaction and
runtime digest work each totalled below 0.04 s; they are not optimization targets.
The pass does not erase the preceding failure or establish timing stability.

Before the membership repair, the bounded target was two fresh Git reads per
path-only listing: checkout identity and the registered worktree list. Loop
membership and PR attachment do not consume the listing's dirty/merged fields,
so they now omit those expensive status and commit-tree calculations. Default
UI and deletion callers retain all fields and their existing safety checks.
The existing real-Git worktree test checks a dirty task's unchanged default
listing and requires exactly two reads for membership, rejecting any status,
merge-base or tree query there. No registration, checkout, collector, final-gate
or merge identity check is cached or removed.

The moved-base shadow witness also prepared a competing clone and commit inside
the fake provider's real 15 s decision budget. That setup now precedes the
observation; the actual push remains inside the provider reply, after the input
snapshot. Its merge-base, changed-tip, persisted stale-result and no-dispatch
assertions remain unchanged. The scoped repair passed in 15.79 s with natural
exit 0. At that revision production decision limits and every test wait were
unchanged. These results are scoped diagnostics, not a full final receipt.

The combined worktree/API/native/shadow selection then exited 1 with 18 passed
and one failed in 219.43 s. Both ordinary API owners passed. Native acceptance
reached handoff, but its wrong-focus negative found a save event before the
later focus rejection. The fixture created its competing window inside the
focus callback, after exposing the main target. A startup/reentrant-focus race
is the repair hypothesis, not a demonstrated Windows API defect. The competing
window is now created and shown before the main target; its focus callback
redirects to that prepared window. The collector, focus guards, real event and
unchanged no-event assertion remain. Microsoft's
[SetFocus contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setfocus)
documents focus notifications; [AttachThreadInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-attachthreadinput)
shares the two threads' input state. Neither source proves the observed race's
exact interleaving. The first fixture-repair run reported one pass and one
failure in 66.46 s and naturally exited 1: it hit the earlier 30 s loop wait,
so it did not reach or validate the repaired negative.

The normal completion waits now share a bounded 120 s synchronization ceiling.
This intentionally revises the earlier no-wait-change constraint: a completed
API step already spends 25.41 s in real Git, and the earlier failed diagnostic
showed additional host-dependent latency. These tests assert final state,
ownership and identity, not a 30 s product performance guarantee. Explicit
timeout/negative waits and production budgets remain unchanged. Planning
completion reuses the same helper instead of its ten-second poll-count loop;
the browser's completion assertions and paused fake reviewer use that ceiling
too. Existing 60 s loop overrides use the shared default. No case, logical
assertion or expected result is removed. Increased synchronization tolerance is
not reported as a runtime optimization or stability proof. A new affected
selection and independent review remain required before the final full gate.

That serial selection finished with 41 passed, one existing Starlette warning,
392.40 s and natural exit 0. It includes all 19 previous full-suite failures,
actual Chromium and Win32 acceptance (including wrong-focus/no-event), both
alias regressions, registration/adoption ownership, targeted/final/restart/reuse
guards and the unchanged production deadline/cancellation witnesses. The earlier
17 worktree checks also passed in the 18-pass/one-failure selection above; the
path-only two-read guard is one of them. Current collection remains 1,762.
Ruff, wiki lint, strict UTF-8-without-BOM and the unchanged debt gate pass.
These focused working-tree receipts do not establish a full-suite pass, overall
speed improvement, or independent approval for this new revision.

## Steps

| # | Step | Deliverable | Status |
| --- | --- | --- | --- |
| 1 | Profile | Profile and map redundant/expensive checks | Done |
| 2 | Refactor | Refactor measured hotspots and final-gate scheduling | Done |
| 3 | Compare | Compare runtime and verify merge binding | Done — merge binding verified; full suite 551 s → 381 s; the 72 s loop/spec target was missed (102.74 s), see Comparison |

## Sources

[pytest duration profiling](https://docs.pytest.org/en/stable/how-to/usage.html)
and [plugin loading](https://pytest.org/en/stable/how-to/plugins.html).
