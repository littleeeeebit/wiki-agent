---
scope: craft
severity: landmine
repeat: rule
triggers: ["수출|export|유출|누출|새는|샌다", "레드액션|redaction|위생|sanitis|sanitiz|마스킹|masking", "허용\\s*목록|allowlist|allow\\s*list|화이트리스트|whitelist", "span|telemetry|텔레메트리|트레이스|trace", "로그(에|를|가)?\\s*(남기|싣|보내|올리)", "PII|개인\\s*정보|비밀|secret|토큰\\s*유출", "리뷰(가|에서)?\\s*(또|다시|계속)", "같은\\s*(자리|결함|지적)", "라운드\\s*\\d"]
slots: []
sources: []
sources_withheld: true
links: [client-lifecycle-in-one-scope, diagnose-from-what-ran, verify-narrow-then-wide]
---

# Gate the exit, not the callers

Rule. When what leaves has to be restricted — telemetry, logs, webhooks,
reports — gate it at the last point the value crosses the boundary, not at
every place that produces the value.

| Do | Do not |
| --- | --- |
| One function sees everything **just before** serialising and sending | Hang one gate per production site |
| The same channel carries field names, bodies, labels and status strings | Gate two slots and forget the rest |
| A new field goes through the channel automatically | A new field means another gate |

Gate the callers instead of the exit and the list of things to block gets
counted by hand. That list is always short. How short it is becomes the next
review's finding.

## How it actually leaks — one defect arriving as five rounds

The work was stopping prompt bodies leaving a sidecar. Every round came back
P0, and every round fixed "the place that was pointed at".

| Round | The exit review named | The repair made | So what came next |
| --- | --- | --- | --- |
| 1 | The body rides on the span unconditionally | Gate the two slots `input`/`output` | Validation reads the id only, not the body's origin |
| 2 | Body origin · redirect · failure fields | Origin check, pinned transport, allow-list for failure fields | The prompt shape is loose |
| 3 | Message shape · environment proxy · session defaults | Pin the shape, block the proxy, strip the path | Forging only the response skips validation |
| 4 | Response binding · structured fields | Pair check, per-name value hygiene | Span names, `usage_details`, nested keys |
| 5 | Names, usage, nesting | Collapsed the exits into one | Places writing outside the channel remained |
| 6 | Direct sets in the transport code, unsummarised tokens | Route the direct writes through the channel, default to a digest | — |

When collapsing exits, count every direct write. Round 5 built the channel,
and the transport loop was still writing four attributes straight onto the
span without passing through it. Building a channel and having everyone use it
are different things — count the direct calls with `grep`.

All twelve P0s were faces of one question. It was not "fix one and two
appear"; there were N faces from the start and each round closed two or three.
The reviewer was not being relentless — the repair was a patch, not a structure.

How to read the signal. Two consecutive rounds of P0 on the same file and the
same subject do not mean there are faces still to find. They mean
**the place chosen to block was wrong**. Do not wait for a third round;
collapse the exits. Expecting review to find the next face is delegating the
design to the reviewer.

## Blocking and erasing are different — one check watches both

Tightening an exit erases real values along with the rest. In round 5 above, a
per-name allow-list erased many of the producer's actual values — a number
from the configuration, a model id (its characters included `/`), the whole
environment dict, and a field assumed to be a list which was really a list of
dicts and crashed the flattening.

There is one reason none of the three were caught: the check used hand-written
events.

So half of this rule lives in the check. One check, run over artefacts a real
producer left, watches three things at once.

1. Planted contamination gets out from nowhere
2. With no contamination, the real values survive intact
3. Passing a whole real log through does not crash

Without 2 and 3, only 1 goes green and what gets built is a gate where nothing
leaves and nothing remains. That is the same as having no feature. It is the
failure [[diagnose-from-what-ran]] wrote down about red — if it was not
actually run, a green is not grounds either.

## An allow-list starts by separating types

Deciding what passes from a list of names means guessing the producer's
shapes. Guesses are wrong.

- Numbers and booleans are not free text. Pass them with no name rule.
- Restrict strings only. That is where bodies and paths arrive.
- Descend into containers with per-parent allowed keys. Re-applying the
  top-level name rule inside a nesting lets a name blocked outside be smuggled
  in underneath.
- Digest long free strings rather than dropping them. Identity survives and
  the body does not leave.

When filtering strings by pattern, keep path characters (`/`, `:`) out of the
pattern. With them in, a short-identifier pattern passes absolute paths.

## When it cannot be told apart, plain text does not go

However tight the pattern, short Latin tokens cannot be told apart. An
`sk-live-secret123` in a version field has the same shape as a version. In the
work above, that indistinguishability was used as grounds for "so send it as
plain text", and that was the next round's P0.

The direction is backwards. When it cannot be told apart, the answer is that
plain text does not go. Make a digest the default and let plain text out only
where there are grounds.

| Goes out in plain text | Why |
| --- | --- |
| An enum value read from the producer | The set is in the code |
| A string equal to a producer constant | It was compared against the constant |
| A hash or an item name | The shape proves the value |
| Purely numeric versions (`0.26.0`, `2.11.0+cu130`) | That shape has no entropy to hide a secret in |
| Short identifiers that build keys and names | Digested, the trace cannot be read. The layer above compares that value instead |

Everything else shrinks to `sha256:<prefix>`. Identity and change survive, so
"did it run with the same settings" is still answerable, and the value does
not leave. A digest is not a loss — record it as distinct from dropping.

When splitting layers, check that the lower one is not deferring to the upper
one something the upper one cannot do. Writing "the layer above blocks this
value" requires confirming the layer above actually sees it. In the work
above, a metadata-only mode never ran the upper layer at all, leaving values
blocked by neither.

What goes wrong. Review finds one adjacent exit per round, and every repair
creates the next round's finding. Through all the reverts the suite stays green.
