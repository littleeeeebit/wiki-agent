# Phase 3 — `translate` Independence

The relationship between the overall design and the phases is [in Overview](0-overview.md)].

Goal. `translate` is called by a single contract — it receives a list of sentences, direction, and deadline, and returns a list of sentences of the same length. There are two budgets. The caller passes the deadline, and `translate` counts the monthly cost limit itself. The structure (#18·#19) where multiple features shared a budget ends here.

## Public Entry Points

`tool/translate/__init__.py`'s `__all__` is the public entry point.

| Name | What | Caller |
| --- | --- | --- |
| `translate(texts, direction, deadline)` | Translation. Returns original text if it fails | `inject`, `session_state`, `chat`, `mirror` |
| `usage()` | Usage and limit for this month | `python tool/translate --usage`, Phase 6 screen |
| `glossary()` | Glossary. Korean terms not to be translated | `english_progress` |
| `KO_EN`, `EN_KO` | Direction | All of the above |

- Remove default values from `direction` and `deadline`. Also remove the 60-second default for the deadline (`TIMEOUT`). If there is a default value, a caller that does not pass a deadline runs on someone else's deadline — #19 was like that. The 60 seconds is passed by the screen side (`chat`'s `/api/translate`, `mirror`) as its own constant.
- Remove `ko_to_en`·`en_to_ko`. `inject.rendering` calls `translate([prompt], KO_EN, deadline)[0]`. If there are three entry points doing the same thing, it is not a single public entry point.
- Keep `--check` and its tools (`protect`, `by_kind`, `baseline`, etc.) inside the package. Tests (`test_*.py`) are allowed to look inside.

### Inspection

`lint.pipeline_surface` checks this. If there is `__all__` in the pipeline's `__init__.py`, all names used by the `tool/` root module (excluding tests) in that pipeline must be within `__all__`.

- Look at all `T.x`, `from translate import x`, `from tool import translate` after `import translate as T`
- Sub-module imports (`import translate.x`, `from translate.x import y`) are immediate discoveries
- The type of discovery is `공개 진입점`

Pipelines without `__all__` (`wiki`, `agent`, `workspace`) are not checked yet. The same inspection will be applied as soon as phases 4 and 5 use `__all__`.

| What to plant in `test_lint.py` | Expectation |
| --- | --- |
| Root module `translate._ask(...)` after `import translate` | Red |
| Root module `T.protect` after `import translate as T` | Red |
| Root module `from translate import protect` | Red |
| Root module `from translate.x import y` | Red |
| Root module `translate.translate`, `from translate import KO_EN`, test file `translate._ask` | Green |

## Monthly Cost Limit

| What | How |
| --- | --- |
| Limit | `TRANSLATE_MONTHLY_USD` of `.env`, if not present, environment variable of the same name, if neither, $5. The reading order is the same as `GEMINI_API_KEY` |
| `0` | Do not send new requests. Cache responds as is |
| Unreadable value | Treated as `0`. Safe for the money side when the limit cannot be read. Non-numeric values, negative numbers, `nan`·`inf`, and empty values — if there is a line like a key, that line wins |
| Fee | `usageMetadata` of the response — input is `promptTokenCount`, output is the sum of `candidatesTokenCount` and `thoughtsTokenCount` — multiplied by the rate table. `gemini-3.1-flash-lite` input $0.25, output $1.50 (per 1 million tokens, 2026-09-24 ai.google.dev rate table) |
| Record first | Accumulate pre-deduction before sending the request. It is usually higher than the actual amount because it is the value of the number of bytes in the request body sent as the number of input/output tokens — not a guarantee since there is no output limit. If it cannot be used, do not send. The time spent waiting to use the pre-deduction is subtracted from the caller's deadline, and if there is no time left, it is reverted and not sent. Recording after the response causes the cost to be missing from the ledger if writing fails due to a lock (Review Round 1) |
| When response arrives | Settle with the difference between the actual fee and the pre-deduction. If the settlement write fails, the pre-deduction remains. If the body is cut off or corrupted after receiving the response, it is considered charged and the pre-deduction remains |
| Request timed out without response | The server may have processed it to the end and charged for it. Keep the pre-deduction as is |
| Connection failure, HTTP error | Not charged, so revert the pre-deduction |
| Month | Pre-deduction, settlement, and refund are used in the month of pre-deduction. So that requests past midnight do not leave negative numbers in the new month |
| Storage | `spend(month, usd)` table in the same sqlite file as the cache. Month is UTC `YYYY-MM` |
| Judgment | If this month's usage is above the limit before sending the request, do not send and return the original text |
| When cache cannot be opened or usage lookup fails | Cannot count, so do not request. Already found cache hits are returned as is |

The rate table is a constant next to `MODEL`. Change it together when changing the model.

Limit. If two processes request simultaneously just below the limit, both go out. The excess amount is the size of one request.

## Cache

Do not change. It is already in the pipeline, and the key contains the model, prompt, and glossary version. Even if the limit is exceeded, the cache responds — it is money already paid.

## Things Not Included

| What | When |
| --- | --- |
| Usage display on screen | Phase 6. Up to `usage()` and `python tool/translate --usage` here |
| Translation on/off switch | Phase 6. It is the main switch |

## Verification

| Check | Result |
| --- | --- |
| `pytest tool/` | 285 passed. 274 before change, 11 limit tests added |
| Do limit tests turn red? | 2 red if limit judgment is removed, 1 if timeout record is removed, 1 if unreadable limit is changed to default, 1 if `thoughtsTokenCount` is removed, 1 if changed to send when storage is missing. Review Round 1 fixes — 1 red each if usage lookup exception is not caught, if pre-deduction failure is ignored, if refund/settlement is removed, if limit reading is reverted. Round 2 fixes — 1 red each if month is not fixed, if refunded on failure after response, if deadline is not re-checked after pre-deduction |
| `python tool/lint.py --check` | Exit 0. If `translate.protect` is planted in `english_progress.py`, exit 1 and `공개 진입점` discovery |
| `python tool/test_lint.py` | Five `공개 진입점` violations are red, public names/test files/pipelines without `__all__` are green |
| Direct execution script | `test_apply`·`test_inject`·`test_declared_continuation`·`test_repo_lint`·`test_trajectory` exit 0, `ruff check tool` passed |
| Actual request | Translated one sentence with a disposable cache. $0.000067 recorded with `usageMetadata`. At limit 0, new sentence returned original, cached sentence returned translation. One sentence again after changing to pre-deduction — pre-deduction approx $0.0017 settled to actual $0.000083 |
| `python tool/translate --check` | Output is the same as before the change |

Things found while verifying. `lint` only chose type names from a fixed list when printing discoveries. Types not in the list made the exit code 1 but did not appear on the screen — new `공개 진입점` did that, and existing `주석이 한국어다` did too. Print types not in the list by appending them at the end.

Review. Round 1 produced a set of P1s. Limit reading (empty value/`inf` became default/unlimited — `nan` was already 0), usage lookup exception discarded even cache hits, cost missing if recording after response failed due to lock. All three fixed, the last one changed to record first and then send.
Round 2 produced a set of P1s from that pre-deduction. Settlement past midnight left negative numbers in the new month, refunded even on failure after receiving response, time spent waiting to use pre-deduction not subtracted from deadline. All three fixed.